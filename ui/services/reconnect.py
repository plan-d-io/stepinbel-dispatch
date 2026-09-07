"""Browser-refresh reconnection from a single ?job= query parameter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from ui.flow import (
    STAGE_REVIEW,
    apply_restored_job,
)
from ui.services.jobs import is_path_safe_job_id, load_durable_job, load_json_object, resolve_trusted_job
from ui.services.paths import JOB_QUERY_KEY, default_outputs_root
from ui.services.snapshot import snapshot_block_reason, snapshot_digest, snapshot_is_complete

ERROR_QUERY = "The reconnect link is not valid."
ERROR_JOB = "The stored job record could not be restored."
ERROR_SNAPSHOT = "The stored configuration for this run is no longer valid."


def query_job_values(query: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(query, Mapping):
        return []
    raw = query.get(JOB_QUERY_KEY)
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    return [str(raw)]


def parse_query_job_id(values: Sequence[str]) -> str | None:
    if not values:
        return None
    if len(values) != 1:
        raise ValueError(ERROR_QUERY)
    job_id = values[0]
    if not is_path_safe_job_id(job_id):
        raise ValueError(ERROR_QUERY)
    return job_id


def restore_job_from_id(
    job_id: str,
    *,
    outputs_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not is_path_safe_job_id(job_id):
        raise ValueError(ERROR_QUERY)
    try:
        record = load_durable_job(job_id, outputs_root=outputs_root)
        snapshot = load_json_object(Path(record["configured_snapshot_path"]))
    except (OSError, ValueError) as exc:
        raise ValueError(str(exc) or ERROR_JOB) from exc
    if not snapshot_is_complete(snapshot):
        raise ValueError(ERROR_SNAPSHOT)
    if snapshot_digest(snapshot) != record["snapshot_fingerprint"]:
        raise ValueError(ERROR_SNAPSHOT)
    form = snapshot.get("form") if isinstance(snapshot.get("form"), Mapping) else None
    if snapshot_block_reason(snapshot, form) is not None:
        raise ValueError(ERROR_SNAPSHOT)
    return record, snapshot


def apply_query_reconnect(
    state: dict[str, Any],
    values: Sequence[str],
    *,
    outputs_root: Path | None = None,
) -> dict[str, Any]:
    """Restore a durable job into JSON-compatible session state. Never calls Popen."""
    if not values:
        return {"ok": True, "reconnected": False}
    try:
        job_id = parse_query_job_id(values)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "reconnected": False}
    if job_id is None:
        return {"ok": True, "reconnected": False}
    existing = state.get("job") if isinstance(state.get("job"), Mapping) else None
    already = isinstance(existing, Mapping) and existing.get("job_id") == job_id
    if already:
        try:
            trusted = resolve_trusted_job(existing, outputs_root=outputs_root)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "reconnected": False}
        return {"ok": True, "reconnected": True, "job": trusted, "already": True}
    try:
        record, snapshot = restore_job_from_id(job_id, outputs_root=outputs_root)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "reconnected": False}
    apply_restored_job(state, job=record, snapshot=snapshot)
    state["stage"] = STAGE_REVIEW
    if int(state.get("max_stage") or 1) < STAGE_REVIEW:
        state["max_stage"] = STAGE_REVIEW
    return {"ok": True, "reconnected": True, "job": record, "already": False}
