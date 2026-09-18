"""Shared optimizer types, tolerances, and result contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Mapping

import pyarrow as pa

from stepinbel.config import SimulationConfig
from stepinbel.data.coverage import ResolvedPeriod

DT_H = 0.25
POWER_TOL_MW = 1e-7
ENERGY_TOL_MWH = 1e-7
ACCOUNTING_TOL_EUR = 1e-5
SIMULTANEOUS_TOL_MW = 1e-6
PV_ALLOCATION_TOL_MW = 1e-6
PV_LOAD_FACTOR_TOL = 1e-9
WIND_LOAD_FACTOR_EXCLUSIVE_MAX = 1.2
DEFAULT_MIP_REL_GAP = 0.015
DEFAULT_MIP_TIME_LIMIT_S = 900.0
HIGHS_RANDOM_SEED = 0
TERMINATION_ACCEPTED_WITHIN_GAP = "accepted_within_requested_mip_gap"
TERMINATION_TIME_LIMIT_FEASIBLE = "time_limit_feasible_outside_requested_gap"
TERMINATION_NO_FEASIBLE_SOLUTION = "no_feasible_solution"
TERMINATION_SOLVER_FAILURE = "solver_failure"
USABLE_MILP_TERMINATIONS = frozenset(
    {TERMINATION_ACCEPTED_WITHIN_GAP, TERMINATION_TIME_LIMIT_FEASIBLE}
)
MIP_TIME_LIMIT_FEASIBLE_WARNING = (
    "Time limit reached with a feasible incumbent; requested MIP gap was not reached."
)
COMPARISON_MIP_TIME_LIMIT_WARNING = (
    "One or more dedicated-market solves reached the time limit with a "
    "feasible incumbent outside the requested MIP gap. Rankings use those "
    "incumbents; they are not accepted within the requested gap."
)

DISPATCH_COLUMNS: tuple[str, ...] = (
    "datetime_utc",
    "market_sell_price_eur_mwh",
    "market_buy_price_eur_mwh",
    "p_pump_mw",
    "p_pump_grid_mw",
    "p_turbine_mw",
    "reservoir_start_mwh",
    "reservoir_end_mwh",
    "pump_ramp_up_mw",
    "turbine_ramp_up_mw",
    "pv_available_mw",
    "pv_to_pump_mw",
    "pv_export_mw",
    "pv_curtail_mw",
    "pv_export_price_eur_mwh",
    "market_energy_net_eur",
    "pv_revenue_eur",
    "total_revenue_eur",
)

WIND_DISPATCH_COLUMNS: tuple[str, ...] = (
    "wind_available_mw",
    "wind_to_pump_mw",
    "wind_export_mw",
    "wind_curtail_mw",
    "wind_export_price_eur_mwh",
    "wind_revenue_eur",
)
DISPATCH_COLUMNS_V3: tuple[str, ...] = (
    *DISPATCH_COLUMNS[:-1],
    *WIND_DISPATCH_COLUMNS,
    DISPATCH_COLUMNS[-1],
)

CAPACITY_RESULT_COLUMNS: tuple[str, ...] = (
    "identifier",
    "direction",
    "start_index",
    "end_index",
    "price_eur_mw_h",
    "cap_max_mw",
    "block_hours",
    "coverage_hours",
    "committed_mw",
    "capacity_revenue_eur",
)

CAPACITY_RESULT_SCHEMA = pa.schema(
    [
        pa.field("identifier", pa.string()),
        pa.field("direction", pa.string()),
        pa.field("start_index", pa.int64()),
        pa.field("end_index", pa.int64()),
        pa.field("price_eur_mw_h", pa.float64()),
        pa.field("cap_max_mw", pa.float64()),
        pa.field("block_hours", pa.float64()),
        pa.field("coverage_hours", pa.float64()),
        pa.field("committed_mw", pa.float64()),
        pa.field("capacity_revenue_eur", pa.float64()),
    ]
)


class ModelError(ValueError):
    """The selected case or physical model cannot be built."""


class SolverError(RuntimeError):
    """HiGHS failed, was infeasible, or failed independent post-solve checks."""


def _require_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ModelError(f"{field} must be a boolean")
    return value


def _require_finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ModelError(f"{field} must be a finite number")
    return number


@dataclass(frozen=True)
class SolverOptions:
    """Production HiGHS options exposed to callers. Internal seeds stay fixed.

    MIP controls are applied only when a physical commitment option is enabled.
    They do not select LP versus MILP independently of those assumptions.
    """

    detailed_output: bool = False
    mip_rel_gap: float = DEFAULT_MIP_REL_GAP
    time_limit_s: float = DEFAULT_MIP_TIME_LIMIT_S

    def __post_init__(self) -> None:
        _require_bool(self.detailed_output, "detailed_output")
        gap = _require_finite(self.mip_rel_gap, "mip_rel_gap")
        if not 0.0 <= gap <= 1.0:
            raise ModelError("mip_rel_gap must be in [0, 1]")
        limit = _require_finite(self.time_limit_s, "time_limit_s")
        if limit <= 0.0:
            raise ModelError("time_limit_s must be > 0")


@dataclass(frozen=True)
class CapacityCommitment:
    """One continuous capacity block offered into the shared LP."""

    identifier: str
    direction: Literal["up", "down"]
    start_index: int
    end_index: int
    price_eur_mw_h: float
    cap_max_mw: float
    block_hours: float
    coverage_hours: float

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not self.identifier:
            raise ModelError("capacity commitment identifier must be a non-empty string")
        if self.direction not in {"up", "down"}:
            raise ModelError("capacity commitment direction must be 'up' or 'down'")
        if not isinstance(self.start_index, int) or isinstance(self.start_index, bool):
            raise ModelError("capacity commitment start_index must be an int")
        if not isinstance(self.end_index, int) or isinstance(self.end_index, bool):
            raise ModelError("capacity commitment end_index must be an int")
        if self.end_index <= self.start_index:
            raise ModelError("capacity commitment span is empty")
        price = _require_finite(self.price_eur_mw_h, "price_eur_mw_h")
        cap = _require_finite(self.cap_max_mw, "cap_max_mw")
        hours = _require_finite(self.block_hours, "block_hours")
        coverage = _require_finite(self.coverage_hours, "coverage_hours")
        if price < 0.0:
            raise ModelError("price_eur_mw_h must be >= 0")
        if cap < 0.0:
            raise ModelError("cap_max_mw must be >= 0")
        if hours <= 0.0:
            raise ModelError("block_hours must be > 0")
        if coverage <= 0.0:
            raise ModelError("coverage_hours must be > 0")


@dataclass(frozen=True)
class DispatchSummary:
    """Scalar accounting and operational totals for one solved case."""

    interval_count: int
    duration_hours: float
    e_max_mwh: float
    reservoir_initial_mwh: float
    reservoir_final_mwh: float
    energy_gross_eur: float
    grid_charging_cost_eur: float
    market_energy_net_eur: float
    capacity_revenue_eur: float
    pv_revenue_eur: float
    total_site_revenue_eur: float
    pumped_mwh: float
    turbined_mwh: float
    pv_available_mwh: float
    pv_self_consumed_mwh: float
    pv_exported_mwh: float
    pv_curtailed_mwh: float
    simultaneous_interval_count: int
    simultaneous_pump_mwh: float
    simultaneous_turbine_mwh: float
    simultaneous_overlap_mwh: float
    n_pump_ramp_up_vars: int
    n_turbine_ramp_up_vars: int
    wind_revenue_eur: float = 0.0
    wind_available_mwh: float = 0.0
    wind_self_consumed_mwh: float = 0.0
    wind_exported_mwh: float = 0.0
    wind_curtailed_mwh: float = 0.0


@dataclass(frozen=True)
class SolverMetadata:
    """HiGHS provenance. Does not retain a live solver or log path."""

    solver_name: str
    solver_version: str
    package_version: str
    status: str
    status_raw: str
    build_s: float
    solve_s: float
    end_to_end_s: float
    num_col: int
    num_row: int
    num_nz: int
    num_integer: int
    num_binary: int
    continuous_lp: bool
    options: Mapping[str, object]
    diagnostics: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))


@dataclass(frozen=True)
class FeasibilityReport:
    """Independent residual maxima. A failed report is never returned as success."""

    max_bound_residual: float
    max_initial_terminal_residual_mwh: float
    max_balance_residual_mwh: float
    max_ramp_residual_mw: float
    max_pv_residual_mw: float
    max_grid_residual_mw: float
    max_capacity_residual: float
    max_interval_accounting_residual_eur: float
    max_summary_accounting_residual_eur: float
    max_objective_residual_eur: float
    ok: bool
    max_wind_residual_mw: float = 0.0


@dataclass(frozen=True)
class DispatchResult:
    """Immutable solved case. Nested mappings are read-only."""

    config: SimulationConfig
    period: ResolvedPeriod
    manifest_sha256: str
    dispatch: pa.Table
    capacity_results: pa.Table
    summary: DispatchSummary
    solver: SolverMetadata
    feasibility: FeasibilityReport

    @property
    def timestamps(self) -> tuple[datetime, ...]:
        return tuple(self.dispatch.column("datetime_utc").to_pylist())
