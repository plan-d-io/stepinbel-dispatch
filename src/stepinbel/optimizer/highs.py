"""Lazy HiGHS backend. No persistent logs and no live solver in results."""

from __future__ import annotations

import gc
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from stepinbel.optimizer.model import SparseLp
from stepinbel.optimizer.types import SolverError, SolverOptions

HIGHS_RANDOM_SEED = 0
HIGHS_SOLVER = "choose"
HIGHS_PRESOLVE = "on"

STATUS_NAMES = {
    "kOptimal": "optimal",
    "kInfeasible": "infeasible",
    "kUnbounded": "unbounded",
    "kUnboundedOrInfeasible": "infeasible_or_unbounded",
    "kTimeLimit": "time_limit",
    "kIterationLimit": "iteration_limit",
    "kSolutionLimit": "solution_limit",
    "kInterrupt": "interrupt",
    "kHighsInterrupt": "interrupt",
    "kMemoryLimit": "memory_limit",
    "kUnknown": "unknown",
    "kNotset": "unknown",
    "kModelEmpty": "model_error",
    "kModelError": "model_error",
    "kLoadError": "model_error",
    "kPresolveError": "presolve_error",
    "kSolveError": "solve_error",
    "kPostsolveError": "solve_error",
}


def import_highspy():
    try:
        import highspy
    except ImportError as exc:
        raise SolverError(
            "HiGHS is required: the highspy package is not installed."
        ) from exc
    return highspy


def _option_values(options: SolverOptions) -> dict[str, object]:
    detailed = bool(options.detailed_output)
    return {
        "output_flag": detailed,
        "log_to_console": detailed,
        "random_seed": HIGHS_RANDOM_SEED,
        "solver": HIGHS_SOLVER,
        "presolve": HIGHS_PRESOLVE,
    }


@dataclass(frozen=True)
class HighsSolve:
    col_value: np.ndarray
    objective: float
    status: str
    status_raw: str
    solver_version: str
    package_version: str
    build_s: float
    solve_s: float
    num_col: int
    num_row: int
    num_nz: int
    num_integer: int
    num_binary: int
    options: dict[str, object]
    diagnostics: dict[str, object]


def solve_sparse_lp(lp: SparseLp, options: SolverOptions, *, build_s: float) -> HighsSolve:
    highspy = import_highspy()
    highs = highspy.Highs()
    try:
        applied = _option_values(options)
        for name, value in applied.items():
            status = highs.setOptionValue(name, value)
            if status != highspy.HighsStatus.kOk:
                raise SolverError(f"Failed to set HiGHS option {name!r}")
        model = highspy.HighsLp()
        model.num_col_ = lp.num_col
        model.num_row_ = lp.num_row
        model.col_cost_ = lp.col_cost
        model.col_lower_ = lp.col_lower
        model.col_upper_ = lp.col_upper
        model.row_lower_ = lp.row_lower
        model.row_upper_ = lp.row_upper
        model.sense_ = highspy.ObjSense.kMaximize
        model.offset_ = 0.0
        model.model_name_ = "stepinbel_phs_lp"
        model.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        model.a_matrix_.num_col_ = lp.num_col
        model.a_matrix_.num_row_ = lp.num_row
        model.a_matrix_.start_ = lp.a_start
        model.a_matrix_.index_ = lp.a_index
        model.a_matrix_.value_ = lp.a_value
        passed = highs.passModel(model)
        if passed != highspy.HighsStatus.kOk:
            raise SolverError(f"HiGHS rejected the LP: {passed}")
        num_integer, num_binary = _integer_counts(highs)
        if num_integer or num_binary:
            raise SolverError("HiGHS model is not a continuous LP")
        started = time.perf_counter()
        run_status = highs.run()
        solve_s = time.perf_counter() - started
        model_status = highs.getModelStatus()
        status_raw = highspy.HighsModelStatus(model_status).name
        status = STATUS_NAMES.get(status_raw, "unknown")
        info = highs.getInfo()
        diagnostics = _info_payload(info, run_status)
        if model_status != highspy.HighsModelStatus.kOptimal:
            raise SolverError(
                f"HiGHS did not return an optimal solution: {status} "
                f"({status_raw})"
            )
        solution = highs.getSolution()
        col_value = np.array(solution.col_value, dtype=np.float64, copy=True)
        col_value.setflags(write=False)
        return HighsSolve(
            col_value=col_value,
            objective=float(info.objective_function_value),
            status=status,
            status_raw=status_raw,
            solver_version=str(highs.version()),
            package_version=_package_version(),
            build_s=build_s,
            solve_s=solve_s,
            num_col=lp.num_col,
            num_row=lp.num_row,
            num_nz=lp.num_nz,
            num_integer=num_integer,
            num_binary=num_binary,
            options=applied,
            diagnostics=diagnostics,
        )
    finally:
        try:
            highs.clear()
        except Exception:
            pass
        del highs
        gc.collect()


def _integer_counts(highs: Any) -> tuple[int, int]:
    model = highs.getLp()
    integrality = np.asarray(getattr(model, "integrality_", []), dtype=object)
    if integrality.size == 0:
        return 0, 0
    names = [str(item) for item in integrality]
    n_int = sum("Integer" in name or name.endswith("kInteger") for name in names)
    n_bin = sum("Binary" in name or name.endswith("kBinary") for name in names)
    n_semi = sum("Semi" in name for name in names)
    return n_int + n_semi, n_bin


def _info_payload(info: Any, run_status: object) -> dict[str, object]:
    payload: dict[str, object] = {"run_status": str(run_status)}
    for name in (
        "objective_function_value",
        "simplex_iteration_count",
        "ipm_iteration_count",
        "crossover_iteration_count",
        "pdlp_iteration_count",
        "primal_solution_status",
        "dual_solution_status",
        "basis_validity",
        "max_primal_infeasibility",
        "max_dual_infeasibility",
        "num_primal_infeasibilities",
        "num_dual_infeasibilities",
    ):
        if hasattr(info, name):
            value = getattr(info, name)
            payload[name] = (
                value if isinstance(value, (int, float, str, bool)) else str(value)
            )
    return payload


def _package_version() -> str:
    import importlib.metadata

    try:
        return importlib.metadata.version("highspy")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
