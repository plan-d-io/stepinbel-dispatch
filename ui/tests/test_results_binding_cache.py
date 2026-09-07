"""Fingerprint-backed exact-binding receipts. No timing assertions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import ui.services.artifacts as artifacts
from ui.services.artifacts import (
    bind_exact_result_artifacts,
    binding_receipt_count,
    clear_binding_receipts,
    open_demo_artifacts,
    result_folder_display,
)
from ui.services.explorer_query import load_explorer_week
from ui.services.result_downloads import (
    DownloadsError,
    build_download_inventory,
    build_result_zip,
    read_inventory_file,
)
from ui.services.result_technical import TechnicalError, load_technical_details
from ui.tests.result_artifact_fixtures import (
    genuine_two_market_live,
    identified_relocated_one_case_live,
    relocated_demo_result,
)


@pytest.fixture(autouse=True)
def _clear_receipts() -> None:
    clear_binding_receipts()
    yield
    clear_binding_receipts()


@pytest.fixture
def validator_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    counts = {"run": 0, "comparison": 0, "record": 0}
    real_run = artifacts.validate_run_artifacts
    real_cmp = artifacts.validate_market_comparison_artifacts
    real_record = artifacts.validate_result_record

    def wrap_run(directory):
        counts["run"] += 1
        return real_run(directory)

    def wrap_cmp(directory):
        counts["comparison"] += 1
        return real_cmp(directory)

    def wrap_record(*args, **kwargs):
        counts["record"] += 1
        return real_record(*args, **kwargs)

    monkeypatch.setattr(artifacts, "validate_run_artifacts", wrap_run)
    monkeypatch.setattr(artifacts, "validate_market_comparison_artifacts", wrap_cmp)
    monkeypatch.setattr(artifacts, "validate_result_record", wrap_record)
    return counts


def _snapshot(counts: dict[str, int]) -> dict[str, int]:
    return dict(counts)


def test_copied_demo_tree_is_rejected_as_demo_result(tmp_path: Path) -> None:
    result = relocated_demo_result(tmp_path)
    demo = open_demo_artifacts()
    assert Path(result["output_directory"]).resolve() != Path(demo["output_directory"]).resolve()
    with pytest.raises(ValueError):
        artifacts.validate_result_record(result)
    with pytest.raises(ValueError):
        bind_exact_result_artifacts(result)


def test_first_technical_load_runs_semantic_validation(validator_calls: dict[str, int]) -> None:
    demo = open_demo_artifacts()
    start = _snapshot(validator_calls)
    load_technical_details(demo, market="da")
    assert validator_calls["comparison"] == start["comparison"] + 1
    assert validator_calls["run"] == start["run"] + 3
    assert validator_calls["record"] > start["record"]
    assert binding_receipt_count() == 1


def test_immediate_second_load_reuses_receipt(validator_calls: dict[str, int]) -> None:
    demo = open_demo_artifacts()
    load_technical_details(demo, market="da")
    after_first = _snapshot(validator_calls)
    load_technical_details(demo, market="da")
    assert validator_calls["run"] == after_first["run"]
    assert validator_calls["comparison"] == after_first["comparison"]
    assert validator_calls["record"] > after_first["record"]


def test_return_after_explorer_reuses_receipt(validator_calls: dict[str, int]) -> None:
    demo = open_demo_artifacts()
    load_technical_details(demo, market="da")
    after_first = _snapshot(validator_calls)
    load_explorer_week(demo, market="da", week_id="2025-W20")
    assert validator_calls["run"] == after_first["run"]
    assert validator_calls["comparison"] == after_first["comparison"]
    load_technical_details(demo, market="da")
    assert validator_calls["run"] == after_first["run"]
    assert validator_calls["comparison"] == after_first["comparison"]


def test_switching_technical_market_reuses_receipt(validator_calls: dict[str, int]) -> None:
    demo = open_demo_artifacts()
    load_technical_details(demo, market="da")
    after_first = _snapshot(validator_calls)
    load_technical_details(demo, market="afrr")
    load_technical_details(demo, market="mfrr")
    assert validator_calls["run"] == after_first["run"]
    assert validator_calls["comparison"] == after_first["comparison"]


def test_downloads_after_technical_reuses_receipt(validator_calls: dict[str, int]) -> None:
    demo = open_demo_artifacts()
    load_technical_details(demo, market="da")
    after_first = _snapshot(validator_calls)
    items = build_download_inventory(demo)
    assert len(items) == 63
    assert validator_calls["run"] == after_first["run"]
    assert validator_calls["comparison"] == after_first["comparison"]


def test_deferred_file_and_zip_reuse_receipt(validator_calls: dict[str, int]) -> None:
    demo = open_demo_artifacts()
    load_technical_details(demo, market="da")
    after_first = _snapshot(validator_calls)
    payload = read_inventory_file(demo, "comparison_summary.csv")
    assert payload
    assert validator_calls["run"] == after_first["run"]
    assert validator_calls["comparison"] == after_first["comparison"]
    archive = build_result_zip(demo)
    assert archive[:2] == b"PK"
    assert validator_calls["run"] == after_first["run"]
    assert validator_calls["comparison"] == after_first["comparison"]


def test_changed_artifact_forces_revalidation_and_rejection(
    tmp_path: Path, validator_calls: dict[str, int]
) -> None:
    record, result = identified_relocated_one_case_live(tmp_path)
    load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    after_first = _snapshot(validator_calls)
    path = Path(record["output_directory"]) / "summary.csv"
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(TechnicalError):
        load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    assert validator_calls["run"] > after_first["run"]


def test_same_size_mtime_restored_mutation_revalidates_and_rejects(
    tmp_path: Path, validator_calls: dict[str, int]
) -> None:
    record, result = identified_relocated_one_case_live(tmp_path)
    load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    after_first = _snapshot(validator_calls)
    path = Path(record["output_directory"]) / "summary.csv"
    stat = path.stat()
    original = path.read_bytes()
    mutated = bytes([original[0] ^ 0xFF]) + original[1:]
    assert len(mutated) == len(original)
    path.write_bytes(mutated)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(TechnicalError):
        load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    assert validator_calls["run"] > after_first["run"]


def test_added_unexpected_file_revalidates_and_rejects(
    tmp_path: Path, validator_calls: dict[str, int]
) -> None:
    record, result = identified_relocated_one_case_live(tmp_path)
    load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    after_first = _snapshot(validator_calls)
    (Path(record["output_directory"]) / "unexpected_extra.txt").write_text("nope", encoding="utf-8")
    with pytest.raises((TechnicalError, DownloadsError)):
        load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    assert validator_calls["run"] > after_first["run"]


def test_different_result_identity_validates_separately(
    tmp_path: Path, validator_calls: dict[str, int]
) -> None:
    demo = open_demo_artifacts()
    load_technical_details(demo, market="da")
    after_demo = _snapshot(validator_calls)
    record, result = genuine_two_market_live(tmp_path)
    load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    assert validator_calls["comparison"] == after_demo["comparison"] + 1
    assert validator_calls["run"] == after_demo["run"] + 2
    assert binding_receipt_count() == 2
    assert result_folder_display(result) == f"outputs/{record['job_id']}/"
    assert result_folder_display(demo) == "ui/demo_artifacts/stepinbel_2025_all_markets_pv500/"


def test_clear_binding_receipts_forces_complete_revalidation(validator_calls: dict[str, int]) -> None:
    demo = open_demo_artifacts()
    load_technical_details(demo, market="da")
    after_first = _snapshot(validator_calls)
    clear_binding_receipts()
    assert binding_receipt_count() == 0
    load_technical_details(demo, market="da")
    assert validator_calls["comparison"] == after_first["comparison"] + 1
    assert validator_calls["run"] == after_first["run"] + 3
