from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from ui.flow import (
    STAGE_REVIEW,
    default_state,
    require_json_compatible,
    store_snapshot,
)
from ui.services.form import PRESET_CUSTOM, default_live_form
from ui.services.jobs import (
    LAUNCH_LAUNCHED,
    LAUNCH_PLANNED,
    iso_utc,
    job_paths,
    load_job_record,
    new_job_id,
)
from ui.services.launch import TEST_HOOKS, launch_live_job
from ui.services.paths import (
    CASE_REQUEST_NAME,
    COMPARISON_REQUEST_NAME,
    DATA_DIRECTORY,
    KIND_CASE,
    KIND_COMPARISON,
    REPO_ROOT,
    SRC_DIRECTORY,
)
from ui.services.process import worker_command, windows_creationflags
from ui.services.request import (
    build_public_request,
    prepare_launch_request,
    revalidate_snapshot,
    serialize_public_request,
)
from ui.services.snapshot import build_snapshot, snapshot_digest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ui" / "app.py"
REPO_OUTPUTS = ROOT / "outputs"


class FakePopen:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(pid=self.pid)


def _form(*markets: str, detailed: bool = False, end: str = "2025-01-01") -> dict:
    form = default_live_form()
    form["market_da"] = "da" in markets
    form["market_mfrr"] = "mfrr" in markets
    form["market_afrr"] = "afrr" in markets
    form["period_preset"] = PRESET_CUSTOM
    form["start_date"] = "2025-01-01"
    form["end_date"] = end
    form["detailed_solver"] = detailed
    return form


def _ready_state(*markets: str, detailed: bool = False) -> dict:
    form = _form(*markets, detailed=detailed)
    snapshot = build_snapshot(form, demo=False)
    state = default_state()
    store_snapshot(state, snapshot, snapshot["form_fingerprint"])
    state["form"] = form
    return state


def _repo_job_dirs() -> set[str]:
    if not REPO_OUTPUTS.exists():
        return set()
    return {path.name for path in REPO_OUTPUTS.iterdir() if path.name.startswith("stepinbel-")}


@pytest.fixture
def outputs_root(tmp_path: Path) -> Path:
    root = tmp_path / "outputs"
    root.mkdir()
    return root


def test_one_market_request_and_solver_flag(tmp_path: Path) -> None:
    snapshot = build_snapshot(_form("da", detailed=False), demo=False)
    rebuilt = revalidate_snapshot(snapshot, snapshot["form"], demo=False)
    assert rebuilt == snapshot
    request = build_public_request(
        snapshot,
        output_directory=tmp_path / "out-da",
        run_id="stepinbel-20250101T000000Z-abcd1234",
    )
    assert request.run_id == "stepinbel-20250101T000000Z-abcd1234"
    assert request.config.market == "da"
    assert request.solver_options.detailed_output is False
    assert request.data_directory.resolve() == DATA_DIRECTORY.resolve()
    payload = serialize_public_request(request)
    from stepinbel.workflows import case_run_request_from_payload, serialize_case_run_request

    assert serialize_case_run_request(case_run_request_from_payload(payload)) == payload


def test_two_and_three_market_requests_and_detailed_solver(tmp_path: Path) -> None:
    for markets in (("da", "mfrr"), ("da", "mfrr", "afrr")):
        snapshot = build_snapshot(_form(*markets, detailed=True), demo=False)
        request = build_public_request(
            snapshot,
            output_directory=tmp_path / f"out-{'-'.join(markets)}",
            run_id="stepinbel-20250101T000000Z-abcd1234",
        )
        assert list(request.case_requests) == list(markets)
        first = next(iter(request.case_requests.values()))
        assert first.solver_options.detailed_output is True
        payload = serialize_public_request(request)
        from stepinbel.workflows import (
            market_comparison_request_from_payload,
            serialize_market_comparison_request,
        )

        assert serialize_market_comparison_request(
            market_comparison_request_from_payload(payload)
        ) == payload


def test_snapshot_request_parity_and_prepare(tmp_path: Path) -> None:
    form = _form("da", "afrr")
    snapshot = build_snapshot(form, demo=False)
    kind, payload, request = prepare_launch_request(
        snapshot,
        form,
        demo=False,
        output_directory=tmp_path / "cmp",
        run_id="stepinbel-20250101T000000Z-abcd1234",
    )
    assert kind == KIND_COMPARISON
    assert payload["run_id"] == request.run_id == "stepinbel-20250101T000000Z-abcd1234"
    assert list(payload["case_requests"]) == ["da", "afrr"]


def test_planned_record_before_popen_and_output_absent(outputs_root: Path) -> None:
    state = _ready_state("da")
    worker = FakePopen()
    seen: dict[str, object] = {}

    def popen(**kwargs):
        job_id = state["job"]["job_id"]
        paths = job_paths(job_id, kind=KIND_CASE, outputs_root=outputs_root)
        record = json.loads(paths["job_path"].read_text(encoding="utf-8"))
        seen["launch_state"] = record["launch_state"]
        seen["pid"] = record["pid"]
        seen["output_exists"] = paths["output_directory"].exists()
        seen["request"] = paths["request_path"].name
        return worker(**kwargs)

    outcome = launch_live_job(
        state,
        outputs_root=outputs_root,
        popen=popen,
        now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        job_id="stepinbel-20260102T030405Z-deadbeef",
    )
    assert outcome["ok"] is True
    assert outcome["popen_called"] is True
    assert seen["launch_state"] == LAUNCH_PLANNED
    assert seen["pid"] is None
    assert seen["output_exists"] is False
    assert seen["request"] == CASE_REQUEST_NAME
    assert state["job"]["launch_state"] == LAUNCH_LAUNCHED
    assert state["job"]["pid"] == 4242
    require_json_compatible(state)
    assert not (outputs_root / "stepinbel-20260102T030405Z-deadbeef").exists()


def test_detached_command_cwd_env_and_flags(outputs_root: Path) -> None:
    state = _ready_state("da", "mfrr")
    worker = FakePopen()
    launch_live_job(state, outputs_root=outputs_root, popen=worker, cwd=REPO_ROOT)
    assert len(worker.calls) == 1
    call = worker.calls[0]
    request_path = Path(state["job"]["request_path"])
    assert call["args"] == worker_command(KIND_COMPARISON, request_path)
    assert call["args"][:5] == [sys.executable, "-u", "-m", "stepinbel", "compare"]
    assert call["args"][-1] == "--quiet"
    assert call["cwd"] == str(REPO_ROOT)
    assert call["shell"] is False
    assert call["stderr"] is subprocess.STDOUT
    assert call["env"]["PYTHONUNBUFFERED"] == "1"
    assert call["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(SRC_DIRECTORY.resolve())
    if sys.platform == "win32":
        assert call["creationflags"] == windows_creationflags()
    else:
        assert call["start_new_session"] is True
    assert request_path.name == COMPARISON_REQUEST_NAME
    handle = call["stdout"]
    assert handle.closed


def test_popen_once_and_double_click_does_not_relaunch(outputs_root: Path) -> None:
    state = _ready_state("da")
    worker = FakePopen()
    first = launch_live_job(state, outputs_root=outputs_root, popen=worker)
    second = launch_live_job(state, outputs_root=outputs_root, popen=worker)
    assert first["popen_called"] is True
    assert second.get("reconnect") is True
    assert second["popen_called"] is False
    assert len(worker.calls) == 1


def test_stored_planned_record_never_relaunches(outputs_root: Path) -> None:
    state = _ready_state("da")
    worker = FakePopen()
    writes: list[str] = []

    def writer(path, record, *, outputs_root=None):
        writes.append(str(record.get("launch_state")))
        if record.get("launch_state") == LAUNCH_LAUNCHED:
            raise OSError("pid write failed")
        from ui.services.jobs import write_job_record

        return write_job_record(path, record, outputs_root=outputs_root)

    first = launch_live_job(state, outputs_root=outputs_root, popen=worker, write_job=writer)
    assert first.get("pid_update_failed") is True
    assert state["job"]["launch_state"] == LAUNCH_PLANNED
    assert state["job"]["pid"] is None
    second = launch_live_job(state, outputs_root=outputs_root, popen=worker)
    assert second.get("reconnect") is True
    assert len(worker.calls) == 1


def test_launch_rejects_demo_and_does_not_touch_repo_outputs(outputs_root: Path) -> None:
    before = _repo_job_dirs()
    state = _ready_state("da")
    state["demo"] = True
    worker = FakePopen()
    outcome = launch_live_job(state, outputs_root=outputs_root, popen=worker)
    assert outcome["ok"] is False
    assert worker.calls == []
    assert _repo_job_dirs() == before


def test_new_job_id_is_path_safe() -> None:
    job_id = new_job_id(now=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc))
    assert job_id.startswith("stepinbel-20260905T120000Z-")
    assert len(job_id.split("-")[-1]) == 8


def test_app_review_run_uses_injected_worker(outputs_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = FakePopen()
    TEST_HOOKS.clear()
    TEST_HOOKS["outputs_root"] = outputs_root
    TEST_HOOKS["popen"] = worker
    TEST_HOOKS["now"] = datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    TEST_HOOKS["job_id"] = "stepinbel-20260203T040506Z-feedf00d"
    try:
        at = AppTest.from_file(str(APP), default_timeout=60)
        at.run()
        next(item for item in at.button if item.label == "Continue").click()
        at.run()
        next(item for item in at.button if item.label == "Run simulation").click()
        at.run()
        assert not at.exception
        assert len(worker.calls) == 1
        assert at.session_state["sib"]["job"]["job_id"] == "stepinbel-20260203T040506Z-feedf00d"
        assert "Cancel" not in [item.label for item in at.button]
        assert next(item for item in at.button if item.label == "1  Configure").disabled is True
        assert next(item for item in at.button if item.label == "3  Results").disabled is True
    finally:
        TEST_HOOKS.clear()
    assert _repo_job_dirs() == set() or not (
        REPO_OUTPUTS / "stepinbel-20260203T040506Z-feedf00d"
    ).exists()
