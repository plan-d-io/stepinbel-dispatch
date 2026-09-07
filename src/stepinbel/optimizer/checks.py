"""Independent post-solve feasibility and accounting checks."""

from __future__ import annotations

import math

import numpy as np

from stepinbel.optimizer.model import SparseLp
from stepinbel.optimizer.types import (
    ACCOUNTING_TOL_EUR,
    ENERGY_TOL_MWH,
    POWER_TOL_MW,
    FeasibilityReport,
    SolverError,
)


class DecodedSolution:
    def __init__(
        self,
        *,
        p_pump: np.ndarray,
        p_pump_grid: np.ndarray,
        p_turbine: np.ndarray,
        energy: np.ndarray,
        r_up_pump: np.ndarray,
        r_up_turb: np.ndarray,
        pv_available: np.ndarray,
        pv_to_pump: np.ndarray,
        pv_export: np.ndarray,
        pv_curtail: np.ndarray,
        pv_price: np.ndarray,
        capacity_mw: np.ndarray,
        objective: float,
    ) -> None:
        self.p_pump = p_pump
        self.p_pump_grid = p_pump_grid
        self.p_turbine = p_turbine
        self.energy = energy
        self.r_up_pump = r_up_pump
        self.r_up_turb = r_up_turb
        self.pv_available = pv_available
        self.pv_to_pump = pv_to_pump
        self.pv_export = pv_export
        self.pv_curtail = pv_curtail
        self.pv_price = pv_price
        self.capacity_mw = capacity_mw
        self.objective = objective


def decode_solution(lp: SparseLp, col_value: np.ndarray, objective: float) -> DecodedSolution:
    layout = lp.layout
    prepared = lp.prepared
    n = prepared.n
    x = np.asarray(col_value, dtype=np.float64)
    p_pump = _copy(x[layout.idx_pump : layout.idx_pump + n])
    p_turb = _copy(x[layout.idx_turb : layout.idx_turb + n])
    energy = _copy(x[layout.idx_e : layout.idx_e + n + 1])
    if layout.idx_pump_grid is None:
        p_grid = _copy(p_pump)
    else:
        p_grid = _copy(x[layout.idx_pump_grid : layout.idx_pump_grid + n])
    if layout.idx_r_pump is None:
        r_pump = np.zeros(n, dtype=np.float64)
    else:
        r_pump = _copy(x[layout.idx_r_pump : layout.idx_r_pump + n])
    if layout.idx_r_turb is None:
        r_turb = np.zeros(n, dtype=np.float64)
    else:
        r_turb = _copy(x[layout.idx_r_turb : layout.idx_r_turb + n])
    if not prepared.pv_enabled:
        zeros = np.zeros(n, dtype=np.float64)
        pv_to_pump = zeros.copy()
        pv_export = zeros.copy()
        pv_curtail = zeros.copy()
        pv_avail = zeros.copy()
        pv_price = np.full(n, np.nan, dtype=np.float64)
    else:
        assert layout.idx_pv_to_pump is not None
        assert layout.idx_pv_export is not None
        assert layout.idx_pv_curtail is not None
        pv_to_pump = _copy(x[layout.idx_pv_to_pump : layout.idx_pv_to_pump + n])
        pv_export = _copy(x[layout.idx_pv_export : layout.idx_pv_export + n])
        pv_curtail = _copy(x[layout.idx_pv_curtail : layout.idx_pv_curtail + n])
        pv_avail = _copy(prepared.pv_available_mw)
        pv_price = _copy(prepared.pv_export_price)
    if layout.idx_capacity is None:
        capacity = np.zeros(0, dtype=np.float64)
    else:
        capacity = _copy(
            x[layout.idx_capacity : layout.idx_capacity + layout.n_commitments]
        )
    return DecodedSolution(
        p_pump=p_pump,
        p_pump_grid=p_grid,
        p_turbine=p_turb,
        energy=energy,
        r_up_pump=r_pump,
        r_up_turb=r_turb,
        pv_available=pv_avail,
        pv_to_pump=pv_to_pump,
        pv_export=pv_export,
        pv_curtail=pv_curtail,
        pv_price=pv_price,
        capacity_mw=capacity,
        objective=float(objective),
    )


def check_solution(
    lp: SparseLp,
    decoded: DecodedSolution,
    col_value: np.ndarray,
    *,
    energy_gross_eur: np.ndarray,
    grid_charging_cost_eur: np.ndarray,
    market_energy_net_eur: np.ndarray,
    pv_revenue_eur: np.ndarray,
    interval_total_eur: np.ndarray,
    summary_energy_gross: float,
    summary_charging: float,
    summary_energy_net: float,
    summary_capacity: float,
    summary_pv: float,
    summary_total: float,
) -> FeasibilityReport:
    prepared = lp.prepared
    n = prepared.n
    dt = prepared.dt_h
    raw = np.asarray(col_value, dtype=np.float64)
    if raw.shape != (lp.num_col,):
        _fail(f"solution has {raw.size} values, expected {lp.num_col}")
    if not np.all(np.isfinite(raw)):
        _fail("solution vector contains non-finite values")
    arrays = [
        decoded.p_pump,
        decoded.p_pump_grid,
        decoded.p_turbine,
        decoded.energy,
        decoded.r_up_pump,
        decoded.r_up_turb,
        decoded.pv_available,
        decoded.pv_to_pump,
        decoded.pv_export,
        decoded.pv_curtail,
        decoded.capacity_mw,
        energy_gross_eur,
        grid_charging_cost_eur,
        market_energy_net_eur,
        pv_revenue_eur,
        interval_total_eur,
    ]
    for item in arrays:
        if item.size and not np.all(np.isfinite(item)):
            _fail("non-finite values in the decoded solution")
    if not math.isfinite(decoded.objective):
        _fail("HiGHS objective is not finite")
    # NaN is allowed only in disabled PV prices.
    if prepared.pv_enabled and not np.all(np.isfinite(decoded.pv_price)):
        _fail("PV export prices are not finite")

    lower_residual = np.maximum(lp.col_lower - raw, 0.0)
    upper_residual = np.maximum(raw - lp.col_upper, 0.0)
    bound = max(float(np.max(lower_residual, initial=0.0)), float(np.max(upper_residual, initial=0.0)))

    init_term = abs(decoded.energy[0] - prepared.e0_mwh)
    if prepared.e_terminal_mwh is not None:
        init_term = max(init_term, abs(decoded.energy[-1] - prepared.e_terminal_mwh))

    balance = 0.0
    for t in range(n):
        expected = (
            decoded.energy[t]
            + prepared.eta_pump
            * (
                decoded.p_pump[t] * dt
                - (prepared.epsilon_pump * decoded.r_up_pump[t] if lp.layout.use_r_pump else 0.0)
            )
            - (
                decoded.p_turbine[t] * dt
                + (
                    prepared.epsilon_turbine * decoded.r_up_turb[t]
                    if lp.layout.use_r_turb
                    else 0.0
                )
            )
            / prepared.eta_turbine
        )
        balance = max(balance, abs(decoded.energy[t + 1] - expected))

    ramp = 0.0
    if lp.layout.use_r_pump:
        ramp = max(ramp, max(0.0, decoded.p_pump[0] - decoded.r_up_pump[0]))
        for t in range(1, n):
            need = decoded.p_pump[t] - decoded.p_pump[t - 1]
            ramp = max(ramp, max(0.0, need - decoded.r_up_pump[t]))
    if lp.layout.use_r_turb:
        ramp = max(ramp, max(0.0, decoded.p_turbine[0] - decoded.r_up_turb[0]))
        for t in range(1, n):
            need = decoded.p_turbine[t] - decoded.p_turbine[t - 1]
            ramp = max(ramp, max(0.0, need - decoded.r_up_turb[t]))
    ramp = max(ramp, max(0.0, decoded.p_pump[0] - prepared.pump_ramp_up_mw))
    ramp = max(ramp, max(0.0, decoded.p_turbine[0] - prepared.turbine_ramp_up_mw))
    for t in range(1, n):
        ramp = max(
            ramp,
            max(0.0, decoded.p_pump[t] - decoded.p_pump[t - 1] - prepared.pump_ramp_up_mw),
        )
        ramp = max(
            ramp,
            max(0.0, decoded.p_pump[t - 1] - decoded.p_pump[t] - prepared.pump_ramp_down_mw),
        )
        ramp = max(
            ramp,
            max(
                0.0,
                decoded.p_turbine[t] - decoded.p_turbine[t - 1] - prepared.turbine_ramp_up_mw,
            ),
        )
        ramp = max(
            ramp,
            max(
                0.0,
                decoded.p_turbine[t - 1] - decoded.p_turbine[t] - prepared.turbine_ramp_down_mw,
            ),
        )

    pv_res = 0.0
    grid = 0.0
    if prepared.pv_enabled:
        split = decoded.pv_export + decoded.pv_to_pump + decoded.pv_curtail
        pv_res = max(
            pv_res,
            float(np.max(np.abs(split - decoded.pv_available), initial=0.0)),
        )
        pv_res = max(
            pv_res,
            float(
                np.max(
                    np.abs(decoded.p_pump - (decoded.p_pump_grid + decoded.pv_to_pump)),
                    initial=0.0,
                )
            ),
        )
        grid = max(
            grid,
            float(np.max(np.maximum(decoded.p_pump_grid - prepared.grid_import_mw, 0.0), initial=0.0)),
        )
        grid = max(
            grid,
            float(
                np.max(
                    np.maximum(
                        decoded.p_turbine + decoded.pv_export - prepared.grid_export_mw,
                        0.0,
                    ),
                    initial=0.0,
                )
            ),
        )
    else:
        grid = max(
            grid,
            float(np.max(np.maximum(decoded.p_pump - prepared.grid_import_mw, 0.0), initial=0.0)),
        )
        grid = max(
            grid,
            float(
                np.max(np.maximum(decoded.p_turbine - prepared.grid_export_mw, 0.0), initial=0.0)
            ),
        )

    cap_res = 0.0
    for i, spec in enumerate(prepared.commitments):
        c_b = float(decoded.capacity_mw[i])
        cap_res = max(cap_res, max(0.0, -c_b), max(0.0, c_b - spec.cap_max_mw))
        if spec.direction == "up":
            span = decoded.p_turbine[spec.start_index : spec.end_index]
            cap_res = max(cap_res, float(np.max(np.maximum(span - c_b, 0.0), initial=0.0)))
            need = c_b * spec.coverage_hours / prepared.eta_turbine
            cap_res = max(cap_res, max(0.0, need - decoded.energy[spec.start_index]))
        else:
            need = c_b * prepared.eta_pump * spec.coverage_hours
            headroom = prepared.e_max_mwh - decoded.energy[spec.start_index]
            cap_res = max(cap_res, max(0.0, need - headroom))

    interval_acc = max(
        float(
            np.max(
                np.abs(
                    market_energy_net_eur - (energy_gross_eur - grid_charging_cost_eur)
                ),
                initial=0.0,
            )
        ),
        float(
            np.max(
                np.abs(interval_total_eur - (market_energy_net_eur + pv_revenue_eur)),
                initial=0.0,
            )
        ),
    )
    summary_acc = max(
        abs(summary_energy_gross - float(energy_gross_eur.sum())),
        abs(summary_charging - float(grid_charging_cost_eur.sum())),
        abs(summary_energy_net - float(market_energy_net_eur.sum())),
        abs(summary_pv - float(pv_revenue_eur.sum())),
        abs(summary_energy_net - (summary_energy_gross - summary_charging)),
        abs(summary_total - (summary_energy_net + summary_capacity + summary_pv)),
    )
    objective_res = abs(decoded.objective - summary_total)

    report = FeasibilityReport(
        max_bound_residual=bound,
        max_initial_terminal_residual_mwh=init_term,
        max_balance_residual_mwh=balance,
        max_ramp_residual_mw=ramp,
        max_pv_residual_mw=pv_res,
        max_grid_residual_mw=grid,
        max_capacity_residual=cap_res,
        max_interval_accounting_residual_eur=interval_acc,
        max_summary_accounting_residual_eur=summary_acc,
        max_objective_residual_eur=objective_res,
        ok=(
            bound <= POWER_TOL_MW
            and init_term <= ENERGY_TOL_MWH
            and balance <= ENERGY_TOL_MWH
            and ramp <= POWER_TOL_MW
            and pv_res <= POWER_TOL_MW
            and grid <= POWER_TOL_MW
            and cap_res <= max(POWER_TOL_MW, ENERGY_TOL_MWH)
            and interval_acc <= ACCOUNTING_TOL_EUR
            and summary_acc <= ACCOUNTING_TOL_EUR
            and objective_res <= ACCOUNTING_TOL_EUR
        ),
    )
    if not report.ok:
        raise SolverError(
            "post-solve feasibility or accounting checks failed: "
            f"bound={bound:.3e} init/term={init_term:.3e} balance={balance:.3e} "
            f"ramp={ramp:.3e} pv={pv_res:.3e} grid={grid:.3e} capacity={cap_res:.3e} "
            f"interval_acc={interval_acc:.3e} summary_acc={summary_acc:.3e} "
            f"objective={objective_res:.3e} status={lp.num_col}x{lp.num_row}"
        )
    return report


def _copy(values: np.ndarray) -> np.ndarray:
    out = np.array(values, dtype=np.float64, copy=True)
    out.setflags(write=False)
    return out


def _fail(message: str) -> None:
    raise SolverError(message)
