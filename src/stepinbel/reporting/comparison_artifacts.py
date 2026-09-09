"""Write and independently validate one completed comparison directory."""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from stepinbel.reporting.comparison_report import render_market_comparison_report
from stepinbel.optimizer.types import (
    COMPARISON_MIP_TIME_LIMIT_WARNING,
    TERMINATION_TIME_LIMIT_FEASIBLE,
    USABLE_MILP_TERMINATIONS,
)
from stepinbel.reporting.constants import (
    COMPARISON_INTERPRETATION,
    COMPARISON_MARKETS,
    COMPARISON_ROW_FIELDS,
    comparison_manifest_entries,
    COMPARISON_SUMMARY_KEYS,
    COMPARISON_TOP_LEVEL_FILES,
    MANIFEST_EXCLUDED,
    MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION,
    MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION_V2,
)
from stepinbel.reporting.io import (
    ArtifactError,
    atomic_write_csv,
    atomic_write_json,
    atomic_write_text,
    csv_cell,
    sha256_file,
)

_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMPARISON_METADATA_KEYS: tuple[str, ...] = (
    "comparison_request_schema_version",
    "comparison_artifact_schema_version",
    "status_schema_version",
    "event_schema_version",
    "run_id",
    "state",
    "software_version",
    "python_version",
    "published_data_manifest_sha256",
    "behavioural_baseline",
    "resolved_start_utc",
    "resolved_end_exclusive_utc",
    "interval_count",
    "canonical_execution_order",
    "dedicated_alternatives_statement",
    "children",
)
COMPARISON_METADATA_KEYS_V2: tuple[str, ...] = COMPARISON_METADATA_KEYS + (
    "mip_termination_warning",
)
COMPARISON_CHILD_METADATA_KEYS: tuple[str, ...] = (
    "run_id",
    "relative_directory",
    "market",
    "artifact_schema_version",
    "artifact_manifest_byte_size",
    "artifact_manifest_sha256",
)
COMPARISON_CHILD_METADATA_KEYS_V2: tuple[str, ...] = COMPARISON_CHILD_METADATA_KEYS + (
    "termination",
    "requested_mip_gap",
    "achieved_mip_gap",
)
COMPARISON_MANIFEST_KEYS: tuple[str, ...] = (
    "comparison_artifact_schema_version",
    "run_id",
    "entries",
)
COMPARISON_MANIFEST_ENTRY_KEYS: tuple[str, ...] = (
    "filename",
    "byte_size",
    "sha256",
)
from stepinbel.workflows.comparison_request import (
    MarketComparisonRequest,
    MarketComparisonRow,
    market_comparison_request_from_payload,
)
from stepinbel.workflows.constants import (
    BEHAVIOURAL_BASELINE,
    RUN_EVENT_SCHEMA_VERSION,
    RUN_STATUS_SCHEMA_VERSION,
)
from stepinbel.workflows.errors import RunRequestError
from stepinbel.workflows.execute import CaseRun
from stepinbel.workflows.request import serialize_case_run_request
from stepinbel.workflows.serialize import format_utc


def row_to_payload(row: MarketComparisonRow) -> dict[str, object]:
    return {name: getattr(row, name) for name in COMPARISON_ROW_FIELDS}


def _selected_markets(keys: object, field: str) -> tuple[str, ...]:
    try:
        key_set = set(keys)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ArtifactError(f"{field} must cover dedicated markets") from exc
    if key_set - set(COMPARISON_MARKETS):
        raise ArtifactError(f"{field} keys must be a subset of da, mfrr, and afrr")
    selected = tuple(market for market in COMPARISON_MARKETS if market in key_set)
    if len(selected) not in {2, 3}:
        raise ArtifactError("comparison summaries must cover two or three dedicated markets")
    return selected


def comparison_rows_from_child_summaries(
    summaries: Mapping[str, Mapping[str, Any]],
    case_run_ids: Mapping[str, str],
) -> tuple[tuple[MarketComparisonRow, ...], str]:
    """Rank dedicated-market summaries without recalculating dispatch."""
    selected = _selected_markets(summaries, "comparison summaries")
    if set(case_run_ids) != set(selected):
        raise ArtifactError("comparison summaries must cover two or three dedicated markets")
    totals: dict[str, float] = {}
    for market in selected:
        total = summaries[market]["total_site_revenue_eur"]
        if isinstance(total, bool) or not isinstance(total, (int, float)) or not math.isfinite(float(total)):
            raise ArtifactError(f"{market} total_site_revenue_eur is not finite")
        totals[market] = float(total)
    order = sorted(selected, key=lambda market: (-totals[market], COMPARISON_MARKETS.index(market)))
    highest_market = order[0]
    highest = totals[highest_market]
    rows: list[MarketComparisonRow] = []
    for rank, market in enumerate(order, start=1):
        rows.append(_row_from_summary(market, case_run_ids[market], summaries[market], rank, highest))
    return tuple(rows), highest_market


def _require_finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ArtifactError(f"{field} must be a finite number")
    return number


def _require_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise ArtifactError(f"{field} must be an integer")
    return value


def _row_from_summary(
    market: str,
    case_run_id: str,
    summary: Mapping[str, Any],
    rank: int,
    highest: float,
) -> MarketComparisonRow:
    e_max = _require_finite_number(summary["e_max_mwh"], f"{market}.e_max_mwh")
    if e_max == 0.0:
        raise ArtifactError(f"{market} e_max_mwh must be positive")
    turbined = _require_finite_number(summary["turbined_mwh"], f"{market}.turbined_mwh")
    total = _require_finite_number(summary["total_site_revenue_eur"], f"{market}.total_site_revenue_eur")
    diagnostics = summary.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        raise ArtifactError(f"{market} summary diagnostics are missing")
    return MarketComparisonRow(
        market=market,
        case_run_id=str(case_run_id),
        revenue_rank=rank,
        difference_from_highest_eur=total - highest,
        interval_count=_require_int(summary["interval_count"], f"{market}.interval_count"),
        duration_hours=_require_finite_number(summary["duration_hours"], f"{market}.duration_hours"),
        e_max_mwh=e_max,
        energy_gross_eur=_require_finite_number(summary["energy_gross_eur"], f"{market}.energy_gross_eur"),
        grid_charging_cost_eur=_require_finite_number(
            summary["grid_charging_cost_eur"],
            f"{market}.grid_charging_cost_eur",
        ),
        market_energy_net_eur=_require_finite_number(
            summary["market_energy_net_eur"],
            f"{market}.market_energy_net_eur",
        ),
        capacity_revenue_eur=_require_finite_number(
            summary["capacity_revenue_eur"],
            f"{market}.capacity_revenue_eur",
        ),
        pv_revenue_eur=_require_finite_number(summary["pv_revenue_eur"], f"{market}.pv_revenue_eur"),
        total_site_revenue_eur=total,
        pumped_mwh=_require_finite_number(summary["pumped_mwh"], f"{market}.pumped_mwh"),
        turbined_mwh=turbined,
        full_cycles=turbined / e_max,
        pv_available_mwh=_require_finite_number(summary["pv_available_mwh"], f"{market}.pv_available_mwh"),
        pv_self_consumed_mwh=_require_finite_number(
            summary["pv_self_consumed_mwh"],
            f"{market}.pv_self_consumed_mwh",
        ),
        pv_exported_mwh=_require_finite_number(summary["pv_exported_mwh"], f"{market}.pv_exported_mwh"),
        pv_curtailed_mwh=_require_finite_number(summary["pv_curtailed_mwh"], f"{market}.pv_curtailed_mwh"),
        simultaneous_interval_count=_require_int(
            summary["simultaneous_interval_count"],
            f"{market}.simultaneous_interval_count",
        ),
        simultaneous_overlap_mwh=_require_finite_number(
            summary["simultaneous_overlap_mwh"],
            f"{market}.simultaneous_overlap_mwh",
        ),
        simultaneous_interval_energy_net_eur=_require_finite_number(
            diagnostics["simultaneous_interval_energy_net_eur"],
            f"{market}.simultaneous_interval_energy_net_eur",
        ),
    )


def _posix_entry(run_dir: Path, relative: str) -> dict[str, object]:
    path = run_dir.joinpath(*relative.split("/"))
    return {
        "filename": relative,
        "byte_size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _selected_from_request_file(run_dir: Path) -> tuple[str, ...]:
    payload = _read_json(run_dir / "comparison_request.json")
    cases = payload.get("case_requests")
    if not isinstance(cases, Mapping):
        raise ArtifactError("comparison_request.json case_requests must be an object")
    return _selected_markets(cases, "comparison_request.json case_requests")


def write_comparison_artifact_manifest(run_dir: Path, run_id: str) -> dict[str, object]:
    request_payload = _read_json(run_dir / "comparison_request.json")
    schema_version = request_payload.get(
        "comparison_artifact_schema_version", MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION
    )
    entries = [_posix_entry(run_dir, name) for name in comparison_manifest_entries(_selected_from_request_file(run_dir))]
    payload = {
        "comparison_artifact_schema_version": schema_version,
        "run_id": run_id,
        "entries": entries,
    }
    atomic_write_json(run_dir / MANIFEST_EXCLUDED, payload)
    return payload


def _child_summaries_from_runs(case_runs: Mapping[str, CaseRun]) -> dict[str, dict[str, Any]]:
    selected = _selected_markets(case_runs, "comparison case runs")
    summaries: dict[str, dict[str, Any]] = {}
    for market in selected:
        path = case_runs[market].directory / "summary.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ArtifactError(f"{market} summary.json must be an object")
        summaries[market] = payload
    return summaries


def ranked_rows_from_case_runs(
    case_runs: Mapping[str, CaseRun],
) -> tuple[tuple[MarketComparisonRow, ...], str]:
    selected = _selected_markets(case_runs, "comparison case runs")
    summaries = _child_summaries_from_runs(case_runs)
    ids = {market: case_runs[market].request.run_id for market in selected}
    return comparison_rows_from_child_summaries(summaries, ids)


def _is_comparison_v2(request: MarketComparisonRequest) -> bool:
    return request.comparison_artifact_schema_version == MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION_V2


def _mip_fields_from_diagnostics(diagnostics: Mapping[str, Any]) -> dict[str, object]:
    requested = diagnostics.get("requested_mip_gap")
    achieved = diagnostics.get("achieved_mip_gap", diagnostics.get("mip_gap"))
    return {
        "termination": diagnostics.get("termination"),
        "requested_mip_gap": None if requested is None else float(requested),
        "achieved_mip_gap": None if achieved is None else float(achieved),
    }


def _mip_warning_from_terminations(terminations: Sequence[object]) -> str | None:
    if any(item == TERMINATION_TIME_LIMIT_FEASIBLE for item in terminations):
        return COMPARISON_MIP_TIME_LIMIT_WARNING
    return None


def _mip_warning_from_case_runs(case_runs: Mapping[str, CaseRun]) -> str | None:
    terminations = [
        dict(run.result.solver.diagnostics).get("termination") for run in case_runs.values()
    ]
    return _mip_warning_from_terminations(terminations)


def write_comparison_artifacts(
    run_dir: Path,
    request: MarketComparisonRequest,
    case_runs: Mapping[str, CaseRun],
    rows: Sequence[MarketComparisonRow],
    highest_revenue_market: str,
) -> None:
    start, end = next(iter(request.case_requests.values())).config.period.to_utc_bounds()
    first_row = rows[0]
    summary = {
        "comparison_artifact_schema_version": request.comparison_artifact_schema_version,
        "run_id": request.run_id,
        "state": "completed",
        "resolved_start_utc": format_utc(start),
        "resolved_end_exclusive_utc": format_utc(end),
        "interval_count": first_row.interval_count,
        "duration_hours": first_row.duration_hours,
        "highest_revenue_market": highest_revenue_market,
        "interpretation": COMPARISON_INTERPRETATION,
        "rows": [row_to_payload(row) for row in rows],
    }
    atomic_write_json(run_dir / "comparison_summary.json", summary)
    atomic_write_csv(
        run_dir / "comparison_summary.csv",
        COMPARISON_ROW_FIELDS,
        [[getattr(row, name) for name in COMPARISON_ROW_FIELDS] for row in rows],
    )
    selected = tuple(request.case_requests)
    children: dict[str, object] = {}
    is_v2 = _is_comparison_v2(request)
    warning = _mip_warning_from_case_runs(case_runs) if is_v2 else None
    for market in selected:
        manifest = case_runs[market].directory / MANIFEST_EXCLUDED
        record: dict[str, object] = {
            "run_id": case_runs[market].request.run_id,
            "relative_directory": f"cases/{market}",
            "market": market,
            "artifact_schema_version": case_runs[market].request.artifact_schema_version,
            "artifact_manifest_byte_size": manifest.stat().st_size,
            "artifact_manifest_sha256": sha256_file(manifest),
        }
        if is_v2:
            record.update(_mip_fields_from_diagnostics(case_runs[market].result.solver.diagnostics))
        children[market] = record
    metadata = {
        "comparison_request_schema_version": request.comparison_request_schema_version,
        "comparison_artifact_schema_version": request.comparison_artifact_schema_version,
        "status_schema_version": RUN_STATUS_SCHEMA_VERSION,
        "event_schema_version": RUN_EVENT_SCHEMA_VERSION,
        "run_id": request.run_id,
        "state": "completed",
        "software_version": request.software_version,
        "python_version": sys.version.split()[0],
        "published_data_manifest_sha256": request.data_manifest_sha256,
        "behavioural_baseline": dict(BEHAVIOURAL_BASELINE),
        "resolved_start_utc": format_utc(start),
        "resolved_end_exclusive_utc": format_utc(end),
        "interval_count": first_row.interval_count,
        "canonical_execution_order": list(selected),
        "dedicated_alternatives_statement": COMPARISON_INTERPRETATION,
        "children": children,
    }
    if is_v2:
        metadata["mip_termination_warning"] = warning
    atomic_write_json(run_dir / "comparison_metadata.json", metadata)
    atomic_write_text(
        run_dir / "report.txt",
        render_market_comparison_report(
            request,
            rows,
            highest_revenue_market=highest_revenue_market,
            resolved_start_utc=start,
            resolved_end_exclusive_utc=end,
            mip_termination_warning=warning,
        ),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"{path.name} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ArtifactError(f"{path.name} must be a JSON object")
    return payload


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        raise ArtifactError(f"{path.name} is not valid CSV") from exc
    if not rows:
        raise ArtifactError(f"{path.name} is empty")
    return rows[0], rows[1:]


def _require_manifest_path(relative: object) -> str:
    if type(relative) is not str or not relative:
        raise ArtifactError("manifest path is missing")
    if relative.startswith("/") or relative.startswith("\\") or Path(relative).is_absolute():
        raise ArtifactError("manifest path must be a relative POSIX path")
    if "\\" in relative or relative.startswith("../") or "/../" in relative or relative.endswith("/.."):
        raise ArtifactError("manifest path is unsafe")
    if relative != Path(relative).as_posix() or relative != relative.strip():
        raise ArtifactError("manifest path must be a sorted POSIX relative path")
    return relative


def _require_exact_keys(payload: Mapping[str, Any], expected: Sequence[str], field: str) -> None:
    if set(payload) != set(expected):
        raise ArtifactError(f"{field} keys are wrong")


def _require_typed_int(value: object, expected: int, field: str) -> None:
    if type(value) is not int or value != expected:
        raise ArtifactError(f"{field} does not match the independent rebuild")


def _require_non_negative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ArtifactError(f"{field} must be a non-negative integer")
    return value


def _require_lowercase_sha256(value: object, field: str) -> str:
    if type(value) is not str or not _LOWER_SHA256_RE.fullmatch(value):
        raise ArtifactError(f"{field} is not a lowercase SHA-256 digest")
    return value


def _require_non_empty_str(value: object, field: str) -> str:
    if type(value) is not str or not value:
        raise ArtifactError(f"{field} must be a non-empty string")
    return value


def _values_match_exactly(actual: object, expected: object, field: str) -> None:
    if type(actual) is not type(expected):
        raise ArtifactError(f"{field} does not match the independent rebuild")
    if isinstance(expected, dict):
        _require_exact_keys(actual, tuple(expected), field)
        for key, want in expected.items():
            _values_match_exactly(actual[key], want, f"{field}.{key}")
        return
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise ArtifactError(f"{field} does not match the independent rebuild")
        for index, (got, want) in enumerate(zip(actual, expected, strict=True)):
            _values_match_exactly(got, want, f"{field}[{index}]")
        return
    if actual != expected:
        raise ArtifactError(f"{field} does not match the independent rebuild")


def _validate_parent_tree(directory: Path, markets: tuple[str, ...]) -> None:
    try:
        children = list(directory.iterdir())
    except OSError as exc:
        raise ArtifactError("comparison directory cannot be read") from exc
    names = {path.name for path in children}
    expected = set(COMPARISON_TOP_LEVEL_FILES) | {"cases"}
    leftover = names - expected
    missing = expected - names
    if leftover:
        raise ArtifactError(f"unexpected leftover files: {sorted(leftover)[0]}")
    if missing:
        raise ArtifactError(f"missing artifact {sorted(missing)[0]}")
    if any(name.endswith(".tmp") or name.startswith(".") for name in names):
        raise ArtifactError("temporary files remain in the comparison directory")
    cases = directory / "cases"
    if not cases.is_dir():
        raise ArtifactError("cases directory is missing")
    try:
        case_entries = list(cases.iterdir())
    except OSError as exc:
        raise ArtifactError("cases directory cannot be read") from exc
    case_names = {path.name for path in case_entries}
    if any(name.endswith(".tmp") or name.startswith(".") for name in case_names):
        raise ArtifactError("temporary files remain in the cases directory")
    if case_names != set(markets):
        raise ArtifactError(
            "cases directory must contain exactly " + ", ".join(markets)
        )
    for market in markets:
        if not (cases / market).is_dir():
            raise ArtifactError(f"cases/{market} is not a directory")
    if any(path.is_dir() for path in children if path.name != "cases"):
        raise ArtifactError("unexpected subdirectory in the comparison directory")


def _validate_csv_rows(path: Path, rows: Sequence[MarketComparisonRow]) -> None:
    headers, data = _read_csv(path)
    if tuple(headers) != COMPARISON_ROW_FIELDS:
        raise ArtifactError(f"{path.name} headers do not match the row contract")
    if len(data) != len(rows):
        raise ArtifactError(f"{path.name} row count is wrong")
    width = len(headers)
    for i, row in enumerate(data):
        if len(row) != width:
            raise ArtifactError(f"{path.name} row {i} does not have {width} cells")
        expected = rows[i]
        for j, name in enumerate(COMPARISON_ROW_FIELDS):
            if row[j] != csv_cell(getattr(expected, name)):
                raise ArtifactError(f"{path.name} {name}[{i}] does not match")


def _validate_market_comparison_artifacts(directory: Path) -> Mapping[str, Path]:
    payload = _read_json(directory / "comparison_request.json")
    try:
        request = market_comparison_request_from_payload(payload)
    except RunRequestError as exc:
        raise ArtifactError("comparison_request.json is not a valid frozen request") from exc
    selected = tuple(request.case_requests)
    _validate_parent_tree(directory, selected)
    expected_manifest = comparison_manifest_entries(selected)

    status = _read_json(directory / "run_status.json")
    if status.get("status_schema_version") != RUN_STATUS_SCHEMA_VERSION:
        raise ArtifactError("run_status.json schema version is wrong")
    if status.get("comparison_artifact_schema_version", status.get("artifact_schema_version")) != (
        request.comparison_artifact_schema_version
    ):
        raise ArtifactError("run_status.json artifact schema version is wrong")
    if status.get("run_id") != request.run_id:
        raise ArtifactError("run_status.json run_id does not match")
    if status.get("state") != "completed":
        raise ArtifactError("run_status.json is not completed")

    manifest = _read_json(directory / MANIFEST_EXCLUDED)
    _require_exact_keys(manifest, COMPARISON_MANIFEST_KEYS, "artifact_manifest.json")
    if type(manifest.get("run_id")) is not str or manifest["run_id"] != request.run_id:
        raise ArtifactError("artifact_manifest.json run_id does not match")
    _require_typed_int(
        manifest.get("comparison_artifact_schema_version"),
        request.comparison_artifact_schema_version,
        "artifact_manifest.json schema version",
    )
    entries = manifest.get("entries")
    if type(entries) is not list:
        raise ArtifactError("artifact_manifest.json entries are missing")
    listed = [item.get("filename") if isinstance(item, dict) else None for item in entries]
    if listed != list(expected_manifest):
        raise ArtifactError("artifact_manifest.json filenames are incomplete or unsorted")
    seen: set[str] = set()
    for item in entries:
        if type(item) is not dict:
            raise ArtifactError("artifact_manifest.json entry is invalid")
        _require_exact_keys(item, COMPARISON_MANIFEST_ENTRY_KEYS, "artifact_manifest.json entry")
        name = _require_manifest_path(item["filename"])
        if name in seen:
            raise ArtifactError("artifact_manifest.json contains duplicate paths")
        seen.add(name)
        path = directory.joinpath(*name.split("/"))
        try:
            size = path.stat().st_size
            digest = sha256_file(path)
        except OSError as exc:
            raise ArtifactError(f"{name} cannot be read") from exc
        byte_size = _require_non_negative_int(item["byte_size"], f"{name} byte_size")
        if byte_size != size:
            raise ArtifactError(f"{name} size does not match the manifest")
        digest_value = _require_lowercase_sha256(item["sha256"], f"{name} sha256")
        if digest_value != digest:
            raise ArtifactError(f"{name} hash does not match the manifest")

    from stepinbel.reporting.artifacts import validate_run_artifacts

    child_summaries: dict[str, dict[str, Any]] = {}
    child_ids: dict[str, str] = {}
    start, end = next(iter(request.case_requests.values())).config.period.to_utc_bounds()
    for market in selected:
        child_dir = directory / "cases" / market
        validate_run_artifacts(child_dir)
        frozen = request.case_requests[market]
        child_payload = _read_json(child_dir / "run_request.json")
        if child_payload != serialize_case_run_request(frozen):
            raise ArtifactError(f"cases/{market}/run_request.json does not match the frozen comparison request")
        if frozen.config.market != market:
            raise ArtifactError(f"cases/{market} market identity is wrong")
        if frozen.data_manifest_sha256 != request.data_manifest_sha256:
            raise ArtifactError(f"cases/{market} data hash does not match the parent")
        if dict(frozen.behavioural_baseline) != dict(request.behavioural_baseline):
            raise ArtifactError(f"cases/{market} baseline does not match the parent")
        child_start, child_end = frozen.config.period.to_utc_bounds()
        if child_start != start or child_end != end:
            raise ArtifactError(f"cases/{market} window does not match the shared period")
        summary = _read_json(child_dir / "summary.json")
        if summary.get("run_id") != frozen.run_id:
            raise ArtifactError(f"cases/{market} summary run_id does not match")
        if summary.get("market") != market:
            raise ArtifactError(f"cases/{market} summary market does not match")
        child_summaries[market] = summary
        child_ids[market] = frozen.run_id

    rows, highest = comparison_rows_from_child_summaries(child_summaries, child_ids)
    for row in rows:
        for name in COMPARISON_ROW_FIELDS:
            value = getattr(row, name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not math.isfinite(float(value)):
                    raise ArtifactError(f"comparison row {row.market}.{name} is not finite")

    summary = _read_json(directory / "comparison_summary.json")
    if set(summary) != set(COMPARISON_SUMMARY_KEYS):
        raise ArtifactError("comparison_summary.json keys are wrong")
    expected_summary = {
        "comparison_artifact_schema_version": request.comparison_artifact_schema_version,
        "run_id": request.run_id,
        "state": "completed",
        "resolved_start_utc": format_utc(start),
        "resolved_end_exclusive_utc": format_utc(end),
        "interval_count": rows[0].interval_count,
        "duration_hours": float(rows[0].duration_hours),
        "highest_revenue_market": highest,
        "interpretation": COMPARISON_INTERPRETATION,
        "rows": [row_to_payload(row) for row in rows],
    }
    _values_match_exactly(summary, expected_summary, "comparison_summary.json")
    _validate_csv_rows(directory / "comparison_summary.csv", rows)

    metadata = _read_json(directory / "comparison_metadata.json")
    is_v2 = _is_comparison_v2(request)
    metadata_keys = COMPARISON_METADATA_KEYS_V2 if is_v2 else COMPARISON_METADATA_KEYS
    child_keys = COMPARISON_CHILD_METADATA_KEYS_V2 if is_v2 else COMPARISON_CHILD_METADATA_KEYS
    _require_exact_keys(metadata, metadata_keys, "comparison_metadata.json")
    _require_typed_int(
        metadata.get("comparison_request_schema_version"),
        request.comparison_request_schema_version,
        "comparison_metadata.json request schema version",
    )
    _require_typed_int(
        metadata.get("comparison_artifact_schema_version"),
        request.comparison_artifact_schema_version,
        "comparison_metadata.json artifact schema version",
    )
    _require_typed_int(
        metadata.get("status_schema_version"),
        RUN_STATUS_SCHEMA_VERSION,
        "comparison_metadata.json status schema version",
    )
    _require_typed_int(
        metadata.get("event_schema_version"),
        RUN_EVENT_SCHEMA_VERSION,
        "comparison_metadata.json event schema version",
    )
    if type(metadata.get("run_id")) is not str or metadata["run_id"] != request.run_id:
        raise ArtifactError("comparison_metadata.json identity is wrong")
    if type(metadata.get("state")) is not str or metadata["state"] != "completed":
        raise ArtifactError("comparison_metadata.json identity is wrong")
    if (
        type(metadata.get("software_version")) is not str
        or metadata["software_version"] != request.software_version
    ):
        raise ArtifactError("comparison_metadata.json software_version does not match")
    _require_non_empty_str(metadata.get("python_version"), "comparison_metadata.json python_version")
    if (
        type(metadata.get("published_data_manifest_sha256")) is not str
        or metadata["published_data_manifest_sha256"] != request.data_manifest_sha256
    ):
        raise ArtifactError("comparison_metadata.json manifest hash does not match")
    if type(metadata.get("behavioural_baseline")) is not dict:
        raise ArtifactError("comparison_metadata.json baseline does not match")
    _values_match_exactly(
        metadata["behavioural_baseline"],
        dict(BEHAVIOURAL_BASELINE),
        "comparison_metadata.json baseline",
    )
    if metadata.get("resolved_start_utc") != format_utc(start):
        raise ArtifactError("comparison_metadata.json window does not match")
    if metadata.get("resolved_end_exclusive_utc") != format_utc(end):
        raise ArtifactError("comparison_metadata.json window does not match")
    _require_typed_int(
        metadata.get("interval_count"),
        rows[0].interval_count,
        "comparison_metadata.json interval_count",
    )
    order = metadata.get("canonical_execution_order")
    if type(order) is not list or order != list(selected):
        raise ArtifactError("comparison_metadata.json execution order is wrong")
    if metadata.get("dedicated_alternatives_statement") != COMPARISON_INTERPRETATION:
        raise ArtifactError("comparison_metadata.json alternatives statement is wrong")
    children = metadata.get("children")
    if type(children) is not dict or set(children) != set(selected):
        raise ArtifactError("comparison_metadata.json children are incomplete")
    child_terminations: list[object] = []
    for market in selected:
        record = children[market]
        if type(record) is not dict:
            raise ArtifactError(f"comparison_metadata.json children[{market}] is invalid")
        _require_exact_keys(
            record,
            child_keys,
            f"comparison_metadata.json children[{market}]",
        )
        manifest_path = directory / "cases" / market / MANIFEST_EXCLUDED
        digest = sha256_file(manifest_path)
        size = manifest_path.stat().st_size
        expected_child: dict[str, object] = {
            "run_id": child_ids[market],
            "relative_directory": f"cases/{market}",
            "market": market,
            "artifact_schema_version": request.case_requests[market].artifact_schema_version,
            "artifact_manifest_byte_size": size,
            "artifact_manifest_sha256": digest,
        }
        if is_v2:
            child_solver = _read_json(directory / "cases" / market / "run_metadata.json").get("solver")
            if not isinstance(child_solver, dict):
                raise ArtifactError(f"cases/{market}/run_metadata.json solver is missing")
            termination = child_solver.get("termination")
            if termination not in USABLE_MILP_TERMINATIONS:
                raise ArtifactError(f"cases/{market} termination is not a usable MILP result")
            child_terminations.append(termination)
            expected_child.update(_mip_fields_from_diagnostics(child_solver))
        _require_typed_int(
            record["artifact_schema_version"],
            request.case_requests[market].artifact_schema_version,
            f"comparison_metadata.json children[{market}] schema version",
        )
        size_value = _require_non_negative_int(
            record["artifact_manifest_byte_size"],
            f"comparison_metadata.json children[{market}] manifest size",
        )
        digest_value = _require_lowercase_sha256(
            record["artifact_manifest_sha256"],
            f"comparison_metadata.json children[{market}] manifest digest",
        )
        if size_value != size:
            raise ArtifactError(f"comparison_metadata.json children[{market}] manifest size is wrong")
        if digest_value != digest:
            raise ArtifactError(f"comparison_metadata.json children[{market}] manifest digest is wrong")
        _values_match_exactly(
            record,
            expected_child,
            f"comparison_metadata.json children[{market}]",
        )

    expected_warning = _mip_warning_from_terminations(child_terminations) if is_v2 else None
    if is_v2 and metadata.get("mip_termination_warning") != expected_warning:
        raise ArtifactError("comparison_metadata.json mip_termination_warning is wrong")

    expected_report = render_market_comparison_report(
        request,
        rows,
        highest_revenue_market=highest,
        resolved_start_utc=start,
        resolved_end_exclusive_utc=end,
        mip_termination_warning=expected_warning,
    )
    try:
        actual_report = (directory / "report.txt").read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactError("report.txt cannot be read") from exc
    if actual_report != expected_report:
        raise ArtifactError("report.txt does not match the independent renderer")

    mapping: dict[str, Path] = {
        name: (directory / name).resolve() for name in COMPARISON_TOP_LEVEL_FILES
    }
    mapping["cases"] = (directory / "cases").resolve()
    for market in selected:
        mapping[f"cases/{market}"] = (directory / "cases" / market).resolve()
        mapping[f"cases/{market}/artifact_manifest.json"] = (
            directory / "cases" / market / MANIFEST_EXCLUDED
        ).resolve()
    return MappingProxyType(mapping)


def validate_market_comparison_artifacts(run_dir: str | Path) -> Mapping[str, Path]:
    """Independently reconcile a completed dedicated-market comparison directory."""
    try:
        directory = Path(run_dir)
        if not directory.is_dir():
            raise ArtifactError("comparison directory does not exist")
        return _validate_market_comparison_artifacts(directory)
    except ArtifactError:
        raise
    except Exception as exc:
        raise ArtifactError(str(exc)) from exc
