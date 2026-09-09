"""Lazy HiGHS backend. No persistent logs and no live solver in results."""

from __future__ import annotations

import gc
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from stepinbel.optimizer.model import SparseModel
from stepinbel.optimizer.types import (
    HIGHS_RANDOM_SEED,
    TERMINATION_ACCEPTED_WITHIN_GAP,
    TERMINATION_NO_FEASIBLE_SOLUTION,
    TERMINATION_SOLVER_FAILURE,
    TERMINATION_TIME_LIMIT_FEASIBLE,
    SolverError,
    SolverOptions,
)

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

USABLE_CLASSIFICATIONS = frozenset(
    {"optimal", TERMINATION_ACCEPTED_WITHIN_GAP, TERMINATION_TIME_LIMIT_FEASIBLE}
)


def import_highspy():
    try:
        import highspy
    except ImportError as exc:
        raise SolverError(
            "HiGHS is required: the highspy package is not installed."
        ) from exc
    return highspy


def _is_mip(model: SparseModel) -> bool:
    return model.integrality is not None and int(np.count_nonzero(model.integrality)) > 0


def _option_values(options: SolverOptions, model: SparseModel) -> dict[str, object]:
    detailed = bool(options.detailed_output)
    applied: dict[str, object] = {
        "output_flag": detailed,
        "log_to_console": detailed,
        "random_seed": HIGHS_RANDOM_SEED,
        "solver": HIGHS_SOLVER,
        "presolve": HIGHS_PRESOLVE,
    }
    if not _is_mip(model):
        return applied
    applied["mip_rel_gap"] = float(options.mip_rel_gap)
    applied["threads"] = 0
    applied["time_limit"] = float(options.time_limit_s)
    return applied


@dataclass(frozen=True)
class HighsSolve:
    col_value: np.ndarray
    objective: float
    status: str
    status_raw: str
    classification: str
    has_incumbent: bool
    best_bound: float | None
    mip_gap: float | None
    mip_node_count: int | None
    requested_mip_gap: float | None
    time_limit_s: float | None
    solver_version: str
    package_version: str
    build_s: float
    solve_s: float
    num_col: int
    num_row: int
    num_nz: int
    num_continuous: int
    num_integer: int
    num_binary: int
    options: dict[str, object]
    diagnostics: dict[str, object]


class UnusableSolveError(SolverError):
    """LP was not optimal, or the MILP finished without a feasible incumbent."""

    def __init__(self, message: str, solved: HighsSolve) -> None:
        super().__init__(message)
        self.solved = solved


def solve_sparse_model(
    model: SparseModel,
    options: SolverOptions,
    *,
    build_s: float,
    require_usable: bool = True,
) -> HighsSolve:
    highspy = import_highspy()
    highs = highspy.Highs()
    try:
        applied = _option_values(options, model)
        for name, value in applied.items():
            status = highs.setOptionValue(name, value)
            if status != highspy.HighsStatus.kOk:
                raise SolverError(f"Failed to set HiGHS option {name!r}")
        recorded = dict(applied)
        is_mip = _is_mip(model)
        lp = highspy.HighsLp()
        lp.num_col_ = model.num_col
        lp.num_row_ = model.num_row
        lp.col_cost_ = model.col_cost
        lp.col_lower_ = model.col_lower
        lp.col_upper_ = model.col_upper
        lp.row_lower_ = model.row_lower
        lp.row_upper_ = model.row_upper
        lp.sense_ = highspy.ObjSense.kMaximize
        lp.offset_ = 0.0
        lp.model_name_ = "stepinbel_phs_milp" if is_mip else "stepinbel_phs_lp"
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.num_col_ = model.num_col
        lp.a_matrix_.num_row_ = model.num_row
        lp.a_matrix_.start_ = model.a_start
        lp.a_matrix_.index_ = model.a_index
        lp.a_matrix_.value_ = model.a_value
        if is_mip:
            assert model.integrality is not None
            integrality = []
            for flag in model.integrality:
                integrality.append(
                    highspy.HighsVarType.kInteger
                    if int(flag) == 1
                    else highspy.HighsVarType.kContinuous
                )
            lp.integrality_ = integrality
        passed = highs.passModel(lp)
        if passed != highspy.HighsStatus.kOk:
            raise SolverError(f"HiGHS rejected the model: {passed}")
        num_integer, num_binary = _integer_counts(highs, model)
        if not is_mip and (num_integer or num_binary):
            raise SolverError("HiGHS model is not a continuous LP")
        if is_mip and num_binary == 0 and num_integer == 0:
            raise SolverError("HiGHS MILP model has no integer or binary variables")
        started = time.perf_counter()
        run_status = highs.run()
        solve_s = time.perf_counter() - started
        model_status = highs.getModelStatus()
        status_raw = highspy.HighsModelStatus(model_status).name
        status = STATUS_NAMES.get(status_raw, "unknown")
        info = highs.getInfo()
        diagnostics = _info_payload(info, run_status)
        has_incumbent = _has_incumbent(highspy, info)
        classification = _classify(status, has_incumbent, is_mip)
        best_bound = None
        mip_gap = None
        node_count = None
        requested_gap = float(options.mip_rel_gap) if is_mip else None
        time_limit = float(options.time_limit_s) if is_mip else None
        if is_mip:
            diagnostics["formulation"] = "milp"
            diagnostics["termination"] = classification
            diagnostics["has_incumbent"] = has_incumbent
            diagnostics["requested_mip_gap"] = requested_gap
            diagnostics["time_limit_s"] = time_limit
            if hasattr(info, "mip_dual_bound"):
                best_bound = float(info.mip_dual_bound)
                if np.isfinite(best_bound):
                    diagnostics["mip_dual_bound"] = best_bound
                    diagnostics["best_bound"] = best_bound
            if hasattr(info, "mip_gap"):
                mip_gap = float(info.mip_gap)
                if np.isfinite(mip_gap):
                    diagnostics["mip_gap"] = mip_gap
                    diagnostics["achieved_mip_gap"] = mip_gap
            if hasattr(info, "mip_node_count"):
                node_count = int(info.mip_node_count)
                diagnostics["mip_node_count"] = node_count
            if has_incumbent and hasattr(info, "objective_function_value"):
                incumbent = float(info.objective_function_value)
                if np.isfinite(incumbent):
                    diagnostics["incumbent_objective"] = incumbent
            if hasattr(info, "max_integrality_violation"):
                violation = float(info.max_integrality_violation)
                if np.isfinite(violation):
                    diagnostics["max_integrality_violation"] = violation
        if has_incumbent:
            solution = highs.getSolution()
            col_value = np.array(solution.col_value, dtype=np.float64, copy=True)
            objective = float(info.objective_function_value)
        else:
            col_value = np.zeros(0, dtype=np.float64)
            objective = float("nan")
        col_value.setflags(write=False)
        solved = HighsSolve(
            col_value=col_value,
            objective=objective,
            status=status,
            status_raw=status_raw,
            classification=classification,
            has_incumbent=has_incumbent,
            best_bound=best_bound,
            mip_gap=mip_gap,
            mip_node_count=node_count,
            requested_mip_gap=requested_gap,
            time_limit_s=time_limit,
            solver_version=str(highs.version()),
            package_version=_package_version(),
            build_s=build_s,
            solve_s=solve_s,
            num_col=model.num_col,
            num_row=model.num_row,
            num_nz=model.num_nz,
            num_continuous=model.num_col - num_integer,
            num_integer=num_integer,
            num_binary=num_binary,
            options=recorded,
            diagnostics=diagnostics,
        )
        if require_usable and classification not in USABLE_CLASSIFICATIONS:
            raise UnusableSolveError(
                _unusable_message(solved, is_mip),
                solved,
            )
        if not is_mip and model_status != highspy.HighsModelStatus.kOptimal:
            raise UnusableSolveError(
                f"HiGHS did not return an optimal solution: {status} ({status_raw})",
                solved,
            )
        return solved
    finally:
        try:
            highs.clear()
        except Exception:
            pass
        del highs
        gc.collect()


def _has_incumbent(highspy: Any, info: Any) -> bool:
    status = getattr(info, "primal_solution_status", None)
    if status is None:
        return False
    feasible = getattr(highspy, "kSolutionStatusFeasible", None)
    if feasible is not None and status == feasible:
        return True
    return int(status) == 2


def _classify(status: str, has_incumbent: bool, is_mip: bool) -> str:
    if not is_mip:
        if status == "optimal" and has_incumbent:
            return "optimal"
        return TERMINATION_SOLVER_FAILURE
    if status == "optimal" and has_incumbent:
        return TERMINATION_ACCEPTED_WITHIN_GAP
    if status == "time_limit" and has_incumbent:
        return TERMINATION_TIME_LIMIT_FEASIBLE
    if status == "time_limit":
        return TERMINATION_NO_FEASIBLE_SOLUTION
    if status in {"infeasible", "infeasible_or_unbounded"}:
        return TERMINATION_NO_FEASIBLE_SOLUTION
    return TERMINATION_SOLVER_FAILURE


def _unusable_message(solved: HighsSolve, is_mip: bool) -> str:
    if solved.classification == TERMINATION_NO_FEASIBLE_SOLUTION:
        if solved.status == "time_limit":
            return (
                "HiGHS reached the time limit without a feasible solution "
                f"({solved.status_raw})"
            )
        return f"HiGHS declared the model infeasible ({solved.status_raw})"
    if not is_mip:
        return (
            f"HiGHS did not return an optimal solution: {solved.status} "
            f"({solved.status_raw})"
        )
    return (
        f"HiGHS did not return an accepted MILP solution: {solved.classification} "
        f"({solved.status_raw})"
    )


def _integer_counts(highs: Any, model: SparseModel) -> tuple[int, int]:
    lp = highs.getLp()
    integrality = np.asarray(getattr(lp, "integrality_", []), dtype=object)
    if integrality.size == 0:
        return 0, 0
    names = [str(item) for item in integrality]
    n_int = sum("Integer" in name or name.endswith("kInteger") for name in names)
    n_named_bin = sum("Binary" in name or name.endswith("kBinary") for name in names)
    n_semi = sum("Semi" in name for name in names)
    n_binary = n_named_bin
    if model.integrality is not None and integrality.size == model.num_col:
        lowers = np.asarray(model.col_lower, dtype=np.float64)
        uppers = np.asarray(model.col_upper, dtype=np.float64)
        for index, name in enumerate(names):
            if "Integer" not in name and not name.endswith("kInteger"):
                continue
            if lowers[index] == 0.0 and uppers[index] == 1.0:
                n_binary += 1
    return n_int + n_semi, n_binary


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
            payload[name] = _json_safe_scalar(value)
    return payload


def _json_safe_scalar(value: object) -> object:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not np.isfinite(value):
            return str(value)
        return float(value)
    if isinstance(value, str):
        return value
    return str(value)


def _package_version() -> str:
    import importlib.metadata

    try:
        return importlib.metadata.version("highspy")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
