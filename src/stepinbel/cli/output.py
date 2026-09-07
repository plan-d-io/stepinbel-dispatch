"""UTF-8 stdout/stderr, compact JSON, and typed exit-code mapping."""

from __future__ import annotations

import json
import math
import sys
from typing import Any, Mapping

from stepinbel.config import ConfigError
from stepinbel.data import DataAccessError, DataBundleError
from stepinbel.optimizer import ModelError, SolverError
from stepinbel.reporting import ArtifactError
from stepinbel.workflows import (
    RunCancelledError,
    RunError,
    RunEvent,
    RunExecutionError,
    RunRequestError,
)

from stepinbel.cli.errors import CliError

_EXIT_INVALID = 2
_EXIT_EXECUTION = 1
_EXIT_CANCELLED = 130


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError, AttributeError):
            continue


def dumps_payload(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        )
    except ValueError as exc:
        raise CliError(
            "JSON output contains a non-finite number",
            category="internal_error",
            exit_code=_EXIT_EXECUTION,
        ) from exc


def write_success(payload: Mapping[str, Any]) -> int:
    sys.stdout.write(dumps_payload(payload) + "\n")
    sys.stdout.flush()
    return 0


def write_error(category: str, message: str) -> None:
    sys.stderr.write(dumps_payload({"ok": False, "error_category": category, "message": message}) + "\n")
    sys.stderr.flush()


def format_progress(event: RunEvent) -> str:
    return (
        f"{event.run_id} {event.stage_number}/{event.stage_total} "
        f"{event.stage_key} {event.state}: {event.message}"
    )


def emit_progress(event: RunEvent) -> None:
    sys.stderr.write(format_progress(event) + "\n")
    sys.stderr.flush()


def _require_finite_number(value: object, field: str) -> int | float:
    if type(value) is bool or type(value) not in (int, float):
        raise CliError(f"{field} must be a finite number", category="internal_error", exit_code=_EXIT_EXECUTION)
    if not math.isfinite(value):
        raise CliError(f"{field} must be a finite number", category="internal_error", exit_code=_EXIT_EXECUTION)
    return value


def status_payload(status: Mapping[str, object]) -> dict[str, object]:
    payload = dict(status)
    elapsed = payload.get("elapsed_seconds")
    if elapsed is not None:
        payload["elapsed_seconds"] = _require_finite_number(elapsed, "status.elapsed_seconds")
    return payload


def map_exception(exc: BaseException) -> tuple[int, str, str]:
    if isinstance(exc, CliError):
        return exc.exit_code, exc.category, exc.message
    if isinstance(exc, RunCancelledError):
        return _EXIT_CANCELLED, exc.category, str(exc)
    if isinstance(exc, RunRequestError):
        return _EXIT_INVALID, exc.category, str(exc)
    if isinstance(exc, RunExecutionError):
        return _EXIT_EXECUTION, exc.category, str(exc)
    if isinstance(exc, RunError):
        return _EXIT_EXECUTION, exc.category, str(exc)
    if isinstance(exc, ConfigError):
        return _EXIT_INVALID, "invalid_configuration", str(exc)
    if isinstance(exc, DataBundleError):
        return _EXIT_INVALID, "data_bundle", str(exc)
    if isinstance(exc, DataAccessError):
        return _EXIT_INVALID, "data_coverage", str(exc)
    if isinstance(exc, ArtifactError):
        return _EXIT_INVALID, "artifact_validation", str(exc)
    if isinstance(exc, (ModelError, SolverError)):
        return _EXIT_EXECUTION, "optimizer", str(exc)
    if isinstance(exc, KeyboardInterrupt):
        return _EXIT_CANCELLED, "cancelled", "run cancelled"
    return _EXIT_EXECUTION, "internal_error", str(exc) or exc.__class__.__name__


def emit_failure(exc: BaseException) -> int:
    code, category, message = map_exception(exc)
    write_error(category, message)
    return code
