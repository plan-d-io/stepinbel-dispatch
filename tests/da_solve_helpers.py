"""Shared builders for optimizer unit tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np

from stepinbel.config import (
    AssetConfig,
    DayAheadCase,
    MachineCommitmentConfig,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.data.coverage import ResolvedPeriod, window_from_period
from stepinbel.markets.base import MarketDispatchInputs
from stepinbel.optimizer import SolverOptions
from stepinbel.optimizer.solve import solve_from_inputs
from stepinbel.optimizer.types import TERMINATION_TIME_LIMIT_FEASIBLE


def utc_horizon(n: int, start: datetime | None = None) -> tuple[datetime, ...]:
    instant = start or datetime(2025, 1, 1, tzinfo=timezone.utc)
    values = []
    for _ in range(n):
        values.append(instant)
        instant += timedelta(minutes=15)
    return tuple(values)


def resolved_da_period(n: int, start: datetime | None = None) -> tuple[UtcPeriod, ResolvedPeriod]:
    stamps = utc_horizon(n, start)
    period = UtcPeriod(stamps[0], stamps[-1] + timedelta(minutes=15))
    window = window_from_period(period)
    resolved = ResolvedPeriod(
        period=period,
        window=window,
        market="da",
        required_sources=("da_prices_qh",),
        pv_region=None,
    )
    return period, resolved


def solve_arrays(
    *,
    sell: np.ndarray,
    buy: np.ndarray | None = None,
    sell_ub: np.ndarray | None = None,
    buy_ub: np.ndarray | None = None,
    asset: AssetConfig | None = None,
    site: SiteConfig | None = None,
    commitments=(),
    pv_load_factor: np.ndarray | None = None,
    wind_load_factor: np.ndarray | None = None,
    options: SolverOptions | None = None,
    commitment: MachineCommitmentConfig | None = None,
    day_ahead_price: np.ndarray | None = None,
):
    sell = np.asarray(sell, dtype=np.float64)
    n = sell.shape[0]
    buy = sell.copy() if buy is None else np.asarray(buy, dtype=np.float64)
    ones = np.ones(n, dtype=np.float64)
    da_price = sell.copy() if day_ahead_price is None else np.asarray(day_ahead_price, dtype=np.float64)
    market = MarketDispatchInputs(
        sell_price_eur_mwh=sell,
        buy_price_eur_mwh=buy,
        sell_upper_mw=ones if sell_ub is None else sell_ub,
        buy_upper_mw=ones if buy_ub is None else buy_ub,
        capacity_commitments=tuple(commitments),
        day_ahead_price_eur_mwh=da_price,
    )
    period, resolved = resolved_da_period(n)
    config = SimulationConfig(
        period=period,
        market_case=DayAheadCase(),
        asset=asset or AssetConfig(),
        site=site or SiteConfig(),
        machine_commitment=commitment or MachineCommitmentConfig(),
    )
    if config.pv_enabled() or config.wind_enabled():
        sources = ["da_prices_qh"]
        if config.pv_enabled():
            sources.append("pv_profile_qh")
        if config.wind_enabled():
            sources.append("wind_profile_qh")
        resolved = ResolvedPeriod(
            period=resolved.period,
            window=resolved.window,
            market=resolved.market,
            required_sources=tuple(sources),
            pv_region=config.site.pv_region if config.pv_enabled() else None,
            wind_profile_id=config.site.wind_profile_id if config.wind_enabled() else None,
        )
    return solve_from_inputs(
        config=config,
        period=resolved,
        manifest_sha256="00" * 32,
        timestamps=utc_horizon(n),
        market_inputs=market,
        pv_load_factor=pv_load_factor,
        wind_load_factor=wind_load_factor,
        options=options,
    )


def relabel_time_limit_feasible(solved, *, achieved_gap: float = 0.02):
    """Construct a usable time-limited outcome from a physically valid solve."""
    diagnostics = dict(solved.diagnostics)
    diagnostics["termination"] = TERMINATION_TIME_LIMIT_FEASIBLE
    diagnostics["achieved_mip_gap"] = float(achieved_gap)
    diagnostics["mip_gap"] = float(achieved_gap)
    diagnostics["has_incumbent"] = True
    return replace(
        solved,
        status="time_limit",
        status_raw="kTimeLimit",
        classification=TERMINATION_TIME_LIMIT_FEASIBLE,
        has_incumbent=True,
        mip_gap=float(achieved_gap),
        diagnostics=diagnostics,
    )
