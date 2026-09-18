"""Write and independently validate one completed run directory."""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from stepinbel import __version__
from stepinbel.config import BelgianDeliveryPeriod, UtcPeriod
from stepinbel.data import PublishedDataBundle
from stepinbel.optimizer import DispatchResult
from stepinbel.optimizer.types import (
    ACCOUNTING_TOL_EUR,
    CAPACITY_RESULT_COLUMNS,
    CAPACITY_RESULT_SCHEMA,
    DISPATCH_COLUMNS,
    DT_H,
    ENERGY_TOL_MWH,
    POWER_TOL_MW,
    SIMULTANEOUS_TOL_MW,
    TERMINATION_ACCEPTED_WITHIN_GAP,
    TERMINATION_TIME_LIMIT_FEASIBLE,
    USABLE_MILP_TERMINATIONS,
)
from stepinbel.reporting.constants import (
    ADDITIVE_PERIOD_FIELDS,
    DISPATCH_SCHEMA,
    DISPATCH_SUMMARY_FIELDS,
    MANIFEST_EXCLUDED,
    PERIOD_SUMMARY_COLUMNS,
    PUBLISHED_TABLE_STEMS,
    REQUIRED_ARTIFACTS,
    RUN_ARTIFACT_SCHEMA_VERSION,
    RUN_ARTIFACT_SCHEMA_VERSION_V2,
    RUN_ARTIFACT_SCHEMA_VERSION_V3,
    additive_period_fields_for,
    dispatch_columns_for,
    dispatch_schema_for,
    dispatch_summary_fields_for,
    is_wind_schema,
    period_summary_columns_for,
    published_table_stems_for,
)
from stepinbel.reporting.io import (
    ArtifactError,
    atomic_write_csv,
    atomic_write_json,
    atomic_write_parquet,
    atomic_write_text,
    csv_cell,
    file_entry,
    sha256_file,
)
from stepinbel.reporting.periods import (
    MONTHLY_SUMMARY_SCHEMA,
    MONTHLY_SUMMARY_SCHEMA_V3,
    YEARLY_SUMMARY_SCHEMA,
    YEARLY_SUMMARY_SCHEMA_V3,
    build_period_summaries,
    build_period_summaries_from_tables,
    simultaneous_interval_energy_net_eur,
)
from stepinbel.reporting.report import render_run_report
from stepinbel.workflows.constants import (
    BEHAVIOURAL_BASELINE,
    ELIA_METHODOLOGY,
    RUN_EVENT_SCHEMA_VERSION,
    RUN_STATUS_SCHEMA_VERSION,
)
from stepinbel.workflows.errors import RunRequestError
from stepinbel.workflows.request import CaseRunRequest, case_run_request_from_payload, serialize_case_run_request
from stepinbel.workflows.serialize import dumps_json, format_utc, require_sha256, serialize_config


def _solver_payload(request: CaseRunRequest, result: DispatchResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "solver_name": result.solver.solver_name,
        "solver_version": result.solver.solver_version,
        "package_version": result.solver.package_version,
        "status": result.solver.status,
        "status_raw": result.solver.status_raw,
        "build_s": result.solver.build_s,
        "solve_s": result.solver.solve_s,
        "end_to_end_s": result.solver.end_to_end_s,
        "num_col": result.solver.num_col,
        "num_row": result.solver.num_row,
        "num_nz": result.solver.num_nz,
        "num_integer": result.solver.num_integer,
        "num_binary": result.solver.num_binary,
        "continuous_lp": result.solver.continuous_lp,
        "options": dict(result.solver.options),
        "diagnostics": dict(result.solver.diagnostics),
    }
    if request.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION:
        return payload
    active = request.config.machine_commitment.physically_active()
    diagnostics = result.solver.diagnostics
    if request.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION_V2:
        payload["formulation"] = "milp" if active else "lp"
        payload["termination"] = (
            diagnostics.get("termination", TERMINATION_ACCEPTED_WITHIN_GAP)
            if active
            else "lp_optimum"
        )
        if active:
            payload["requested_mip_gap"] = diagnostics.get(
                "requested_mip_gap", request.solver_options.mip_rel_gap
            )
            payload["achieved_mip_gap"] = diagnostics.get(
                "achieved_mip_gap", diagnostics.get("mip_gap")
            )
            payload["incumbent_objective"] = diagnostics.get(
                "incumbent_objective", result.summary.total_site_revenue_eur
            )
            payload["best_bound"] = diagnostics.get("best_bound", diagnostics.get("mip_dual_bound"))
            payload["time_limit_s"] = diagnostics.get(
                "time_limit_s", request.solver_options.time_limit_s
            )
            payload["node_count"] = diagnostics.get("mip_node_count")
        return payload
    if request.artifact_schema_version != RUN_ARTIFACT_SCHEMA_VERSION_V3:
        raise ArtifactError("unsupported artifact schema version")
    payload["formulation"] = "milp" if active else "lp"
    payload["termination"] = (
        diagnostics.get("termination", TERMINATION_ACCEPTED_WITHIN_GAP) if active else "lp_optimum"
    )
    if active:
        payload["requested_mip_gap"] = diagnostics.get(
            "requested_mip_gap", request.solver_options.mip_rel_gap
        )
        payload["achieved_mip_gap"] = diagnostics.get(
            "achieved_mip_gap", diagnostics.get("mip_gap")
        )
        payload["incumbent_objective"] = diagnostics.get(
            "incumbent_objective", result.summary.total_site_revenue_eur
        )
        payload["best_bound"] = diagnostics.get("best_bound", diagnostics.get("mip_dual_bound"))
        payload["time_limit_s"] = diagnostics.get(
            "time_limit_s", request.solver_options.time_limit_s
        )
        payload["node_count"] = diagnostics.get("mip_node_count")
    else:
        payload["requested_mip_gap"] = None
        payload["achieved_mip_gap"] = None
        payload["incumbent_objective"] = None
        payload["best_bound"] = None
        payload["time_limit_s"] = None
        payload["node_count"] = None
    return payload


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ArtifactError("artifact JSON cannot contain NaN or infinity")
        return float(value)
    if isinstance(value, datetime):
        return format_utc(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise ArtifactError(f"cannot serialize {type(value).__name__} to JSON")


def _summary_fields(result: DispatchResult, *, schema_version: int) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name in dispatch_summary_fields_for(schema_version):
        payload[name] = getattr(result.summary, name)
    return payload


def _period_kind(request: CaseRunRequest) -> str:
    if isinstance(request.config.period, BelgianDeliveryPeriod):
        return "belgian_delivery"
    if isinstance(request.config.period, UtcPeriod):
        return "utc"
    raise ArtifactError("unsupported period kind")


def _dispatch_csv_rows(table: pa.Table, columns: tuple[str, ...]) -> list[list[object]]:
    timestamps = table.column("datetime_utc").to_pylist()
    data_columns = [table.column(name).to_pylist() for name in columns[1:]]
    rows: list[list[object]] = []
    for i, stamp in enumerate(timestamps):
        row: list[object] = [format_utc(stamp)]
        for values in data_columns:
            row.append(values[i])
        rows.append(row)
    return rows


def _capacity_csv_rows(table: pa.Table) -> list[list[object]]:
    columns = [table.column(name).to_pylist() for name in CAPACITY_RESULT_COLUMNS]
    rows: list[list[object]] = []
    for i in range(table.num_rows):
        rows.append([values[i] for values in columns])
    return rows


def _period_csv_rows(table: pa.Table, columns: tuple[str, ...]) -> list[list[object]]:
    data_columns = [table.column(name).to_pylist() for name in columns]
    rows: list[list[object]] = []
    for i in range(table.num_rows):
        rows.append([values[i] for values in data_columns])
    return rows


def _feasibility_payload(result: DispatchResult, schema_version: int) -> dict[str, object]:
    payload: dict[str, object] = {
        "max_bound_residual": result.feasibility.max_bound_residual,
        "max_initial_terminal_residual_mwh": result.feasibility.max_initial_terminal_residual_mwh,
        "max_balance_residual_mwh": result.feasibility.max_balance_residual_mwh,
        "max_ramp_residual_mw": result.feasibility.max_ramp_residual_mw,
        "max_pv_residual_mw": result.feasibility.max_pv_residual_mw,
        "max_grid_residual_mw": result.feasibility.max_grid_residual_mw,
        "max_capacity_residual": result.feasibility.max_capacity_residual,
        "max_interval_accounting_residual_eur": result.feasibility.max_interval_accounting_residual_eur,
        "max_summary_accounting_residual_eur": result.feasibility.max_summary_accounting_residual_eur,
        "max_objective_residual_eur": result.feasibility.max_objective_residual_eur,
        "ok": result.feasibility.ok,
    }
    if is_wind_schema(schema_version):
        payload["max_wind_residual_mw"] = result.feasibility.max_wind_residual_mw
    return payload


def resolved_config_payload(request: CaseRunRequest, result: DispatchResult) -> dict[str, object]:
    window = result.period.window
    asset = request.config.asset
    return {
        "artifact_schema_version": request.artifact_schema_version,
        "run_id": request.run_id,
        "config": serialize_config(request.config, schema_version=request.request_schema_version),
        "resolved_start_utc": format_utc(window.start_utc),
        "resolved_end_exclusive_utc": format_utc(window.end_exclusive_utc),
        "interval_count": result.summary.interval_count,
        "period_kind": _period_kind(request),
        "effective_grid_import_mw": request.config.effective_grid_import_mw(),
        "effective_grid_export_mw": request.config.effective_grid_export_mw(),
        "e_max_source": asset.e_max_source(),
        "e_max_mwh": asset.e_max_mwh(),
        "usable_energy_mwh": asset.usable_energy_mwh(),
        "grid_energy_to_fill_mwh": asset.grid_energy_to_fill_mwh(),
        "round_trip_efficiency": asset.round_trip_efficiency(),
        "charge_duration_h": asset.charge_duration_h(),
        "discharge_duration_h": asset.discharge_duration_h(),
    }


def summary_payload(
    request: CaseRunRequest,
    result: DispatchResult,
    simultaneous_energy_net: float,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "artifact_schema_version": request.artifact_schema_version,
        "run_id": request.run_id,
        "market": request.config.market,
    }
    payload.update(_summary_fields(result, schema_version=request.artifact_schema_version))
    payload["diagnostics"] = {
        "simultaneous_interval_energy_net_eur": simultaneous_energy_net,
    }
    return payload


def metadata_payload(
    request: CaseRunRequest,
    result: DispatchResult,
    bundle: PublishedDataBundle,
) -> dict[str, object]:
    window = result.period.window
    tables: dict[str, object] = {}
    stems = published_table_stems_for(request.artifact_schema_version)
    for stem in stems:
        table = bundle.tables[stem]
        tables[stem] = {
            "expected_sha256": table.expected_sha256,
            "actual_sha256": table.actual_sha256,
            "manifest_row_count": table.manifest_row_count,
            "parquet_row_count": table.parquet_row_count,
        }
    market = request.config.market
    methodology: dict[str, object] | None
    if market == "da":
        methodology = {
            "market": "da",
            "filename": None,
            "sha256": None,
            "statement": (
                "No applicable Elia conformance reference; the accepted PHS "
                "day-ahead behaviour is used."
            ),
        }
    else:
        ref = ELIA_METHODOLOGY[market]
        methodology = {
            "market": market,
            "filename": ref["filename"],
            "sha256": ref["sha256"],
            "statement": ref["statement"],
        }
    return {
        "request_schema_version": request.request_schema_version,
        "artifact_schema_version": request.artifact_schema_version,
        "status_schema_version": RUN_STATUS_SCHEMA_VERSION,
        "event_schema_version": RUN_EVENT_SCHEMA_VERSION,
        "run_id": request.run_id,
        "software_version": __version__,
        "python_version": sys.version.split()[0],
        "market": market,
        "state": "completed",
        "resolved_start_utc": format_utc(window.start_utc),
        "resolved_end_exclusive_utc": format_utc(window.end_exclusive_utc),
        "interval_count": result.summary.interval_count,
        "published_data": {
            "manifest_sha256": bundle.manifest_sha256,
            "data_vintage": bundle.built_at_utc,
            "pipeline_version": bundle.pipeline_version,
            "pipeline_git_commit": bundle.git_commit,
            "tables": tables,
            "required_sources": list(result.period.required_sources),
        },
        "behavioural_baseline": dict(BEHAVIOURAL_BASELINE),
        "methodology_reference": methodology,
        "parity_statement": (
            "These are methodology references, not exact Watts.Happening-conformance claims."
        ),
        "solver": _json_safe(_solver_payload(request, result)),
        "feasibility": _feasibility_payload(result, request.artifact_schema_version),
        "required_artifacts": list(REQUIRED_ARTIFACTS),
    }


def write_run_artifacts(
    run_dir: Path,
    request: CaseRunRequest,
    result: DispatchResult,
    bundle: PublishedDataBundle,
) -> tuple[pa.Table, pa.Table, float]:
    """Write every successful artifact except the manifest and job journal."""
    simultaneous = simultaneous_interval_energy_net_eur(result)
    monthly, yearly = build_period_summaries(result)
    dispatch_columns = dispatch_columns_for(request.artifact_schema_version)
    summary_fields = dispatch_summary_fields_for(request.artifact_schema_version)
    period_columns = period_summary_columns_for(request.artifact_schema_version)
    atomic_write_text(run_dir / "run_request.json", dumps_json(serialize_case_run_request(request)))
    atomic_write_json(run_dir / "resolved_config.json", resolved_config_payload(request, result))
    atomic_write_parquet(run_dir / "dispatch.parquet", result.dispatch)
    atomic_write_csv(
        run_dir / "dispatch.csv",
        dispatch_columns,
        _dispatch_csv_rows(result.dispatch, dispatch_columns),
    )
    capacity = result.capacity_results
    if capacity.schema != CAPACITY_RESULT_SCHEMA:
        capacity = capacity.cast(CAPACITY_RESULT_SCHEMA)
    atomic_write_parquet(run_dir / "capacity.parquet", capacity)
    atomic_write_csv(
        run_dir / "capacity.csv",
        CAPACITY_RESULT_COLUMNS,
        _capacity_csv_rows(capacity),
    )
    atomic_write_json(
        run_dir / "summary.json",
        summary_payload(request, result, simultaneous),
    )
    summary_row = [
        request.run_id,
        request.config.market,
        *[getattr(result.summary, name) for name in summary_fields],
        simultaneous,
    ]
    atomic_write_csv(
        run_dir / "summary.csv",
        ("run_id", "market", *summary_fields, "simultaneous_interval_energy_net_eur"),
        [summary_row],
    )
    atomic_write_parquet(run_dir / "monthly_summary.parquet", monthly)
    atomic_write_csv(
        run_dir / "monthly_summary.csv",
        period_columns,
        _period_csv_rows(monthly, period_columns),
    )
    atomic_write_parquet(run_dir / "yearly_summary.parquet", yearly)
    atomic_write_csv(
        run_dir / "yearly_summary.csv",
        period_columns,
        _period_csv_rows(yearly, period_columns),
    )
    atomic_write_json(run_dir / "run_metadata.json", metadata_payload(request, result, bundle))
    atomic_write_text(
        run_dir / "report.txt",
        render_run_report(
            request,
            result,
            simultaneous_interval_energy_net_eur=simultaneous,
        ),
    )
    return monthly, yearly, simultaneous


def write_artifact_manifest(
    run_dir: Path,
    run_id: str,
    *,
    artifact_schema_version: int = RUN_ARTIFACT_SCHEMA_VERSION,
) -> dict[str, object]:
    names = [name for name in REQUIRED_ARTIFACTS if name != MANIFEST_EXCLUDED]
    entries = [file_entry(run_dir / name) for name in names]
    entries.sort(key=lambda item: str(item["filename"]))
    payload = {
        "artifact_schema_version": artifact_schema_version,
        "run_id": run_id,
        "entries": entries,
    }
    atomic_write_json(run_dir / MANIFEST_EXCLUDED, payload)
    return payload


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


def _read_parquet(path: Path) -> pa.Table:
    try:
        return pq.read_table(path)
    except Exception as exc:
        raise ArtifactError(f"{path.name} is not valid Parquet") from exc


def _close(left: float, right: float, field: str, tol: float = ACCOUNTING_TOL_EUR) -> None:
    if not math.isclose(left, right, rel_tol=0.0, abs_tol=tol):
        raise ArtifactError(f"{field} does not reconcile ({left} != {right})")


def _require_schema(table: pa.Table, expected: pa.Schema, name: str) -> None:
    if tuple(table.column_names) != tuple(expected.names):
        raise ArtifactError(f"{name} columns do not match the contracted schema")
    if table.schema.equals(expected, check_metadata=False):
        return
    for field, want in zip(table.schema, expected, strict=True):
        if field.type != want.type:
            raise ArtifactError(
                f"{name} field {field.name} has type {field.type}, expected {want.type}"
            )


def _require_completed_status(
    status: Mapping[str, Any],
    run_id: str,
    *,
    artifact_schema_version: int,
) -> None:
    if status.get("status_schema_version") != RUN_STATUS_SCHEMA_VERSION:
        raise ArtifactError("run_status.json schema version is wrong")
    if status.get("run_id") != run_id:
        raise ArtifactError("run_status.json run_id does not match")
    if status.get("state") != "completed":
        raise ArtifactError("run_status.json is not completed")
    if status.get("artifact_schema_version") != artifact_schema_version:
        raise ArtifactError("run_status.json artifact schema version is wrong")


def _validate_solver_record(solver: Mapping[str, Any], request: CaseRunRequest) -> None:
    active = request.config.machine_commitment.physically_active()
    if request.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION:
        if solver.get("continuous_lp") is not True:
            raise ArtifactError("run_metadata.json solver is not a continuous LP")
        if int(solver.get("num_integer") or 0) != 0 or int(solver.get("num_binary") or 0) != 0:
            raise ArtifactError("schema-v1 artifacts cannot contain integer variables")
        extra = {"formulation", "termination", "requested_mip_gap", "achieved_mip_gap"}
        if extra.intersection(solver):
            raise ArtifactError("schema-v1 artifacts cannot contain MILP solver fields")
        if active:
            raise ArtifactError("schema-v1 artifacts cannot represent enabled machine-commitment options")
        return
    if request.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION_V2:
        if not active:
            raise ArtifactError("schema-v2 artifacts require enabled machine-commitment options")
        _require_milp_solver_record(solver)
        return
    if request.artifact_schema_version != RUN_ARTIFACT_SCHEMA_VERSION_V3:
        raise ArtifactError("unsupported artifact schema version")
    if not request.config.wind_enabled():
        raise ArtifactError("schema-v3 artifacts require enabled co-located wind")
    for key in (
        "formulation",
        "termination",
        "requested_mip_gap",
        "achieved_mip_gap",
        "incumbent_objective",
        "best_bound",
        "time_limit_s",
        "node_count",
    ):
        if key not in solver:
            raise ArtifactError(f"run_metadata.json solver is missing {key}")
    if active:
        _require_milp_solver_record(solver)
        return
    if solver.get("continuous_lp") is not True:
        raise ArtifactError("run_metadata.json solver is not a continuous LP")
    if int(solver.get("num_integer") or 0) != 0 or int(solver.get("num_binary") or 0) != 0:
        raise ArtifactError("schema-v3 LP artifacts cannot contain integer variables")
    if solver.get("formulation") != "lp":
        raise ArtifactError("run_metadata.json formulation is not lp")
    if solver.get("termination") != "lp_optimum":
        raise ArtifactError("run_metadata.json termination is not lp_optimum")
    for key in (
        "requested_mip_gap",
        "achieved_mip_gap",
        "incumbent_objective",
        "best_bound",
        "time_limit_s",
        "node_count",
    ):
        if solver.get(key) is not None:
            raise ArtifactError(f"schema-v3 LP artifacts must record {key} as null")


def _require_milp_solver_record(solver: Mapping[str, Any]) -> None:
    if solver.get("continuous_lp") is not False:
        raise ArtifactError("run_metadata.json solver is not a mixed-integer model")
    if solver.get("formulation") != "milp":
        raise ArtifactError("run_metadata.json formulation is not milp")
    termination = solver.get("termination")
    if termination not in USABLE_MILP_TERMINATIONS:
        raise ArtifactError("run_metadata.json termination is not a usable MILP result")
    if termination == TERMINATION_ACCEPTED_WITHIN_GAP and solver.get("status") == "time_limit":
        raise ArtifactError("time-limited MILP artifacts cannot claim acceptance within the requested gap")
    if termination == TERMINATION_TIME_LIMIT_FEASIBLE and solver.get("status") == "optimal":
        raise ArtifactError("time-limited MILP artifacts cannot record solver status optimal")
    for key in ("requested_mip_gap", "achieved_mip_gap", "incumbent_objective", "best_bound", "time_limit_s"):
        value = solver.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ArtifactError(f"run_metadata.json solver {key} is not a finite number")
    if int(solver.get("num_integer") or 0) <= 0 and int(solver.get("num_binary") or 0) <= 0:
        raise ArtifactError("run_metadata.json MILP solver has no integer variables")
    if "node_count" not in solver:
        raise ArtifactError("run_metadata.json solver is missing node_count")
    node_count = solver.get("node_count")
    if node_count is not None and (isinstance(node_count, bool) or type(node_count) is not int or node_count < 0):
        raise ArtifactError("run_metadata.json solver node_count is invalid")


def _require_csv_width(path_name: str, headers: list[str], rows: list[list[str]]) -> None:
    width = len(headers)
    for i, row in enumerate(rows):
        if len(row) != width:
            raise ArtifactError(f"{path_name} row {i} does not have {width} cells")


def _csv_matches_table(path: Path, table: pa.Table, timestamp_column: str | None) -> None:
    headers, rows = _read_csv(path)
    if tuple(headers) != tuple(table.column_names):
        raise ArtifactError(f"{path.name} headers do not match Parquet")
    if len(rows) != table.num_rows:
        raise ArtifactError(f"{path.name} row count does not match Parquet")
    _require_csv_width(path.name, headers, rows)
    columns = [table.column(name).to_pylist() for name in table.column_names]
    for i, row in enumerate(rows):
        for j, name in enumerate(table.column_names):
            expected = columns[j][i]
            raw = row[j]
            if expected is None:
                if raw != "":
                    raise ArtifactError(f"{path.name} {name} is not empty at row {i}")
                continue
            if isinstance(expected, str) or name == timestamp_column or isinstance(expected, datetime):
                wanted = expected if isinstance(expected, str) else format_utc(expected)
                if raw != wanted:
                    raise ArtifactError(f"{path.name} timestamp mismatch at row {i}" if not isinstance(expected, str) else f"{path.name} {name} mismatch at row {i}")
                continue
            if isinstance(expected, bool):
                if raw != csv_cell(expected):
                    raise ArtifactError(f"{path.name} {name} mismatch at row {i}")
                continue
            if isinstance(expected, int) and not isinstance(expected, bool):
                try:
                    if int(raw) != int(expected):
                        raise ArtifactError(f"{path.name} {name} mismatch at row {i}")
                except ValueError as exc:
                    raise ArtifactError(f"{path.name} {name} mismatch at row {i}") from exc
                continue
            try:
                actual = float(raw)
            except ValueError as exc:
                raise ArtifactError(f"{path.name} {name} mismatch at row {i}") from exc
            _close(actual, float(expected), f"{path.name}.{name}[{i}]")


def _period_totals(table: pa.Table, fields: tuple[str, ...]) -> dict[str, float]:
    totals = {name: 0.0 for name in fields}
    for name in fields:
        values = table.column(name).to_pylist()
        totals[name] = float(sum(float(item) for item in values))
    return totals


def _tables_match(actual: pa.Table, expected: pa.Table, name: str) -> None:
    if not actual.schema.equals(expected.schema, check_metadata=False):
        raise ArtifactError(f"{name} schema does not match the independent rebuild")
    if actual.num_rows != expected.num_rows:
        raise ArtifactError(f"{name} row count does not match the independent rebuild")
    if tuple(actual.column_names) != tuple(expected.column_names):
        raise ArtifactError(f"{name} columns do not match the independent rebuild")
    for column in actual.column_names:
        left = actual.column(column).to_pylist()
        right = expected.column(column).to_pylist()
        for index, (got, want) in enumerate(zip(left, right, strict=True)):
            if got == want:
                continue
            if isinstance(got, (int, float)) and isinstance(want, (int, float)) and not isinstance(got, bool):
                _close(float(got), float(want), f"{name}.{column}[{index}]")
                continue
            raise ArtifactError(f"{name} {column}[{index}] does not match the independent rebuild")


def _float_col(table: pa.Table, name: str) -> np.ndarray:
    return np.asarray(table.column(name).to_numpy(zero_copy_only=False), dtype=np.float64)


def _require_finite_numeric_table(table: pa.Table, name: str, *, nullable: frozenset[str]) -> None:
    for field in table.schema:
        values = table.column(field.name).to_pylist()
        for i, value in enumerate(values):
            if value is None:
                if field.name in nullable:
                    continue
                if pa.types.is_floating(field.type) or pa.types.is_integer(field.type):
                    raise ArtifactError(f"{name} {field.name}[{i}] is null")
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not math.isfinite(float(value)):
                raise ArtifactError(f"{name} {field.name}[{i}] is not finite")


def _require_dispatch_time_axis(dispatch: pa.Table, start: datetime, end: datetime) -> None:
    expected_n = int((end - start) / timedelta(minutes=15))
    if dispatch.num_rows != expected_n:
        raise ArtifactError("dispatch.parquet row count does not match the frozen period")
    if start + timedelta(minutes=15 * expected_n) != end:
        raise ArtifactError("frozen request window is not an exact quarter-hour grid")
    stamps = dispatch.column("datetime_utc").to_pylist()
    for i, stamp in enumerate(stamps):
        if stamp is None:
            raise ArtifactError(f"dispatch datetime_utc[{i}] is null")
        try:
            actual = stamp.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError, AttributeError) as exc:
            raise ArtifactError(f"dispatch datetime_utc[{i}] is not a UTC timestamp") from exc
        expected = start + timedelta(minutes=15 * i)
        if actual != expected:
            raise ArtifactError(
                f"dispatch datetime_utc[{i}] does not match the frozen request window"
            )


def _require_non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArtifactError(f"{field} must be a non-negative integer")
    if value < 0:
        raise ArtifactError(f"{field} must be a non-negative integer")
    return value


_PUBLISHED_TABLE_FIELDS = (
    "expected_sha256",
    "actual_sha256",
    "manifest_row_count",
    "parquet_row_count",
)


def _derive_from_dispatch(
    dispatch: pa.Table,
    capacity: pa.Table,
    request: CaseRunRequest,
    pv_enabled: bool,
) -> dict[str, float | int]:
    n = dispatch.num_rows
    wind_enabled = request.config.wind_enabled()
    sell = _float_col(dispatch, "market_sell_price_eur_mwh")
    buy = _float_col(dispatch, "market_buy_price_eur_mwh")
    pump = _float_col(dispatch, "p_pump_mw")
    pump_grid = _float_col(dispatch, "p_pump_grid_mw")
    turbine = _float_col(dispatch, "p_turbine_mw")
    energy_net = _float_col(dispatch, "market_energy_net_eur")
    pv_rev = _float_col(dispatch, "pv_revenue_eur")
    total_rev = _float_col(dispatch, "total_revenue_eur")
    pv_available = _float_col(dispatch, "pv_available_mw")
    pv_self = _float_col(dispatch, "pv_to_pump_mw")
    pv_export = _float_col(dispatch, "pv_export_mw")
    pv_curtail = _float_col(dispatch, "pv_curtail_mw")
    prices = dispatch.column("pv_export_price_eur_mwh").to_pylist()
    starts = dispatch.column("reservoir_start_mwh").to_pylist()
    ends = dispatch.column("reservoir_end_mwh").to_pylist()
    if wind_enabled:
        wind_rev = _float_col(dispatch, "wind_revenue_eur")
        wind_available = _float_col(dispatch, "wind_available_mw")
        wind_self = _float_col(dispatch, "wind_to_pump_mw")
        wind_export = _float_col(dispatch, "wind_export_mw")
        wind_curtail = _float_col(dispatch, "wind_curtail_mw")
        wind_prices = dispatch.column("wind_export_price_eur_mwh").to_pylist()
    else:
        zeros = np.zeros(n, dtype=np.float64)
        wind_rev = wind_available = wind_self = wind_export = wind_curtail = zeros
        wind_prices = [None] * n

    energy_gross = DT_H * sell * turbine
    charging = DT_H * buy * pump_grid
    for i in range(n):
        _close(
            float(energy_net[i]),
            float(energy_gross[i] - charging[i]),
            f"dispatch.market_energy_net_eur[{i}]",
        )
        price = prices[i]
        if price is None:
            expected_pv = 0.0
        else:
            expected_pv = float(price) * float(pv_export[i]) * DT_H
        _close(float(pv_rev[i]), expected_pv, f"dispatch.pv_revenue_eur[{i}]")
        wind_price = wind_prices[i]
        if wind_price is None:
            expected_wind = 0.0
        else:
            expected_wind = float(wind_price) * float(wind_export[i]) * DT_H
        _close(float(wind_rev[i]), expected_wind, f"dispatch.wind_revenue_eur[{i}]")
        _close(
            float(total_rev[i]),
            float(energy_net[i] + pv_rev[i] + wind_rev[i]),
            f"dispatch.total_revenue_eur[{i}]",
        )
        if not pv_enabled:
            if price is not None:
                raise ArtifactError("no-PV dispatch must have null PV export prices")
            for field, value in (
                ("pv_available_mw", float(pv_available[i])),
                ("pv_to_pump_mw", float(pv_self[i])),
                ("pv_export_mw", float(pv_export[i])),
                ("pv_curtail_mw", float(pv_curtail[i])),
                ("pv_revenue_eur", float(pv_rev[i])),
            ):
                if value != 0.0:
                    raise ArtifactError(f"no-PV dispatch {field}[{i}] must be exact zero")
        if wind_enabled and wind_price is None:
            raise ArtifactError("wind dispatch must have wind export prices")

    mask = (pump > SIMULTANEOUS_TOL_MW) & (turbine > SIMULTANEOUS_TOL_MW)
    capacity_rev = float(sum(capacity.column("capacity_revenue_eur").to_pylist() or [0.0]))
    asset = request.config.asset
    derived: dict[str, float | int] = {
        "interval_count": n,
        "duration_hours": float(n * DT_H),
        "e_max_mwh": float(asset.e_max_mwh()),
        "reservoir_initial_mwh": float(starts[0]),
        "reservoir_final_mwh": float(ends[-1]),
        "energy_gross_eur": float(energy_gross.sum()),
        "grid_charging_cost_eur": float(charging.sum()),
        "market_energy_net_eur": float(energy_net.sum()),
        "capacity_revenue_eur": capacity_rev,
        "pv_revenue_eur": float(pv_rev.sum()),
        "wind_revenue_eur": float(wind_rev.sum()),
        "pumped_mwh": float(pump.sum() * DT_H),
        "turbined_mwh": float(turbine.sum() * DT_H),
        "pv_available_mwh": float(pv_available.sum() * DT_H),
        "pv_self_consumed_mwh": float(pv_self.sum() * DT_H),
        "pv_exported_mwh": float(pv_export.sum() * DT_H),
        "pv_curtailed_mwh": float(pv_curtail.sum() * DT_H),
        "wind_available_mwh": float(wind_available.sum() * DT_H),
        "wind_self_consumed_mwh": float(wind_self.sum() * DT_H),
        "wind_exported_mwh": float(wind_export.sum() * DT_H),
        "wind_curtailed_mwh": float(wind_curtail.sum() * DT_H),
        "simultaneous_interval_count": int(np.count_nonzero(mask)),
        "simultaneous_pump_mwh": float(pump[mask].sum() * DT_H),
        "simultaneous_turbine_mwh": float(turbine[mask].sum() * DT_H),
        "simultaneous_overlap_mwh": float(np.minimum(pump[mask], turbine[mask]).sum() * DT_H)
        if np.any(mask)
        else 0.0,
        "simultaneous_interval_energy_net_eur": float(energy_net[mask].sum()),
        "n_pump_ramp_up_vars": n if asset.epsilon_pump_mwh_per_mw() > 0.0 else 0,
        "n_turbine_ramp_up_vars": n if asset.epsilon_turbine_mwh_per_mw() > 0.0 else 0,
    }
    derived["total_site_revenue_eur"] = (
        float(derived["market_energy_net_eur"])
        + float(derived["capacity_revenue_eur"])
        + float(derived["pv_revenue_eur"])
        + float(derived["wind_revenue_eur"])
    )
    return derived


def _validate_resolved_config(
    resolved: Mapping[str, Any],
    request: CaseRunRequest,
    derived: Mapping[str, float | int],
) -> None:
    if resolved.get("artifact_schema_version") != request.artifact_schema_version:
        raise ArtifactError("resolved_config.json schema version is wrong")
    if resolved.get("run_id") != request.run_id:
        raise ArtifactError("resolved_config.json run_id does not match")
    if resolved.get("config") != serialize_config(
        request.config, schema_version=request.request_schema_version
    ):
        raise ArtifactError("resolved_config.json config does not match the frozen request")
    start, end = request.config.period.to_utc_bounds()
    if resolved.get("resolved_start_utc") != format_utc(start):
        raise ArtifactError("resolved_config.json start does not match the frozen period")
    if resolved.get("resolved_end_exclusive_utc") != format_utc(end):
        raise ArtifactError("resolved_config.json end does not match the frozen period")
    if resolved.get("period_kind") != _period_kind(request):
        raise ArtifactError("resolved_config.json period kind is wrong")
    if int(resolved["interval_count"]) != int(derived["interval_count"]):
        raise ArtifactError("resolved_config.json interval count does not match dispatch")
    asset = request.config.asset
    expected = {
        "effective_grid_import_mw": request.config.effective_grid_import_mw(),
        "effective_grid_export_mw": request.config.effective_grid_export_mw(),
        "e_max_source": asset.e_max_source(),
        "e_max_mwh": asset.e_max_mwh(),
        "usable_energy_mwh": asset.usable_energy_mwh(),
        "grid_energy_to_fill_mwh": asset.grid_energy_to_fill_mwh(),
        "round_trip_efficiency": asset.round_trip_efficiency(),
        "charge_duration_h": asset.charge_duration_h(),
        "discharge_duration_h": asset.discharge_duration_h(),
    }
    for name, want in expected.items():
        if name == "e_max_source":
            if resolved.get(name) != want:
                raise ArtifactError("resolved_config.json E_max source is wrong")
            continue
        _close(float(resolved[name]), float(want), f"resolved_config.{name}", ENERGY_TOL_MWH)


def _validate_metadata(
    metadata: Mapping[str, Any],
    request: CaseRunRequest,
    derived: Mapping[str, float | int],
    summary: Mapping[str, Any],
) -> None:
    if metadata.get("request_schema_version") != request.request_schema_version:
        raise ArtifactError("run_metadata.json request schema version is wrong")
    if metadata.get("artifact_schema_version") != request.artifact_schema_version:
        raise ArtifactError("run_metadata.json artifact schema version is wrong")
    if metadata.get("status_schema_version") != RUN_STATUS_SCHEMA_VERSION:
        raise ArtifactError("run_metadata.json status schema version is wrong")
    if metadata.get("event_schema_version") != RUN_EVENT_SCHEMA_VERSION:
        raise ArtifactError("run_metadata.json event schema version is wrong")
    if metadata.get("run_id") != request.run_id:
        raise ArtifactError("run_metadata.json run_id does not match")
    if metadata.get("state") != "completed":
        raise ArtifactError("run_metadata.json is not completed")
    if metadata.get("market") != request.config.market:
        raise ArtifactError("run_metadata.json market does not match")
    if metadata.get("published_data", {}).get("manifest_sha256") != request.data_manifest_sha256:
        raise ArtifactError("run_metadata.json manifest hash does not match the frozen request")
    if dict(metadata.get("behavioural_baseline") or {}) != dict(request.behavioural_baseline):
        raise ArtifactError("run_metadata.json baseline does not match the frozen request")
    start, end = request.config.period.to_utc_bounds()
    if metadata.get("resolved_start_utc") != format_utc(start):
        raise ArtifactError("run_metadata.json window does not match the frozen period")
    if metadata.get("resolved_end_exclusive_utc") != format_utc(end):
        raise ArtifactError("run_metadata.json window does not match the frozen period")
    if int(metadata["interval_count"]) != int(derived["interval_count"]):
        raise ArtifactError("run_metadata.json interval count does not match dispatch")
    solver = metadata.get("solver")
    feasibility = metadata.get("feasibility")
    if not isinstance(solver, dict):
        raise ArtifactError("run_metadata.json solver is missing")
    status = solver.get("status")
    if request.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION:
        if status != "optimal":
            raise ArtifactError("solver status is not optimal")
    elif (
        request.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION_V3
        and not request.config.machine_commitment.physically_active()
    ):
        if status != "optimal":
            raise ArtifactError("solver status is not optimal")
        if solver.get("termination") != "lp_optimum":
            raise ArtifactError("run_metadata.json termination is not lp_optimum")
    else:
        termination = solver.get("termination")
        if termination == TERMINATION_ACCEPTED_WITHIN_GAP and status != "optimal":
            raise ArtifactError("accepted MILP artifacts must record solver status optimal")
        if termination == TERMINATION_TIME_LIMIT_FEASIBLE and status != "time_limit":
            raise ArtifactError("time-limited MILP artifacts must record solver status time_limit")
        if termination not in USABLE_MILP_TERMINATIONS:
            raise ArtifactError("run_metadata.json termination is not a usable MILP result")
    if not isinstance(feasibility, dict) or feasibility.get("ok") is not True:
        raise ArtifactError("feasibility report is not ok")
    _validate_solver_record(solver, request)
    methodology = metadata.get("methodology_reference")
    if not isinstance(methodology, dict) or methodology.get("market") != request.config.market:
        raise ArtifactError("run_metadata.json methodology does not match the selected market")
    if request.config.market == "da":
        if methodology.get("filename") is not None or methodology.get("sha256") is not None:
            raise ArtifactError("day-ahead metadata must not claim an Elia conformance reference")
    else:
        ref = ELIA_METHODOLOGY[request.config.market]
        if methodology.get("filename") != ref["filename"] or methodology.get("sha256") != ref["sha256"]:
            raise ArtifactError("run_metadata.json methodology reference is wrong")
    tables = metadata.get("published_data", {}).get("tables")
    stems = published_table_stems_for(request.artifact_schema_version)
    if not isinstance(tables, dict) or set(tables) != set(stems):
        raise ArtifactError("run_metadata.json is missing published table provenance")
    for stem in stems:
        entry = tables[stem]
        if not isinstance(entry, dict):
            raise ArtifactError(f"run_metadata.json table {stem} is invalid")
        if set(entry) != set(_PUBLISHED_TABLE_FIELDS):
            raise ArtifactError(
                f"run_metadata.json table {stem} fields are not the contracted provenance fields"
            )
        try:
            expected = require_sha256(entry["expected_sha256"], f"{stem}.expected_sha256")
            actual = require_sha256(entry["actual_sha256"], f"{stem}.actual_sha256")
        except RunRequestError as exc:
            raise ArtifactError(f"run_metadata.json table {stem} SHA-256 is invalid") from exc
        if expected != actual:
            raise ArtifactError(
                f"run_metadata.json table {stem} expected and actual SHA-256 do not match"
            )
        manifest_rows = _require_non_negative_int(
            entry["manifest_row_count"],
            f"{stem}.manifest_row_count",
        )
        parquet_rows = _require_non_negative_int(
            entry["parquet_row_count"],
            f"{stem}.parquet_row_count",
        )
        if manifest_rows != parquet_rows:
            raise ArtifactError(f"run_metadata.json table {stem} row counts do not match")
    _close(
        float(summary["total_site_revenue_eur"]),
        float(derived["total_site_revenue_eur"]),
        "metadata/summary total",
    )


def _validate_run_artifacts(directory: Path) -> Mapping[str, Path]:
    try:
        children = list(directory.iterdir())
    except OSError as exc:
        raise ArtifactError("run directory cannot be read") from exc
    names = {path.name for path in children}
    leftover = names - set(REQUIRED_ARTIFACTS)
    missing = set(REQUIRED_ARTIFACTS) - names
    if leftover:
        raise ArtifactError(f"unexpected leftover files: {sorted(leftover)[0]}")
    if missing:
        raise ArtifactError(f"missing artifact {sorted(missing)[0]}")
    if any(path.is_dir() for path in children):
        raise ArtifactError("unexpected subdirectory in the run directory")
    if any(name.endswith(".tmp") or name.startswith(".") for name in names):
        raise ArtifactError("temporary files remain in the run directory")

    request_payload = _read_json(directory / "run_request.json")
    try:
        request = case_run_request_from_payload(request_payload)
    except RunRequestError as exc:
        raise ArtifactError("run_request.json is not a valid frozen request") from exc
    run_id = request.run_id

    status = _read_json(directory / "run_status.json")
    _require_completed_status(
        status, run_id, artifact_schema_version=request.artifact_schema_version
    )

    summary = _read_json(directory / "summary.json")
    if summary.get("run_id") != run_id:
        raise ArtifactError("summary.json run_id does not match")
    if summary.get("artifact_schema_version") != request.artifact_schema_version:
        raise ArtifactError("summary.json schema version is wrong")
    if summary.get("market") != request.config.market:
        raise ArtifactError("summary.json market does not match the frozen request")
    diagnostics = summary.get("diagnostics")
    if not isinstance(diagnostics, dict) or "simultaneous_interval_energy_net_eur" not in diagnostics:
        raise ArtifactError("summary.json diagnostics are missing")

    resolved = _read_json(directory / "resolved_config.json")
    metadata = _read_json(directory / "run_metadata.json")

    manifest = _read_json(directory / MANIFEST_EXCLUDED)
    if manifest.get("run_id") != run_id:
        raise ArtifactError("artifact_manifest.json run_id does not match")
    if manifest.get("artifact_schema_version") != request.artifact_schema_version:
        raise ArtifactError("artifact_manifest.json schema version is wrong")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ArtifactError("artifact_manifest.json entries are missing")
    expected_names = [name for name in REQUIRED_ARTIFACTS if name != MANIFEST_EXCLUDED]
    listed = [item.get("filename") for item in entries if isinstance(item, dict)]
    if listed != sorted(expected_names):
        raise ArtifactError("artifact_manifest.json filenames are incomplete or unsorted")
    for item in entries:
        if not isinstance(item, dict):
            raise ArtifactError("artifact_manifest.json entry is invalid")
        name = str(item["filename"])
        path = directory / name
        try:
            size = path.stat().st_size
            digest = sha256_file(path)
        except OSError as exc:
            raise ArtifactError(f"{name} cannot be read") from exc
        if int(item["byte_size"]) != size:
            raise ArtifactError(f"{name} size does not match the manifest")
        if str(item["sha256"]) != digest:
            raise ArtifactError(f"{name} hash does not match the manifest")

    dispatch = _read_parquet(directory / "dispatch.parquet")
    _require_schema(
        dispatch, dispatch_schema_for(request.artifact_schema_version), "dispatch.parquet"
    )
    start, end = request.config.period.to_utc_bounds()
    _require_dispatch_time_axis(dispatch, start, end)
    nullable = {"pv_export_price_eur_mwh"}
    _require_finite_numeric_table(
        dispatch,
        "dispatch.parquet",
        nullable=frozenset(nullable),
    )
    _csv_matches_table(directory / "dispatch.csv", dispatch, "datetime_utc")

    capacity = _read_parquet(directory / "capacity.parquet")
    _require_schema(capacity, CAPACITY_RESULT_SCHEMA, "capacity.parquet")
    _require_finite_numeric_table(capacity, "capacity.parquet", nullable=frozenset())
    _csv_matches_table(directory / "capacity.csv", capacity, None)

    derived = _derive_from_dispatch(dispatch, capacity, request, request.config.pv_enabled())
    _validate_resolved_config(resolved, request, derived)
    _validate_metadata(metadata, request, derived, summary)

    summary_fields = dispatch_summary_fields_for(request.artifact_schema_version)
    for name in summary_fields:
        if name not in summary:
            raise ArtifactError(f"summary.json is missing {name}")
        want = derived[name]
        got = summary[name]
        if isinstance(want, int) and not isinstance(want, bool):
            if int(got) != int(want):
                raise ArtifactError(f"summary.json {name} does not match dispatch")
        else:
            tol = ACCOUNTING_TOL_EUR if name.endswith("_eur") else ENERGY_TOL_MWH
            if name.endswith("_mw") or name.endswith("_vars"):
                tol = POWER_TOL_MW
            _close(float(got), float(want), f"summary.json.{name}", tol)
    _close(
        float(diagnostics["simultaneous_interval_energy_net_eur"]),
        float(derived["simultaneous_interval_energy_net_eur"]),
        "summary.json diagnostics",
    )
    wind_rev = float(summary.get("wind_revenue_eur", 0.0)) if is_wind_schema(
        request.artifact_schema_version
    ) else 0.0
    _close(
        float(summary["total_site_revenue_eur"]),
        float(summary["market_energy_net_eur"])
        + float(summary["capacity_revenue_eur"])
        + float(summary["pv_revenue_eur"])
        + wind_rev,
        "summary.json total identity",
    )

    csv_headers, csv_rows = _read_csv(directory / "summary.csv")
    expected_headers = (
        "run_id",
        "market",
        *summary_fields,
        "simultaneous_interval_energy_net_eur",
    )
    if tuple(csv_headers) != expected_headers or len(csv_rows) != 1:
        raise ArtifactError("summary.csv shape is wrong")
    _require_csv_width("summary.csv", csv_headers, csv_rows)
    csv_row = csv_rows[0]
    if csv_row[0] != run_id or csv_row[1] != summary.get("market"):
        raise ArtifactError("summary.csv identity does not match summary.json")
    for index, name in enumerate(summary_fields, start=2):
        actual = summary[name]
        if isinstance(actual, int) and not isinstance(actual, bool):
            if int(csv_row[index]) != actual:
                raise ArtifactError(f"summary.csv {name} does not match JSON")
        else:
            _close(float(csv_row[index]), float(actual), f"summary.csv.{name}")
    _close(
        float(csv_row[-1]),
        float(diagnostics["simultaneous_interval_energy_net_eur"]),
        "summary.csv.simultaneous_interval_energy_net_eur",
    )

    monthly = _read_parquet(directory / "monthly_summary.parquet")
    yearly = _read_parquet(directory / "yearly_summary.parquet")
    monthly_schema = (
        MONTHLY_SUMMARY_SCHEMA_V3
        if is_wind_schema(request.artifact_schema_version)
        else MONTHLY_SUMMARY_SCHEMA
    )
    yearly_schema = (
        YEARLY_SUMMARY_SCHEMA_V3
        if is_wind_schema(request.artifact_schema_version)
        else YEARLY_SUMMARY_SCHEMA
    )
    _require_schema(monthly, monthly_schema, "monthly_summary.parquet")
    _require_schema(yearly, yearly_schema, "yearly_summary.parquet")
    rebuilt_monthly, rebuilt_yearly = build_period_summaries_from_tables(
        dispatch,
        capacity,
        float(derived["e_max_mwh"]),
    )
    _tables_match(monthly, rebuilt_monthly, "monthly_summary.parquet")
    _tables_match(yearly, rebuilt_yearly, "yearly_summary.parquet")
    _csv_matches_table(directory / "monthly_summary.csv", monthly, None)
    _csv_matches_table(directory / "yearly_summary.csv", yearly, None)
    months = monthly.column("period").to_pylist()
    years = yearly.column("period").to_pylist()
    if months != sorted(months) or years != sorted(years):
        raise ArtifactError("period summary rows are not sorted")

    additive_fields = additive_period_fields_for(request.artifact_schema_version)
    monthly_totals = _period_totals(monthly, additive_fields)
    yearly_totals = _period_totals(yearly, additive_fields)
    for name in additive_fields:
        expected = float(derived[name])
        _close(monthly_totals[name], expected, f"monthly {name}")
        _close(yearly_totals[name], expected, f"yearly {name}")

    mapping = {name: (directory / name).resolve() for name in REQUIRED_ARTIFACTS}
    return MappingProxyType(mapping)


def validate_run_artifacts(run_dir: str | Path) -> Mapping[str, Path]:
    """Independently reconcile a completed run directory."""
    try:
        directory = Path(run_dir)
        if not directory.is_dir():
            raise ArtifactError("run directory does not exist")
        return _validate_run_artifacts(directory)
    except ArtifactError:
        raise
    except Exception as exc:
        raise ArtifactError(str(exc)) from exc
