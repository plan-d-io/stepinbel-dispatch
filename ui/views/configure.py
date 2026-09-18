"""Functional Configure page. Calls UI services; does not import StepInBel core."""

from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from ui.flow import (
    SESSION_KEY,
    apply_demo_change,
    consume_configure_restore,
    invalidate_if_form_changed,
    is_demo,
    require_json_compatible,
    set_form,
    set_validation_error,
    store_snapshot,
    stored_form,
)
from ui.presentation.components import (
    render_action_row,
    render_page_header,
    render_readouts,
    render_section_heading,
    render_status_panel,
)
from ui.presentation.tokens import (
    CONFIGURE_SUBTITLE,
    CONFIGURE_TITLE,
    DEMO_MODE_HELP,
    GRID_HELP_AUTOMATIC,
    GRID_RESET_LABEL,
    MARKETS_INDEPENDENT_COPY,
    PERIOD_CUSTOM_HELP,
    PERIOD_END_LABEL,
    PERIOD_OPTIONS,
    PERIOD_SELECTOR_LABEL,
    PERIOD_START_LABEL,
    PV_HELP_OFF,
    PV_HELP_ON,
    SOLVER_LOG_COPY,
    WIND_HELP_OFF,
    WIND_HELP_ON,
)
from ui.services.commitment import (
    COMMITMENT_EXPANDER,
    COMMITMENT_TIME_NOTICE,
    DEFAULT_MIP_GAP_PCT,
    FIXED_SPEED_HELP,
    FIXED_SPEED_LABEL,
    FORBID_SIMULTANEOUS_LABEL,
    MIP_GAP_LABEL,
    MIP_TIME_LABEL,
    SOLVER_CONTROLS_HEADING,
    SOLVER_CONTROLS_HELP,
    TURBINE_MINIMUM_INPUT_LABEL,
    TURBINE_MINIMUM_LABEL,
    machine_commitment_enabled,
)
from ui.services.configs import derived_reservoir_text
from ui.services.demo import demo_form
from ui.services.form import (
    ACTIVATION_LABELS,
    BID_FIXED,
    BID_HISTORICAL,
    LABEL_TO_PRESET,
    PERIOD_PRESET_LABELS,
    PRESET_2026,
    PRESET_CUSTOM,
    PV_MODE_DA,
    PV_MODE_FIXED,
    PV_REGION_LABELS,
    PV_REGION_ORDER,
    PV_VALUATION_LABELS,
    STORAGE_HOURS,
    STORAGE_POND,
    WIND_MODE_DA,
    WIND_MODE_FIXED,
    WIND_PROFILE_LABELS,
    WIND_PROFILE_ONSHORE_BELGIUM,
    WIND_PROFILE_ORDER,
    WIND_VALUATION_LABELS,
    apply_form_transitions,
    coalesce_float,
    default_live_form,
    grid_override_help,
    pv_region_label,
    pv_region_value,
    selected_markets,
    suggested_grid_mw,
)
from ui.services.period import apply_period_preset, latest_inclusive_end
from ui.services.snapshot import build_snapshot, lightweight_continue_reason

WIDGET_PREFIX = "sib-cfg-"
DEMO_KEY = "sib-cfg-demo"

_KEYS = {
    "market_da": f"{WIDGET_PREFIX}m-da",
    "market_mfrr": f"{WIDGET_PREFIX}m-mfrr",
    "market_afrr": f"{WIDGET_PREFIX}m-afrr",
    "activation": f"{WIDGET_PREFIX}activation",
    "period_preset": f"{WIDGET_PREFIX}period",
    "start_date": f"{WIDGET_PREFIX}start",
    "end_date": f"{WIDGET_PREFIX}end",
    "separate_machines": f"{WIDGET_PREFIX}separate-machines",
    "common_mw": f"{WIDGET_PREFIX}common-mw",
    "pump_mw": f"{WIDGET_PREFIX}pump-mw",
    "turbine_mw": f"{WIDGET_PREFIX}turb-mw",
    "eta_pump": f"{WIDGET_PREFIX}eta-p",
    "eta_turbine": f"{WIDGET_PREFIX}eta-t",
    "storage_hours": f"{WIDGET_PREFIX}storage",
    "use_pond": f"{WIDGET_PREFIX}use-pond",
    "pond_energy_mwh": f"{WIDGET_PREFIX}pond",
    "soc_initial": f"{WIDGET_PREFIX}soc-i",
    "soc_terminal": f"{WIDGET_PREFIX}soc-t",
    "enforce_terminal": f"{WIDGET_PREFIX}enforce-term",
    "pump_ramp_up_min": f"{WIDGET_PREFIX}p-ru",
    "pump_ramp_down_min": f"{WIDGET_PREFIX}p-rd",
    "pump_ramp_power_frac": f"{WIDGET_PREFIX}p-rf",
    "turbine_ramp_up_min": f"{WIDGET_PREFIX}t-ru",
    "turbine_ramp_down_min": f"{WIDGET_PREFIX}t-rd",
    "turbine_ramp_power_frac": f"{WIDGET_PREFIX}t-rf",
    "separate_grid": f"{WIDGET_PREFIX}separate-grid",
    "grid_common_mw": f"{WIDGET_PREFIX}grid",
    "grid_import_mw": f"{WIDGET_PREFIX}grid-in",
    "grid_export_mw": f"{WIDGET_PREFIX}grid-out",
    "pv_enabled": f"{WIDGET_PREFIX}pv",
    "pv_ac_kw": f"{WIDGET_PREFIX}pv-kw",
    "pv_region": f"{WIDGET_PREFIX}pv-region",
    "pv_revenue_mode": f"{WIDGET_PREFIX}pv-val",
    "pv_fixed_price": f"{WIDGET_PREFIX}pv-price",
    "wind_enabled": f"{WIDGET_PREFIX}wind",
    "wind_capacity_kw": f"{WIDGET_PREFIX}wind-kw",
    "wind_profile_id": f"{WIDGET_PREFIX}wind-profile",
    "wind_revenue_mode": f"{WIDGET_PREFIX}wind-val",
    "wind_fixed_price": f"{WIDGET_PREFIX}wind-price",
    "bid_kind": f"{WIDGET_PREFIX}bid-kind",
    "bid_quantile": f"{WIDGET_PREFIX}quantile",
    "capacity_coverage_hours": f"{WIDGET_PREFIX}cover",
    "mfrr_fixed_up": f"{WIDGET_PREFIX}mfrr-up",
    "afrr_fixed_up": f"{WIDGET_PREFIX}afrr-up-p",
    "afrr_fixed_down": f"{WIDGET_PREFIX}afrr-down-p",
    "afrr_up_fraction": f"{WIDGET_PREFIX}afrr-up",
    "detailed_solver": f"{WIDGET_PREFIX}solver",
    "fixed_speed_pump": f"{WIDGET_PREFIX}fixed-pump",
    "turbine_minimum_enabled": f"{WIDGET_PREFIX}turb-min",
    "turbine_minimum_output_pct": f"{WIDGET_PREFIX}turb-min-pct",
    "forbid_simultaneous_operation": f"{WIDGET_PREFIX}no-simul",
    "mip_gap_pct": f"{WIDGET_PREFIX}mip-gap",
    "mip_time_limit_min": f"{WIDGET_PREFIX}mip-time",
}


def wipe_configure_widgets() -> None:
    for key in list(st.session_state.keys()):
        if str(key).startswith(WIDGET_PREFIX) and key != DEMO_KEY:
            del st.session_state[key]


def _activation_label(value: object) -> str:
    return ACTIVATION_LABELS.get(str(value), "Balanced")


def _activation_value(label: str) -> str:
    for key, text in ACTIVATION_LABELS.items():
        if text == label:
            return key
    return "balanced"


def _preset_label(value: object) -> str:
    return PERIOD_PRESET_LABELS.get(str(value), PERIOD_PRESET_LABELS["2025"])


def _bid_label(value: object) -> str:
    return "Fixed minimum prices" if value == BID_FIXED else "Historical quantile"


def _pv_label(value: object) -> str:
    return PV_VALUATION_LABELS.get(str(value), PV_VALUATION_LABELS[PV_MODE_DA])


def _pv_region_label(value: object) -> str:
    return pv_region_label(value)


def _pv_region_value(label: str) -> str:
    return pv_region_value(label)


def _wind_profile_label(value: object) -> str:
    return WIND_PROFILE_LABELS.get(str(value), WIND_PROFILE_LABELS[WIND_PROFILE_ONSHORE_BELGIUM])


def _wind_profile_value(label: str) -> str:
    for key, text in WIND_PROFILE_LABELS.items():
        if text == label:
            return key
    return WIND_PROFILE_ONSHORE_BELGIUM


def _wind_label(value: object) -> str:
    return WIND_VALUATION_LABELS.get(str(value), WIND_VALUATION_LABELS[WIND_MODE_DA])


def _widget_values(form: Mapping[str, Any]) -> dict[str, Any]:
    return {
        _KEYS["market_da"]: bool(form.get("market_da")),
        _KEYS["market_mfrr"]: bool(form.get("market_mfrr")),
        _KEYS["market_afrr"]: bool(form.get("market_afrr")),
        _KEYS["activation"]: _activation_label(form.get("activation")),
        _KEYS["period_preset"]: _preset_label(form.get("period_preset")),
        _KEYS["start_date"]: str(form.get("start_date") or "2025-01-01"),
        _KEYS["end_date"]: str(form.get("end_date") or "2025-12-31"),
        _KEYS["separate_machines"]: bool(form.get("separate_machines")),
        _KEYS["common_mw"]: coalesce_float(form.get("common_mw"), 1.0),
        _KEYS["pump_mw"]: coalesce_float(form.get("pump_mw"), 1.0),
        _KEYS["turbine_mw"]: coalesce_float(form.get("turbine_mw"), 1.0),
        _KEYS["eta_pump"]: coalesce_float(form.get("eta_pump"), 0.84),
        _KEYS["eta_turbine"]: coalesce_float(form.get("eta_turbine"), 0.90),
        _KEYS["storage_hours"]: coalesce_float(form.get("storage_hours"), 4.0),
        _KEYS["use_pond"]: form.get("storage_mode") == STORAGE_POND,
        _KEYS["pond_energy_mwh"]: coalesce_float(form.get("pond_energy_mwh"), 4.444),
        _KEYS["soc_initial"]: coalesce_float(form.get("soc_initial"), 0.5),
        _KEYS["soc_terminal"]: coalesce_float(form.get("soc_terminal"), 0.5),
        _KEYS["enforce_terminal"]: bool(form.get("enforce_terminal")),
        _KEYS["pump_ramp_up_min"]: coalesce_float(form.get("pump_ramp_up_min"), 10.0),
        _KEYS["pump_ramp_down_min"]: coalesce_float(form.get("pump_ramp_down_min"), 1.0),
        _KEYS["pump_ramp_power_frac"]: coalesce_float(form.get("pump_ramp_power_frac"), 0.10),
        _KEYS["turbine_ramp_up_min"]: coalesce_float(form.get("turbine_ramp_up_min"), 2.0),
        _KEYS["turbine_ramp_down_min"]: coalesce_float(form.get("turbine_ramp_down_min"), 2.0),
        _KEYS["turbine_ramp_power_frac"]: coalesce_float(form.get("turbine_ramp_power_frac"), 0.0),
        _KEYS["separate_grid"]: bool(form.get("separate_grid")),
        _KEYS["grid_common_mw"]: coalesce_float(form.get("grid_common_mw"), 1.0),
        _KEYS["grid_import_mw"]: coalesce_float(form.get("grid_import_mw"), 1.0),
        _KEYS["grid_export_mw"]: coalesce_float(form.get("grid_export_mw"), 1.0),
        _KEYS["pv_enabled"]: bool(form.get("pv_enabled")),
        _KEYS["pv_ac_kw"]: coalesce_float(form.get("pv_ac_kw"), 500.0),
        _KEYS["pv_region"]: _pv_region_label(form.get("pv_region")),
        _KEYS["pv_revenue_mode"]: _pv_label(form.get("pv_revenue_mode")),
        _KEYS["pv_fixed_price"]: coalesce_float(form.get("pv_fixed_price"), 0.0),
        _KEYS["wind_enabled"]: bool(form.get("wind_enabled")),
        _KEYS["wind_capacity_kw"]: coalesce_float(form.get("wind_capacity_kw"), 1000.0),
        _KEYS["wind_profile_id"]: _wind_profile_label(form.get("wind_profile_id")),
        _KEYS["wind_revenue_mode"]: _wind_label(form.get("wind_revenue_mode")),
        _KEYS["wind_fixed_price"]: coalesce_float(form.get("wind_fixed_price"), 0.0),
        _KEYS["bid_kind"]: _bid_label(form.get("bid_kind")),
        _KEYS["bid_quantile"]: coalesce_float(form.get("bid_quantile"), 0.50),
        _KEYS["capacity_coverage_hours"]: coalesce_float(form.get("capacity_coverage_hours"), 4.0),
        _KEYS["mfrr_fixed_up"]: coalesce_float(form.get("mfrr_fixed_up"), 16.0),
        _KEYS["afrr_fixed_up"]: coalesce_float(form.get("afrr_fixed_up"), 16.0),
        _KEYS["afrr_fixed_down"]: coalesce_float(form.get("afrr_fixed_down"), 16.0),
        _KEYS["afrr_up_fraction"]: coalesce_float(form.get("afrr_up_fraction"), 1.0),
        _KEYS["detailed_solver"]: bool(form.get("detailed_solver")),
        _KEYS["fixed_speed_pump"]: bool(form.get("fixed_speed_pump")),
        _KEYS["turbine_minimum_enabled"]: bool(form.get("turbine_minimum_enabled")),
        _KEYS["turbine_minimum_output_pct"]: coalesce_float(form.get("turbine_minimum_output_pct"), 18.0),
        _KEYS["forbid_simultaneous_operation"]: bool(form.get("forbid_simultaneous_operation")),
        _KEYS["mip_gap_pct"]: coalesce_float(form.get("mip_gap_pct"), DEFAULT_MIP_GAP_PCT),
        _KEYS["mip_time_limit_min"]: coalesce_float(form.get("mip_time_limit_min"), 15.0),
    }


def _seed_widgets(form: Mapping[str, Any], *, overwrite: bool = False) -> None:
    for key, value in _widget_values(form).items():
        if overwrite or key not in st.session_state:
            st.session_state[key] = value
            continue
        current = st.session_state.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not isinstance(current, (int, float)) or isinstance(current, bool):
                st.session_state[key] = value
    if DEMO_KEY not in st.session_state:
        st.session_state[DEMO_KEY] = False


def _apply_pending_widgets(state: dict[str, Any]) -> None:
    pending = state.pop("pending_widgets", None)
    if not isinstance(pending, Mapping):
        return
    for key, value in pending.items():
        st.session_state[str(key)] = value
    _persist(state)


def _collect(previous: Mapping[str, Any]) -> dict[str, Any]:
    form = dict(previous)
    form["market_da"] = bool(st.session_state.get(_KEYS["market_da"]))
    form["market_mfrr"] = bool(st.session_state.get(_KEYS["market_mfrr"]))
    form["market_afrr"] = bool(st.session_state.get(_KEYS["market_afrr"]))
    form["activation"] = _activation_value(str(st.session_state.get(_KEYS["activation"]) or "Balanced"))
    form["period_preset"] = LABEL_TO_PRESET.get(
        str(st.session_state.get(_KEYS["period_preset"])),
        "2025",
    )
    form["start_date"] = str(st.session_state.get(_KEYS["start_date"]) or form.get("start_date"))
    form["end_date"] = str(st.session_state.get(_KEYS["end_date"]) or form.get("end_date"))
    form["separate_machines"] = bool(st.session_state.get(_KEYS["separate_machines"]))
    form["common_mw"] = coalesce_float(st.session_state.get(_KEYS["common_mw"]), 0.0)
    form["pump_mw"] = coalesce_float(st.session_state.get(_KEYS["pump_mw"]), 0.0)
    form["turbine_mw"] = coalesce_float(st.session_state.get(_KEYS["turbine_mw"]), 0.0)
    form["eta_pump"] = coalesce_float(st.session_state.get(_KEYS["eta_pump"]), 0.0)
    form["eta_turbine"] = coalesce_float(st.session_state.get(_KEYS["eta_turbine"]), 0.0)
    form["storage_mode"] = STORAGE_POND if st.session_state.get(_KEYS["use_pond"]) else STORAGE_HOURS
    form["storage_hours"] = coalesce_float(st.session_state.get(_KEYS["storage_hours"]), 0.0)
    form["pond_energy_mwh"] = coalesce_float(st.session_state.get(_KEYS["pond_energy_mwh"]), 0.0)
    form["soc_initial"] = coalesce_float(st.session_state.get(_KEYS["soc_initial"]), 0.0)
    form["soc_terminal"] = coalesce_float(st.session_state.get(_KEYS["soc_terminal"]), 0.0)
    form["enforce_terminal"] = bool(st.session_state.get(_KEYS["enforce_terminal"]))
    form["pump_ramp_up_min"] = coalesce_float(st.session_state.get(_KEYS["pump_ramp_up_min"]), 0.0)
    form["pump_ramp_down_min"] = coalesce_float(st.session_state.get(_KEYS["pump_ramp_down_min"]), 0.0)
    form["pump_ramp_power_frac"] = coalesce_float(st.session_state.get(_KEYS["pump_ramp_power_frac"]), 0.0)
    form["turbine_ramp_up_min"] = coalesce_float(st.session_state.get(_KEYS["turbine_ramp_up_min"]), 0.0)
    form["turbine_ramp_down_min"] = coalesce_float(st.session_state.get(_KEYS["turbine_ramp_down_min"]), 0.0)
    form["turbine_ramp_power_frac"] = coalesce_float(st.session_state.get(_KEYS["turbine_ramp_power_frac"]), 0.0)
    form["separate_grid"] = bool(st.session_state.get(_KEYS["separate_grid"]))
    form["grid_common_mw"] = coalesce_float(st.session_state.get(_KEYS["grid_common_mw"]), 0.0)
    form["grid_import_mw"] = coalesce_float(st.session_state.get(_KEYS["grid_import_mw"]), 0.0)
    form["grid_export_mw"] = coalesce_float(st.session_state.get(_KEYS["grid_export_mw"]), 0.0)
    form["grid_follow"] = bool(previous.get("grid_follow", True))
    form["pv_enabled"] = bool(st.session_state.get(_KEYS["pv_enabled"]))
    form["pv_ac_kw"] = coalesce_float(st.session_state.get(_KEYS["pv_ac_kw"]), 0.0)
    form["pv_region"] = _pv_region_value(
        str(st.session_state.get(_KEYS["pv_region"]) or _pv_region_label(previous.get("pv_region")))
    )
    form["pv_revenue_mode"] = (
        PV_MODE_FIXED
        if st.session_state.get(_KEYS["pv_revenue_mode"]) == PV_VALUATION_LABELS[PV_MODE_FIXED]
        else PV_MODE_DA
    )
    if form["pv_revenue_mode"] == PV_MODE_FIXED:
        form["pv_fixed_price"] = coalesce_float(st.session_state.get(_KEYS["pv_fixed_price"]), 0.0)
    form["wind_enabled"] = bool(st.session_state.get(_KEYS["wind_enabled"]))
    form["wind_capacity_kw"] = coalesce_float(
        st.session_state.get(_KEYS["wind_capacity_kw"], previous.get("wind_capacity_kw")),
        1000.0,
    )
    form["wind_profile_id"] = _wind_profile_value(
        str(st.session_state.get(_KEYS["wind_profile_id"]) or _wind_profile_label(previous.get("wind_profile_id")))
    )
    form["wind_revenue_mode"] = (
        WIND_MODE_FIXED
        if st.session_state.get(_KEYS["wind_revenue_mode"]) == WIND_VALUATION_LABELS[WIND_MODE_FIXED]
        else WIND_MODE_DA
    )
    if form["wind_revenue_mode"] == WIND_MODE_FIXED:
        form["wind_fixed_price"] = coalesce_float(st.session_state.get(_KEYS["wind_fixed_price"]), 0.0)
    form["bid_kind"] = (
        BID_FIXED if st.session_state.get(_KEYS["bid_kind"]) == "Fixed minimum prices" else BID_HISTORICAL
    )
    form["bid_quantile"] = coalesce_float(st.session_state.get(_KEYS["bid_quantile"]), 0.50)
    form["capacity_coverage_hours"] = coalesce_float(st.session_state.get(_KEYS["capacity_coverage_hours"]), 0.0)
    form["mfrr_fixed_up"] = coalesce_float(st.session_state.get(_KEYS["mfrr_fixed_up"]), 0.0)
    form["afrr_fixed_up"] = coalesce_float(st.session_state.get(_KEYS["afrr_fixed_up"]), 0.0)
    form["afrr_fixed_down"] = coalesce_float(st.session_state.get(_KEYS["afrr_fixed_down"]), 0.0)
    form["afrr_up_fraction"] = coalesce_float(st.session_state.get(_KEYS["afrr_up_fraction"]), 0.0)
    form["detailed_solver"] = bool(st.session_state.get(_KEYS["detailed_solver"]))
    form["fixed_speed_pump"] = bool(st.session_state.get(_KEYS["fixed_speed_pump"]))
    form["turbine_minimum_enabled"] = bool(st.session_state.get(_KEYS["turbine_minimum_enabled"]))
    form["turbine_minimum_output_pct"] = coalesce_float(
        st.session_state.get(_KEYS["turbine_minimum_output_pct"], previous.get("turbine_minimum_output_pct")),
        18.0,
    )
    form["forbid_simultaneous_operation"] = bool(st.session_state.get(_KEYS["forbid_simultaneous_operation"]))
    form["mip_gap_pct"] = coalesce_float(
        st.session_state.get(_KEYS["mip_gap_pct"], previous.get("mip_gap_pct")),
        DEFAULT_MIP_GAP_PCT,
    )
    form["mip_time_limit_min"] = coalesce_float(
        st.session_state.get(_KEYS["mip_time_limit_min"], previous.get("mip_time_limit_min")),
        15.0,
    )
    return form


def _ensure_session_number(key: str, fallback: object, default: float) -> None:
    current = st.session_state.get(key)
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        return
    st.session_state[key] = coalesce_float(fallback, default)


def _persist(state: dict[str, Any]) -> None:
    st.session_state[SESSION_KEY] = require_json_compatible(dict(state))


def _ensure_form(state: dict[str, Any]) -> dict[str, Any]:
    form = stored_form(state)
    if form is None:
        form = demo_form() if is_demo(state) else default_live_form()
        set_form(state, form)
        _persist(state)
    return form


def render_configure(state: dict[str, Any]) -> None:
    _apply_pending_widgets(state)
    previous = _ensure_form(state)
    demo = is_demo(state)
    if DEMO_KEY not in st.session_state:
        st.session_state[DEMO_KEY] = demo

    render_page_header("Stage 1 of 3", CONFIGURE_TITLE, CONFIGURE_SUBTITLE)
    demo_now = bool(
        st.checkbox("Demo mode", key=DEMO_KEY, help=DEMO_MODE_HELP)
    )
    st.caption(DEMO_MODE_HELP)
    if demo_now != demo:
        apply_demo_change(state, demo_now)
        wipe_configure_widgets()
        _persist(state)
        st.rerun()

    form = demo_form() if demo else previous
    restore = consume_configure_restore(state)
    _seed_widgets(form, overwrite=restore)
    if restore:
        _persist(state)
    disabled = demo

    render_section_heading("Markets and period", MARKETS_INDEPENDENT_COPY)
    cols = st.columns(3)
    with cols[0]:
        st.checkbox("Day-ahead", disabled=disabled, key=_KEYS["market_da"])
    with cols[1]:
        st.checkbox("mFRR", disabled=disabled, key=_KEYS["market_mfrr"])
    with cols[2]:
        st.checkbox("aFRR", disabled=disabled, key=_KEYS["market_afrr"])

    live_markets = selected_markets(
        {
            "market_da": st.session_state.get(_KEYS["market_da"]),
            "market_mfrr": st.session_state.get(_KEYS["market_mfrr"]),
            "market_afrr": st.session_state.get(_KEYS["market_afrr"]),
        }
    )
    show_balancing = bool(
        st.session_state.get(_KEYS["market_mfrr"]) or st.session_state.get(_KEYS["market_afrr"])
    )
    left, right = st.columns(2)
    with left:
        if show_balancing:
            st.selectbox(
                "Balancing activation",
                ("Balanced", "Passive"),
                disabled=disabled,
                key=_KEYS["activation"],
            )
            st.caption("One strategy for all selected balancing markets.")
    with right:
        st.selectbox(
            PERIOD_SELECTOR_LABEL,
            PERIOD_OPTIONS,
            disabled=disabled,
            key=_KEYS["period_preset"],
        )
        dates_locked = disabled or st.session_state.get(_KEYS["period_preset"]) != PERIOD_PRESET_LABELS[PRESET_CUSTOM]
        st.text_input(PERIOD_START_LABEL, disabled=dates_locked, key=_KEYS["start_date"])
        st.text_input(PERIOD_END_LABEL, disabled=dates_locked, key=_KEYS["end_date"])
        if st.session_state.get(_KEYS["period_preset"]) == PERIOD_PRESET_LABELS[PRESET_CUSTOM]:
            st.caption(PERIOD_CUSTOM_HELP)
        elif st.session_state.get(_KEYS["period_preset"]) == PERIOD_PRESET_LABELS[PRESET_2026]:
            try:
                end = latest_inclusive_end(
                    live_markets,
                    pv_enabled=bool(st.session_state.get(_KEYS["pv_enabled"])),
                    wind_enabled=bool(st.session_state.get(_KEYS["wind_enabled"])),
                    year=2026,
                )
                st.caption(
                    "Latest completely covered Belgian delivery date for the selected "
                    f"markets, PV, and wind: {end.isoformat()}."
                )
            except Exception as exc:
                st.caption(str(exc))

    render_section_heading("Pumped-hydro asset")
    st.checkbox(
        "Configure pump and turbine separately",
        disabled=disabled,
        key=_KEYS["separate_machines"],
    )
    if st.session_state.get(_KEYS["separate_machines"]):
        c1, c2 = st.columns(2)
        with c1:
            st.number_input("Pump rating (MW)", min_value=0.0, disabled=disabled, key=_KEYS["pump_mw"])
            st.number_input("Pump efficiency", min_value=0.0, max_value=1.0, disabled=disabled, key=_KEYS["eta_pump"])
        with c2:
            st.number_input("Turbine rating (MW)", min_value=0.0, disabled=disabled, key=_KEYS["turbine_mw"])
            st.number_input("Turbine efficiency", min_value=0.0, max_value=1.0, disabled=disabled, key=_KEYS["eta_turbine"])
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.number_input("Common rating (MW)", min_value=0.0, disabled=disabled, key=_KEYS["common_mw"])
        with c2:
            st.number_input("Pump efficiency", min_value=0.0, max_value=1.0, disabled=disabled, key=_KEYS["eta_pump"])
        with c3:
            st.number_input("Turbine efficiency", min_value=0.0, max_value=1.0, disabled=disabled, key=_KEYS["eta_turbine"])
    if not st.session_state.get(_KEYS["use_pond"]):
        st.number_input(
            "Storage (h discharge at rated turbine)",
            min_value=0.0,
            disabled=disabled,
            key=_KEYS["storage_hours"],
        )
    else:
        st.number_input(
            "Pond energy (MWh)",
            min_value=0.0,
            disabled=disabled,
            key=_KEYS["pond_energy_mwh"],
        )
    preview_form = apply_form_transitions(previous, _collect(previous))
    render_readouts((("Derived reservoir capacity", derived_reservoir_text(preview_form)),))
    with st.expander("Advanced asset assumptions", expanded=False):
        st.checkbox("Use explicit pond energy", disabled=disabled, key=_KEYS["use_pond"])
        st.number_input("Initial state of charge", min_value=0.0, max_value=1.0, disabled=disabled, key=_KEYS["soc_initial"])
        st.number_input("Terminal state of charge", min_value=0.0, max_value=1.0, disabled=disabled, key=_KEYS["soc_terminal"])
        st.checkbox("Enforce terminal state of charge", disabled=disabled, key=_KEYS["enforce_terminal"])
        r1, r2 = st.columns(2)
        with r1:
            st.number_input("Pump ramp-up (min)", min_value=0.0, disabled=disabled, key=_KEYS["pump_ramp_up_min"])
            st.number_input("Pump ramp-down (min)", min_value=0.0, disabled=disabled, key=_KEYS["pump_ramp_down_min"])
            st.number_input("Pump ramp power fraction", min_value=0.0, max_value=1.0, disabled=disabled, key=_KEYS["pump_ramp_power_frac"])
        with r2:
            st.number_input("Turbine ramp-up (min)", min_value=0.0, disabled=disabled, key=_KEYS["turbine_ramp_up_min"])
            st.number_input("Turbine ramp-down (min)", min_value=0.0, disabled=disabled, key=_KEYS["turbine_ramp_down_min"])
            st.number_input("Turbine ramp power fraction", min_value=0.0, max_value=1.0, disabled=disabled, key=_KEYS["turbine_ramp_power_frac"])
        st.divider()
        st.markdown(f"**{COMMITMENT_EXPANDER}**")
        st.checkbox(
            FIXED_SPEED_LABEL,
            disabled=disabled,
            key=_KEYS["fixed_speed_pump"],
            help=FIXED_SPEED_HELP,
        )
        st.checkbox(
            TURBINE_MINIMUM_LABEL,
            disabled=disabled,
            key=_KEYS["turbine_minimum_enabled"],
        )
        if st.session_state.get(_KEYS["turbine_minimum_enabled"]):
            if form.get("turbine_minimum_enabled") is not True:
                st.session_state[_KEYS["turbine_minimum_output_pct"]] = coalesce_float(
                    form.get("turbine_minimum_output_pct"), 18.0
                )
            else:
                _ensure_session_number(
                    _KEYS["turbine_minimum_output_pct"],
                    form.get("turbine_minimum_output_pct"),
                    18.0,
                )
            st.number_input(
                TURBINE_MINIMUM_INPUT_LABEL,
                min_value=0.0,
                max_value=100.0,
                disabled=disabled,
                key=_KEYS["turbine_minimum_output_pct"],
            )
        st.checkbox(
            FORBID_SIMULTANEOUS_LABEL,
            disabled=disabled,
            key=_KEYS["forbid_simultaneous_operation"],
        )
        live_commitment = {
            "fixed_speed_pump": st.session_state.get(_KEYS["fixed_speed_pump"]),
            "turbine_minimum_enabled": st.session_state.get(_KEYS["turbine_minimum_enabled"]),
            "forbid_simultaneous_operation": st.session_state.get(_KEYS["forbid_simultaneous_operation"]),
        }
        if machine_commitment_enabled(live_commitment):
            st.caption(COMMITMENT_TIME_NOTICE)

    render_section_heading("Grid connection")
    st.checkbox(
        "Configure import and export separately",
        disabled=disabled,
        key=_KEYS["separate_grid"],
    )
    suggested = suggested_grid_mw(preview_form)
    if st.session_state.get(_KEYS["separate_grid"]):
        g1, g2 = st.columns(2)
        with g1:
            st.number_input(
                "Grid import (MW)",
                min_value=0.0,
                disabled=disabled,
                key=_KEYS["grid_import_mw"],
                help=GRID_HELP_AUTOMATIC if preview_form.get("grid_follow") else grid_override_help(suggested),
            )
        with g2:
            st.number_input(
                "Grid export (MW)",
                min_value=0.0,
                disabled=disabled,
                key=_KEYS["grid_export_mw"],
                help=GRID_HELP_AUTOMATIC if preview_form.get("grid_follow") else grid_override_help(suggested),
            )
    else:
        st.number_input(
            "Connection limit (MW)",
            min_value=0.0,
            disabled=disabled,
            key=_KEYS["grid_common_mw"],
            help=GRID_HELP_AUTOMATIC if preview_form.get("grid_follow") else grid_override_help(suggested),
        )
    reset_grid = bool(
        st.button(GRID_RESET_LABEL, type="secondary", disabled=disabled, key=f"{WIDGET_PREFIX}grid-reset")
    )

    render_section_heading("PV")
    st.checkbox("Include co-located PV", disabled=disabled, key=_KEYS["pv_enabled"])
    if st.session_state.get(_KEYS["pv_enabled"]):
        if form.get("pv_enabled") is not True:
            previous_capacity = coalesce_float(form.get("pv_ac_kw"), 0.0)
            st.session_state[_KEYS["pv_ac_kw"]] = (
                previous_capacity if previous_capacity > 0.0 else 500.0
            )
            st.session_state[_KEYS["pv_region"]] = _pv_region_label(form.get("pv_region"))
            st.session_state[_KEYS["pv_revenue_mode"]] = _pv_label(form.get("pv_revenue_mode"))
        else:
            _ensure_session_number(_KEYS["pv_ac_kw"], form.get("pv_ac_kw"), 500.0)
        p1, p2 = st.columns(2)
        with p1:
            st.number_input("Installed PV (kW)", min_value=0.0, disabled=disabled, key=_KEYS["pv_ac_kw"])
        with p2:
            st.selectbox(
                "PV region",
                tuple(PV_REGION_LABELS[item] for item in PV_REGION_ORDER),
                disabled=disabled,
                key=_KEYS["pv_region"],
            )
        st.selectbox(
            "Export valuation",
            tuple(PV_VALUATION_LABELS.values()),
            disabled=disabled,
            key=_KEYS["pv_revenue_mode"],
        )
        if st.session_state.get(_KEYS["pv_revenue_mode"]) == PV_VALUATION_LABELS[PV_MODE_FIXED]:
            st.number_input(
                "Fixed PV export price (EUR/MWh)",
                disabled=disabled,
                key=_KEYS["pv_fixed_price"],
            )
        st.caption(PV_HELP_ON)
    else:
        st.caption(PV_HELP_OFF)

    render_section_heading("Wind")
    st.checkbox("Include co-located wind", disabled=disabled, key=_KEYS["wind_enabled"])
    if st.session_state.get(_KEYS["wind_enabled"]):
        if form.get("wind_enabled") is not True:
            previous_capacity = coalesce_float(form.get("wind_capacity_kw"), 0.0)
            st.session_state[_KEYS["wind_capacity_kw"]] = (
                previous_capacity if previous_capacity > 0.0 else 1000.0
            )
            st.session_state[_KEYS["wind_profile_id"]] = _wind_profile_label(form.get("wind_profile_id"))
            st.session_state[_KEYS["wind_revenue_mode"]] = _wind_label(form.get("wind_revenue_mode"))
        else:
            _ensure_session_number(_KEYS["wind_capacity_kw"], form.get("wind_capacity_kw"), 1000.0)
        w1, w2 = st.columns(2)
        with w1:
            st.number_input("Wind capacity (kW)", min_value=0.0, disabled=disabled, key=_KEYS["wind_capacity_kw"])
        with w2:
            st.selectbox(
                "Wind profile",
                tuple(WIND_PROFILE_LABELS[item] for item in WIND_PROFILE_ORDER),
                disabled=disabled,
                key=_KEYS["wind_profile_id"],
            )
        st.selectbox(
            "Export valuation",
            tuple(WIND_VALUATION_LABELS.values()),
            disabled=disabled,
            key=_KEYS["wind_revenue_mode"],
        )
        if st.session_state.get(_KEYS["wind_revenue_mode"]) == WIND_VALUATION_LABELS[WIND_MODE_FIXED]:
            st.number_input(
                "Fixed wind export price (EUR/MWh)",
                disabled=disabled,
                key=_KEYS["wind_fixed_price"],
            )
        st.caption(WIND_HELP_ON)
    else:
        st.caption(WIND_HELP_OFF)

    render_section_heading("Advanced")
    if show_balancing:
        with st.expander("Advanced balancing assumptions", expanded=False):
            st.selectbox(
                "Capacity bidding",
                ("Historical quantile", "Fixed minimum prices"),
                disabled=disabled,
                key=_KEYS["bid_kind"],
            )
            if st.session_state.get(_KEYS["bid_kind"]) == "Historical quantile":
                st.number_input(
                    "Historical capacity quantile",
                    min_value=0.0,
                    max_value=1.0,
                    disabled=disabled,
                    key=_KEYS["bid_quantile"],
                )
            st.number_input(
                "Capacity coverage (h)",
                min_value=0.0,
                disabled=disabled,
                key=_KEYS["capacity_coverage_hours"],
            )
            if st.session_state.get(_KEYS["market_mfrr"]) and st.session_state.get(_KEYS["bid_kind"]) == "Fixed minimum prices":
                st.number_input(
                    "mFRR fixed upward price (EUR/MW/h)",
                    min_value=0.0,
                    disabled=disabled,
                    key=_KEYS["mfrr_fixed_up"],
                )
            if st.session_state.get(_KEYS["market_afrr"]) and st.session_state.get(_KEYS["bid_kind"]) == "Fixed minimum prices":
                a1, a2 = st.columns(2)
                with a1:
                    st.number_input(
                        "aFRR fixed upward price (EUR/MW/h)",
                        min_value=0.0,
                        disabled=disabled,
                        key=_KEYS["afrr_fixed_up"],
                    )
                with a2:
                    st.number_input(
                        "aFRR fixed downward price (EUR/MW/h)",
                        min_value=0.0,
                        disabled=disabled,
                        key=_KEYS["afrr_fixed_down"],
                    )
            if st.session_state.get(_KEYS["market_afrr"]):
                st.number_input(
                    "aFRR upward-capacity fraction",
                    min_value=0.0,
                    max_value=1.0,
                    disabled=disabled,
                    key=_KEYS["afrr_up_fraction"],
                )
    with st.expander("Solver and diagnostics", expanded=False):
        st.checkbox(SOLVER_LOG_COPY, disabled=disabled, key=_KEYS["detailed_solver"])
        solver_commitment = {
            "fixed_speed_pump": st.session_state.get(_KEYS["fixed_speed_pump"]),
            "turbine_minimum_enabled": st.session_state.get(_KEYS["turbine_minimum_enabled"]),
            "forbid_simultaneous_operation": st.session_state.get(_KEYS["forbid_simultaneous_operation"]),
        }
        if machine_commitment_enabled(solver_commitment):
            st.markdown(f"**{SOLVER_CONTROLS_HEADING}**")
            if not machine_commitment_enabled(form):
                st.session_state[_KEYS["mip_gap_pct"]] = coalesce_float(
                    form.get("mip_gap_pct"), DEFAULT_MIP_GAP_PCT
                )
                st.session_state[_KEYS["mip_time_limit_min"]] = coalesce_float(
                    form.get("mip_time_limit_min"), 15.0
                )
            else:
                _ensure_session_number(
                    _KEYS["mip_gap_pct"], form.get("mip_gap_pct"), DEFAULT_MIP_GAP_PCT
                )
                _ensure_session_number(_KEYS["mip_time_limit_min"], form.get("mip_time_limit_min"), 15.0)
            st.number_input(
                MIP_GAP_LABEL,
                min_value=0.0,
                max_value=100.0,
                disabled=disabled,
                key=_KEYS["mip_gap_pct"],
            )
            st.number_input(
                MIP_TIME_LABEL,
                min_value=0.0,
                disabled=disabled,
                key=_KEYS["mip_time_limit_min"],
            )
            st.caption(SOLVER_CONTROLS_HELP)

    incoming = demo_form() if demo else _collect(previous)
    updated = apply_form_transitions(previous, incoming, reset_grid=reset_grid and not demo)
    if not demo:
        updated = apply_period_preset(updated)
    if updated != incoming and not demo:
        invalidate_if_form_changed(state, previous=previous, current=updated)
        set_form(state, updated)
        state["pending_widgets"] = _widget_values(updated)
        _persist(state)
        st.rerun()
    if not demo:
        invalidate_if_form_changed(state, previous=previous, current=updated)
    else:
        set_form(state, updated)
    _persist(state)

    error = state.get("validation_error")
    if error:
        render_status_panel("danger", "Cannot continue", str(error))

    reason = lightweight_continue_reason(updated)
    events = render_action_row(
        primary="Continue",
        primary_disabled=reason is not None,
        disabled_reason=reason,
        key="sib-cfg-actions",
        interactive=True,
    )
    if events.primary:
        try:
            snapshot = build_snapshot(updated, demo=demo)
        except ValueError as exc:
            set_validation_error(state, str(exc))
            _persist(state)
            st.rerun()
        store_snapshot(state, snapshot, str(snapshot.get("form_fingerprint") or ""))
        _persist(state)
        st.rerun()
