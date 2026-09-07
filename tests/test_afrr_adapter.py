from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pyarrow as pa
import pytest

from stepinbel.config import (
    AFRRCase,
    AssetConfig,
    BelgianDeliveryPeriod,
    DayAheadCase,
    FixedMinimumCapacityBid,
    HistoricalQuantileCapacityBid,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.data import open_published_bundle
from stepinbel.data.coverage import ResolvedPeriod, window_from_period
from stepinbel.data.load import MarketDataSlice, load_market_data
from stepinbel.markets.base import MarketInputError
from stepinbel.markets.afrr import build_afrr_inputs
from stepinbel.optimizer import ModelError, solve_case
from stepinbel.optimizer.model import build_sparse_lp, prepare_physical


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _stamps(n: int, start: datetime) -> list[datetime]:
    return [start + timedelta(minutes=15 * i) for i in range(n)]


def _da_table(stamps: list[datetime], prices: np.ndarray) -> pa.Table:
    return pa.table(
        {
            "datetime_utc": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
            "da_price_eur_mwh": prices,
        }
    )


def _balancing_table(
    stamps: list[datetime],
    *,
    has_up: np.ndarray,
    has_down: np.ndarray,
    cbmp_up: np.ndarray,
    cbmp_down: np.ndarray,
) -> pa.Table:
    return pa.table(
        {
            "datetime_utc": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
            "has_afrr_up": has_up,
            "has_afrr_down": has_down,
            "cbmp_afrr_up": cbmp_up,
            "cbmp_afrr_down": cbmp_down,
        }
    )


def _capacity_table(rows: list[dict]) -> pa.Table:
    return pa.table(
        {
            "delivery_date_local": [row["delivery_date_local"] for row in rows],
            "block": [row["block"] for row in rows],
            "product": [row.get("product", "afrr") for row in rows],
            "direction": [row["direction"] for row in rows],
            "auction_step": ["step1"] * len(rows),
            "block_start_utc": [row["block_start_utc"] for row in rows],
            "block_end_utc": [row["block_end_utc"] for row in rows],
            "block_hours": [row["block_hours"] for row in rows],
            "data_available": [True] * len(rows),
            "awarded_volume_mw": [1.0] * len(rows),
            "marginal_price_eur_mw_h": [row["marginal_price"] for row in rows],
            "vwap_price_eur_mw_h": [row["marginal_price"] for row in rows],
        }
    )


def _slice(
    stamps: list[datetime],
    *,
    da: np.ndarray,
    has_up: np.ndarray,
    has_down: np.ndarray,
    cbmp_up: np.ndarray,
    cbmp_down: np.ndarray,
    capacity_rows: list[dict],
    asset: AssetConfig | None = None,
    site: SiteConfig | None = None,
    market_case: AFRRCase | None = None,
) -> MarketDataSlice:
    start = stamps[0]
    end = stamps[-1] + timedelta(minutes=15)
    period = UtcPeriod(start, end)
    window = window_from_period(period)
    resolved = ResolvedPeriod(
        period=period,
        window=window,
        market="afrr",
        required_sources=("da_prices_qh", "balancing_qh", "capacity_blocks"),
        pv_region=None,
    )
    config = SimulationConfig(
        period=period,
        market_case=market_case or AFRRCase(),
        asset=asset or AssetConfig(pump_ramp_power_frac=0.0, turbine_ramp_power_frac=0.0),
        site=site or SiteConfig(),
    )
    return MarketDataSlice(
        config=config,
        period=resolved,
        manifest_sha256="00" * 32,
        da_prices=_da_table(stamps, da),
        balancing=_balancing_table(
            stamps, has_up=has_up, has_down=has_down, cbmp_up=cbmp_up, cbmp_down=cbmp_down
        ),
        capacity_blocks=_capacity_table(capacity_rows),
        pv_profile=None,
    )


def _block_row(
    day: date,
    block: str,
    start: datetime,
    hours: float,
    price: float,
    direction: str,
) -> dict:
    return {
        "delivery_date_local": day,
        "block": block,
        "direction": direction,
        "block_start_utc": start,
        "block_end_utc": start + timedelta(hours=hours),
        "block_hours": hours,
        "marginal_price": price,
    }


def test_build_afrr_inputs_requires_afrr_case(data_root) -> None:
    bundle = open_published_bundle(data_root)
    period = BelgianDeliveryPeriod(date(2025, 1, 15), date(2025, 1, 15))
    slice_ = load_market_data(
        bundle, SimulationConfig(period=period, market_case=DayAheadCase())
    )
    with pytest.raises(ModelError, match="AFRRCase"):
        build_afrr_inputs(slice_)


def test_missing_and_unconvertible_tables_are_domain_errors() -> None:
    stamps = _stamps(16, _utc(2025, 1, 15, 3))
    rows = [
        _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 20.0, "up"),
        _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 8.0, "down"),
    ]
    slice_ = _slice(
        stamps,
        da=np.zeros(16),
        has_up=np.ones(16, dtype=bool),
        has_down=np.ones(16, dtype=bool),
        cbmp_up=np.ones(16),
        cbmp_down=np.ones(16),
        capacity_rows=rows,
    )
    missing_product = replace(
        slice_, capacity_blocks=slice_.capacity_blocks.drop_columns(["product"])
    )
    with pytest.raises(MarketInputError, match="product") as caught:
        build_afrr_inputs(missing_product)
    assert "capacity" in str(caught.value)

    missing_flag = replace(
        slice_, balancing=slice_.balancing.drop_columns(["has_afrr_down"])
    )
    with pytest.raises(MarketInputError, match="has_afrr_down") as caught:
        build_afrr_inputs(missing_flag)
    assert "balancing" in str(caught.value)

    table = slice_.capacity_blocks
    index = table.schema.get_field_index("block_hours")
    broken = replace(
        slice_,
        capacity_blocks=table.set_column(index, "block_hours", pa.array(["not-a-number"] * table.num_rows)),
    )
    with pytest.raises(MarketInputError) as caught:
        build_afrr_inputs(broken)
    assert "capacity" in str(caught.value)
    assert caught.value.__cause__ is not None


def test_arrays_are_owned_read_only_and_da_reference_is_unmodified() -> None:
    stamps = _stamps(16, _utc(2025, 1, 15, 3))
    da = np.linspace(10.0, 25.0, 16)
    cbmp_up = np.full(16, 80.0)
    market = build_afrr_inputs(
        _slice(
            stamps,
            da=da,
            has_up=np.ones(16, dtype=bool),
            has_down=np.zeros(16, dtype=bool),
            cbmp_up=cbmp_up,
            cbmp_down=np.full(16, np.nan),
            capacity_rows=[
                _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 20.0, "up"),
                _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 8.0, "down"),
            ],
            market_case=AFRRCase(capacity_bid=FixedMinimumCapacityBid(0.0, 0.0)),
        )
    )
    expected_da = np.linspace(10.0, 25.0, 16)
    expected_sell = np.array(market.sell_price_eur_mwh)
    expected_buy = np.array(market.buy_price_eur_mwh)
    expected_sell_ub = np.array(market.sell_upper_mw)
    expected_buy_ub = np.array(market.buy_upper_mw)
    for name in (
        "sell_price_eur_mwh",
        "buy_price_eur_mwh",
        "sell_upper_mw",
        "buy_upper_mw",
        "day_ahead_price_eur_mwh",
    ):
        assert getattr(market, name).flags.writeable is False
    da[0] = -99.0
    cbmp_up[0] = -99.0
    np.testing.assert_array_equal(market.day_ahead_price_eur_mwh, expected_da)
    np.testing.assert_array_equal(market.sell_price_eur_mwh, expected_sell)
    np.testing.assert_array_equal(market.buy_price_eur_mwh, expected_buy)
    np.testing.assert_array_equal(market.sell_upper_mw, expected_sell_ub)
    np.testing.assert_array_equal(market.buy_upper_mw, expected_buy_ub)
    with pytest.raises(ValueError):
        market.sell_price_eur_mwh[0] = 0.0


def test_three_four_five_hour_blocks_map_to_qh_spans() -> None:
    start = _utc(2025, 3, 30, 0)
    stamps = _stamps(12 + 16 + 20, start)
    n = len(stamps)
    rows = [
        _block_row(date(2025, 3, 30), "0-3", start, 3.0, 80.0, "up"),
        _block_row(date(2025, 3, 30), "3-7", start + timedelta(hours=3), 4.0, 80.0, "up"),
        _block_row(date(2025, 3, 30), "7-12", start + timedelta(hours=7), 5.0, 80.0, "up"),
        _block_row(date(2025, 3, 30), "0-3", start, 3.0, 80.0, "down"),
        _block_row(date(2025, 3, 30), "3-7", start + timedelta(hours=3), 4.0, 80.0, "down"),
        _block_row(date(2025, 3, 30), "7-12", start + timedelta(hours=7), 5.0, 80.0, "down"),
    ]
    market = build_afrr_inputs(
        _slice(
            stamps,
            da=np.full(n, 10.0),
            has_up=np.ones(n, dtype=bool),
            has_down=np.ones(n, dtype=bool),
            cbmp_up=np.full(n, 100.0),
            cbmp_down=np.full(n, 1.0),
            capacity_rows=rows,
            market_case=AFRRCase(
                capacity_bid=FixedMinimumCapacityBid(0.0, 0.0),
                up_capacity_fraction=0.5,
            ),
        )
    )
    spans = {
        item.identifier: (item.end_index - item.start_index, item.block_hours)
        for item in market.capacity_commitments
    }
    assert spans["afrr:2025-03-30:0-3:up"] == (12, 3.0)
    assert spans["afrr:2025-03-30:3-7:up"] == (16, 4.0)
    assert spans["afrr:2025-03-30:7-12:up"] == (20, 5.0)
    assert spans["afrr:2025-03-30:0-3:down"] == (12, 3.0)
    assert [item.direction for item in market.capacity_commitments][:3] == ["up", "up", "up"]
    assert [item.direction for item in market.capacity_commitments][3:] == ["down", "down", "down"]


def test_partial_block_is_rejected() -> None:
    stamps = _stamps(8, _utc(2025, 1, 15, 0))
    rows = [_block_row(date(2025, 1, 15), "0-4", _utc(2025, 1, 14, 23), 4.0, 10.0, "down")]
    with pytest.raises(MarketInputError, match="complete capacity blocks") as caught:
        build_afrr_inputs(
            _slice(
                stamps,
                da=np.zeros(8),
                has_up=np.zeros(8, dtype=bool),
                has_down=np.ones(8, dtype=bool),
                cbmp_up=np.zeros(8),
                cbmp_down=np.ones(8),
                capacity_rows=rows,
            )
        )
    assert "was not moved, clipped, or prorated" in str(caught.value)


def test_cross_direction_overlap_is_allowed_and_identifiers_are_unique() -> None:
    stamps = _stamps(16, _utc(2025, 1, 15, 3))
    rows = [
        _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 20.0, "up"),
        _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 8.0, "down"),
    ]
    market = build_afrr_inputs(
        _slice(
            stamps,
            da=np.zeros(16),
            has_up=np.ones(16, dtype=bool),
            has_down=np.ones(16, dtype=bool),
            cbmp_up=np.full(16, 80.0),
            cbmp_down=np.full(16, 1.0),
            capacity_rows=rows,
            market_case=AFRRCase(
                capacity_bid=FixedMinimumCapacityBid(0.0, 0.0),
                up_capacity_fraction=0.5,
            ),
        )
    )
    ids = [item.identifier for item in market.capacity_commitments]
    assert ids == ["afrr:2025-01-15:4-8:up", "afrr:2025-01-15:4-8:down"]
    assert len(set(ids)) == 2
    assert market.capacity_commitments[0].start_index == market.capacity_commitments[1].start_index


def test_capacity_split_follows_fraction_machine_and_grid_limits() -> None:
    stamps = _stamps(16, _utc(2025, 1, 15, 3))
    rows = [
        _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 20.0, "up"),
        _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 8.0, "down"),
    ]
    shared = dict(
        da=np.zeros(16),
        has_up=np.ones(16, dtype=bool),
        has_down=np.ones(16, dtype=bool),
        cbmp_up=np.full(16, 80.0),
        cbmp_down=np.full(16, 1.0),
        capacity_rows=rows,
        asset=AssetConfig(
            power_pump_mw=2.0,
            power_turbine_mw=1.5,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
        site=SiteConfig(grid_export_mw=0.4, grid_import_mw=0.3),
    )
    both = build_afrr_inputs(
        _slice(
            stamps,
            market_case=AFRRCase(
                capacity_bid=FixedMinimumCapacityBid(0.0, 0.0),
                up_capacity_fraction=0.5,
            ),
            **shared,
        )
    )
    by_dir = {item.direction: item for item in both.capacity_commitments}
    assert by_dir["up"].cap_max_mw == pytest.approx(0.4)
    assert by_dir["down"].cap_max_mw == pytest.approx(0.3)
    assert float(np.max(both.sell_upper_mw)) == pytest.approx(0.4)
    assert float(np.max(both.buy_upper_mw)) == pytest.approx(2.0)

    only_up = build_afrr_inputs(
        _slice(
            stamps,
            market_case=AFRRCase(
                capacity_bid=FixedMinimumCapacityBid(0.0, 0.0),
                up_capacity_fraction=1.0,
            ),
            **shared,
        )
    )
    assert [item.direction for item in only_up.capacity_commitments] == ["up"]

    only_down = build_afrr_inputs(
        _slice(
            stamps,
            market_case=AFRRCase(
                capacity_bid=FixedMinimumCapacityBid(0.0, 0.0),
                up_capacity_fraction=0.0,
            ),
            **shared,
        )
    )
    assert [item.direction for item in only_down.capacity_commitments] == ["down"]


def test_upward_eligibility_and_downward_m2_buy_price() -> None:
    stamps = _stamps(16, _utc(2025, 1, 15, 3))
    da = np.full(16, 30.0)
    cbmp_up = np.full(16, 40.0)
    cbmp_up[1] = 39.9
    has_up = np.ones(16, dtype=bool)
    has_up[3] = False
    cbmp_down = np.full(16, 12.0)
    cbmp_down[5] = np.nan
    has_down = np.ones(16, dtype=bool)
    has_down[6] = False
    market = build_afrr_inputs(
        _slice(
            stamps,
            da=da,
            has_up=has_up,
            has_down=has_down,
            cbmp_up=cbmp_up,
            cbmp_down=cbmp_down,
            capacity_rows=[
                _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 10.0, "up"),
                _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 10.0, "down"),
            ],
            asset=AssetConfig(
                eta_pump=1.0,
                eta_turbine=1.0,
                pump_ramp_power_frac=0.0,
                turbine_ramp_power_frac=0.0,
            ),
            market_case=AFRRCase(capacity_bid=FixedMinimumCapacityBid(10.0, 10.0)),
        )
    )
    sell = market.sell_price_eur_mwh
    ub = market.sell_upper_mw
    buy = market.buy_price_eur_mwh
    assert sell[1] == 0.0 and ub[1] == 0.0
    assert sell[2] == pytest.approx(40.0) and ub[2] == pytest.approx(1.0)
    assert sell[3] == 0.0 and ub[3] == 0.0
    assert buy[0] == pytest.approx(12.0)
    assert buy[5] == pytest.approx(30.0)
    assert buy[6] == pytest.approx(30.0)
    np.testing.assert_array_equal(market.day_ahead_price_eur_mwh, da)
    assert np.all(market.buy_upper_mw == 1.0)


def test_passive_upward_eligibility_is_subset_and_downward_prices_match() -> None:
    stamps = _stamps(40, _utc(2025, 6, 1, 0))
    n = len(stamps)
    rows = [
        _block_row(date(2025, 6, 1), "2-6", stamps[0], 4.0, 20.0, "up"),
        _block_row(date(2025, 6, 1), "6-10", stamps[16], 4.0, 20.0, "up"),
        _block_row(date(2025, 6, 1), "10-14", stamps[32], 2.0, 20.0, "up"),
        _block_row(date(2025, 6, 1), "2-6", stamps[0], 4.0, 20.0, "down"),
        _block_row(date(2025, 6, 1), "6-10", stamps[16], 4.0, 20.0, "down"),
        _block_row(date(2025, 6, 1), "10-14", stamps[32], 2.0, 20.0, "down"),
    ]
    shared = dict(
        da=np.full(n, 20.0),
        has_up=np.ones(n, dtype=bool),
        has_down=np.ones(n, dtype=bool),
        cbmp_up=np.linspace(10.0, 90.0, n),
        cbmp_down=np.full(n, 5.0),
        capacity_rows=rows,
        asset=AssetConfig(
            eta_pump=1.0,
            eta_turbine=1.0,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
    )
    cap = HistoricalQuantileCapacityBid(0.50)
    balanced = build_afrr_inputs(
        _slice(
            stamps,
            market_case=AFRRCase(activation_profile="balanced", capacity_bid=cap),
            **shared,
        )
    )
    passive = build_afrr_inputs(
        _slice(
            stamps,
            market_case=AFRRCase(activation_profile="passive", capacity_bid=cap),
            **shared,
        )
    )
    assert [c.identifier for c in balanced.capacity_commitments] == [
        c.identifier for c in passive.capacity_commitments
    ]
    assert [c.price_eur_mw_h for c in balanced.capacity_commitments] == [
        c.price_eur_mw_h for c in passive.capacity_commitments
    ]
    assert np.all((passive.sell_upper_mw > 0) <= (balanced.sell_upper_mw > 0))
    assert int((passive.sell_upper_mw > 0).sum()) < int((balanced.sell_upper_mw > 0).sum())
    np.testing.assert_array_equal(balanced.buy_price_eur_mwh, passive.buy_price_eur_mwh)
    np.testing.assert_array_equal(balanced.buy_price_eur_mwh, np.full(n, 5.0))


def test_downward_pumping_available_without_down_capacity_and_no_pump_rows() -> None:
    stamps = _stamps(16, _utc(2025, 1, 15, 3))
    slice_ = _slice(
        stamps,
        da=np.full(16, 40.0),
        has_up=np.zeros(16, dtype=bool),
        has_down=np.ones(16, dtype=bool),
        cbmp_up=np.full(16, np.nan),
        cbmp_down=np.full(16, 8.0),
        capacity_rows=[
            _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 20.0, "up"),
            _block_row(date(2025, 1, 15), "4-8", stamps[0], 4.0, 8.0, "down"),
        ],
        market_case=AFRRCase(
            capacity_bid=FixedMinimumCapacityBid(0.0, 0.0),
            up_capacity_fraction=1.0,
        ),
    )
    market = build_afrr_inputs(slice_)
    assert not any(item.direction == "down" for item in market.capacity_commitments)
    assert np.all(market.buy_upper_mw == 1.0)
    np.testing.assert_array_equal(market.buy_price_eur_mwh, np.full(16, 8.0))
    lp = build_sparse_lp(prepare_physical(slice_.config, market, None))
    assert lp.has_downward_pump_capacity_rows is False


def test_real_delivery_day_balanced_passive_fixed_and_fraction(data_root) -> None:
    bundle = open_published_bundle(data_root)
    period = BelgianDeliveryPeriod(date(2025, 1, 15), date(2025, 1, 15))
    cap = HistoricalQuantileCapacityBid(0.50)
    balanced_slice = load_market_data(
        bundle,
        SimulationConfig(
            period=period,
            market_case=AFRRCase(activation_profile="balanced", capacity_bid=cap),
        ),
    )
    passive_slice = load_market_data(
        bundle,
        SimulationConfig(
            period=period,
            market_case=AFRRCase(activation_profile="passive", capacity_bid=cap),
        ),
    )
    fixed_slice = load_market_data(
        bundle,
        SimulationConfig(
            period=period,
            market_case=AFRRCase(
                activation_profile="balanced",
                capacity_bid=FixedMinimumCapacityBid(0.0, 0.0),
            ),
        ),
    )
    split_slice = load_market_data(
        bundle,
        SimulationConfig(
            period=period,
            market_case=AFRRCase(
                activation_profile="balanced",
                capacity_bid=FixedMinimumCapacityBid(0.0, 0.0),
                up_capacity_fraction=0.5,
            ),
        ),
    )
    balanced_in = build_afrr_inputs(balanced_slice)
    passive_in = build_afrr_inputs(passive_slice)
    np.testing.assert_array_equal(balanced_in.buy_price_eur_mwh, passive_in.buy_price_eur_mwh)
    assert np.all((passive_in.sell_upper_mw > 0) <= (balanced_in.sell_upper_mw > 0))

    split_in = build_afrr_inputs(split_slice)
    assert {item.direction for item in split_in.capacity_commitments} == {"up", "down"}

    for slice_ in (balanced_slice, passive_slice, fixed_slice, split_slice):
        result = solve_case(slice_)
        assert result.solver.status == "optimal"
        assert result.solver.continuous_lp is True
        assert result.solver.num_integer == 0
        assert result.solver.num_binary == 0
        assert result.feasibility.ok
        assert np.isfinite(result.summary.total_site_revenue_eur)
        assert result.summary.reservoir_final_mwh == pytest.approx(
            0.5 * result.summary.e_max_mwh, abs=1e-6
        )
        assert result.dispatch.column("pv_export_price_eur_mwh").null_count == (
            result.dispatch.num_rows
        )
    split = solve_case(split_slice)
    assert set(split.capacity_results.column("direction").to_pylist()) == {"up", "down"}
    default = solve_case(balanced_slice)
    assert set(default.capacity_results.column("direction").to_pylist()) <= {"up"}
    assert default.capacity_results.num_rows >= 1
