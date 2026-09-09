"""Public solve_case entry point for the HiGHS LP/MILP model."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import numpy as np
import pyarrow as pa

from stepinbel.config import AFRRCase, DayAheadCase, MFRRCase, SimulationConfig
from stepinbel.data.coverage import ResolvedPeriod
from stepinbel.data.load import MarketDataSlice
from stepinbel.markets.base import MarketDispatchInputs, MarketInputError
from stepinbel.markets.afrr import build_afrr_inputs
from stepinbel.markets.da import build_day_ahead_inputs
from stepinbel.markets.mfrr import build_mfrr_inputs
from stepinbel.optimizer.checks import check_solution, decode_solution
from stepinbel.optimizer.commitment import resolve_machine_commitment
from stepinbel.optimizer.highs import solve_sparse_model
from stepinbel.optimizer.model import SparseModel, build_sparse_model, prepare_physical
from stepinbel.optimizer.types import (
    CAPACITY_RESULT_SCHEMA,
    DISPATCH_COLUMNS,
    SIMULTANEOUS_TOL_MW,
    DispatchResult,
    DispatchSummary,
    ModelError,
    SolverMetadata,
    SolverOptions,
)


def solve_case(
    data_slice: MarketDataSlice,
    *,
    options: SolverOptions | None = None,
) -> DispatchResult:
    """Solve one validated data slice. Day-ahead, mFRR, and aFRR are implemented."""
    started = time.perf_counter()
    _validate_slice(data_slice)
    market_case = data_slice.config.market_case
    if isinstance(market_case, DayAheadCase):
        market = build_day_ahead_inputs(data_slice)
    elif isinstance(market_case, MFRRCase):
        market = build_mfrr_inputs(data_slice)
    elif isinstance(market_case, AFRRCase):
        market = build_afrr_inputs(data_slice)
    else:
        raise ModelError("unsupported market case")
    pv_factor = _pv_load_factor(data_slice)
    timestamps = _timestamps(data_slice)
    return solve_from_inputs(
        config=data_slice.config,
        period=data_slice.period,
        manifest_sha256=data_slice.manifest_sha256,
        timestamps=timestamps,
        market_inputs=market,
        pv_load_factor=pv_factor,
        options=options,
        started=started,
    )


def solve_from_inputs(
    *,
    config: SimulationConfig,
    period: ResolvedPeriod,
    manifest_sha256: str,
    timestamps: tuple[datetime, ...],
    market_inputs: MarketDispatchInputs,
    pv_load_factor: np.ndarray | None,
    options: SolverOptions | None = None,
    started: float | None = None,
) -> DispatchResult:
    """Solve a prepared physical problem. Used by solve_case and synthetic tests."""
    if started is None:
        started = time.perf_counter()
    if options is None:
        options = SolverOptions()
    elif not isinstance(options, SolverOptions):
        raise ModelError("options must be a SolverOptions instance")
    if len(timestamps) != market_inputs.interval_count:
        raise ModelError("timestamp count does not match market arrays")
    if period.window.interval_count != market_inputs.interval_count:
        raise ModelError("resolved period does not match market arrays")
    prepared = prepare_physical(config, market_inputs, pv_load_factor)
    resolved = resolve_machine_commitment(config)
    build_started = time.perf_counter()
    model = build_sparse_model(prepared, resolved)
    build_s = time.perf_counter() - build_started
    solved = solve_sparse_model(model, options, build_s=build_s)
    decoded = decode_solution(model, solved.col_value, solved.objective)
    return _assemble(
        config=config,
        period=period,
        manifest_sha256=manifest_sha256,
        timestamps=timestamps,
        model=model,
        decoded=decoded,
        solved=solved,
        started=started,
    )


def _validate_slice(data_slice: MarketDataSlice) -> None:
    if not isinstance(data_slice, MarketDataSlice):
        raise ModelError("solve_case requires a MarketDataSlice")
    if data_slice.period.market != data_slice.config.market:
        raise ModelError("data slice period market does not match configuration")
    n = data_slice.da_prices.num_rows
    if n != data_slice.period.window.interval_count:
        raise ModelError("data slice row count does not match the resolved period")
    if data_slice.config.pv_enabled() and data_slice.pv_profile is None:
        raise ModelError("PV is enabled but the data slice has no PV profile")
    if not data_slice.config.pv_enabled() and data_slice.pv_profile is not None:
        raise ModelError("PV profile is present while PV is disabled")


def _timestamps(data_slice: MarketDataSlice) -> tuple[datetime, ...]:
    values = data_slice.da_prices.column("datetime_utc").to_pylist()
    expected = []
    instant = data_slice.period.window.start_utc
    while instant < data_slice.period.window.end_exclusive_utc:
        expected.append(instant)
        instant += timedelta(minutes=15)
    if values != expected:
        raise MarketInputError("day-ahead timestamps are missing, misaligned, or out of window")
    return tuple(values)


def _pv_load_factor(data_slice: MarketDataSlice) -> np.ndarray | None:
    if not data_slice.config.pv_enabled():
        return None
    assert data_slice.pv_profile is not None
    column = data_slice.pv_profile.column("load_factor")
    if column.null_count:
        raise MarketInputError("PV load_factor contains null values")
    values = np.array(column.to_numpy(zero_copy_only=False), dtype=np.float64, copy=True)
    if values.shape != (data_slice.period.window.interval_count,):
        raise MarketInputError("PV load_factor length does not match the resolved period")
    return values


def _assemble(
    *,
    config: SimulationConfig,
    period: ResolvedPeriod,
    manifest_sha256: str,
    timestamps: tuple[datetime, ...],
    model: SparseModel,
    decoded,
    solved,
    started: float,
) -> DispatchResult:
    prepared = model.prepared
    n = prepared.n
    dt = prepared.dt_h
    energy_gross = dt * prepared.sell_price * decoded.p_turbine
    charging = dt * prepared.buy_price * decoded.p_pump_grid
    energy_net = energy_gross - charging
    if prepared.pv_enabled:
        pv_rev = dt * decoded.pv_price * decoded.pv_export
        pv_price_col: object = decoded.pv_price
    else:
        pv_rev = np.zeros(n, dtype=np.float64)
        pv_price_col = [None] * n
    interval_total = energy_net + pv_rev
    capacity_rev = 0.0
    committed = decoded.capacity_mw
    if prepared.commitments:
        hours = np.array([spec.block_hours for spec in prepared.commitments], dtype=np.float64)
        prices = np.array(
            [spec.price_eur_mw_h for spec in prepared.commitments], dtype=np.float64
        )
        capacity_rev = float(np.dot(prices, committed * hours))
    summary = DispatchSummary(
        interval_count=n,
        duration_hours=float(n * dt),
        e_max_mwh=prepared.e_max_mwh,
        reservoir_initial_mwh=float(decoded.energy[0]),
        reservoir_final_mwh=float(decoded.energy[-1]),
        energy_gross_eur=float(energy_gross.sum()),
        grid_charging_cost_eur=float(charging.sum()),
        market_energy_net_eur=float(energy_net.sum()),
        capacity_revenue_eur=capacity_rev,
        pv_revenue_eur=float(pv_rev.sum()),
        total_site_revenue_eur=float(energy_net.sum() + capacity_rev + pv_rev.sum()),
        pumped_mwh=float(decoded.p_pump.sum() * dt),
        turbined_mwh=float(decoded.p_turbine.sum() * dt),
        pv_available_mwh=float(decoded.pv_available.sum() * dt),
        pv_self_consumed_mwh=float(decoded.pv_to_pump.sum() * dt),
        pv_exported_mwh=float(decoded.pv_export.sum() * dt),
        pv_curtailed_mwh=float(decoded.pv_curtail.sum() * dt),
        simultaneous_interval_count=_simultaneous_count(decoded.p_pump, decoded.p_turbine),
        simultaneous_pump_mwh=_simultaneous_energy(decoded.p_pump, decoded.p_turbine, dt, "pump"),
        simultaneous_turbine_mwh=_simultaneous_energy(
            decoded.p_pump, decoded.p_turbine, dt, "turbine"
        ),
        simultaneous_overlap_mwh=_simultaneous_overlap(decoded.p_pump, decoded.p_turbine, dt),
        n_pump_ramp_up_vars=model.n_pump_ramp_up_vars,
        n_turbine_ramp_up_vars=model.n_turbine_ramp_up_vars,
    )
    feasibility = check_solution(
        model,
        decoded,
        solved.col_value,
        energy_gross_eur=energy_gross,
        grid_charging_cost_eur=charging,
        market_energy_net_eur=energy_net,
        pv_revenue_eur=pv_rev,
        interval_total_eur=interval_total,
        summary_energy_gross=summary.energy_gross_eur,
        summary_charging=summary.grid_charging_cost_eur,
        summary_energy_net=summary.market_energy_net_eur,
        summary_capacity=summary.capacity_revenue_eur,
        summary_pv=summary.pv_revenue_eur,
        summary_total=summary.total_site_revenue_eur,
    )
    dispatch = _dispatch_table(
        timestamps=timestamps,
        sell=prepared.sell_price,
        buy=prepared.buy_price,
        decoded=decoded,
        energy_net=energy_net,
        pv_rev=pv_rev,
        interval_total=interval_total,
        pv_price_col=pv_price_col,
        pv_enabled=prepared.pv_enabled,
    )
    capacity_results = _capacity_table(prepared.commitments, committed)
    solver = SolverMetadata(
        solver_name="HiGHS",
        solver_version=solved.solver_version,
        package_version=solved.package_version,
        status=solved.status,
        status_raw=solved.status_raw,
        build_s=solved.build_s,
        solve_s=solved.solve_s,
        end_to_end_s=time.perf_counter() - started,
        num_col=solved.num_col,
        num_row=solved.num_row,
        num_nz=solved.num_nz,
        num_integer=solved.num_integer,
        num_binary=solved.num_binary,
        continuous_lp=solved.num_integer == 0 and solved.num_binary == 0,
        options=solved.options,
        diagnostics=solved.diagnostics,
    )
    return DispatchResult(
        config=config,
        period=period,
        manifest_sha256=manifest_sha256,
        dispatch=dispatch,
        capacity_results=capacity_results,
        summary=summary,
        solver=solver,
        feasibility=feasibility,
    )


def _dispatch_table(
    *,
    timestamps: tuple[datetime, ...],
    sell: np.ndarray,
    buy: np.ndarray,
    decoded,
    energy_net: np.ndarray,
    pv_rev: np.ndarray,
    interval_total: np.ndarray,
    pv_price_col: object,
    pv_enabled: bool,
) -> pa.Table:
    n = len(timestamps)
    table = pa.table(
        {
            "datetime_utc": pa.array(list(timestamps), type=pa.timestamp("us", tz="UTC")),
            "market_sell_price_eur_mwh": sell,
            "market_buy_price_eur_mwh": buy,
            "p_pump_mw": decoded.p_pump,
            "p_pump_grid_mw": decoded.p_pump_grid,
            "p_turbine_mw": decoded.p_turbine,
            "reservoir_start_mwh": decoded.energy[:-1],
            "reservoir_end_mwh": decoded.energy[1:],
            "pump_ramp_up_mw": decoded.r_up_pump,
            "turbine_ramp_up_mw": decoded.r_up_turb,
            "pv_available_mw": decoded.pv_available,
            "pv_to_pump_mw": decoded.pv_to_pump,
            "pv_export_mw": decoded.pv_export,
            "pv_curtail_mw": decoded.pv_curtail,
            "pv_export_price_eur_mwh": pa.array(pv_price_col, type=pa.float64()),
            "market_energy_net_eur": energy_net,
            "pv_revenue_eur": pv_rev,
            "total_revenue_eur": interval_total,
        }
    )
    if tuple(table.column_names) != DISPATCH_COLUMNS:
        raise ModelError("dispatch table columns do not match the published schema")
    if table.num_rows != n:
        raise ModelError("dispatch table row count is wrong")
    if not pv_enabled:
        for name in (
            "pv_available_mw",
            "pv_to_pump_mw",
            "pv_export_mw",
            "pv_curtail_mw",
            "pv_revenue_eur",
        ):
            values = np.asarray(table.column(name).to_numpy())
            if np.any(values != 0.0):
                raise ModelError(f"{name} must be exact zeros when PV is disabled")
        if table.column("pv_export_price_eur_mwh").null_count != n:
            raise ModelError("PV export price must be null when PV is disabled")
    return table


def _capacity_table(commitments, committed: np.ndarray) -> pa.Table:
    if not commitments:
        return CAPACITY_RESULT_SCHEMA.empty_table()
    rows = {
        "identifier": [spec.identifier for spec in commitments],
        "direction": [spec.direction for spec in commitments],
        "start_index": [spec.start_index for spec in commitments],
        "end_index": [spec.end_index for spec in commitments],
        "price_eur_mw_h": [spec.price_eur_mw_h for spec in commitments],
        "cap_max_mw": [spec.cap_max_mw for spec in commitments],
        "block_hours": [spec.block_hours for spec in commitments],
        "coverage_hours": [spec.coverage_hours for spec in commitments],
        "committed_mw": list(committed),
        "capacity_revenue_eur": [
            float(committed[i]) * spec.price_eur_mw_h * spec.block_hours
            for i, spec in enumerate(commitments)
        ],
    }
    return pa.table(rows, schema=CAPACITY_RESULT_SCHEMA)


def _simultaneous_mask(pump: np.ndarray, turb: np.ndarray) -> np.ndarray:
    return (pump > SIMULTANEOUS_TOL_MW) & (turb > SIMULTANEOUS_TOL_MW)


def _simultaneous_count(pump: np.ndarray, turb: np.ndarray) -> int:
    return int(np.count_nonzero(_simultaneous_mask(pump, turb)))


def _simultaneous_energy(pump: np.ndarray, turb: np.ndarray, dt: float, which: str) -> float:
    mask = _simultaneous_mask(pump, turb)
    source = pump if which == "pump" else turb
    return float(np.sum(source[mask]) * dt)


def _simultaneous_overlap(pump: np.ndarray, turb: np.ndarray, dt: float) -> float:
    mask = _simultaneous_mask(pump, turb)
    return float(np.sum(np.minimum(pump[mask], turb[mask])) * dt)
