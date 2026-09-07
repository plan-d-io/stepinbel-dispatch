from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui.flow import (
    STAGE_RESULTS,
    STAGE_REVIEW,
    default_state,
    require_json_compatible,
    return_to_review,
    store_snapshot,
    unlock_results,
)
from ui.services.artifacts import ERROR_PARTIAL, accept_live_artifacts, open_demo_artifacts, result_is_valid
from ui.services.form import default_live_form
from ui.services.jobs import (
    JOB_RECORD_VERSION,
    LAUNCH_FAILED,
    LAUNCH_LAUNCHED,
    atomic_write_json,
    iso_utc,
    job_paths,
    job_record,
    write_job_record,
)
from ui.services.launch import TEST_HOOKS, launch_live_job
from ui.services.paths import DEMO_COMPARISON_DIR, KIND_CASE, KIND_COMPARISON
from ui.services.reconnect import apply_query_reconnect, parse_query_job_id
from ui.services.snapshot import build_snapshot, snapshot_digest
from ui.services.status import (
    CLASS_FAILED,
    CLASS_INCOMPLETE,
    CLASS_LAUNCH_FAILED,
    CLASS_QUEUED,
    CLASS_READY,
    CLASS_RUNNING,
    CLASS_UNEXPECTED,
    CLASS_VALIDATING,
    LAUNCH_GRACE_SECONDS,
    classify_job,
    last_complete_event,
    progress_from_event,
    reconcile_execution,
    tail_text,
    trusted_status,
)

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ui" / "app.py"
REPO_OUTPUTS = ROOT / "outputs"
DEMO_BEFORE = {
    str(path.relative_to(DEMO_COMPARISON_DIR)): (path.stat().st_size, path.stat().st_mtime_ns)
    for path in DEMO_COMPARISON_DIR.rglob("*")
    if path.is_file()
}


def _form_da() -> dict:
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    return form


def _snapshot_state() -> tuple[dict, dict]:
    form = _form_da()
    snapshot = build_snapshot(form, demo=False)
    state = default_state()
    store_snapshot(state, snapshot, snapshot["form_fingerprint"])
    state["form"] = form
    return state, snapshot


def _write_status(folder: Path, **fields) -> dict:
    payload = {
        "status_schema_version": 1,
        "run_id": fields.pop("run_id"),
        "state": fields.pop("state", "running"),
        "current_stage": fields.pop("current_stage", "solve"),
        "message": fields.pop("message", "working"),
        "started_at_utc": fields.pop("started_at_utc", "2026-09-05T12:00:00Z"),
        "updated_at_utc": fields.pop("updated_at_utc", "2026-09-05T12:00:05Z"),
        "completed_at_utc": fields.pop("completed_at_utc", None),
        "elapsed_seconds": fields.pop("elapsed_seconds", 5.0),
        "artifact_schema_version": fields.pop("artifact_schema_version", 1),
        "error_category": fields.pop("error_category", None),
        "error_message": fields.pop("error_message", None),
    }
    payload.update(fields)
    (folder / "run_status.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _planned_job(outputs_root: Path, snapshot: dict, *, job_id: str = "stepinbel-20260905T120000Z-abcd1234") -> dict:
    paths = job_paths(job_id, kind=KIND_CASE, outputs_root=outputs_root)
    paths["staging_directory"].mkdir(parents=True)
    atomic_write_json(paths["configured_snapshot_path"], snapshot)
    atomic_write_json(paths["request_path"], {"run_id": job_id})
    record = job_record(
        job_id=job_id,
        kind=KIND_CASE,
        markets=["da"],
        snapshot_fingerprint=snapshot_digest(snapshot),
        launch_state=LAUNCH_LAUNCHED,
        launch_utc=iso_utc(),
        paths=paths,
        pid=4242,
        outputs_root=outputs_root,
    )
    write_job_record(paths["job_path"], record, outputs_root=outputs_root)
    return record


def test_query_and_path_traversal_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        parse_query_job_id(["a", "b"])
    with pytest.raises(ValueError):
        parse_query_job_id(["../etc/passwd"])
    with pytest.raises(ValueError):
        parse_query_job_id(["stepinbel-20260905T120000Z-abcd1234/../x"])
    state = default_state()
    outcome = apply_query_reconnect(state, ["..", "stepinbel-20260905T120000Z-abcd1234"], outputs_root=tmp_path)
    assert outcome["ok"] is False
    assert state.get("job") is None


def test_browser_query_reloads_without_relaunch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state, snapshot = _snapshot_state()
    job_id = "stepinbel-20260905T120000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id=job_id)
    empty = default_state()
    called = []

    def bang(*_args, **_kwargs):
        called.append("popen")
        raise AssertionError("reconnect must not launch")

    monkeypatch.setattr("ui.services.launch.start_worker", bang)
    outcome = apply_query_reconnect(empty, [job_id], outputs_root=tmp_path)
    assert outcome["ok"] is True
    assert empty["job"]["job_id"] == job_id
    assert empty["snapshot"]["form_fingerprint"] == snapshot["form_fingerprint"]
    assert empty["form"]["market_da"] is True
    assert empty["demo"] is False
    require_json_compatible(empty)
    second = launch_live_job(empty, outputs_root=tmp_path, popen=bang)
    assert second.get("reconnect") is True
    assert called == []
    assert empty["job"]["snapshot_fingerprint"] == record["snapshot_fingerprint"]


def test_altered_fingerprint_and_mismatched_paths_rejected(tmp_path: Path) -> None:
    state, snapshot = _snapshot_state()
    job_id = "stepinbel-20260905T120000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id=job_id)
    paths = job_paths(job_id, kind=KIND_CASE, outputs_root=tmp_path)
    tampered = dict(snapshot)
    tampered["detailed_solver_output"] = not snapshot["detailed_solver_output"]
    atomic_write_json(paths["configured_snapshot_path"], tampered)
    empty = default_state()
    outcome = apply_query_reconnect(empty, [job_id], outputs_root=tmp_path)
    assert outcome["ok"] is False
    assert empty.get("job") is None
    atomic_write_json(paths["configured_snapshot_path"], snapshot)
    raw = json.loads(paths["job_path"].read_text(encoding="utf-8"))
    raw["output_directory"] = str(tmp_path / "elsewhere")
    paths["job_path"].write_text(json.dumps(raw), encoding="utf-8")
    outcome = apply_query_reconnect(default_state(), [job_id], outputs_root=tmp_path)
    assert outcome["ok"] is False


def test_queued_running_and_incomplete_jsonl(tmp_path: Path) -> None:
    now = datetime(2026, 9, 5, 12, 0, 2, tzinfo=timezone.utc)
    job = {
        "job_id": "stepinbel-20260905T120000Z-abcd1234",
        "kind": KIND_CASE,
        "launch_state": LAUNCH_LAUNCHED,
        "launch_utc": "2026-09-05T12:00:00Z",
        "pid": 99,
        "output_directory": str(tmp_path),
    }
    assert (
        classify_job(job, now=now, pid_alive=lambda pid: True, status=None) == CLASS_QUEUED
    )
    status = _write_status(tmp_path, run_id=job["job_id"], state="running", current_stage="solve")
    assert trusted_status(job, status) is not None
    assert classify_job(job, now=now, pid_alive=lambda pid: True, status=status) == CLASS_RUNNING
    events = tmp_path / "run_events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event_schema_version": 1,
                "run_id": job["job_id"],
                "stage_key": "solve",
                "stage_number": 4,
                "stage_total": 6,
                "state": "started",
            }
        )
        + "\n{\"event_schema_version\": 1, \"run_id\":",
        encoding="utf-8",
    )
    event = last_complete_event(tmp_path, run_id=job["job_id"])
    assert event is not None
    assert event["stage_key"] == "solve"
    assert progress_from_event(event) == (3, 6)
    (tmp_path / "run.log").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    assert tail_text(tmp_path / "run.log", max_lines=2) == "beta\ngamma"


def test_failed_stale_and_malformed_status_never_completed(tmp_path: Path) -> None:
    now = datetime(2026, 9, 5, 12, 0, 20, tzinfo=timezone.utc)
    job = {
        "job_id": "stepinbel-20260905T120000Z-abcd1234",
        "kind": KIND_CASE,
        "launch_state": LAUNCH_LAUNCHED,
        "launch_utc": "2026-09-05T12:00:00Z",
        "pid": 99,
        "output_directory": str(tmp_path),
    }
    failed = _write_status(tmp_path, run_id=job["job_id"], state="failed", error_message="HiGHS failed")
    assert classify_job(job, now=now, pid_alive=lambda pid: False, status=failed) == CLASS_FAILED
    stale = _write_status(
        tmp_path,
        run_id=job["job_id"],
        state="running",
        updated_at_utc="2026-09-05T12:00:01Z",
    )
    assert classify_job(job, now=now, pid_alive=lambda pid: False, status=stale) == CLASS_UNEXPECTED
    bad = dict(stale)
    bad["status_schema_version"] = 1.0
    assert trusted_status(job, bad) is None
    assert classify_job(job, now=now, pid_alive=lambda pid: True, status=bad) != CLASS_VALIDATING
    completed_wrong_id = _write_status(tmp_path, run_id="other", state="completed")
    assert trusted_status(job, completed_wrong_id) is None
    assert (
        classify_job(job, now=now, pid_alive=lambda pid: False, status=completed_wrong_id)
        != CLASS_VALIDATING
    )
    launch_failed = dict(job)
    launch_failed["launch_state"] = LAUNCH_FAILED
    assert classify_job(launch_failed, now=now, pid_alive=lambda pid: False, status=None) == CLASS_LAUNCH_FAILED
    assert LAUNCH_GRACE_SECONDS == 8.0


def test_completed_invalid_and_valid_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, snapshot = _snapshot_state()
    job_id = "stepinbel-20260905T120000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id=job_id)
    out = Path(record["output_directory"])
    out.mkdir()
    _write_status(out, run_id=job_id, state="completed", current_stage="verify_artifacts")
    state = default_state()
    state["job"] = record
    store_snapshot(state, snapshot, snapshot["form_fingerprint"])

    def boom(_path):
        from stepinbel.reporting import ArtifactError

        raise ArtifactError("incomplete")

    monkeypatch.setattr("ui.services.artifacts.validate_run_artifacts", boom)
    assert reconcile_execution(state, pid_alive=lambda pid: False, outputs_root=tmp_path) == CLASS_INCOMPLETE
    assert not result_is_valid(state.get("result"), outputs_root=tmp_path)

    class _Req:
        run_id = job_id
        output_directory = out
        config = type("C", (), {"market": "da"})()

    monkeypatch.setattr(
        "ui.services.artifacts.validate_run_artifacts",
        lambda _path: {"summary.json": out / "summary.json"},
    )
    monkeypatch.setattr("ui.services.artifacts.load_case_run_request", lambda _path: _Req())
    state["result"] = None
    assert reconcile_execution(state, pid_alive=lambda pid: False, outputs_root=tmp_path) == CLASS_READY
    assert result_is_valid(state["result"], job=state["job"], outputs_root=tmp_path)
    assert state["stage"] == STAGE_RESULTS


def test_valid_comparison_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    form = default_live_form()
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20260905T120000Z-ffff0000"
    paths = job_paths(job_id, kind=KIND_COMPARISON, outputs_root=tmp_path)
    paths["staging_directory"].mkdir(parents=True)
    atomic_write_json(paths["configured_snapshot_path"], snapshot)
    record = job_record(
        job_id=job_id,
        kind=KIND_COMPARISON,
        markets=["da", "mfrr", "afrr"],
        snapshot_fingerprint=snapshot_digest(snapshot),
        launch_state=LAUNCH_LAUNCHED,
        launch_utc=iso_utc(),
        paths=paths,
        pid=8,
        outputs_root=tmp_path,
    )
    write_job_record(paths["job_path"], record, outputs_root=tmp_path)
    paths["output_directory"].mkdir()
    _write_status(paths["output_directory"], run_id=job_id, state="completed")

    class _Cmp:
        run_id = job_id
        output_directory = paths["output_directory"]
        case_requests = {"da": None, "mfrr": None, "afrr": None}

    monkeypatch.setattr(
        "ui.services.artifacts.validate_market_comparison_artifacts",
        lambda _path: {"comparison_summary.json": paths["output_directory"] / "comparison_summary.json"},
    )
    monkeypatch.setattr("ui.services.artifacts.load_market_comparison_request", lambda _path: _Cmp())
    state = default_state()
    state["job"] = record
    assert reconcile_execution(state, pid_alive=lambda pid: False, outputs_root=tmp_path) == CLASS_READY
    assert state["result"]["kind"] == KIND_COMPARISON
    assert state["result"]["markets"] == ["da", "mfrr", "afrr"]
    assert result_is_valid(state["result"], job=record, outputs_root=tmp_path)


def test_return_to_review_preserves_inputs_and_audit(tmp_path: Path) -> None:
    state, snapshot = _snapshot_state()
    record = _planned_job(tmp_path, snapshot)
    state["job"] = record
    state["result"] = {"schema_version": 1, "validated": True}
    state["max_stage"] = STAGE_RESULTS
    paths = job_paths(record["job_id"], kind=KIND_CASE, outputs_root=tmp_path)
    return_to_review(state)
    assert state["job"] is None
    assert state["result"] is None
    assert state["stage"] == STAGE_REVIEW
    assert state["max_stage"] == STAGE_REVIEW
    assert state["snapshot"]["form_fingerprint"] == snapshot["form_fingerprint"]
    assert state["form"]["market_da"] is True
    assert paths["job_path"].is_file()
    assert paths["configured_snapshot_path"].is_file()
    assert paths["request_path"].is_file()


def test_demo_opens_committed_artifacts_without_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def bang(*_args, **_kwargs):
        called.append("called")
        raise AssertionError("demo must not build a request or launch")

    import stepinbel.workflows as workflows

    for name in (
        "build_case_run_request",
        "build_market_comparison_request",
        "serialize_case_run_request",
        "serialize_market_comparison_request",
        "execute_case_run",
        "execute_market_comparison",
    ):
        monkeypatch.setattr(workflows, name, bang)
    monkeypatch.setattr("ui.services.launch.start_worker", bang)
    before = dict(DEMO_BEFORE)
    result = open_demo_artifacts()
    assert result_is_valid(result)
    assert result["source"] == "demo"
    assert result["markets"] == ["da", "mfrr", "afrr"]
    assert called == []
    after = {
        str(path.relative_to(DEMO_COMPARISON_DIR)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in DEMO_COMPARISON_DIR.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (REPO_OUTPUTS / "demo-2025-all-markets-pv500").exists()


def test_results_cannot_open_from_partial_output(tmp_path: Path) -> None:
    _, snapshot = _snapshot_state()
    record = _planned_job(tmp_path, snapshot)
    Path(record["output_directory"]).mkdir()
    _write_status(Path(record["output_directory"]), run_id=record["job_id"], state="running")
    with pytest.raises(ValueError):
        accept_live_artifacts(record)
    assert not result_is_valid(None)


def test_app_demo_opens_results_and_working_locks_navigation() -> None:
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.run()
    next(item for item in at.checkbox if item.label == "Demo mode").set_value(True)
    at.run()
    next(item for item in at.button if item.label == "Continue").click()
    at.run()
    next(item for item in at.button if item.label == "View demonstration results").click()
    at.run()
    assert not at.exception
    text = " ".join(str(item.value) for item in at.header) + " ".join(
        str(item.value) for item in at.success
    )
    html = " ".join(str(item.proto.body) for item in at.get("html"))
    combined = text + " " + html
    assert "Results" in [item.value for item in at.header]
    assert "Results ready" not in combined
    assert "EUR 254,183.10" in combined
    assert "Saved demonstration" in combined
    assert "Demo" in combined
    assert "Cancel" not in [item.label for item in at.button]
    assert at.session_state["sib"]["stage"] == STAGE_RESULTS

    at = AppTest.from_file(str(APP), default_timeout=40)
    at.run()
    _, snapshot = _snapshot_state()
    job_id = "stepinbel-20260905T120000Z-abcd1234"
    state = default_state()
    store_snapshot(state, snapshot, snapshot["form_fingerprint"])
    state["form"] = snapshot["form"]
    state["job"] = {
        "schema_version": JOB_RECORD_VERSION,
        "job_id": job_id,
        "kind": KIND_CASE,
        "markets": ["da"],
        "snapshot_fingerprint": snapshot_digest(snapshot),
        "launch_state": LAUNCH_LAUNCHED,
        "launch_utc": iso_utc(),
        "output_directory": str(ROOT / "does-not-exist-output"),
        "staging_directory": str(ROOT / "does-not-exist-staging"),
        "configured_snapshot_path": str(ROOT / "does-not-exist-snap"),
        "request_path": str(ROOT / "does-not-exist-req"),
        "worker_console_path": str(ROOT / "does-not-exist-console"),
        "pid": 4242,
    }
    at.session_state["sib"] = state
    at.run()
    assert not at.exception
    labels = [item.label for item in at.button]
    assert "Cancel" not in labels
    assert next(item for item in at.button if item.label == "1  Configure").disabled is True
    assert next(item for item in at.button if item.label == "3  Results").disabled is True
    combined = " ".join(str(item.value) for item in list(at.warning) + list(at.header) + list(at.caption) + list(at.error))
    assert (
        "Queued" in combined
        or "Running" in combined
        or "worker ended" in combined.lower()
        or "could not be started" in combined.lower()
        or "not valid" in combined.lower()
        or "Simulation" in combined
    )
