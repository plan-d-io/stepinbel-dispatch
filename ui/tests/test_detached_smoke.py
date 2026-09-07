from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ui.flow import default_state, store_snapshot
from ui.services.artifacts import accept_live_artifacts, result_is_valid
from ui.services.form import PRESET_CUSTOM, default_live_form
from ui.services.launch import launch_live_job
from ui.services.paths import REPO_ROOT
from ui.services.process import pid_is_alive
from ui.services.snapshot import build_snapshot
from ui.services.status import CLASS_READY, reconcile_execution

ROOT = Path(__file__).resolve().parents[2]
REPO_OUTPUTS = ROOT / "outputs"


def test_short_day_ahead_detached_smoke(tmp_path: Path) -> None:
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    form["period_preset"] = PRESET_CUSTOM
    form["start_date"] = "2025-01-01"
    form["end_date"] = "2025-01-01"
    snapshot = build_snapshot(form, demo=False)
    state = default_state()
    store_snapshot(state, snapshot, snapshot["form_fingerprint"])
    state["form"] = form
    outputs_root = tmp_path / "outputs"
    outputs_root.mkdir()
    job_id = "stepinbel-20260905T000000Z-aa01bb02"
    outcome = launch_live_job(
        state,
        outputs_root=outputs_root,
        cwd=REPO_ROOT,
        now=datetime(2026, 9, 5, 0, 0, 0, tzinfo=timezone.utc),
        job_id=job_id,
        allow_real_worker=True,
    )
    assert outcome["ok"] is True, outcome
    assert outcome["popen_called"] is True
    pid = state["job"]["pid"]
    assert type(pid) is int and pid > 0
    deadline = time.time() + 180
    klass = None
    while time.time() < deadline:
        klass = reconcile_execution(state, pid_alive=pid_is_alive, outputs_root=outputs_root)
        if klass in {CLASS_READY, "failed", "unexpected", "incomplete", "launch_failed"}:
            break
        time.sleep(1)
    assert klass == CLASS_READY, f"smoke ended as {klass}: {state.get('launch_error')}"
    result = accept_live_artifacts(state["job"], outputs_root=outputs_root)
    assert result_is_valid(result, job=state["job"], outputs_root=outputs_root)
    output = Path(state["job"]["output_directory"])
    assert output.is_dir()
    assert (output / "run_status.json").is_file()
    assert output.resolve().is_relative_to(outputs_root.resolve())
    assert "gurobipy" not in sys.modules
    console = Path(state["job"]["worker_console_path"]).read_text(encoding="utf-8", errors="replace")
    assert "gurobi" not in console.lower()
    if REPO_OUTPUTS.exists():
        assert not (REPO_OUTPUTS / job_id).exists()
        assert not (REPO_OUTPUTS / ".ui-jobs" / job_id).exists()
