from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui.flow import STAGE_CONFIGURE, STAGE_RESULTS, default_state, store_snapshot, unlock_results
from ui.services.artifacts import result_is_valid
from ui.services.form import default_live_form
from ui.services.jobs import (
    LAUNCH_LAUNCHED,
    atomic_write_json,
    iso_utc,
    job_paths,
    job_record,
    write_job_record,
)
from ui.services.launch import TEST_HOOKS
from ui.services.paths import JOB_QUERY_KEY, KIND_CASE
from ui.services.snapshot import build_snapshot, snapshot_digest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ui" / "app.py"


def _form_da() -> dict:
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    return form


def _write_completed_job(outputs_root: Path, snapshot: dict, job_id: str) -> dict:
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
    out = paths["output_directory"]
    out.mkdir()
    (out / "run_status.json").write_text(
        '{"status_schema_version": 1, "run_id": "%s", "state": "completed",'
        ' "current_stage": "verify_artifacts", "message": "done",'
        ' "started_at_utc": "2026-09-05T12:00:00Z",'
        ' "updated_at_utc": "2026-09-05T12:00:20Z",'
        ' "completed_at_utc": "2026-09-05T12:00:20Z",'
        ' "elapsed_seconds": 20.0, "artifact_schema_version": 1,'
        ' "error_category": null, "error_message": null}' % job_id,
        encoding="utf-8",
    )
    return record


def _audit(paths: list[Path]) -> dict[str, tuple[int, int]]:
    return {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in paths
        if path.is_file()
    }


def _query_job(at: AppTest) -> list[str]:
    raw = at.query_params.get(JOB_QUERY_KEY)
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if str(item)]
    text = str(raw)
    return [text] if text else []


def test_completed_live_results_configure_starts_another_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    form = _form_da()
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20260906T204339Z-0a0bb5a7"
    record = _write_completed_job(tmp_path, snapshot, job_id)
    paths = job_paths(job_id, kind=KIND_CASE, outputs_root=tmp_path)
    before = _audit(
        [
            paths["job_path"],
            paths["configured_snapshot_path"],
            paths["request_path"],
            paths["output_directory"] / "run_status.json",
        ]
    )
    accept_calls = {"n": 0}

    class _Req:
        run_id = job_id
        output_directory = paths["output_directory"]
        config = type("C", (), {"market": "da"})()

    def _validate(_path):
        accept_calls["n"] += 1
        return {"summary.json": paths["output_directory"] / "summary.json"}

    monkeypatch.setattr("ui.services.artifacts.validate_run_artifacts", _validate)
    monkeypatch.setattr("ui.services.artifacts.load_case_run_request", lambda _path: _Req())

    popen_calls: list[str] = []

    def bang(**_kwargs):
        popen_calls.append("popen")
        raise AssertionError("Configure must not launch a worker")

    TEST_HOOKS.clear()
    TEST_HOOKS["outputs_root"] = tmp_path
    TEST_HOOKS["popen"] = bang
    try:
        state = default_state()
        store_snapshot(state, snapshot, snapshot["form_fingerprint"])
        state["form"] = form
        state["job"] = record
        unlock_results(
            state,
            {
                "schema_version": 1,
                "source": "live",
                "kind": "case",
                "job_id": job_id,
                "output_directory": record["output_directory"],
                "markets": ["da"],
                "period": {
                    "start_date": snapshot["period"]["start_date"],
                    "end_date": snapshot["period"]["end_date"],
                },
                "validated": True,
            },
        )
        assert result_is_valid(state["result"], job=record, outputs_root=tmp_path)

        at = AppTest.from_file(str(APP), default_timeout=60)
        at.run()
        at.session_state["sib"] = state
        at.query_params[JOB_QUERY_KEY] = job_id
        at.run()
        assert not at.exception
        assert at.session_state["sib"]["stage"] == STAGE_RESULTS
        assert "Results" in [item.value for item in at.header]
        accepts_on_results = accept_calls["n"]

        next(item for item in at.button if item.label == "1  Configure").click()
        at.run()
        assert not at.exception
        sib = at.session_state["sib"]
        assert sib["stage"] == STAGE_CONFIGURE
        assert sib["max_stage"] == STAGE_CONFIGURE
        assert sib["job"] is None
        assert sib["result"] is None
        assert sib["launch_error"] is None
        assert sib["form"]["market_da"] is True
        assert sib["form"]["market_mfrr"] is False
        assert sib["form"]["market_afrr"] is False
        assert _query_job(at) == []
        assert "PHS dispatch simulator" in [item.value for item in at.header]
        assert next(item for item in at.button if item.label == "3  Results").disabled is True
        assert next(item for item in at.checkbox if item.label == "Day-ahead").value is True
        assert next(item for item in at.checkbox if item.label == "mFRR").value is False
        assert next(item for item in at.checkbox if item.label == "aFRR").value is False
        assert accept_calls["n"] == accepts_on_results
        assert popen_calls == []
        assert _audit(
            [
                paths["job_path"],
                paths["configured_snapshot_path"],
                paths["request_path"],
                paths["output_directory"] / "run_status.json",
            ]
        ) == before
    finally:
        TEST_HOOKS.clear()


def test_demo_results_configure_returns_to_stage_one() -> None:
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.run()
    next(item for item in at.checkbox if item.label == "Demo mode").set_value(True)
    at.run()
    next(item for item in at.button if item.label == "Continue").click()
    at.run()
    next(item for item in at.button if item.label == "View demonstration results").click()
    at.run()
    assert not at.exception
    assert at.session_state["sib"]["stage"] == STAGE_RESULTS
    assert at.session_state["sib"]["result"]["source"] == "demo"

    next(item for item in at.button if item.label == "1  Configure").click()
    at.run()
    assert not at.exception
    sib = at.session_state["sib"]
    assert sib["stage"] == STAGE_CONFIGURE
    assert sib["max_stage"] == STAGE_CONFIGURE
    assert sib["job"] is None
    assert sib["result"] is None
    assert sib["demo"] is True
    assert _query_job(at) == []
    assert "PHS dispatch simulator" in [item.value for item in at.header]
    assert next(item for item in at.button if item.label == "3  Results").disabled is True
    assert next(item for item in at.checkbox if item.label == "Demo mode").value is True
    assert next(item for item in at.checkbox if item.label == "Day-ahead").value is True
    assert next(item for item in at.checkbox if item.label == "mFRR").value is True
    assert next(item for item in at.checkbox if item.label == "aFRR").value is True


def _foundation_preview() -> None:
    from ui.presentation.styles import inject_styles
    from ui.views.foundation_preview import render_foundation_preview

    inject_styles()
    render_foundation_preview()


@pytest.mark.skipif(
    not (ROOT / "ui" / "views" / "foundation_preview.py").is_file(),
    reason="foundation preview is not in the public tree",
)
def test_working_preview_uses_compact_status_summary() -> None:
    at = AppTest.from_function(_foundation_preview, default_timeout=30)
    at.run()
    at.radio[0].set_value("Review — running")
    at.run()
    assert not at.exception
    html = " ".join(str(item.proto.body) for item in at.get("html"))
    assert "sib-status-summary" in html
    assert "sib-status-label" in html
    assert "State" in html
    assert "Workflow stage" in html
    assert "Elapsed" in html
    metrics = getattr(at, "metric", [])
    assert list(metrics) == []
