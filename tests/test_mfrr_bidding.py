from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pyarrow as pa
import pytest

from stepinbel.config import ConfigError, FixedMinimumCapacityBid, MFRRCase
from stepinbel.markets.base import MarketInputError
from stepinbel.markets.bidding import (
    ENERGY_PROFILE_QUANTILE,
    collapse_capacity_blocks,
    yearly_energy_bids,
    yearly_historical_capacity_bids,
)
from stepinbel.markets.mfrr import _capacity_bids_by_year


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _capacity_table(rows: list[dict]) -> pa.Table:
    return pa.table(
        {
            "delivery_date_local": [row["delivery_date_local"] for row in rows],
            "block": [row["block"] for row in rows],
            "product": [row.get("product", "mfrr") for row in rows],
            "direction": [row["direction"] for row in rows],
            "block_start_utc": [row["block_start_utc"] for row in rows],
            "block_end_utc": [row["block_end_utc"] for row in rows],
            "block_hours": [row["block_hours"] for row in rows],
            "data_available": [row["data_available"] for row in rows],
            "marginal_price_eur_mw_h": [row["marginal_price_eur_mw_h"] for row in rows],
        }
    )


def _block(
    *,
    day: date,
    block: str,
    start: datetime,
    hours: float,
    price: float,
    direction: str = "up",
    available: bool = True,
    product: str = "mfrr",
) -> dict:
    return {
        "delivery_date_local": day,
        "block": block,
        "product": product,
        "direction": direction,
        "block_start_utc": start,
        "block_end_utc": start + timedelta(hours=hours),
        "block_hours": hours,
        "data_available": available,
        "marginal_price_eur_mw_h": price,
    }


def test_historical_p50_of_ten_to_one_hundred_is_55() -> None:
    prices = list(range(10, 101, 10))
    rows = []
    start = _utc(2025, 1, 1, 23)
    for i, price in enumerate(prices):
        day = date(2025, 1, i + 1)
        rows.append(
            _block(
                day=day,
                block="0-4",
                start=start + timedelta(days=i),
                hours=4.0,
                price=float(price),
            )
        )
    collapsed = collapse_capacity_blocks(_capacity_table(rows), product="mfrr")
    bids = yearly_historical_capacity_bids(collapsed, 0.50)
    assert bids[2025] == pytest.approx(55.0)
    assert np.quantile(np.arange(10.0, 101.0, 10.0), 0.50) == pytest.approx(55.0)


def test_clearing_is_inclusive_and_equality_clears() -> None:
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=55.0),
        _block(day=date(2025, 1, 1), block="4-8", start=_utc(2025, 1, 1, 3), hours=4, price=54.9),
        _block(day=date(2025, 1, 1), block="8-12", start=_utc(2025, 1, 1, 7), hours=4, price=55.1),
    ]
    collapsed = collapse_capacity_blocks(_capacity_table(rows), product="mfrr")
    bid = 55.0
    contracted = [
        item.block
        for item in collapsed
        if bid <= item.marginal_price_eur_mw_h
    ]
    assert contracted == ["0-4", "8-12"]


def test_historical_bids_are_by_local_delivery_year() -> None:
    rows = [
        _block(day=date(2024, 12, 31), block="20-24", start=_utc(2024, 12, 31, 19), hours=4, price=10.0),
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=100.0),
    ]
    collapsed = collapse_capacity_blocks(_capacity_table(rows), product="mfrr")
    bids = yearly_historical_capacity_bids(collapsed, 0.50)
    assert bids[2024] == pytest.approx(10.0)
    assert bids[2025] == pytest.approx(100.0)


def test_unavailable_null_nonfinite_and_downward_rows_are_excluded() -> None:
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=10.0),
        _block(
            day=date(2025, 1, 1),
            block="4-8",
            start=_utc(2025, 1, 1, 3),
            hours=4,
            price=999.0,
            available=False,
        ),
        _block(
            day=date(2025, 1, 1),
            block="8-12",
            start=_utc(2025, 1, 1, 7),
            hours=4,
            price=float("nan"),
        ),
        _block(
            day=date(2025, 1, 1),
            block="12-16",
            start=_utc(2025, 1, 1, 11),
            hours=4,
            price=20.0,
            direction="down",
        ),
        _block(
            day=date(2025, 1, 1),
            block="16-20",
            start=_utc(2025, 1, 1, 15),
            hours=4,
            price=30.0,
            product="afrr",
        ),
    ]
    collapsed = collapse_capacity_blocks(_capacity_table(rows), product="mfrr")
    assert [item.block for item in collapsed] == ["0-4"]
    assert collapsed[0].marginal_price_eur_mw_h == pytest.approx(10.0)


def test_duplicate_steps_collapse_to_maximum_marginal() -> None:
    start = _utc(2024, 12, 31, 23)
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=start, hours=4, price=12.0),
        _block(
            day=date(2025, 1, 1),
            block="0-4",
            start=start + timedelta(minutes=15),
            hours=4,
            price=18.0,
        ),
    ]
    rows[1]["block_end_utc"] = start + timedelta(hours=4)
    collapsed = collapse_capacity_blocks(_capacity_table(rows), product="mfrr")
    assert len(collapsed) == 1
    assert collapsed[0].marginal_price_eur_mw_h == pytest.approx(18.0)
    assert collapsed[0].block_start_utc == start
    assert collapsed[0].block_end_utc == start + timedelta(hours=4)


def test_negative_historical_quantile_is_not_clipped() -> None:
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=-8.0),
        _block(day=date(2025, 1, 1), block="4-8", start=_utc(2025, 1, 1, 3), hours=4, price=-2.0),
    ]
    collapsed = collapse_capacity_blocks(_capacity_table(rows), product="mfrr")
    bids = yearly_historical_capacity_bids(collapsed, 0.50)
    assert bids[2025] < 0.0


def test_fixed_negative_capacity_bid_fails_in_configuration() -> None:
    with pytest.raises(ConfigError, match="upward_price_eur_mw_h"):
        FixedMinimumCapacityBid(upward_price_eur_mw_h=-1.0)


def _qh_stamps(n: int, start: datetime) -> tuple[datetime, ...]:
    return tuple(start + timedelta(minutes=15 * i) for i in range(n))


def test_balanced_uses_p50_and_passive_uses_p90() -> None:
    stamps = _qh_stamps(40, _utc(2025, 6, 1, 0))
    cbmp = np.arange(10.0, 10.0 + 40.0, dtype=np.float64)
    has_up = np.ones(40, dtype=bool)
    da = np.full(40, 20.0)
    balanced = yearly_energy_bids(
        stamps, da, cbmp, has_up, quantile=0.50, eta_pump=1.0, eta_turbine=1.0
    )
    passive = yearly_energy_bids(
        stamps, da, cbmp, has_up, quantile=0.90, eta_pump=1.0, eta_turbine=1.0
    )
    assert ENERGY_PROFILE_QUANTILE["balanced"] == 0.50
    assert ENERGY_PROFILE_QUANTILE["passive"] == 0.90
    assert balanced[2025] == pytest.approx(float(np.quantile(cbmp, 0.50)))
    assert passive[2025] == pytest.approx(float(np.quantile(cbmp, 0.90)))
    assert passive[2025] > balanced[2025]


def test_stored_energy_floor_and_activation_quantile_sides() -> None:
    stamps = _qh_stamps(40, _utc(2025, 6, 1, 0))
    has_up = np.ones(40, dtype=bool)
    cheap_da = np.full(40, 10.0)
    expensive_cbmp = np.full(40, 1.0)
    floor = yearly_energy_bids(
        stamps, cheap_da, expensive_cbmp, has_up, quantile=0.50, eta_pump=0.5, eta_turbine=0.5
    )
    assert floor[2025] == pytest.approx(10.0 / 0.25)
    high_cbmp = np.full(40, 80.0)
    quantile_side = yearly_energy_bids(
        stamps, cheap_da, high_cbmp, has_up, quantile=0.50, eta_pump=1.0, eta_turbine=1.0
    )
    assert quantile_side[2025] == pytest.approx(80.0)


def test_finite_side_or_neither_energy_bid() -> None:
    stamps = _qh_stamps(10, _utc(2025, 6, 1, 0))
    da = np.full(10, 12.0)
    cbmp = np.full(10, np.nan)
    none_on = np.zeros(10, dtype=bool)
    neither = yearly_energy_bids(
        stamps, da, cbmp, none_on, quantile=0.50, eta_pump=0.84, eta_turbine=0.90
    )
    assert np.isnan(neither.get(2025, float("nan")))
    has_up = np.ones(10, dtype=bool)
    only_q = yearly_energy_bids(
        stamps,
        da,
        np.full(10, 40.0),
        has_up,
        quantile=0.50,
        eta_pump=0.84,
        eta_turbine=0.90,
    )
    assert only_q[2025] == pytest.approx(40.0)
    only_cost = yearly_energy_bids(
        _qh_stamps(40, _utc(2025, 6, 1, 0)),
        np.full(40, 8.0),
        np.full(40, np.nan),
        np.zeros(40, dtype=bool),
        quantile=0.50,
        eta_pump=1.0,
        eta_turbine=1.0,
    )
    assert only_cost[2025] == pytest.approx(8.0)


def test_energy_bids_use_utc_year_and_skip_short_utc_days() -> None:
    late_2024 = _qh_stamps(4, _utc(2024, 12, 31, 23))
    day_2025 = _qh_stamps(40, _utc(2025, 1, 1, 0))
    stamps = late_2024 + day_2025
    da = np.concatenate([np.full(4, 5.0), np.full(40, 20.0)])
    cbmp = np.concatenate([np.full(4, 100.0), np.full(40, 30.0)])
    has_up = np.ones(len(stamps), dtype=bool)
    bids = yearly_energy_bids(
        stamps, da, cbmp, has_up, quantile=0.50, eta_pump=1.0, eta_turbine=1.0
    )
    assert bids[2024] == pytest.approx(100.0)
    assert bids[2025] == pytest.approx(max(float(np.quantile(np.full(40, 30.0), 0.50)), 20.0))


def test_inconsistent_block_hours_are_rejected() -> None:
    start = _utc(2024, 12, 31, 23)
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=start, hours=4, price=10.0),
        _block(day=date(2025, 1, 1), block="0-4", start=start, hours=5, price=11.0),
    ]
    with pytest.raises(MarketInputError, match="inconsistent published block_hours"):
        collapse_capacity_blocks(_capacity_table(rows), product="mfrr")


def test_capacity_bid_mapping_is_read_only() -> None:
    rows = [
        _block(
            day=date(2025, 1, 1),
            block="0-4",
            start=_utc(2024, 12, 31, 23),
            hours=4.0,
            price=50.0,
        )
    ]
    collapsed = collapse_capacity_blocks(_capacity_table(rows), product="mfrr")
    historical = _capacity_bids_by_year(collapsed, MFRRCase())
    fixed = _capacity_bids_by_year(
        collapsed, MFRRCase(capacity_bid=FixedMinimumCapacityBid(1.0))
    )
    for mapping in (historical, fixed):
        with pytest.raises(TypeError):
            mapping[2025] = 0.0
