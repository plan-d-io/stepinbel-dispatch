"""Domain errors for the one-case workflow."""

from __future__ import annotations


class RunError(Exception):
    """Workflow failure with a stable category."""

    category: str = "execution"

    def __init__(self, message: str, *, category: str | None = None) -> None:
        super().__init__(message)
        if category is not None:
            self.category = category
        self.message = message


class RunRequestError(RunError):
    """The request, output path, or frozen data cannot be used."""

    category = "invalid_request"


class RunExecutionError(RunError):
    """Solving, writing, validating, or notifying progress failed."""

    category = "execution"


class RunCancelledError(RunError):
    """The caller cancelled the run before completion."""

    category = "cancelled"
