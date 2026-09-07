from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pyarrow as pa
import pytest

from stepinbel.config import AFRRCase, FixedMinimumCapacityBid, HistoricalQuantileCapacityBid
from stepinbel.markets.base import MarketInputError
from stepinbel.markets.bidding import (
    ENERGY_PROFILE_QUANTILE,
    collapse_capacity_blocks,
    yearly_capacity_bids,
    yearly_energy_bids,
    yearly_historical_capacity_bids,
)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _capacity_table(rows: list[dict]) -> pa.Table:
    return pa.table(
        {
            "delivery_date_local": [row["delivery_date_local"] for row in rows],
            "block": [row["block"] for row in rows],
            "product": [row.get("product", "afrr") for row in rows],
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
    direction: str,
    available: bool = True,
    product: str = "afrr",
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


def test_up_and_down_blocks_collapse_independently() -> None:
    start = _utc(2024, 12, 31, 23)
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=start, hours=4, price=10.0, direction="up"),
        _block(day=date(2025, 1, 1), block="0-4", start=start, hours=4, price=4.0, direction="down"),
        _block(
            day=date(2025, 1, 1),
            block="4-8",
            start=_utc(2025, 1, 1, 3),
            hours=4,
            price=99.0,
            direction="up",
            available=False,
        ),
        _block(
            day=date(2025, 1, 1),
            block="8-12",
            start=_utc(2025, 1, 1, 7),
            hours=4,
            price=float("nan"),
            direction="down",
        ),
        _block(
            day=date(2025, 1, 1),
            block="12-16",
            start=_utc(2025, 1, 1, 11),
            hours=4,
            price=20.0,
            direction="up",
            product="mfrr",
        ),
    ]
    table = _capacity_table(rows)
    up = collapse_capacity_blocks(table, product="afrr", direction="up")
    down = collapse_capacity_blocks(table, product="afrr", direction="down")
    assert [item.block for item in up] == ["0-4"]
    assert [item.block for item in down] == ["0-4"]
    assert up[0].marginal_price_eur_mw_h == pytest.approx(10.0)
    assert down[0].marginal_price_eur_mw_h == pytest.approx(4.0)


def test_afrr_auction_steps_collapse_to_maximum_marginal() -> None:
    start = _utc(2024, 12, 31, 23)
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=start, hours=4, price=12.0, direction="up"),
        _block(
            day=date(2025, 1, 1),
            block="0-4",
            start=start + timedelta(minutes=15),
            hours=4,
            price=18.0,
            direction="up",
        ),
        _block(day=date(2025, 1, 1), block="0-4", start=start, hours=4, price=3.0, direction="down"),
        _block(day=date(2025, 1, 1), block="0-4", start=start, hours=4, price=7.5, direction="down"),
    ]
    rows[1]["block_end_utc"] = start + timedelta(hours=4)
    table = _capacity_table(rows)
    up = collapse_capacity_blocks(table, product="afrr", direction="up")
    down = collapse_capacity_blocks(table, product="afrr", direction="down")
    assert len(up) == 1 and len(down) == 1
    assert up[0].marginal_price_eur_mw_h == pytest.approx(18.0)
    assert down[0].marginal_price_eur_mw_h == pytest.approx(7.5)
    assert up[0].block_start_utc == start
    assert down[0].block_end_utc == start + timedelta(hours=4)


def test_historical_bids_are_directional_and_by_local_delivery_year() -> None:
    rows = [
        _block(day=date(2024, 12, 31), block="20-24", start=_utc(2024, 12, 31, 19), hours=4, price=10.0, direction="up"),
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=100.0, direction="up"),
        _block(day=date(2024, 12, 31), block="20-24", start=_utc(2024, 12, 31, 19), hours=4, price=2.0, direction="down"),
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=8.0, direction="down"),
    ]
    table = _capacity_table(rows)
    up = collapse_capacity_blocks(table, product="afrr", direction="up")
    down = collapse_capacity_blocks(table, product="afrr", direction="down")
    up_bids = yearly_historical_capacity_bids(up, 0.50)
    down_bids = yearly_historical_capacity_bids(down, 0.50)
    assert up_bids[2024] == pytest.approx(10.0)
    assert up_bids[2025] == pytest.approx(100.0)
    assert down_bids[2024] == pytest.approx(2.0)
    assert down_bids[2025] == pytest.approx(8.0)


def test_fixed_bids_use_respective_upward_and_downward_prices() -> None:
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=50.0, direction="up"),
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=50.0, direction="down"),
    ]
    table = _capacity_table(rows)
    up = collapse_capacity_blocks(table, product="afrr", direction="up")
    down = collapse_capacity_blocks(table, product="afrr", direction="down")
    bid = FixedMinimumCapacityBid(3.25, 1.5)
    up_map = yearly_capacity_bids(up, bid, direction="up")
    down_map = yearly_capacity_bids(down, bid, direction="down")
    assert up_map[2025] == pytest.approx(3.25)
    assert down_map[2025] == pytest.approx(1.5)


def test_clearing_is_inclusive_and_equality_clears() -> None:
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=55.0, direction="up"),
        _block(day=date(2025, 1, 1), block="4-8", start=_utc(2025, 1, 1, 3), hours=4, price=54.9, direction="up"),
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=5.0, direction="down"),
        _block(day=date(2025, 1, 1), block="4-8", start=_utc(2025, 1, 1, 3), hours=4, price=4.9, direction="down"),
    ]
    table = _capacity_table(rows)
    up = collapse_capacity_blocks(table, product="afrr", direction="up")
    down = collapse_capacity_blocks(table, product="afrr", direction="down")
    assert [item.block for item in up if 55.0 <= item.marginal_price_eur_mw_h] == ["0-4"]
    assert [item.block for item in down if 5.0 <= item.marginal_price_eur_mw_h] == ["0-4"]


def test_capacity_bidding_is_independent_of_activation_profile() -> None:
    quantile = HistoricalQuantileCapacityBid(0.50)
    balanced = AFRRCase(activation_profile="balanced", capacity_bid=quantile)
    passive = AFRRCase(activation_profile="passive", capacity_bid=quantile)
    assert balanced.capacity_bid == passive.capacity_bid
    assert ENERGY_PROFILE_QUANTILE[balanced.activation_profile] != ENERGY_PROFILE_QUANTILE[
        passive.activation_profile
    ]


def test_negative_historical_quantile_is_not_clipped() -> None:
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=-8.0, direction="up"),
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=-2.0, direction="down"),
    ]
    table = _capacity_table(rows)
    up = yearly_historical_capacity_bids(
        collapse_capacity_blocks(table, product="afrr", direction="up"), 0.50
    )
    down = yearly_historical_capacity_bids(
        collapse_capacity_blocks(table, product="afrr", direction="down"), 0.50
    )
    assert up[2025] < 0.0
    assert down[2025] < 0.0


def test_capacity_bid_mappings_are_read_only() -> None:
    rows = [
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=50.0, direction="up"),
        _block(day=date(2025, 1, 1), block="0-4", start=_utc(2024, 12, 31, 23), hours=4, price=20.0, direction="down"),
    ]
    table = _capacity_table(rows)
    up = collapse_capacity_blocks(table, product="afrr", direction="up")
    down = collapse_capacity_blocks(table, product="afrr", direction="down")
    historical = yearly_capacity_bids(up, HistoricalQuantileCapacityBid(0.50), direction="up")
    fixed = yearly_capacity_bids(down, FixedMinimumCapacityBid(1.0, 2.0), direction="down")
    for mapping in (historical, fixed):
        with pytest.raises(TypeError):
            mapping[2025] = 0.0


def test_balanced_upward_energy_bid_uses_p50_and_passive_uses_p90() -> None:
    stamps = tuple(_utc(2025, 6, 1, 0) + timedelta(minutes=15 * i) for i in range(40))
    cbmp = np.arange(10.0, 50.0, dtype=np.float64)
    has_up = np.ones(40, dtype=bool)
    da = np.full(40, 1.0)
    balanced = yearly_energy_bids(
        stamps, da, cbmp, has_up, quantile=0.50, eta_pump=1.0, eta_turbine=1.0
    )
    passive = yearly_energy_bids(
        stamps, da, cbmp, has_up, quantile=0.90, eta_pump=1.0, eta_turbine=1.0
    )
    assert balanced[2025] == pytest.approx(float(np.quantile(cbmp, 0.50)))
    assert passive[2025] == pytest.approx(float(np.quantile(cbmp, 0.90)))
    assert passive[2025] > balanced[2025]
