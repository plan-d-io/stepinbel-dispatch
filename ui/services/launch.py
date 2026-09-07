"""At-most-once detached launch transaction. Popen is called at most once per job."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from ui.flow import (
    is_demo,
    stored_form,
    stored_snapshot,
)
from ui.services.jobs import (
    ERROR_DEMO,
    ERROR_LAUNCH,
    ERROR_OUTPUT_EXISTS,
    ERROR_STAGING,
    ERROR_UNTRUSTED,
    ERROR_WORKER,
    LAUNCH_FAILED,
    LAUNCH_LAUNCHED,
    LAUNCH_PLANNED,
    atomic_write_json,
    create_staging_directory,
    iso_utc,
    job_paths,
    job_record,
    new_job_id,
    remove_private_staging,
    trusted_job_or_none,
    write_job_record,
)
from ui.services.paths import DATA_DIRECTORY, REPO_ROOT, default_outputs_root, default_staging_root
from ui.services.process import PopenFactory, start_worker
from ui.services.request import prepare_launch_request, request_kind, revalidate_snapshot
from ui.services.snapshot import snapshot_digest

QUERY_JOB = "job"
TEST_HOOKS: dict[str, Any] = {}
OnPlanned = Callable[[Mapping[str, Any]], None]


def _plain(record: Mapping[str, Any]) -> dict[str, Any]:
    return dict(record)


def _record(name: str) -> None:
    ops = TEST_HOOKS.get("operations")
    if isinstance(ops, list):
        ops.append(name)


def _hook_value(name: str, explicit: Any) -> Any:
    return explicit if explicit is not None else TEST_HOOKS.get(name)


def _resolved_hooks(
    *,
    outputs_root: Path | None,
    cwd: Path | None,
    popen: PopenFactory | None,
    now: datetime | None,
    job_id: str | None,
    data_directory: Path | None,
    allow_real_worker: bool,
    write_job: Any,
) -> dict[str, Any]:
    testing = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    outputs_root = _hook_value("outputs_root", outputs_root)
    popen = _hook_value("popen", popen)
    now = _hook_value("now", now)
    job_id = _hook_value("job_id", job_id)
    data_directory = _hook_value("data_directory", data_directory)
    cwd = _hook_value("cwd", cwd)
    allow_real_worker = bool(allow_real_worker or TEST_HOOKS.get("allow_real_worker"))
    if testing and outputs_root is None:
        raise RuntimeError("tests must pass a temporary outputs_root")
    worker = popen or subprocess.Popen
    if testing and worker is subprocess.Popen and not allow_real_worker:
        raise RuntimeError("tests must inject a fake worker")
    writer = write_job if write_job is not write_job_record else TEST_HOOKS.get("write_job") or write_job_record
    return {
        "outputs_root": Path(outputs_root) if outputs_root is not None else default_outputs_root(),
        "cwd": Path(cwd) if cwd is not None else REPO_ROOT,
        "popen": worker,
        "now": now,
        "job_id": job_id,
        "data_directory": Path(data_directory) if data_directory is not None else DATA_DIRECTORY,
        "write_job": writer,
    }


def _abort_before_planned(
    state: dict[str, Any],
    *,
    staging_dir: Path | None,
    staging_root: Path,
    error: str,
) -> dict[str, Any]:
    state.pop("job", None)
    state["launch_error"] = error
    remove_private_staging(staging_dir, staging_root=staging_root)
    return {
        "ok": False,
        "error": error,
        "popen_called": False,
        "ready_to_start": False,
    }


def _lock_response(state: dict[str, Any], trusted: Mapping[str, Any]) -> dict[str, Any]:
    state["job"] = _plain(trusted)
    return {
        "ok": True,
        "job": _plain(trusted),
        "reconnect": True,
        "popen_called": False,
        "ready_to_start": False,
        "query_job": trusted.get("job_id"),
    }


def prepare_live_job(
    state: dict[str, Any],
    *,
    outputs_root: Path | None = None,
    cwd: Path | None = None,
    popen: PopenFactory | None = None,
    now: datetime | None = None,
    job_id: str | None = None,
    data_directory: Path | None = None,
    allow_real_worker: bool = False,
    write_job: Any = write_job_record,
) -> dict[str, Any]:
    """Validate inputs and persist the planned job before any worker start."""
    hooks = _resolved_hooks(
        outputs_root=outputs_root,
        cwd=cwd,
        popen=popen,
        now=now,
        job_id=job_id,
        data_directory=data_directory,
        allow_real_worker=allow_real_worker,
        write_job=write_job,
    )
    out_root = hooks["outputs_root"]
    stage_root = default_staging_root(out_root)
    if is_demo(state):
        return {"ok": False, "error": ERROR_DEMO, "popen_called": False, "ready_to_start": False}

    existing = state.get("job") if isinstance(state.get("job"), Mapping) else None
    if existing is not None:
        trusted = trusted_job_or_none(existing, outputs_root=out_root)
        if trusted is None:
            state["launch_error"] = ERROR_UNTRUSTED
            return {
                "ok": False,
                "error": ERROR_UNTRUSTED,
                "popen_called": False,
                "ready_to_start": False,
            }
        return _lock_response(state, trusted)

    snapshot = stored_snapshot(state)
    form = stored_form(state)
    staging_dir: Path | None = None
    planned_written = False
    created_id = hooks["job_id"] or new_job_id(now=hooks["now"])
    try:
        rebuilt = revalidate_snapshot(snapshot, form, demo=False)
        _record("validate_snapshot")
        kind = request_kind(list(rebuilt.get("markets") or []))
        paths = job_paths(created_id, kind=kind, outputs_root=out_root)
        _record("derive_paths")
        if paths["output_directory"].exists():
            raise ValueError(ERROR_OUTPUT_EXISTS)
        kind, payload, request = prepare_launch_request(
            rebuilt,
            form,
            demo=False,
            output_directory=paths["output_directory"],
            run_id=created_id,
            data_directory=hooks["data_directory"],
            created_at_utc=hooks["now"],
        )
        if request.run_id != created_id:
            raise ValueError(ERROR_LAUNCH)
        if Path(request.output_directory).resolve() != paths["output_directory"]:
            raise ValueError(ERROR_LAUNCH)
        fingerprint = snapshot_digest(rebuilt)
        create_staging_directory(paths["staging_directory"])
        staging_dir = paths["staging_directory"]
        atomic_write_json(paths["configured_snapshot_path"], rebuilt)
        _record("write_snapshot")
        atomic_write_json(paths["request_path"], payload)
        _record("write_request")
        planned = job_record(
            job_id=created_id,
            kind=kind,
            markets=list(rebuilt["markets"]),
            snapshot_fingerprint=fingerprint,
            launch_state=LAUNCH_PLANNED,
            launch_utc=iso_utc(hooks["now"]),
            paths=paths,
            pid=None,
            outputs_root=out_root,
        )
        hooks["write_job"](paths["job_path"], planned, outputs_root=out_root)
        planned_written = True
        _record("write_planned")
        state["job"] = _plain(planned)
        state["launch_error"] = None
        state["result"] = None
        _record("store_session")
    except Exception as exc:
        if planned_written:
            return {
                "ok": False,
                "error": ERROR_STAGING,
                "popen_called": False,
                "ready_to_start": False,
            }
        message = str(exc).strip() or ERROR_LAUNCH
        return _abort_before_planned(
            state,
            staging_dir=staging_dir,
            staging_root=stage_root,
            error=message if message else ERROR_LAUNCH,
        )

    return {
        "ok": True,
        "job": state["job"],
        "popen_called": False,
        "ready_to_start": True,
        "query_job": created_id,
    }


def start_prepared_job(
    state: dict[str, Any],
    *,
    outputs_root: Path | None = None,
    cwd: Path | None = None,
    popen: PopenFactory | None = None,
    now: datetime | None = None,
    job_id: str | None = None,
    data_directory: Path | None = None,
    allow_real_worker: bool = False,
    write_job: Any = write_job_record,
) -> dict[str, Any]:
    """Start the already-planned job. Never creates a second worker."""
    hooks = _resolved_hooks(
        outputs_root=outputs_root,
        cwd=cwd,
        popen=popen,
        now=now,
        job_id=job_id,
        data_directory=data_directory,
        allow_real_worker=allow_real_worker,
        write_job=write_job,
    )
    out_root = hooks["outputs_root"]
    existing = state.get("job") if isinstance(state.get("job"), Mapping) else None
    trusted = trusted_job_or_none(existing, outputs_root=out_root)
    if trusted is None:
        state["launch_error"] = ERROR_UNTRUSTED
        return {
            "ok": False,
            "error": ERROR_UNTRUSTED,
            "popen_called": False,
            "ready_to_start": False,
        }
    if trusted.get("launch_state") != LAUNCH_PLANNED or trusted.get("pid") is not None:
        return _lock_response(state, trusted)

    paths = job_paths(trusted["job_id"], kind=trusted["kind"], outputs_root=out_root)
    kind = trusted["kind"]
    created_id = trusted["job_id"]
    try:
        _record("popen")
        pid = start_worker(
            kind=kind,
            request_path=paths["request_path"],
            console_path=paths["worker_console_path"],
            cwd=hooks["cwd"],
            popen=hooks["popen"],
        )
    except Exception:
        failed = dict(trusted)
        failed["launch_state"] = LAUNCH_FAILED
        try:
            hooks["write_job"](paths["job_path"], failed, outputs_root=out_root)
            state["job"] = _plain(failed)
        except Exception:
            state["job"] = _plain(failed)
        state["launch_error"] = ERROR_WORKER
        return {
            "ok": False,
            "error": ERROR_WORKER,
            "job": state["job"],
            "popen_called": True,
            "ready_to_start": False,
            "query_job": created_id,
        }

    launched = dict(trusted)
    launched["pid"] = pid
    launched["launch_state"] = LAUNCH_LAUNCHED
    try:
        validated = hooks["write_job"](paths["job_path"], launched, outputs_root=out_root)
        state["job"] = _plain(validated)
        _record("write_launched")
    except Exception:
        state["job"] = _plain(trusted)
        return {
            "ok": True,
            "job": state["job"],
            "popen_called": True,
            "pid_update_failed": True,
            "ready_to_start": False,
            "query_job": created_id,
        }
    state["launch_error"] = None
    return {
        "ok": True,
        "job": state["job"],
        "popen_called": True,
        "ready_to_start": False,
        "query_job": created_id,
    }


def launch_live_job(
    state: dict[str, Any],
    *,
    outputs_root: Path | None = None,
    cwd: Path | None = None,
    popen: PopenFactory | None = None,
    now: datetime | None = None,
    job_id: str | None = None,
    data_directory: Path | None = None,
    allow_real_worker: bool = False,
    write_job: Any = write_job_record,
    on_planned: OnPlanned | None = None,
) -> dict[str, Any]:
    """Prepare one job directory and start one detached CLI worker.

    A stored planned/launched/failed job is a lock. Reconnects never call Popen.
    Streamlit query/session persistence belongs in the view callback, not here.
    """
    kwargs = {
        "outputs_root": outputs_root,
        "cwd": cwd,
        "popen": popen,
        "now": now,
        "job_id": job_id,
        "data_directory": data_directory,
        "allow_real_worker": allow_real_worker,
        "write_job": write_job,
    }
    prepared = prepare_live_job(state, **kwargs)
    callback = on_planned or TEST_HOOKS.get("on_planned")
    if prepared.get("query_job") and callback is not None:
        _record("on_planned")
        callback(prepared)
    if not prepared.get("ok") or prepared.get("reconnect") or not prepared.get("ready_to_start"):
        return prepared
    return start_prepared_job(state, **kwargs)


def mark_launch_failed(state: dict[str, Any], error: str) -> dict[str, Any]:
    job = state.get("job") if isinstance(state.get("job"), dict) else None
    if job is not None:
        job["launch_state"] = LAUNCH_FAILED
        state["job"] = _plain(job)
    state["launch_error"] = error
    return dict(state)
