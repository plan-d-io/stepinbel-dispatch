"""Solver-neutral market dispatch inputs."""

from __future__ import annotations

import numpy as np

from stepinbel.optimizer.types import CapacityCommitment

__all__ = ["MarketInputError", "MarketDispatchInputs"]


class MarketInputError(ValueError):
    """Market arrays or bounds cannot be used as model inputs."""


_MISSING = object()


def _owned_float64(value: object, field: str) -> np.ndarray:
    try:
        array = np.array(value, dtype=np.float64, copy=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketInputError(
            f"{field} cannot be converted to a finite one-dimensional float64 array"
        ) from exc
    if array.ndim != 1:
        raise MarketInputError(f"{field} must be a one-dimensional array")
    if array.size == 0:
        raise MarketInputError(f"{field} is empty")
    if not np.all(np.isfinite(array)):
        raise MarketInputError(f"{field} contains null, NaN, or infinite values")
    array.setflags(write=False)
    return array


class MarketDispatchInputs:
    """Owned, read-only market arrays plus optional capacity commitments."""

    __slots__ = (
        "sell_price_eur_mwh",
        "buy_price_eur_mwh",
        "sell_upper_mw",
        "buy_upper_mw",
        "day_ahead_price_eur_mwh",
        "capacity_commitments",
    )

    def __init__(
        self,
        sell_price_eur_mwh: object,
        buy_price_eur_mwh: object,
        sell_upper_mw: object,
        buy_upper_mw: object,
        capacity_commitments: tuple[CapacityCommitment, ...] = (),
        *,
        day_ahead_price_eur_mwh: object = _MISSING,
    ) -> None:
        sell = _owned_float64(sell_price_eur_mwh, "sell_price_eur_mwh")
        buy = _owned_float64(buy_price_eur_mwh, "buy_price_eur_mwh")
        sell_ub = _owned_float64(sell_upper_mw, "sell_upper_mw")
        buy_ub = _owned_float64(buy_upper_mw, "buy_upper_mw")
        if day_ahead_price_eur_mwh is _MISSING:
            raise MarketInputError("day_ahead_price_eur_mwh is required")
        da_price = _owned_float64(day_ahead_price_eur_mwh, "day_ahead_price_eur_mwh")
        n = sell.shape[0]
        if {buy.shape, sell_ub.shape, buy_ub.shape, da_price.shape} != {(n,)}:
            raise MarketInputError("market arrays must be equal length")
        if np.any(sell_ub < 0.0) or np.any(buy_ub < 0.0):
            raise MarketInputError("sell_upper_mw and buy_upper_mw must be >= 0")
        if not isinstance(capacity_commitments, tuple):
            raise MarketInputError("capacity_commitments must be a tuple")
        for item in capacity_commitments:
            if not isinstance(item, CapacityCommitment):
                raise MarketInputError(
                    "capacity_commitments must contain CapacityCommitment values"
                )
        object.__setattr__(self, "sell_price_eur_mwh", sell)
        object.__setattr__(self, "buy_price_eur_mwh", buy)
        object.__setattr__(self, "sell_upper_mw", sell_ub)
        object.__setattr__(self, "buy_upper_mw", buy_ub)
        object.__setattr__(self, "day_ahead_price_eur_mwh", da_price)
        object.__setattr__(self, "capacity_commitments", capacity_commitments)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("MarketDispatchInputs is immutable")

    @property
    def interval_count(self) -> int:
        return int(self.sell_price_eur_mwh.shape[0])
