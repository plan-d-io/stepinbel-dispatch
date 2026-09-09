"""Explicit JSON schema for CaseRunRequest. No asdict or pickle."""

from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from stepinbel.config import (
    AFRRCase,
    AssetConfig,
    BelgianDeliveryPeriod,
    ConfigError,
    DayAheadCase,
    FixedMinimumCapacityBid,
    HistoricalQuantileCapacityBid,
    MachineCommitmentConfig,
    MFRRCase,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.optimizer import SolverOptions
from stepinbel.workflows.constants import (
    BEHAVIOURAL_BASELINE,
    CASE_RUN_REQUEST_SCHEMA_VERSION,
    CASE_RUN_REQUEST_SCHEMA_VERSION_V2,
    RUN_ARTIFACT_SCHEMA_VERSION,
    RUN_ARTIFACT_SCHEMA_VERSION_V2,
    SUPPORTED_CASE_SCHEMA_VERSIONS,
)
from stepinbel.workflows.errors import RunRequestError

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_FORBIDDEN_TOKENS = {
    "god",
    "active",
    "fcr",
    "named_period",
    "preset",
    "year_2025",
    "full_year",
}


def format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise RunRequestError("UTC timestamps must be timezone-aware", category="invalid_request")
    utc = value.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RunRequestError(f"{field} must be an explicit Zulu UTC timestamp", category="invalid_request")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RunRequestError(f"{field} is not a valid UTC timestamp", category="invalid_request") from exc
    return parsed


def parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise RunRequestError(f"{field} must be an ISO date", category="invalid_request")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RunRequestError(f"{field} is not a valid ISO date", category="invalid_request") from exc


def require_finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunRequestError(f"{field} must be a finite number", category="invalid_request")
    number = float(value)
    if not math.isfinite(number):
        raise RunRequestError(f"{field} must be a finite number", category="invalid_request")
    return number


def require_optional_finite(value: object, field: str) -> float | None:
    if value is None:
        return None
    return require_finite(value, field)


def require_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise RunRequestError(f"{field} must be a boolean", category="invalid_request")
    return value


def require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RunRequestError(f"{field} must be a non-empty string", category="invalid_request")
    return value


def require_mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RunRequestError(f"{field} must be an object", category="invalid_request")
    return value


def require_schema_version(value: object, allowed: frozenset[int], field: str) -> int:
    if type(value) is not int or value not in allowed:
        raise RunRequestError(f"{field} is not supported", category="invalid_request")
    return value


def preferred_case_schema_versions(config: SimulationConfig) -> tuple[int, int]:
    if config.machine_commitment.physically_active():
        return CASE_RUN_REQUEST_SCHEMA_VERSION_V2, RUN_ARTIFACT_SCHEMA_VERSION_V2
    return CASE_RUN_REQUEST_SCHEMA_VERSION, RUN_ARTIFACT_SCHEMA_VERSION


def require_matching_case_schema_versions(request_version: int, artifact_version: int) -> None:
    if request_version == CASE_RUN_REQUEST_SCHEMA_VERSION:
        if artifact_version != RUN_ARTIFACT_SCHEMA_VERSION:
            raise RunRequestError(
                "schema-v1 requests must use artifact schema version 1",
                category="invalid_request",
            )
        return
    if request_version == CASE_RUN_REQUEST_SCHEMA_VERSION_V2:
        if artifact_version != RUN_ARTIFACT_SCHEMA_VERSION_V2:
            raise RunRequestError(
                "schema-v2 requests must use artifact schema version 2",
                category="invalid_request",
            )
        return
    raise RunRequestError("request_schema_version is not supported", category="invalid_request")


def require_keys(payload: Mapping[str, Any], keys: tuple[str, ...], field: str) -> None:
    missing = [key for key in keys if key not in payload]
    extra = [key for key in payload if key not in keys]
    if missing:
        raise RunRequestError(f"{field} is missing {missing[0]}", category="invalid_request")
    if extra:
        raise RunRequestError(f"{field} contains unexpected field {extra[0]}", category="invalid_request")


def validate_run_id(value: object) -> str:
    run_id = require_str(value, "run_id")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise RunRequestError("run_id contains unsupported characters", category="invalid_request")
    return run_id


def require_utc_seconds(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise RunRequestError(f"{field} must be a datetime", category="invalid_request")
    if value.tzinfo is None:
        raise RunRequestError(f"{field} must be timezone-aware UTC", category="invalid_request")
    offset = value.utcoffset()
    if offset is None or offset != timedelta(0):
        raise RunRequestError(f"{field} must have a zero UTC offset", category="invalid_request")
    if value.microsecond != 0:
        raise RunRequestError(f"{field} must have second precision", category="invalid_request")
    return value.replace(tzinfo=timezone.utc)


def require_absolute_path(value: object, field: str) -> Path:
    try:
        if isinstance(value, Path):
            path = value
        else:
            path = Path(require_str(value, field))
    except (TypeError, ValueError) as exc:
        raise RunRequestError(f"{field} is not a valid path", category="invalid_request") from exc
    if not path.is_absolute():
        raise RunRequestError(f"{field} must be an absolute path", category="invalid_request")
    try:
        normalized = Path(os.path.normpath(str(path)))
    except (TypeError, ValueError, OSError) as exc:
        raise RunRequestError(f"{field} is not a valid path", category="invalid_request") from exc
    if not normalized.is_absolute():
        raise RunRequestError(f"{field} must be an absolute path", category="invalid_request")
    return normalized


def require_sha256(value: object, field: str) -> str:
    digest = require_str(value, field)
    if not _SHA256_RE.fullmatch(digest):
        raise RunRequestError(f"{field} is not a SHA-256 hex digest", category="invalid_request")
    return digest.lower()


def resolve_builder_path(value: object, field: str) -> Path:
    try:
        path = Path(value).expanduser()
        if path.is_absolute():
            return Path(os.path.normpath(str(path))).resolve()
        return path.resolve()
    except (TypeError, ValueError, OSError) as exc:
        raise RunRequestError(f"{field} is not a valid path", category="invalid_request") from exc


def dumps_json(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, ensure_ascii=False) + "\n"
    except ValueError as exc:
        raise RunRequestError("JSON payload contains NaN or infinity", category="invalid_request") from exc


def dumps_jsonl(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(payload, sort_keys=True, allow_nan=False, ensure_ascii=False) + "\n"
    except ValueError as exc:
        raise RunRequestError("JSON payload contains NaN or infinity", category="invalid_request") from exc


def loads_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise RunRequestError("request JSON is corrupt", category="invalid_request") from exc
    if not isinstance(payload, dict):
        raise RunRequestError("request JSON must be an object", category="invalid_request")
    return payload


def _reject_constant(value: str) -> None:
    raise RunRequestError(f"JSON payload contains {value}", category="invalid_request")


def serialize_period(period: BelgianDeliveryPeriod | UtcPeriod) -> dict[str, Any]:
    if isinstance(period, BelgianDeliveryPeriod):
        return {
            "kind": "belgian_delivery",
            "start_date": period.start_date.isoformat(),
            "end_date_inclusive": period.end_date_inclusive.isoformat(),
        }
    if isinstance(period, UtcPeriod):
        return {
            "kind": "utc",
            "start_utc": format_utc(period.start_utc),
            "end_exclusive_utc": format_utc(period.end_exclusive_utc),
        }
    raise RunRequestError("unsupported period kind", category="invalid_request")


def deserialize_period(payload: object) -> BelgianDeliveryPeriod | UtcPeriod:
    body = require_mapping(payload, "period")
    kind = require_str(body.get("kind"), "period.kind")
    lowered = kind.lower()
    if lowered in _FORBIDDEN_TOKENS or kind in {"preset", "named", "year"}:
        raise RunRequestError(f"period kind {kind!r} is not supported", category="invalid_request")
    try:
        if kind == "belgian_delivery":
            require_keys(body, ("kind", "start_date", "end_date_inclusive"), "period")
            return BelgianDeliveryPeriod(
                parse_date(body["start_date"], "period.start_date"),
                parse_date(body["end_date_inclusive"], "period.end_date_inclusive"),
            )
        if kind == "utc":
            require_keys(body, ("kind", "start_utc", "end_exclusive_utc"), "period")
            return UtcPeriod(
                parse_utc(body["start_utc"], "period.start_utc"),
                parse_utc(body["end_exclusive_utc"], "period.end_exclusive_utc"),
            )
    except ConfigError as exc:
        raise RunRequestError(str(exc), category="invalid_configuration") from exc
    raise RunRequestError(f"unknown period kind {kind!r}", category="invalid_request")


def serialize_bid(bid: HistoricalQuantileCapacityBid | FixedMinimumCapacityBid) -> dict[str, Any]:
    if isinstance(bid, HistoricalQuantileCapacityBid):
        return {"kind": "historical_quantile", "quantile": float(bid.quantile)}
    if isinstance(bid, FixedMinimumCapacityBid):
        return {
            "kind": "fixed_minimum",
            "upward_price_eur_mw_h": float(bid.upward_price_eur_mw_h),
            "downward_price_eur_mw_h": (
                None
                if bid.downward_price_eur_mw_h is None
                else float(bid.downward_price_eur_mw_h)
            ),
        }
    raise RunRequestError("unsupported capacity bid", category="invalid_request")


def deserialize_bid(payload: object) -> HistoricalQuantileCapacityBid | FixedMinimumCapacityBid:
    body = require_mapping(payload, "capacity_bid")
    kind = require_str(body.get("kind"), "capacity_bid.kind")
    try:
        if kind == "historical_quantile":
            require_keys(body, ("kind", "quantile"), "capacity_bid")
            return HistoricalQuantileCapacityBid(require_finite(body["quantile"], "quantile"))
        if kind == "fixed_minimum":
            require_keys(
                body,
                ("kind", "upward_price_eur_mw_h", "downward_price_eur_mw_h"),
                "capacity_bid",
            )
            return FixedMinimumCapacityBid(
                require_finite(body["upward_price_eur_mw_h"], "upward_price_eur_mw_h"),
                require_optional_finite(
                    body["downward_price_eur_mw_h"], "downward_price_eur_mw_h"
                ),
            )
    except ConfigError as exc:
        raise RunRequestError(str(exc), category="invalid_configuration") from exc
    raise RunRequestError(f"unknown capacity bid kind {kind!r}", category="invalid_request")


def serialize_market_case(case: DayAheadCase | MFRRCase | AFRRCase) -> dict[str, Any]:
    if isinstance(case, DayAheadCase):
        return {"kind": "DayAheadCase", "market": "da"}
    if isinstance(case, MFRRCase):
        return {
            "kind": "MFRRCase",
            "market": "mfrr",
            "activation_profile": case.activation_profile,
            "capacity_coverage_hours": float(case.capacity_coverage_hours),
            "capacity_bid": serialize_bid(case.capacity_bid),
        }
    if isinstance(case, AFRRCase):
        return {
            "kind": "AFRRCase",
            "market": "afrr",
            "activation_profile": case.activation_profile,
            "capacity_coverage_hours": float(case.capacity_coverage_hours),
            "up_capacity_fraction": float(case.up_capacity_fraction),
            "capacity_bid": serialize_bid(case.capacity_bid),
        }
    raise RunRequestError("unsupported market case", category="invalid_request")


def deserialize_market_case(payload: object) -> DayAheadCase | MFRRCase | AFRRCase:
    body = require_mapping(payload, "market_case")
    kind = require_str(body.get("kind"), "market_case.kind")
    market = body.get("market")
    token = f"{kind} {market}".lower()
    if any(item in token for item in ("god", "active", "fcr")):
        raise RunRequestError(f"market case {kind!r} is not supported", category="invalid_request")
    try:
        if kind == "DayAheadCase":
            require_keys(body, ("kind", "market"), "market_case")
            if body["market"] != "da":
                raise RunRequestError("DayAheadCase.market must be 'da'", category="invalid_request")
            return DayAheadCase()
        if kind == "MFRRCase":
            require_keys(
                body,
                ("kind", "market", "activation_profile", "capacity_coverage_hours", "capacity_bid"),
                "market_case",
            )
            if body["market"] != "mfrr":
                raise RunRequestError("MFRRCase.market must be 'mfrr'", category="invalid_request")
            return MFRRCase(
                activation_profile=require_str(body["activation_profile"], "activation_profile"),  # type: ignore[arg-type]
                capacity_coverage_hours=require_finite(
                    body["capacity_coverage_hours"], "capacity_coverage_hours"
                ),
                capacity_bid=deserialize_bid(body["capacity_bid"]),
            )
        if kind == "AFRRCase":
            require_keys(
                body,
                (
                    "kind",
                    "market",
                    "activation_profile",
                    "capacity_coverage_hours",
                    "up_capacity_fraction",
                    "capacity_bid",
                ),
                "market_case",
            )
            if body["market"] != "afrr":
                raise RunRequestError("AFRRCase.market must be 'afrr'", category="invalid_request")
            return AFRRCase(
                activation_profile=require_str(body["activation_profile"], "activation_profile"),  # type: ignore[arg-type]
                capacity_coverage_hours=require_finite(
                    body["capacity_coverage_hours"], "capacity_coverage_hours"
                ),
                up_capacity_fraction=require_finite(
                    body["up_capacity_fraction"], "up_capacity_fraction"
                ),
                capacity_bid=deserialize_bid(body["capacity_bid"]),
            )
    except ConfigError as exc:
        raise RunRequestError(str(exc), category="invalid_configuration") from exc
    raise RunRequestError(f"unknown market case {kind!r}", category="invalid_request")


_ASSET_KEYS = (
    "power_pump_mw",
    "power_turbine_mw",
    "eta_pump",
    "eta_turbine",
    "storage_hours",
    "storage_hours_basis",
    "pond_energy_mwh",
    "soc_initial_frac",
    "soc_terminal_frac",
    "enforce_terminal_soc",
    "pump_ramp_up_min",
    "pump_ramp_down_min",
    "pump_ramp_power_frac",
    "turbine_ramp_up_min",
    "turbine_ramp_down_min",
    "turbine_ramp_power_frac",
)


def serialize_asset(asset: AssetConfig) -> dict[str, Any]:
    return {
        "power_pump_mw": float(asset.power_pump_mw),
        "power_turbine_mw": float(asset.power_turbine_mw),
        "eta_pump": float(asset.eta_pump),
        "eta_turbine": float(asset.eta_turbine),
        "storage_hours": None if asset.storage_hours is None else float(asset.storage_hours),
        "storage_hours_basis": asset.storage_hours_basis,
        "pond_energy_mwh": None if asset.pond_energy_mwh is None else float(asset.pond_energy_mwh),
        "soc_initial_frac": float(asset.soc_initial_frac),
        "soc_terminal_frac": float(asset.soc_terminal_frac),
        "enforce_terminal_soc": bool(asset.enforce_terminal_soc),
        "pump_ramp_up_min": float(asset.pump_ramp_up_min),
        "pump_ramp_down_min": float(asset.pump_ramp_down_min),
        "pump_ramp_power_frac": float(asset.pump_ramp_power_frac),
        "turbine_ramp_up_min": float(asset.turbine_ramp_up_min),
        "turbine_ramp_down_min": float(asset.turbine_ramp_down_min),
        "turbine_ramp_power_frac": float(asset.turbine_ramp_power_frac),
    }


def deserialize_asset(payload: object) -> AssetConfig:
    body = require_mapping(payload, "asset")
    require_keys(body, _ASSET_KEYS, "asset")
    try:
        return AssetConfig(
            power_pump_mw=require_finite(body["power_pump_mw"], "power_pump_mw"),
            power_turbine_mw=require_finite(body["power_turbine_mw"], "power_turbine_mw"),
            eta_pump=require_finite(body["eta_pump"], "eta_pump"),
            eta_turbine=require_finite(body["eta_turbine"], "eta_turbine"),
            storage_hours=require_optional_finite(body["storage_hours"], "storage_hours"),
            storage_hours_basis=require_str(body["storage_hours_basis"], "storage_hours_basis"),  # type: ignore[arg-type]
            pond_energy_mwh=require_optional_finite(body["pond_energy_mwh"], "pond_energy_mwh"),
            soc_initial_frac=require_finite(body["soc_initial_frac"], "soc_initial_frac"),
            soc_terminal_frac=require_finite(body["soc_terminal_frac"], "soc_terminal_frac"),
            enforce_terminal_soc=require_bool(body["enforce_terminal_soc"], "enforce_terminal_soc"),
            pump_ramp_up_min=require_finite(body["pump_ramp_up_min"], "pump_ramp_up_min"),
            pump_ramp_down_min=require_finite(body["pump_ramp_down_min"], "pump_ramp_down_min"),
            pump_ramp_power_frac=require_finite(body["pump_ramp_power_frac"], "pump_ramp_power_frac"),
            turbine_ramp_up_min=require_finite(body["turbine_ramp_up_min"], "turbine_ramp_up_min"),
            turbine_ramp_down_min=require_finite(body["turbine_ramp_down_min"], "turbine_ramp_down_min"),
            turbine_ramp_power_frac=require_finite(
                body["turbine_ramp_power_frac"], "turbine_ramp_power_frac"
            ),
        )
    except ConfigError as exc:
        raise RunRequestError(str(exc), category="invalid_configuration") from exc


_SITE_KEYS = (
    "grid_export_mw",
    "grid_import_mw",
    "pv_ac_kw",
    "pv_region",
    "pv_revenue_mode",
    "pv_fixed_price_eur_mwh",
)


def serialize_site(site: SiteConfig) -> dict[str, Any]:
    return {
        "grid_export_mw": None if site.grid_export_mw is None else float(site.grid_export_mw),
        "grid_import_mw": None if site.grid_import_mw is None else float(site.grid_import_mw),
        "pv_ac_kw": float(site.pv_ac_kw),
        "pv_region": site.pv_region,
        "pv_revenue_mode": site.pv_revenue_mode,
        "pv_fixed_price_eur_mwh": (
            None if site.pv_fixed_price_eur_mwh is None else float(site.pv_fixed_price_eur_mwh)
        ),
    }


def deserialize_site(payload: object) -> SiteConfig:
    body = require_mapping(payload, "site")
    require_keys(body, _SITE_KEYS, "site")
    region = body["pv_region"]
    if region is not None and not isinstance(region, str):
        raise RunRequestError("pv_region must be a string or null", category="invalid_request")
    try:
        return SiteConfig(
            grid_export_mw=require_optional_finite(body["grid_export_mw"], "grid_export_mw"),
            grid_import_mw=require_optional_finite(body["grid_import_mw"], "grid_import_mw"),
            pv_ac_kw=require_finite(body["pv_ac_kw"], "pv_ac_kw"),
            pv_region=region,
            pv_revenue_mode=require_str(body["pv_revenue_mode"], "pv_revenue_mode"),  # type: ignore[arg-type]
            pv_fixed_price_eur_mwh=require_optional_finite(
                body["pv_fixed_price_eur_mwh"], "pv_fixed_price_eur_mwh"
            ),
        )
    except ConfigError as exc:
        raise RunRequestError(str(exc), category="invalid_configuration") from exc


_MACHINE_COMMITMENT_KEYS = (
    "fixed_speed_pump",
    "turbine_minimum_output_fraction",
    "forbid_simultaneous_operation",
)


def serialize_machine_commitment(commitment: MachineCommitmentConfig) -> dict[str, Any]:
    return {
        "fixed_speed_pump": bool(commitment.fixed_speed_pump),
        "turbine_minimum_output_fraction": float(commitment.turbine_minimum_output_fraction),
        "forbid_simultaneous_operation": bool(commitment.forbid_simultaneous_operation),
    }


def deserialize_machine_commitment(payload: object) -> MachineCommitmentConfig:
    body = require_mapping(payload, "machine_commitment")
    require_keys(body, _MACHINE_COMMITMENT_KEYS, "machine_commitment")
    try:
        return MachineCommitmentConfig(
            fixed_speed_pump=require_bool(body["fixed_speed_pump"], "fixed_speed_pump"),
            turbine_minimum_output_fraction=require_finite(
                body["turbine_minimum_output_fraction"],
                "turbine_minimum_output_fraction",
            ),
            forbid_simultaneous_operation=require_bool(
                body["forbid_simultaneous_operation"],
                "forbid_simultaneous_operation",
            ),
        )
    except ConfigError as exc:
        raise RunRequestError(str(exc), category="invalid_configuration") from exc


def serialize_config(config: SimulationConfig, *, schema_version: int) -> dict[str, Any]:
    payload = {
        "period": serialize_period(config.period),
        "market_case": serialize_market_case(config.market_case),
        "asset": serialize_asset(config.asset),
        "site": serialize_site(config.site),
    }
    if schema_version == CASE_RUN_REQUEST_SCHEMA_VERSION_V2:
        payload["machine_commitment"] = serialize_machine_commitment(config.machine_commitment)
        return payload
    if schema_version != CASE_RUN_REQUEST_SCHEMA_VERSION:
        raise RunRequestError("request_schema_version is not supported", category="invalid_request")
    if config.machine_commitment.physically_active():
        raise RunRequestError(
            "schema-v1 requests cannot represent enabled machine-commitment options",
            category="invalid_request",
        )
    return payload


def deserialize_config(payload: object, *, schema_version: int) -> SimulationConfig:
    body = require_mapping(payload, "config")
    if schema_version == CASE_RUN_REQUEST_SCHEMA_VERSION:
        require_keys(body, ("period", "market_case", "asset", "site"), "config")
        commitment = MachineCommitmentConfig()
    elif schema_version == CASE_RUN_REQUEST_SCHEMA_VERSION_V2:
        require_keys(
            body,
            ("period", "market_case", "asset", "site", "machine_commitment"),
            "config",
        )
        commitment = deserialize_machine_commitment(body["machine_commitment"])
    else:
        raise RunRequestError("request_schema_version is not supported", category="invalid_request")
    try:
        return SimulationConfig(
            period=deserialize_period(body["period"]),
            market_case=deserialize_market_case(body["market_case"]),
            asset=deserialize_asset(body["asset"]),
            site=deserialize_site(body["site"]),
            machine_commitment=commitment,
        )
    except ConfigError as exc:
        raise RunRequestError(str(exc), category="invalid_configuration") from exc


_SOLVER_OPTIONS_V1_KEYS = ("detailed_output",)
_SOLVER_OPTIONS_V2_KEYS = ("detailed_output", "mip_rel_gap", "time_limit_s")


def serialize_solver_options(options: SolverOptions, *, schema_version: int) -> dict[str, Any]:
    payload = {"detailed_output": bool(options.detailed_output)}
    if schema_version == CASE_RUN_REQUEST_SCHEMA_VERSION:
        return payload
    if schema_version != CASE_RUN_REQUEST_SCHEMA_VERSION_V2:
        raise RunRequestError("request_schema_version is not supported", category="invalid_request")
    payload["mip_rel_gap"] = float(options.mip_rel_gap)
    payload["time_limit_s"] = float(options.time_limit_s)
    return payload


def deserialize_solver_options(payload: object, *, schema_version: int) -> SolverOptions:
    body = require_mapping(payload, "solver_options")
    try:
        if schema_version == CASE_RUN_REQUEST_SCHEMA_VERSION:
            require_keys(body, _SOLVER_OPTIONS_V1_KEYS, "solver_options")
            return SolverOptions(
                detailed_output=require_bool(body["detailed_output"], "detailed_output")
            )
        if schema_version == CASE_RUN_REQUEST_SCHEMA_VERSION_V2:
            require_keys(body, _SOLVER_OPTIONS_V2_KEYS, "solver_options")
            return SolverOptions(
                detailed_output=require_bool(body["detailed_output"], "detailed_output"),
                mip_rel_gap=require_finite(body["mip_rel_gap"], "mip_rel_gap"),
                time_limit_s=require_finite(body["time_limit_s"], "time_limit_s"),
            )
    except RunRequestError:
        raise
    except Exception as exc:
        raise RunRequestError(str(exc), category="invalid_request") from exc
    raise RunRequestError("request_schema_version is not supported", category="invalid_request")


def serialize_baseline(baseline: Mapping[str, str]) -> dict[str, str]:
    return {
        "project": baseline["project"],
        "tag": baseline["tag"],
        "commit": baseline["commit"],
    }


def deserialize_baseline(payload: object) -> dict[str, str]:
    body = require_mapping(payload, "behavioural_baseline")
    require_keys(body, ("project", "tag", "commit"), "behavioural_baseline")
    project = require_str(body["project"], "behavioural_baseline.project")
    tag = require_str(body["tag"], "behavioural_baseline.tag")
    commit = require_str(body["commit"], "behavioural_baseline.commit")
    expected = dict(BEHAVIOURAL_BASELINE)
    if (project, tag, commit) != (expected["project"], expected["tag"], expected["commit"]):
        raise RunRequestError("behavioural baseline identity does not match the accepted PHS baseline", category="invalid_request")
    return {"project": project, "tag": tag, "commit": commit}


_REQUEST_KEYS = (
    "request_schema_version",
    "run_id",
    "created_at_utc",
    "software_version",
    "artifact_schema_version",
    "data_directory",
    "data_manifest_sha256",
    "output_directory",
    "config",
    "solver_options",
    "behavioural_baseline",
)


def request_to_payload(request: Any) -> dict[str, Any]:
    schema_version = int(request.request_schema_version)
    return {
        "request_schema_version": schema_version,
        "run_id": request.run_id,
        "created_at_utc": format_utc(request.created_at_utc),
        "software_version": request.software_version,
        "artifact_schema_version": int(request.artifact_schema_version),
        "data_directory": str(request.data_directory),
        "data_manifest_sha256": request.data_manifest_sha256,
        "output_directory": str(request.output_directory),
        "config": serialize_config(request.config, schema_version=schema_version),
        "solver_options": serialize_solver_options(
            request.solver_options, schema_version=schema_version
        ),
        "behavioural_baseline": serialize_baseline(request.behavioural_baseline),
    }


def payload_to_request_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    require_keys(payload, _REQUEST_KEYS, "request")
    request_version = require_schema_version(
        payload["request_schema_version"],
        SUPPORTED_CASE_SCHEMA_VERSIONS,
        "request_schema_version",
    )
    artifact_version = require_schema_version(
        payload["artifact_schema_version"],
        SUPPORTED_CASE_SCHEMA_VERSIONS,
        "artifact_schema_version",
    )
    require_matching_case_schema_versions(request_version, artifact_version)
    software = require_str(payload["software_version"], "software_version")
    return {
        "request_schema_version": request_version,
        "run_id": validate_run_id(payload["run_id"]),
        "created_at_utc": parse_utc(payload["created_at_utc"], "created_at_utc"),
        "software_version": software,
        "artifact_schema_version": artifact_version,
        "data_directory": require_absolute_path(payload["data_directory"], "data_directory"),
        "data_manifest_sha256": require_sha256(payload["data_manifest_sha256"], "data_manifest_sha256"),
        "output_directory": require_absolute_path(payload["output_directory"], "output_directory"),
        "config": deserialize_config(payload["config"], schema_version=request_version),
        "solver_options": deserialize_solver_options(
            payload["solver_options"], schema_version=request_version
        ),
        "behavioural_baseline": deserialize_baseline(payload["behavioural_baseline"]),
    }
