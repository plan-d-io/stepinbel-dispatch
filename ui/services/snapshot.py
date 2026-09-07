"""Validate Configure input and produce a JSON-compatible snapshot."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from typing import Any, Mapping

from stepinbel.config import ConfigError
from stepinbel.data import DataAccessError, DataBundleError, resolve_period

from ui.services.configs import build_simulation_configs, form_period
from ui.services.errors import user_facing_error
from ui.flow import is_exact_int
from ui.services.form import (
    BID_FIXED,
    BID_HISTORICAL,
    PRESET_2025,
    PRESET_2026,
    PRESET_CUSTOM,
    form_fingerprint,
    has_balancing,
    selected_markets,
)
from ui.services.paths import CANONICAL_MARKETS, DEMO_IDENTITY, MARKET_LABELS
from ui.services.period import parse_iso_date, published_bundle

SNAPSHOT_SCHEMA_VERSION = 1
LIVE_IDENTITY = "live"

PRE_2025_BALANCING = (
    "mFRR and aFRR cannot start before 2025. Change the markets or the period."
)
MARKET_REQUIRED = "Select at least one market."
FIXED_PV_PRICE_REQUIRED = "Enter a finite fixed PV export price."
EXECUTION_DISABLED_REASON = "Execution and result opening are not connected yet."


def lightweight_continue_reason(form: Mapping[str, Any]) -> str | None:
    markets = selected_markets(form)
    if not markets:
        return MARKET_REQUIRED
    start = form.get("start_date")
    try:
        start_date = date.fromisoformat(str(start))
    except ValueError:
        return "Enter a valid simulation period start."
    if start_date.year < 2025 and has_balancing(form):
        return PRE_2025_BALANCING
    if form.get("pv_enabled") and form.get("pv_revenue_mode") == "fixed":
        price = form.get("pv_fixed_price")
        try:
            if price in (None, "") or not math.isfinite(float(price)):
                return FIXED_PV_PRICE_REQUIRED
        except (TypeError, ValueError):
            return FIXED_PV_PRICE_REQUIRED
    return None


def as_serialisable(payload: Mapping[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False, default=_json_default)
    loaded = json.loads(encoded)
    if not isinstance(loaded, dict):
        raise TypeError("snapshot must be a JSON object")
    return loaded


def snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    payload = as_serialisable(snapshot)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"not JSON-compatible: {type(value)!r}")


def build_snapshot(form: Mapping[str, Any], *, demo: bool) -> dict[str, Any]:
    reason = lightweight_continue_reason(form)
    if reason:
        raise ValueError(reason)
    try:
        configs = build_simulation_configs(form)
        bundle = published_bundle()
        resolved = None
        for _market, config in configs:
            resolved = resolve_period(bundle, config)
    except (ConfigError, DataAccessError, DataBundleError) as exc:
        raise ValueError(user_facing_error(exc)) from exc
    except ValueError:
        raise
    if resolved is None:
        raise ValueError("The selected period could not be resolved.")
    first = configs[0][1]
    asset = first.asset
    site = first.site
    start, end = form_period(form)
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "identity": DEMO_IDENTITY if demo else LIVE_IDENTITY,
        "demo": bool(demo),
        "form_fingerprint": form_fingerprint(form),
        "markets": selected_markets(form),
        "period": {
            "preset": form.get("period_preset"),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        "activation": None if not has_balancing(form) else form.get("activation"),
        "asset": {
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
        },
        "site": {
            "grid_import_mw": float(site.grid_import_mw),
            "grid_export_mw": float(site.grid_export_mw),
            "pv_ac_kw": float(site.pv_ac_kw),
            "pv_region": site.pv_region,
            "pv_revenue_mode": site.pv_revenue_mode,
            "pv_fixed_price_eur_mwh": site.pv_fixed_price_eur_mwh,
        },
        "balancing": _balancing_snapshot(form),
        "detailed_solver_output": bool(form.get("detailed_solver")),
        "derived": {
            "e_max_mwh": float(asset.e_max_mwh()),
            "round_trip_efficiency": float(asset.round_trip_efficiency()),
            "interval_count": int(resolved.window.interval_count),
            "duration_hours": float(resolved.window.duration_hours),
            "coverage_ok": True,
            "configs_valid": True,
        },
        "form": as_serialisable(form),
    }
    return as_serialisable(snapshot)


def _balancing_snapshot(form: Mapping[str, Any]) -> dict[str, Any] | None:
    if not has_balancing(form):
        return None
    return {
        "activation": form.get("activation"),
        "bid_kind": form.get("bid_kind"),
        "bid_quantile": form.get("bid_quantile"),
        "capacity_coverage_hours": form.get("capacity_coverage_hours"),
        "mfrr_fixed_up": form.get("mfrr_fixed_up") if form.get("market_mfrr") else None,
        "afrr_fixed_up": form.get("afrr_fixed_up") if form.get("market_afrr") else None,
        "afrr_fixed_down": form.get("afrr_fixed_down") if form.get("market_afrr") else None,
        "afrr_up_fraction": form.get("afrr_up_fraction") if form.get("market_afrr") else None,
    }


INCOMPLETE_SNAPSHOT = "The stored snapshot is incomplete or could not be read."
UNSUPPORTED_SNAPSHOT = "The stored snapshot is not a supported version."
STALE_SNAPSHOT = "Configured inputs have changed. Return to Configure and continue again."

_ASSET_REQUIRED = (
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
_ASSET_FINITE = (
    "power_pump_mw",
    "power_turbine_mw",
    "eta_pump",
    "eta_turbine",
    "soc_initial_frac",
    "soc_terminal_frac",
    "pump_ramp_up_min",
    "pump_ramp_down_min",
    "pump_ramp_power_frac",
    "turbine_ramp_up_min",
    "turbine_ramp_down_min",
    "turbine_ramp_power_frac",
)
_SITE_REQUIRED = (
    "grid_import_mw",
    "grid_export_mw",
    "pv_ac_kw",
    "pv_region",
    "pv_revenue_mode",
    "pv_fixed_price_eur_mwh",
)
_DERIVED_REQUIRED = (
    "e_max_mwh",
    "round_trip_efficiency",
    "interval_count",
    "duration_hours",
    "coverage_ok",
    "configs_valid",
)
_BALANCING_REQUIRED = (
    "activation",
    "bid_kind",
    "bid_quantile",
    "capacity_coverage_hours",
    "mfrr_fixed_up",
    "afrr_fixed_up",
    "afrr_fixed_down",
    "afrr_up_fraction",
)


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _optional_finite_number(value: object) -> bool:
    return value is None or _finite_number(value)


def _canonical_markets(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    if any(not isinstance(item, str) for item in value):
        return False
    if len(value) != len(set(value)):
        return False
    allowed = set(CANONICAL_MARKETS)
    if any(item not in allowed for item in value):
        return False
    expected = [item for item in CANONICAL_MARKETS if item in value]
    return value == expected


def _period_is_valid(period: object) -> bool:
    if not isinstance(period, Mapping):
        return False
    if period.get("preset") not in {PRESET_2025, PRESET_2026, PRESET_CUSTOM}:
        return False
    start = parse_iso_date(period.get("start_date"))
    end = parse_iso_date(period.get("end_date"))
    if start is None or end is None or end < start:
        return False
    return True


def _asset_is_valid(asset: object) -> bool:
    if not isinstance(asset, Mapping) or any(key not in asset for key in _ASSET_REQUIRED):
        return False
    if not all(_finite_number(asset.get(key)) for key in _ASSET_FINITE):
        return False
    if not isinstance(asset.get("enforce_terminal_soc"), bool):
        return False
    if asset.get("storage_hours_basis") not in {"discharge_at_rated", "stored_energy"}:
        return False
    hours = asset.get("storage_hours")
    pond = asset.get("pond_energy_mwh")
    if not _optional_finite_number(hours) or not _optional_finite_number(pond):
        return False
    if (hours is None) == (pond is None):
        return False
    return True


def _site_is_valid(site: object) -> bool:
    if not isinstance(site, Mapping) or any(key not in site for key in _SITE_REQUIRED):
        return False
    if not all(_finite_number(site.get(key)) for key in ("grid_import_mw", "grid_export_mw", "pv_ac_kw")):
        return False
    if not isinstance(site.get("pv_region"), str) or not site.get("pv_region"):
        return False
    if site.get("pv_revenue_mode") not in {"da", "fixed"}:
        return False
    return _optional_finite_number(site.get("pv_fixed_price_eur_mwh"))


def _balancing_is_valid(snapshot: Mapping[str, Any]) -> bool:
    markets = snapshot.get("markets")
    if not isinstance(markets, list):
        return False
    balancing_selected = "mfrr" in markets or "afrr" in markets
    activation = snapshot.get("activation")
    balancing = snapshot.get("balancing")
    if not balancing_selected:
        return activation is None and balancing is None
    if activation not in {"balanced", "passive"}:
        return False
    if not isinstance(balancing, Mapping) or any(key not in balancing for key in _BALANCING_REQUIRED):
        return False
    if balancing.get("activation") != activation:
        return False
    if balancing.get("bid_kind") not in {BID_HISTORICAL, BID_FIXED}:
        return False
    if not _finite_number(balancing.get("capacity_coverage_hours")):
        return False
    if balancing.get("bid_kind") == BID_HISTORICAL:
        if not _finite_number(balancing.get("bid_quantile")):
            return False
    if "mfrr" in markets and balancing.get("bid_kind") == BID_FIXED:
        if not _finite_number(balancing.get("mfrr_fixed_up")):
            return False
    elif balancing.get("mfrr_fixed_up") is not None and "mfrr" not in markets:
        return False
    if "afrr" in markets:
        if not _finite_number(balancing.get("afrr_up_fraction")):
            return False
        if balancing.get("bid_kind") == BID_FIXED:
            if not _finite_number(balancing.get("afrr_fixed_up")):
                return False
            if not _finite_number(balancing.get("afrr_fixed_down")):
                return False
    return True


def _derived_is_valid(derived: object) -> bool:
    if not isinstance(derived, Mapping) or any(key not in derived for key in _DERIVED_REQUIRED):
        return False
    if not _finite_number(derived.get("e_max_mwh")):
        return False
    if not _finite_number(derived.get("round_trip_efficiency")):
        return False
    if not _finite_number(derived.get("duration_hours")):
        return False
    count = derived.get("interval_count")
    if type(count) is not int or count < 1:
        return False
    if not isinstance(derived.get("coverage_ok"), bool) or not derived.get("coverage_ok"):
        return False
    if not isinstance(derived.get("configs_valid"), bool) or not derived.get("configs_valid"):
        return False
    return True


def snapshot_is_complete(snapshot: Mapping[str, Any] | None) -> bool:
    try:
        return _snapshot_integrity_reason(snapshot) is None
    except Exception:
        return False


def _snapshot_integrity_reason(snapshot: Mapping[str, Any] | None) -> str | None:
    if not isinstance(snapshot, Mapping):
        return INCOMPLETE_SNAPSHOT
    version = snapshot.get("schema_version")
    if version is None:
        return UNSUPPORTED_SNAPSHOT
    if not is_exact_int(version, SNAPSHOT_SCHEMA_VERSION):
        return UNSUPPORTED_SNAPSHOT
    identity = snapshot.get("identity")
    demo = snapshot.get("demo")
    if not isinstance(demo, bool):
        return INCOMPLETE_SNAPSHOT
    if identity == LIVE_IDENTITY:
        if demo is not False:
            return INCOMPLETE_SNAPSHOT
    elif identity == DEMO_IDENTITY:
        if demo is not True:
            return INCOMPLETE_SNAPSHOT
    else:
        return INCOMPLETE_SNAPSHOT
    if not _canonical_markets(snapshot.get("markets")):
        return INCOMPLETE_SNAPSHOT
    if not _period_is_valid(snapshot.get("period")):
        return INCOMPLETE_SNAPSHOT
    if not _asset_is_valid(snapshot.get("asset")):
        return INCOMPLETE_SNAPSHOT
    if not _site_is_valid(snapshot.get("site")):
        return INCOMPLETE_SNAPSHOT
    if not _balancing_is_valid(snapshot):
        return INCOMPLETE_SNAPSHOT
    if not _derived_is_valid(snapshot.get("derived")):
        return INCOMPLETE_SNAPSHOT
    if not isinstance(snapshot.get("detailed_solver_output"), bool):
        return INCOMPLETE_SNAPSHOT
    form = snapshot.get("form")
    fingerprint = snapshot.get("form_fingerprint")
    if not isinstance(form, Mapping) or not isinstance(fingerprint, str) or not fingerprint:
        return INCOMPLETE_SNAPSHOT
    if form_fingerprint(form) != fingerprint:
        return INCOMPLETE_SNAPSHOT
    return None


def snapshot_block_reason(
    snapshot: Mapping[str, Any] | None,
    form: Mapping[str, Any] | None,
) -> str | None:
    try:
        if snapshot is None:
            return "No configured snapshot is available."
        integrity = _snapshot_integrity_reason(snapshot)
        if integrity is not None:
            return integrity
        if snapshot_is_stale(form, snapshot):
            return STALE_SNAPSHOT
        return None
    except Exception:
        return INCOMPLETE_SNAPSHOT


def snapshot_is_stale(state_form: Mapping[str, Any] | None, snapshot: Mapping[str, Any] | None) -> bool:
    try:
        if not isinstance(snapshot, Mapping):
            return True
        if not isinstance(state_form, Mapping):
            return True
        fingerprint = snapshot.get("form_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            return True
        return fingerprint != form_fingerprint(state_form)
    except Exception:
        return True


def review_warnings(snapshot: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    try:
        if not isinstance(snapshot, Mapping):
            return warnings
        asset = snapshot.get("asset") if isinstance(snapshot.get("asset"), Mapping) else {}
        site = snapshot.get("site") if isinstance(snapshot.get("site"), Mapping) else {}
        period = snapshot.get("period") if isinstance(snapshot.get("period"), Mapping) else {}
        try:
            pump = float(asset.get("power_pump_mw"))
            turbine = float(asset.get("power_turbine_mw"))
            grid_in = float(site.get("grid_import_mw"))
            grid_out = float(site.get("grid_export_mw"))
            pv_raw = site.get("pv_ac_kw")
            pv_mw = (0.0 if pv_raw is None else float(pv_raw)) / 1000.0
        except (TypeError, ValueError):
            return warnings
        if isinstance(asset.get("power_pump_mw"), bool) or isinstance(site.get("grid_import_mw"), bool):
            return warnings
        if grid_in < pump:
            warnings.append("Grid import is below the pump rating.")
        if grid_out < turbine:
            warnings.append("Grid export is below the turbine rating.")
        if pv_mw > grid_out:
            warnings.append("Installed PV exceeds the export-side grid limit.")
        if period.get("preset") == PRESET_CUSTOM:
            start = parse_iso_date(period.get("start_date"))
            end = parse_iso_date(period.get("end_date"))
            if start is None or end is None or end < start:
                return warnings
            if (end - start).days + 1 < 365:
                warnings.append("The custom period is shorter than a full year.")
        return warnings
    except Exception:
        return warnings


def market_labels(markets: list[str]) -> str:
    return " · ".join(MARKET_LABELS[key] for key in CANONICAL_MARKETS if key in markets)


def format_mw(value: float) -> str:
    return f"{value:.3f} MW"


def format_mwh(value: float) -> str:
    return f"{value:.3f} MWh"
