"""Atomic artifact writes and file hashing."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from stepinbel.reporting.constants import RUN_ARTIFACT_SCHEMA_VERSION


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dumps_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, ensure_ascii=False) + "\n"


class ArtifactError(ValueError):
    """A run directory is incomplete, corrupt, or fails reconciliation."""


def temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.tmp")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = temporary_path(path)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(path, dumps_json(payload))


def format_json_number(value: float) -> str:
    return json.dumps(float(value), allow_nan=False)


def csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return format_json_number(value)
    return str(value)


def atomic_write_csv(
    path: Path,
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> None:
    tmp = temporary_path(path)
    try:
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(headers)
            for row in rows:
                writer.writerow(csv_cell(item) for item in row)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_parquet(path: Path, table: pa.Table) -> None:
    tmp = temporary_path(path)
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(path: Path) -> dict[str, object]:
    return {
        "filename": path.name,
        "byte_size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def artifact_schema_payload() -> dict[str, int]:
    return {"artifact_schema_version": RUN_ARTIFACT_SCHEMA_VERSION}
