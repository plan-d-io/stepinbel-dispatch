"""Accept completed live or Demo artifacts only after public validation."""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, NamedTuple

from stepinbel.reporting import (
    ArtifactError,
    validate_market_comparison_artifacts,
    validate_run_artifacts,
)
from stepinbel.workflows import (
    RunRequestError,
    load_case_run_request,
    load_market_comparison_request,
)

from ui.flow import is_exact_int
from ui.services.errors import user_facing_error
from ui.services.jobs import (
    is_path_safe_job_id,
    job_paths,
    load_durable_job,
    load_json_object,
    resolve_trusted_job,
)
from ui.services.paths import (
    CANONICAL_MARKETS,
    DEMO_COMPARISON_DIR,
    DEMO_IDENTITY,
    KIND_CASE,
    KIND_COMPARISON,
)
from ui.services.period import parse_iso_date
from ui.services.snapshot import market_labels

RESULT_SCHEMA_VERSION = 1
SOURCE_LIVE = "live"
SOURCE_DEMO = "demo"
RESULT_RECORD_KEYS = (
    "schema_version",
    "source",
    "kind",
    "job_id",
    "output_directory",
    "markets",
    "period",
    "validated",
)

ERROR_INCOMPLETE = "The completed run has incomplete or incompatible artifacts."
ERROR_PARTIAL = "Partial results are not opened."
ERROR_DEMO = "The saved demonstration artifacts could not be validated."
ERROR_BINDING = "The stored result does not match the exact requested run."
BINDING_RECEIPT_LIMIT = 8
DEMO_STORAGE_DISPLAY = "ui/demo_artifacts/stepinbel_2025_all_markets_pv500/"


def _outputs_root(outputs_root: Path | None) -> Path | None:
    if outputs_root is not None:
        return Path(outputs_root)
    from ui.services.launch import TEST_HOOKS

    hooked = TEST_HOOKS.get("outputs_root")
    return Path(hooked) if hooked is not None else None


def _canonical_result_markets(value: object, *, kind: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("markets")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("markets")
    if len(value) != len(set(value)):
        raise ValueError("markets")
    allowed = set(CANONICAL_MARKETS)
    if any(item not in allowed for item in value):
        raise ValueError("markets")
    expected = [item for item in CANONICAL_MARKETS if item in value]
    if value != expected:
        raise ValueError("markets")
    if kind == KIND_CASE and len(value) != 1:
        raise ValueError("markets")
    if kind == KIND_COMPARISON and len(value) not in {2, 3}:
        raise ValueError("markets")
    return list(value)


def _validate_period(period: object) -> dict[str, str]:
    if not isinstance(period, Mapping) or set(period) != {"start_date", "end_date"}:
        raise ValueError("period")
    start_text = period.get("start_date")
    end_text = period.get("end_date")
    if not isinstance(start_text, str) or not isinstance(end_text, str):
        raise ValueError("period")
    start = parse_iso_date(start_text)
    end = parse_iso_date(end_text)
    if start is None or end is None or end < start:
        raise ValueError("period")
    return {"start_date": start_text, "end_date": end_text}


def result_record(
    *,
    source: str,
    kind: str,
    job_id: str,
    output_directory: str,
    markets: list[str],
    period: Mapping[str, Any] | None,
    validated: bool = True,
) -> dict[str, Any]:
    start = None
    end = None
    if isinstance(period, Mapping):
        start = period.get("start_date")
        end = period.get("end_date")
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "source": source,
        "kind": kind,
        "job_id": job_id,
        "output_directory": output_directory,
        "markets": list(markets),
        "period": {
            "start_date": start,
            "end_date": end,
        },
        "validated": bool(validated),
    }


def validate_result_record(
    result: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        raise ValueError("result")
    if set(result) != set(RESULT_RECORD_KEYS):
        raise ValueError("result fields")
    if not is_exact_int(result.get("schema_version"), RESULT_SCHEMA_VERSION):
        raise ValueError("result schema")
    if result.get("validated") is not True:
        raise ValueError("validated")
    source = result.get("source")
    kind = result.get("kind")
    if source not in {SOURCE_LIVE, SOURCE_DEMO}:
        raise ValueError("source")
    if kind not in {KIND_CASE, KIND_COMPARISON}:
        raise ValueError("kind")
    job_id = result.get("job_id")
    output = result.get("output_directory")
    if not isinstance(output, str) or not output.strip():
        raise ValueError("output")
    markets = _canonical_result_markets(result.get("markets"), kind=kind)
    period = _validate_period(result.get("period"))
    root = _outputs_root(outputs_root)
    if source == SOURCE_DEMO:
        if job_id != DEMO_IDENTITY:
            raise ValueError("demo identity")
        if kind != KIND_COMPARISON:
            raise ValueError("demo kind")
        if markets != list(CANONICAL_MARKETS):
            raise ValueError("demo markets")
        if Path(output).resolve() != DEMO_COMPARISON_DIR.resolve():
            raise ValueError("demo directory")
        if not DEMO_COMPARISON_DIR.is_dir():
            raise ValueError("demo directory")
    else:
        if not is_path_safe_job_id(job_id):
            raise ValueError("job ID")
        derived = job_paths(str(job_id), kind=kind, outputs_root=root)
        resolved_output = Path(output).resolve()
        if resolved_output != derived["output_directory"]:
            raise ValueError("output")
        if not derived["output_directory"].is_dir():
            raise ValueError("output")
        durable = load_durable_job(str(job_id), outputs_root=root)
        if durable["job_id"] != job_id or durable["kind"] != kind:
            raise ValueError("job")
        if durable["markets"] != markets:
            raise ValueError("markets")
        if Path(durable["output_directory"]).resolve() != derived["output_directory"]:
            raise ValueError("output")
        if job is not None:
            trusted = resolve_trusted_job(job, outputs_root=root)
            if (
                trusted["job_id"] != job_id
                or trusted["kind"] != kind
                or trusted["markets"] != markets
            ):
                raise ValueError("job")
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "source": source,
        "kind": kind,
        "job_id": job_id,
        "output_directory": str(Path(output).resolve()),
        "markets": markets,
        "period": period,
        "validated": True,
    }


class BoundResultArtifacts(NamedTuple):
    """Validated result record bound to the exact stored artifact request."""

    result: dict[str, Any]
    directory: Path
    mapping: Mapping[str, Path]
    child_mappings: Mapping[str, Mapping[str, Path]]


class _BindingReceipt(NamedTuple):
    fingerprint: str
    files: tuple[str, ...]
    directories: tuple[str, ...]
    mapping_files: tuple[str, ...]
    mapping_directories: tuple[str, ...]
    child_files: Mapping[str, tuple[str, ...]]


_RECEIPT_LOCK = threading.Lock()
_RECEIPTS: OrderedDict[tuple[object, ...], _BindingReceipt] = OrderedDict()


def _request_period_dates(period: object) -> tuple[str, str]:
    start = getattr(period, "start_date", None)
    end = getattr(period, "end_date_inclusive", None)
    if start is None or end is None:
        raise ValueError("period")
    return str(start.isoformat()), str(end.isoformat())


def _period_matches(period: object, recorded: Mapping[str, str]) -> None:
    start, end = _request_period_dates(period)
    if start != recorded["start_date"] or end != recorded["end_date"]:
        raise ValueError("period")


def clear_binding_receipts() -> None:
    with _RECEIPT_LOCK:
        _RECEIPTS.clear()


def binding_receipt_count() -> int:
    with _RECEIPT_LOCK:
        return len(_RECEIPTS)


def result_folder_display(result: Mapping[str, Any]) -> str:
    source = result.get("source")
    job_id = result.get("job_id")
    if source == SOURCE_DEMO:
        return DEMO_STORAGE_DISPLAY
    if not is_path_safe_job_id(job_id):
        raise ValueError("job ID")
    return f"outputs/{job_id}/"


def _identity_key(validated: Mapping[str, Any]) -> tuple[object, ...]:
    period = validated["period"]
    return (
        str(validated["source"]),
        str(validated["kind"]),
        str(validated["job_id"]),
        str(Path(str(validated["output_directory"])).resolve()),
        tuple(validated["markets"]),
        str(period["start_date"]),
        str(period["end_date"]),
    )


def _posix_key(value: str) -> str:
    if type(value) is not str or not value or "\\" in value or value.startswith("/"):
        raise ValueError("path")
    posix = value.replace("\\", "/")
    if posix != value or ".." in posix.split("/"):
        raise ValueError("path")
    return posix


def _is_within(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        trusted = root.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == trusted or trusted in resolved.parents


def _safe_join(root: Path, relative: str) -> Path:
    posix = _posix_key(relative)
    current = root
    for part in posix.split("/"):
        current = current / part
        if not _is_within(current, root):
            raise ValueError("path")
    try:
        resolved = current.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("path") from exc
    if not _is_within(resolved, root):
        raise ValueError("path")
    return current


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _list_tree(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    try:
        if not root.is_dir() or not _is_within(root, root):
            raise ValueError("directory")
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(dirpath)
            if not _is_within(current, root):
                raise ValueError("symlink")
            rel_dir = current.relative_to(root).as_posix()
            prefix = "" if rel_dir == "." else rel_dir
            for name in dirnames:
                child = current / name
                if not child.is_dir() or not _is_within(child, root):
                    raise ValueError("symlink")
                rel = name if not prefix else f"{prefix}/{name}"
                directories.add(rel)
            for name in filenames:
                child = current / name
                if not child.is_file() or not _is_within(child, root):
                    raise ValueError("symlink")
                rel = name if not prefix else f"{prefix}/{name}"
                files.add(rel)
    except (OSError, ValueError, TypeError):
        raise ValueError("tree") from None
    return files, directories


def _topology_from_mappings(
    mapping: Mapping[str, Path],
    child_mappings: Mapping[str, Mapping[str, Path]],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], dict[str, tuple[str, ...]]]:
    mapping_files: list[str] = []
    mapping_dirs: list[str] = []
    files: set[str] = set()
    directories: set[str] = set()
    for key, path in mapping.items():
        rel = _posix_key(key)
        if path.is_dir():
            mapping_dirs.append(rel)
            directories.add(rel)
        elif path.is_file():
            mapping_files.append(rel)
            files.add(rel)
        else:
            raise ValueError("artifacts")
    child_files: dict[str, tuple[str, ...]] = {}
    for market, child_mapping in child_mappings.items():
        if type(market) is not str or not market:
            raise ValueError("market")
        names: list[str] = []
        prefix = f"cases/{market}"
        directories.add(prefix)
        for key, path in child_mapping.items():
            name = _posix_key(key)
            names.append(name)
            rel = f"{prefix}/{name}"
            if path.is_dir():
                directories.add(rel)
            elif path.is_file():
                files.add(rel)
            else:
                raise ValueError("artifacts")
        child_files[market] = tuple(sorted(names))
    for relative in list(files):
        parts = relative.split("/")
        for index in range(1, len(parts)):
            directories.add("/".join(parts[:index]))
    for relative in list(directories):
        parts = relative.split("/")
        for index in range(1, len(parts)):
            directories.add("/".join(parts[:index]))
    return (
        tuple(sorted(files)),
        tuple(sorted(directories)),
        tuple(sorted(mapping_files)),
        tuple(sorted(mapping_dirs)),
        child_files,
    )


def _fingerprint_tree(root: Path, files: tuple[str, ...], directories: tuple[str, ...]) -> str | None:
    try:
        actual_files, actual_dirs = _list_tree(root)
        if actual_files != set(files) or actual_dirs != set(directories):
            return None
        digest = hashlib.sha256()
        for relative in files:
            path = _safe_join(root, relative)
            if not path.is_file() or not _is_within(path, root):
                return None
            size = int(path.stat().st_size)
            digest.update(f"{relative}\0{size}\0{_sha256_file(path)}\n".encode("utf-8"))
        for relative in directories:
            digest.update(f"dir:{relative}\n".encode("utf-8"))
        return digest.hexdigest()
    except (OSError, TypeError, ValueError):
        return None


def _rebuild_bound(
    validated: dict[str, Any],
    receipt: _BindingReceipt,
) -> BoundResultArtifacts:
    directory = Path(str(validated["output_directory"]))
    mapping: dict[str, Path] = {}
    for relative in receipt.mapping_files:
        mapping[relative] = _safe_join(directory, relative)
    for relative in receipt.mapping_directories:
        mapping[relative] = _safe_join(directory, relative)
    children: dict[str, Mapping[str, Path]] = {}
    for market, names in receipt.child_files.items():
        child: dict[str, Path] = {}
        prefix = f"cases/{market}"
        for name in names:
            child[name] = _safe_join(directory, f"{prefix}/{name}")
        children[market] = MappingProxyType(child)
    return BoundResultArtifacts(
        result=validated,
        directory=directory,
        mapping=MappingProxyType(mapping),
        child_mappings=MappingProxyType(children),
    )


def _store_receipt(key: tuple[object, ...], receipt: _BindingReceipt) -> None:
    with _RECEIPT_LOCK:
        _RECEIPTS[key] = receipt
        _RECEIPTS.move_to_end(key)
        while len(_RECEIPTS) > BINDING_RECEIPT_LIMIT:
            _RECEIPTS.popitem(last=False)


def _semantic_bind(validated: dict[str, Any]) -> BoundResultArtifacts:
    directory = Path(str(validated["output_directory"]))
    kind = str(validated["kind"])
    job_id = str(validated["job_id"])
    markets = list(validated["markets"])
    period = validated["period"]
    if kind == KIND_CASE:
        if len(markets) != 1:
            raise ValueError("markets")
        mapping = validate_run_artifacts(directory)
        if not mapping:
            raise ValueError("artifacts")
        request = load_case_run_request(directory / "run_request.json")
        if request.run_id != job_id:
            raise ValueError("run ID")
        if request.config.market != markets[0]:
            raise ValueError("market")
        _period_matches(request.config.period, period)
        return BoundResultArtifacts(
            result=validated,
            directory=directory,
            mapping=mapping,
            child_mappings=MappingProxyType({}),
        )
    if kind == KIND_COMPARISON:
        mapping = validate_market_comparison_artifacts(directory)
        if not mapping:
            raise ValueError("artifacts")
        request = load_market_comparison_request(directory / "comparison_request.json")
        if request.run_id != job_id:
            raise ValueError("run ID")
        stored_markets = list(request.case_requests)
        if stored_markets != markets:
            raise ValueError("markets")
        child_mappings: dict[str, Mapping[str, Path]] = {}
        for market, child in request.case_requests.items():
            if child.config.market != market:
                raise ValueError("market")
            _period_matches(child.config.period, period)
            child_dir = directory / "cases" / market
            child_mapping = validate_run_artifacts(child_dir)
            if not child_mapping:
                raise ValueError("artifacts")
            child_mappings[market] = child_mapping
        if set(child_mappings) != set(markets):
            raise ValueError("markets")
        return BoundResultArtifacts(
            result=validated,
            directory=directory,
            mapping=mapping,
            child_mappings=MappingProxyType(child_mappings),
        )
    raise ValueError("kind")


def bind_exact_result_artifacts(
    result: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> BoundResultArtifacts:
    """Bind a validated result record to the exact stored run request.

    Public validators establish internal artifact consistency. This helper then
    requires stored request identity, market membership, and period to equal the
    result record. It does not compare the historical request output directory
    with the directory currently being validated. Successful bindings are kept
    as in-memory receipts and reused only when a strong current-tree fingerprint
    still matches.
    """
    try:
        validated = validate_result_record(result, job=job, outputs_root=outputs_root)
        key = _identity_key(validated)
        directory = Path(str(validated["output_directory"]))
        with _RECEIPT_LOCK:
            receipt = _RECEIPTS.get(key)
        if receipt is not None:
            current = _fingerprint_tree(directory, receipt.files, receipt.directories)
            if current is not None and current == receipt.fingerprint:
                with _RECEIPT_LOCK:
                    if key in _RECEIPTS and _RECEIPTS[key] is receipt:
                        _RECEIPTS.move_to_end(key)
                return _rebuild_bound(validated, receipt)
            with _RECEIPT_LOCK:
                stored = _RECEIPTS.get(key)
                if stored is receipt:
                    del _RECEIPTS[key]
        bound = _semantic_bind(validated)
        files, directories, mapping_files, mapping_dirs, child_files = _topology_from_mappings(
            bound.mapping,
            bound.child_mappings,
        )
        fingerprint = _fingerprint_tree(directory, files, directories)
        if fingerprint is None:
            raise ValueError("fingerprint")
        _store_receipt(
            key,
            _BindingReceipt(
                fingerprint=fingerprint,
                files=files,
                directories=directories,
                mapping_files=mapping_files,
                mapping_directories=mapping_dirs,
                child_files=MappingProxyType(child_files),
            ),
        )
        return bound
    except (ArtifactError, RunRequestError, OSError, TypeError, ValueError) as exc:
        raise ValueError(ERROR_BINDING) from exc


def result_is_valid(
    result: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> bool:
    try:
        validate_result_record(result, job=job, outputs_root=outputs_root)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _same_path(left: object, right: object) -> bool:
    try:
        return Path(str(left)).resolve() == Path(str(right)).resolve()
    except (OSError, TypeError, ValueError):
        return False


def accept_live_artifacts(
    job: Mapping[str, Any],
    *,
    outputs_root: Path | None = None,
) -> dict[str, Any]:
    root = _outputs_root(outputs_root)
    try:
        trusted = resolve_trusted_job(job, outputs_root=root)
        output = Path(trusted["output_directory"])
        kind = str(trusted["kind"])
        run_id = str(trusted["job_id"])
        markets = list(trusted["markets"])
        snapshot = load_json_object(Path(trusted["configured_snapshot_path"]))
        period = snapshot.get("period") if isinstance(snapshot.get("period"), Mapping) else None
        if kind == KIND_CASE:
            mapping = validate_run_artifacts(output)
            request = load_case_run_request(output / "run_request.json")
            if request.run_id != run_id:
                raise ArtifactError("run ID does not match the job")
            if not _same_path(request.output_directory, output):
                raise ArtifactError("output directory does not match the job")
            if [request.config.market] != markets:
                raise ArtifactError("selected markets do not match the job")
        elif kind == KIND_COMPARISON:
            mapping = validate_market_comparison_artifacts(output)
            request = load_market_comparison_request(output / "comparison_request.json")
            if request.run_id != run_id:
                raise ArtifactError("run ID does not match the job")
            if not _same_path(request.output_directory, output):
                raise ArtifactError("output directory does not match the job")
            if list(request.case_requests) != markets:
                raise ArtifactError("selected markets do not match the job")
        else:
            raise ArtifactError("unsupported job kind")
        if not mapping:
            raise ArtifactError("artifact mapping is empty")
        record = result_record(
            source=SOURCE_LIVE,
            kind=kind,
            job_id=run_id,
            output_directory=str(output.resolve()),
            markets=markets,
            period=period,
        )
        return validate_result_record(record, job=trusted, outputs_root=root)
    except (ArtifactError, RunRequestError, OSError, TypeError, ValueError) as exc:
        raise ValueError(user_facing_error(exc) or ERROR_INCOMPLETE) from exc


def open_demo_artifacts() -> dict[str, Any]:
    try:
        mapping = validate_market_comparison_artifacts(DEMO_COMPARISON_DIR)
        if not mapping:
            raise ValueError(ERROR_DEMO)
        request = load_market_comparison_request(DEMO_COMPARISON_DIR / "comparison_request.json")
        if not request.case_requests:
            raise ValueError(ERROR_DEMO)
        first = next(iter(request.case_requests.values())).config
        record = result_record(
            source=SOURCE_DEMO,
            kind=KIND_COMPARISON,
            job_id=DEMO_IDENTITY,
            output_directory=str(DEMO_COMPARISON_DIR.resolve()),
            markets=list(request.case_requests),
            period={
                "start_date": first.period.start_date.isoformat(),
                "end_date": first.period.end_date_inclusive.isoformat(),
            },
        )
        return validate_result_record(record)
    except (ArtifactError, RunRequestError, OSError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == ERROR_DEMO:
            raise
        raise ValueError(ERROR_DEMO) from exc


def result_identity_label(result: Mapping[str, Any]) -> str:
    return "Demo" if result.get("source") == SOURCE_DEMO else "Live"


def result_markets_label(result: Mapping[str, Any]) -> str:
    markets = result.get("markets")
    if not isinstance(markets, list):
        return ""
    return market_labels([str(item) for item in markets])
