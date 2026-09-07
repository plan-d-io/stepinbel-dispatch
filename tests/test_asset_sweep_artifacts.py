from __future__ import annotations

import csv
import json
import math
import shutil
from pathlib import Path

import pytest

from stepinbel.reporting import ArtifactError, validate_asset_sweep_artifacts
from stepinbel.reporting.artifacts import write_artifact_manifest
from stepinbel.reporting.constants import ASSET_SWEEP_ROW_FIELDS
from stepinbel.reporting.io import csv_cell, sha256_file
from stepinbel.reporting.sweep_artifacts import (
    sweep_rows_from_child_summaries,
    write_asset_sweep_artifact_manifest,
)
from stepinbel.reporting.sweep_report import render_asset_sweep_report
from stepinbel.workflows import build_asset_sweep_request, execute_asset_sweep
from stepinbel.workflows.sweep_request import AssetSweepRow
from tests.workflow_helpers import da_config, two_asset_candidates, utc


@pytest.fixture(scope="module")
def completed_sweep(data_root: Path, tmp_path_factory):
    request = build_asset_sweep_request(
        da_config(),
        two_asset_candidates(),
        data_root,
        tmp_path_factory.mktemp("sweep-art") / "run",
        run_id="sweep-art-01",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    return execute_asset_sweep(request)


def _copy(source: Path, dest: Path) -> Path:
    shutil.copytree(source, dest)
    return dest


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _child_run_id(dest: Path, candidate_id: str) -> str:
    return json.loads((dest / "cases" / candidate_id / "run_request.json").read_text(encoding="utf-8"))["run_id"]


def _rebuild_child_manifest(dest: Path, candidate_id: str) -> None:
    child = dest / "cases" / candidate_id
    write_artifact_manifest(child, _child_run_id(dest, candidate_id))


def _rebuild_parent_after_children(dest: Path, request, candidate_ids=None) -> None:
    ids = list(request.candidate_order if candidate_ids is None else candidate_ids)
    payload = json.loads((dest / "asset_sweep_metadata.json").read_text(encoding="utf-8"))
    for candidate_id in ids:
        path = dest / "cases" / candidate_id / "artifact_manifest.json"
        payload["children"][candidate_id]["artifact_manifest_byte_size"] = path.stat().st_size
        payload["children"][candidate_id]["artifact_manifest_sha256"] = sha256_file(path)
    _write_json(dest / "asset_sweep_metadata.json", payload)
    write_asset_sweep_artifact_manifest(dest, request)


def _rehash_candidates(dest: Path, request, candidate_ids) -> None:
    for candidate_id in candidate_ids:
        _rebuild_child_manifest(dest, candidate_id)
    _rebuild_parent_after_children(dest, request, candidate_ids)


def _patch_child_metadata(dest: Path, candidate_id: str, mutator) -> None:
    path = dest / "cases" / candidate_id / "run_metadata.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutator(payload)
    _write_json(path, payload)


def _rewrite_parent_report(dest: Path, request, solver_name: str, solver_version: str) -> None:
    summary = json.loads((dest / "asset_sweep_summary.json").read_text(encoding="utf-8"))
    rows = tuple(AssetSweepRow(**item) for item in summary["rows"])
    start, end = request.case_requests[request.candidate_order[0]].config.period.to_utc_bounds()
    text = render_asset_sweep_report(
        request,
        rows,
        highest_revenue_candidate_id=summary["highest_revenue_candidate_id"],
        resolved_start_utc=start,
        resolved_end_exclusive_utc=end,
        solver_name=solver_name,
        solver_version=solver_version,
    )
    (dest / "report.txt").write_text(text, encoding="utf-8")


def _set_summary_csv_field(path: Path, field: str, value: object) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    index = rows[0].index(field)
    rows[1][index] = csv_cell(value)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(rows)


def _minimal_summary(total: float, **overrides) -> dict:
    payload = {
        "interval_count": 8,
        "duration_hours": 2.0,
        "energy_gross_eur": 1.0,
        "grid_charging_cost_eur": 0.0,
        "market_energy_net_eur": total,
        "capacity_revenue_eur": 0.0,
        "pv_revenue_eur": 0.0,
        "total_site_revenue_eur": total,
        "pumped_mwh": 1.0,
        "turbined_mwh": 1.0,
        "pv_available_mwh": 0.0,
        "pv_self_consumed_mwh": 0.0,
        "pv_exported_mwh": 0.0,
        "pv_curtailed_mwh": 0.0,
        "simultaneous_interval_count": 0,
        "simultaneous_overlap_mwh": 0.0,
        "diagnostics": {"simultaneous_interval_energy_net_eur": 0.0},
    }
    payload.update(overrides)
    return payload


def test_relocation_succeeds(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "relocated")
    validate_asset_sweep_artifacts(dest)


def test_tie_uses_frozen_candidate_order(data_root: Path, tmp_path: Path) -> None:
    request = build_asset_sweep_request(
        da_config(),
        two_asset_candidates(),
        data_root,
        tmp_path / "tie",
        run_id="sweep-tie",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    summaries = {"small": _minimal_summary(10.0), "large": _minimal_summary(10.0)}
    rows, highest = sweep_rows_from_child_summaries(request, summaries)
    assert highest == "small"
    assert [row.candidate_id for row in rows] == ["small", "large"]
    assert [row.revenue_rank for row in rows] == [1, 2]
    assert all(row.difference_from_highest_eur == 0.0 for row in rows)


def test_json_csv_headers_and_rank_order(completed_sweep) -> None:
    directory = completed_sweep.directory
    summary = json.loads((directory / "asset_sweep_summary.json").read_text(encoding="utf-8"))
    headers, *rows = (directory / "asset_sweep_summary.csv").read_text(encoding="utf-8").splitlines()
    assert tuple(headers.split(",")) == ASSET_SWEEP_ROW_FIELDS
    assert [item["candidate_id"] for item in summary["rows"]] == [row.split(",")[0] for row in rows]
    validate_asset_sweep_artifacts(directory)


@pytest.mark.parametrize("value", (True, 1.0, 1.5))
def test_request_schema_corruption_fails(completed_sweep, tmp_path: Path, value) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / f"schema-{value}")
    payload = json.loads((dest / "asset_sweep_request.json").read_text(encoding="utf-8"))
    payload["asset_sweep_request_schema_version"] = value
    _write_json(dest / "asset_sweep_request.json", payload)
    write_asset_sweep_artifact_manifest(dest, completed_sweep.request)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(dest)


def test_child_summary_and_request_corruption_fail(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "child-sum")
    child = dest / "cases" / "small"
    payload = json.loads((child / "summary.json").read_text(encoding="utf-8"))
    payload["total_site_revenue_eur"] = float(payload["total_site_revenue_eur"]) + 99.0
    _write_json(child / "summary.json", payload)
    _set_summary_csv_field(child / "summary.csv", "total_site_revenue_eur", payload["total_site_revenue_eur"])
    _rehash_candidates(dest, completed_sweep.request, ["small"])
    with pytest.raises(ArtifactError, match="does not reconcile|does not match dispatch") as excinfo:
        validate_asset_sweep_artifacts(dest)
    assert "hash does not match" not in str(excinfo.value)

    dest = _copy(completed_sweep.directory, tmp_path / "child-req")
    payload = json.loads((dest / "cases" / "small" / "run_request.json").read_text(encoding="utf-8"))
    payload["run_id"] = "tampered"
    _write_json(dest / "cases" / "small" / "run_request.json", payload)
    write_asset_sweep_artifact_manifest(dest, completed_sweep.request)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(dest)


def test_second_child_solver_rehash_is_rejected(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "second-solver")
    second = completed_sweep.request.candidate_order[1]

    def mutate(payload: dict) -> None:
        payload["solver"]["solver_name"] = "NotHiGHS"
        payload["solver"]["solver_version"] = "999.0"

    _patch_child_metadata(dest, second, mutate)
    _rehash_candidates(dest, completed_sweep.request, [second])
    with pytest.raises(ArtifactError, match="solver_name must be HiGHS"):
        validate_asset_sweep_artifacts(dest)


def test_consistent_non_highs_solver_rehash_is_rejected(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "all-solver")

    def mutate(payload: dict) -> None:
        payload["solver"]["solver_name"] = "NotHiGHS"
        payload["solver"]["solver_version"] = "999.0"

    for candidate_id in completed_sweep.request.candidate_order:
        _patch_child_metadata(dest, candidate_id, mutate)
    _rewrite_parent_report(dest, completed_sweep.request, "NotHiGHS", "999.0")
    _rehash_candidates(dest, completed_sweep.request, completed_sweep.request.candidate_order)
    with pytest.raises(ArtifactError, match="solver_name must be HiGHS"):
        validate_asset_sweep_artifacts(dest)


def test_child_solver_options_rehash_is_rejected(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "solver-options")
    second = completed_sweep.request.candidate_order[1]

    def mutate(payload: dict) -> None:
        payload["solver"]["options"]["random_seed"] = 0.0

    _patch_child_metadata(dest, second, mutate)
    _rehash_candidates(dest, completed_sweep.request, [second])
    with pytest.raises(ArtifactError, match="solver options"):
        validate_asset_sweep_artifacts(dest)


def test_child_python_version_rehash_is_rejected(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "py-version")
    second = completed_sweep.request.candidate_order[1]

    def mutate(payload: dict) -> None:
        payload["python_version"] = "0.0.0"

    _patch_child_metadata(dest, second, mutate)
    _rehash_candidates(dest, completed_sweep.request, [second])
    with pytest.raises(ArtifactError, match="python_version values disagree"):
        validate_asset_sweep_artifacts(dest)


def test_child_software_version_rehash_is_rejected(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "sw-version")
    second = completed_sweep.request.candidate_order[1]

    def mutate(payload: dict) -> None:
        payload["software_version"] = str(payload["software_version"]) + "-x"

    _patch_child_metadata(dest, second, mutate)
    _rehash_candidates(dest, completed_sweep.request, [second])
    with pytest.raises(ArtifactError, match="software_version does not match the frozen sweep request"):
        validate_asset_sweep_artifacts(dest)


def test_untampered_rehash_chain_and_relocation_validate(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "rehash-ok")
    _rehash_candidates(dest, completed_sweep.request, completed_sweep.request.candidate_order)
    validate_asset_sweep_artifacts(dest)
    relocated = _copy(dest, tmp_path / "rehash-relocated")
    validate_asset_sweep_artifacts(relocated)


def test_missing_and_extra_candidate_directories_fail(completed_sweep, tmp_path: Path) -> None:
    missing = _copy(completed_sweep.directory, tmp_path / "missing")
    shutil.rmtree(missing / "cases" / "large")
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(missing)
    extra = _copy(completed_sweep.directory, tmp_path / "extra")
    (extra / "cases" / "bonus").mkdir()
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(extra)


def test_wrong_rank_highest_and_grid_mode_fail(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "rank")
    payload = json.loads((dest / "asset_sweep_summary.json").read_text(encoding="utf-8"))
    payload["highest_revenue_candidate_id"] = "large" if payload["highest_revenue_candidate_id"] != "large" else "small"
    payload["rows"][0]["revenue_rank"] = 2
    payload["rows"][1]["difference_from_highest_eur"] = 12.0
    _write_json(dest / "asset_sweep_summary.json", payload)
    write_asset_sweep_artifact_manifest(dest, completed_sweep.request)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(dest)

    dest = _copy(completed_sweep.directory, tmp_path / "grid")
    payload = json.loads((dest / "asset_sweep_summary.json").read_text(encoding="utf-8"))
    payload["grid_import_limit_mode"] = "fixed_site_limit"
    payload["rows"][0]["effective_grid_import_mw"] = 99.0
    _write_json(dest / "asset_sweep_summary.json", payload)
    write_asset_sweep_artifact_manifest(dest, completed_sweep.request)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(dest)


def test_wrong_numeric_types_and_extra_json_field_fail(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "types")
    payload = json.loads((dest / "asset_sweep_summary.json").read_text(encoding="utf-8"))
    payload["interval_count"] = str(payload["interval_count"])
    payload["rows"][0]["revenue_rank"] = True
    payload["rows"][0]["total_site_revenue_eur"] = "1.0"
    payload["note"] = "x"
    _write_json(dest / "asset_sweep_summary.json", payload)
    write_asset_sweep_artifact_manifest(dest, completed_sweep.request)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(dest)

    dest = _copy(completed_sweep.directory, tmp_path / "nan")
    payload = json.loads((dest / "asset_sweep_summary.json").read_text(encoding="utf-8"))
    payload["rows"][0]["full_cycles"] = math.nan
    text = json.dumps(payload, indent=2).replace("NaN", "NaN")
    (dest / "asset_sweep_summary.json").write_text(text + "\n", encoding="utf-8")
    write_asset_sweep_artifact_manifest(dest, completed_sweep.request)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(dest)


def test_csv_cell_and_reorder_fail(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "csv")
    path = dest / "asset_sweep_summary.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    rows[1].append("extra")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(rows)
    write_asset_sweep_artifact_manifest(dest, completed_sweep.request)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(dest)

    dest = _copy(completed_sweep.directory, tmp_path / "reorder")
    path = dest / "asset_sweep_summary.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    rows[1], rows[2] = rows[2], rows[1]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(rows)
    write_asset_sweep_artifact_manifest(dest, completed_sweep.request)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(dest)


def test_metadata_identity_and_report_fail(completed_sweep, tmp_path: Path) -> None:
    dest = _copy(completed_sweep.directory, tmp_path / "meta")
    payload = json.loads((dest / "asset_sweep_metadata.json").read_text(encoding="utf-8"))
    payload["software_version"] = payload["software_version"] + "-x"
    _write_json(dest / "asset_sweep_metadata.json", payload)
    write_asset_sweep_artifact_manifest(dest, completed_sweep.request)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(dest)

    dest = _copy(completed_sweep.directory, tmp_path / "report")
    (dest / "report.txt").write_text("tampered\n", encoding="utf-8")
    write_asset_sweep_artifact_manifest(dest, completed_sweep.request)
    with pytest.raises(ArtifactError, match="report.txt"):
        validate_asset_sweep_artifacts(dest)


def test_manifest_extra_missing_unsafe_and_types_fail(completed_sweep, tmp_path: Path) -> None:
    extra = _copy(completed_sweep.directory, tmp_path / "man-extra")
    manifest = json.loads((extra / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0]["note"] = "x"
    _write_json(extra / "artifact_manifest.json", manifest)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(extra)

    missing = _copy(completed_sweep.directory, tmp_path / "man-miss")
    manifest = json.loads((missing / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["entries"] = manifest["entries"][1:]
    _write_json(missing / "artifact_manifest.json", manifest)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(missing)

    unsafe = _copy(completed_sweep.directory, tmp_path / "man-unsafe")
    manifest = json.loads((unsafe / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0]["filename"] = "..\\secret.json"
    _write_json(unsafe / "artifact_manifest.json", manifest)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(unsafe)

    types = _copy(completed_sweep.directory, tmp_path / "man-types")
    manifest = json.loads((types / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["asset_sweep_artifact_schema_version"] = True
    manifest["entries"][0]["byte_size"] = str(manifest["entries"][0]["byte_size"])
    manifest["entries"][1]["sha256"] = str(manifest["entries"][1]["sha256"]).upper()
    _write_json(types / "artifact_manifest.json", manifest)
    with pytest.raises(ArtifactError):
        validate_asset_sweep_artifacts(types)
