"""Day-ahead market adapter. No capacity leg and no activation profile."""

from __future__ import annotations

import numpy as np

from stepinbel.config import DayAheadCase
from stepinbel.data.load import MarketDataSlice
from stepinbel.markets.base import MarketDispatchInputs, MarketInputError
from stepinbel.optimizer.types import ModelError

__all__ = ["build_day_ahead_inputs"]


def build_day_ahead_inputs(data_slice: MarketDataSlice) -> MarketDispatchInputs:
    """Translate a validated DA slice into solver-neutral market arrays."""
    if not isinstance(data_slice.config.market_case, DayAheadCase):
        raise ModelError("build_day_ahead_inputs requires a DayAheadCase")
    prices = _da_prices(data_slice)
    n = prices.shape[0]
    if n == 0:
        raise MarketInputError("day-ahead: empty price series")
    expected = data_slice.period.window.interval_count
    if n != expected:
        raise MarketInputError(
            f"day-ahead: {n} prices, expected {expected} for the resolved period"
        )
    asset = data_slice.config.asset
    sell_ub = np.full(n, float(asset.power_turbine_mw), dtype=np.float64)
    buy_ub = np.full(n, float(asset.power_pump_mw), dtype=np.float64)
    return MarketDispatchInputs(
        sell_price_eur_mwh=prices,
        buy_price_eur_mwh=np.array(prices, dtype=np.float64, copy=True),
        sell_upper_mw=sell_ub,
        buy_upper_mw=buy_ub,
        capacity_commitments=(),
        day_ahead_price_eur_mwh=np.array(prices, dtype=np.float64, copy=True),
    )


def _da_prices(data_slice: MarketDataSlice) -> np.ndarray:
    column = data_slice.da_prices.column("da_price_eur_mwh")
    if column.null_count:
        raise MarketInputError("day-ahead prices contain null values")
    values = np.array(column.to_numpy(zero_copy_only=False), dtype=np.float64, copy=True)
    if values.ndim != 1:
        raise MarketInputError("day-ahead prices must be one-dimensional")
    if not np.all(np.isfinite(values)):
        raise MarketInputError("day-ahead prices contain NaN or infinite values")
    return values
