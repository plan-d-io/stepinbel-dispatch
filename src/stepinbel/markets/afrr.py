"""aFRR upward energy, downward free-bid (M2), and bidirectional capacity adapter."""

from __future__ import annotations

from datetime import timezone

import numpy as np

from stepinbel.config import AFRRCase, SimulationConfig
from stepinbel.data.load import MarketDataSlice
from stepinbel.markets.base import MarketDispatchInputs, MarketInputError
from stepinbel.markets.bidding import (
    ENERGY_PROFILE_QUANTILE,
    _CAPACITY_COLUMNS,
    _CONVERSION_ERRORS,
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

__all__ = ["build_afrr_inputs"]

_DA_COLUMNS: tuple[str, ...] = ("datetime_utc", "da_price_eur_mwh")
_BALANCING_COLUMNS: tuple[str, ...] = (
    "datetime_utc",
    "has_afrr_up",
    "has_afrr_down",
    "cbmp_afrr_up",
    "cbmp_afrr_down",
)


def build_afrr_inputs(data_slice: MarketDataSlice) -> MarketDispatchInputs:
    """Translate a validated aFRR slice into solver-neutral market arrays."""
    if not isinstance(data_slice.config.market_case, AFRRCase):
        raise ModelError("build_afrr_inputs requires an AFRRCase")
    if data_slice.balancing is None or data_slice.capacity_blocks is None:
        raise MarketInputError("aFRR requires aligned balancing and capacity-block tables")
    require_table_columns(data_slice.da_prices, _DA_COLUMNS, "day-ahead")
    require_table_columns(data_slice.balancing, _BALANCING_COLUMNS, "balancing")
    require_table_columns(data_slice.capacity_blocks, _CAPACITY_COLUMNS, "capacity")

    timestamps = qh_timestamps(data_slice.da_prices, "day-ahead")
    balancing_stamps = qh_timestamps(data_slice.balancing, "balancing")
    if timestamps != balancing_stamps:
        raise MarketInputError("aFRR: balancing timestamps do not match day-ahead timestamps")
    n = len(timestamps)
    expected = data_slice.period.window.interval_count
    if n == 0 or n != expected:
        raise MarketInputError(
            f"aFRR: {n} intervals, expected {expected} for the resolved period"
        )

    da_price = finite_series(data_slice.da_prices, "da_price_eur_mwh", "day-ahead prices")
    has_up = boolean_flags(data_slice.balancing, "has_afrr_up")
    has_down = boolean_flags(data_slice.balancing, "has_afrr_down")
    cbmp_up = float_series(data_slice.balancing, "cbmp_afrr_up")
    cbmp_down = float_series(data_slice.balancing, "cbmp_afrr_down")
    if (
        da_price.shape != (n,)
        or has_up.shape != (n,)
        or has_down.shape != (n,)
        or cbmp_up.shape != (n,)
        or cbmp_down.shape != (n,)
    ):
        raise MarketInputError("aFRR: DA, balancing, and timestamp lengths are not aligned")

    config = data_slice.config
    market_case = config.market_case
    cap_up_mw, cap_down_mw = _directional_capacity_mw(config)
    pump_mw = float(config.asset.power_pump_mw)
    window_start = data_slice.period.window.start_utc
    window_end = data_slice.period.window.end_exclusive_utc

    try:
        collapsed_up = collapse_capacity_blocks(
            data_slice.capacity_blocks, product="afrr", direction="up"
        )
        collapsed_down = collapse_capacity_blocks(
            data_slice.capacity_blocks, product="afrr", direction="down"
        )
    except MarketInputError:
        raise
    except _CONVERSION_ERRORS as exc:
        raise MarketInputError(
            "capacity table cannot be converted into aFRR bidding inputs"
        ) from exc
    reject_partial_capacity_blocks(
        collapsed_up + collapsed_down,
        window_start,
        window_end,
        market_label="aFRR",
    )

    yearly_cap_up = yearly_capacity_bids(
        collapsed_up, market_case.capacity_bid, direction="up"
    )
    yearly_cap_down = yearly_capacity_bids(
        collapsed_down, market_case.capacity_bid, direction="down"
    )
    coverage_hours = float(market_case.capacity_coverage_hours)
    up_commitments, contracted_up = build_capacity_commitments(
        collapsed_up,
        timestamps,
        yearly_cap_up,
        product="afrr",
        cap_mw=cap_up_mw,
        coverage_hours=coverage_hours,
        window_end=window_end,
    )
    down_commitments, _contracted_down = build_capacity_commitments(
        collapsed_down,
        timestamps,
        yearly_cap_down,
        product="afrr",
        cap_mw=cap_down_mw,
        coverage_hours=coverage_hours,
        window_end=window_end,
    )
    commitments = up_commitments + down_commitments

    energy_quantile = ENERGY_PROFILE_QUANTILE[market_case.activation_profile]
    yearly_energy = yearly_energy_bids(
        timestamps,
        da_price,
        cbmp_up,
        has_up,
        quantile=energy_quantile,
        eta_pump=float(config.asset.eta_pump),
        eta_turbine=float(config.asset.eta_turbine),
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
        & contracted_up
        & np.isfinite(cbmp_up)
        & np.isfinite(energy_bid)
        & (cbmp_up >= energy_bid)
    )
    sell = np.where(eligible, np.nan_to_num(cbmp_up, nan=0.0), 0.0)
    sell_ub = np.where(eligible, cap_up_mw, 0.0)

    # M2: free downward activation bids. Pumping is not gated on a downward
    # capacity award or activation profile.
    buy = np.array(da_price, dtype=np.float64, copy=True)
    mask_down = has_down & np.isfinite(cbmp_down)
    buy[mask_down] = np.minimum(da_price[mask_down], cbmp_down[mask_down])
    buy_ub = np.full(n, pump_mw, dtype=np.float64)

    return MarketDispatchInputs(
        sell_price_eur_mwh=sell,
        buy_price_eur_mwh=buy,
        sell_upper_mw=sell_ub,
        buy_upper_mw=buy_ub,
        capacity_commitments=commitments,
        day_ahead_price_eur_mwh=np.array(da_price, dtype=np.float64, copy=True),
    )


def _directional_capacity_mw(config: SimulationConfig) -> tuple[float, float]:
    if not isinstance(config.market_case, AFRRCase):
        raise ModelError("aFRR capacity split requires an AFRRCase")
    fraction = float(config.market_case.up_capacity_fraction)
    cap_up = min(
        float(config.asset.power_turbine_mw) * fraction,
        float(config.effective_grid_export_mw()),
    )
    cap_down = min(
        float(config.asset.power_pump_mw) * (1.0 - fraction),
        float(config.effective_grid_import_mw()),
    )
    return cap_up, cap_down
