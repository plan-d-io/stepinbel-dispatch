"""Load Demo Configure values from the committed comparison request."""

from __future__ import annotations

from typing import Any

from stepinbel.workflows import load_market_comparison_request

from ui.services.form import (
    BID_FIXED,
    BID_HISTORICAL,
    PRESET_2025,
    STORAGE_HOURS,
    STORAGE_POND,
    coalesce_float,
    default_live_form,
)
from ui.services.paths import DEMO_COMPARISON_REQUEST


def demo_form() -> dict[str, Any]:
    request = load_market_comparison_request(DEMO_COMPARISON_REQUEST)
    cases = request.case_requests
    first = next(iter(cases.values())).config
    asset = first.asset
    site = first.site
    period = first.period
    form = default_live_form()
    form["market_da"] = "da" in cases
    form["market_mfrr"] = "mfrr" in cases
    form["market_afrr"] = "afrr" in cases
    form["period_preset"] = PRESET_2025
    form["start_date"] = period.start_date.isoformat()
    form["end_date"] = period.end_date_inclusive.isoformat()
    form["separate_machines"] = asset.power_pump_mw != asset.power_turbine_mw
    form["common_mw"] = max(float(asset.power_pump_mw), float(asset.power_turbine_mw))
    form["pump_mw"] = float(asset.power_pump_mw)
    form["turbine_mw"] = float(asset.power_turbine_mw)
    form["eta_pump"] = float(asset.eta_pump)
    form["eta_turbine"] = float(asset.eta_turbine)
    if asset.pond_energy_mwh is not None:
        form["storage_mode"] = STORAGE_POND
        form["pond_energy_mwh"] = float(asset.pond_energy_mwh)
    else:
        form["storage_mode"] = STORAGE_HOURS
        form["storage_hours"] = coalesce_float(asset.storage_hours, 4.0)
    form["soc_initial"] = float(asset.soc_initial_frac)
    form["soc_terminal"] = float(asset.soc_terminal_frac)
    form["enforce_terminal"] = bool(asset.enforce_terminal_soc)
    form["pump_ramp_up_min"] = float(asset.pump_ramp_up_min)
    form["pump_ramp_down_min"] = float(asset.pump_ramp_down_min)
    form["pump_ramp_power_frac"] = float(asset.pump_ramp_power_frac)
    form["turbine_ramp_up_min"] = float(asset.turbine_ramp_up_min)
    form["turbine_ramp_down_min"] = float(asset.turbine_ramp_down_min)
    form["turbine_ramp_power_frac"] = float(asset.turbine_ramp_power_frac)
    form["separate_grid"] = coalesce_float(site.grid_import_mw, 0.0) != coalesce_float(
        site.grid_export_mw, 0.0
    )
    form["grid_follow"] = False
    form["grid_common_mw"] = max(
        coalesce_float(site.grid_import_mw, 0.0),
        coalesce_float(site.grid_export_mw, 0.0),
    )
    form["grid_import_mw"] = coalesce_float(site.grid_import_mw, 0.0)
    form["grid_export_mw"] = coalesce_float(site.grid_export_mw, 0.0)
    form["pv_enabled"] = float(site.pv_ac_kw) > 0.0
    form["pv_ac_kw"] = float(site.pv_ac_kw) if float(site.pv_ac_kw) > 0.0 else 500.0
    form["pv_revenue_mode"] = site.pv_revenue_mode
    form["pv_fixed_price"] = site.pv_fixed_price_eur_mwh
    form["detailed_solver"] = False
    balancing = cases.get("afrr") or cases.get("mfrr")
    if balancing is not None:
        case = balancing.config.market_case
        form["activation"] = getattr(case, "activation_profile", "balanced")
        form["capacity_coverage_hours"] = float(getattr(case, "capacity_coverage_hours", 4.0))
        form["afrr_up_fraction"] = float(getattr(case, "up_capacity_fraction", 1.0))
        bid = getattr(case, "capacity_bid", None)
        quantile = getattr(bid, "quantile", None)
        if quantile is None:
            form["bid_kind"] = BID_FIXED
            form["mfrr_fixed_up"] = getattr(bid, "upward_price_eur_mw_h", 16.0)
            form["afrr_fixed_up"] = getattr(bid, "upward_price_eur_mw_h", 16.0)
            form["afrr_fixed_down"] = getattr(bid, "downward_price_eur_mw_h", 16.0)
        else:
            form["bid_kind"] = BID_HISTORICAL
            form["bid_quantile"] = float(quantile)
    return form
