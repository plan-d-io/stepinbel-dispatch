from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from types import MappingProxyType

import pytest

from stepinbel.reporting import ArtifactError, validate_market_comparison_artifacts
from stepinbel.reporting.comparison_artifacts import (
    comparison_rows_from_child_summaries,
    write_comparison_artifact_manifest,
)
from stepinbel.reporting.constants import (
    COMPARISON_MANIFEST_ENTRIES,
    COMPARISON_MARKETS,
    COMPARISON_ROW_FIELDS,
    comparison_manifest_entries,
)
from stepinbel.workflows import execute_market_comparison
from tests.workflow_helpers import comparison_configs, utc


@pytest.fixture(scope="module")
def completed_comparison(data_root: Path, tmp_path_factory):
    from stepinbel.workflows import build_market_comparison_request

    request = build_market_comparison_request(
        comparison_configs(),
        data_root,
        tmp_path_factory.mktemp("cmp-art") / "run",
        run_id="cmp-art-01",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    return execute_market_comparison(request)


def _copy(source: Path, dest: Path) -> Path:
    shutil.copytree(source, dest)
    return dest


def _run_id(run_dir: Path) -> str:
    return json.loads((run_dir / "comparison_request.json").read_text(encoding="utf-8"))["run_id"]


def _minimal_summary(total: float, **overrides) -> dict:
    payload = {
        "interval_count": 4,
        "duration_hours": 1.0,
        "e_max_mwh": 2.0,
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


def test_exact_tree_and_parent_manifest(completed_comparison) -> None:
    directory = completed_comparison.directory
    names = {path.name for path in directory.iterdir()}
    assert names == {
        "comparison_request.json",
        "comparison_summary.json",
        "comparison_summary.csv",
        "comparison_metadata.json",
        "report.txt",
        "run_status.json",
        "run_events.jsonl",
        "run.log",
        "artifact_manifest.json",
        "cases",
    }
    assert {path.name for path in (directory / "cases").iterdir()} == set(COMPARISON_MARKETS)
    artifacts = validate_market_comparison_artifacts(directory)
    assert isinstance(artifacts, MappingProxyType)
    with pytest.raises((TypeError, AttributeError)):
        artifacts["report.txt"] = directory  # type: ignore[index]
    assert isinstance(completed_comparison.rows, tuple)
    manifest = json.loads((directory / "artifact_manifest.json").read_text(encoding="utf-8"))
    listed = [item["filename"] for item in manifest["entries"]]
    assert listed == list(COMPARISON_MANIFEST_ENTRIES)
    for entry in manifest["entries"]:
        assert "/" == Path(entry["filename"]).as_posix()[entry["filename"].find("/") :][0] if "/" in entry["filename"] else True
        assert "\\" not in entry["filename"]
        path = directory.joinpath(*entry["filename"].split("/"))
        assert path.is_file()


def test_child_manifest_hash_chain(completed_comparison) -> None:
    directory = completed_comparison.directory
    manifest = json.loads((directory / "artifact_manifest.json").read_text(encoding="utf-8"))
    by_name = {item["filename"]: item for item in manifest["entries"]}
    for market in COMPARISON_MARKETS:
        relative = f"cases/{market}/artifact_manifest.json"
        child = directory / "cases" / market / "artifact_manifest.json"
        assert by_name[relative]["sha256"] == __import__("hashlib").sha256(child.read_bytes()).hexdigest()
        assert by_name[relative]["byte_size"] == child.stat().st_size
        child_manifest = json.loads(child.read_text(encoding="utf-8"))
        for entry in child_manifest["entries"]:
            digest = __import__("hashlib").sha256((directory / "cases" / market / entry["filename"]).read_bytes()).hexdigest()
            assert digest == entry["sha256"]


def test_json_csv_full_precision_and_rank_rebuild(completed_comparison) -> None:
    directory = completed_comparison.directory
    summary = json.loads((directory / "comparison_summary.json").read_text(encoding="utf-8"))
    headers, *rows = (directory / "comparison_summary.csv").read_text(encoding="utf-8").splitlines()
    cells = [dict(zip(headers.split(","), row.split(","), strict=True)) for row in rows]
    assert [item["market"] for item in summary["rows"]] == [row["market"] for row in cells]
    for json_row, csv_row in zip(summary["rows"], cells, strict=True):
        for name in COMPARISON_ROW_FIELDS:
            parsed = json.loads(csv_row[name]) if name not in {"market", "case_run_id"} else csv_row[name]
            if name in {"market", "case_run_id"}:
                assert parsed == json_row[name]
            else:
                assert parsed == json_row[name]
    child_summaries = {}
    child_ids = {}
    for market in COMPARISON_MARKETS:
        payload = json.loads((directory / "cases" / market / "summary.json").read_text(encoding="utf-8"))
        child_summaries[market] = payload
        child_ids[market] = payload["run_id"]
    rows, highest = comparison_rows_from_child_summaries(child_summaries, child_ids)
    assert highest == summary["highest_revenue_market"]
    assert [row.market for row in rows] == [item["market"] for item in summary["rows"]]
    assert [row.difference_from_highest_eur for row in rows] == [
        item["difference_from_highest_eur"] for item in summary["rows"]
    ]


def test_tie_ranking_uses_canonical_order() -> None:
    summaries = {
        "da": _minimal_summary(10.0, turbined_mwh=2.0),
        "mfrr": _minimal_summary(10.0, turbined_mwh=4.0),
        "afrr": _minimal_summary(10.0, turbined_mwh=6.0),
    }
    ids = {"da": "da-id", "mfrr": "mfrr-id", "afrr": "afrr-id"}
    rows, highest = comparison_rows_from_child_summaries(summaries, ids)
    assert highest == "da"
    assert [row.market for row in rows] == ["da", "mfrr", "afrr"]
    assert [row.revenue_rank for row in rows] == [1, 2, 3]
    assert all(row.difference_from_highest_eur == 0.0 for row in rows)
    assert rows[1].full_cycles == 2.0


def test_relocation_succeeds(completed_comparison, tmp_path: Path) -> None:
    dest = _copy(completed_comparison.directory, tmp_path / "relocated")
    validate_market_comparison_artifacts(dest)


def test_child_corruption_fails(completed_comparison, tmp_path: Path) -> None:
    dest = _copy(completed_comparison.directory, tmp_path / "child-edit")
    payload = json.loads((dest / "cases" / "da" / "summary.json").read_text(encoding="utf-8"))
    payload["total_site_revenue_eur"] = float(payload["total_site_revenue_eur"]) + 99.0
    (dest / "cases" / "da" / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_comparison_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(dest)


def test_coordinated_summary_corruption_fails(completed_comparison, tmp_path: Path) -> None:
    dest = _copy(completed_comparison.directory, tmp_path / "agg")
    payload = json.loads((dest / "comparison_summary.json").read_text(encoding="utf-8"))
    payload["rows"][0]["total_site_revenue_eur"] = float(payload["rows"][0]["total_site_revenue_eur"]) + 50.0
    (dest / "comparison_summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_comparison_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(dest)


def test_swapped_rows_fail(completed_comparison, tmp_path: Path) -> None:
    dest = _copy(completed_comparison.directory, tmp_path / "swap")
    payload = json.loads((dest / "comparison_summary.json").read_text(encoding="utf-8"))
    payload["rows"] = list(reversed(payload["rows"]))
    (dest / "comparison_summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_comparison_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(dest)


def test_false_highest_rank_difference_and_cycles_fail(completed_comparison, tmp_path: Path) -> None:
    dest = _copy(completed_comparison.directory, tmp_path / "false")
    payload = json.loads((dest / "comparison_summary.json").read_text(encoding="utf-8"))
    payload["highest_revenue_market"] = "afrr" if payload["highest_revenue_market"] != "afrr" else "da"
    payload["rows"][0]["revenue_rank"] = 3
    payload["rows"][1]["difference_from_highest_eur"] = 12.0
    payload["rows"][2]["full_cycles"] = 99.0
    (dest / "comparison_summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_comparison_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(dest)


def test_metadata_child_digest_corruption_fails(completed_comparison, tmp_path: Path) -> None:
    dest = _copy(completed_comparison.directory, tmp_path / "meta")
    payload = json.loads((dest / "comparison_metadata.json").read_text(encoding="utf-8"))
    payload["children"]["da"]["artifact_manifest_sha256"] = "ab" * 32
    (dest / "comparison_metadata.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_comparison_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError, match="digest"):
        validate_market_comparison_artifacts(dest)


def test_modified_report_plus_manifest_fails(completed_comparison, tmp_path: Path) -> None:
    dest = _copy(completed_comparison.directory, tmp_path / "report")
    (dest / "report.txt").write_text("tampered\n", encoding="utf-8")
    write_comparison_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError, match="report.txt"):
        validate_market_comparison_artifacts(dest)


def test_missing_extra_hidden_temp_fail(completed_comparison, tmp_path: Path) -> None:
    missing = _copy(completed_comparison.directory, tmp_path / "missing")
    (missing / "report.txt").unlink()
    with pytest.raises(ArtifactError, match="missing"):
        validate_market_comparison_artifacts(missing)

    extra = _copy(completed_comparison.directory, tmp_path / "extra")
    (extra / "bonus.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ArtifactError, match="unexpected"):
        validate_market_comparison_artifacts(extra)

    hidden = _copy(completed_comparison.directory, tmp_path / "hidden")
    (hidden / ".secret").write_text("x", encoding="utf-8")
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(hidden)

    temp = _copy(completed_comparison.directory, tmp_path / "temp")
    (temp / ".report.txt.1.tmp").write_text("partial", encoding="utf-8")
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(temp)

    subdir = _copy(completed_comparison.directory, tmp_path / "subdir")
    (subdir / "extra-dir").mkdir()
    with pytest.raises(ArtifactError, match="unexpected"):
        validate_market_comparison_artifacts(subdir)


def test_malformed_json_csv_and_unsafe_manifest_paths(completed_comparison, tmp_path: Path) -> None:
    bad_json = _copy(completed_comparison.directory, tmp_path / "bad-json")
    (bad_json / "comparison_summary.json").write_text("{", encoding="utf-8")
    write_comparison_artifact_manifest(bad_json, _run_id(bad_json))
    with pytest.raises(ArtifactError) as caught:
        validate_market_comparison_artifacts(bad_json)
    assert caught.value.__cause__ is not None or "JSON" in str(caught.value)

    bad_csv = _copy(completed_comparison.directory, tmp_path / "bad-csv")
    (bad_csv / "comparison_summary.csv").write_bytes(b"\xff\xfe invalid csv")
    write_comparison_artifact_manifest(bad_csv, _run_id(bad_csv))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(bad_csv)

    unsafe = _copy(completed_comparison.directory, tmp_path / "unsafe")
    manifest = json.loads((unsafe / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0]["filename"] = "..\\secret.json"
    (unsafe / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(unsafe)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_summary_wrong_numeric_types_fail(completed_comparison, tmp_path: Path) -> None:
    cases = (
        ("interval-string", lambda payload: payload.__setitem__("interval_count", str(payload["interval_count"]))),
        ("rank-float", lambda payload: payload["rows"][0].__setitem__("revenue_rank", float(payload["rows"][0]["revenue_rank"]))),
        ("row-int-bool", lambda payload: payload["rows"][0].__setitem__("interval_count", True)),
        ("float-string", lambda payload: payload["rows"][0].__setitem__("total_site_revenue_eur", str(payload["rows"][0]["total_site_revenue_eur"]))),
    )
    for name, mutate in cases:
        dest = _copy(completed_comparison.directory, tmp_path / name)
        payload = json.loads((dest / "comparison_summary.json").read_text(encoding="utf-8"))
        mutate(payload)
        _write_json(dest / "comparison_summary.json", payload)
        write_comparison_artifact_manifest(dest, _run_id(dest))
        with pytest.raises(ArtifactError):
            validate_market_comparison_artifacts(dest)


def test_metadata_identity_extra_field_and_wrong_numeric_types_fail(
    completed_comparison, tmp_path: Path
) -> None:
    dest = _copy(completed_comparison.directory, tmp_path / "meta-identity")
    payload = json.loads((dest / "comparison_metadata.json").read_text(encoding="utf-8"))
    payload["software_version"] = payload["software_version"] + "-tampered"
    payload["children"]["da"]["note"] = "unexpected"
    _write_json(dest / "comparison_metadata.json", payload)
    write_comparison_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(dest)

    numeric = _copy(completed_comparison.directory, tmp_path / "meta-numeric")
    payload = json.loads((numeric / "comparison_metadata.json").read_text(encoding="utf-8"))
    payload["interval_count"] = float(payload["interval_count"])
    payload["comparison_artifact_schema_version"] = True
    payload["children"]["mfrr"]["artifact_manifest_byte_size"] = str(
        payload["children"]["mfrr"]["artifact_manifest_byte_size"]
    )
    _write_json(numeric / "comparison_metadata.json", payload)
    write_comparison_artifact_manifest(numeric, _run_id(numeric))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(numeric)


def test_manifest_extra_fields_and_wrong_types_fail(completed_comparison, tmp_path: Path) -> None:
    extra_root = _copy(completed_comparison.directory, tmp_path / "man-root")
    manifest = json.loads((extra_root / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["note"] = "unexpected"
    _write_json(extra_root / "artifact_manifest.json", manifest)
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(extra_root)

    extra_entry = _copy(completed_comparison.directory, tmp_path / "man-entry")
    manifest = json.loads((extra_entry / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0]["note"] = "unexpected"
    _write_json(extra_entry / "artifact_manifest.json", manifest)
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(extra_entry)

    string_size = _copy(completed_comparison.directory, tmp_path / "man-size")
    manifest = json.loads((string_size / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0]["byte_size"] = str(manifest["entries"][0]["byte_size"])
    _write_json(string_size / "artifact_manifest.json", manifest)
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(string_size)

    bool_schema = _copy(completed_comparison.directory, tmp_path / "man-bool")
    manifest = json.loads((bool_schema / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["comparison_artifact_schema_version"] = True
    _write_json(bool_schema / "artifact_manifest.json", manifest)
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(bool_schema)

    bad_hash = _copy(completed_comparison.directory, tmp_path / "man-hash")
    manifest = json.loads((bad_hash / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0]["sha256"] = "ZZ" * 32
    _write_json(bad_hash / "artifact_manifest.json", manifest)
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(bad_hash)

    upper_hash = _copy(completed_comparison.directory, tmp_path / "man-upper")
    manifest = json.loads((upper_hash / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["entries"][0]["sha256"] = str(manifest["entries"][0]["sha256"]).upper()
    _write_json(upper_hash / "artifact_manifest.json", manifest)
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(upper_hash)


@pytest.fixture(scope="module", params=(("da", "mfrr"), ("da", "afrr"), ("mfrr", "afrr")))
def completed_subset(data_root: Path, tmp_path_factory, request):
    from stepinbel.workflows import build_market_comparison_request

    markets = request.param
    built = build_market_comparison_request(
        comparison_configs(markets=markets),
        data_root,
        tmp_path_factory.mktemp("cmp-sub-art") / "-".join(markets),
        run_id="cmp-sub-" + "-".join(markets),
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    return execute_market_comparison(built), markets


def test_subset_tree_manifest_and_row_count(completed_subset) -> None:
    run, markets = completed_subset
    directory = run.directory
    assert {path.name for path in (directory / "cases").iterdir()} == set(markets)
    for omitted in set(COMPARISON_MARKETS) - set(markets):
        assert not (directory / "cases" / omitted).exists()
    artifacts = validate_market_comparison_artifacts(directory)
    assert isinstance(artifacts, MappingProxyType)
    manifest = json.loads((directory / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert [item["filename"] for item in manifest["entries"]] == list(comparison_manifest_entries(markets))
    metadata = json.loads((directory / "comparison_metadata.json").read_text(encoding="utf-8"))
    assert metadata["canonical_execution_order"] == list(markets)
    assert set(metadata["children"]) == set(markets)
    summary = json.loads((directory / "comparison_summary.json").read_text(encoding="utf-8"))
    assert len(summary["rows"]) == len(markets)
    assert len(run.rows) == len(markets)


def test_subset_omitted_and_unknown_children_rejected(completed_subset, tmp_path: Path) -> None:
    run, markets = completed_subset
    omitted = next(iter(set(COMPARISON_MARKETS) - set(markets)))
    inserted = _copy(run.directory, tmp_path / f"insert-{omitted}")
    source_child = next((inserted / "cases").iterdir())
    shutil.copytree(source_child, inserted / "cases" / omitted)
    write_comparison_artifact_manifest(inserted, _run_id(inserted))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(inserted)

    removed_market = markets[0]
    removed = _copy(run.directory, tmp_path / f"removed-{removed_market}")
    shutil.rmtree(removed / "cases" / removed_market)
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(removed)

    unknown = _copy(run.directory, tmp_path / "unknown-child")
    shutil.copytree(next((unknown / "cases").iterdir()), unknown / "cases" / "fcr")
    write_comparison_artifact_manifest(unknown, _run_id(unknown))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(unknown)


def test_subset_omitted_metadata_and_summary_rows_rejected(completed_subset, tmp_path: Path) -> None:
    run, markets = completed_subset
    omitted = next(iter(set(COMPARISON_MARKETS) - set(markets)))
    dest = _copy(run.directory, tmp_path / "meta-omitted")
    payload = json.loads((dest / "comparison_metadata.json").read_text(encoding="utf-8"))
    first = next(iter(payload["children"].values()))
    payload["children"][omitted] = dict(first)
    payload["children"][omitted]["market"] = omitted
    _write_json(dest / "comparison_metadata.json", payload)
    write_comparison_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(dest)

    summary_dest = _copy(run.directory, tmp_path / "summary-omitted")
    summary = json.loads((summary_dest / "comparison_summary.json").read_text(encoding="utf-8"))
    extra = dict(summary["rows"][0])
    extra["market"] = omitted
    summary["rows"].append(extra)
    _write_json(summary_dest / "comparison_summary.json", summary)
    write_comparison_artifact_manifest(summary_dest, _run_id(summary_dest))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(summary_dest)


def test_subset_coordinated_child_corruption_fails(completed_subset, tmp_path: Path) -> None:
    run, markets = completed_subset
    dest = _copy(run.directory, tmp_path / "coord")
    child = dest / "cases" / markets[0] / "summary.json"
    payload = json.loads(child.read_text(encoding="utf-8"))
    payload["total_site_revenue_eur"] = float(payload["total_site_revenue_eur"]) + 77.0
    child.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_comparison_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(dest)


def test_subset_relocation_succeeds(completed_subset, tmp_path: Path) -> None:
    run, _markets = completed_subset
    dest = _copy(run.directory, tmp_path / "reloc-sub")
    validate_market_comparison_artifacts(dest)


def test_two_market_tie_uses_canonical_order() -> None:
    summaries = {
        "afrr": _minimal_summary(10.0, turbined_mwh=6.0),
        "mfrr": _minimal_summary(10.0, turbined_mwh=4.0),
    }
    ids = {"afrr": "afrr-id", "mfrr": "mfrr-id"}
    rows, highest = comparison_rows_from_child_summaries(summaries, ids)
    assert highest == "mfrr"
    assert [row.market for row in rows] == ["mfrr", "afrr"]
    assert [row.revenue_rank for row in rows] == [1, 2]


def test_csv_alternate_integer_spelling_fails(completed_comparison, tmp_path: Path) -> None:
    dest = _copy(completed_comparison.directory, tmp_path / "csv-spell")
    path = dest / "comparison_summary.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    header = rows[0]
    index = header.index("revenue_rank")
    original = rows[1][index]
    rows[1][index] = "1.0" if original == "1" else f"+{original}"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(rows)
    write_comparison_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError):
        validate_market_comparison_artifacts(dest)
