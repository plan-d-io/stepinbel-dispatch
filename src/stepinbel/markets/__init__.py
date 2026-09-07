"""Market adapters. Day-ahead, mFRR, and aFRR are implemented."""

from stepinbel.markets.afrr import build_afrr_inputs
from stepinbel.markets.base import MarketDispatchInputs, MarketInputError
from stepinbel.markets.da import build_day_ahead_inputs
from stepinbel.markets.mfrr import build_mfrr_inputs

__all__ = [
    "MarketDispatchInputs",
    "MarketInputError",
    "build_day_ahead_inputs",
    "build_mfrr_inputs",
    "build_afrr_inputs",
]
