from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pyarrow as pa
import pytest

from stepinbel.config import (
    AssetConfig,
    BelgianDeliveryPeriod,
    FixedMinimumCapacityBid,
    HistoricalQuantileCapacityBid,
    MFRRCase,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.data import open_published_bundle
from stepinbel.data.coverage import ResolvedPeriod, window_from_period
from stepinbel.data.load import MarketDataSlice, load_market_data
from stepinbel.markets.base import MarketInputError
from stepinbel.markets.mfrr import build_mfrr_inputs
from stepinbel.optimizer import solve_case


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
    cbmp: np.ndarray,
) -> pa.Table:
    return pa.table(
        {
            "datetime_utc": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
            "has_mfrr_up": has_up,
            "cbmp_mfrr_up": cbmp,
        }
    )


def _capacity_table(rows: list[dict]) -> pa.Table:
    return pa.table(
        {
            "delivery_date_local": [row["delivery_date_local"] for row in rows],
            "block": [row["block"] for row in rows],
            "product": ["mfrr"] * len(rows),
            "direction": [row.get("direction", "up") for row in rows],
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
    cbmp: np.ndarray,
    capacity_rows: list[dict],
    asset: AssetConfig | None = None,
    site: SiteConfig | None = None,
    market_case: MFRRCase | None = None,
) -> MarketDataSlice:
    start = stamps[0]
    end = stamps[-1] + timedelta(minutes=15)
    period = UtcPeriod(start, end)
    window = window_from_period(period)
    resolved = ResolvedPeriod(
        period=period,
        window=window,
        market="mfrr",
        required_sources=("da_prices_qh", "balancing_qh", "capacity_blocks"),
        pv_region=None,
    )
    config = SimulationConfig(
        period=period,
        market_case=market_case or MFRRCase(),
        asset=asset or AssetConfig(pump_ramp_power_frac=0.0, turbine_ramp_power_frac=0.0),
        site=site or SiteConfig(),
    )
    return MarketDataSlice(
        config=config,
        period=resolved,
        manifest_sha256="00" * 32,
        da_prices=_da_table(stamps, da),
        balancing=_balancing_table(stamps, has_up=has_up, cbmp=cbmp),
        capacity_blocks=_capacity_table(capacity_rows),
        pv_profile=None,
    )


def test_three_four_five_hour_blocks_map_to_qh_spans() -> None:
    start = _utc(2025, 3, 30, 0)
    stamps = _stamps(12 + 16 + 20, start)
    n = len(stamps)
    rows = [
        {
            "delivery_date_local": date(2025, 3, 30),
            "block": "0-3",
            "block_start_utc": start,
            "block_end_utc": start + timedelta(hours=3),
            "block_hours": 3.0,
            "marginal_price": 80.0,
        },
        {
            "delivery_date_local": date(2025, 3, 30),
            "block": "3-7",
            "block_start_utc": start + timedelta(hours=3),
            "block_end_utc": start + timedelta(hours=7),
            "block_hours": 4.0,
            "marginal_price": 80.0,
        },
        {
            "delivery_date_local": date(2025, 3, 30),
            "block": "7-12",
            "block_start_utc": start + timedelta(hours=7),
            "block_end_utc": start + timedelta(hours=12),
            "block_hours": 5.0,
            "marginal_price": 80.0,
        },
    ]
    market = build_mfrr_inputs(
        _slice(
            stamps,
            da=np.full(n, 10.0),
            has_up=np.ones(n, dtype=bool),
            cbmp=np.full(n, 100.0),
            capacity_rows=rows,
            market_case=MFRRCase(capacity_bid=FixedMinimumCapacityBid(0.0)),
        )
    )
    spans = {
        item.identifier: (item.end_index - item.start_index, item.block_hours)
        for item in market.capacity_commitments
    }
    assert spans["mfrr:2025-03-30:0-3:up"] == (12, 3.0)
    assert spans["mfrr:2025-03-30:3-7:up"] == (16, 4.0)
    assert spans["mfrr:2025-03-30:7-12:up"] == (20, 5.0)
    assert len(spans) == 3


def test_window_cutting_a_capacity_block_is_rejected() -> None:
    stamps = _stamps(8, _utc(2025, 1, 15, 0))
    rows = [
        {
            "delivery_date_local": date(2025, 1, 15),
            "block": "0-4",
            "block_start_utc": _utc(2025, 1, 14, 23),
            "block_end_utc": _utc(2025, 1, 15, 3),
            "block_hours": 4.0,
            "marginal_price": 10.0,
        }
    ]
    with pytest.raises(MarketInputError, match="complete capacity blocks") as caught:
        build_mfrr_inputs(
            _slice(
                stamps,
                da=np.zeros(8),
                has_up=np.ones(8, dtype=bool),
                cbmp=np.ones(8),
                capacity_rows=rows,
            )
        )
    message = str(caught.value)
    assert "0-4" in message
    assert "was not moved, clipped, or prorated" in message


def test_cap_max_is_min_of_turbine_and_grid_export_not_pump() -> None:
    stamps = _stamps(16, _utc(2025, 1, 15, 3))
    rows = [
        {
            "delivery_date_local": date(2025, 1, 15),
            "block": "4-8",
            "block_start_utc": _utc(2025, 1, 15, 3),
            "block_end_utc": _utc(2025, 1, 15, 7),
            "block_hours": 4.0,
            "marginal_price": 50.0,
        }
    ]
    market = build_mfrr_inputs(
        _slice(
            stamps,
            da=np.zeros(16),
            has_up=np.ones(16, dtype=bool),
            cbmp=np.full(16, 80.0),
            capacity_rows=rows,
            asset=AssetConfig(
                power_pump_mw=2.0,
                power_turbine_mw=1.5,
                pump_ramp_power_frac=0.0,
                turbine_ramp_power_frac=0.0,
            ),
            site=SiteConfig(grid_export_mw=0.4),
            market_case=MFRRCase(capacity_bid=FixedMinimumCapacityBid(0.0)),
        )
    )
    assert market.capacity_commitments[0].cap_max_mw == pytest.approx(0.4)
    assert float(np.max(market.sell_upper_mw)) == pytest.approx(0.4)
    assert float(np.max(market.buy_upper_mw)) == pytest.approx(2.0)


def test_eligibility_requires_flags_and_threshold_equality() -> None:
    stamps = _stamps(16, _utc(2025, 1, 15, 3))
    cbmp = np.full(16, 40.0)
    cbmp[1] = 39.9
    has_up = np.ones(16, dtype=bool)
    has_up[3] = False
    rows = [
        {
            "delivery_date_local": date(2025, 1, 15),
            "block": "4-8",
            "block_start_utc": _utc(2025, 1, 15, 3),
            "block_end_utc": _utc(2025, 1, 15, 7),
            "block_hours": 4.0,
            "marginal_price": 10.0,
        }
    ]
    market = build_mfrr_inputs(
        _slice(
            stamps,
            da=np.full(16, 1.0),
            has_up=has_up,
            cbmp=cbmp,
            capacity_rows=rows,
            asset=AssetConfig(
                eta_pump=1.0,
                eta_turbine=1.0,
                pump_ramp_power_frac=0.0,
                turbine_ramp_power_frac=0.0,
            ),
            market_case=MFRRCase(capacity_bid=FixedMinimumCapacityBid(10.0)),
        )
    )
    sell = market.sell_price_eur_mwh
    ub = market.sell_upper_mw
    assert sell[1] == 0.0 and ub[1] == 0.0
    assert sell[2] == pytest.approx(40.0) and ub[2] == pytest.approx(1.0)
    assert sell[3] == 0.0 and ub[3] == 0.0
    np.testing.assert_array_equal(market.buy_price_eur_mwh, np.full(16, 1.0))
    np.testing.assert_array_equal(market.day_ahead_price_eur_mwh, np.full(16, 1.0))
    assert not np.allclose(market.sell_price_eur_mwh, market.day_ahead_price_eur_mwh)


def test_passive_eligible_qhs_are_subset_of_balanced_with_same_capacity_bid() -> None:
    stamps = _stamps(40, _utc(2025, 6, 1, 0))
    n = len(stamps)
    rows = [
        {
            "delivery_date_local": date(2025, 6, 1),
            "block": "2-6",
            "block_start_utc": _utc(2025, 6, 1, 0),
            "block_end_utc": _utc(2025, 6, 1, 4),
            "block_hours": 4.0,
            "marginal_price": 20.0,
        },
        {
            "delivery_date_local": date(2025, 6, 1),
            "block": "6-10",
            "block_start_utc": _utc(2025, 6, 1, 4),
            "block_end_utc": _utc(2025, 6, 1, 8),
            "block_hours": 4.0,
            "marginal_price": 20.0,
        },
        {
            "delivery_date_local": date(2025, 6, 1),
            "block": "10-14",
            "block_start_utc": _utc(2025, 6, 1, 8),
            "block_end_utc": _utc(2025, 6, 1, 10),
            "block_hours": 2.0,
            "marginal_price": 20.0,
        },
    ]
    cbmp = np.linspace(10.0, 90.0, n)
    shared = dict(
        da=np.full(n, 1.0),
        has_up=np.ones(n, dtype=bool),
        cbmp=cbmp,
        capacity_rows=rows,
        asset=AssetConfig(
            eta_pump=1.0,
            eta_turbine=1.0,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
    )
    cap = HistoricalQuantileCapacityBid(0.50)
    balanced = build_mfrr_inputs(
        _slice(
            stamps,
            market_case=MFRRCase(activation_profile="balanced", capacity_bid=cap),
            **shared,
        )
    )
    passive = build_mfrr_inputs(
        _slice(
            stamps,
            market_case=MFRRCase(activation_profile="passive", capacity_bid=cap),
            **shared,
        )
    )
    assert [c.identifier for c in balanced.capacity_commitments] == [
        c.identifier for c in passive.capacity_commitments
    ]
    assert [c.price_eur_mw_h for c in balanced.capacity_commitments] == [
        c.price_eur_mw_h for c in passive.capacity_commitments
    ]
    balanced_on = balanced.sell_upper_mw > 0
    passive_on = passive.sell_upper_mw > 0
    assert np.all(passive_on <= balanced_on)
    assert int(passive_on.sum()) < int(balanced_on.sum())


def test_negative_historical_bid_creates_no_commitments() -> None:
    stamps = _stamps(16, _utc(2025, 1, 15, 3))
    market = build_mfrr_inputs(
        _slice(
            stamps,
            da=np.zeros(16),
            has_up=np.ones(16, dtype=bool),
            cbmp=np.full(16, 80.0),
            capacity_rows=[
                {
                    "delivery_date_local": date(2025, 1, 15),
                    "block": "4-8",
                    "block_start_utc": _utc(2025, 1, 15, 3),
                    "block_end_utc": _utc(2025, 1, 15, 7),
                    "block_hours": 4.0,
                    "marginal_price": -5.0,
                }
            ],
        )
    )
    assert market.capacity_commitments == ()
    assert np.all(market.sell_upper_mw == 0.0)


def test_real_delivery_day_balanced_passive_and_fixed(data_root) -> None:
    bundle = open_published_bundle(data_root)
    period = BelgianDeliveryPeriod(date(2025, 1, 15), date(2025, 1, 15))
    cap = HistoricalQuantileCapacityBid(0.50)
    balanced_slice = load_market_data(
        bundle,
        SimulationConfig(
            period=period,
            market_case=MFRRCase(activation_profile="balanced", capacity_bid=cap),
        ),
    )
    passive_slice = load_market_data(
        bundle,
        SimulationConfig(
            period=period,
            market_case=MFRRCase(activation_profile="passive", capacity_bid=cap),
        ),
    )
    fixed_slice = load_market_data(
        bundle,
        SimulationConfig(
            period=period,
            market_case=MFRRCase(
                activation_profile="balanced",
                capacity_bid=FixedMinimumCapacityBid(0.0),
            ),
        ),
    )
    balanced_in = build_mfrr_inputs(balanced_slice)
    passive_in = build_mfrr_inputs(passive_slice)
    assert [c.identifier for c in balanced_in.capacity_commitments] == [
        c.identifier for c in passive_in.capacity_commitments
    ]
    assert np.all((passive_in.sell_upper_mw > 0) <= (balanced_in.sell_upper_mw > 0))
    np.testing.assert_array_equal(
        balanced_in.buy_price_eur_mwh, balanced_in.day_ahead_price_eur_mwh
    )
    assert not np.array_equal(balanced_in.sell_price_eur_mwh, balanced_in.buy_price_eur_mwh)

    balanced = solve_case(balanced_slice)
    passive = solve_case(passive_slice)
    fixed = solve_case(fixed_slice)
    for result in (balanced, passive, fixed):
        assert result.solver.status == "optimal"
        assert result.solver.continuous_lp is True
        assert result.solver.num_integer == 0
        assert result.solver.num_binary == 0
        assert result.capacity_results.num_rows >= 1
        assert set(result.capacity_results.column("direction").to_pylist()) == {"up"}
        assert result.feasibility.ok
        assert np.isfinite(result.summary.total_site_revenue_eur)
        assert result.summary.reservoir_final_mwh == pytest.approx(
            0.5 * result.summary.e_max_mwh, abs=1e-6
        )
        assert result.dispatch.column("pv_export_price_eur_mwh").null_count == (
            result.dispatch.num_rows
        )


def _one_block_slice() -> MarketDataSlice:
    stamps = _stamps(16, _utc(2025, 1, 15, 3))
    rows = [
        {
            "delivery_date_local": date(2025, 1, 15),
            "block": "4-8",
            "block_start_utc": _utc(2025, 1, 15, 3),
            "block_end_utc": _utc(2025, 1, 15, 7),
            "block_hours": 4.0,
            "marginal_price": 50.0,
        }
    ]
    return _slice(
        stamps,
        da=np.zeros(16),
        has_up=np.ones(16, dtype=bool),
        cbmp=np.ones(16),
        capacity_rows=rows,
    )


def test_missing_capacity_product_column_is_domain_error() -> None:
    slice_ = _one_block_slice()
    broken = replace(slice_, capacity_blocks=slice_.capacity_blocks.drop_columns(["product"]))
    with pytest.raises(MarketInputError, match="product") as caught:
        build_mfrr_inputs(broken)
    assert "capacity" in str(caught.value)
    assert not isinstance(caught.value, KeyError)


def test_unconvertible_capacity_numeric_column_is_domain_error() -> None:
    slice_ = _one_block_slice()
    table = slice_.capacity_blocks
    index = table.schema.get_field_index("block_hours")
    broken_table = table.set_column(index, "block_hours", pa.array(["not-a-number"]))
    broken = replace(slice_, capacity_blocks=broken_table)
    with pytest.raises(MarketInputError) as caught:
        build_mfrr_inputs(broken)
    message = str(caught.value)
    assert "capacity" in message and (
        "block_hours" in message or "converted" in message
    )
    assert caught.value.__cause__ is not None
    assert isinstance(
        caught.value.__cause__,
        (TypeError, ValueError, OverflowError, pa.ArrowInvalid),
    )
