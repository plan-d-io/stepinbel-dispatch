from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from ui.flow import default_state, store_snapshot, unlock_results
from ui.presentation.tokens import (
    DOWNLOADS_ERROR_BODY,
    DOWNLOADS_PACKAGE_LABEL,
    DOWNLOADS_REPORT_LABEL,
    DOWNLOADS_STORED_INTRO,
    DOWNLOADS_SUMMARY_LABEL,
    RESERVED_TAB_BODY,
)
from ui.services.artifacts import DEMO_STORAGE_DISPLAY, open_demo_artifacts, result_folder_display
from ui.services.form import default_live_form
from ui.services.launch import TEST_HOOKS
from ui.services.paths import DEMO_COMPARISON_DIR, JOB_QUERY_KEY
from ui.services.result_downloads import (
    ZIP_SOURCE_SIZE_LIMIT_BYTES,
    DownloadsError,
    build_download_inventory,
    build_result_zip,
    download_filename,
    mime_type,
    quick_download_paths,
    read_inventory_file,
    require_relative_artifact_path,
    zip_download_filename,
    zip_is_within_limit,
    zip_members,
)
from ui.services.snapshot import build_snapshot
from ui.tests.result_artifact_fixtures import (
    copy_one_case_tree,
    genuine_two_market_live,
    identified_relocated_one_case_live,
    mismatched_one_case_live,
    mismatched_two_market_on_three_market_demo,
    rebind_one_case_run_id,
    relocated_demo_result,
)

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ui" / "app.py"


def _audit(paths: list[Path]) -> dict[str, tuple[int, int]]:
    return {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in paths
        if path.is_file()
    }


def _one_market_da(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return identified_relocated_one_case_live(tmp_path)


def _two_market(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return genuine_two_market_live(tmp_path)


def test_demo_inventory_has_exactly_63_files() -> None:
    result = open_demo_artifacts()
    items = build_download_inventory(result)
    assert result_folder_display(result) == DEMO_STORAGE_DISPLAY
    assert not Path(result_folder_display(result)).is_absolute()
    assert len(items) == 63
    paths = [item.relative_path for item in items]
    assert len(paths) == len(set(paths))
    assert all("/" == rel[rel.find("/") :].count("\\") or "\\" not in rel for rel in paths)
    for item in items:
        assert "\\" not in item.relative_path
        assert ".." not in item.relative_path
        assert not Path(item.relative_path).is_absolute()
        require_relative_artifact_path(DEMO_COMPARISON_DIR, item.relative_path)
    assert "artifact_manifest.json" in paths
    assert "cases/da/artifact_manifest.json" in paths
    assert "cases/afrr/dispatch.parquet" in paths
    scopes = {item.scope for item in items}
    assert scopes == {"overall", "da", "afrr", "mfrr"}


def test_one_case_and_two_market_inventory(tmp_path: Path) -> None:
    record, result = _one_market_da(tmp_path)
    items = build_download_inventory(result, job=record, outputs_root=tmp_path)
    assert len(items) == 18
    assert {item.scope for item in items} == {"da"}
    assert {item.relative_path for item in items} >= {"summary.csv", "report.txt", "artifact_manifest.json"}
    two_record, two_result = _two_market(tmp_path / "cmp")
    two = build_download_inventory(two_result, job=two_record, outputs_root=tmp_path / "cmp")
    assert {item.scope for item in two} == {"overall", "da", "afrr"}
    assert not any(item.relative_path.startswith("cases/mfrr/") for item in two)
    assert not (Path(two_record["output_directory"]) / "cases" / "mfrr").exists()
    assert len(two) == 45


def test_complete_zip_matches_inventory_and_source_bytes() -> None:
    result = open_demo_artifacts()
    items = build_download_inventory(result)
    payload = build_result_zip(result)
    members = zip_members(payload)
    folder = "demo-2025-all-markets-pv500"
    expected = {f"{folder}/{item.relative_path}" for item in items}
    assert set(members) == expected
    for item in items:
        source = (DEMO_COMPARISON_DIR / item.relative_path).read_bytes()
        assert members[f"{folder}/{item.relative_path}"] == source
        assert hashlib.sha256(source).hexdigest() == item.sha256


def test_quick_and_individual_bytes_mime_and_names() -> None:
    result = open_demo_artifacts()
    summary, report = quick_download_paths("comparison")
    summary_bytes = read_inventory_file(result, summary)
    report_bytes = read_inventory_file(result, report)
    assert summary_bytes == (DEMO_COMPARISON_DIR / "comparison_summary.csv").read_bytes()
    assert report_bytes == (DEMO_COMPARISON_DIR / "report.txt").read_bytes()
    dispatch = read_inventory_file(result, "cases/da/dispatch.parquet")
    assert dispatch == (DEMO_COMPARISON_DIR / "cases" / "da" / "dispatch.parquet").read_bytes()
    assert mime_type("comparison_summary.csv") == "text/csv"
    assert mime_type("report.txt").startswith("text/plain")
    assert mime_type("dispatch.parquet") == "application/vnd.apache.parquet"
    assert mime_type("run_events.jsonl") == "application/jsonl"
    assert mime_type("run.log").startswith("text/plain")
    assert mime_type("artifact_manifest.json") == "application/json"
    assert mime_type("package.zip") == "application/zip"
    name = download_filename("demo-2025-all-markets-pv500", "cases/da/dispatch.parquet")
    assert name == "demo-2025-all-markets-pv500-da-dispatch.parquet"
    assert "/" not in name and "\\" not in name
    assert zip_download_filename("demo-2025-all-markets-pv500") == "demo-2025-all-markets-pv500.zip"


def test_path_safety_rejects_traversal_absolute_and_duplicates(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.json").write_text("{}", encoding="utf-8")
    require_relative_artifact_path(root, "ok.json")
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        require_relative_artifact_path(root, "../ok.json")
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        require_relative_artifact_path(root, "..\\ok.json")
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        require_relative_artifact_path(root, str(root / "ok.json"))
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        require_relative_artifact_path(root, "sub\\ok.json")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "escape.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        require_relative_artifact_path(root, "escape.json")


def test_size_hash_change_and_missing_file_fail_closed(tmp_path: Path) -> None:
    record, result = _one_market_da(tmp_path)
    items = build_download_inventory(result, job=record, outputs_root=tmp_path)
    target = Path(record["output_directory"]) / "summary.csv"
    original = target.read_bytes()
    target.write_bytes(original + b"\n")
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        read_inventory_file(result, "summary.csv", job=record, outputs_root=tmp_path)
    target.write_bytes(original)
    target.unlink()
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        read_inventory_file(result, "summary.csv", job=record, outputs_root=tmp_path)
    assert items


def test_size_cap_blocks_zip_but_not_individual_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record, result = _one_market_da(tmp_path)
    items = build_download_inventory(result, job=record, outputs_root=tmp_path)
    monkeypatch.setattr("ui.services.result_downloads.ZIP_SOURCE_SIZE_LIMIT_BYTES", 1)
    assert zip_is_within_limit(items, limit=1) is False
    with pytest.raises(DownloadsError, match="larger than the display limit"):
        build_result_zip(result, job=record, outputs_root=tmp_path, size_limit=1)
    payload = read_inventory_file(result, "report.txt", job=record, outputs_root=tmp_path)
    assert payload == (Path(record["output_directory"]) / "report.txt").read_bytes()
    assert ZIP_SOURCE_SIZE_LIMIT_BYTES == 128 * 1024 * 1024


def test_inventory_does_not_read_dispatch_contents() -> None:
    import inspect

    import ui.services.result_downloads as downloads

    source = inspect.getsource(downloads)
    assert "pyarrow" not in source
    assert "pandas" not in source
    items = build_download_inventory(open_demo_artifacts())
    assert len(items) == 63
    dispatch = next(item for item in items if item.relative_path.endswith("dispatch.parquet"))
    assert dispatch.size > 0
    assert dispatch.sha256


def test_deferred_callable_rechecks_trust(tmp_path: Path) -> None:
    record, result = _one_market_da(tmp_path)
    items = build_download_inventory(result, job=record, outputs_root=tmp_path)
    relative = "summary.csv"
    original = (Path(record["output_directory"]) / relative).read_bytes()
    assert read_inventory_file(result, relative, job=record, outputs_root=tmp_path) == original
    (Path(record["output_directory"]) / relative).write_bytes(original + b"x")
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        read_inventory_file(result, relative, job=record, outputs_root=tmp_path)
    assert any(item.relative_path == relative for item in items)


def test_app_demo_downloads_do_not_modify_artifacts_or_launch() -> None:
    tracked = list(DEMO_COMPARISON_DIR.rglob("*"))
    before = _audit(tracked)
    popen_calls: list[str] = []

    def bang(**_kwargs):
        popen_calls.append("popen")
        raise AssertionError("Downloads must not launch a worker")

    TEST_HOOKS.clear()
    TEST_HOOKS["popen"] = bang
    try:
        at = AppTest.from_file(str(APP), default_timeout=120)
        at.run()
        next(item for item in at.checkbox if item.label == "Demo mode").set_value(True)
        at.run()
        next(item for item in at.button if item.label == "Continue").click()
        at.run()
        next(item for item in at.button if item.label == "View demonstration results").click()
        at.run()
        next(item for item in at.button if item.label == "Downloads").click()
        at.run()
        assert not at.exception
        labels = [item.label for item in at.download_button]
        assert DOWNLOADS_PACKAGE_LABEL in labels
        assert DOWNLOADS_SUMMARY_LABEL in labels
        assert DOWNLOADS_REPORT_LABEL in labels
        text = " ".join(
            [
                *[str(item.value) for item in at.caption],
                *[str(item.value) for item in at.subheader],
                *[str(item.value) for item in at.markdown],
                *[str(item.value) for item in at.text],
                *[str(getattr(item, "value", item)) for item in at.get("code")],
            ]
        )
        assert RESERVED_TAB_BODY not in text
        assert DOWNLOADS_STORED_INTRO in text
        assert "Individual downloads use the stored files unchanged." not in text
        assert "The complete result package is assembled when requested and is not saved in the project." not in text
        assert "ui/demo_artifacts/stepinbel_2025_all_markets_pv500/" in text
        assert "C:\\" not in text
        assert popen_calls == []
    finally:
        TEST_HOOKS.clear()
    after = _audit(tracked)
    assert after == before
    zips = list(ROOT.rglob("*.zip"))
    assert not any("demo-2025-all-markets-pv500.zip" in str(path) for path in zips)


def test_app_one_market_hides_scope_selector(tmp_path: Path) -> None:
    record, result = _one_market_da(tmp_path)
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    snapshot = build_snapshot(form, demo=False)
    popen_calls: list[str] = []

    def bang(**_kwargs):
        popen_calls.append("popen")
        raise AssertionError("one-market downloads must not launch")

    TEST_HOOKS.clear()
    TEST_HOOKS["outputs_root"] = tmp_path
    TEST_HOOKS["popen"] = bang
    try:
        state = default_state()
        store_snapshot(state, snapshot, snapshot["form_fingerprint"])
        state["form"] = form
        state["job"] = record
        unlock_results(state, result)
        at = AppTest.from_file(str(APP), default_timeout=120)
        at.run()
        at.session_state["sib"] = state
        at.query_params[JOB_QUERY_KEY] = record["job_id"]
        at.run()
        next(item for item in at.button if item.label == "Downloads").click()
        at.run()
        assert not at.exception
        assert not any(item.label == "Scope" for item in at.selectbox)
        assert any(item.label == "File" for item in at.selectbox)
        assert popen_calls == []
    finally:
        TEST_HOOKS.clear()


def test_mismatched_one_case_identity_is_rejected(tmp_path: Path) -> None:
    record, result = mismatched_one_case_live(tmp_path)
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        build_download_inventory(result, job=record, outputs_root=tmp_path)


def test_two_market_claim_on_three_market_tree_is_rejected(tmp_path: Path) -> None:
    record, result = mismatched_two_market_on_three_market_demo(tmp_path)
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        build_download_inventory(result, job=record, outputs_root=tmp_path)


def test_result_period_mismatch_is_rejected(tmp_path: Path) -> None:
    record, result = identified_relocated_one_case_live(tmp_path)
    wrong = dict(result)
    wrong["period"] = {"start_date": "2024-01-01", "end_date": "2024-12-31"}
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        build_download_inventory(wrong, job=record, outputs_root=tmp_path)


def test_relocated_identified_one_case_is_accepted(tmp_path: Path) -> None:
    record, result = identified_relocated_one_case_live(tmp_path)
    items = build_download_inventory(result, job=record, outputs_root=tmp_path)
    assert len(items) == 18
    assert {item.scope for item in items} == {"da"}


def test_copied_demo_tree_is_rejected_as_demo_inventory(tmp_path: Path) -> None:
    result = relocated_demo_result(tmp_path)
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        build_download_inventory(result)


def test_genuine_two_market_inventory_has_only_selected_children(tmp_path: Path) -> None:
    record, result = genuine_two_market_live(tmp_path)
    items = build_download_inventory(result, job=record, outputs_root=tmp_path)
    assert len(items) == 45
    assert {item.scope for item in items} == {"overall", "da", "afrr"}
    assert not any(item.relative_path.startswith("cases/mfrr/") for item in items)
    assert not (Path(record["output_directory"]) / "cases" / "mfrr").exists()


def test_deferred_callables_reject_swapped_artifact_tree(tmp_path: Path) -> None:
    record, result = identified_relocated_one_case_live(tmp_path)
    relative = "summary.csv"
    original = read_inventory_file(result, relative, job=record, outputs_root=tmp_path)
    assert original
    output = Path(record["output_directory"])
    copy_one_case_tree(DEMO_COMPARISON_DIR / "cases" / "afrr", output)
    rebind_one_case_run_id(output, str(record["job_id"]))
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        read_inventory_file(result, relative, job=record, outputs_root=tmp_path)
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        build_result_zip(result, job=record, outputs_root=tmp_path)
