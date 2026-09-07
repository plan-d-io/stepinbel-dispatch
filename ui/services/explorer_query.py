"""Trusted week-scoped Parquet queries for the Data explorer. No pandas."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

from ui.services.artifacts import validate_result_record
from ui.services.explorer_weeks import (
    enumerate_weeks,
    expected_row_count,
    find_week,
    parse_utc,
    week_axis,
    INTERVAL,
)
from ui.services.paths import KIND_CASE, KIND_COMPARISON
from ui.services.result_format import display_market_keys, is_finite_number, require_finite
from ui.services.result_view import BALANCING_MARKETS

ERROR_EXPLORER_TITLE = "Data explorer could not be opened"
ERROR_EXPLORER_BODY = (
    "The stored dispatch data for this selection are incomplete or incompatible."
)
DISPATCH_COLUMNS: tuple[str, ...] = (
    "datetime_utc",
    "market_sell_price_eur_mwh",
    "market_buy_price_eur_mwh",
    "p_pump_mw",
    "p_pump_grid_mw",
    "p_turbine_mw",
    "reservoir_start_mwh",
    "reservoir_end_mwh",
    "pv_available_mw",
    "pv_to_pump_mw",
    "pv_export_mw",
    "pv_curtail_mw",
    "market_energy_net_eur",
    "pv_revenue_eur",
    "total_revenue_eur",
)
CAPACITY_COLUMNS: tuple[str, ...] = (
    "direction",
    "start_index",
    "end_index",
    "committed_mw",
)
NUMERIC_DISPATCH = DISPATCH_COLUMNS[1:]
UP_DIRECTIONS = frozenset({"up", "upward"})
DOWN_DIRECTIONS = frozenset({"down", "downward"})


class ExplorerError(ValueError):
    """Selected-week dispatch or capacity could not be opened for display."""


def _fail() -> None:
    raise ExplorerError(ERROR_EXPLORER_BODY)


def _as_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail()
    return dict(value)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail()
    if not isinstance(loaded, dict):
        _fail()
    return loaded


def _nested(mapping: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = mapping
    for key in keys:
        current = current.get(key) if isinstance(current, Mapping) else None
        if not isinstance(current, Mapping):
            _fail()
    return dict(current)


def file_identity(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        _fail()
    return (str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns))


def _validated_record(
    result: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None,
    outputs_root: Path | None,
) -> dict[str, Any]:
    try:
        return validate_result_record(result, job=job, outputs_root=outputs_root)
    except (OSError, TypeError, ValueError):
        _fail()
    raise ExplorerError(ERROR_EXPLORER_BODY)


def _child_directory(validated: Mapping[str, Any], market: str) -> Path:
    if market not in validated["markets"]:
        _fail()
    output = Path(str(validated["output_directory"]))
    kind = str(validated["kind"])
    if kind == KIND_CASE:
        if list(validated["markets"]) != [market]:
            _fail()
        return output
    if kind != KIND_COMPARISON:
        _fail()
    return output / "cases" / market


def _resolved_bounds(resolved: Mapping[str, Any]) -> dict[str, Any]:
    try:
        start = parse_utc(resolved.get("resolved_start_utc"))
        end = parse_utc(resolved.get("resolved_end_exclusive_utc"))
        e_max = require_finite(resolved.get("e_max_mwh"))
        grid_import = require_finite(resolved.get("effective_grid_import_mw"))
        grid_export = require_finite(resolved.get("effective_grid_export_mw"))
    except ValueError:
        _fail()
    site = _nested(resolved, "config", "site")
    try:
        pv_kw = require_finite(site.get("pv_ac_kw"))
    except ValueError:
        _fail()
    return {
        "start_utc": start,
        "end_utc": end,
        "e_max_mwh": e_max,
        "effective_grid_import_mw": grid_import,
        "effective_grid_export_mw": grid_export,
        "pv_included": pv_kw > 0,
    }


def _require_aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail()
    return value.astimezone(timezone.utc)


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not is_finite_number(value):
        _fail()
    number = float(value)
    if not math.isfinite(number):
        _fail()
    return number


def _interval_index(moment: datetime, origin: datetime) -> int:
    delta = moment - origin
    seconds = delta.total_seconds()
    step = INTERVAL.total_seconds()
    if seconds < 0 or seconds % step != 0:
        _fail()
    return int(seconds // step)


def query_dispatch_week(
    path: Path,
    *,
    start_utc: datetime,
    end_utc: datetime,
    expected_rows: int,
) -> dict[str, list[Any]]:
    if path.name != "dispatch.parquet":
        _fail()
    try:
        dataset = ds.dataset(str(path), format="parquet")
        start_scalar = pa.scalar(start_utc, type=pa.timestamp("us", tz="UTC"))
        end_scalar = pa.scalar(end_utc, type=pa.timestamp("us", tz="UTC"))
        filt = (pc.field("datetime_utc") >= start_scalar) & (pc.field("datetime_utc") < end_scalar)
        table = dataset.to_table(columns=list(DISPATCH_COLUMNS), filter=filt)
    except (OSError, TypeError, ValueError, pa.ArrowInvalid, pa.ArrowTypeError):
        _fail()
    if list(table.column_names) != list(DISPATCH_COLUMNS):
        _fail()
    if table.num_rows != expected_rows:
        _fail()
    lengths = {table.column(name).length() for name in DISPATCH_COLUMNS}
    if lengths != {expected_rows}:
        _fail()
    stamps = [_require_aware_utc(item) for item in table.column("datetime_utc").to_pylist()]
    if stamps[0] != start_utc:
        _fail()
    if stamps[-1] != end_utc - INTERVAL:
        _fail()
    for index, moment in enumerate(stamps):
        expected = start_utc + INTERVAL * index
        if moment != expected:
            _fail()
        if index and moment <= stamps[index - 1]:
            _fail()
    payload: dict[str, list[Any]] = {"datetime_utc": stamps}
    for name in NUMERIC_DISPATCH:
        values = table.column(name).to_pylist()
        payload[name] = [_finite_number(item) for item in values]
    return payload


def query_capacity_week(
    path: Path,
    *,
    resolved_start_utc: datetime,
    start_utc: datetime,
    end_utc: datetime,
    expected_rows: int,
) -> dict[str, Any]:
    if path.name != "capacity.parquet":
        _fail()
    week_start_index = _interval_index(start_utc, resolved_start_utc)
    week_end_index = _interval_index(end_utc, resolved_start_utc)
    if week_end_index - week_start_index != expected_rows:
        _fail()
    try:
        dataset = ds.dataset(str(path), format="parquet")
        filt = (pc.field("start_index") < week_end_index) & (pc.field("end_index") > week_start_index)
        table = dataset.to_table(columns=list(CAPACITY_COLUMNS), filter=filt)
    except (OSError, TypeError, ValueError, pa.ArrowInvalid, pa.ArrowTypeError):
        _fail()
    if list(table.column_names) != list(CAPACITY_COLUMNS):
        _fail()
    upward = [0.0] * expected_rows
    downward = [0.0] * expected_rows
    filled_up = [False] * expected_rows
    filled_down = [False] * expected_rows
    has_up = False
    has_down = False
    directions = table.column("direction").to_pylist()
    starts = table.column("start_index").to_pylist()
    ends = table.column("end_index").to_pylist()
    committed = table.column("committed_mw").to_pylist()
    for index in range(table.num_rows):
        direction = directions[index]
        if not isinstance(direction, str):
            _fail()
        if not isinstance(starts[index], int) or isinstance(starts[index], bool):
            _fail()
        if not isinstance(ends[index], int) or isinstance(ends[index], bool):
            _fail()
        power = _finite_number(committed[index])
        overlap_start = max(int(starts[index]), week_start_index)
        overlap_end = min(int(ends[index]), week_end_index)
        if overlap_start >= overlap_end:
            continue
        if direction in UP_DIRECTIONS:
            series, filled, flag = upward, filled_up, "up"
        elif direction in DOWN_DIRECTIONS:
            series, filled, flag = downward, filled_down, "down"
        else:
            _fail()
            raise ExplorerError(ERROR_EXPLORER_BODY)
        for cursor in range(overlap_start, overlap_end):
            local = cursor - week_start_index
            if filled[local]:
                _fail()
            series[local] = abs(power)
            filled[local] = True
        if flag == "up":
            has_up = True
        else:
            has_down = True
    return {
        "upward": upward if has_up else None,
        "downward": downward if has_down else None,
        "has_commitment": has_up or has_down,
    }


def trusted_dispatch_path(
    result: Mapping[str, Any] | None,
    *,
    market: str,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> Path:
    validated = _validated_record(result, job=job, outputs_root=outputs_root)
    if market not in validated["markets"]:
        _fail()
    path = _child_directory(validated, market) / "dispatch.parquet"
    if not path.is_file():
        _fail()
    return path


def explorer_weeks_by_market(
    result: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    validated = _validated_record(result, job=job, outputs_root=outputs_root)
    weeks_by_market: dict[str, list[dict[str, Any]]] = {}
    for market in display_market_keys(validated["markets"]):
        resolved = _read_json_object(_child_directory(validated, market) / "resolved_config.json")
        bounds = _resolved_bounds(resolved)
        try:
            weeks_by_market[market] = enumerate_weeks(bounds["start_utc"], bounds["end_utc"])
        except ValueError:
            _fail()
    return weeks_by_market


def load_explorer_week(
    result: Mapping[str, Any] | None,
    *,
    market: str,
    week_id: str,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> dict[str, Any]:
    validated = _validated_record(result, job=job, outputs_root=outputs_root)
    if market not in validated["markets"]:
        _fail()
    child = _child_directory(validated, market)
    resolved = _read_json_object(child / "resolved_config.json")
    bounds = _resolved_bounds(resolved)
    try:
        weeks = enumerate_weeks(bounds["start_utc"], bounds["end_utc"])
        week = find_week(weeks, week_id)
        start = parse_utc(week["start_utc"])
        end = parse_utc(week["end_utc"])
        expected = expected_row_count(start, end)
    except ValueError:
        _fail()
    if expected != int(week["expected_rows"]):
        _fail()
    dispatch_path = child / "dispatch.parquet"
    dispatch = query_dispatch_week(
        dispatch_path,
        start_utc=start,
        end_utc=end,
        expected_rows=expected,
    )
    axis = week_axis(dispatch["datetime_utc"])
    capacity: dict[str, Any] | None = None
    if market in BALANCING_MARKETS:
        capacity = query_capacity_week(
            child / "capacity.parquet",
            resolved_start_utc=bounds["start_utc"],
            start_utc=start,
            end_utc=end,
            expected_rows=expected,
        )
    hover = list(axis["hover_labels"])
    return {
        "market": market,
        "week": week,
        "pv_included": bounds["pv_included"],
        "e_max_mwh": bounds["e_max_mwh"],
        "effective_grid_import_mw": bounds["effective_grid_import_mw"],
        "effective_grid_export_mw": bounds["effective_grid_export_mw"],
        "row_count": expected,
        "x_index": list(axis["x_index"]),
        "hover_labels": hover,
        "tick_vals": list(axis["tick_vals"]),
        "tick_text": list(axis["tick_text"]),
        "first_hover": hover[0],
        "last_hover": hover[-1],
        "dispatch": {name: list(dispatch[name]) for name in NUMERIC_DISPATCH},
        "capacity": capacity,
        "dispatch_identity": file_identity(dispatch_path),
        "has_capacity": market in BALANCING_MARKETS,
    }


def explorer_cache_key(
    *,
    result_identity: str,
    market: str,
    week_id: str,
    dispatch_identity: Sequence[object],
) -> tuple[object, ...]:
    return (result_identity, market, week_id, tuple(dispatch_identity))
