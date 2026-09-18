"""Write and independently validate one completed asset-sweep directory."""

from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from stepinbel.optimizer.types import HIGHS_RANDOM_SEED, SolverOptions
from stepinbel.reporting.constants import (
    ASSET_SWEEP_INTERPRETATION,
    ASSET_SWEEP_SUMMARY_KEYS,
    ASSET_SWEEP_TOP_LEVEL_FILES,
    MANIFEST_EXCLUDED,
    asset_sweep_row_fields_for,
)
from stepinbel.reporting.io import (
    ArtifactError,
    atomic_write_csv,
    atomic_write_json,
    atomic_write_text,
    csv_cell,
    sha256_file,
)
from stepinbel.reporting.sweep_report import render_asset_sweep_report
from stepinbel.workflows.constants import (
    BEHAVIOURAL_BASELINE,
    RUN_EVENT_SCHEMA_VERSION,
    RUN_STATUS_SCHEMA_VERSION,
)
from stepinbel.workflows.errors import RunRequestError
from stepinbel.workflows.execute import CaseRun
from stepinbel.workflows.request import CaseRunRequest, serialize_case_run_request
from stepinbel.workflows.serialize import format_utc
from stepinbel.workflows.sweep_request import (
    AssetSweepRequest,
    AssetSweepRow,
    asset_sweep_request_from_payload,
    grid_export_limit_mode,
    grid_import_limit_mode,
    row_to_payload,
)

_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ASSET_SWEEP_METADATA_KEYS: tuple[str, ...] = (
    "asset_sweep_request_schema_version",
    "asset_sweep_artifact_schema_version",
    "status_schema_version",
    "event_schema_version",
    "run_id",
    "state",
    "software_version",
    "python_version",
    "market",
    "published_data_manifest_sha256",
    "behavioural_baseline",
    "resolved_start_utc",
    "resolved_end_exclusive_utc",
    "interval_count",
    "candidate_count",
    "canonical_execution_order",
    "grid_import_limit_mode",
    "grid_export_limit_mode",
    "interpretation",
    "children",
)
ASSET_SWEEP_CHILD_METADATA_KEYS: tuple[str, ...] = (
    "candidate_id",
    "candidate_label",
    "run_id",
    "relative_directory",
    "market",
    "artifact_schema_version",
    "artifact_manifest_byte_size",
    "artifact_manifest_sha256",
)
ASSET_SWEEP_MANIFEST_KEYS: tuple[str, ...] = (
    "asset_sweep_artifact_schema_version",
    "run_id",
    "entries",
)
ASSET_SWEEP_MANIFEST_ENTRY_KEYS: tuple[str, ...] = (
    "filename",
    "byte_size",
    "sha256",
)
_INT_ROW_FIELDS = frozenset({"revenue_rank", "interval_count", "simultaneous_interval_count"})
_OPTIONAL_FLOAT_FIELDS = frozenset({"configured_storage_hours", "configured_pond_energy_mwh"})
_STRING_ROW_FIELDS = frozenset(
    {"candidate_id", "candidate_label", "case_run_id", "market", "storage_source", "storage_hours_basis"}
)


def _require_finite_number(value: object, field: str) -> float:
    if type(value) is bool or type(value) not in (int, float):
        raise ArtifactError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ArtifactError(f"{field} must be a finite number")
    return number


def _optional_summary_float(summary: Mapping[str, Any], name: str, prefix: str) -> float:
    if name not in summary:
        return 0.0
    return _require_finite_number(summary[name], f"{prefix}.{name}")


def _require_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise ArtifactError(f"{field} must be an integer")
    return value


def _row_from_child(
    request: AssetSweepRequest,
    candidate_id: str,
    summary: Mapping[str, Any],
    rank: int,
    highest: float,
) -> AssetSweepRow:
    child = request.case_requests[candidate_id]
    config = child.config
    asset = config.asset
    e_max = asset.e_max_mwh()
    if e_max == 0.0:
        raise ArtifactError(f"{candidate_id} e_max_mwh must be positive")
    usable = asset.usable_energy_mwh()
    if usable == 0.0:
        raise ArtifactError(f"{candidate_id} usable_energy_mwh must be positive")
    total = _require_finite_number(summary["total_site_revenue_eur"], f"{candidate_id}.total_site_revenue_eur")
    turbined = _require_finite_number(summary["turbined_mwh"], f"{candidate_id}.turbined_mwh")
    diagnostics = summary.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        raise ArtifactError(f"{candidate_id} summary diagnostics are missing")
    hours = asset.storage_hours
    pond = asset.pond_energy_mwh
    return AssetSweepRow(
        candidate_id=candidate_id,
        candidate_label=request.candidate_labels[candidate_id],
        case_run_id=child.run_id,
        market=request.market,
        revenue_rank=rank,
        difference_from_highest_eur=total - highest,
        power_pump_mw=float(asset.power_pump_mw),
        power_turbine_mw=float(asset.power_turbine_mw),
        storage_source=asset.e_max_source(),
        storage_hours_basis=asset.storage_hours_basis,
        configured_storage_hours=None if hours is None else float(hours),
        configured_pond_energy_mwh=None if pond is None else float(pond),
        e_max_mwh=float(e_max),
        usable_energy_mwh=float(usable),
        grid_energy_to_fill_mwh=float(asset.grid_energy_to_fill_mwh()),
        charge_duration_h=float(asset.charge_duration_h()),
        discharge_duration_h=float(asset.discharge_duration_h()),
        effective_grid_import_mw=float(config.effective_grid_import_mw()),
        effective_grid_export_mw=float(config.effective_grid_export_mw()),
        interval_count=_require_int(summary["interval_count"], f"{candidate_id}.interval_count"),
        duration_hours=_require_finite_number(summary["duration_hours"], f"{candidate_id}.duration_hours"),
        energy_gross_eur=_require_finite_number(summary["energy_gross_eur"], f"{candidate_id}.energy_gross_eur"),
        grid_charging_cost_eur=_require_finite_number(
            summary["grid_charging_cost_eur"],
            f"{candidate_id}.grid_charging_cost_eur",
        ),
        market_energy_net_eur=_require_finite_number(
            summary["market_energy_net_eur"],
            f"{candidate_id}.market_energy_net_eur",
        ),
        capacity_revenue_eur=_require_finite_number(
            summary["capacity_revenue_eur"],
            f"{candidate_id}.capacity_revenue_eur",
        ),
        pv_revenue_eur=_require_finite_number(summary["pv_revenue_eur"], f"{candidate_id}.pv_revenue_eur"),
        total_site_revenue_eur=total,
        period_revenue_per_turbine_mw_eur=total / float(asset.power_turbine_mw),
        period_revenue_per_usable_mwh_eur=total / float(usable),
        pumped_mwh=_require_finite_number(summary["pumped_mwh"], f"{candidate_id}.pumped_mwh"),
        turbined_mwh=turbined,
        full_cycles=turbined / float(e_max),
        pv_available_mwh=_require_finite_number(summary["pv_available_mwh"], f"{candidate_id}.pv_available_mwh"),
        pv_self_consumed_mwh=_require_finite_number(
            summary["pv_self_consumed_mwh"],
            f"{candidate_id}.pv_self_consumed_mwh",
        ),
        pv_exported_mwh=_require_finite_number(summary["pv_exported_mwh"], f"{candidate_id}.pv_exported_mwh"),
        pv_curtailed_mwh=_require_finite_number(summary["pv_curtailed_mwh"], f"{candidate_id}.pv_curtailed_mwh"),
        simultaneous_interval_count=_require_int(
            summary["simultaneous_interval_count"],
            f"{candidate_id}.simultaneous_interval_count",
        ),
        simultaneous_overlap_mwh=_require_finite_number(
            summary["simultaneous_overlap_mwh"],
            f"{candidate_id}.simultaneous_overlap_mwh",
        ),
        simultaneous_interval_energy_net_eur=_require_finite_number(
            diagnostics["simultaneous_interval_energy_net_eur"],
            f"{candidate_id}.simultaneous_interval_energy_net_eur",
        ),
        wind_revenue_eur=_optional_summary_float(summary, "wind_revenue_eur", candidate_id),
        wind_available_mwh=_optional_summary_float(summary, "wind_available_mwh", candidate_id),
        wind_self_consumed_mwh=_optional_summary_float(
            summary, "wind_self_consumed_mwh", candidate_id
        ),
        wind_exported_mwh=_optional_summary_float(summary, "wind_exported_mwh", candidate_id),
        wind_curtailed_mwh=_optional_summary_float(summary, "wind_curtailed_mwh", candidate_id),
    )


def sweep_rows_from_child_summaries(
    request: AssetSweepRequest,
    summaries: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[AssetSweepRow, ...], str]:
    """Rank dedicated-market asset summaries without recalculating dispatch."""
    if set(summaries) != set(request.candidate_order):
        raise ArtifactError("sweep summaries must cover every frozen candidate")
    totals: dict[str, float] = {}
    for candidate_id in request.candidate_order:
        total = summaries[candidate_id]["total_site_revenue_eur"]
        totals[candidate_id] = _require_finite_number(total, f"{candidate_id}.total_site_revenue_eur")
    ranked_ids = sorted(
        request.candidate_order,
        key=lambda candidate_id: (-totals[candidate_id], request.candidate_order.index(candidate_id)),
    )
    highest_id = ranked_ids[0]
    highest = totals[highest_id]
    rows = [
        _row_from_child(request, candidate_id, summaries[candidate_id], rank, highest)
        for rank, candidate_id in enumerate(ranked_ids, start=1)
    ]
    return tuple(rows), highest_id


def _posix_entry(run_dir: Path, relative: str) -> dict[str, object]:
    path = run_dir.joinpath(*relative.split("/"))
    return {
        "filename": relative,
        "byte_size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def sweep_manifest_entries(request: AssetSweepRequest) -> tuple[str, ...]:
    names = [
        *(name for name in ASSET_SWEEP_TOP_LEVEL_FILES if name != MANIFEST_EXCLUDED),
        *(f"cases/{candidate_id}/artifact_manifest.json" for candidate_id in request.candidate_order),
    ]
    return tuple(sorted(names))


def write_asset_sweep_artifact_manifest(run_dir: Path, request: AssetSweepRequest) -> dict[str, object]:
    entries = [_posix_entry(run_dir, name) for name in sweep_manifest_entries(request)]
    payload = {
        "asset_sweep_artifact_schema_version": request.asset_sweep_artifact_schema_version,
        "run_id": request.run_id,
        "entries": entries,
    }
    atomic_write_json(run_dir / MANIFEST_EXCLUDED, payload)
    return payload


def _child_summaries_from_runs(
    request: AssetSweepRequest,
    case_runs: Mapping[str, CaseRun],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for candidate_id in request.candidate_order:
        path = case_runs[candidate_id].directory / "summary.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if type(payload) is not dict:
            raise ArtifactError(f"{candidate_id} summary.json must be an object")
        summaries[candidate_id] = payload
    return summaries


def ranked_rows_from_case_runs(
    request: AssetSweepRequest,
    case_runs: Mapping[str, CaseRun],
) -> tuple[tuple[AssetSweepRow, ...], str]:
    return sweep_rows_from_child_summaries(request, _child_summaries_from_runs(request, case_runs))


def write_asset_sweep_artifacts(
    run_dir: Path,
    request: AssetSweepRequest,
    case_runs: Mapping[str, CaseRun],
    rows: Sequence[AssetSweepRow],
    highest_revenue_candidate_id: str,
) -> None:
    python_version, solver_name, solver_version = _require_write_time_solver_provenance(request, case_runs)
    start, end = next(iter(request.case_requests.values())).config.period.to_utc_bounds()
    first_row = rows[0]
    site = request.case_requests[request.candidate_order[0]].config.site
    highest_label = request.candidate_labels[highest_revenue_candidate_id]
    fields = asset_sweep_row_fields_for(request.asset_sweep_artifact_schema_version)
    summary = {
        "asset_sweep_artifact_schema_version": request.asset_sweep_artifact_schema_version,
        "run_id": request.run_id,
        "state": "completed",
        "market": request.market,
        "resolved_start_utc": format_utc(start),
        "resolved_end_exclusive_utc": format_utc(end),
        "interval_count": first_row.interval_count,
        "duration_hours": float(first_row.duration_hours),
        "candidate_count": len(rows),
        "candidate_order": list(request.candidate_order),
        "highest_revenue_candidate_id": highest_revenue_candidate_id,
        "highest_revenue_candidate_label": highest_label,
        "grid_import_limit_mode": grid_import_limit_mode(site),
        "grid_export_limit_mode": grid_export_limit_mode(site),
        "interpretation": ASSET_SWEEP_INTERPRETATION,
        "rows": [row_to_payload(row, fields) for row in rows],
    }
    atomic_write_json(run_dir / "asset_sweep_summary.json", summary)
    atomic_write_csv(
        run_dir / "asset_sweep_summary.csv",
        fields,
        [[getattr(row, name) for name in fields] for row in rows],
    )
    children: dict[str, object] = {}
    for candidate_id in request.candidate_order:
        manifest = case_runs[candidate_id].directory / MANIFEST_EXCLUDED
        children[candidate_id] = {
            "candidate_id": candidate_id,
            "candidate_label": request.candidate_labels[candidate_id],
            "run_id": case_runs[candidate_id].request.run_id,
            "relative_directory": f"cases/{candidate_id}",
            "market": request.market,
            "artifact_schema_version": case_runs[candidate_id].request.artifact_schema_version,
            "artifact_manifest_byte_size": manifest.stat().st_size,
            "artifact_manifest_sha256": sha256_file(manifest),
        }
    metadata = {
        "asset_sweep_request_schema_version": request.asset_sweep_request_schema_version,
        "asset_sweep_artifact_schema_version": request.asset_sweep_artifact_schema_version,
        "status_schema_version": RUN_STATUS_SCHEMA_VERSION,
        "event_schema_version": RUN_EVENT_SCHEMA_VERSION,
        "run_id": request.run_id,
        "state": "completed",
        "software_version": request.software_version,
        "python_version": python_version,
        "market": request.market,
        "published_data_manifest_sha256": request.data_manifest_sha256,
        "behavioural_baseline": dict(BEHAVIOURAL_BASELINE),
        "resolved_start_utc": format_utc(start),
        "resolved_end_exclusive_utc": format_utc(end),
        "interval_count": first_row.interval_count,
        "candidate_count": len(request.candidate_order),
        "canonical_execution_order": list(request.candidate_order),
        "grid_import_limit_mode": grid_import_limit_mode(site),
        "grid_export_limit_mode": grid_export_limit_mode(site),
        "interpretation": ASSET_SWEEP_INTERPRETATION,
        "children": children,
    }
    atomic_write_json(run_dir / "asset_sweep_metadata.json", metadata)
    atomic_write_text(
        run_dir / "report.txt",
        render_asset_sweep_report(
            request,
            rows,
            highest_revenue_candidate_id=highest_revenue_candidate_id,
            resolved_start_utc=start,
            resolved_end_exclusive_utc=end,
            solver_name=solver_name,
            solver_version=solver_version,
        ),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"{path.name} is not valid JSON") from exc
    if type(payload) is not dict:
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
    if actual is None and expected is None:
        return
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


def _expected_highs_options(solver_options: SolverOptions, *, is_mip: bool) -> dict[str, object]:
    if type(solver_options.detailed_output) is not bool:
        raise ArtifactError("frozen solver detailed_output must be a bool")
    expected: dict[str, object] = {
        "output_flag": solver_options.detailed_output,
        "log_to_console": solver_options.detailed_output,
        "random_seed": HIGHS_RANDOM_SEED,
        "solver": "choose",
        "presolve": "on",
    }
    if not is_mip:
        return expected
    expected["mip_rel_gap"] = float(solver_options.mip_rel_gap)
    expected["threads"] = 0
    expected["time_limit"] = float(solver_options.time_limit_s)
    return expected


def _require_highs_options(
    options: object,
    solver_options: SolverOptions,
    *,
    is_mip: bool,
    field: str,
) -> dict[str, object]:
    if not isinstance(options, Mapping) or isinstance(options, (str, bytes)):
        raise ArtifactError(f"{field} must be an object")
    actual = dict(options)
    _values_match_exactly(actual, _expected_highs_options(solver_options, is_mip=is_mip), field)
    return actual


def _require_highs_solver_fields(
    solver_name: object,
    solver_version: object,
    package_version: object,
    options: object,
    solver_options: SolverOptions,
    *,
    is_mip: bool,
    field: str,
) -> tuple[str, str, str, dict[str, object]]:
    if type(solver_name) is not str or solver_name != "HiGHS":
        raise ArtifactError(f"{field} solver_name must be HiGHS")
    version = _require_non_empty_str(solver_version, f"{field} solver_version")
    package = _require_non_empty_str(package_version, f"{field} package_version")
    option_values = _require_highs_options(
        options,
        solver_options,
        is_mip=is_mip,
        field=f"{field} options",
    )
    return solver_name, version, package, option_values


def _require_persisted_child_provenance(
    child_dir: Path,
    frozen: CaseRunRequest,
    candidate_id: str,
) -> tuple[str, str, str, str, dict[str, object]]:
    metadata = _read_json(child_dir / "run_metadata.json")
    software = metadata.get("software_version")
    if type(software) is not str or software != frozen.software_version:
        raise ArtifactError(
            f"cases/{candidate_id}/run_metadata.json software_version does not match the frozen sweep request"
        )
    python_version = _require_non_empty_str(
        metadata.get("python_version"),
        f"cases/{candidate_id}/run_metadata.json python_version",
    )
    solver = metadata.get("solver")
    if type(solver) is not dict:
        raise ArtifactError(f"cases/{candidate_id}/run_metadata.json solver must be an object")
    name, version, package, options = _require_highs_solver_fields(
        solver.get("solver_name"),
        solver.get("solver_version"),
        solver.get("package_version"),
        solver.get("options"),
        frozen.solver_options,
        is_mip=frozen.config.machine_commitment.physically_active(),
        field=f"cases/{candidate_id}/run_metadata.json solver",
    )
    return python_version, name, version, package, options


def _require_common_child_provenance(
    items: Sequence[tuple[str, str, str, str, dict[str, object]]],
) -> tuple[str, str, str, str, dict[str, object]]:
    if not items:
        raise ArtifactError("child solver provenance is missing")
    first = items[0]
    for item in items[1:]:
        if item[0] != first[0]:
            raise ArtifactError("child python_version values disagree")
        if item[1] != first[1]:
            raise ArtifactError("child solver_name values disagree")
        if item[2] != first[2]:
            raise ArtifactError("child solver_version values disagree")
        if item[3] != first[3]:
            raise ArtifactError("child package_version values disagree")
        _values_match_exactly(item[4], first[4], "child solver options")
    if first[1] != "HiGHS":
        raise ArtifactError("sweep solver_name must be HiGHS")
    return first


def _require_write_time_solver_provenance(
    request: AssetSweepRequest,
    case_runs: Mapping[str, CaseRun],
) -> tuple[str, str, str]:
    persisted: list[tuple[str, str, str, str, dict[str, object]]] = []
    in_memory: list[tuple[str, str, str, dict[str, object]]] = []
    for candidate_id in request.candidate_order:
        frozen = request.case_requests[candidate_id]
        run = case_runs[candidate_id]
        solver = run.result.solver
        in_memory.append(
            _require_highs_solver_fields(
                solver.solver_name,
                solver.solver_version,
                solver.package_version,
                solver.options,
                frozen.solver_options,
                is_mip=frozen.config.machine_commitment.physically_active(),
                field=f"{candidate_id} in-memory solver",
            )
        )
        persisted.append(_require_persisted_child_provenance(run.directory, frozen, candidate_id))
    first_memory = in_memory[0]
    for item in in_memory[1:]:
        if item[0] != first_memory[0]:
            raise ArtifactError("in-memory child solver_name values disagree")
        if item[1] != first_memory[1]:
            raise ArtifactError("in-memory child solver_version values disagree")
        if item[2] != first_memory[2]:
            raise ArtifactError("in-memory child package_version values disagree")
        _values_match_exactly(item[3], first_memory[3], "in-memory child solver options")
    common = _require_common_child_provenance(persisted)
    _values_match_exactly(
        {
            "solver_name": first_memory[0],
            "solver_version": first_memory[1],
            "package_version": first_memory[2],
            "options": first_memory[3],
        },
        {
            "solver_name": common[1],
            "solver_version": common[2],
            "package_version": common[3],
            "options": common[4],
        },
        "in-memory solver provenance",
    )
    return common[0], common[1], common[2]


def _validate_parent_tree(directory: Path, request: AssetSweepRequest) -> None:
    try:
        children = list(directory.iterdir())
    except OSError as exc:
        raise ArtifactError("sweep directory cannot be read") from exc
    names = {path.name for path in children}
    expected = set(ASSET_SWEEP_TOP_LEVEL_FILES) | {"cases"}
    leftover = names - expected
    missing = expected - names
    if leftover:
        raise ArtifactError(f"unexpected leftover files: {sorted(leftover)[0]}")
    if missing:
        raise ArtifactError(f"missing artifact {sorted(missing)[0]}")
    if any(name.endswith(".tmp") or name.startswith(".") for name in names):
        raise ArtifactError("temporary files remain in the sweep directory")
    cases = directory / "cases"
    if not cases.is_dir():
        raise ArtifactError("cases directory is missing")
    case_names = {path.name for path in cases.iterdir()}
    if case_names != set(request.candidate_order):
        raise ArtifactError("cases directory must contain exactly the frozen candidates")
    for candidate_id in request.candidate_order:
        if not (cases / candidate_id).is_dir():
            raise ArtifactError(f"cases/{candidate_id} is not a directory")
    if any(path.is_dir() for path in children if path.name != "cases"):
        raise ArtifactError("unexpected subdirectory in the sweep directory")


def _validate_csv_rows(
    path: Path, rows: Sequence[AssetSweepRow], fields: tuple[str, ...]
) -> None:
    headers, data = _read_csv(path)
    if tuple(headers) != fields:
        raise ArtifactError(f"{path.name} headers do not match the row contract")
    if len(data) != len(rows):
        raise ArtifactError(f"{path.name} row count is wrong")
    width = len(headers)
    for i, row in enumerate(data):
        if len(row) != width:
            raise ArtifactError(f"{path.name} row {i} does not have {width} cells")
        expected = rows[i]
        for j, name in enumerate(fields):
            if row[j] != csv_cell(getattr(expected, name)):
                raise ArtifactError(f"{path.name} {name}[{i}] does not match")


def _validate_asset_sweep_artifacts(directory: Path) -> Mapping[str, Path]:
    payload = _read_json(directory / "asset_sweep_request.json")
    try:
        request = asset_sweep_request_from_payload(payload)
    except RunRequestError as exc:
        raise ArtifactError("asset_sweep_request.json is not a valid frozen request") from exc
    _validate_parent_tree(directory, request)

    status = _read_json(directory / "run_status.json")
    _require_typed_int(status.get("status_schema_version"), RUN_STATUS_SCHEMA_VERSION, "run_status.json schema version")
    artifact_version = status.get("asset_sweep_artifact_schema_version", status.get("artifact_schema_version"))
    _require_typed_int(artifact_version, request.asset_sweep_artifact_schema_version, "run_status.json artifact schema version")
    if type(status.get("run_id")) is not str or status["run_id"] != request.run_id:
        raise ArtifactError("run_status.json run_id does not match")
    if status.get("state") != "completed":
        raise ArtifactError("run_status.json is not completed")

    expected_manifest_names = list(sweep_manifest_entries(request))
    manifest = _read_json(directory / MANIFEST_EXCLUDED)
    _require_exact_keys(manifest, ASSET_SWEEP_MANIFEST_KEYS, "artifact_manifest.json")
    if type(manifest.get("run_id")) is not str or manifest["run_id"] != request.run_id:
        raise ArtifactError("artifact_manifest.json run_id does not match")
    _require_typed_int(
        manifest.get("asset_sweep_artifact_schema_version"),
        request.asset_sweep_artifact_schema_version,
        "artifact_manifest.json schema version",
    )
    entries = manifest.get("entries")
    if type(entries) is not list:
        raise ArtifactError("artifact_manifest.json entries are missing")
    listed = [item.get("filename") if isinstance(item, dict) else None for item in entries]
    if listed != expected_manifest_names:
        raise ArtifactError("artifact_manifest.json filenames are incomplete or unsorted")
    seen: set[str] = set()
    for item in entries:
        if type(item) is not dict:
            raise ArtifactError("artifact_manifest.json entry is invalid")
        _require_exact_keys(item, ASSET_SWEEP_MANIFEST_ENTRY_KEYS, "artifact_manifest.json entry")
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
    child_provenances: list[tuple[str, str, str, str, dict[str, object]]] = []
    start, end = next(iter(request.case_requests.values())).config.period.to_utc_bounds()
    first = request.case_requests[request.candidate_order[0]]
    for candidate_id in request.candidate_order:
        child_dir = directory / "cases" / candidate_id
        validate_run_artifacts(child_dir)
        frozen = request.case_requests[candidate_id]
        child_provenances.append(_require_persisted_child_provenance(child_dir, frozen, candidate_id))
        child_payload = _read_json(child_dir / "run_request.json")
        if child_payload != serialize_case_run_request(frozen):
            raise ArtifactError(f"cases/{candidate_id}/run_request.json does not match the frozen sweep request")
        if frozen.config.market != request.market:
            raise ArtifactError(f"cases/{candidate_id} market identity is wrong")
        if frozen.config.period != first.config.period:
            raise ArtifactError(f"cases/{candidate_id} period does not match the parent")
        if frozen.config.market_case != first.config.market_case:
            raise ArtifactError(f"cases/{candidate_id} market case does not match the parent")
        if frozen.config.site != first.config.site:
            raise ArtifactError(f"cases/{candidate_id} site does not match the parent")
        if frozen.config.machine_commitment != first.config.machine_commitment:
            raise ArtifactError(f"cases/{candidate_id} machine-commitment options do not match the parent")
        if frozen.solver_options != first.solver_options:
            raise ArtifactError(f"cases/{candidate_id} solver options do not match the parent")
        if frozen.data_manifest_sha256 != request.data_manifest_sha256:
            raise ArtifactError(f"cases/{candidate_id} data hash does not match the parent")
        if frozen.software_version != request.software_version:
            raise ArtifactError(f"cases/{candidate_id} software version does not match the parent")
        if frozen.created_at_utc != request.created_at_utc:
            raise ArtifactError(f"cases/{candidate_id} created_at_utc does not match the parent")
        if dict(frozen.behavioural_baseline) != dict(request.behavioural_baseline):
            raise ArtifactError(f"cases/{candidate_id} baseline does not match the parent")
        summary = _read_json(child_dir / "summary.json")
        if summary.get("run_id") != frozen.run_id:
            raise ArtifactError(f"cases/{candidate_id} summary run_id does not match")
        if summary.get("market") != request.market:
            raise ArtifactError(f"cases/{candidate_id} summary market does not match")
        child_summaries[candidate_id] = summary

    common_python, common_solver_name, common_solver_version, _, _ = _require_common_child_provenance(
        child_provenances
    )
    rows, highest = sweep_rows_from_child_summaries(request, child_summaries)
    fields = asset_sweep_row_fields_for(request.asset_sweep_artifact_schema_version)
    for row in rows:
        for name in fields:
            value = getattr(row, name)
            if value is None:
                if name not in _OPTIONAL_FLOAT_FIELDS:
                    raise ArtifactError(f"sweep row {row.candidate_id}.{name} is missing")
                continue
            if name in _STRING_ROW_FIELDS:
                if type(value) is not str or not value:
                    raise ArtifactError(f"sweep row {row.candidate_id}.{name} must be a string")
                continue
            if name in _INT_ROW_FIELDS:
                if type(value) is not int:
                    raise ArtifactError(f"sweep row {row.candidate_id}.{name} must be an integer")
                continue
            if type(value) is bool or type(value) not in (int, float) or not math.isfinite(float(value)):
                raise ArtifactError(f"sweep row {row.candidate_id}.{name} is not finite")

    site = first.config.site
    summary = _read_json(directory / "asset_sweep_summary.json")
    if set(summary) != set(ASSET_SWEEP_SUMMARY_KEYS):
        raise ArtifactError("asset_sweep_summary.json keys are wrong")
    expected_summary = {
        "asset_sweep_artifact_schema_version": request.asset_sweep_artifact_schema_version,
        "run_id": request.run_id,
        "state": "completed",
        "market": request.market,
        "resolved_start_utc": format_utc(start),
        "resolved_end_exclusive_utc": format_utc(end),
        "interval_count": rows[0].interval_count,
        "duration_hours": float(rows[0].duration_hours),
        "candidate_count": len(rows),
        "candidate_order": list(request.candidate_order),
        "highest_revenue_candidate_id": highest,
        "highest_revenue_candidate_label": request.candidate_labels[highest],
        "grid_import_limit_mode": grid_import_limit_mode(site),
        "grid_export_limit_mode": grid_export_limit_mode(site),
        "interpretation": ASSET_SWEEP_INTERPRETATION,
        "rows": [row_to_payload(row, fields) for row in rows],
    }
    _values_match_exactly(summary, expected_summary, "asset_sweep_summary.json")
    _validate_csv_rows(directory / "asset_sweep_summary.csv", rows, fields)

    metadata = _read_json(directory / "asset_sweep_metadata.json")
    _require_exact_keys(metadata, ASSET_SWEEP_METADATA_KEYS, "asset_sweep_metadata.json")
    _require_typed_int(
        metadata.get("asset_sweep_request_schema_version"),
        request.asset_sweep_request_schema_version,
        "asset_sweep_metadata.json request schema version",
    )
    _require_typed_int(
        metadata.get("asset_sweep_artifact_schema_version"),
        request.asset_sweep_artifact_schema_version,
        "asset_sweep_metadata.json artifact schema version",
    )
    _require_typed_int(
        metadata.get("status_schema_version"),
        RUN_STATUS_SCHEMA_VERSION,
        "asset_sweep_metadata.json status schema version",
    )
    _require_typed_int(
        metadata.get("event_schema_version"),
        RUN_EVENT_SCHEMA_VERSION,
        "asset_sweep_metadata.json event schema version",
    )
    if type(metadata.get("run_id")) is not str or metadata["run_id"] != request.run_id:
        raise ArtifactError("asset_sweep_metadata.json identity is wrong")
    if type(metadata.get("state")) is not str or metadata["state"] != "completed":
        raise ArtifactError("asset_sweep_metadata.json identity is wrong")
    if type(metadata.get("software_version")) is not str or metadata["software_version"] != request.software_version:
        raise ArtifactError("asset_sweep_metadata.json software_version does not match")
    if type(metadata.get("python_version")) is not str or metadata["python_version"] != common_python:
        raise ArtifactError("asset_sweep_metadata.json python_version does not match child provenance")
    if type(metadata.get("market")) is not str or metadata["market"] != request.market:
        raise ArtifactError("asset_sweep_metadata.json market does not match")
    if (
        type(metadata.get("published_data_manifest_sha256")) is not str
        or metadata["published_data_manifest_sha256"] != request.data_manifest_sha256
    ):
        raise ArtifactError("asset_sweep_metadata.json manifest hash does not match")
    if type(metadata.get("behavioural_baseline")) is not dict:
        raise ArtifactError("asset_sweep_metadata.json baseline does not match")
    _values_match_exactly(
        metadata["behavioural_baseline"],
        dict(BEHAVIOURAL_BASELINE),
        "asset_sweep_metadata.json baseline",
    )
    if metadata.get("resolved_start_utc") != format_utc(start):
        raise ArtifactError("asset_sweep_metadata.json window does not match")
    if metadata.get("resolved_end_exclusive_utc") != format_utc(end):
        raise ArtifactError("asset_sweep_metadata.json window does not match")
    _require_typed_int(metadata.get("interval_count"), rows[0].interval_count, "asset_sweep_metadata.json interval_count")
    _require_typed_int(
        metadata.get("candidate_count"),
        len(request.candidate_order),
        "asset_sweep_metadata.json candidate_count",
    )
    order = metadata.get("canonical_execution_order")
    if type(order) is not list or order != list(request.candidate_order):
        raise ArtifactError("asset_sweep_metadata.json execution order is wrong")
    if metadata.get("grid_import_limit_mode") != grid_import_limit_mode(site):
        raise ArtifactError("asset_sweep_metadata.json import mode is wrong")
    if metadata.get("grid_export_limit_mode") != grid_export_limit_mode(site):
        raise ArtifactError("asset_sweep_metadata.json export mode is wrong")
    if metadata.get("interpretation") != ASSET_SWEEP_INTERPRETATION:
        raise ArtifactError("asset_sweep_metadata.json interpretation is wrong")
    children = metadata.get("children")
    if type(children) is not dict or set(children) != set(request.candidate_order):
        raise ArtifactError("asset_sweep_metadata.json children are incomplete")
    for candidate_id in request.candidate_order:
        record = children[candidate_id]
        if type(record) is not dict:
            raise ArtifactError(f"asset_sweep_metadata.json children[{candidate_id}] is invalid")
        _require_exact_keys(
            record,
            ASSET_SWEEP_CHILD_METADATA_KEYS,
            f"asset_sweep_metadata.json children[{candidate_id}]",
        )
        manifest_path = directory / "cases" / candidate_id / MANIFEST_EXCLUDED
        digest = sha256_file(manifest_path)
        size = manifest_path.stat().st_size
        expected_child = {
            "candidate_id": candidate_id,
            "candidate_label": request.candidate_labels[candidate_id],
            "run_id": request.case_requests[candidate_id].run_id,
            "relative_directory": f"cases/{candidate_id}",
            "market": request.market,
            "artifact_schema_version": request.case_requests[candidate_id].artifact_schema_version,
            "artifact_manifest_byte_size": size,
            "artifact_manifest_sha256": digest,
        }
        _require_typed_int(
            record["artifact_schema_version"],
            request.case_requests[candidate_id].artifact_schema_version,
            f"asset_sweep_metadata.json children[{candidate_id}] schema version",
        )
        size_value = _require_non_negative_int(
            record["artifact_manifest_byte_size"],
            f"asset_sweep_metadata.json children[{candidate_id}] manifest size",
        )
        digest_value = _require_lowercase_sha256(
            record["artifact_manifest_sha256"],
            f"asset_sweep_metadata.json children[{candidate_id}] manifest digest",
        )
        if size_value != size:
            raise ArtifactError(f"asset_sweep_metadata.json children[{candidate_id}] manifest size is wrong")
        if digest_value != digest:
            raise ArtifactError(f"asset_sweep_metadata.json children[{candidate_id}] manifest digest is wrong")
        _values_match_exactly(
            record,
            expected_child,
            f"asset_sweep_metadata.json children[{candidate_id}]",
        )

    solver_name, solver_version = common_solver_name, common_solver_version
    expected_report = render_asset_sweep_report(
        request,
        rows,
        highest_revenue_candidate_id=highest,
        resolved_start_utc=start,
        resolved_end_exclusive_utc=end,
        solver_name=solver_name,
        solver_version=solver_version,
    )
    try:
        actual_report = (directory / "report.txt").read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactError("report.txt cannot be read") from exc
    if actual_report != expected_report:
        raise ArtifactError("report.txt does not match the independent renderer")

    mapping: dict[str, Path] = {name: (directory / name).resolve() for name in ASSET_SWEEP_TOP_LEVEL_FILES}
    mapping["cases"] = (directory / "cases").resolve()
    for candidate_id in request.candidate_order:
        mapping[f"cases/{candidate_id}"] = (directory / "cases" / candidate_id).resolve()
        mapping[f"cases/{candidate_id}/artifact_manifest.json"] = (
            directory / "cases" / candidate_id / MANIFEST_EXCLUDED
        ).resolve()
    return MappingProxyType(mapping)


def validate_asset_sweep_artifacts(run_dir: str | Path) -> Mapping[str, Path]:
    """Independently reconcile a completed dedicated-market asset-sweep directory."""
    try:
        directory = Path(run_dir)
        if not directory.is_dir():
            raise ArtifactError("sweep directory does not exist")
        return _validate_asset_sweep_artifacts(directory)
    except ArtifactError:
        raise
    except Exception as exc:
        raise ArtifactError(str(exc)) from exc
