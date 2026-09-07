"""Trusted Downloads inventory, path safety, and deferred byte readers."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, NamedTuple

from ui.services.artifacts import bind_exact_result_artifacts
from ui.services.paths import KIND_CASE, KIND_COMPARISON
from ui.services.result_format import display_market_keys, market_label

ERROR_DOWNLOADS_TITLE = "Downloads unavailable"
ERROR_DOWNLOADS_BODY = "The stored result files are incomplete or incompatible."
ERROR_ZIP_TOO_LARGE = (
    "The complete result package is larger than the display limit. "
    "Individual files remain available."
)
ZIP_SOURCE_SIZE_LIMIT_BYTES = 128 * 1024 * 1024
SCOPE_OVERALL = "overall"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
MIME_BY_SUFFIX = {
    ".json": "application/json",
    ".csv": "text/csv",
    ".parquet": "application/vnd.apache.parquet",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".jsonl": "application/jsonl",
    ".zip": "application/zip",
}
FILE_DESCRIPTIONS = {
    "artifact_manifest.json": "Artifact inventory",
    "capacity.csv": "Balancing capacity commitments",
    "capacity.parquet": "Balancing capacity commitments",
    "comparison_metadata.json": "Comparison metadata",
    "comparison_request.json": "Comparison request",
    "comparison_summary.csv": "Comparison summary",
    "comparison_summary.json": "Comparison summary",
    "dispatch.csv": "Quarter-hour dispatch",
    "dispatch.parquet": "Quarter-hour dispatch",
    "monthly_summary.csv": "Monthly summary",
    "monthly_summary.parquet": "Monthly summary",
    "report.txt": "Readable run summary",
    "resolved_config.json": "Resolved configuration",
    "run.log": "Run log",
    "run_events.jsonl": "Workflow events",
    "run_metadata.json": "Run metadata",
    "run_request.json": "Run request",
    "run_status.json": "Run status",
    "summary.csv": "Result summary",
    "summary.json": "Result summary",
    "yearly_summary.csv": "Yearly summary",
    "yearly_summary.parquet": "Yearly summary",
}


class DownloadsError(ValueError):
    """Stored result files could not be prepared for download."""


class InventoryItem(NamedTuple):
    scope: str
    scope_label: str
    relative_path: str
    filename: str
    format: str
    size: int
    sha256: str
    description: str


def _fail() -> None:
    raise DownloadsError(ERROR_DOWNLOADS_BODY)


def _require_str(value: object) -> str:
    if type(value) is not str or not value:
        _fail()
    return value


def _require_int(value: object) -> int:
    if type(value) is not int or isinstance(value, bool):
        _fail()
    return value


def _require_hash(value: object) -> str:
    text = _require_str(value)
    if HASH_RE.fullmatch(text) is None:
        _fail()
    return text


def format_bytes(size: int) -> str:
    if type(size) is not int or isinstance(size, bool) or size < 0:
        _fail()
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def mime_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in MIME_BY_SUFFIX:
        _fail()
    return MIME_BY_SUFFIX[suffix]


def format_name(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    labels = {
        "json": "JSON",
        "csv": "CSV",
        "parquet": "Parquet",
        "txt": "TXT",
        "log": "LOG",
        "jsonl": "JSONL",
        "zip": "ZIP",
    }
    if suffix not in labels:
        _fail()
    return labels[suffix]


def path_safe_name(value: str) -> str:
    text = SAFE_NAME_RE.sub("-", value).strip(".-")
    return text or "result"


def file_description(filename: str) -> str:
    return FILE_DESCRIPTIONS.get(filename, filename)


def _is_within(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    trusted = root.resolve()
    return resolved == trusted or trusted in resolved.parents


def require_relative_artifact_path(root: Path, relative: str) -> Path:
    if type(relative) is not str or not relative:
        _fail()
    if "\\" in relative or relative.startswith("/") or relative.startswith("\\"):
        _fail()
    posix = PurePosixPath(relative)
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        _fail()
    if str(posix) != relative:
        _fail()
    current = root
    for part in posix.parts:
        current = current / part
        if current.is_symlink():
            _fail()
    candidate = root.joinpath(*posix.parts)
    if candidate.is_symlink():
        _fail()
    try:
        resolved = candidate.resolve()
    except OSError:
        _fail()
    if not _is_within(resolved, root):
        _fail()
    if resolved != candidate.resolve():
        _fail()
    lexical = Path(*((root,) + posix.parts)).resolve()
    if resolved != lexical:
        _fail()
    return resolved


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        _fail()
    return digest.hexdigest()


def _bind_result(
    result: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None,
    outputs_root: Path | None,
):
    try:
        return bind_exact_result_artifacts(result, job=job, outputs_root=outputs_root)
    except (OSError, TypeError, ValueError):
        _fail()
    raise DownloadsError(ERROR_DOWNLOADS_BODY)


def _split_mapping(mapping: Mapping[str, Path]) -> tuple[dict[str, Path], set[str]]:
    files: dict[str, Path] = {}
    directories: set[str] = set()
    for key, path in mapping.items():
        if type(key) is not str or not key:
            _fail()
        if path.is_dir():
            directories.add(key)
        elif path.is_file():
            files[key] = path
        else:
            _fail()
    return files, directories


def _read_manifest_entries(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail()
    if not isinstance(payload, Mapping):
        _fail()
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        _fail()
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, Mapping):
            _fail()
        filename = _require_str(item.get("filename"))
        if filename in seen:
            _fail()
        seen.add(filename)
        parsed.append(
            {
                "filename": filename,
                "byte_size": _require_int(item.get("byte_size")),
                "sha256": _require_hash(item.get("sha256")),
            }
        )
    return parsed


def _stat_file(path: Path) -> int:
    try:
        if path.is_symlink() or not path.is_file():
            _fail()
        return int(path.stat().st_size)
    except OSError:
        _fail()
    raise DownloadsError(ERROR_DOWNLOADS_BODY)


def _add_item(
    items: list[InventoryItem],
    seen: set[str],
    *,
    root: Path,
    scope: str,
    scope_label: str,
    relative: str,
    size: int,
    digest: str,
) -> None:
    path = require_relative_artifact_path(root, relative)
    actual = _stat_file(path)
    if actual != size:
        _fail()
    if relative in seen:
        _fail()
    seen.add(relative)
    filename = PurePosixPath(relative).name
    items.append(
        InventoryItem(
            scope=scope,
            scope_label=scope_label,
            relative_path=relative,
            filename=filename,
            format=format_name(filename),
            size=size,
            sha256=digest,
            description=file_description(filename),
        )
    )


def _manifest_self(path: Path) -> tuple[int, str]:
    size = _stat_file(path)
    return size, sha256_file(path)


def build_download_inventory(
    result: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> list[InventoryItem]:
    bound = _bind_result(result, job=job, outputs_root=outputs_root)
    validated = bound.result
    root = bound.directory
    kind = str(validated["kind"])
    items: list[InventoryItem] = []
    seen: set[str] = set()
    mapping_files, mapping_dirs = _split_mapping(bound.mapping)
    if kind == KIND_CASE:
        if mapping_dirs:
            _fail()
        market = display_market_keys(validated["markets"])[0]
        label = market_label(market)
        entries = _read_manifest_entries(root / "artifact_manifest.json")
        listed = {item["filename"] for item in entries}
        if listed | {"artifact_manifest.json"} != set(mapping_files):
            _fail()
        if "artifact_manifest.json" not in mapping_files:
            _fail()
        size, digest = _manifest_self(root / "artifact_manifest.json")
        _add_item(
            items,
            seen,
            root=root,
            scope=market,
            scope_label=label,
            relative="artifact_manifest.json",
            size=size,
            digest=digest,
        )
        for entry in entries:
            _add_item(
                items,
                seen,
                root=root,
                scope=market,
                scope_label=label,
                relative=entry["filename"],
                size=entry["byte_size"],
                digest=entry["sha256"],
            )
        if {item.relative_path for item in items} != set(mapping_files):
            _fail()
    elif kind == KIND_COMPARISON:
        selected = list(validated["markets"])
        expected_dirs = {"cases"} | {f"cases/{market}" for market in selected}
        if mapping_dirs != expected_dirs:
            _fail()
        child_manifests = {f"cases/{market}/artifact_manifest.json" for market in selected}
        parent_files = {name for name in mapping_files if not name.startswith("cases/")}
        if set(mapping_files) != parent_files | child_manifests:
            _fail()
        if "artifact_manifest.json" not in parent_files:
            _fail()
        if set(bound.child_mappings) != set(selected):
            _fail()
        parent_entries = _read_manifest_entries(root / "artifact_manifest.json")
        listed = {item["filename"] for item in parent_entries}
        if listed != set(mapping_files) - {"artifact_manifest.json"}:
            _fail()
        size, digest = _manifest_self(root / "artifact_manifest.json")
        _add_item(
            items,
            seen,
            root=root,
            scope=SCOPE_OVERALL,
            scope_label="Overall comparison",
            relative="artifact_manifest.json",
            size=size,
            digest=digest,
        )
        for entry in parent_entries:
            relative = entry["filename"]
            if relative.startswith("cases/"):
                continue
            if relative not in parent_files:
                _fail()
            _add_item(
                items,
                seen,
                root=root,
                scope=SCOPE_OVERALL,
                scope_label="Overall comparison",
                relative=relative,
                size=entry["byte_size"],
                digest=entry["sha256"],
            )
        if {item.relative_path for item in items if item.scope == SCOPE_OVERALL} != parent_files:
            _fail()
        for market in display_market_keys(selected):
            child_files, child_dirs = _split_mapping(bound.child_mappings[market])
            if child_dirs:
                _fail()
            label = market_label(market)
            prefix = f"cases/{market}/"
            child_manifest = prefix + "artifact_manifest.json"
            parent_child = next(
                (item for item in parent_entries if item["filename"] == child_manifest),
                None,
            )
            if parent_child is None or child_manifest not in mapping_files:
                _fail()
            _add_item(
                items,
                seen,
                root=root,
                scope=market,
                scope_label=label,
                relative=child_manifest,
                size=parent_child["byte_size"],
                digest=parent_child["sha256"],
            )
            child_entries = _read_manifest_entries(root / "cases" / market / "artifact_manifest.json")
            child_listed = {item["filename"] for item in child_entries}
            if child_listed | {"artifact_manifest.json"} != set(child_files):
                _fail()
            for entry in child_entries:
                _add_item(
                    items,
                    seen,
                    root=root,
                    scope=market,
                    scope_label=label,
                    relative=prefix + entry["filename"],
                    size=entry["byte_size"],
                    digest=entry["sha256"],
                )
            expected_child = {prefix + name for name in child_files}
            actual_child = {item.relative_path for item in items if item.scope == market}
            if actual_child != expected_child:
                _fail()
    else:
        _fail()
    if not items:
        _fail()
    return items


def total_source_size(items: list[InventoryItem]) -> int:
    return sum(item.size for item in items)


def zip_is_within_limit(
    items: list[InventoryItem],
    *,
    limit: int = ZIP_SOURCE_SIZE_LIMIT_BYTES,
) -> bool:
    return total_source_size(items) <= limit


def download_filename(run_id: str, relative_path: str) -> str:
    safe_id = path_safe_name(run_id)
    posix = PurePosixPath(relative_path)
    if posix.parts[0] == "cases" and len(posix.parts) >= 3:
        market = posix.parts[1]
        name = posix.name
        return f"{safe_id}-{path_safe_name(market)}-{path_safe_name(name)}"
    return f"{safe_id}-{path_safe_name(posix.name)}"


def zip_download_filename(run_id: str) -> str:
    return f"{path_safe_name(run_id)}.zip"


def _item_by_relative(items: list[InventoryItem], relative: str) -> InventoryItem:
    matches = [item for item in items if item.relative_path == relative]
    if len(matches) != 1:
        _fail()
    return matches[0]


def read_inventory_file(
    result: Mapping[str, Any] | None,
    relative_path: str,
    *,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> bytes:
    items = build_download_inventory(result, job=job, outputs_root=outputs_root)
    item = _item_by_relative(items, relative_path)
    bound = _bind_result(result, job=job, outputs_root=outputs_root)
    root = bound.directory
    path = require_relative_artifact_path(root, item.relative_path)
    try:
        payload = path.read_bytes()
    except OSError:
        _fail()
    if len(payload) != item.size or sha256_bytes(payload) != item.sha256:
        _fail()
    return payload


def build_result_zip(
    result: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
    size_limit: int = ZIP_SOURCE_SIZE_LIMIT_BYTES,
) -> bytes:
    items = build_download_inventory(result, job=job, outputs_root=outputs_root)
    if not zip_is_within_limit(items, limit=size_limit):
        raise DownloadsError(ERROR_ZIP_TOO_LARGE)
    bound = _bind_result(result, job=job, outputs_root=outputs_root)
    root = bound.directory
    folder = path_safe_name(str(bound.result["job_id"]))
    members: dict[str, bytes] = {}
    for item in items:
        path = require_relative_artifact_path(root, item.relative_path)
        try:
            payload = path.read_bytes()
        except OSError:
            _fail()
        if len(payload) != item.size or sha256_bytes(payload) != item.sha256:
            _fail()
        archive_name = f"{folder}/{item.relative_path}"
        if archive_name in members:
            _fail()
        members[archive_name] = payload
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            archive.writestr(name, members[name])
    return buffer.getvalue()


def zip_members(payload: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                _fail()
            return {name: archive.read(name) for name in names}
    except zipfile.BadZipFile:
        _fail()
    raise DownloadsError(ERROR_DOWNLOADS_BODY)


def quick_download_paths(kind: str) -> tuple[str, str]:
    if kind == KIND_CASE:
        return ("summary.csv", "report.txt")
    if kind == KIND_COMPARISON:
        return ("comparison_summary.csv", "report.txt")
    _fail()
    raise DownloadsError(ERROR_DOWNLOADS_BODY)


def inventory_table_rows(items: list[InventoryItem]) -> dict[str, list[str]]:
    return {
        "Scope": [item.scope_label for item in items],
        "File": [item.relative_path for item in items],
        "Format": [item.format for item in items],
        "Size": [format_bytes(item.size) for item in items],
    }


def scopes_from_inventory(items: list[InventoryItem]) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if item.scope in seen:
            continue
        seen.add(item.scope)
        ordered.append((item.scope, item.scope_label))
    return ordered
