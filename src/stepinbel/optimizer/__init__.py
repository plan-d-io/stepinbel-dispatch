"""HiGHS optimizer boundary. Do not import from top-level stepinbel."""

from stepinbel.optimizer.types import (
    COMPARISON_MIP_TIME_LIMIT_WARNING,
    DispatchResult,
    DispatchSummary,
    FeasibilityReport,
    MIP_TIME_LIMIT_FEASIBLE_WARNING,
    ModelError,
    SolverError,
    SolverMetadata,
    SolverOptions,
    TERMINATION_ACCEPTED_WITHIN_GAP,
    TERMINATION_TIME_LIMIT_FEASIBLE,
)

__all__ = [
    "solve_case",
    "SolverOptions",
    "DispatchResult",
    "DispatchSummary",
    "SolverMetadata",
    "FeasibilityReport",
    "ModelError",
    "SolverError",
    "TERMINATION_ACCEPTED_WITHIN_GAP",
    "TERMINATION_TIME_LIMIT_FEASIBLE",
    "MIP_TIME_LIMIT_FEASIBLE_WARNING",
    "COMPARISON_MIP_TIME_LIMIT_WARNING",
]


def solve_case(*args, **kwargs):
    from stepinbel.optimizer.solve import solve_case as _solve_case

    return _solve_case(*args, **kwargs)
