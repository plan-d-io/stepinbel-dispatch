"""Read already-validated result artifacts for Overview and Market detail."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from ui.services.artifacts import validate_result_record
from ui.services.paths import KIND_CASE, KIND_COMPARISON, MARKET_LABELS
from ui.services.result_format import (
    format_balancing,
    format_below_highest,
    format_bid,
    format_count,
    format_cycles,
    format_eur,
    format_eur_amount,
    format_grid,
    format_hours,
    format_mwh,
    format_period,
    format_pump_turbine,
    format_pv_capacity,
    format_pv_self_share,
    format_storage,
    format_wind_capacity,
    format_wind_self_share,
    RESULTS_DISPLAY_MARKETS,
    display_market_keys,
    is_finite_number,
    market_label,
    markets_label,
    require_finite,
    require_int,
)

ERROR_RESULTS_TITLE = "Results could not be opened"
ERROR_RESULTS_BODY = "The stored result files are incomplete or incompatible."
CAPACITY_PREVIEW_LIMIT = 50
BALANCING_MARKETS = frozenset({"mfrr", "afrr"})

ROW_FLOAT_FIELDS = (
    "total_site_revenue_eur",
    "difference_from_highest_eur",
    "market_energy_net_eur",
    "capacity_revenue_eur",
    "pv_revenue_eur",
    "pumped_mwh",
    "turbined_mwh",
    "full_cycles",
    "pv_available_mwh",
    "pv_self_consumed_mwh",
    "pv_exported_mwh",
    "pv_curtailed_mwh",
    "simultaneous_overlap_mwh",
    "simultaneous_interval_energy_net_eur",
    "e_max_mwh",
)
WIND_FLOAT_FIELDS = (
    "wind_revenue_eur",
    "wind_available_mwh",
    "wind_self_consumed_mwh",
    "wind_exported_mwh",
    "wind_curtailed_mwh",
)
CHILD_FLOAT_FIELDS = (
    "total_site_revenue_eur",
    "market_energy_net_eur",
    "capacity_revenue_eur",
    "pv_revenue_eur",
    "pumped_mwh",
    "turbined_mwh",
    "e_max_mwh",
    "reservoir_initial_mwh",
    "reservoir_final_mwh",
    "pv_available_mwh",
    "pv_self_consumed_mwh",
    "pv_exported_mwh",
    "pv_curtailed_mwh",
    "simultaneous_overlap_mwh",
)
MONTHLY_REQUIRED = ("period", "total_site_revenue_eur")
CAPACITY_REQUIRED = (
    "identifier",
    "direction",
    "price_eur_mw_h",
    "cap_max_mw",
    "committed_mw",
    "block_hours",
    "capacity_revenue_eur",
)


class ResultViewError(ValueError):
    """Required result files could not be opened for display."""


def _fail() -> None:
    raise ResultViewError(ERROR_RESULTS_BODY)


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


def _read_parquet(path: Path, required: Sequence[str]) -> pa.Table:
    try:
        table = pq.read_table(path)
    except (OSError, TypeError, ValueError, pa.ArrowInvalid):
        _fail()
    names = set(table.column_names)
    if any(column not in names for column in required):
        _fail()
    return table


def _finite(mapping: Mapping[str, Any], key: str) -> float:
    try:
        return require_finite(mapping.get(key))
    except ValueError:
        _fail()
    raise ResultViewError(ERROR_RESULTS_BODY)


def _integer(mapping: Mapping[str, Any], key: str) -> int:
    try:
        return require_int(mapping.get(key))
    except ValueError:
        _fail()
    raise ResultViewError(ERROR_RESULTS_BODY)


def _text(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        _fail()
    return value


def _nested(mapping: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = mapping
    for key in keys:
        current = current.get(key) if isinstance(current, Mapping) else None
        if not isinstance(current, Mapping):
            _fail()
    return dict(current)


def _case_directory(kind: str, output: Path, market: str) -> Path:
    if kind == KIND_CASE:
        return output
    return output / "cases" / market


def _full_cycles(summary: Mapping[str, Any]) -> float:
    stored = summary.get("full_cycles")
    if is_finite_number(stored):
        return float(stored)
    try:
        energy = require_finite(summary.get("turbined_mwh"))
        capacity = require_finite(summary.get("e_max_mwh"))
    except ValueError:
        _fail()
    if capacity == 0:
        _fail()
    cycles = energy / capacity
    if not math.isfinite(cycles):
        _fail()
    return cycles


def _energy_net_in_simultaneous(summary: Mapping[str, Any]) -> float:
    if is_finite_number(summary.get("simultaneous_interval_energy_net_eur")):
        return float(summary["simultaneous_interval_energy_net_eur"])
    diagnostics = summary.get("diagnostics")
    if isinstance(diagnostics, Mapping) and is_finite_number(
        diagnostics.get("simultaneous_interval_energy_net_eur")
    ):
        return float(diagnostics["simultaneous_interval_energy_net_eur"])
    _fail()
    raise ResultViewError(ERROR_RESULTS_BODY)


def _normalize_case_row(summary: Mapping[str, Any], market: str) -> dict[str, Any]:
    row = {
        "market": market,
        "revenue_rank": 1,
        "difference_from_highest_eur": 0.0,
        "full_cycles": _full_cycles(summary),
        "simultaneous_interval_count": _integer(summary, "simultaneous_interval_count"),
        "simultaneous_interval_energy_net_eur": _energy_net_in_simultaneous(summary),
    }
    for key in ROW_FLOAT_FIELDS:
        if key in {"difference_from_highest_eur", "full_cycles", "simultaneous_interval_energy_net_eur"}:
            continue
        row[key] = _finite(summary, key)
    if "wind_revenue_eur" in summary:
        row.update(_require_wind_fields(summary))
    return row


def _normalize_comparison_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    market = _text(raw, "market")
    if market not in MARKET_LABELS:
        _fail()
    row = {
        "market": market,
        "revenue_rank": _integer(raw, "revenue_rank"),
        "simultaneous_interval_count": _integer(raw, "simultaneous_interval_count"),
    }
    for key in ROW_FLOAT_FIELDS:
        row[key] = _finite(raw, key)
    if "wind_revenue_eur" in raw:
        row.update(_require_wind_fields(raw))
    return row


def _header_from_resolved(
    *,
    resolved: Mapping[str, Any],
    markets: list[str],
    period: Mapping[str, str],
    source: str,
    activation_profile: object = None,
) -> dict[str, str | None]:
    config = _nested(resolved, "config")
    asset = _nested(config, "asset")
    site = _nested(config, "site")
    start = period.get("start_date")
    end = period.get("end_date")
    if not isinstance(start, str) or not isinstance(end, str):
        _fail()
    pv_kw = _finite(site, "pv_ac_kw")
    if pv_kw > 0:
        try:
            pv_text = format_pv_capacity(pv_kw, site.get("pv_region") or "Belgium")
        except ValueError:
            _fail()
    else:
        pv_text = "Off"
    wind_value = site.get("wind_capacity_kw")
    wind_text = None
    if wind_value is not None:
        wind_kw = _finite(site, "wind_capacity_kw")
        if wind_kw > 0:
            try:
                wind_text = format_wind_capacity(wind_kw, site.get("wind_profile_id"))
            except ValueError:
                _fail()
    balancing = None
    if any(item in BALANCING_MARKETS for item in markets):
        profile = activation_profile
        if profile is None:
            case = _nested(config, "market_case")
            profile = case.get("activation_profile")
        try:
            balancing = format_balancing(profile)
        except ValueError:
            _fail()
    storage_hours = asset.get("storage_hours")
    if storage_hours is not None and not is_finite_number(storage_hours):
        _fail()
    return {
        "markets": markets_label(markets),
        "period": format_period(start, end),
        "pump_turbine": format_pump_turbine(asset.get("power_pump_mw"), asset.get("power_turbine_mw")),
        "storage": format_storage(
            storage_hours=storage_hours,
            e_max_mwh=resolved.get("e_max_mwh", asset.get("pond_energy_mwh")),
        ),
        "grid": format_grid(site.get("grid_import_mw"), site.get("grid_export_mw")),
        "pv": pv_text,
        "wind": wind_text,
        "balancing": balancing,
        "run_type": "Saved demonstration" if source == "demo" else None,
    }


def _pv_included(resolved: Mapping[str, Any]) -> bool:
    site = _nested(resolved, "config", "site")
    return _finite(site, "pv_ac_kw") > 0


def _wind_included(resolved: Mapping[str, Any]) -> bool:
    site = _nested(resolved, "config", "site")
    value = site.get("wind_capacity_kw")
    if value is None:
        return False
    return _finite(site, "wind_capacity_kw") > 0


def _require_wind_fields(mapping: Mapping[str, Any]) -> dict[str, float]:
    return {key: _finite(mapping, key) for key in WIND_FLOAT_FIELDS}


def _monthly_series(path: Path) -> dict[str, list[Any]]:
    table = _read_parquet(path, MONTHLY_REQUIRED)
    periods = table.column("period").to_pylist()
    values = table.column("total_site_revenue_eur").to_pylist()
    if not periods or len(periods) != len(values):
        _fail()
    series: list[float] = []
    labels: list[str] = []
    for period, value in zip(periods, values, strict=True):
        if not isinstance(period, str) or not period:
            _fail()
        try:
            series.append(require_finite(value))
        except ValueError:
            _fail()
        labels.append(period)
    return {"periods": labels, "total_site_revenue_eur": series}


def _capacity_preview(path: Path) -> dict[str, Any]:
    table = _read_parquet(path, CAPACITY_REQUIRED)
    total = int(table.num_rows)
    preview = table.slice(0, min(CAPACITY_PREVIEW_LIMIT, total))
    rows: list[dict[str, str]] = []
    identifiers = preview.column("identifier").to_pylist()
    directions = preview.column("direction").to_pylist()
    bids = preview.column("price_eur_mw_h").to_pylist()
    available = preview.column("cap_max_mw").to_pylist()
    committed = preview.column("committed_mw").to_pylist()
    hours = preview.column("block_hours").to_pylist()
    revenue = preview.column("capacity_revenue_eur").to_pylist()
    for index in range(preview.num_rows):
        identifier = identifiers[index]
        direction = directions[index]
        if not isinstance(identifier, str) or not isinstance(direction, str):
            _fail()
        try:
            rows.append(
                {
                    "Identifier": identifier,
                    "Direction": direction,
                    "Bid (EUR/MW/h)": format_bid(bids[index]),
                    "Available capacity (MW)": f"{require_finite(available[index]):,.3f}",
                    "Committed capacity (MW)": f"{require_finite(committed[index]):,.3f}",
                    "Block duration (h)": format_hours(hours[index]).removesuffix(" h"),
                    "Capacity revenue (EUR)": format_eur_amount(revenue[index]),
                }
            )
        except ValueError:
            _fail()
    return {
        "block_count": total,
        "preview": rows,
        "truncated": total > CAPACITY_PREVIEW_LIMIT,
    }


def _pv_self_share(row: Mapping[str, Any], *, pv_included: bool) -> str:
    try:
        return format_pv_self_share(
            self_consumed=row["pv_self_consumed_mwh"],
            available=row["pv_available_mwh"],
            pv_included=pv_included,
        )
    except ValueError:
        _fail()
    raise ResultViewError(ERROR_RESULTS_BODY)


def _wind_self_share(row: Mapping[str, Any], *, wind_included: bool) -> str:
    try:
        return format_wind_self_share(
            self_consumed=row["wind_self_consumed_mwh"],
            available=row["wind_available_mwh"],
            wind_included=wind_included,
        )
    except ValueError:
        _fail()
    raise ResultViewError(ERROR_RESULTS_BODY)


def _format_row(
    row: Mapping[str, Any],
    *,
    one_market: bool,
    pv_included: bool,
    wind_included: bool,
) -> dict[str, Any]:
    market = str(row["market"])
    rank = int(row["revenue_rank"])
    note = "Highest simulated revenue"
    if not one_market and rank != 1:
        note = format_below_highest(row["difference_from_highest_eur"])
    formatted = {
        "market": market_label(market),
        "rank": str(rank),
        "total": format_eur(row["total_site_revenue_eur"]),
        "total_amount": format_eur_amount(row["total_site_revenue_eur"]),
        "difference": format_eur_amount(row["difference_from_highest_eur"]),
        "energy": format_eur(row["market_energy_net_eur"]),
        "energy_amount": format_eur_amount(row["market_energy_net_eur"]),
        "capacity": format_eur(row["capacity_revenue_eur"]),
        "capacity_amount": format_eur_amount(row["capacity_revenue_eur"]),
        "pv": format_eur(row["pv_revenue_eur"]),
        "pv_amount": format_eur_amount(row["pv_revenue_eur"]),
        "pumped": format_mwh(row["pumped_mwh"]),
        "turbined": format_mwh(row["turbined_mwh"]),
        "cycles": format_cycles(row["full_cycles"]),
        "pv_self": format_mwh(row["pv_self_consumed_mwh"]),
        "pv_self_share": _pv_self_share(row, pv_included=pv_included),
        "pv_export": format_mwh(row["pv_exported_mwh"]),
        "pv_curtail": format_mwh(row["pv_curtailed_mwh"]),
        "simul_n": format_count(row["simultaneous_interval_count"]),
        "simul_mwh": format_mwh(row["simultaneous_overlap_mwh"]),
        "simul_eur": format_eur(row["simultaneous_interval_energy_net_eur"]),
        "note": note,
    }
    if wind_included:
        formatted["wind"] = format_eur(row["wind_revenue_eur"])
        formatted["wind_amount"] = format_eur_amount(row["wind_revenue_eur"])
        formatted["wind_self"] = format_mwh(row["wind_self_consumed_mwh"])
        formatted["wind_self_share"] = _wind_self_share(row, wind_included=True)
        formatted["wind_export"] = format_mwh(row["wind_exported_mwh"])
        formatted["wind_curtail"] = format_mwh(row["wind_curtailed_mwh"])
    return {
        "market": market,
        "revenue_rank": rank,
        "has_capacity": market in BALANCING_MARKETS,
        "show_pv": pv_included,
        "show_wind": wind_included,
        "values": {key: row[key] for key in ("total_site_revenue_eur", *ROW_FLOAT_FIELDS, *WIND_FLOAT_FIELDS, "revenue_rank", "simultaneous_interval_count") if key in row},
        "formatted": formatted,
    }


def _child_display(
    *,
    directory: Path,
    market: str,
    row: Mapping[str, Any],
    pv_included: bool,
    wind_included: bool,
) -> dict[str, Any]:
    summary = _read_json_object(directory / "summary.json")
    resolved = _read_json_object(directory / "resolved_config.json")
    _read_json_object(directory / "run_metadata.json")
    if _text(summary, "market") != market:
        _fail()
    values = {key: _finite(summary, key) for key in CHILD_FLOAT_FIELDS}
    values["full_cycles"] = float(row["full_cycles"])
    values["simultaneous_interval_count"] = _integer(summary, "simultaneous_interval_count")
    values["simultaneous_interval_energy_net_eur"] = _energy_net_in_simultaneous(summary)
    if wind_included:
        values.update(_require_wind_fields(summary))
    monthly = _monthly_series(directory / "monthly_summary.parquet")
    capacity = _capacity_preview(directory / "capacity.parquet")
    formatted = {
        "total": format_eur(values["total_site_revenue_eur"]),
        "energy": format_eur(values["market_energy_net_eur"]),
        "capacity": format_eur(values["capacity_revenue_eur"]),
        "pv": format_eur(values["pv_revenue_eur"]),
        "pumped": format_mwh(values["pumped_mwh"]),
        "turbined": format_mwh(values["turbined_mwh"]),
        "cycles": format_cycles(values["full_cycles"]),
        "reservoir": format_mwh(values["e_max_mwh"]),
        "reservoir_initial": format_mwh(values["reservoir_initial_mwh"]),
        "reservoir_final": format_mwh(values["reservoir_final_mwh"]),
        "pv_available": format_mwh(values["pv_available_mwh"]),
        "pv_self": format_mwh(values["pv_self_consumed_mwh"]),
        "pv_export": format_mwh(values["pv_exported_mwh"]),
        "pv_curtail": format_mwh(values["pv_curtailed_mwh"]),
        "simul_n": format_count(values["simultaneous_interval_count"]),
        "simul_mwh": format_mwh(values["simultaneous_overlap_mwh"]),
        "simul_eur": format_eur(values["simultaneous_interval_energy_net_eur"]),
        "capacity_revenue": format_eur(values["capacity_revenue_eur"]),
        "block_count": format_count(capacity["block_count"]),
    }
    if wind_included:
        formatted["wind"] = format_eur(values["wind_revenue_eur"])
        formatted["wind_available"] = format_mwh(values["wind_available_mwh"])
        formatted["wind_self"] = format_mwh(values["wind_self_consumed_mwh"])
        formatted["wind_export"] = format_mwh(values["wind_exported_mwh"])
        formatted["wind_curtail"] = format_mwh(values["wind_curtailed_mwh"])
    return {
        "market": market,
        "label": market_label(market),
        "has_capacity": market in BALANCING_MARKETS,
        "pv_included": pv_included,
        "wind_included": wind_included,
        "values": values,
        "formatted": formatted,
        "composition": _composition_series(
            values, market=market, pv_included=pv_included, wind_included=wind_included
        ),
        "monthly": monthly,
        "capacity": capacity,
        "e_max_mwh": values["e_max_mwh"],
    }


def _composition_series(
    values: Mapping[str, float],
    *,
    market: str,
    pv_included: bool,
    wind_included: bool = False,
) -> dict[str, list[Any]]:
    categories = ["Net energy revenue"]
    series = [values["market_energy_net_eur"]]
    if market in BALANCING_MARKETS:
        categories.append("Capacity revenue")
        series.append(values["capacity_revenue_eur"])
    if pv_included:
        categories.append("PV revenue")
        series.append(values["pv_revenue_eur"])
    if wind_included:
        categories.append("Wind revenue")
        series.append(values["wind_revenue_eur"])
    return {"categories": categories, "values": series}


def read_result_artifacts(
    *,
    kind: str,
    markets: Sequence[str],
    directory: Path,
    source: str,
    period: Mapping[str, str],
) -> dict[str, Any]:
    selected = list(markets)
    if not selected:
        _fail()
    output = Path(directory)
    if kind == KIND_COMPARISON:
        summary = _read_json_object(output / "comparison_summary.json")
        _read_json_object(output / "comparison_request.json")
        _read_json_object(output / "comparison_metadata.json")
        highest = _text(summary, "highest_revenue_market")
        raw_rows = summary.get("rows")
        if not isinstance(raw_rows, list) or not raw_rows:
            _fail()
        by_market: dict[str, dict[str, Any]] = {}
        for item in raw_rows:
            parsed = _normalize_comparison_row(_as_mapping(item))
            by_market[str(parsed["market"])] = parsed
        rows = []
        for market in selected:
            if market not in by_market:
                _fail()
            rows.append(by_market[market])
        order = {key: index for index, key in enumerate(RESULTS_DISPLAY_MARKETS)}
        rows.sort(key=lambda item: order[str(item["market"])])
        if highest not in selected:
            _fail()
    elif kind == KIND_CASE:
        if len(selected) != 1:
            _fail()
        summary = _read_json_object(output / "summary.json")
        if _text(summary, "market") != selected[0]:
            _fail()
        rows = [_normalize_case_row(summary, selected[0])]
        highest = selected[0]
    else:
        _fail()
        raise ResultViewError(ERROR_RESULTS_BODY)
    first_dir = _case_directory(kind, output, selected[0])
    first_resolved = _read_json_object(first_dir / "resolved_config.json")
    pv_included = _pv_included(first_resolved)
    wind_included = _wind_included(first_resolved)
    if wind_included:
        for row in rows:
            if any(key not in row for key in WIND_FLOAT_FIELDS):
                _fail()
    activation = None
    balancing_market = next((item for item in selected if item in BALANCING_MARKETS), None)
    if balancing_market is not None:
        balancing_resolved = (
            first_resolved
            if balancing_market == selected[0]
            else _read_json_object(
                _case_directory(kind, output, balancing_market) / "resolved_config.json"
            )
        )
        activation = _nested(balancing_resolved, "config", "market_case").get("activation_profile")
    display_markets = display_market_keys(selected)
    header = _header_from_resolved(
        resolved=first_resolved,
        markets=display_markets,
        period=period,
        source=source,
        activation_profile=activation,
    )
    formatted_rows = [
        _format_row(
            row,
            one_market=len(selected) == 1,
            pv_included=pv_included,
            wind_included=wind_included,
        )
        for row in rows
    ]
    children: dict[str, Any] = {}
    for row in rows:
        market = str(row["market"])
        child_dir = _case_directory(kind, output, market)
        children[market] = _child_display(
            directory=child_dir,
            market=market,
            row=row,
            pv_included=pv_included,
            wind_included=wind_included,
        )
    highest_row = next(item for item in formatted_rows if item["market"] == highest)
    return {
        "kind": kind,
        "source": source,
        "one_market": len(selected) == 1,
        "markets": display_markets,
        "highest_revenue_market": highest,
        "pv_included": pv_included,
        "wind_included": wind_included,
        "header": header,
        "rows": formatted_rows,
        "children": children,
        "highest_total": highest_row["formatted"]["total"],
        "highest_label": market_label(highest),
    }


def load_result_display(
    result: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> dict[str, Any]:
    try:
        validated = validate_result_record(result, job=job, outputs_root=outputs_root)
    except (OSError, TypeError, ValueError):
        raise ResultViewError(ERROR_RESULTS_BODY) from None
    try:
        return read_result_artifacts(
            kind=str(validated["kind"]),
            markets=list(validated["markets"]),
            directory=Path(str(validated["output_directory"])),
            source=str(validated["source"]),
            period=dict(validated["period"]),
        )
    except ResultViewError:
        raise
    except (OSError, TypeError, ValueError, KeyError, ZeroDivisionError):
        raise ResultViewError(ERROR_RESULTS_BODY) from None
