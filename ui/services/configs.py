"""Build public configuration objects transiently from the Configure form."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from stepinbel.config import (
    AFRRCase,
    AssetConfig,
    BelgianDeliveryPeriod,
    ConfigError,
    DayAheadCase,
    FixedMinimumCapacityBid,
    HistoricalQuantileCapacityBid,
    MFRRCase,
    SimulationConfig,
    SiteConfig,
)

from ui.services.commitment import build_machine_commitment

from ui.services.form import (
    BID_FIXED,
    STORAGE_POND,
    coalesce_float,
    optional_float,
    pump_rating,
    resolve_form_pv_region,
    selected_markets,
    turbine_rating,
)
from ui.services.period import parse_iso_date


def form_period(form: Mapping[str, Any]) -> tuple[date, date]:
    start = parse_iso_date(form.get("start_date"))
    end = parse_iso_date(form.get("end_date"))
    if start is None or end is None:
        raise ValueError("Enter valid Belgian delivery dates.")
    if end < start:
        raise ValueError("Simulation period end must be on or after the start.")
    return start, end


def build_asset(form: Mapping[str, Any]) -> AssetConfig:
    if form.get("storage_mode") == STORAGE_POND:
        return AssetConfig(
            power_pump_mw=pump_rating(form),
            power_turbine_mw=turbine_rating(form),
            eta_pump=coalesce_float(form.get("eta_pump"), 0.0),
            eta_turbine=coalesce_float(form.get("eta_turbine"), 0.0),
            storage_hours=None,
            pond_energy_mwh=coalesce_float(form.get("pond_energy_mwh"), 0.0),
            soc_initial_frac=coalesce_float(form.get("soc_initial"), 0.0),
            soc_terminal_frac=coalesce_float(form.get("soc_terminal"), 0.0),
            enforce_terminal_soc=bool(form.get("enforce_terminal")),
            pump_ramp_up_min=coalesce_float(form.get("pump_ramp_up_min"), 0.0),
            pump_ramp_down_min=coalesce_float(form.get("pump_ramp_down_min"), 0.0),
            pump_ramp_power_frac=coalesce_float(form.get("pump_ramp_power_frac"), 0.0),
            turbine_ramp_up_min=coalesce_float(form.get("turbine_ramp_up_min"), 0.0),
            turbine_ramp_down_min=coalesce_float(form.get("turbine_ramp_down_min"), 0.0),
            turbine_ramp_power_frac=coalesce_float(form.get("turbine_ramp_power_frac"), 0.0),
        )
    return AssetConfig(
        power_pump_mw=pump_rating(form),
        power_turbine_mw=turbine_rating(form),
        eta_pump=coalesce_float(form.get("eta_pump"), 0.0),
        eta_turbine=coalesce_float(form.get("eta_turbine"), 0.0),
        storage_hours=coalesce_float(form.get("storage_hours"), 0.0),
        storage_hours_basis="discharge_at_rated",
        pond_energy_mwh=None,
        soc_initial_frac=coalesce_float(form.get("soc_initial"), 0.0),
        soc_terminal_frac=coalesce_float(form.get("soc_terminal"), 0.0),
        enforce_terminal_soc=bool(form.get("enforce_terminal")),
        pump_ramp_up_min=coalesce_float(form.get("pump_ramp_up_min"), 0.0),
        pump_ramp_down_min=coalesce_float(form.get("pump_ramp_down_min"), 0.0),
        pump_ramp_power_frac=coalesce_float(form.get("pump_ramp_power_frac"), 0.0),
        turbine_ramp_up_min=coalesce_float(form.get("turbine_ramp_up_min"), 0.0),
        turbine_ramp_down_min=coalesce_float(form.get("turbine_ramp_down_min"), 0.0),
        turbine_ramp_power_frac=coalesce_float(form.get("turbine_ramp_power_frac"), 0.0),
    )


def build_site(form: Mapping[str, Any]) -> SiteConfig:
    if form.get("separate_grid"):
        import_mw = coalesce_float(form.get("grid_import_mw"), 0.0)
        export_mw = coalesce_float(form.get("grid_export_mw"), 0.0)
    else:
        common = coalesce_float(form.get("grid_common_mw"), 0.0)
        import_mw = common
        export_mw = common
    pv_enabled = bool(form.get("pv_enabled"))
    mode = "fixed" if form.get("pv_revenue_mode") == "fixed" else "da"
    price = optional_float(form.get("pv_fixed_price"))
    wind_enabled = bool(form.get("wind_enabled"))
    wind_mode = "fixed" if form.get("wind_revenue_mode") == "fixed" else "da"
    wind_price = optional_float(form.get("wind_fixed_price"))
    wind_profile = form.get("wind_profile_id")
    if not isinstance(wind_profile, str) or not wind_profile.strip():
        wind_profile = "onshore_belgium"
    return SiteConfig(
        grid_import_mw=import_mw,
        grid_export_mw=export_mw,
        pv_ac_kw=coalesce_float(form.get("pv_ac_kw"), 0.0) if pv_enabled else 0.0,
        pv_region=resolve_form_pv_region(form.get("pv_region")),
        pv_revenue_mode=mode,
        pv_fixed_price_eur_mwh=None if mode != "fixed" else price,
        wind_capacity_kw=coalesce_float(form.get("wind_capacity_kw"), 0.0) if wind_enabled else 0.0,
        wind_profile_id=wind_profile,
        wind_revenue_mode=wind_mode,
        wind_fixed_price_eur_mwh=None if wind_mode != "fixed" else wind_price,
    )


def _capacity_bid(form: Mapping[str, Any], market: str):
    if form.get("bid_kind") != BID_FIXED:
        return HistoricalQuantileCapacityBid(coalesce_float(form.get("bid_quantile"), 0.50))
    if market == "mfrr":
        return FixedMinimumCapacityBid(
            upward_price_eur_mw_h=coalesce_float(form.get("mfrr_fixed_up"), 0.0),
            downward_price_eur_mw_h=None,
        )
    return FixedMinimumCapacityBid(
        upward_price_eur_mw_h=coalesce_float(form.get("afrr_fixed_up"), 0.0),
        downward_price_eur_mw_h=coalesce_float(form.get("afrr_fixed_down"), 0.0),
    )


def build_market_case(form: Mapping[str, Any], market: str):
    activation = "passive" if form.get("activation") == "passive" else "balanced"
    if market == "da":
        return DayAheadCase()
    if market == "mfrr":
        return MFRRCase(
            activation_profile=activation,
            capacity_bid=_capacity_bid(form, "mfrr"),
            capacity_coverage_hours=coalesce_float(form.get("capacity_coverage_hours"), 0.0),
        )
    return AFRRCase(
        activation_profile=activation,
        capacity_bid=_capacity_bid(form, "afrr"),
        capacity_coverage_hours=coalesce_float(form.get("capacity_coverage_hours"), 0.0),
        up_capacity_fraction=coalesce_float(form.get("afrr_up_fraction"), 0.0),
    )


def derived_reservoir_text(form: Mapping[str, Any]) -> str:
    try:
        return f"{build_asset(form).e_max_mwh():.3f} MWh"
    except (ConfigError, TypeError, ValueError):
        return "—"


def build_simulation_configs(form: Mapping[str, Any]) -> list[tuple[str, SimulationConfig]]:
    markets = selected_markets(form)
    if not markets:
        raise ValueError("Select at least one market.")
    start, end = form_period(form)
    period = BelgianDeliveryPeriod(start, end)
    asset = build_asset(form)
    site = build_site(form)
    commitment = build_machine_commitment(form)
    configs: list[tuple[str, SimulationConfig]] = []
    for market in markets:
        configs.append(
            (
                market,
                SimulationConfig(
                    period=period,
                    market_case=build_market_case(form, market),
                    asset=asset,
                    site=site,
                    machine_commitment=commitment,
                ),
            )
        )
    return configs
