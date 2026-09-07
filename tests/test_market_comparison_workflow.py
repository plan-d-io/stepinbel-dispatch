from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from stepinbel.config import SiteConfig
from stepinbel.optimizer import ModelError
from stepinbel.reporting import validate_market_comparison_artifacts, validate_run_artifacts
from stepinbel.reporting.constants import COMPARISON_MARKETS
from stepinbel.workflows import (
    RunCancelledError,
    RunExecutionError,
    RunRequestError,
    execute_market_comparison,
)
from stepinbel.workflows.constants import COMPARISON_STAGES
from stepinbel.workflows.comparison_request import child_run_id
from tests.workflow_helpers import comparison_configs, utc


def _file_hashes(directory: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(directory.iterdir()):
        if path.is_file():
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _build(data_root: Path, output: Path, **kwargs):
    from stepinbel.workflows import build_market_comparison_request

    return build_market_comparison_request(
        comparison_configs(site=kwargs.get("site")),
        data_root,
        output,
        run_id=kwargs.get("run_id", "cmp-wf-01"),
        created_at_utc=kwargs.get("created_at", utc(2026, 1, 1, 12, 0)),
    )


def test_real_belgian_comparison_executes_all_markets(data_root: Path, tmp_path: Path) -> None:
    events = []

    def progress(event) -> None:
        events.append(event)

    request = _build(data_root, tmp_path / "all", run_id="cmp-all")
    run = execute_market_comparison(request, progress=progress)
    assert run.ok
    assert tuple(run.case_runs) == COMPARISON_MARKETS
    assert all(child.ok for child in run.case_runs.values())
    for market, child in run.case_runs.items():
        validate_run_artifacts(child.directory)
        assert child.request.config.market == market
    parent_started = [event.stage_key for event in events if event.run_id == request.run_id and event.state == "started"]
    assert parent_started == list(COMPARISON_STAGES)
    child_ids = {event.run_id for event in events}
    assert request.run_id in child_ids
    assert request.case_requests["da"].run_id in child_ids
    assert request.case_requests["mfrr"].run_id in child_ids
    assert request.case_requests["afrr"].run_id in child_ids
    artifacts = validate_market_comparison_artifacts(run.directory)
    assert set(run.artifacts) == set(artifacts)
    names = {path.name for path in run.directory.iterdir()}
    assert "da_prices_qh.parquet" not in names
    assert (run.directory / "cases" / "da").is_dir()


def test_pv_comparison_validates_total_site_accounting(data_root: Path, tmp_path: Path) -> None:
    request = _build(
        data_root,
        tmp_path / "pv",
        run_id="cmp-pv",
        site=SiteConfig(pv_ac_kw=80.0, pv_region="Belgium"),
    )
    run = execute_market_comparison(request)
    assert run.ok
    for row in run.rows:
        assert row.total_site_revenue_eur == (
            row.market_energy_net_eur + row.capacity_revenue_eur + row.pv_revenue_eur
        )
        assert row.difference_from_highest_eur <= 0.0
    assert run.rows[0].difference_from_highest_eur == 0.0
    assert [row.revenue_rank for row in run.rows] == [1, 2, 3]
    validate_market_comparison_artifacts(run.directory)


def test_existing_directory_refused_before_cancellation(data_root: Path, tmp_path: Path) -> None:
    existing = tmp_path / "exists"
    existing.mkdir()
    marker = existing / "keep.bin"
    marker.write_bytes(b"abc")
    request = _build(data_root, existing, run_id="cmp-exists")

    def cancel_requested() -> bool:
        raise AssertionError("cancellation must not be consulted")

    with pytest.raises(RunRequestError) as caught:
        execute_market_comparison(request, cancel_requested=cancel_requested)
    assert caught.value.category == "invalid_output"
    assert marker.read_bytes() == b"abc"


def test_immediate_cancellation_is_recorded(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "cancel", run_id="cmp-cancel")
    with pytest.raises(RunCancelledError):
        execute_market_comparison(request, cancel_requested=lambda: True)
    assert request.output_directory.is_dir()
    status = json.loads((request.output_directory / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "cancelled"
    assert not (request.output_directory / "cases" / "da").exists()
    assert not (request.output_directory / "artifact_manifest.json").exists()


def test_cancellation_during_child_stops_later_children(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "mid-cancel", run_id="cmp-mid")
    da_done = {"v": False}

    def progress(event) -> None:
        if event.stage_key == "execute_da" and event.state == "completed" and event.run_id == request.run_id:
            da_done["v"] = True

    def cancel_requested() -> bool:
        return da_done["v"]

    with pytest.raises(RunCancelledError):
        execute_market_comparison(request, progress=progress, cancel_requested=cancel_requested)
    assert (request.output_directory / "cases" / "da").is_dir()
    assert not (request.output_directory / "cases" / "mfrr").exists()
    assert not (request.output_directory / "cases" / "afrr").exists()
    status = json.loads((request.output_directory / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "cancelled"


def test_child_failure_preserves_diagnostics(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "fail", run_id="cmp-fail")
    count = {"n": 0}
    from stepinbel.optimizer import solve_case as real_solve

    def fake_solve(*args, **kwargs):
        count["n"] += 1
        if count["n"] == 2:
            raise ModelError("synthetic fail")
        return real_solve(*args, **kwargs)

    with patch("stepinbel.workflows.execute.solve_case", side_effect=fake_solve):
        with pytest.raises(RunExecutionError) as caught:
            execute_market_comparison(request)
    assert caught.value.category == "optimizer"
    assert (request.output_directory / "cases" / "da").is_dir()
    assert (request.output_directory / "cases" / "mfrr").is_dir()
    assert not (request.output_directory / "cases" / "afrr").exists()
    status = json.loads((request.output_directory / "run_status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert not (request.output_directory / "artifact_manifest.json").exists() or status["state"] == "failed"


def test_final_callback_can_validate_and_hashes_are_stable(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "cb", run_id="cmp-cb")
    seen = {"validated": False, "hashes": None}

    def progress(event) -> None:
        if event.stage_key == "verify_artifacts" and event.state == "completed" and event.run_id == request.run_id:
            validate_market_comparison_artifacts(request.output_directory)
            seen["validated"] = True
            seen["hashes"] = _file_hashes(request.output_directory)

    run = execute_market_comparison(request, progress=progress)
    assert seen["validated"] is True
    assert run.ok
    validate_market_comparison_artifacts(run.directory)
    assert _file_hashes(run.directory) == seen["hashes"]
    manifest = json.loads((run.directory / "artifact_manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        path = run.directory.joinpath(*str(entry["filename"]).split("/"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == entry["sha256"]


def _subset_build(data_root: Path, output: Path, markets: tuple[str, ...], **kwargs):
    from stepinbel.workflows import build_market_comparison_request

    return build_market_comparison_request(
        comparison_configs(markets=markets, site=kwargs.get("site")),
        data_root,
        output,
        run_id=kwargs.get("run_id", "cmp-sub-01"),
        created_at_utc=kwargs.get("created_at", utc(2026, 1, 1, 12, 0)),
    )


@pytest.mark.parametrize("markets", (("da", "mfrr"), ("mfrr", "afrr")))
def test_real_subset_comparison_executes_selected_only(
    data_root: Path, tmp_path: Path, markets: tuple[str, ...]
) -> None:
    from stepinbel.workflows.constants import comparison_stages

    events = []
    request = _subset_build(data_root, tmp_path / ("-".join(markets)), markets, run_id="cmp-" + "-".join(markets))
    run = execute_market_comparison(request, progress=events.append)
    assert run.ok
    assert tuple(run.case_runs) == markets
    omitted = set(COMPARISON_MARKETS) - set(markets)
    for market in omitted:
        assert market not in run.case_runs
        assert not (run.directory / "cases" / market).exists()
    assert {path.name for path in (run.directory / "cases").iterdir()} == set(markets)
    parent_started = [
        event.stage_key for event in events if event.run_id == request.run_id and event.state == "started"
    ]
    assert parent_started == list(comparison_stages(markets))
    child_ids = {event.run_id for event in events}
    assert request.run_id in child_ids
    for market in markets:
        assert request.case_requests[market].run_id in child_ids
        validate_run_artifacts(run.case_runs[market].directory)
    for market in omitted:
        assert all(event.run_id != child_run_id(request.run_id, market) for event in events)
    validate_market_comparison_artifacts(run.directory)
    names = {path.name for path in run.directory.iterdir()}
    assert "da_prices_qh.parquet" not in names


def test_pv_subset_reconciles_total_site(data_root: Path, tmp_path: Path) -> None:
    request = _subset_build(
        data_root,
        tmp_path / "pv-sub",
        ("da", "afrr"),
        run_id="cmp-pv-sub",
        site=SiteConfig(pv_ac_kw=80.0, pv_region="Belgium"),
    )
    run = execute_market_comparison(request)
    assert tuple(run.case_runs) == ("da", "afrr")
    assert [row.revenue_rank for row in run.rows] == [1, 2]
    for row in run.rows:
        assert row.total_site_revenue_eur == (
            row.market_energy_net_eur + row.capacity_revenue_eur + row.pv_revenue_eur
        )
    validate_market_comparison_artifacts(run.directory)


def test_subset_failure_skips_later_selected_child(data_root: Path, tmp_path: Path) -> None:
    request = _subset_build(data_root, tmp_path / "sub-fail", ("da", "afrr"), run_id="cmp-sub-fail")
    from stepinbel.optimizer import solve_case as real_solve

    def fake_solve(*args, **kwargs):
        raise ModelError("synthetic subset fail")

    with patch("stepinbel.workflows.execute.solve_case", side_effect=fake_solve):
        with pytest.raises(RunExecutionError) as caught:
            execute_market_comparison(request)
    assert caught.value.category == "optimizer"
    assert not (request.output_directory / "cases" / "afrr").exists()
    assert not (request.output_directory / "cases" / "mfrr").exists()


def test_child_progress_callback_failure_fails_parent(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path / "child-cb", run_id="cmp-child-cb")

    def progress(event) -> None:
        if event.run_id != request.run_id:
            raise RuntimeError("child callback exploded")

    with pytest.raises(RunExecutionError) as caught:
        execute_market_comparison(request, progress=progress)
    assert caught.value.category == "progress"
    da = request.output_directory / "cases" / "da"
    assert da.is_dir()
    assert (da / "run_status.json").is_file()
    assert (da / "run.log").is_file()
    assert (da / "run_events.jsonl").is_file()
    da_status = json.loads((da / "run_status.json").read_text(encoding="utf-8"))
    assert da_status["state"] == "failed"
    parent = json.loads((request.output_directory / "run_status.json").read_text(encoding="utf-8"))
    assert parent["state"] == "failed"
    assert parent["error_category"] == "progress"
    assert parent["current_stage"] == "execute_da"
    assert not (request.output_directory / "cases" / "mfrr").exists()
    assert not (request.output_directory / "cases" / "afrr").exists()
    assert not (request.output_directory / "artifact_manifest.json").exists()
