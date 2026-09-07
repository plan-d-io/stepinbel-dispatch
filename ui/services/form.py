"""JSON-compatible Configure form defaults and transitions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from stepinbel.config import AssetConfig

from ui.presentation.tokens import PERIOD_2025, PERIOD_2026, PERIOD_CUSTOM
from ui.services.paths import CANONICAL_MARKETS

PRESET_2025 = "2025"
PRESET_2026 = "2026"
PRESET_CUSTOM = "custom"
PERIOD_PRESET_LABELS = {
    PRESET_2025: PERIOD_2025,
    PRESET_2026: PERIOD_2026,
    PRESET_CUSTOM: PERIOD_CUSTOM,
}
LABEL_TO_PRESET = {label: key for key, label in PERIOD_PRESET_LABELS.items()}
ACTIVATION_LABELS = {"balanced": "Balanced", "passive": "Passive"}
BID_HISTORICAL = "historical"
BID_FIXED = "fixed"
PV_MODE_DA = "da"
PV_MODE_FIXED = "fixed"
PV_VALUATION_LABELS = {
    PV_MODE_DA: "Day-ahead prices",
    PV_MODE_FIXED: "Fixed price",
}
STORAGE_HOURS = "hours"
STORAGE_POND = "pond"


def is_missing_number(value: object) -> bool:
    return value is None or value == "" or isinstance(value, bool)


def coalesce_float(value: object, default: float) -> float:
    """Return *value* as float, keeping 0.0. Only None, blank, or bools use *default*."""
    if is_missing_number(value):
        return float(default)
    return float(value)


def optional_float(value: object) -> float | None:
    if is_missing_number(value):
        return None
    return float(value)


def first_present_float(*values: object, default: float) -> float:
    for value in values:
        if not is_missing_number(value):
            return float(value)
    return float(default)


def _asset_defaults() -> AssetConfig:
    return AssetConfig()


def default_live_form() -> dict[str, Any]:
    asset = _asset_defaults()
    rating = float(asset.power_pump_mw)
    return {
        "market_da": True,
        "market_mfrr": True,
        "market_afrr": True,
        "activation": "balanced",
        "period_preset": PRESET_2025,
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        "separate_machines": False,
        "common_mw": rating,
        "pump_mw": float(asset.power_pump_mw),
        "turbine_mw": float(asset.power_turbine_mw),
        "eta_pump": float(asset.eta_pump),
        "eta_turbine": float(asset.eta_turbine),
        "storage_mode": STORAGE_HOURS,
        "storage_hours": coalesce_float(asset.storage_hours, 4.0),
        "pond_energy_mwh": float(asset.e_max_mwh()),
        "soc_initial": float(asset.soc_initial_frac),
        "soc_terminal": float(asset.soc_terminal_frac),
        "enforce_terminal": bool(asset.enforce_terminal_soc),
        "pump_ramp_up_min": float(asset.pump_ramp_up_min),
        "pump_ramp_down_min": float(asset.pump_ramp_down_min),
        "pump_ramp_power_frac": float(asset.pump_ramp_power_frac),
        "turbine_ramp_up_min": float(asset.turbine_ramp_up_min),
        "turbine_ramp_down_min": float(asset.turbine_ramp_down_min),
        "turbine_ramp_power_frac": float(asset.turbine_ramp_power_frac),
        "separate_grid": False,
        "grid_follow": True,
        "grid_common_mw": rating,
        "grid_import_mw": rating,
        "grid_export_mw": rating,
        "pv_enabled": False,
        "pv_ac_kw": 500.0,
        "pv_revenue_mode": PV_MODE_DA,
        "pv_fixed_price": None,
        "bid_kind": BID_HISTORICAL,
        "bid_quantile": 0.50,
        "capacity_coverage_hours": 4.0,
        "mfrr_fixed_up": 16.0,
        "afrr_fixed_up": 16.0,
        "afrr_fixed_down": 16.0,
        "afrr_up_fraction": 1.0,
        "detailed_solver": False,
    }


def selected_markets(form: Mapping[str, Any]) -> list[str]:
    selected: list[str] = []
    flags = {
        "da": bool(form.get("market_da")),
        "mfrr": bool(form.get("market_mfrr")),
        "afrr": bool(form.get("market_afrr")),
    }
    for key in CANONICAL_MARKETS:
        if flags[key]:
            selected.append(key)
    return selected


def has_balancing(form: Mapping[str, Any]) -> bool:
    return bool(form.get("market_mfrr") or form.get("market_afrr"))


def pump_rating(form: Mapping[str, Any]) -> float:
    if form.get("separate_machines"):
        return coalesce_float(form.get("pump_mw"), 0.0)
    return coalesce_float(form.get("common_mw"), 0.0)


def turbine_rating(form: Mapping[str, Any]) -> float:
    if form.get("separate_machines"):
        return coalesce_float(form.get("turbine_mw"), 0.0)
    return coalesce_float(form.get("common_mw"), 0.0)


def suggested_grid_mw(form: Mapping[str, Any]) -> float:
    return max(pump_rating(form), turbine_rating(form))


def apply_form_transitions(
    previous: Mapping[str, Any] | None,
    incoming: Mapping[str, Any],
    *,
    reset_grid: bool = False,
) -> dict[str, Any]:
    form = deepcopy(dict(incoming))
    prior = dict(previous or {})
    if form.get("separate_machines") and not prior.get("separate_machines"):
        common = first_present_float(prior.get("common_mw"), form.get("common_mw"), default=1.0)
        form["pump_mw"] = common
        form["turbine_mw"] = common
    elif prior.get("separate_machines") and not form.get("separate_machines"):
        form["common_mw"] = max(
            coalesce_float(prior.get("pump_mw"), 0.0),
            coalesce_float(prior.get("turbine_mw"), 0.0),
        )
    if form.get("separate_grid") and not prior.get("separate_grid"):
        common = first_present_float(
            prior.get("grid_common_mw"),
            form.get("grid_common_mw"),
            default=suggested_grid_mw(form),
        )
        form["grid_import_mw"] = common
        form["grid_export_mw"] = common
    elif prior.get("separate_grid") and not form.get("separate_grid"):
        form["grid_common_mw"] = max(
            coalesce_float(prior.get("grid_import_mw"), 0.0),
            coalesce_float(prior.get("grid_export_mw"), 0.0),
        )
    if form.get("storage_mode") == STORAGE_POND and prior.get("storage_mode") != STORAGE_POND:
        hours_form = dict(form)
        hours_form["storage_mode"] = STORAGE_HOURS
        try:
            form["pond_energy_mwh"] = float(
                AssetConfig(
                    power_pump_mw=pump_rating(hours_form),
                    power_turbine_mw=turbine_rating(hours_form),
                    eta_pump=coalesce_float(hours_form.get("eta_pump"), AssetConfig().eta_pump),
                    eta_turbine=coalesce_float(hours_form.get("eta_turbine"), AssetConfig().eta_turbine),
                    storage_hours=coalesce_float(hours_form.get("storage_hours"), 4.0),
                    pond_energy_mwh=None,
                ).e_max_mwh()
            )
        except Exception:
            pass
    if reset_grid:
        form["grid_follow"] = True
    elif prior.get("grid_follow"):
        if form.get("separate_grid"):
            if coalesce_float(form.get("grid_import_mw"), 0.0) != coalesce_float(
                prior.get("grid_import_mw"), 0.0
            ) or coalesce_float(form.get("grid_export_mw"), 0.0) != coalesce_float(
                prior.get("grid_export_mw"), 0.0
            ):
                form["grid_follow"] = False
        elif coalesce_float(form.get("grid_common_mw"), 0.0) != coalesce_float(
            prior.get("grid_common_mw"), 0.0
        ):
            form["grid_follow"] = False
    if form.get("grid_follow"):
        suggested = suggested_grid_mw(form)
        form["grid_common_mw"] = suggested
        form["grid_import_mw"] = suggested
        form["grid_export_mw"] = suggested
    return form


def grid_override_help(suggested_mw: float) -> str:
    return f"Custom grid limit. Suggested value: {suggested_mw:.3f} MW."


def form_fingerprint(form: Mapping[str, Any]) -> str:
    import hashlib
    import json

    payload = json.dumps(form, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
