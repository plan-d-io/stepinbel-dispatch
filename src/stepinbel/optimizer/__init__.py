"""HiGHS optimizer boundary. Do not import from top-level stepinbel."""

from stepinbel.optimizer.types import (
    DispatchResult,
    DispatchSummary,
    FeasibilityReport,
    ModelError,
    SolverError,
    SolverMetadata,
    SolverOptions,
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
]


def solve_case(*args, **kwargs):
    from stepinbel.optimizer.solve import solve_case as _solve_case

    return _solve_case(*args, **kwargs)
