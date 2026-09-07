"""CLI-local argument errors. Workflow failures keep their existing types."""

from __future__ import annotations


class CliError(Exception):
    """Invalid CLI arguments or an output contract failure."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "invalid_argument",
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.category = category
        self.exit_code = exit_code
