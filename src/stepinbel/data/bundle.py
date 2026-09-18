"""Validate a published StepInBel data directory against its manifest."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import pyarrow as pa
import pyarrow.parquet as pq

REQUIRED_TABLES: tuple[str, ...] = (
    "da_prices_qh",
    "balancing_qh",
    "capacity_blocks",
    "capacity_bids",
    "pv_profile_qh",
)
OPTIONAL_WIND_TABLE = "wind_profile_qh"

_REQUIRED_MANIFEST_KEYS: tuple[str, ...] = (
    "pipeline_version",
    "built_at_utc",
    "git_commit",
    "partial_build",
    "tables",
)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_HASH_CHUNK = 1024 * 1024

_TIMESTAMP_UTC = "timestamp[us, tz=UTC]"
_DICTIONARY_STRING = "dictionary<string>"

REQUIRED_SCHEMAS: dict[str, tuple[tuple[str, str], ...]] = {
    "da_prices_qh": (
        ("datetime_utc", _TIMESTAMP_UTC),
        ("da_price_eur_mwh", "double"),
        ("native_resolution", _DICTIONARY_STRING),
        ("upsampled_from_hourly", "bool"),
        ("source_file", _DICTIONARY_STRING),
    ),
    "balancing_qh": (
        ("datetime_utc", _TIMESTAMP_UTC),
        ("quality_status", _DICTIONARY_STRING),
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
        ("block", _DICTIONARY_STRING),
        ("product", _DICTIONARY_STRING),
        ("direction", _DICTIONARY_STRING),
        ("auction_step", _DICTIONARY_STRING),
        ("block_start_utc", _TIMESTAMP_UTC),
        ("block_end_utc", _TIMESTAMP_UTC),
        ("block_hours", "double"),
        ("data_available", "bool"),
        ("n_bids_awarded", "int32"),
        ("awarded_volume_mw", "double"),
        ("marginal_price_eur_mw_h", "double"),
        ("vwap_price_eur_mw_h", "double"),
        ("min_awarded_price_eur_mw_h", "double"),
        ("source_dataset", _DICTIONARY_STRING),
    ),
    "capacity_bids": (
        ("delivery_date_local", "date32[day]"),
        ("block", _DICTIONARY_STRING),
        ("product", _DICTIONARY_STRING),
        ("direction", _DICTIONARY_STRING),
        ("auction_step", _DICTIONARY_STRING),
        ("price_eur_mw_h", "double"),
        ("awarded_volume_mw", "double"),
        ("source_dataset", _DICTIONARY_STRING),
        ("cumulative_volume_mw", "double"),
    ),
    "pv_profile_qh": (
        ("datetime_utc", _TIMESTAMP_UTC),
        ("region", _DICTIONARY_STRING),
        ("measured_mw", "double"),
        ("monitored_capacity_mw", "double"),
        ("load_factor", "double"),
    ),
    "wind_profile_qh": (
        ("datetime_utc", _TIMESTAMP_UTC),
        ("profile_id", _DICTIONARY_STRING),
        ("wind_type", _DICTIONARY_STRING),
        ("region", _DICTIONARY_STRING),
        ("measured_mw", "double"),
        ("monitored_capacity_mw", "double"),
        ("load_factor", "double"),
    ),
}


class DataBundleError(Exception):
    """Published data directory is missing, incomplete, or schema-incompatible."""


@dataclass(frozen=True)
class PublishedTable:
    """Validated description of one required Parquet table."""

    stem: str
    path: Path
    expected_sha256: str
    actual_sha256: str
    manifest_row_count: int
    parquet_row_count: int
    column_names: tuple[str, ...]


@dataclass(frozen=True)
class PublishedDataBundle:
    """Validated description of a complete published data directory."""

    root: Path
    manifest_path: Path
    manifest_sha256: str
    pipeline_version: str
    built_at_utc: str
    git_commit: str
    partial_build: bool
    tables: Mapping[str, PublishedTable]
    coverage: Mapping[str, Mapping[str, object]]


def open_published_bundle(root: str | Path) -> PublishedDataBundle:
    """Validate and describe a complete published StepInBel data directory."""
    data_root = Path(root).expanduser().resolve()
    manifest_path = data_root / "MANIFEST.json"
    payload, manifest_sha256 = _read_manifest(manifest_path)
    _require_manifest_keys(payload)
    _require_complete_build(payload["partial_build"])
    tables_obj = payload["tables"]
    if not isinstance(tables_obj, dict):
        raise DataBundleError("MANIFEST.json field 'tables' must be an object")

    validated: dict[str, PublishedTable] = {}
    for stem in REQUIRED_TABLES:
        validated[stem] = _validate_table(data_root, stem, tables_obj)
    wind_in_manifest = OPTIONAL_WIND_TABLE in tables_obj
    wind_path = data_root / f"{OPTIONAL_WIND_TABLE}.parquet"
    wind_file_present = wind_path.is_file()
    if wind_in_manifest or wind_file_present:
        if not wind_in_manifest:
            raise DataBundleError(
                f"{OPTIONAL_WIND_TABLE}.parquet is present but MANIFEST.json "
                f"has no {OPTIONAL_WIND_TABLE} entry"
            )
        validated[OPTIONAL_WIND_TABLE] = _validate_table(
            data_root, OPTIONAL_WIND_TABLE, tables_obj
        )

    coverage = _freeze_coverage(tables_obj, tuple(validated))
    return PublishedDataBundle(
        root=data_root,
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        pipeline_version=_require_str(payload["pipeline_version"], "pipeline_version"),
        built_at_utc=_require_str(payload["built_at_utc"], "built_at_utc"),
        git_commit=_require_str(payload["git_commit"], "git_commit"),
        partial_build=False,
        tables=MappingProxyType(validated),
        coverage=coverage,
    )


def _read_manifest(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise DataBundleError(f"MANIFEST.json is missing at {path}") from exc
    except OSError as exc:
        raise DataBundleError(f"MANIFEST.json is unreadable at {path}: {exc}") from exc

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataBundleError(f"MANIFEST.json is not valid UTF-8: {exc}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataBundleError(f"MANIFEST.json is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise DataBundleError("MANIFEST.json must contain a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def _require_manifest_keys(payload: Mapping[str, Any]) -> None:
    missing = [key for key in _REQUIRED_MANIFEST_KEYS if key not in payload]
    if missing:
        raise DataBundleError(
            f"MANIFEST.json is missing required key {missing[0]!r}"
        )


def _require_complete_build(value: object) -> None:
    if not isinstance(value, bool):
        raise DataBundleError("MANIFEST.json field 'partial_build' must be a boolean")
    if value:
        raise DataBundleError(
            "MANIFEST.json field 'partial_build' is true; incomplete bundles are rejected"
        )


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise DataBundleError(f"MANIFEST.json field {field!r} must be a non-empty string")
    return value


def _validate_table(
    data_root: Path, stem: str, tables_obj: Mapping[str, Any]
) -> PublishedTable:
    if stem not in tables_obj:
        raise DataBundleError(f"MANIFEST.json is missing required table {stem!r}")
    entry = tables_obj[stem]
    if not isinstance(entry, dict):
        raise DataBundleError(f"MANIFEST.json tables.{stem} must be an object")

    if "sha256" not in entry:
        raise DataBundleError(f"MANIFEST.json tables.{stem} is missing sha256")
    if "rows" not in entry:
        raise DataBundleError(f"MANIFEST.json tables.{stem} is missing rows")
    expected_sha256 = _require_sha256(entry["sha256"], stem)
    manifest_rows = _require_row_count(entry["rows"], stem)

    path = data_root / f"{stem}.parquet"
    if not path.is_file():
        raise DataBundleError(f"required table file {path.name} is missing")

    try:
        actual_sha256 = _sha256_file(path)
    except OSError as exc:
        raise DataBundleError(
            f"table {stem} file {path.name} is unreadable: {exc}"
        ) from exc
    if actual_sha256 != expected_sha256:
        raise DataBundleError(
            f"table {stem} sha256 {actual_sha256} does not match manifest {expected_sha256}"
        )

    try:
        parquet_file = pq.ParquetFile(path)
    except Exception as exc:
        raise DataBundleError(
            f"table {stem} is not a readable Parquet file: {exc}"
        ) from exc

    parquet_rows = int(parquet_file.metadata.num_rows)
    if parquet_rows != manifest_rows:
        raise DataBundleError(
            f"table {stem} Parquet row count {parquet_rows} does not match "
            f"manifest row count {manifest_rows}"
        )

    schema = parquet_file.schema_arrow
    column_names = tuple(field.name for field in schema)
    _require_schema(stem, schema)
    return PublishedTable(
        stem=stem,
        path=path,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
        manifest_row_count=manifest_rows,
        parquet_row_count=parquet_rows,
        column_names=column_names,
    )


def _require_sha256(value: object, stem: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise DataBundleError(f"MANIFEST.json tables.{stem}.sha256 is malformed")
    return value.lower()


def _require_row_count(value: object, stem: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataBundleError(
            f"MANIFEST.json tables.{stem}.rows must be a non-negative integer"
        )
    return value


def _require_schema(stem: str, schema: pa.Schema) -> None:
    expected = REQUIRED_SCHEMAS[stem]
    actual = tuple((field.name, _logical_type_label(field.type)) for field in schema)
    if actual == expected:
        return
    raise DataBundleError(
        f"table {stem} schema {actual} does not match required schema {expected}"
    )


def _logical_type_label(data_type: pa.DataType) -> str:
    if pa.types.is_timestamp(data_type):
        tz = data_type.tz
        return f"timestamp[{data_type.unit}, tz={tz}]"
    if pa.types.is_float64(data_type):
        return "double"
    if pa.types.is_boolean(data_type):
        return "bool"
    if pa.types.is_int32(data_type):
        return "int32"
    if pa.types.is_date32(data_type):
        return "date32[day]"
    if pa.types.is_dictionary(data_type):
        value_type = data_type.value_type
        if pa.types.is_string(value_type):
            return _DICTIONARY_STRING
        return f"dictionary<{value_type}>"
    return str(data_type)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _freeze_coverage(
    tables_obj: Mapping[str, Any],
    stems: tuple[str, ...],
) -> Mapping[str, Mapping[str, object]]:
    frozen: dict[str, Mapping[str, object]] = {}
    for stem in stems:
        entry = tables_obj[stem]
        if not isinstance(entry, dict):
            continue
        item: dict[str, object] = {}
        if "coverage_utc" in entry:
            item["coverage_utc"] = _as_sequence(entry["coverage_utc"])
        if "coverage_by_product" in entry:
            item["coverage_by_product"] = _freeze_product_coverage(
                entry["coverage_by_product"]
            )
        if "regions" in entry:
            item["regions"] = _as_sequence(entry["regions"])
        if "profiles" in entry:
            item["profiles"] = _as_sequence(entry["profiles"])
        frozen[stem] = MappingProxyType(item)
    return MappingProxyType(frozen)


def _freeze_product_coverage(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        return MappingProxyType({"value": value})
    return MappingProxyType(
        {str(key): _as_sequence(bounds) for key, bounds in value.items()}
    )


def _as_sequence(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value
