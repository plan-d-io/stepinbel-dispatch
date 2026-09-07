from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from stepinbel.config import SiteConfig
from stepinbel.optimizer import ModelError
from stepinbel.reporting import validate_run_artifacts
from stepinbel.reporting.constants import PUBLISHED_TABLE_STEMS
from stepinbel.workflows import (
    RunCancelledError,
    RunError,
    RunExecutionError,
    RunRequestError,
    execute_case_run,
)
from stepinbel.workflows.constants import STAGES
from tests.workflow_helpers import afrr_config, build_request, da_config, mfrr_config


def _file_hashes(directory: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(directory.iterdir()):
        if path.is_file():
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _assert_successful_run(run, request) -> None:
    assert run.ok
    assert run.result.solver.status == "optimal"
    assert run.result.feasibility.ok is True
    assert run.result.manifest_sha256 == request.data_manifest_sha256
    assert run.status["state"] == "completed"
    artifacts = validate_run_artifacts(run.directory)
    assert set(artifacts) == set(run.artifacts)
    summary = run.result.summary
    assert summary.total_site_revenue_eur == (
        summary.market_energy_net_eur + summary.capacity_revenue_eur + summary.pv_revenue_eur
    )
    names = {path.name for path in run.directory.iterdir()}
    assert "da_prices_qh.parquet" not in names
    assert "balancing_qh.parquet" not in names
    assert "capacity_blocks.parquet" not in names
    assert "pv_profile_qh.parquet" not in names
    metadata = json.loads((run.directory / "run_metadata.json").read_text(encoding="utf-8"))
    assert set(metadata["published_data"]["tables"]) == set(PUBLISHED_TABLE_STEMS)


def _collect_progress():
    events = []

    def progress(event) -> None:
        events.append(event)

    return events, progress


def test_da_without_pv(data_root: Path, tmp_path: Path) -> None:
    events, progress = _collect_progress()
    request = build_request(data_root, tmp_path / "da", da_config(), run_id="wf-da")
    run = execute_case_run(request, progress=progress)
    _assert_successful_run(run, request)
    keys = [event.stage_key for event in events if event.state == "started"]
    assert keys == list(STAGES)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))


def test_da_with_pv(data_root: Path, tmp_path: Path) -> None:
    request = build_request(
        data_root,
        tmp_path / "da-pv",
        da_config(site=SiteConfig(pv_ac_kw=100.0, pv_region="Belgium")),
        run_id="wf-da-pv",
    )
    run = execute_case_run(request)
    _assert_successful_run(run, request)
    assert run.result.summary.pv_available_mwh >= 0.0


def test_mfrr_workflow(data_root: Path, tmp_path: Path) -> None:
    request = build_request(data_root, tmp_path / "mfrr", mfrr_config(), run_id="wf-mfrr")
    run = execute_case_run(request)
    _assert_successful_run(run, request)
    assert run.result.capacity_results.num_rows > 0


def test_afrr_workflow(data_root: Path, tmp_path: Path) -> None:
    request = build_request(data_root, tmp_path / "afrr", afrr_config(), run_id="wf-afrr")
    run = execute_case_run(request)
    _assert_successful_run(run, request)
    assert run.result.capacity_results.num_rows > 0


def test_failure_after_output_creation_is_diagnosable(data_root: Path, tmp_path: Path) -> None:
    request = build_request(data_root, tmp_path / "fail", da_config(), run_id="wf-fail")
    with patch("stepinbel.workflows.execute.solve_case", side_effect=ModelError("synthetic fail")):
        with pytest.raises(RunError) as caught:
            execute_case_run(request)
    assert isinstance(caught.value, RunExecutionError)
    assert caught.value.category == "optimizer"
    assert request.output_directory.is_dir()
    status = (request.output_directory / "run_status.json").read_text(encoding="utf-8")
    assert '"state": "failed"' in status
    assert "synthetic fail" in (request.output_directory / "run.log").read_text(encoding="utf-8")


def test_cancellation_is_recorded(data_root: Path, tmp_path: Path) -> None:
    request = build_request(data_root, tmp_path / "cancel", da_config(), run_id="wf-cancel")

    def cancel_requested() -> bool:
        return True

    with pytest.raises(RunCancelledError) as caught:
        execute_case_run(request, cancel_requested=cancel_requested)
    assert caught.value.category == "cancelled"
    assert request.output_directory.is_dir()
    status = json.loads((request.output_directory / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "cancelled"
    events = (request.output_directory / "run_events.jsonl").read_text(encoding="utf-8")
    log = (request.output_directory / "run.log").read_text(encoding="utf-8")
    assert "cancelled" in events
    assert "cancelled" in log


def test_existing_directory_refused_before_cancellation(data_root: Path, tmp_path: Path) -> None:
    existing = tmp_path / "exists-first"
    existing.mkdir()
    marker = existing / "keep.bin"
    marker.write_bytes(b"abc")
    request = build_request(data_root, existing, da_config(), run_id="wf-exists-cancel")

    def cancel_requested() -> bool:
        raise AssertionError("cancellation must not be consulted")

    with pytest.raises(RunRequestError) as caught:
        execute_case_run(request, cancel_requested=cancel_requested)
    assert caught.value.category == "invalid_output"
    assert marker.read_bytes() == b"abc"


def test_final_verify_callback_can_validate(data_root: Path, tmp_path: Path) -> None:
    request = build_request(data_root, tmp_path / "callback-validate", da_config(), run_id="wf-cb")
    seen = {"validated": False, "hashes": None}

    def progress(event) -> None:
        if event.stage_key == "verify_artifacts" and event.state == "completed":
            validate_run_artifacts(request.output_directory)
            seen["validated"] = True
            seen["hashes"] = _file_hashes(request.output_directory)

    run = execute_case_run(request, progress=progress)
    assert seen["validated"] is True
    assert run.ok
    validate_run_artifacts(run.directory)
    assert _file_hashes(run.directory) == seen["hashes"]
    manifest = json.loads((run.directory / "artifact_manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        digest = hashlib.sha256((run.directory / entry["filename"]).read_bytes()).hexdigest()
        assert digest == entry["sha256"]


def test_progress_callback_failure(data_root: Path, tmp_path: Path) -> None:
    request = build_request(data_root, tmp_path / "progress", da_config(), run_id="wf-progress")

    def progress(event) -> None:
        if event.sequence >= 2:
            raise RuntimeError("callback exploded")

    with pytest.raises(RunExecutionError) as caught:
        execute_case_run(request, progress=progress)
    assert caught.value.category == "progress"
    status = (request.output_directory / "run_status.json").read_text(encoding="utf-8")
    assert '"state": "failed"' in status


def test_existing_directory_not_modified(data_root: Path, tmp_path: Path) -> None:
    existing = tmp_path / "exists"
    existing.mkdir()
    marker = existing / "keep.bin"
    marker.write_bytes(b"abc")
    request = build_request(data_root, existing, da_config(), run_id="wf-exists")
    with pytest.raises(RunRequestError) as caught:
        execute_case_run(request)
    assert caught.value.category == "invalid_output"
    assert marker.read_bytes() == b"abc"
    assert sorted(path.name for path in existing.iterdir()) == ["keep.bin"]
