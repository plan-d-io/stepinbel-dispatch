"""mFRR upward energy and capacity adapter."""

from __future__ import annotations

from datetime import timezone
from typing import Mapping

import numpy as np

from stepinbel.config import (
    FixedMinimumCapacityBid,
    HistoricalQuantileCapacityBid,
    MFRRCase,
    SimulationConfig,
)
from stepinbel.data.load import MarketDataSlice
from stepinbel.markets.base import MarketDispatchInputs, MarketInputError
from stepinbel.markets.bidding import (
    ENERGY_PROFILE_QUANTILE,
    _CAPACITY_COLUMNS,
    _CONVERSION_ERRORS,
    CollapsedCapacityBlock,
    boolean_flags,
    build_capacity_commitments,
    collapse_capacity_blocks,
    finite_series,
    float_series,
    qh_timestamps,
    reject_partial_capacity_blocks,
    require_table_columns,
    yearly_capacity_bids,
    yearly_energy_bids,
)
from stepinbel.optimizer.types import ModelError

__all__ = ["build_mfrr_inputs"]

_DA_COLUMNS: tuple[str, ...] = ("datetime_utc", "da_price_eur_mwh")
_BALANCING_COLUMNS: tuple[str, ...] = ("datetime_utc", "has_mfrr_up", "cbmp_mfrr_up")


def build_mfrr_inputs(data_slice: MarketDataSlice) -> MarketDispatchInputs:
    """Translate a validated mFRR slice into solver-neutral market arrays."""
    if not isinstance(data_slice.config.market_case, MFRRCase):
        raise ModelError("build_mfrr_inputs requires an MFRRCase")
    if data_slice.balancing is None or data_slice.capacity_blocks is None:
        raise MarketInputError("mFRR requires aligned balancing and capacity-block tables")
    require_table_columns(data_slice.da_prices, _DA_COLUMNS, "day-ahead")
    require_table_columns(data_slice.balancing, _BALANCING_COLUMNS, "balancing")
    require_table_columns(data_slice.capacity_blocks, _CAPACITY_COLUMNS, "capacity")

    timestamps = qh_timestamps(data_slice.da_prices, "day-ahead")
    balancing_stamps = qh_timestamps(data_slice.balancing, "balancing")
    if timestamps != balancing_stamps:
        raise MarketInputError("mFRR: balancing timestamps do not match day-ahead timestamps")
    n = len(timestamps)
    expected = data_slice.period.window.interval_count
    if n == 0 or n != expected:
        raise MarketInputError(
            f"mFRR: {n} intervals, expected {expected} for the resolved period"
        )

    da_price = finite_series(data_slice.da_prices, "da_price_eur_mwh", "day-ahead prices")
    has_up = boolean_flags(data_slice.balancing, "has_mfrr_up")
    cbmp = float_series(data_slice.balancing, "cbmp_mfrr_up")
    if da_price.shape != (n,) or has_up.shape != (n,) or cbmp.shape != (n,):
        raise MarketInputError("mFRR: DA, balancing, and timestamp lengths are not aligned")

    config = data_slice.config
    market_case = config.market_case
    cap_mw = _deliverable_upward_mw(config)
    pump_mw = float(config.asset.power_pump_mw)
    window_start = data_slice.period.window.start_utc
    window_end = data_slice.period.window.end_exclusive_utc

    try:
        collapsed = collapse_capacity_blocks(
            data_slice.capacity_blocks, product="mfrr", direction="up"
        )
    except MarketInputError:
        raise
    except _CONVERSION_ERRORS as exc:
        raise MarketInputError(
            "capacity table cannot be converted into mFRR bidding inputs"
        ) from exc
    reject_partial_capacity_blocks(
        collapsed, window_start, window_end, market_label="mFRR"
    )
    yearly_cap = _capacity_bids_by_year(collapsed, market_case)
    energy_quantile = ENERGY_PROFILE_QUANTILE[market_case.activation_profile]
    yearly_energy = yearly_energy_bids(
        timestamps,
        da_price,
        cbmp,
        has_up,
        quantile=energy_quantile,
        eta_pump=float(config.asset.eta_pump),
        eta_turbine=float(config.asset.eta_turbine),
    )
    commitments, contracted_mask = build_capacity_commitments(
        collapsed,
        timestamps,
        yearly_cap,
        product="mfrr",
        cap_mw=cap_mw,
        coverage_hours=float(market_case.capacity_coverage_hours),
        window_end=window_end,
    )
    energy_bid = np.array(
        [
            yearly_energy.get(stamp.astimezone(timezone.utc).year, float("nan"))
            for stamp in timestamps
        ],
        dtype=np.float64,
    )
    eligible = (
        has_up
        & contracted_mask
        & np.isfinite(cbmp)
        & np.isfinite(energy_bid)
        & (cbmp >= energy_bid)
    )
    sell = np.where(eligible, np.nan_to_num(cbmp, nan=0.0), 0.0)
    sell_ub = np.where(eligible, cap_mw, 0.0)
    buy_ub = np.full(n, pump_mw, dtype=np.float64)
    return MarketDispatchInputs(
        sell_price_eur_mwh=sell,
        buy_price_eur_mwh=da_price,
        sell_upper_mw=sell_ub,
        buy_upper_mw=buy_ub,
        capacity_commitments=commitments,
        day_ahead_price_eur_mwh=np.array(da_price, dtype=np.float64, copy=True),
    )


def _deliverable_upward_mw(config: SimulationConfig) -> float:
    return min(float(config.asset.power_turbine_mw), float(config.effective_grid_export_mw()))


def _capacity_bids_by_year(
    collapsed: tuple[CollapsedCapacityBlock, ...],
    market_case: MFRRCase,
) -> Mapping[int, float]:
    bid = market_case.capacity_bid
    if isinstance(bid, (HistoricalQuantileCapacityBid, FixedMinimumCapacityBid)):
        return yearly_capacity_bids(collapsed, bid, direction="up")
    raise MarketInputError("unsupported mFRR capacity bid")
