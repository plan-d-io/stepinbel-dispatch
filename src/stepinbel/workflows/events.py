"""Progress events, durable journal files, and cooperative cancellation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from stepinbel.reporting.io import append_text, atomic_write_json
from stepinbel.workflows.constants import (
    RUN_ARTIFACT_SCHEMA_VERSION,
    RUN_EVENT_SCHEMA_VERSION,
    RUN_STATUS_SCHEMA_VERSION,
    STAGES,
)
from stepinbel.workflows.errors import RunCancelledError, RunExecutionError
from stepinbel.workflows.serialize import dumps_jsonl, format_utc

EVENT_STATES = frozenset({"started", "completed", "failed", "cancelled"})


@dataclass(frozen=True)
class RunEvent:
    """One journaled workflow event."""

    event_schema_version: int
    run_id: str
    sequence: int
    event_time_utc: datetime
    stage_key: str
    stage_number: int
    stage_total: int
    state: str
    level: str
    message: str
    details: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.event_schema_version != RUN_EVENT_SCHEMA_VERSION:
            raise RunExecutionError("event_schema_version is not supported", category="execution")
        if self.state not in EVENT_STATES:
            raise RunExecutionError(f"unsupported event state {self.state!r}", category="execution")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    def to_payload(self) -> dict[str, object]:
        return {
            "event_schema_version": self.event_schema_version,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_time_utc": format_utc(self.event_time_utc),
            "stage_key": self.stage_key,
            "stage_number": self.stage_number,
            "stage_total": self.stage_total,
            "state": self.state,
            "level": self.level,
            "message": self.message,
            "details": dict(self.details),
        }


class RunJournal:
    """Append-only events/log plus an atomic status snapshot."""

    def __init__(
        self,
        directory: Path,
        run_id: str,
        progress: Callable[[RunEvent], None] | None,
        *,
        stages: tuple[str, ...] = STAGES,
        artifact_schema_version: int = RUN_ARTIFACT_SCHEMA_VERSION,
    ) -> None:
        self.directory = directory
        self.run_id = run_id
        self._progress = progress
        self._stages = tuple(stages)
        self.sequence = 0
        self.started_at = datetime.now(timezone.utc).replace(microsecond=0)
        self._mono_start = time.perf_counter()
        self.status: dict[str, Any] = {
            "status_schema_version": RUN_STATUS_SCHEMA_VERSION,
            "run_id": run_id,
            "state": "running",
            "current_stage": None,
            "message": "starting",
            "started_at_utc": format_utc(self.started_at),
            "updated_at_utc": format_utc(self.started_at),
            "completed_at_utc": None,
            "elapsed_seconds": 0.0,
            "artifact_schema_version": artifact_schema_version,
            "error_category": None,
            "error_message": None,
        }
        (directory / "run_events.jsonl").write_text("", encoding="utf-8")
        (directory / "run.log").write_text("", encoding="utf-8")
        self._write_status()

    def check_cancel(self, cancel_requested: Callable[[], bool] | None) -> None:
        if cancel_requested is None:
            return
        try:
            requested = bool(cancel_requested())
        except RunCancelledError:
            raise
        except Exception as exc:
            raise RunExecutionError(str(exc), category="execution") from exc
        if requested:
            raise RunCancelledError("run cancelled", category="cancelled")

    def emit(
        self,
        *,
        stage_key: str,
        state: str,
        message: str,
        level: str = "info",
        details: Mapping[str, object] | None = None,
        notify: bool = True,
        status_state: str | None = None,
        error_category: str | None = None,
        error_message: str | None = None,
        completed: bool = False,
    ) -> RunEvent:
        self.sequence += 1
        now = datetime.now(timezone.utc).replace(microsecond=0)
        try:
            stage_number = self._stages.index(stage_key) + 1
        except ValueError:
            stage_number = 0
        event = RunEvent(
            event_schema_version=RUN_EVENT_SCHEMA_VERSION,
            run_id=self.run_id,
            sequence=self.sequence,
            event_time_utc=now,
            stage_key=stage_key,
            stage_number=stage_number,
            stage_total=len(self._stages),
            state=state,
            level=level,
            message=message,
            details=dict(details or {}),
        )
        append_text(self.directory / "run_events.jsonl", dumps_jsonl(event.to_payload()))
        append_text(
            self.directory / "run.log",
            f"{format_utc(now)} [{state}] {stage_key}: {message}\n",
        )
        self.status["current_stage"] = stage_key
        self.status["message"] = message
        self.status["updated_at_utc"] = format_utc(now)
        self.status["elapsed_seconds"] = time.perf_counter() - self._mono_start
        if status_state is not None:
            self.status["state"] = status_state
        if error_category is not None:
            self.status["error_category"] = error_category
        if error_message is not None:
            self.status["error_message"] = error_message
        if completed:
            self.status["completed_at_utc"] = format_utc(now)
        self._write_status()
        if notify:
            self.notify(event)
        return event

    def notify(self, event: RunEvent) -> None:
        if self._progress is None:
            return
        try:
            self._progress(event)
        except Exception as exc:
            self.emit(
                stage_key=event.stage_key,
                state="failed",
                message="progress callback failed",
                level="error",
                details={"error": str(exc)},
                notify=False,
                status_state="failed",
                error_category="progress",
                error_message=str(exc),
                completed=True,
            )
            raise RunExecutionError(str(exc), category="progress") from exc

    def start_stage(self, stage_key: str) -> RunEvent:
        current = str(self.status["state"])
        return self.emit(
            stage_key=stage_key,
            state="started",
            message=f"started {stage_key}",
            status_state="running" if current == "running" else current,
        )

    def mark_completed_status(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.status["state"] = "completed"
        self.status["message"] = "artifacts written"
        self.status["updated_at_utc"] = format_utc(now)
        self.status["completed_at_utc"] = format_utc(now)
        self.status["elapsed_seconds"] = time.perf_counter() - self._mono_start
        self.status["error_category"] = None
        self.status["error_message"] = None
        self._write_status()

    def complete_stage(self, stage_key: str, *, notify: bool = True) -> RunEvent:
        current = str(self.status["state"])
        if current in {"completed", "failed", "cancelled"}:
            status_state = current
            done = current != "running"
        else:
            status_state = "completed" if stage_key == self._stages[-1] else "running"
            done = stage_key == self._stages[-1]
        return self.emit(
            stage_key=stage_key,
            state="completed",
            message=f"completed {stage_key}",
            status_state=status_state,
            completed=done,
            notify=notify,
        )

    def record_failed(
        self,
        stage_key: str,
        error: Exception,
        category: str,
        *,
        notify: bool = True,
    ) -> RunEvent:
        return self.emit(
            stage_key=stage_key,
            state="failed",
            message=str(error),
            level="error",
            details={"category": category},
            notify=notify,
            status_state="failed",
            error_category=category,
            error_message=str(error),
            completed=True,
        )

    def record_cancelled(self, stage_key: str) -> RunEvent:
        return self.emit(
            stage_key=stage_key,
            state="cancelled",
            message="run cancelled",
            status_state="cancelled",
            error_category="cancelled",
            error_message="run cancelled",
            completed=True,
        )

    def _write_status(self) -> None:
        atomic_write_json(self.directory / "run_status.json", self.status)
