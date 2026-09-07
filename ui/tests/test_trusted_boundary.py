from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ui.flow import default_state, store_snapshot, unlock_results
from ui.services.artifacts import (
    ERROR_DEMO,
    accept_live_artifacts,
    open_demo_artifacts,
    result_is_valid,
)
from ui.services.form import default_live_form
from ui.services.jobs import (
    ERROR_UNTRUSTED,
    LAUNCH_LAUNCHED,
    LAUNCH_PLANNED,
    atomic_write_json,
    iso_utc,
    job_paths,
    job_record,
    write_job_record,
)
from ui.services.launch import TEST_HOOKS, launch_live_job, prepare_live_job
from ui.services.paths import DEMO_COMPARISON_DIR, DEMO_IDENTITY, KIND_CASE, KIND_COMPARISON
from ui.services.reconnect import apply_query_reconnect
from ui.services.snapshot import build_snapshot, snapshot_digest
from ui.services.status import (
    CLASS_READY,
    CLASS_UNTRUSTED,
    classify_job,
    diagnostic_paths,
    friendly_stage_label,
    last_complete_event,
    reconcile_execution,
    trusted_status,
)

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
REPO_OUTPUTS = ROOT / "outputs"


class FakePopen:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)

        class _Proc:
            def __init__(self, pid: int) -> None:
                self.pid = pid

        return _Proc(self.pid)


def _snapshot_state(*markets: str) -> tuple[dict, dict]:
    form = default_live_form()
    form["market_da"] = "da" in markets or not markets
    form["market_mfrr"] = "mfrr" in markets
    form["market_afrr"] = "afrr" in markets
    if not markets:
        form["market_mfrr"] = False
        form["market_afrr"] = False
    snapshot = build_snapshot(form, demo=False)
    state = default_state()
    store_snapshot(state, snapshot, snapshot["form_fingerprint"])
    state["form"] = form
    return state, snapshot


def _write_job(
    outputs_root: Path,
    snapshot: dict,
    *,
    job_id: str = "stepinbel-20260905T120000Z-abcd1234",
    kind: str = KIND_CASE,
    markets: list[str] | None = None,
    launch_state: str = LAUNCH_LAUNCHED,
    pid: int | None = 4242,
) -> dict:
    paths = job_paths(job_id, kind=kind, outputs_root=outputs_root)
    paths["staging_directory"].mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["configured_snapshot_path"], snapshot)
    atomic_write_json(paths["request_path"], {"run_id": job_id})
    record = job_record(
        job_id=job_id,
        kind=kind,
        markets=markets or (["da", "mfrr", "afrr"] if kind == KIND_COMPARISON else ["da"]),
        snapshot_fingerprint=snapshot_digest(snapshot),
        launch_state=launch_state,
        launch_utc=iso_utc(),
        paths=paths,
        pid=pid,
        outputs_root=outputs_root,
    )
    write_job_record(paths["job_path"], record, outputs_root=outputs_root)
    return record


def _fabricated_fcr() -> dict:
    return {
        "schema_version": 1,
        "source": "live",
        "kind": "case",
        "validated": True,
        "output_directory": "C:/does/not/exist",
        "markets": ["fcr"],
    }


def test_fabricated_fcr_result_cannot_unlock_results(tmp_path: Path) -> None:
    fabricated = _fabricated_fcr()
    assert result_is_valid(fabricated) is False
    assert result_is_valid(fabricated, outputs_root=tmp_path) is False
    state = default_state()
    unlock_results(state, fabricated)
    assert result_is_valid(state.get("result"), outputs_root=tmp_path) is False
    assert reconcile_execution(state, outputs_root=tmp_path) != CLASS_READY


def test_wrong_result_shapes_and_identities_fail(tmp_path: Path) -> None:
    state, snapshot = _snapshot_state()
    record = _write_job(tmp_path, snapshot)
    Path(record["output_directory"]).mkdir(exist_ok=True)
    valid_period = {"start_date": "2025-01-01", "end_date": "2025-01-01"}
    base = {
        "schema_version": 1,
        "source": "live",
        "kind": "case",
        "job_id": record["job_id"],
        "output_directory": record["output_directory"],
        "markets": ["da"],
        "period": valid_period,
        "validated": True,
    }
    assert result_is_valid(base, job=record, outputs_root=tmp_path) is True
    extras = dict(base)
    extras["note"] = "extra"
    assert result_is_valid(extras, job=record, outputs_root=tmp_path) is False
    missing = dict(base)
    missing.pop("period")
    assert result_is_valid(missing, job=record, outputs_root=tmp_path) is False
    unordered = dict(base)
    unordered["kind"] = "comparison"
    unordered["markets"] = ["mfrr", "da"]
    assert result_is_valid(unordered, job=record, outputs_root=tmp_path) is False
    one_of_three = dict(base)
    one_of_three["kind"] = "comparison"
    one_of_three["markets"] = ["da"]
    assert result_is_valid(one_of_three, job=record, outputs_root=tmp_path) is False
    four = dict(base)
    four["markets"] = ["da", "mfrr", "afrr", "da"]
    assert result_is_valid(four, job=record, outputs_root=tmp_path) is False
    wrong_kind = dict(base)
    wrong_kind["kind"] = "sweep"
    assert result_is_valid(wrong_kind, job=record, outputs_root=tmp_path) is False
    wrong_path = dict(base)
    wrong_path["output_directory"] = str(tmp_path / "elsewhere")
    assert result_is_valid(wrong_path, job=record, outputs_root=tmp_path) is False
    wrong_id = dict(base)
    wrong_id["job_id"] = "stepinbel-20260905T120000Z-ffffffff"
    assert result_is_valid(wrong_id, job=record, outputs_root=tmp_path) is False
    demo_wrong = {
        "schema_version": 1,
        "source": "demo",
        "kind": "comparison",
        "job_id": "not-the-demo",
        "output_directory": str(DEMO_COMPARISON_DIR.resolve()),
        "markets": ["da", "mfrr", "afrr"],
        "period": valid_period,
        "validated": True,
    }
    assert result_is_valid(demo_wrong) is False


def test_readme_diagnostic_paths_are_rejected_without_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, snapshot = _snapshot_state()
    record = _write_job(tmp_path, snapshot)
    altered = dict(record)
    altered["worker_console_path"] = str(README)
    altered["request_path"] = str(README)
    opened: list[Path] = []
    original_open = Path.open

    def spy(self, *args, **kwargs):
        opened.append(Path(self).resolve())
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    found = diagnostic_paths(altered, outputs_root=tmp_path)
    assert found == {}
    assert README.resolve() not in opened


def test_same_id_query_does_not_bypass_altered_session_job(tmp_path: Path) -> None:
    _, snapshot = _snapshot_state()
    record = _write_job(tmp_path, snapshot)
    state = default_state()
    state["job"] = dict(record)
    state["job"]["worker_console_path"] = str(README)
    state["job"]["request_path"] = str(README)
    outcome = apply_query_reconnect(state, [record["job_id"]], outputs_root=tmp_path)
    assert outcome["ok"] is False
    assert outcome.get("reconnected") is False
    assert Path(state["job"]["worker_console_path"]).resolve() == README.resolve()


def test_status_reconciliation_never_reads_untrusted_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    status_path = outside / "run_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status_schema_version": 1,
                "run_id": "stepinbel-20260905T120000Z-abcd1234",
                "state": "completed",
                "current_stage": "verify_artifacts",
                "message": "done",
                "started_at_utc": "2026-09-05T12:00:00Z",
                "updated_at_utc": "2026-09-05T12:00:05Z",
                "completed_at_utc": "2026-09-05T12:00:05Z",
                "elapsed_seconds": 5.0,
                "artifact_schema_version": 1,
                "error_category": None,
                "error_message": None,
            }
        ),
        encoding="utf-8",
    )
    state = default_state()
    state["job"] = {
        "schema_version": 1,
        "job_id": "stepinbel-20260905T120000Z-abcd1234",
        "kind": KIND_CASE,
        "markets": ["da"],
        "snapshot_fingerprint": "x",
        "launch_state": LAUNCH_LAUNCHED,
        "launch_utc": "2026-09-05T12:00:00Z",
        "output_directory": str(outside),
        "staging_directory": str(outside),
        "configured_snapshot_path": str(outside / "configured_snapshot.json"),
        "request_path": str(outside / "case_request.json"),
        "worker_console_path": str(outside / "worker_console.log"),
        "pid": 9,
    }
    opened: list[Path] = []
    original_open = Path.open

    def spy(self, *args, **kwargs):
        opened.append(Path(self).resolve())
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    assert reconcile_execution(state, pid_alive=lambda pid: False, outputs_root=tmp_path) == CLASS_UNTRUSTED
    assert status_path.resolve() not in opened
    assert classify_job(state["job"], outputs_root=tmp_path) == CLASS_UNTRUSTED


def test_recorded_launch_order_persists_before_popen(tmp_path: Path) -> None:
    outputs_root = tmp_path / "outputs"
    outputs_root.mkdir()
    state, _ = _snapshot_state()
    worker = FakePopen()
    ops: list[str] = []
    TEST_HOOKS.clear()
    TEST_HOOKS["operations"] = ops

    def on_planned(outcome):
        assert state["job"]["launch_state"] == LAUNCH_PLANNED
        assert outcome["query_job"] == state["job"]["job_id"]
        ops.append("set_query")
        ops.append("persist")

    try:
        outcome = launch_live_job(
            state,
            outputs_root=outputs_root,
            popen=worker,
            now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            job_id="stepinbel-20260102T030405Z-deadbeef",
            on_planned=on_planned,
        )
    finally:
        TEST_HOOKS.clear()
    assert outcome["ok"] is True
    assert len(worker.calls) == 1
    required = (
        "validate_snapshot",
        "derive_paths",
        "write_snapshot",
        "write_request",
        "write_planned",
        "store_session",
        "on_planned",
        "set_query",
        "persist",
        "popen",
    )
    for name in required:
        assert name in ops, ops
    for earlier, later in (
        ("write_planned", "popen"),
        ("store_session", "popen"),
        ("on_planned", "popen"),
        ("set_query", "popen"),
        ("persist", "popen"),
        ("write_planned", "set_query"),
        ("store_session", "set_query"),
    ):
        assert ops.index(earlier) < ops.index(later), ops


def test_rerun_after_planned_never_calls_popen(tmp_path: Path) -> None:
    outputs_root = tmp_path / "outputs"
    outputs_root.mkdir()
    state, _ = _snapshot_state()
    worker = FakePopen()
    prepared = prepare_live_job(
        state,
        outputs_root=outputs_root,
        popen=worker,
        job_id="stepinbel-20260102T030405Z-feedf00d",
    )
    assert prepared["ready_to_start"] is True
    assert prepared["popen_called"] is False
    assert state["job"]["launch_state"] == LAUNCH_PLANNED
    assert worker.calls == []
    second = launch_live_job(state, outputs_root=outputs_root, popen=worker)
    assert second.get("reconnect") is True
    assert second["popen_called"] is False
    assert worker.calls == []
    third = prepare_live_job(state, outputs_root=outputs_root, popen=worker)
    assert third.get("reconnect") is True
    assert third["popen_called"] is False
    assert worker.calls == []


def test_double_click_still_calls_popen_at_most_once(tmp_path: Path) -> None:
    outputs_root = tmp_path / "outputs"
    outputs_root.mkdir()
    state, _ = _snapshot_state()
    worker = FakePopen()
    first = launch_live_job(state, outputs_root=outputs_root, popen=worker)
    second = launch_live_job(state, outputs_root=outputs_root, popen=worker)
    assert first["popen_called"] is True
    assert second.get("reconnect") is True
    assert second["popen_called"] is False
    assert len(worker.calls) == 1


def test_malformed_public_request_becomes_safe_ui_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, snapshot = _snapshot_state()
    record = _write_job(tmp_path, snapshot)
    out = Path(record["output_directory"])
    out.mkdir()
    (out / "run_request.json").write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(
        "ui.services.artifacts.validate_run_artifacts",
        lambda _path: {"summary.json": out / "summary.json"},
    )
    with pytest.raises(ValueError, match="incomplete|incompatible|request|could not") as excinfo:
        accept_live_artifacts(record, outputs_root=tmp_path)
    assert excinfo.type is ValueError
    assert "Traceback" not in str(excinfo.value)

    def boom(_path):
        raise TypeError("unexpected payload")

    monkeypatch.setattr("ui.services.artifacts.validate_run_artifacts", boom)
    with pytest.raises(ValueError) as typed:
        accept_live_artifacts(record, outputs_root=tmp_path)
    assert typed.type is ValueError

    def demo_boom(_path):
        from stepinbel.workflows import RunRequestError

        raise RunRequestError("bad demo request")

    monkeypatch.setattr("ui.services.artifacts.validate_market_comparison_artifacts", demo_boom)
    with pytest.raises(ValueError, match=ERROR_DEMO):
        open_demo_artifacts()


def test_invalid_timestamps_and_impossible_event_ranges_rejected(tmp_path: Path) -> None:
    job = {"job_id": "stepinbel-20260905T120000Z-abcd1234", "kind": KIND_CASE}
    payload = {
        "status_schema_version": 1,
        "run_id": job["job_id"],
        "state": "running",
        "current_stage": "solve",
        "message": "working",
        "started_at_utc": "not-a-timestamp",
        "updated_at_utc": "2026-09-05T12:00:05Z",
        "completed_at_utc": None,
        "elapsed_seconds": 5.0,
        "artifact_schema_version": 1,
        "error_category": None,
        "error_message": None,
    }
    assert trusted_status(job, payload) is None
    payload["started_at_utc"] = "2026-09-05T12:00:00Z"
    payload["updated_at_utc"] = "yesterday"
    assert trusted_status(job, payload) is None
    events = tmp_path / "run_events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event_schema_version": 1,
                "run_id": job["job_id"],
                "stage_key": "solve",
                "stage_number": 0,
                "stage_total": 6,
                "state": "started",
            }
        )
        + "\n"
        + json.dumps(
            {
                "event_schema_version": 1,
                "run_id": job["job_id"],
                "stage_key": "solve",
                "stage_number": 9,
                "stage_total": 6,
                "state": "started",
            }
        )
        + "\n"
        + json.dumps(
            {
                "event_schema_version": 1,
                "run_id": job["job_id"],
                "stage_key": "solve",
                "stage_number": 4,
                "stage_total": 6,
                "state": "started",
                "event_time_utc": "not-utc",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert last_complete_event(tmp_path, run_id=job["job_id"]) is None


def test_unknown_stage_names_are_not_raw_ui_copy() -> None:
    assert friendly_stage_label("not_a_real_stage") == "Working"
    assert friendly_stage_label("internal_secret_stage") == "Working"
    assert friendly_stage_label("solve") == "Solving"
    assert "not_a_real_stage" not in friendly_stage_label("not_a_real_stage")


def test_valid_live_case_comparison_and_demo_still_unlock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    demo = open_demo_artifacts()
    assert result_is_valid(demo) is True
    assert demo["job_id"] == DEMO_IDENTITY
    assert demo["kind"] == KIND_COMPARISON
    assert demo["markets"] == ["da", "mfrr", "afrr"]
    assert Path(demo["output_directory"]).resolve() == DEMO_COMPARISON_DIR.resolve()

    state, snapshot = _snapshot_state()
    case = _write_job(tmp_path, snapshot)
    Path(case["output_directory"]).mkdir(exist_ok=True)

    class _Req:
        run_id = case["job_id"]
        output_directory = Path(case["output_directory"])
        config = type("C", (), {"market": "da"})()

    monkeypatch.setattr(
        "ui.services.artifacts.validate_run_artifacts",
        lambda _path: {"summary.json": Path(case["output_directory"]) / "summary.json"},
    )
    monkeypatch.setattr("ui.services.artifacts.load_case_run_request", lambda _path: _Req())
    accepted = accept_live_artifacts(case, outputs_root=tmp_path)
    assert result_is_valid(accepted, job=case, outputs_root=tmp_path) is True

    form = default_live_form()
    comparison_snapshot = build_snapshot(form, demo=False)
    cmp_id = "stepinbel-20260905T120000Z-ffff0000"
    comparison = _write_job(
        tmp_path,
        comparison_snapshot,
        job_id=cmp_id,
        kind=KIND_COMPARISON,
        markets=["da", "mfrr", "afrr"],
    )
    Path(comparison["output_directory"]).mkdir(exist_ok=True)

    class _Cmp:
        run_id = cmp_id
        output_directory = Path(comparison["output_directory"])
        case_requests = {"da": None, "mfrr": None, "afrr": None}

    monkeypatch.setattr(
        "ui.services.artifacts.validate_market_comparison_artifacts",
        lambda _path: {"comparison_summary.json": Path(comparison["output_directory"]) / "comparison.json"},
    )
    monkeypatch.setattr("ui.services.artifacts.load_market_comparison_request", lambda _path: _Cmp())
    accepted_cmp = accept_live_artifacts(comparison, outputs_root=tmp_path)
    assert result_is_valid(accepted_cmp, job=comparison, outputs_root=tmp_path) is True
    assert accepted_cmp["kind"] == KIND_COMPARISON
    assert not (REPO_OUTPUTS / cmp_id).exists()
    assert not (REPO_OUTPUTS / case["job_id"]).exists()


def test_untrusted_session_job_does_not_launch(tmp_path: Path) -> None:
    outputs_root = tmp_path / "outputs"
    outputs_root.mkdir()
    state, _ = _snapshot_state()
    state["job"] = {
        "job_id": "stepinbel-20260905T120000Z-abcd1234",
        "kind": KIND_CASE,
        "worker_console_path": str(README),
        "request_path": str(README),
        "launch_state": LAUNCH_PLANNED,
    }
    worker = FakePopen()
    outcome = launch_live_job(state, outputs_root=outputs_root, popen=worker)
    assert outcome["ok"] is False
    assert outcome["popen_called"] is False
    assert outcome["error"] == ERROR_UNTRUSTED
    assert worker.calls == []
