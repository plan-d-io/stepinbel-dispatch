from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stepinbel.data import DataBundleError, open_published_bundle
from stepinbel.data.bundle import OPTIONAL_WIND_TABLE, REQUIRED_SCHEMAS, REQUIRED_TABLES

EXPECTED_DATA_HASHES = {
    "MANIFEST.json": "f74396e74c3f149b092be7529395e361afeb7811a2b7e641ed220c4b9d996c8d",
    "da_prices_qh.parquet": "2ee198b8829baf45b18c13fda8fac24f349a385c9021af1372bb40070aec2ca4",
    "balancing_qh.parquet": "9bd5f40bb3b81086e9c1810da6ab2e03942c6088f3496d8e71c4b3ff229850b2",
    "capacity_blocks.parquet": "ebc122484f46c2caaece1fdef69dda53961ef58d1a27604455da8f7b3679b8c5",
    "capacity_bids.parquet": "819924867c73c348d223aef45fa8abd6bbcff28cf0189f53ae871be8250cdb7d",
    "pv_profile_qh.parquet": "fa5043887b8a61f0c0603d35f71a8f68f82e48a8fa36ed753a626ceadd9b9eb3",
    "wind_profile_qh.parquet": "0317c91d85f7185cb272f653ef46b9b495db1c16304c0200852c58d1aeabf917",
}

EXPECTED_ROW_COUNTS = {
    "da_prices_qh": 410396,
    "balancing_qh": 81408,
    "capacity_blocks": 63420,
    "capacity_bids": 3779118,
    "pv_profile_qh": 3007872,
    "wind_profile_qh": 941168,
}

EXPECTED_SCHEMAS = {
    "da_prices_qh": (
        ("datetime_utc", "timestamp[us, tz=UTC]"),
        ("da_price_eur_mwh", "double"),
        ("native_resolution", "dictionary<string>"),
        ("upsampled_from_hourly", "bool"),
        ("source_file", "dictionary<string>"),
    ),
    "balancing_qh": (
        ("datetime_utc", "timestamp[us, tz=UTC]"),
        ("quality_status", "dictionary<string>"),
        ("system_imbalance_mw", "double"),
        ("afrr_volume_up_mw", "double"),
        ("afrr_volume_down_mw", "double"),
        ("mfrr_sa_up_mw", "double"),
        ("mfrr_da_up_mw", "double"),
        ("mfrr_sa_down_mw", "double"),
        ("mfrr_da_down_mw", "double"),
        ("igcc_up_mw", "double"),
        ("igcc_down_mw", "double"),
        ("afrr_price_up_eur_mwh", "double"),
        ("afrr_price_down_eur_mwh", "double"),
        ("mfrr_sa_up_price", "double"),
        ("mfrr_da_up_price", "double"),
        ("mfrr_sa_down_price", "double"),
        ("mfrr_da_down_price", "double"),
        ("marginal_incremental_price", "double"),
        ("marginal_decremental_price", "double"),
        ("has_afrr_up", "bool"),
        ("has_afrr_down", "bool"),
        ("has_mfrr_up", "bool"),
        ("has_mfrr_down", "bool"),
        ("cbmp_afrr_up", "double"),
        ("cbmp_afrr_down", "double"),
        ("cbmp_mfrr_up", "double"),
        ("cbmp_mfrr_down", "double"),
    ),
    "capacity_blocks": (
        ("delivery_date_local", "date32[day]"),
        ("block", "dictionary<string>"),
        ("product", "dictionary<string>"),
        ("direction", "dictionary<string>"),
        ("auction_step", "dictionary<string>"),
        ("block_start_utc", "timestamp[us, tz=UTC]"),
        ("block_end_utc", "timestamp[us, tz=UTC]"),
        ("block_hours", "double"),
        ("data_available", "bool"),
        ("n_bids_awarded", "int32"),
        ("awarded_volume_mw", "double"),
        ("marginal_price_eur_mw_h", "double"),
        ("vwap_price_eur_mw_h", "double"),
        ("min_awarded_price_eur_mw_h", "double"),
        ("source_dataset", "dictionary<string>"),
    ),
    "capacity_bids": (
        ("delivery_date_local", "date32[day]"),
        ("block", "dictionary<string>"),
        ("product", "dictionary<string>"),
        ("direction", "dictionary<string>"),
        ("auction_step", "dictionary<string>"),
        ("price_eur_mw_h", "double"),
        ("awarded_volume_mw", "double"),
        ("source_dataset", "dictionary<string>"),
        ("cumulative_volume_mw", "double"),
    ),
    "pv_profile_qh": (
        ("datetime_utc", "timestamp[us, tz=UTC]"),
        ("region", "dictionary<string>"),
        ("measured_mw", "double"),
        ("monitored_capacity_mw", "double"),
        ("load_factor", "double"),
    ),
    "wind_profile_qh": (
        ("datetime_utc", "timestamp[us, tz=UTC]"),
        ("profile_id", "dictionary<string>"),
        ("wind_type", "dictionary<string>"),
        ("region", "dictionary<string>"),
        ("measured_mw", "double"),
        ("monitored_capacity_mw", "double"),
        ("load_factor", "double"),
    ),
}

_FAKE_SHA256 = "00" * 32


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _logical_type_label(data_type: pa.DataType) -> str:
    if pa.types.is_timestamp(data_type):
        return f"timestamp[{data_type.unit}, tz={data_type.tz}]"
    if pa.types.is_float64(data_type):
        return "double"
    if pa.types.is_boolean(data_type):
        return "bool"
    if pa.types.is_int32(data_type):
        return "int32"
    if pa.types.is_date32(data_type):
        return "date32[day]"
    if pa.types.is_dictionary(data_type) and (
        pa.types.is_string(data_type.value_type)
        or pa.types.is_large_string(data_type.value_type)
    ):
        return "dictionary<string>"
    return str(data_type)


def _dummy_array(kind: str) -> pa.Array:
    if kind == "timestamp[us, tz=UTC]":
        value = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        return pa.array([value], type=pa.timestamp("us", tz="UTC"))
    if kind == "double":
        return pa.array([1.0], type=pa.float64())
    if kind == "bool":
        return pa.array([True])
    if kind == "int32":
        return pa.array([1], type=pa.int32())
    if kind == "date32[day]":
        return pa.array([dt.date(2025, 1, 1)], type=pa.date32())
    if kind == "dictionary<string>":
        return pa.array(["x"], type=pa.dictionary(pa.int8(), pa.string()))
    raise ValueError(kind)


def _write_parquet(path: Path, stem: str, schema: tuple[tuple[str, str], ...] | None = None) -> None:
    fields = schema if schema is not None else REQUIRED_SCHEMAS[stem]
    table = pa.table({name: _dummy_array(kind) for name, kind in fields})
    pq.write_table(table, path)


def _write_complete_bundle(root: Path) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for stem in REQUIRED_TABLES:
        path = root / f"{stem}.parquet"
        _write_parquet(path, stem)
        tables[stem] = {"sha256": _sha256(path), "rows": 1}
    payload = {
        "pipeline_version": "1.0.0",
        "built_at_utc": "2026-08-14T17:18:21Z",
        "git_commit": "8208d06",
        "partial_build": False,
        "tables": tables,
    }
    (root / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


@pytest.fixture(scope="module")
def published_bundle(data_root: Path):
    return open_published_bundle(data_root)


def test_open_published_bundle_accepts_copied_bundle(published_bundle, data_root: Path) -> None:
    assert published_bundle.root == data_root.resolve()
    assert published_bundle.manifest_path == data_root.resolve() / "MANIFEST.json"
    assert published_bundle.pipeline_version == "1.0.0"
    assert published_bundle.built_at_utc == "2026-09-17T20:05:04Z"
    assert published_bundle.git_commit == "2f071d2"
    assert published_bundle.partial_build is False
    assert published_bundle.manifest_sha256 == EXPECTED_DATA_HASHES["MANIFEST.json"]
    assert set(REQUIRED_TABLES) <= set(published_bundle.tables)
    assert OPTIONAL_WIND_TABLE in published_bundle.tables
    assert published_bundle.coverage["da_prices_qh"]["coverage_utc"] == (
        "2015-01-04T23:00:00Z",
        "2026-09-18T21:45:00Z",
    )
    assert published_bundle.coverage["capacity_blocks"]["coverage_by_product"]["mfrr"] == (
        "2021-01-01",
        "2026-09-18",
    )
    assert "Belgium" in published_bundle.coverage["pv_profile_qh"]["regions"]
    assert published_bundle.coverage["wind_profile_qh"]["coverage_utc"] == (
        "2019-12-31T23:00:00Z",
        "2026-09-16T21:45:00Z",
    )
    assert published_bundle.coverage["wind_profile_qh"]["profiles"] == (
        "onshore_belgium",
        "onshore_flanders",
        "onshore_wallonia",
        "offshore_belgium",
    )


def test_copied_data_hashes_match_brief(data_root: Path) -> None:
    for name, digest in EXPECTED_DATA_HASHES.items():
        assert _sha256(data_root / name) == digest


def test_parquet_row_counts_match_manifest(published_bundle) -> None:
    for stem, rows in EXPECTED_ROW_COUNTS.items():
        table = published_bundle.tables[stem]
        assert table.manifest_row_count == rows
        assert table.parquet_row_count == rows


def test_ordered_schemas_match_brief(data_root: Path) -> None:
    for stem, expected in EXPECTED_SCHEMAS.items():
        schema = pq.ParquetFile(data_root / f"{stem}.parquet").schema_arrow
        actual = tuple((field.name, _logical_type_label(field.type)) for field in schema)
        assert actual == expected


def test_open_published_bundle_uses_explicit_root_not_cwd(
    data_root: Path, tmp_path: Path
) -> None:
    script = (
        "from pathlib import Path\n"
        "from stepinbel.data import open_published_bundle\n"
        "import sys\n"
        "bundle = open_published_bundle(Path(sys.argv[1]))\n"
        "assert bundle.pipeline_version == '1.0.0'\n"
        "assert bundle.tables['da_prices_qh'].parquet_row_count == 410396\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(data_root.resolve())],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(DataBundleError, match="MANIFEST.json is missing"):
        open_published_bundle(tmp_path)


def test_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "MANIFEST.json").write_text("{", encoding="utf-8")
    with pytest.raises(DataBundleError, match="not valid JSON"):
        open_published_bundle(tmp_path)


@pytest.mark.parametrize("key", ["pipeline_version", "built_at_utc", "git_commit", "partial_build", "tables"])
def test_missing_required_manifest_key(tmp_path: Path, key: str) -> None:
    payload = {
        "pipeline_version": "1.0.0",
        "built_at_utc": "2026-08-14T17:18:21Z",
        "git_commit": "8208d06",
        "partial_build": False,
        "tables": {},
    }
    del payload[key]
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match=key):
        open_published_bundle(tmp_path)


def test_partial_build_true_is_rejected(tmp_path: Path) -> None:
    payload = {
        "pipeline_version": "1.0.0",
        "built_at_utc": "2026-08-14T17:18:21Z",
        "git_commit": "8208d06",
        "partial_build": True,
        "tables": {},
    }
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match="partial_build"):
        open_published_bundle(tmp_path)


def test_missing_required_table_entry(tmp_path: Path) -> None:
    payload = _write_complete_bundle(tmp_path)
    del payload["tables"]["da_prices_qh"]
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match="da_prices_qh"):
        open_published_bundle(tmp_path)


def test_missing_required_table_file(tmp_path: Path) -> None:
    _write_complete_bundle(tmp_path)
    (tmp_path / "da_prices_qh.parquet").unlink()
    with pytest.raises(DataBundleError, match="da_prices_qh.parquet is missing"):
        open_published_bundle(tmp_path)


def test_malformed_table_hash(tmp_path: Path) -> None:
    payload = _write_complete_bundle(tmp_path)
    payload["tables"]["da_prices_qh"]["sha256"] = "not-a-hash"
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match="da_prices_qh.sha256 is malformed"):
        open_published_bundle(tmp_path)


def test_hash_mismatch(tmp_path: Path) -> None:
    payload = _write_complete_bundle(tmp_path)
    payload["tables"]["da_prices_qh"]["sha256"] = _FAKE_SHA256
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match="da_prices_qh sha256"):
        open_published_bundle(tmp_path)


def test_invalid_parquet(tmp_path: Path) -> None:
    payload = _write_complete_bundle(tmp_path)
    garbage = tmp_path / "da_prices_qh.parquet"
    garbage.write_bytes(b"not parquet")
    payload["tables"]["da_prices_qh"]["sha256"] = _sha256(garbage)
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match="da_prices_qh is not a readable Parquet file"):
        open_published_bundle(tmp_path)


def test_row_count_mismatch(tmp_path: Path) -> None:
    payload = _write_complete_bundle(tmp_path)
    payload["tables"]["da_prices_qh"]["rows"] = 2
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match="da_prices_qh Parquet row count"):
        open_published_bundle(tmp_path)


def test_schema_mismatch(tmp_path: Path) -> None:
    payload = _write_complete_bundle(tmp_path)
    path = tmp_path / "da_prices_qh.parquet"
    _write_parquet(path, "da_prices_qh", schema=(("wrong", "double"),))
    payload["tables"]["da_prices_qh"]["sha256"] = _sha256(path)
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match="da_prices_qh schema"):
        open_published_bundle(tmp_path)


def test_extra_manifest_table_is_ignored(tmp_path: Path) -> None:
    payload = _write_complete_bundle(tmp_path)
    payload["tables"]["future_table"] = {"sha256": _FAKE_SHA256, "rows": 0}
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    bundle = open_published_bundle(tmp_path)
    assert "future_table" not in bundle.tables
    assert set(bundle.tables) == set(REQUIRED_TABLES)


def test_table_hash_oserror_becomes_data_bundle_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_complete_bundle(tmp_path)
    blocked = (tmp_path / "da_prices_qh.parquet").resolve()
    original_open = Path.open

    def guarded_open(self: Path, *args: object, **kwargs: object):
        if Path(self).resolve() == blocked:
            raise PermissionError("denied")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(DataBundleError, match="da_prices_qh") as caught:
        open_published_bundle(tmp_path)
    assert "da_prices_qh.parquet" in str(caught.value)
    assert isinstance(caught.value.__cause__, PermissionError)


def test_dictionary_large_string_is_schema_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stepinbel.data import bundle as bundle_mod

    _write_complete_bundle(tmp_path)
    original = bundle_mod.pq.ParquetFile

    def patched_parquet_file(source: object, *args: object, **kwargs: object):
        inner = original(source, *args, **kwargs)
        if Path(source).name != "da_prices_qh.parquet":
            return inner
        fields = []
        for field in inner.schema_arrow:
            if field.name == "native_resolution":
                fields.append(
                    field.with_type(
                        pa.dictionary(field.type.index_type, pa.large_string())
                    )
                )
            else:
                fields.append(field)

        class _PatchedParquetFile:
            def __init__(self, wrapped: object, schema: pa.Schema) -> None:
                self._wrapped = wrapped
                self.schema_arrow = schema

            def __getattr__(self, name: str) -> object:
                return getattr(self._wrapped, name)

        return _PatchedParquetFile(inner, pa.schema(fields))

    monkeypatch.setattr(bundle_mod.pq, "ParquetFile", patched_parquet_file)
    with pytest.raises(DataBundleError, match="da_prices_qh schema") as caught:
        open_published_bundle(tmp_path)
    message = str(caught.value)
    assert "dictionary<large_string>" in message
    assert "('native_resolution', 'dictionary<string>')" in message


def test_bundle_mappings_and_sequences_are_read_only(published_bundle) -> None:
    with pytest.raises(TypeError):
        published_bundle.tables["da_prices_qh"] = published_bundle.tables["balancing_qh"]
    with pytest.raises(TypeError):
        published_bundle.coverage["da_prices_qh"] = {}
    product_coverage = published_bundle.coverage["capacity_blocks"]["coverage_by_product"]
    with pytest.raises(TypeError):
        product_coverage["mfrr"] = ("2021-01-01", "2026-08-15")
    coverage_utc = published_bundle.coverage["da_prices_qh"]["coverage_utc"]
    with pytest.raises(TypeError):
        coverage_utc[0] = "2015-01-04T23:00:00Z"
    regions = published_bundle.coverage["pv_profile_qh"]["regions"]
    with pytest.raises(TypeError):
        regions[0] = "Belgium"
