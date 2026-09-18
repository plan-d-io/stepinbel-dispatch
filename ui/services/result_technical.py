"""Load artifact-backed Technical details. No Streamlit, no solver."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from ui.services.commitment import (
    format_gap_percent,
    format_minutes_phrase,
    termination_label,
)
from ui.services.artifacts import SOURCE_DEMO, bind_exact_result_artifacts
from ui.services.paths import KIND_CASE, KIND_COMPARISON
from ui.services.result_format import (
    display_market_keys,
    format_balancing,
    format_count,
    format_grid,
    format_hours,
    format_mwh,
    format_period,
    format_percent_fraction,
    format_pump_turbine,
    format_pv_capacity,
    format_wind_capacity,
    is_finite_number,
    market_label,
    markets_label,
    require_finite,
    require_int,
)

PV_VALUATION_LABELS = {
    "da": "Day-ahead prices",
    "fixed": "Fixed price",
}
WIND_VALUATION_LABELS = {
    "da": "Day-ahead prices",
    "fixed": "Fixed price",
}
STORAGE_BASIS_LABELS = {
    "discharge_at_rated": "Discharge at rated turbine output",
}
BID_METHOD_LABELS = {
    "historical_quantile": "Historical quantile",
    "fixed_minimum": "Fixed minimum",
}
ERROR_TECHNICAL_TITLE = "Technical details unavailable"
ERROR_TECHNICAL_BODY = "The stored technical information is incomplete or incompatible."
JSON_MAX_BYTES = 1_048_576
EVENTS_MAX_BYTES = 262_144
EVENTS_DISPLAY_LIMIT = 200
TEXT_DISPLAY_LIMIT = 65_536
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
FEASIBILITY_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("max_bound_residual", "Bound residual", ""),
    ("max_initial_terminal_residual_mwh", "Initial/terminal energy residual", "MWh"),
    ("max_balance_residual_mwh", "Energy balance residual", "MWh"),
    ("max_ramp_residual_mw", "Ramp residual", "MW"),
    ("max_pv_residual_mw", "PV residual", "MW"),
    ("max_grid_residual_mw", "Grid residual", "MW"),
    ("max_capacity_residual", "Capacity residual", ""),
    ("max_interval_accounting_residual_eur", "Interval accounting residual", "EUR"),
    ("max_summary_accounting_residual_eur", "Summary accounting residual", "EUR"),
    ("max_objective_residual_eur", "Objective residual", "EUR"),
)
SOLVER_REQUIRED = (
    "solver_name",
    "solver_version",
    "package_version",
    "status",
    "continuous_lp",
    "num_col",
    "num_row",
    "num_nz",
    "num_integer",
    "num_binary",
    "build_s",
    "solve_s",
    "end_to_end_s",
    "diagnostics",
)


class TechnicalError(ValueError):
    """Stored technical information could not be opened for display."""


def _fail() -> None:
    raise TechnicalError(ERROR_TECHNICAL_BODY)


def _as_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail()
    return dict(value)


def _require_str(value: object) -> str:
    if type(value) is not str or not value:
        _fail()
    return value


def _require_bool(value: object) -> bool:
    if value is True or value is False:
        return value
    _fail()
    raise TechnicalError(ERROR_TECHNICAL_BODY)


def _require_hash(value: object) -> str:
    text = _require_str(value)
    if HASH_RE.fullmatch(text) is None:
        _fail()
    return text


def format_residual(value: object, unit: str = "") -> str:
    number = require_finite(value)
    if number == 0.0:
        shown = "0"
    elif abs(number) < 1e-3 or abs(number) >= 1e6:
        shown = f"{number:.6e}"
    else:
        shown = f"{number:.12g}"
        if float(shown) == 0.0:
            shown = f"{number:.6e}"
    return f"{shown} {unit}".strip() if unit else shown


def format_seconds(value: object) -> str:
    number = require_finite(value)
    if number < 0:
        _fail()
    return f"{number:,.3f} s"


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError:
        _fail()
    if len(raw) > JSON_MAX_BYTES:
        _fail()
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail()
    if not isinstance(loaded, dict):
        _fail()
    return loaded


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
    raise TechnicalError(ERROR_TECHNICAL_BODY)


def _child_directory(bound, market: str) -> Path:
    validated = bound.result
    if market not in validated["markets"]:
        _fail()
    kind = str(validated["kind"])
    if kind == KIND_CASE:
        if list(validated["markets"]) != [market]:
            _fail()
        return bound.directory
    if kind == KIND_COMPARISON:
        if market not in bound.child_mappings:
            _fail()
        child = bound.directory / "cases" / market
        if not bound.child_mappings[market]:
            _fail()
        return child
    _fail()
    raise TechnicalError(ERROR_TECHNICAL_BODY)


def _nested(mapping: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = mapping
    for key in keys:
        current = current.get(key) if isinstance(current, Mapping) else None
        if not isinstance(current, Mapping):
            _fail()
    return dict(current)


def _optional_finite(value: object) -> float | None:
    if value is None:
        return None
    try:
        return require_finite(value)
    except ValueError:
        _fail()
    return None


def _hide_paths(message: str) -> str:
    if "\\" in message or ":/" in message or message.startswith("/") or ".." in message:
        return "Details stored in the result files."
    return message


def order_structured_run_log(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [dict(item) for item in rows],
        key=lambda item: (str(item["timestamp"]), int(item["sequence"])),
        reverse=True,
    )


def _read_events(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError:
        _fail()
    if len(raw) > EVENTS_MAX_BYTES:
        _fail()
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        _fail()
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            _fail()
        if not isinstance(payload, Mapping):
            _fail()
        try:
            sequence = require_int(payload.get("sequence"))
            timestamp = _require_str(payload.get("event_time_utc"))
            stage = _require_str(payload.get("stage_key"))
            state = _require_str(payload.get("state"))
            message = _require_str(payload.get("message"))
        except (TypeError, ValueError):
            _fail()
        rows.append(
            {
                "sequence": sequence,
                "timestamp": timestamp,
                "stage": stage,
                "state": state,
                "message": _hide_paths(message),
            }
        )
    rows = order_structured_run_log(rows)
    total = len(rows)
    limited = rows[:EVENTS_DISPLAY_LIMIT]
    return {
        "rows": limited,
        "total": total,
        "limited": total > EVENTS_DISPLAY_LIMIT,
    }


def _read_text(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(TEXT_DISPLAY_LIMIT + 1)
    except OSError:
        _fail()
    truncated = len(text) > TEXT_DISPLAY_LIMIT or size > TEXT_DISPLAY_LIMIT
    if truncated:
        text = text[:TEXT_DISPLAY_LIMIT]
    return {"text": text, "truncated": truncated, "stored_bytes": int(size)}


def _run_status(path: Path, expected_run_id: str) -> dict[str, Any]:
    payload = _read_json_object(path)
    if _require_str(payload.get("run_id")) != expected_run_id:
        _fail()
    if _require_str(payload.get("state")) != "completed":
        _fail()
    started = payload.get("started_at_utc")
    completed = payload.get("completed_at_utc")
    elapsed = payload.get("elapsed_seconds")
    return {
        "run_id": expected_run_id,
        "state": "completed",
        "started_at_utc": _require_str(started) if started is not None else None,
        "completed_at_utc": _require_str(completed) if completed is not None else None,
        "elapsed": format_seconds(elapsed) if elapsed is not None else None,
    }


def _friendly_label(value: object, labels: Mapping[str, str]) -> str:
    text = _require_str(value)
    return labels.get(text, text.replace("_", " "))


def _configured_groups(
    resolved: Mapping[str, Any],
) -> tuple[list[tuple[str, list[tuple[str, str]]]], list[tuple[str, list[tuple[str, str]]]]]:
    config = _nested(resolved, "config")
    asset = _nested(config, "asset")
    site = _nested(config, "site")
    period = _nested(config, "period")
    market_case = _nested(config, "market_case")
    try:
        start = _require_str(period.get("start_date"))
        end = _require_str(period.get("end_date_inclusive"))
        pump = require_finite(asset.get("power_pump_mw"))
        turbine = require_finite(asset.get("power_turbine_mw"))
        eta_pump = require_finite(asset.get("eta_pump"))
        eta_turbine = require_finite(asset.get("eta_turbine"))
        soc_initial = require_finite(asset.get("soc_initial_frac"))
        soc_terminal = require_finite(asset.get("soc_terminal_frac"))
        e_max = require_finite(resolved.get("e_max_mwh"))
        usable = require_finite(resolved.get("usable_energy_mwh"))
        grid_import = require_finite(resolved.get("effective_grid_import_mw"))
        grid_export = require_finite(resolved.get("effective_grid_export_mw"))
        pv_kw = require_finite(site.get("pv_ac_kw"))
        wind_raw = site.get("wind_capacity_kw")
        wind_kw = None if wind_raw is None else require_finite(wind_raw)
        interval_count = require_int(resolved.get("interval_count"))
        round_trip = require_finite(resolved.get("round_trip_efficiency"))
    except (TypeError, ValueError):
        _fail()
    period_rows = [
        ("Simulation period", format_period(start, end)),
        ("Interval count", format_count(interval_count)),
    ]
    asset_rows = [
        ("Pump / turbine", format_pump_turbine(pump, turbine)),
    ]
    storage_hours = asset.get("storage_hours")
    if storage_hours is not None:
        asset_rows.append(("Storage duration", format_hours(storage_hours)))
    asset_rows.extend(
        [
            ("Usable energy", format_mwh(usable)),
            ("Reservoir capacity", format_mwh(e_max)),
            ("Round-trip efficiency", format_percent_fraction(round_trip)),
        ]
    )
    site_rows = [
        ("Grid import / export", format_grid(grid_import, grid_export)),
        ("PV capacity", format_pv_capacity(pv_kw, site.get("pv_region") if pv_kw > 0 else None)),
    ]
    if pv_kw > 0:
        site_rows.append(("PV valuation", _friendly_label(site.get("pv_revenue_mode"), PV_VALUATION_LABELS)))
        price = site.get("pv_fixed_price_eur_mwh")
        if site.get("pv_revenue_mode") == "fixed" and price is not None:
            site_rows.append(("PV fixed export price", f"{require_finite(price):,.2f} EUR/MWh"))
    if wind_kw is not None and wind_kw > 0:
        site_rows.append(
            ("Wind", format_wind_capacity(wind_kw, site.get("wind_profile_id")))
        )
        site_rows.append(
            ("Wind valuation", _friendly_label(site.get("wind_revenue_mode"), WIND_VALUATION_LABELS))
        )
        wind_price = site.get("wind_fixed_price_eur_mwh")
        if site.get("wind_revenue_mode") == "fixed" and wind_price is not None:
            site_rows.append(("Wind fixed export price", f"{require_finite(wind_price):,.2f} EUR/MWh"))
    market_rows = [("Selected market", market_label(_require_str(market_case.get("market"))))]
    if "activation_profile" in market_case:
        market_rows.append(("Balancing strategy", format_balancing(market_case.get("activation_profile"))))
        bid = market_case.get("capacity_bid")
        if isinstance(bid, Mapping):
            kind = bid.get("kind")
            if type(kind) is str and kind:
                market_rows.append(("Bid method", _friendly_label(kind, BID_METHOD_LABELS)))
        coverage = market_case.get("capacity_coverage_hours")
        if coverage is not None:
            market_rows.append(("Capacity coverage", format_hours(coverage)))
        fraction = market_case.get("up_capacity_fraction")
        if fraction is not None:
            market_rows.append(("Upward capacity fraction", format_percent_fraction(fraction)))
    main = [
        ("Period", period_rows),
        ("Asset", asset_rows),
        ("Site", site_rows),
        ("Market", market_rows),
    ]
    commitment = config.get("machine_commitment")
    if isinstance(commitment, Mapping):
        constraint_rows: list[tuple[str, str]] = []
        if commitment.get("fixed_speed_pump") is True:
            constraint_rows.append(("Fixed-speed pump", "Enabled"))
        fraction = commitment.get("turbine_minimum_output_fraction")
        if is_finite_number(fraction) and float(fraction) > 0.0:
            constraint_rows.append(("Minimum turbine output", format_gap_percent(fraction)))
        if commitment.get("forbid_simultaneous_operation") is True:
            constraint_rows.append(("Prevent simultaneous pumping and generation", "Enabled"))
        if constraint_rows:
            main.append(("Machine operating constraints", constraint_rows))
    advanced: list[tuple[str, str]] = [
        ("Resolved start UTC", _require_str(resolved.get("resolved_start_utc"))),
        ("Resolved end exclusive UTC", _require_str(resolved.get("resolved_end_exclusive_utc"))),
        ("Storage-hours basis", _friendly_label(asset.get("storage_hours_basis"), STORAGE_BASIS_LABELS)),
    ]
    pond = asset.get("pond_energy_mwh")
    if pond is not None:
        advanced.append(("Pond energy", format_mwh(pond)))
    advanced.extend(
        [
            ("Pump efficiency", format_percent_fraction(eta_pump)),
            ("Turbine efficiency", format_percent_fraction(eta_turbine)),
            ("Initial state of charge", format_percent_fraction(soc_initial)),
            ("Terminal state of charge", format_percent_fraction(soc_terminal)),
            (
                "Enforce terminal state of charge",
                "Yes" if _require_bool(asset.get("enforce_terminal_soc")) else "No",
            ),
            ("Pump ramp-up", f"{require_finite(asset.get('pump_ramp_up_min')):,.1f} min"),
            ("Pump ramp-down", f"{require_finite(asset.get('pump_ramp_down_min')):,.1f} min"),
            ("Pump ramp power fraction", format_percent_fraction(asset.get("pump_ramp_power_frac"))),
            ("Turbine ramp-up", f"{require_finite(asset.get('turbine_ramp_up_min')):,.1f} min"),
            ("Turbine ramp-down", f"{require_finite(asset.get('turbine_ramp_down_min')):,.1f} min"),
            ("Turbine ramp power fraction", format_percent_fraction(asset.get("turbine_ramp_power_frac"))),
        ]
    )
    return main, [("Assumptions", advanced)]


def _software(metadata: Mapping[str, Any]) -> list[tuple[str, str]]:
    solver = _nested(metadata, "solver")
    for key in SOLVER_REQUIRED:
        if key not in solver:
            _fail()
    if _require_str(solver.get("solver_name")) != "HiGHS":
        _fail()
    continuous = _require_bool(solver.get("continuous_lp"))
    formulation = solver.get("formulation")
    if continuous:
        if formulation not in {None, "lp"}:
            _fail()
        model_type = "Continuous linear program"
    else:
        if formulation != "milp":
            _fail()
        model_type = "Mixed-integer linear program (MILP)"
    diagnostics = _as_mapping(solver.get("diagnostics"))
    baseline = _nested(metadata, "behavioural_baseline")
    rows = [
        ("StepInBel version", _require_str(metadata.get("software_version"))),
        ("Python version", _require_str(metadata.get("python_version"))),
        ("PHS baseline tag", _require_str(baseline.get("tag"))),
        ("PHS baseline commit", _require_str(baseline.get("commit"))),
        ("HiGHS solver", f"HiGHS {_require_str(solver.get('solver_version'))}"),
        ("Solver package version", _require_str(solver.get("package_version"))),
        ("Solver status", _require_str(solver.get("status"))),
        ("Model type", model_type),
        ("Columns", format_count(solver.get("num_col"))),
        ("Rows", format_count(solver.get("num_row"))),
        ("Non-zero coefficients", format_count(solver.get("num_nz"))),
        ("Integer variables", format_count(solver.get("num_integer"))),
        ("Binary variables", format_count(solver.get("num_binary"))),
        ("Build duration", format_seconds(solver.get("build_s"))),
        ("Solve duration", format_seconds(solver.get("solve_s"))),
        ("End-to-end duration", format_seconds(solver.get("end_to_end_s"))),
    ]
    if not continuous:
        try:
            rows.insert(8, ("Formulation", "MILP"))
            rows.append(("Termination", termination_label(solver.get("termination"))))
            rows.append(("Requested optimality gap", format_gap_percent(solver.get("requested_mip_gap"))))
            rows.append(("Achieved optimality gap", format_gap_percent(solver.get("achieved_mip_gap"))))
            rows.append(("Maximum solve time", format_minutes_phrase(solver.get("time_limit_s"))))
            rows.append(("Incumbent objective", f"{require_finite(solver.get('incumbent_objective')):.12g}"))
            rows.append(("Best bound", f"{require_finite(solver.get('best_bound')):.12g}"))
            node_count = solver.get("node_count")
            if node_count is not None:
                rows.append(("Node count", format_count(node_count)))
        except (TypeError, ValueError):
            _fail()
        return rows
    try:
        iterations = require_int(diagnostics.get("simplex_iteration_count"))
    except ValueError:
        _fail()
    rows.append(("Simplex iterations", format_count(iterations)))
    return rows


def _solution_checks(metadata: Mapping[str, Any]) -> dict[str, Any]:
    feasibility = _nested(metadata, "feasibility")
    ok = _require_bool(feasibility.get("ok"))
    fields = list(FEASIBILITY_FIELDS)
    version = metadata.get("artifact_schema_version")
    wind_required = type(version) is int and version == 3
    if wind_required and "max_wind_residual_mw" not in feasibility:
        _fail()
    if "max_wind_residual_mw" in feasibility:
        insert_at = next(index for index, (key, _, _) in enumerate(fields) if key == "max_pv_residual_mw") + 1
        fields.insert(insert_at, ("max_wind_residual_mw", "Wind residual", "MW"))
    rows: list[dict[str, str]] = []
    for key, label, unit in fields:
        if key not in feasibility:
            _fail()
        try:
            value = require_finite(feasibility.get(key))
        except ValueError:
            _fail()
        rows.append({"Check": label, "Maximum residual": format_residual(value, unit)})
    return {"ok": ok, "label": "Passed" if ok else "Failed", "rows": rows}


def _data_sources(metadata: Mapping[str, Any]) -> dict[str, Any]:
    published = _nested(metadata, "published_data")
    tables = _as_mapping(published.get("tables"))
    required = published.get("required_sources")
    if not isinstance(required, list) or not required or any(type(item) is not str for item in required):
        _fail()
    rows: list[dict[str, str]] = []
    hash_rows: list[dict[str, str]] = []
    for name in sorted(tables):
        entry = _as_mapping(tables.get(name))
        expected = _require_hash(entry.get("expected_sha256"))
        actual = _require_hash(entry.get("actual_sha256"))
        try:
            manifest_rows = require_int(entry.get("manifest_row_count"))
            parquet_rows = require_int(entry.get("parquet_row_count"))
        except ValueError:
            _fail()
        if manifest_rows != parquet_rows:
            _fail()
        match = expected == actual
        rows.append(
            {
                "Table": name,
                "Rows": format_count(parquet_rows),
                "Required": "Yes" if name in required else "No",
                "Hashes": "Match" if match else "Do not match",
            }
        )
        hash_rows.append(
            {
                "Table": name,
                "Expected SHA-256": expected,
                "Actual SHA-256": actual,
            }
        )
    return {
        "data_vintage": _require_str(published.get("data_vintage")),
        "pipeline_version": _require_str(published.get("pipeline_version")),
        "pipeline_commit": _require_str(published.get("pipeline_git_commit")),
        "manifest_sha256": _require_hash(published.get("manifest_sha256")),
        "required_sources": [str(item) for item in required],
        "rows": rows,
        "hashes": hash_rows,
    }


def _parent_run_info(validated: Mapping[str, Any], metadata: Mapping[str, Any], status: Mapping[str, Any]) -> list[tuple[str, str]]:
    period = validated["period"]
    items = [
        ("Run ID", _require_str(metadata.get("run_id"))),
        ("Run type", "Saved demonstration" if validated["source"] == SOURCE_DEMO else "Live simulation"),
        ("State", _require_str(status.get("state") if status.get("state") else metadata.get("state"))),
        ("Selected markets", markets_label(validated["markets"])),
        (
            "Configured simulation period",
            format_period(str(period["start_date"]), str(period["end_date"])),
        ),
        ("Interval count", format_count(metadata.get("interval_count"))),
        ("Request schema version", format_count(metadata.get("comparison_request_schema_version") or metadata.get("request_schema_version"))),
        (
            "Artifact schema version",
            format_count(
                metadata.get("comparison_artifact_schema_version") or metadata.get("artifact_schema_version")
            ),
        ),
    ]
    if status.get("started_at_utc"):
        items.append(("Started", str(status["started_at_utc"])))
    if status.get("completed_at_utc"):
        items.append(("Completed", str(status["completed_at_utc"])))
    if status.get("elapsed"):
        items.append(("Elapsed", str(status["elapsed"])))
    return items


def _child_run_info(metadata: Mapping[str, Any], status: Mapping[str, Any], market: str) -> list[tuple[str, str]]:
    items = [
        ("Selected market", market_label(market)),
        ("Child run ID", _require_str(metadata.get("run_id"))),
        ("State", _require_str(metadata.get("state"))),
        ("Interval count", format_count(metadata.get("interval_count"))),
        ("Request schema version", format_count(metadata.get("request_schema_version"))),
        ("Artifact schema version", format_count(metadata.get("artifact_schema_version"))),
    ]
    if status.get("started_at_utc"):
        items.append(("Started", str(status["started_at_utc"])))
    if status.get("completed_at_utc"):
        items.append(("Completed", str(status["completed_at_utc"])))
    if status.get("elapsed"):
        items.append(("Elapsed", str(status["elapsed"])))
    return items


def load_technical_details(
    result: Mapping[str, Any] | None,
    *,
    market: str,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> dict[str, Any]:
    if type(market) is not str or not market:
        _fail()
    bound = _bind_result(result, job=job, outputs_root=outputs_root)
    validated = bound.result
    root = bound.directory
    selected = market if market in validated["markets"] else display_market_keys(validated["markets"])[0]
    child_dir = _child_directory(bound, selected)
    child_meta = _read_json_object(child_dir / "run_metadata.json")
    child_resolved = _read_json_object(child_dir / "resolved_config.json")
    child_status = _run_status(child_dir / "run_status.json", _require_str(child_meta.get("run_id")))
    if _require_str(child_resolved.get("run_id")) != child_status["run_id"]:
        _fail()
    if _require_str(child_meta.get("market")) != selected:
        _fail()
    comparison = str(validated["kind"]) == KIND_COMPARISON
    parent_meta: dict[str, Any] | None = None
    parent_status: dict[str, Any] | None = None
    parent_events: dict[str, Any] | None = None
    parent_report: dict[str, Any] | None = None
    parent_log: dict[str, Any] | None = None
    if comparison:
        parent_meta = _read_json_object(root / "comparison_metadata.json")
        parent_run_id = _require_str(parent_meta.get("run_id"))
        if parent_run_id != str(validated["job_id"]):
            _fail()
        children = _nested(parent_meta, "children")
        child_record = _as_mapping(children.get(selected))
        if _require_str(child_record.get("run_id")) != child_status["run_id"]:
            _fail()
        parent_status = _run_status(root / "run_status.json", parent_run_id)
        parent_events = _read_events(root / "run_events.jsonl")
        parent_report = _read_text(root / "report.txt")
        parent_log = _read_text(root / "run.log")
        run_info = _parent_run_info(validated, parent_meta, parent_status)
        child_info = _child_run_info(child_meta, child_status, selected)
    else:
        run_info = _parent_run_info(validated, child_meta, child_status)
        child_info = None
    try:
        config_json = json.dumps(child_resolved, indent=2, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        _fail()
    configured, advanced = _configured_groups(child_resolved)
    return {
        "markets": display_market_keys(validated["markets"]),
        "selected_market": selected,
        "comparison": comparison,
        "run_information": run_info,
        "child_information": child_info,
        "configured_groups": configured,
        "advanced_groups": advanced,
        "configuration_record": config_json,
        "software": _software(child_meta),
        "solution_checks": _solution_checks(child_meta),
        "data_sources": _data_sources(child_meta),
        "parent_events": parent_events,
        "child_events": _read_events(child_dir / "run_events.jsonl"),
        "parent_report": parent_report,
        "parent_log": parent_log,
        "child_report": _read_text(child_dir / "report.txt"),
        "child_log": _read_text(child_dir / "run.log"),
    }
