"""Shared builders for one-case workflow and reporting tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

from stepinbel.config import (
    AFRRCase,
    AssetConfig,
    BelgianDeliveryPeriod,
    DayAheadCase,
    FixedMinimumCapacityBid,
    HistoricalQuantileCapacityBid,
    MFRRCase,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.data.coverage import ResolvedPeriod, window_from_period
from stepinbel.optimizer import (
    DispatchResult,
    DispatchSummary,
    FeasibilityReport,
    SolverMetadata,
    SolverOptions,
)
from stepinbel.optimizer.types import CAPACITY_RESULT_SCHEMA, DISPATCH_COLUMNS
from stepinbel.workflows import build_case_run_request

REPO_DATA = Path(__file__).resolve().parents[1] / "data"


def utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def da_period(hours: int = 2, start: datetime | None = None) -> UtcPeriod:
    begin = start or utc(2025, 1, 15, 0, 0)
    return UtcPeriod(begin, begin + timedelta(hours=hours))


def one_day_belgian(day: date | None = None) -> BelgianDeliveryPeriod:
    value = day or date(2025, 1, 15)
    return BelgianDeliveryPeriod(value, value)


def da_config(
    *,
    period=None,
    asset: AssetConfig | None = None,
    site: SiteConfig | None = None,
) -> SimulationConfig:
    return SimulationConfig(
        period=period or da_period(),
        market_case=DayAheadCase(),
        asset=asset or AssetConfig(),
        site=site or SiteConfig(),
    )


def mfrr_config(
    *,
    period=None,
    asset: AssetConfig | None = None,
    site: SiteConfig | None = None,
    passive: bool = False,
    fixed: bool = False,
) -> SimulationConfig:
    bid = (
        FixedMinimumCapacityBid(1.0)
        if fixed
        else HistoricalQuantileCapacityBid(0.5)
    )
    return SimulationConfig(
        period=period or one_day_belgian(),
        market_case=MFRRCase(
            activation_profile="passive" if passive else "balanced",
            capacity_bid=bid,
        ),
        asset=asset or AssetConfig(),
        site=site or SiteConfig(),
    )


def afrr_config(
    *,
    period=None,
    asset: AssetConfig | None = None,
    site: SiteConfig | None = None,
    fixed: bool = False,
    up_fraction: float = 1.0,
) -> SimulationConfig:
    bid: HistoricalQuantileCapacityBid | FixedMinimumCapacityBid
    if fixed:
        bid = FixedMinimumCapacityBid(2.0, 3.0)
    else:
        bid = HistoricalQuantileCapacityBid(0.25)
    return SimulationConfig(
        period=period or one_day_belgian(),
        market_case=AFRRCase(
            activation_profile="balanced",
            capacity_bid=bid,
            up_capacity_fraction=up_fraction,
        ),
        asset=asset or AssetConfig(),
        site=site or SiteConfig(),
    )


def comparison_configs(
    *,
    period=None,
    asset: AssetConfig | None = None,
    site: SiteConfig | None = None,
    markets: tuple[str, ...] | None = None,
) -> dict[str, SimulationConfig]:
    shared_period = period or one_day_belgian()
    shared_asset = asset or AssetConfig()
    shared_site = site or SiteConfig()
    configs = {
        "da": da_config(period=shared_period, asset=shared_asset, site=shared_site),
        "mfrr": mfrr_config(period=shared_period, asset=shared_asset, site=shared_site),
        "afrr": afrr_config(period=shared_period, asset=shared_asset, site=shared_site),
    }
    if markets is None:
        return configs
    return {name: configs[name] for name in markets}


def build_request(
    data_root: Path,
    output: Path,
    config: SimulationConfig | None = None,
    *,
    run_id: str = "test-run-001",
    created_at: datetime | None = None,
    solver_options: SolverOptions | None = None,
):
    return build_case_run_request(
        config or da_config(),
        data_root,
        output,
        solver_options=solver_options,
        run_id=run_id,
        created_at_utc=created_at or utc(2026, 1, 1, 12, 0),
    )


def empty_capacity() -> pa.Table:
    return pa.table({name: [] for name in CAPACITY_RESULT_SCHEMA.names}, schema=CAPACITY_RESULT_SCHEMA)


def dispatch_table(
    stamps: list[datetime],
    *,
    sell: list[float] | None = None,
    buy: list[float] | None = None,
    pump: list[float] | None = None,
    pump_grid: list[float] | None = None,
    turbine: list[float] | None = None,
    energy_net: list[float] | None = None,
    pv_rev: list[float] | None = None,
) -> pa.Table:
    n = len(stamps)
    zeros = [0.0] * n
    sell = sell if sell is not None else [10.0] * n
    buy = buy if buy is not None else [10.0] * n
    pump = pump if pump is not None else zeros
    pump_grid = pump_grid if pump_grid is not None else list(pump)
    turbine = turbine if turbine is not None else zeros
    energy_net = energy_net if energy_net is not None else zeros
    pv_rev = pv_rev if pv_rev is not None else zeros
    reservoir = [2.0 + 0.1 * i for i in range(n + 1)]
    values = {
        "datetime_utc": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
        "market_sell_price_eur_mwh": sell,
        "market_buy_price_eur_mwh": buy,
        "p_pump_mw": pump,
        "p_pump_grid_mw": pump_grid,
        "p_turbine_mw": turbine,
        "reservoir_start_mwh": reservoir[:-1],
        "reservoir_end_mwh": reservoir[1:],
        "pump_ramp_up_mw": zeros,
        "turbine_ramp_up_mw": zeros,
        "pv_available_mw": zeros,
        "pv_to_pump_mw": zeros,
        "pv_export_mw": zeros,
        "pv_curtail_mw": zeros,
        "pv_export_price_eur_mwh": [None] * n,
        "market_energy_net_eur": energy_net,
        "pv_revenue_eur": pv_rev,
        "total_revenue_eur": [energy_net[i] + pv_rev[i] for i in range(n)],
    }
    table = pa.table(values)
    assert tuple(table.column_names) == DISPATCH_COLUMNS
    return table


def capacity_table(
    rows: list[dict[str, object]] | None = None,
) -> pa.Table:
    empty = empty_capacity()
    if not rows:
        return empty
    return pa.table(
        {name: [row[name] for row in rows] for name in CAPACITY_RESULT_SCHEMA.names},
        schema=CAPACITY_RESULT_SCHEMA,
    )


def summary_from_dispatch(
    dispatch: pa.Table,
    *,
    capacity_revenue: float = 0.0,
    e_max: float = 4.0 / 0.9,
) -> DispatchSummary:
    n = dispatch.num_rows
    dt = 0.25
    energy_net = dispatch.column("market_energy_net_eur").to_pylist()
    pv_rev = dispatch.column("pv_revenue_eur").to_pylist()
    pump = dispatch.column("p_pump_mw").to_pylist()
    turbine = dispatch.column("p_turbine_mw").to_pylist()
    sell = dispatch.column("market_sell_price_eur_mwh").to_pylist()
    buy = dispatch.column("market_buy_price_eur_mwh").to_pylist()
    pump_grid = dispatch.column("p_pump_grid_mw").to_pylist()
    energy_gross = sum(dt * sell[i] * turbine[i] for i in range(n))
    charging = sum(dt * buy[i] * pump_grid[i] for i in range(n))
    net = float(sum(energy_net))
    pv = float(sum(pv_rev))
    return DispatchSummary(
        interval_count=n,
        duration_hours=float(n * dt),
        e_max_mwh=e_max,
        reservoir_initial_mwh=float(dispatch.column("reservoir_start_mwh").to_pylist()[0]),
        reservoir_final_mwh=float(dispatch.column("reservoir_end_mwh").to_pylist()[-1]),
        energy_gross_eur=float(energy_gross),
        grid_charging_cost_eur=float(charging),
        market_energy_net_eur=net,
        capacity_revenue_eur=capacity_revenue,
        pv_revenue_eur=pv,
        total_site_revenue_eur=net + capacity_revenue + pv,
        pumped_mwh=float(sum(pump) * dt),
        turbined_mwh=float(sum(turbine) * dt),
        pv_available_mwh=0.0,
        pv_self_consumed_mwh=0.0,
        pv_exported_mwh=0.0,
        pv_curtailed_mwh=0.0,
        simultaneous_interval_count=sum(
            1 for i in range(n) if pump[i] > 1e-6 and turbine[i] > 1e-6
        ),
        simultaneous_pump_mwh=0.0,
        simultaneous_turbine_mwh=0.0,
        simultaneous_overlap_mwh=sum(
            min(pump[i], turbine[i]) * dt
            for i in range(n)
            if pump[i] > 1e-6 and turbine[i] > 1e-6
        ),
        n_pump_ramp_up_vars=0,
        n_turbine_ramp_up_vars=0,
    )


def fake_result(
    config: SimulationConfig,
    dispatch: pa.Table,
    capacity: pa.Table | None = None,
) -> DispatchResult:
    period = config.period
    window = window_from_period(period)
    resolved = ResolvedPeriod(
        period=period,
        window=window,
        market=config.market,
        required_sources=("da_prices_qh",),
        pv_region=None,
    )
    capacity = capacity if capacity is not None else empty_capacity()
    revenue = float(sum(capacity.column("capacity_revenue_eur").to_pylist() or [0.0]))
    return DispatchResult(
        config=config,
        period=resolved,
        manifest_sha256="0" * 64,
        dispatch=dispatch,
        capacity_results=capacity,
        summary=summary_from_dispatch(dispatch, capacity_revenue=revenue),
        solver=SolverMetadata(
            solver_name="HiGHS",
            solver_version="1.15.1",
            package_version="1.15.1",
            status="optimal",
            status_raw="Optimal",
            build_s=0.01,
            solve_s=0.02,
            end_to_end_s=0.03,
            num_col=1,
            num_row=1,
            num_nz=1,
            num_integer=0,
            num_binary=0,
            continuous_lp=True,
            options={"detailed_output": False},
            diagnostics={},
        ),
        feasibility=FeasibilityReport(
            max_bound_residual=0.0,
            max_initial_terminal_residual_mwh=0.0,
            max_balance_residual_mwh=0.0,
            max_ramp_residual_mw=0.0,
            max_pv_residual_mw=0.0,
            max_grid_residual_mw=0.0,
            max_capacity_residual=0.0,
            max_interval_accounting_residual_eur=0.0,
            max_summary_accounting_residual_eur=0.0,
            max_objective_residual_eur=0.0,
            ok=True,
        ),
    )


def two_asset_candidates():
    from stepinbel.workflows import AssetSweepCandidate

    return (
        AssetSweepCandidate(
            "small",
            "1 MW / 2 h",
            AssetConfig(power_pump_mw=1.0, power_turbine_mw=1.0, storage_hours=2.0),
        ),
        AssetSweepCandidate(
            "large",
            "2 MW / 4 h",
            AssetConfig(power_pump_mw=2.0, power_turbine_mw=2.0, storage_hours=4.0),
        ),
    )
