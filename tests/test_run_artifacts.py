from __future__ import annotations

import csv
import json
import math
import shutil
from pathlib import Path
from types import MappingProxyType

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stepinbel.optimizer.types import CAPACITY_RESULT_SCHEMA, DISPATCH_COLUMNS
from stepinbel.reporting import ArtifactError, validate_run_artifacts
from stepinbel.reporting.artifacts import _dispatch_csv_rows, write_artifact_manifest
from stepinbel.reporting.constants import DISPATCH_SUMMARY_FIELDS, PUBLISHED_TABLE_STEMS
from stepinbel.reporting.io import atomic_write_csv
from stepinbel.workflows import execute_case_run
from stepinbel.workflows.constants import REQUIRED_ARTIFACTS
from tests.workflow_helpers import build_request, da_config


@pytest.fixture
def completed_run(data_root: Path, tmp_path: Path):
    request = build_request(data_root, tmp_path / "artifacts-run", da_config())
    return execute_case_run(request)


def _copy(source: Path, dest: Path) -> Path:
    shutil.copytree(source, dest)
    return dest


def _run_id(run_dir: Path) -> str:
    return json.loads((run_dir / "run_request.json").read_text(encoding="utf-8"))["run_id"]


def _replace_column(table: pa.Table, name: str, values) -> pa.Table:
    arrays = []
    for column in table.column_names:
        if column == name:
            arrays.append(values)
        else:
            arrays.append(table.column(column))
    return pa.Table.from_arrays(arrays, names=table.column_names)


def _rewrite_dispatch(dest: Path, table: pa.Table) -> None:
    pq.write_table(table, dest / "dispatch.parquet")
    atomic_write_csv(dest / "dispatch.csv", DISPATCH_COLUMNS, _dispatch_csv_rows(table))
    write_artifact_manifest(dest, _run_id(dest))


def test_required_success_filenames(completed_run) -> None:
    names = {path.name for path in completed_run.directory.iterdir()}
    assert names == set(REQUIRED_ARTIFACTS)
    assert not any(name.endswith(".tmp") for name in names)


def test_dispatch_schema_order_and_values(completed_run) -> None:
    table = pq.read_table(completed_run.directory / "dispatch.parquet")
    expected = completed_run.result.dispatch
    assert tuple(table.column_names) == DISPATCH_COLUMNS
    assert table.equals(expected)
    csv_text = (completed_run.directory / "dispatch.csv").read_text(encoding="utf-8")
    header = csv_text.splitlines()[0].split(",")
    assert header == list(DISPATCH_COLUMNS)
    assert "Z" in csv_text.splitlines()[1]


def test_empty_typed_capacity(completed_run) -> None:
    table = pq.read_table(completed_run.directory / "capacity.parquet")
    assert table.schema == CAPACITY_RESULT_SCHEMA
    assert table.num_rows == 0
    csv_lines = (completed_run.directory / "capacity.csv").read_text(encoding="utf-8").splitlines()
    assert csv_lines == [",".join(CAPACITY_RESULT_SCHEMA.names)]


def test_json_csv_parquet_reconcile_and_manifest(completed_run) -> None:
    artifacts = validate_run_artifacts(completed_run.directory)
    assert isinstance(artifacts, MappingProxyType)
    with pytest.raises((TypeError, AttributeError)):
        artifacts["report.txt"] = completed_run.directory  # type: ignore[index]
    manifest = json.loads((completed_run.directory / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == len(REQUIRED_ARTIFACTS) - 1
    summary = json.loads((completed_run.directory / "summary.json").read_text(encoding="utf-8"))
    headers, row = (completed_run.directory / "summary.csv").read_text(encoding="utf-8").splitlines()
    cells = dict(zip(headers.split(","), row.split(","), strict=True))
    for name in DISPATCH_SUMMARY_FIELDS:
        parsed = json.loads(cells[name])
        assert parsed == summary[name]
    assert json.loads(cells["simultaneous_interval_energy_net_eur"]) == summary["diagnostics"][
        "simultaneous_interval_energy_net_eur"
    ]
    assert summary["total_site_revenue_eur"] == completed_run.result.summary.total_site_revenue_eur


def test_all_five_bundle_tables_are_recorded(completed_run) -> None:
    metadata = json.loads((completed_run.directory / "run_metadata.json").read_text(encoding="utf-8"))
    tables = metadata["published_data"]["tables"]
    assert set(tables) == set(PUBLISHED_TABLE_STEMS)
    for stem in PUBLISHED_TABLE_STEMS:
        entry = tables[stem]
        assert entry["expected_sha256"]
        assert entry["actual_sha256"]
        assert isinstance(entry["manifest_row_count"], int)
        assert isinstance(entry["parquet_row_count"], int)
        assert entry["manifest_row_count"] == entry["parquet_row_count"]


def test_corruption_and_missing_and_wrong_identity_detected(completed_run, tmp_path: Path) -> None:
    source = completed_run.directory

    missing = _copy(source, tmp_path / "missing")
    (missing / "report.txt").unlink()
    with pytest.raises(ArtifactError, match="missing"):
        validate_run_artifacts(missing)

    corrupt = _copy(source, tmp_path / "corrupt")
    (corrupt / "summary.json").write_text("{", encoding="utf-8")
    with pytest.raises(ArtifactError):
        validate_run_artifacts(corrupt)

    wrong_id = _copy(source, tmp_path / "wrong-id")
    payload = json.loads((wrong_id / "summary.json").read_text(encoding="utf-8"))
    payload["run_id"] = "other-run"
    (wrong_id / "summary.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArtifactError, match="run_id"):
        validate_run_artifacts(wrong_id)

    wrong_status = _copy(source, tmp_path / "wrong-status")
    status = json.loads((wrong_status / "run_status.json").read_text(encoding="utf-8"))
    status["state"] = "failed"
    (wrong_status / "run_status.json").write_text(json.dumps(status), encoding="utf-8")
    with pytest.raises(ArtifactError, match="completed"):
        validate_run_artifacts(wrong_status)

    temps = _copy(source, tmp_path / "temps")
    (temps / ".dispatch.parquet.1.tmp").write_text("partial", encoding="utf-8")
    with pytest.raises(ArtifactError):
        validate_run_artifacts(temps)


def test_existing_output_directory_is_untouched(data_root: Path, tmp_path: Path) -> None:
    existing = tmp_path / "already"
    existing.mkdir()
    sentinel = existing / "sentinel.txt"
    sentinel.write_text("keep-me", encoding="utf-8")
    request = build_request(data_root, existing, da_config())
    from stepinbel.workflows import RunRequestError

    with pytest.raises(RunRequestError, match="already exists") as caught:
        execute_case_run(request)
    assert caught.value.category == "invalid_output"
    assert sentinel.read_text(encoding="utf-8") == "keep-me"
    assert list(existing.iterdir()) == [sentinel]


def test_dispatch_schema_type_corruption_is_rejected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "dtype")
    table = pq.read_table(dest / "dispatch.parquet")
    corrupted = _replace_column(
        table,
        "p_pump_mw",
        pa.array([int(value) for value in table.column("p_pump_mw").to_pylist()], type=pa.int64()),
    )
    pq.write_table(corrupted, dest / "dispatch.parquet")
    write_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError, match="type"):
        validate_run_artifacts(dest)


def test_period_summary_schema_type_corruption_is_rejected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "period-dtype")
    table = pq.read_table(dest / "monthly_summary.parquet")
    corrupted = _replace_column(
        table,
        "interval_count",
        pa.array([float(value) for value in table.column("interval_count").to_pylist()], type=pa.float64()),
    )
    pq.write_table(corrupted, dest / "monthly_summary.parquet")
    write_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError, match="type"):
        validate_run_artifacts(dest)


def test_coordinated_summary_inconsistency_is_detected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "summary-edit")
    payload = json.loads((dest / "summary.json").read_text(encoding="utf-8"))
    payload["energy_gross_eur"] = float(payload["energy_gross_eur"]) + 50.0
    (dest / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError, match="energy_gross"):
        validate_run_artifacts(dest)


def test_coordinated_period_inconsistency_is_detected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "period-edit")
    table = pq.read_table(dest / "monthly_summary.parquet")
    periods = ["2099-12" if i == 0 else value for i, value in enumerate(table.column("period").to_pylist())]
    corrupted = _replace_column(table, "period", pa.array(periods, type=pa.string()))
    pq.write_table(corrupted, dest / "monthly_summary.parquet")
    write_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError):
        validate_run_artifacts(dest)


def test_per_row_dispatch_accounting_corruption_is_detected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "row-edit")
    table = pq.read_table(dest / "dispatch.parquet")
    energy = table.column("market_energy_net_eur").to_pylist()
    energy[0] = float(energy[0]) + 25.0
    corrupted = _replace_column(table, "market_energy_net_eur", pa.array(energy, type=pa.float64()))
    pq.write_table(corrupted, dest / "dispatch.parquet")
    write_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError, match="market_energy_net"):
        validate_run_artifacts(dest)


def test_corrupt_parquet_and_malformed_csv_raise_artifact_error(completed_run, tmp_path: Path) -> None:
    parquet_dir = _copy(completed_run.directory, tmp_path / "bad-parquet")
    (parquet_dir / "dispatch.parquet").write_bytes(b"not parquet")
    write_artifact_manifest(parquet_dir, _run_id(parquet_dir))
    with pytest.raises(ArtifactError) as caught:
        validate_run_artifacts(parquet_dir)
    assert not isinstance(caught.value, OSError)

    csv_dir = _copy(completed_run.directory, tmp_path / "bad-csv")
    (csv_dir / "dispatch.csv").write_bytes(b"\xff\xfe invalid csv")
    write_artifact_manifest(csv_dir, _run_id(csv_dir))
    with pytest.raises(ArtifactError):
        validate_run_artifacts(csv_dir)


def test_wrong_schema_versions_are_rejected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "schema-ver")
    metadata = json.loads((dest / "run_metadata.json").read_text(encoding="utf-8"))
    metadata["status_schema_version"] = 99
    (dest / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    write_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError, match="schema version"):
        validate_run_artifacts(dest)


def test_unexpected_subdirectory_is_rejected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "subdir")
    (dest / "extra").mkdir()
    with pytest.raises(ArtifactError, match="unexpected"):
        validate_run_artifacts(dest)


def test_duplicate_dispatch_timestamp_is_rejected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "dup-time")
    table = pq.read_table(dest / "dispatch.parquet")
    stamps = table.column("datetime_utc").to_pylist()
    stamps[0] = stamps[1]
    corrupted = _replace_column(table, "datetime_utc", pa.array(stamps, type=table.schema.field("datetime_utc").type))
    _rewrite_dispatch(dest, corrupted)
    with pytest.raises(ArtifactError, match="datetime_utc"):
        validate_run_artifacts(dest)


def test_published_table_provenance_corruption_is_rejected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "prov")
    metadata = json.loads((dest / "run_metadata.json").read_text(encoding="utf-8"))
    stem = PUBLISHED_TABLE_STEMS[0]
    metadata["published_data"]["tables"][stem] = {
        "expected_sha256": "x",
        "actual_sha256": "y",
        "manifest_row_count": -1,
        "parquet_row_count": -2,
    }
    (dest / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    write_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError):
        validate_run_artifacts(dest)


def test_no_pv_near_zero_pv_available_is_rejected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "no-pv-eps")
    table = pq.read_table(dest / "dispatch.parquet")
    values = table.column("pv_available_mw").to_pylist()
    values[0] = 1e-10
    corrupted = _replace_column(table, "pv_available_mw", pa.array(values, type=pa.float64()))
    _rewrite_dispatch(dest, corrupted)
    with pytest.raises(ArtifactError, match="exact zero"):
        validate_run_artifacts(dest)


def test_nonfinite_dispatch_value_is_rejected(completed_run, tmp_path: Path) -> None:
    dest = _copy(completed_run.directory, tmp_path / "inf")
    table = pq.read_table(dest / "dispatch.parquet")
    values = table.column("p_pump_mw").to_pylist()
    values[0] = math.inf
    corrupted = _replace_column(table, "p_pump_mw", pa.array(values, type=pa.float64()))
    pq.write_table(corrupted, dest / "dispatch.parquet")
    headers, rows = [], []
    with (dest / "dispatch.csv").open("r", encoding="utf-8", newline="") as handle:
        parsed = list(csv.reader(handle))
        headers, rows = parsed[0], parsed[1:]
    pump_index = headers.index("p_pump_mw")
    rows[0][pump_index] = "Infinity"
    with (dest / "dispatch.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)
    write_artifact_manifest(dest, _run_id(dest))
    with pytest.raises(ArtifactError, match="finite"):
        validate_run_artifacts(dest)


def test_csv_extra_and_missing_cells_are_rejected(completed_run, tmp_path: Path) -> None:
    extra = _copy(completed_run.directory, tmp_path / "csv-extra")
    text = (extra / "dispatch.csv").read_text(encoding="utf-8")
    lines = text.splitlines()
    lines[1] = lines[1] + ",extra"
    (extra / "dispatch.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_artifact_manifest(extra, _run_id(extra))
    with pytest.raises(ArtifactError, match="cells"):
        validate_run_artifacts(extra)

    missing = _copy(completed_run.directory, tmp_path / "csv-missing")
    text = (missing / "dispatch.csv").read_text(encoding="utf-8")
    lines = text.splitlines()
    cells = lines[1].split(",")
    lines[1] = ",".join(cells[:-1])
    (missing / "dispatch.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_artifact_manifest(missing, _run_id(missing))
    with pytest.raises(ArtifactError, match="cells"):
        validate_run_artifacts(missing)

    summary = _copy(completed_run.directory, tmp_path / "summary-extra")
    text = (summary / "summary.csv").read_text(encoding="utf-8")
    lines = text.splitlines()
    lines[1] = lines[1] + ",extra"
    (summary / "summary.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_artifact_manifest(summary, _run_id(summary))
    with pytest.raises(ArtifactError, match="cells"):
        validate_run_artifacts(summary)
