from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from stepinbel.config import SiteConfig
from stepinbel.optimizer import ModelError
from stepinbel.reporting import validate_asset_sweep_artifacts, validate_run_artifacts
from stepinbel.workflows import (
    RunCancelledError,
    RunExecutionError,
    RunRequestError,
    execute_asset_sweep,
)
from stepinbel.workflows.sweep_request import sweep_stages
from tests.workflow_helpers import afrr_config, da_config, mfrr_config, two_asset_candidates, utc


def _file_hashes(directory: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(directory.iterdir()):
        if path.is_file():
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _build(data_root: Path, output: Path, **kwargs):
    from stepinbel.workflows import build_asset_sweep_request

    return build_asset_sweep_request(
        kwargs.get("config", da_config()),
        two_asset_candidates(),
        data_root,
        output,
        run_id=kwargs.get("run_id", "sweep-wf-01"),
        created_at_utc=kwargs.get("created_at", utc(2026, 1, 1, 12, 0)),
    )


def test_successful_da_sweep(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "da", run_id="sweep-da")
    run = execute_asset_sweep(request)
    assert run.ok
    assert tuple(run.case_runs) == request.candidate_order
    for child in run.case_runs.values():
        validate_run_artifacts(child.directory)
    validate_asset_sweep_artifacts(run.directory)
    assert [row.revenue_rank for row in run.rows] == [1, 2]
    for row in run.rows:
        assert row.total_site_revenue_eur == (
            row.market_energy_net_eur + row.capacity_revenue_eur + row.pv_revenue_eur
        )
        assert row.difference_from_highest_eur <= 0.0
    assert run.rows[0].difference_from_highest_eur == 0.0
    assert run.highest_revenue_candidate_id == run.rows[0].candidate_id
    assert sweep_stages(request.candidate_order) == (
        "validate_request",
        "validate_data",
        "execute_small",
        "execute_large",
        "aggregate",
        "verify_artifacts",
    )
    names = {path.name for path in run.directory.iterdir()}
    assert "dispatch.parquet" not in names
    assert (tmp_path / "da").is_dir()


def test_mfrr_and_afrr_routing(data_root: Path, tmp_path: Path) -> None:
    mfrr = execute_asset_sweep(_build(data_root, tmp_path / "mfrr", config=mfrr_config(), run_id="sweep-mfrr"))
    assert mfrr.request.market == "mfrr"
    assert all(child.request.config.market == "mfrr" for child in mfrr.case_runs.values())
    validate_asset_sweep_artifacts(mfrr.directory)
    afrr = execute_asset_sweep(_build(data_root, tmp_path / "afrr", config=afrr_config(), run_id="sweep-afrr"))
    assert afrr.request.market == "afrr"
    validate_asset_sweep_artifacts(afrr.directory)


def test_explicit_and_fallback_grid_modes(data_root: Path, tmp_path: Path) -> None:
    fallback = execute_asset_sweep(_build(data_root, tmp_path / "fb", run_id="sweep-fb"))
    assert fallback.rows[0].effective_grid_import_mw == fallback.rows[0].power_pump_mw
    assert fallback.rows[0].effective_grid_export_mw == fallback.rows[0].power_turbine_mw
    summary = json.loads((fallback.directory / "asset_sweep_summary.json").read_text(encoding="utf-8"))
    assert summary["grid_import_limit_mode"] == "candidate_pump_rating"
    assert summary["grid_export_limit_mode"] == "candidate_turbine_rating"
    fixed = execute_asset_sweep(
        _build(
            data_root,
            tmp_path / "fx",
            run_id="sweep-fx",
            config=da_config(site=SiteConfig(grid_import_mw=5.0, grid_export_mw=6.0)),
        )
    )
    assert all(row.effective_grid_import_mw == 5.0 for row in fixed.rows)
    assert all(row.effective_grid_export_mw == 6.0 for row in fixed.rows)
    summary = json.loads((fixed.directory / "asset_sweep_summary.json").read_text(encoding="utf-8"))
    assert summary["grid_import_limit_mode"] == "fixed_site_limit"
    assert summary["grid_export_limit_mode"] == "fixed_site_limit"


def test_existing_directory_refused_before_cancellation(data_root: Path, tmp_path: Path) -> None:
    existing = tmp_path / "exists"
    existing.mkdir()
    marker = existing / "keep.bin"
    marker.write_bytes(b"abc")
    request = _build(data_root, existing, run_id="sweep-exists")

    def cancel_requested() -> bool:
        raise AssertionError("cancellation must not be consulted")

    with pytest.raises(RunRequestError) as caught:
        execute_asset_sweep(request, cancel_requested=cancel_requested)
    assert caught.value.category == "invalid_output"
    assert marker.read_bytes() == b"abc"


def test_cancellation_before_a_candidate(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "cancel", run_id="sweep-cancel")
    with pytest.raises(RunCancelledError):
        execute_asset_sweep(request, cancel_requested=lambda: True)
    assert request.output_directory.is_dir()
    status = json.loads((request.output_directory / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "cancelled"
    assert not (request.output_directory / "cases" / "small").exists()
    assert not (request.output_directory / "artifact_manifest.json").exists()


def test_cancellation_after_a_child(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "mid-cancel", run_id="sweep-mid")
    first_done = {"v": False}

    def progress(event) -> None:
        if event.stage_key == "execute_small" and event.state == "completed" and event.run_id == request.run_id:
            first_done["v"] = True

    def cancel_requested() -> bool:
        return first_done["v"]

    with pytest.raises(RunCancelledError):
        execute_asset_sweep(request, progress=progress, cancel_requested=cancel_requested)
    assert (request.output_directory / "cases" / "small").is_dir()
    assert not (request.output_directory / "cases" / "large").exists()
    status = json.loads((request.output_directory / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "cancelled"
    assert not (request.output_directory / "artifact_manifest.json").exists()


def test_child_failure_stops_later_candidates(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "fail", run_id="sweep-fail")
    count = {"n": 0}
    from stepinbel.optimizer import solve_case as real_solve

    def fake_solve(*args, **kwargs):
        count["n"] += 1
        if count["n"] == 1:
            raise ModelError("synthetic fail")
        return real_solve(*args, **kwargs)

    with patch("stepinbel.workflows.execute.solve_case", side_effect=fake_solve):
        with pytest.raises(RunExecutionError) as caught:
            execute_asset_sweep(request)
    assert caught.value.category == "optimizer"
    assert (request.output_directory / "cases" / "small").is_dir()
    assert not (request.output_directory / "cases" / "large").exists()
    status = json.loads((request.output_directory / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert not (request.output_directory / "artifact_manifest.json").exists()


def test_child_progress_callback_failure_fails_parent(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "child-cb", run_id="sweep-child-cb")

    def progress(event) -> None:
        if event.run_id != request.run_id:
            raise RuntimeError("child callback exploded")

    with pytest.raises(RunExecutionError) as caught:
        execute_asset_sweep(request, progress=progress)
    assert caught.value.category == "progress"
    small = request.output_directory / "cases" / "small"
    assert small.is_dir()
    assert json.loads((small / "run_status.json").read_text(encoding="utf-8"))["state"] == "failed"
    parent = json.loads((request.output_directory / "run_status.json").read_text(encoding="utf-8"))
    assert parent["state"] == "failed"
    assert parent["error_category"] == "progress"
    assert parent["current_stage"] == "execute_small"
    assert not (request.output_directory / "cases" / "large").exists()
    assert not (request.output_directory / "artifact_manifest.json").exists()


def test_final_callback_can_validate_and_hashes_are_stable(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "cb", run_id="sweep-cb")
    seen = {"validated": False, "hashes": None}

    def progress(event) -> None:
        if event.stage_key == "verify_artifacts" and event.state == "completed" and event.run_id == request.run_id:
            validate_asset_sweep_artifacts(request.output_directory)
            seen["validated"] = True
            seen["hashes"] = _file_hashes(request.output_directory)

    run = execute_asset_sweep(request, progress=progress)
    assert seen["validated"] is True
    assert run.ok
    validate_asset_sweep_artifacts(run.directory)
    assert _file_hashes(run.directory) == seen["hashes"]
