"""Path-safe job IDs, trusted output/staging paths, and durable job records."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ui.flow import is_exact_int
from ui.services.paths import (
    CASE_REQUEST_NAME,
    COMPARISON_REQUEST_NAME,
    JOB_JSON_NAME,
    KIND_CASE,
    KIND_COMPARISON,
    SNAPSHOT_JSON_NAME,
    WORKER_CONSOLE_NAME,
    default_outputs_root,
    default_staging_root,
    request_filename,
)

JOB_RECORD_VERSION = 1
JOB_ID_RE = re.compile(r"^stepinbel-\d{8}T\d{6}Z-[0-9a-f]{8}$")
LAUNCH_PLANNED = "planned"
LAUNCH_LAUNCHED = "launched"
LAUNCH_FAILED = "failed"
LAUNCH_STATES = frozenset({LAUNCH_PLANNED, LAUNCH_LAUNCHED, LAUNCH_FAILED})
JOB_RECORD_KEYS = (
    "schema_version",
    "job_id",
    "kind",
    "markets",
    "snapshot_fingerprint",
    "launch_state",
    "launch_utc",
    "output_directory",
    "staging_directory",
    "configured_snapshot_path",
    "request_path",
    "worker_console_path",
    "pid",
)

ERROR_LAUNCH = "The simulation could not be started. Check the settings and try again."
ERROR_WORKER = "The worker process could not be started."
ERROR_ACTIVE = "A simulation is already running."
ERROR_STAGING = "The simulation files could not be prepared. Try again."
ERROR_DEMO = "Demo mode opens saved results and does not start a worker."
ERROR_OUTPUT_EXISTS = "The output directory already exists. Start a new run from Review."
ERROR_UNTRUSTED = "The stored run record is not valid."
SESSION_PATH_FIELDS = (
    "output_directory",
    "staging_directory",
    "configured_snapshot_path",
    "request_path",
    "worker_console_path",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_utc(moment: datetime | None = None) -> str:
    value = utc_now() if moment is None else moment
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_safe(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def new_job_id(*, now: datetime | None = None) -> str:
    stamp = iso_utc(now).replace("-", "").replace(":", "")
    return f"stepinbel-{stamp}-{uuid.uuid4().hex[:8]}"


def is_path_safe_job_id(value: object) -> bool:
    return isinstance(value, str) and bool(JOB_ID_RE.fullmatch(value))


def require_job_id(value: object) -> str:
    if not is_path_safe_job_id(value):
        raise ValueError("job ID is not path-safe")
    return str(value)


def _resolve_under(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    trusted = root.resolve()
    if resolved != trusted and trusted not in resolved.parents:
        raise ValueError("path escapes the trusted root")
    return resolved


def job_paths(
    job_id: str,
    *,
    kind: str,
    outputs_root: Path | None = None,
) -> dict[str, Path]:
    if kind not in {KIND_CASE, KIND_COMPARISON}:
        raise ValueError("unsupported job kind")
    identifier = require_job_id(job_id)
    out_root = Path(outputs_root) if outputs_root is not None else default_outputs_root()
    out_root = out_root.resolve()
    staging_root = default_staging_root(out_root).resolve()
    output_directory = _resolve_under(out_root / identifier, out_root)
    staging_directory = _resolve_under(staging_root / identifier, staging_root)
    return {
        "output_directory": output_directory,
        "staging_directory": staging_directory,
        "configured_snapshot_path": _resolve_under(
            staging_directory / SNAPSHOT_JSON_NAME, staging_root
        ),
        "request_path": _resolve_under(
            staging_directory / request_filename(kind), staging_root
        ),
        "worker_console_path": _resolve_under(
            staging_directory / WORKER_CONSOLE_NAME, staging_root
        ),
        "job_path": _resolve_under(staging_directory / JOB_JSON_NAME, staging_root),
    }


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    text = json.dumps(dict(payload), indent=2, allow_nan=False, ensure_ascii=False) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON document must be an object")
    return payload


def _canonical_markets(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("markets")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("markets")
    if len(value) != len(set(value)):
        raise ValueError("markets")
    from ui.services.paths import CANONICAL_MARKETS

    allowed = set(CANONICAL_MARKETS)
    if any(item not in allowed for item in value):
        raise ValueError("markets")
    expected = [item for item in CANONICAL_MARKETS if item in value]
    if value != expected:
        raise ValueError("markets")
    return list(value)


def _require_pid(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError("pid")
    return value


def validate_job_record(
    payload: Mapping[str, Any],
    *,
    outputs_root: Path | None = None,
    expected_job_id: str | None = None,
) -> dict[str, Any]:
    if set(payload) != set(JOB_RECORD_KEYS):
        raise ValueError("job record fields")
    if not is_exact_int(payload.get("schema_version"), JOB_RECORD_VERSION):
        raise ValueError("job schema")
    job_id = require_job_id(payload.get("job_id"))
    if expected_job_id is not None and job_id != expected_job_id:
        raise ValueError("job ID mismatch")
    kind = payload.get("kind")
    if kind not in {KIND_CASE, KIND_COMPARISON}:
        raise ValueError("job kind")
    markets = _canonical_markets(payload.get("markets"))
    if kind == KIND_CASE and len(markets) != 1:
        raise ValueError("case markets")
    if kind == KIND_COMPARISON and len(markets) not in {2, 3}:
        raise ValueError("comparison markets")
    fingerprint = payload.get("snapshot_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("snapshot fingerprint")
    launch_state = payload.get("launch_state")
    if launch_state not in LAUNCH_STATES:
        raise ValueError("launch state")
    launch_utc = payload.get("launch_utc")
    if not isinstance(launch_utc, str) or parse_utc_safe(launch_utc) is None:
        raise ValueError("launch UTC")
    pid = _require_pid(payload.get("pid"))
    derived = job_paths(job_id, kind=kind, outputs_root=outputs_root)
    recorded = {
        "output_directory": payload.get("output_directory"),
        "staging_directory": payload.get("staging_directory"),
        "configured_snapshot_path": payload.get("configured_snapshot_path"),
        "request_path": payload.get("request_path"),
        "worker_console_path": payload.get("worker_console_path"),
    }
    for key, expected in derived.items():
        if key == "job_path":
            continue
        value = recorded.get(key)
        if not isinstance(value, str) or Path(value).resolve() != expected:
            raise ValueError(f"{key} mismatch")
    request_name = Path(str(recorded["request_path"])).name
    if kind == KIND_CASE and request_name != CASE_REQUEST_NAME:
        raise ValueError("request path")
    if kind == KIND_COMPARISON and request_name != COMPARISON_REQUEST_NAME:
        raise ValueError("request path")
    return {
        "schema_version": JOB_RECORD_VERSION,
        "job_id": job_id,
        "kind": kind,
        "markets": markets,
        "snapshot_fingerprint": fingerprint,
        "launch_state": launch_state,
        "launch_utc": launch_utc,
        "output_directory": str(derived["output_directory"]),
        "staging_directory": str(derived["staging_directory"]),
        "configured_snapshot_path": str(derived["configured_snapshot_path"]),
        "request_path": str(derived["request_path"]),
        "worker_console_path": str(derived["worker_console_path"]),
        "pid": pid,
    }


def job_record(
    *,
    job_id: str,
    kind: str,
    markets: list[str],
    snapshot_fingerprint: str,
    launch_state: str,
    launch_utc: str,
    paths: Mapping[str, Path],
    pid: int | None,
    outputs_root: Path | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": JOB_RECORD_VERSION,
        "job_id": job_id,
        "kind": kind,
        "markets": list(markets),
        "snapshot_fingerprint": snapshot_fingerprint,
        "launch_state": launch_state,
        "launch_utc": launch_utc,
        "output_directory": str(paths["output_directory"]),
        "staging_directory": str(paths["staging_directory"]),
        "configured_snapshot_path": str(paths["configured_snapshot_path"]),
        "request_path": str(paths["request_path"]),
        "worker_console_path": str(paths["worker_console_path"]),
        "pid": pid,
    }
    return validate_job_record(payload, outputs_root=outputs_root, expected_job_id=job_id)


def write_job_record(
    path: Path,
    record: Mapping[str, Any],
    *,
    outputs_root: Path | None = None,
) -> dict[str, Any]:
    validated = validate_job_record(record, outputs_root=outputs_root)
    atomic_write_json(path, validated)
    return validated


def load_job_record(path: Path, *, outputs_root: Path | None = None, expected_job_id: str | None = None) -> dict[str, Any]:
    return validate_job_record(
        load_json_object(path),
        outputs_root=outputs_root,
        expected_job_id=expected_job_id,
    )


def load_durable_job(job_id: str, *, outputs_root: Path | None = None) -> dict[str, Any]:
    identifier = require_job_id(job_id)
    out_root = Path(outputs_root) if outputs_root is not None else default_outputs_root()
    staging_root = default_staging_root(out_root).resolve()
    staging = (staging_root / identifier).resolve()
    if staging_root not in staging.parents:
        raise ValueError("staging path escapes the trusted root")
    job_path = staging / JOB_JSON_NAME
    if not job_path.is_file():
        raise ValueError("job record is missing")
    return load_job_record(job_path, outputs_root=out_root, expected_job_id=identifier)


def create_staging_directory(staging_directory: Path) -> None:
    staging_directory.parent.mkdir(parents=True, exist_ok=True)
    staging_directory.mkdir(exist_ok=False)


def remove_private_staging(staging_directory: Path | None, *, staging_root: Path) -> None:
    if staging_directory is None:
        return
    try:
        resolved = staging_directory.resolve()
        root = staging_root.resolve()
    except OSError:
        return
    if resolved == root or root not in resolved.parents:
        return
    if not resolved.exists():
        return
    shutil.rmtree(resolved)


def job_is_lock(job: Mapping[str, Any] | None) -> bool:
    if not isinstance(job, Mapping):
        return False
    if not is_path_safe_job_id(job.get("job_id")):
        return False
    return job.get("launch_state") in {LAUNCH_PLANNED, LAUNCH_LAUNCHED, LAUNCH_FAILED}


def resolve_trusted_job(
    session_job: Mapping[str, Any] | None,
    *,
    outputs_root: Path | None = None,
) -> dict[str, Any]:
    """Load the durable job for a session record. Never use session paths for I/O."""
    if not isinstance(session_job, Mapping):
        raise ValueError(ERROR_UNTRUSTED)
    try:
        job_id = require_job_id(session_job.get("job_id"))
        kind = session_job.get("kind")
        if kind not in {KIND_CASE, KIND_COMPARISON}:
            raise ValueError(ERROR_UNTRUSTED)
        derived = job_paths(job_id, kind=kind, outputs_root=outputs_root)
        durable = load_durable_job(job_id, outputs_root=outputs_root)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(ERROR_UNTRUSTED) from exc
    if durable["job_id"] != job_id or durable["kind"] != kind:
        raise ValueError(ERROR_UNTRUSTED)
    session_markets = session_job.get("markets")
    if session_markets is not None:
        try:
            if _canonical_markets(session_markets) != durable["markets"]:
                raise ValueError(ERROR_UNTRUSTED)
        except ValueError as exc:
            raise ValueError(ERROR_UNTRUSTED) from exc
    session_fingerprint = session_job.get("snapshot_fingerprint")
    if session_fingerprint is not None and session_fingerprint != durable["snapshot_fingerprint"]:
        raise ValueError(ERROR_UNTRUSTED)
    for key in SESSION_PATH_FIELDS:
        value = session_job.get(key)
        if value is None:
            continue
        try:
            if Path(str(value)).resolve() != derived[key]:
                raise ValueError(ERROR_UNTRUSTED)
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(ERROR_UNTRUSTED) from exc
    return durable


def trusted_job_or_none(
    session_job: Mapping[str, Any] | None,
    *,
    outputs_root: Path | None = None,
) -> dict[str, Any] | None:
    try:
        return resolve_trusted_job(session_job, outputs_root=outputs_root)
    except (OSError, TypeError, ValueError):
        return None
