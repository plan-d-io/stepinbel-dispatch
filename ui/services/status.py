"""Read durable workflow status/events/logs and classify a live job."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ui.flow import is_exact_int
from ui.services.jobs import LAUNCH_FAILED, parse_utc_safe, trusted_job_or_none
from ui.services.paths import (
    EVENTS_FILENAME,
    KIND_CASE,
    KIND_COMPARISON,
    LOG_FILENAME,
    STATUS_FILENAME,
    WORKER_CONSOLE_NAME,
)
from ui.services.process import PidCheck, pid_is_alive

CLASS_QUEUED = "queued"
CLASS_RUNNING = "running"
CLASS_VALIDATING = "validating"
CLASS_READY = "ready"
CLASS_FAILED = "failed"
CLASS_UNEXPECTED = "unexpected"
CLASS_INCOMPLETE = "incomplete"
CLASS_LAUNCH_FAILED = "launch_failed"
CLASS_UNTRUSTED = "untrusted"

_STATUS_UNSET = object()

CORE_RUNNING = "running"
CORE_COMPLETED = "completed"
CORE_FAILED = "failed"
CORE_CANCELLED = "cancelled"
CORE_SUPPORTED = frozenset({CORE_RUNNING, CORE_COMPLETED, CORE_FAILED, CORE_CANCELLED})
CORE_TERMINAL = frozenset({CORE_COMPLETED, CORE_FAILED, CORE_CANCELLED})
EVENT_STATES = frozenset({"started", "completed", "failed", "cancelled"})

LAUNCH_GRACE_SECONDS = 8.0
STALE_STATUS_SECONDS = 8.0
STATUS_MAX_BYTES = 65536
EVENTS_MAX_BYTES = 262144
EVENTS_MAX_LINES = 2000
LOG_MAX_BYTES = 65536
LOG_MAX_LINES = 80
CONSOLE_MAX_BYTES = 65536
CONSOLE_MAX_LINES = 80

CASE_ARTIFACT_SCHEMA = 1
COMPARISON_ARTIFACT_SCHEMA = 1

STAGE_LABELS = {
    "validate_request": "Validating request",
    "validate_data": "Checking market data",
    "load_data": "Loading market data",
    "solve": "Solving",
    "write_artifacts": "Writing results",
    "verify_artifacts": "Verifying results",
    "execute_da": "Day-ahead case",
    "execute_mfrr": "mFRR case",
    "execute_afrr": "aFRR case",
    "aggregate": "Combining cases",
}


def parse_utc(value: Any) -> datetime | None:
    return parse_utc_safe(value)


def expected_artifact_schema(kind: str) -> int:
    if kind == KIND_CASE:
        return CASE_ARTIFACT_SCHEMA
    if kind == KIND_COMPARISON:
        return COMPARISON_ARTIFACT_SCHEMA
    return CASE_ARTIFACT_SCHEMA


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _read_bounded_bytes(path: Path, *, max_bytes: int) -> bytes | None:
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            return handle.read(max_bytes)
    except OSError:
        return None


def read_status_payload(output_directory: str | Path | None) -> dict[str, Any] | None:
    if output_directory is None:
        return None
    path = Path(output_directory) / STATUS_FILENAME
    raw = _read_bounded_bytes(path, max_bytes=STATUS_MAX_BYTES)
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def trusted_status(job: Mapping[str, Any], payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    if not is_exact_int(payload.get("status_schema_version"), 1):
        return None
    if payload.get("run_id") != job.get("job_id"):
        return None
    state = payload.get("state")
    if state not in CORE_SUPPORTED:
        return None
    expected = expected_artifact_schema(str(job.get("kind") or ""))
    if not is_exact_int(payload.get("artifact_schema_version"), expected):
        return None
    current = payload.get("current_stage")
    if current is not None and not isinstance(current, str):
        return None
    if parse_utc_safe(payload.get("started_at_utc")) is None:
        return None
    if parse_utc_safe(payload.get("updated_at_utc")) is None:
        return None
    completed = payload.get("completed_at_utc")
    if completed is not None and parse_utc_safe(completed) is None:
        return None
    elapsed = payload.get("elapsed_seconds")
    if elapsed is not None and not _finite_number(elapsed):
        return None
    for field in ("error_category", "error_message"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            return None
    return dict(payload)


def last_complete_event(
    output_directory: str | Path | None,
    *,
    run_id: str,
    max_bytes: int = EVENTS_MAX_BYTES,
    max_lines: int = EVENTS_MAX_LINES,
) -> dict[str, Any] | None:
    if output_directory is None:
        return None
    path = Path(output_directory) / EVENTS_FILENAME
    raw = _read_bounded_bytes(path, max_bytes=max_bytes)
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if raw.startswith(b"{") is False and text and not text.startswith("{") and "\n" in text:
        lines = lines[1:]
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    last: dict[str, Any] | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        event = _trusted_event(payload, run_id=run_id)
        if event is not None:
            last = event
    return last


def _trusted_event(payload: object, *, run_id: str) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    if not is_exact_int(payload.get("event_schema_version"), 1):
        return None
    if payload.get("run_id") != run_id:
        return None
    if not isinstance(payload.get("stage_key"), str) or not payload.get("stage_key"):
        return None
    number = payload.get("stage_number")
    total = payload.get("stage_total")
    if type(number) is not int or type(total) is not int:
        return None
    if total < 1 or number < 1 or number > total:
        return None
    if payload.get("state") not in EVENT_STATES:
        return None
    event_time = payload.get("event_time_utc")
    if event_time is not None and parse_utc_safe(event_time) is None:
        return None
    return {
        "stage_key": str(payload["stage_key"]),
        "stage_number": number,
        "stage_total": total,
        "state": str(payload["state"]),
        "message": payload.get("message") if isinstance(payload.get("message"), str) else "",
    }


def tail_text(
    path: str | Path | None,
    *,
    max_lines: int = LOG_MAX_LINES,
    max_bytes: int = LOG_MAX_BYTES,
) -> str:
    if path is None:
        return ""
    raw = _read_bounded_bytes(Path(path), max_bytes=max_bytes)
    if raw is None:
        return ""
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def status_age_seconds(status: Mapping[str, Any] | None, *, now: datetime | None = None) -> float | None:
    if not status:
        return None
    updated = parse_utc(status.get("updated_at_utc"))
    if updated is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - updated).total_seconds())


def worker_ended_unexpectedly(
    job: Mapping[str, Any],
    status: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
    pid_alive: PidCheck | None = None,
    stale_after: float = STALE_STATUS_SECONDS,
) -> bool:
    state = None if status is None else status.get("state")
    if state in CORE_TERMINAL:
        return False
    check = pid_alive or pid_is_alive
    if check(job.get("pid") if type(job.get("pid")) is int else None):
        return False
    current = now or datetime.now(timezone.utc)
    if status is None:
        launched = parse_utc(job.get("launch_utc")) or current
        return (current - launched).total_seconds() >= stale_after
    age = status_age_seconds(status, now=current)
    if age is None:
        return True
    return age >= stale_after


def classify_job(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    pid_alive: PidCheck | None = None,
    status: Mapping[str, Any] | None | object = _STATUS_UNSET,
    has_results: bool = False,
    artifacts_ok: bool | None = None,
    outputs_root: Path | None = None,
) -> str:
    if has_results:
        return CLASS_READY
    if status is _STATUS_UNSET:
        resolved = trusted_job_or_none(job, outputs_root=outputs_root)
        if resolved is None:
            return CLASS_UNTRUSTED
        job = resolved
        if job.get("launch_state") == LAUNCH_FAILED:
            return CLASS_LAUNCH_FAILED
        raw = read_status_payload(job.get("output_directory"))
    else:
        if job.get("launch_state") == LAUNCH_FAILED:
            return CLASS_LAUNCH_FAILED
        raw = status if isinstance(status, Mapping) or status is None else None
    check = pid_alive or pid_is_alive
    current = now or datetime.now(timezone.utc)
    trusted = trusted_status(job, raw)
    live = check(job.get("pid") if type(job.get("pid")) is int else None)
    if trusted and trusted.get("state") in {CORE_FAILED, CORE_CANCELLED}:
        return CLASS_FAILED
    if trusted and trusted.get("state") == CORE_COMPLETED:
        if artifacts_ok is False:
            return CLASS_INCOMPLETE
        return CLASS_VALIDATING
    if worker_ended_unexpectedly(job, trusted, now=current, pid_alive=check):
        return CLASS_UNEXPECTED
    if trusted and trusted.get("state") == CORE_RUNNING and live:
        return CLASS_RUNNING
    if trusted is None and raw is not None:
        if live:
            return CLASS_RUNNING
        launched = parse_utc(job.get("launch_utc")) or current
        if (current - launched).total_seconds() < LAUNCH_GRACE_SECONDS:
            return CLASS_QUEUED
        return CLASS_UNEXPECTED
    launched = parse_utc(job.get("launch_utc")) or current
    age = (current - launched).total_seconds()
    if live:
        if trusted and trusted.get("state") == CORE_RUNNING:
            return CLASS_RUNNING
        return CLASS_QUEUED if age < LAUNCH_GRACE_SECONDS else CLASS_RUNNING
    if age < LAUNCH_GRACE_SECONDS:
        return CLASS_QUEUED
    return CLASS_UNEXPECTED


def friendly_stage_label(stage_key: str | None) -> str:
    if not stage_key:
        return "Waiting for the worker"
    return STAGE_LABELS.get(stage_key, "Working")


def progress_from_event(event: Mapping[str, Any] | None) -> tuple[int, int] | None:
    if not event:
        return None
    number = event.get("stage_number")
    total = event.get("stage_total")
    if type(number) is not int or type(total) is not int or total < 1:
        return None
    completed = number if event.get("state") == "completed" else max(number - 1, 0)
    return completed, total


def live_elapsed_seconds(
    status: Mapping[str, Any] | None,
    *,
    launched_at_utc: str | None = None,
    now: datetime | None = None,
) -> float | None:
    current = now or datetime.now(timezone.utc)
    payload = status or {}
    state = payload.get("state")
    started = parse_utc(payload.get("started_at_utc")) or parse_utc(launched_at_utc)
    finished = parse_utc(payload.get("completed_at_utc"))
    reported = payload.get("elapsed_seconds")
    if state in CORE_TERMINAL:
        if _finite_number(reported):
            return max(0.0, float(reported))
        if started is not None and finished is not None:
            return max(0.0, (finished - started).total_seconds())
        return None
    if started is not None:
        return max(0.0, (current - started).total_seconds())
    if _finite_number(reported):
        return max(0.0, float(reported))
    return None


def format_elapsed(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def safe_error_message(text: Any) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "The simulation failed."
    if "Traceback" in raw or "\n" in raw:
        return "The simulation failed."
    if "\\" in raw or raw.startswith("/") or ":/" in raw:
        return "The simulation failed."
    return raw


def _hooked_outputs_root(outputs_root: Path | None) -> Path | None:
    if outputs_root is not None:
        return Path(outputs_root)
    from ui.services.launch import TEST_HOOKS

    hooked = TEST_HOOKS.get("outputs_root")
    return Path(hooked) if hooked is not None else None


def diagnostic_paths(
    job: Mapping[str, Any] | None,
    *,
    outputs_root: Path | None = None,
) -> dict[str, Path]:
    trusted = trusted_job_or_none(job, outputs_root=_hooked_outputs_root(outputs_root))
    if trusted is None:
        return {}
    found: dict[str, Path] = {}
    folder = Path(trusted["output_directory"])
    for key, name in (("status", STATUS_FILENAME), ("events", EVENTS_FILENAME), ("log", LOG_FILENAME)):
        path = folder / name
        if path.is_file():
            found[key] = path
    console = Path(trusted["worker_console_path"])
    if console.is_file():
        found["console"] = console
    request = Path(trusted["request_path"])
    if request.is_file():
        found["request"] = request
    return found


def classification_is_active(klass: str) -> bool:
    return klass in {CLASS_QUEUED, CLASS_RUNNING, CLASS_VALIDATING}


def classification_is_recovery(klass: str) -> bool:
    return klass in {
        CLASS_FAILED,
        CLASS_UNEXPECTED,
        CLASS_INCOMPLETE,
        CLASS_LAUNCH_FAILED,
        CLASS_UNTRUSTED,
    }


def reconcile_execution(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    pid_alive: PidCheck | None = None,
    outputs_root: Path | None = None,
) -> str:
    from ui.flow import unlock_results
    from ui.services.artifacts import accept_live_artifacts, result_is_valid

    root = _hooked_outputs_root(outputs_root)
    result = state.get("result") if isinstance(state.get("result"), Mapping) else None
    job = state.get("job") if isinstance(state.get("job"), Mapping) else None
    if result_is_valid(result, job=job, outputs_root=root):
        return CLASS_READY
    if job is None:
        return CLASS_INCOMPLETE
    trusted = trusted_job_or_none(job, outputs_root=root)
    if trusted is None:
        return CLASS_UNTRUSTED
    raw = read_status_payload(trusted["output_directory"])
    klass = classify_job(
        trusted,
        now=now,
        pid_alive=pid_alive,
        status=raw,
        outputs_root=root,
    )
    if klass == CLASS_VALIDATING:
        try:
            accepted = accept_live_artifacts(trusted, outputs_root=root)
        except ValueError:
            return CLASS_INCOMPLETE
        if not result_is_valid(accepted, job=trusted, outputs_root=root):
            return CLASS_INCOMPLETE
        unlock_results(state, accepted)
        return CLASS_READY
    return klass
