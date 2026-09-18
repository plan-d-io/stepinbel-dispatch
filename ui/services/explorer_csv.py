"""Deferred weekly Data Explorer CSV. Generated in memory only on click."""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ui.services.artifacts import bind_exact_result_artifacts
from ui.services.explorer_query import ExplorerError, load_explorer_week
from ui.services.explorer_weeks import BRUSSELS, INTERVAL, expected_row_count, parse_utc
from ui.services.result_downloads import path_safe_name
from ui.services.result_format import is_finite_number

ERROR_EXPLORER_BODY = (
    "The stored dispatch data for this selection are incomplete or incompatible."
)
CSV_MIME = "text/csv"
UTC_COLUMN = "datetime_utc"
BELGIUM_COLUMN = "datetime_belgium"
E_MAX_COLUMN = "e_max_mwh"
GRID_IMPORT_COLUMN = "effective_grid_import_mw"
GRID_EXPORT_COLUMN = "effective_grid_export_mw"
UPWARD_COLUMN = "upward_commitment_mw"
DOWNWARD_COLUMN = "downward_commitment_mw"
LEADING_COLUMNS = (UTC_COLUMN, BELGIUM_COLUMN)
MAIN_POWER_COLUMNS = ("p_pump_mw", "p_turbine_mw", "reservoir_end_mwh", E_MAX_COLUMN)
PRICE_COLUMNS = ("market_buy_price_eur_mwh", "market_sell_price_eur_mwh", "market_energy_net_eur")
PV_REVENUE_COLUMN = "pv_revenue_eur"
WIND_REVENUE_COLUMN = "wind_revenue_eur"
TOTAL_REVENUE_COLUMN = "total_revenue_eur"
PV_ALLOCATION_COLUMNS = ("pv_available_mw", "pv_to_pump_mw", "pv_export_mw", "pv_curtail_mw")
WIND_ALLOCATION_COLUMNS = (
    "wind_available_mw",
    "wind_to_pump_mw",
    "wind_export_mw",
    "wind_curtail_mw",
)
GRID_COLUMNS = ("p_pump_grid_mw", GRID_IMPORT_COLUMN, GRID_EXPORT_COLUMN)
REFERENCE_COLUMNS = (E_MAX_COLUMN, GRID_IMPORT_COLUMN, GRID_EXPORT_COLUMN)


def _fail() -> None:
    raise ExplorerError(ERROR_EXPLORER_BODY)


def explorer_csv_filename(run_id: str, market: str, week_id: str) -> str:
    return (
        f"{path_safe_name(run_id)}-{path_safe_name(market)}-"
        f"{path_safe_name(week_id)}-data-explorer.csv"
    )


def explorer_csv_button_key(identity: str, market: str, week_id: str) -> str:
    return (
        f"sib-explorer-csv-{path_safe_name(identity)}-"
        f"{path_safe_name(market)}-{path_safe_name(week_id)}"
    )


def format_datetime_utc(moment: datetime) -> str:
    utc = moment.astimezone(timezone.utc)
    if utc.microsecond:
        _fail()
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_datetime_belgium(moment: datetime) -> str:
    local = moment.astimezone(BRUSSELS)
    if local.microsecond:
        _fail()
    text = local.isoformat(timespec="seconds")
    if not (text.endswith("+01:00") or text.endswith("+02:00")):
        _fail()
    return text


def explorer_csv_columns(payload: Mapping[str, Any]) -> tuple[str, ...]:
    pv_included = bool(payload.get("pv_included"))
    wind_included = bool(payload.get("wind_included"))
    columns: list[str] = []
    columns.extend(LEADING_COLUMNS)
    columns.extend(MAIN_POWER_COLUMNS)
    columns.extend(PRICE_COLUMNS)
    if pv_included:
        columns.append(PV_REVENUE_COLUMN)
    if wind_included:
        columns.append(WIND_REVENUE_COLUMN)
    columns.append(TOTAL_REVENUE_COLUMN)
    if pv_included:
        columns.extend(PV_ALLOCATION_COLUMNS)
    if wind_included:
        columns.extend(WIND_ALLOCATION_COLUMNS)
    columns.extend(GRID_COLUMNS)
    if payload.get("has_capacity"):
        capacity = payload.get("capacity")
        if isinstance(capacity, Mapping) and capacity.get("has_commitment"):
            if capacity.get("upward") is not None:
                columns.append(UPWARD_COLUMN)
            if capacity.get("downward") is not None:
                columns.append(DOWNWARD_COLUMN)
    if len(columns) != len(set(columns)):
        _fail()
    return tuple(columns)


def explorer_week_timestamps(payload: Mapping[str, Any]) -> list[datetime]:
    week = payload.get("week")
    if not isinstance(week, Mapping):
        _fail()
    try:
        start = parse_utc(week.get("start_utc"))
        end = parse_utc(week.get("end_utc"))
        expected = expected_row_count(start, end)
    except (TypeError, ValueError):
        _fail()
    count = payload.get("row_count")
    if type(count) is not int or isinstance(count, bool) or count != expected or count <= 0:
        _fail()
    stamps = [start + INTERVAL * index for index in range(count)]
    if stamps[0] != start or stamps[-1] + INTERVAL != end:
        _fail()
    return stamps


def _finite_cell(value: object) -> float:
    if isinstance(value, bool) or not is_finite_number(value):
        _fail()
    number = float(value)
    if not math.isfinite(number):
        _fail()
    return number


def _repeated_reference(payload: Mapping[str, Any], name: str, count: int) -> list[float]:
    value = _finite_cell(payload.get(name))
    return [value] * count


def _dispatch_series(payload: Mapping[str, Any], name: str, count: int) -> list[float]:
    dispatch = payload.get("dispatch")
    if not isinstance(dispatch, Mapping):
        _fail()
    values = dispatch.get(name)
    if not isinstance(values, list) or len(values) != count:
        _fail()
    return [_finite_cell(item) for item in values]


def _capacity_series(payload: Mapping[str, Any], name: str, count: int) -> list[float]:
    capacity = payload.get("capacity")
    if not isinstance(capacity, Mapping):
        _fail()
    key = "upward" if name == UPWARD_COLUMN else "downward"
    values = capacity.get(key)
    if not isinstance(values, list) or len(values) != count:
        _fail()
    return [_finite_cell(item) for item in values]


def _column_series(
    payload: Mapping[str, Any],
    stamps: list[datetime],
    name: str,
) -> list[object]:
    count = len(stamps)
    if name == UTC_COLUMN:
        return [format_datetime_utc(item) for item in stamps]
    if name == BELGIUM_COLUMN:
        return [format_datetime_belgium(item) for item in stamps]
    if name in REFERENCE_COLUMNS:
        return _repeated_reference(payload, name, count)
    if name in {UPWARD_COLUMN, DOWNWARD_COLUMN}:
        return _capacity_series(payload, name, count)
    return _dispatch_series(payload, name, count)


def encode_explorer_week_csv(payload: Mapping[str, Any]) -> bytes:
    columns = explorer_csv_columns(payload)
    stamps = explorer_week_timestamps(payload)
    series = [_column_series(payload, stamps, name) for name in columns]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row_index in range(len(stamps)):
        writer.writerow([series[column_index][row_index] for column_index in range(len(columns))])
    return buffer.getvalue().encode("utf-8")


def build_explorer_week_csv(
    result: Mapping[str, Any] | None,
    *,
    market: str,
    week_id: str,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> bytes:
    if type(market) is not str or not market:
        _fail()
    if type(week_id) is not str or not week_id:
        _fail()
    try:
        bind_exact_result_artifacts(result, job=job, outputs_root=outputs_root)
    except (OSError, TypeError, ValueError):
        _fail()
    payload = load_explorer_week(
        result,
        market=market,
        week_id=week_id,
        job=job,
        outputs_root=outputs_root,
    )
    return encode_explorer_week_csv(payload)


def deferred_explorer_week_csv(
    result: Mapping[str, Any],
    *,
    market: str,
    week_id: str,
    job: Mapping[str, Any] | None,
    outputs_root: Path | None,
):
    captured_result = dict(result)
    captured_job = dict(job) if isinstance(job, Mapping) else None
    captured_root = outputs_root
    captured_market = str(market)
    captured_week = str(week_id)

    def _load() -> bytes:
        return build_explorer_week_csv(
            captured_result,
            market=captured_market,
            week_id=captured_week,
            job=captured_job,
            outputs_root=captured_root,
        )

    return _load
