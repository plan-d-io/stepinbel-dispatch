"""Idle Review page. Reads only the stored snapshot, never live widget keys."""

from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from ui.flow import (
    SESSION_KEY,
    back_to_configure,
    is_demo,
    require_json_compatible,
    stored_form,
    stored_snapshot,
    unlock_results,
)
from ui.services.artifacts import open_demo_artifacts
from ui.services.launch import prepare_live_job, start_prepared_job
from ui.services.paths import JOB_QUERY_KEY
from ui.presentation.components import (
    render_action_row,
    render_blocked_state,
    render_page_header,
    render_section_heading,
    render_status_panel,
    render_table_frame,
)
from ui.presentation.tokens import (
    DEMO_MODE_HELP,
    MARKETS_INDEPENDENT_COPY,
    REVIEW_FINAL_HEADING,
    REVIEW_READY_BODY,
    REVIEW_READY_TITLE,
)
from ui.services.form import BID_FIXED, PRESET_CUSTOM, STORAGE_POND
from ui.services.snapshot import (
    EXECUTION_DISABLED_REASON,
    INCOMPLETE_SNAPSHOT,
    market_labels,
    review_warnings,
    snapshot_block_reason,
)


def _persist(state: dict[str, Any]) -> None:
    st.session_state[SESSION_KEY] = require_json_compatible(dict(state))


def _rows(items: tuple[tuple[str, str], ...]) -> dict[str, list[str]]:
    return {
        "Item": [item[0] for item in items],
        "Value": [item[1] for item in items],
    }


def _quantile_label(value: object) -> str:
    try:
        return f"Historical P{int(round(float(value) * 100))}"
    except (TypeError, ValueError):
        return "Historical quantile"


def _period_text(period: Mapping[str, Any]) -> str:
    return (
        f"Belgian delivery {period.get('start_date')} to {period.get('end_date')}"
    )


def _asset_storage(snapshot: Mapping[str, Any]) -> str:
    asset = snapshot.get("asset") if isinstance(snapshot.get("asset"), Mapping) else {}
    form = snapshot.get("form") if isinstance(snapshot.get("form"), Mapping) else {}
    if form.get("storage_mode") == STORAGE_POND or asset.get("pond_energy_mwh") is not None:
        return f"{float(asset.get('pond_energy_mwh') or 0.0):.3f} MWh pond energy"
    hours = asset.get("storage_hours")
    return f"{float(hours or 0.0):.0f} h discharge at rated turbine"


def _pv_text(site: Mapping[str, Any], *, demo: bool) -> str:
    pv_kw = float(site.get("pv_ac_kw") or 0.0)
    if pv_kw <= 0:
        return "Off · Belgian profile available if enabled"
    mode = "day-ahead valuation" if site.get("pv_revenue_mode") != "fixed" else (
        f"fixed {site.get('pv_fixed_price_eur_mwh')} EUR/MWh"
    )
    region = site.get("pv_region") or "Belgium"
    return f"{pv_kw:.0f} kW {region}, {mode}"


def _balancing_text(snapshot: Mapping[str, Any]) -> str:
    markets = list(snapshot.get("markets") or [])
    if "mfrr" not in markets and "afrr" not in markets:
        return "Not applicable — Day-ahead only."
    balancing = snapshot.get("balancing") if isinstance(snapshot.get("balancing"), Mapping) else {}
    activation = str(balancing.get("activation") or snapshot.get("activation") or "balanced").title()
    if balancing.get("bid_kind") == BID_FIXED:
        bidding = "Fixed minimum capacity prices"
    else:
        bidding = f"{_quantile_label(balancing.get('bid_quantile'))} capacity bids"
    hours = balancing.get("capacity_coverage_hours")
    return f"{activation} · {bidding} · {hours} h coverage"


def _render_blocked(state: dict[str, Any], *, demo: bool, reason: str) -> None:
    primary = "View demonstration results" if demo else "Run simulation"
    render_blocked_state("Cannot open Review", reason)
    events = render_action_row(
        back="Back",
        primary=primary,
        primary_disabled=True,
        disabled_reason=EXECUTION_DISABLED_REASON,
        key="sib-review-actions",
        interactive=True,
    )
    if events.back:
        back_to_configure(state)
        _persist(state)
        st.rerun()


def render_review(state: dict[str, Any]) -> None:
    snapshot = stored_snapshot(state)
    form = stored_form(state)
    blocked = snapshot_block_reason(snapshot, form)
    demo = is_demo(state)
    if isinstance(snapshot, Mapping) and isinstance(snapshot.get("demo"), bool):
        demo = snapshot["demo"]
    render_page_header(
        "Stage 2 of 3",
        "Review & run",
        (
            "Saved demonstration. Settings are read-only and match the saved result."
            if demo
            else "Configured settings. Review before starting the simulation run."
        ),
    )
    if blocked or snapshot is None:
        _render_blocked(
            state,
            demo=demo,
            reason=blocked or "No configured snapshot is available.",
        )
        return

    try:
        _render_review_body(snapshot, demo=demo)
    except Exception:
        _render_blocked(state, demo=demo, reason=INCOMPLETE_SNAPSHOT)
        return

    launch_error = state.get("launch_error")
    if isinstance(launch_error, str) and launch_error:
        render_status_panel("danger", "The simulation could not be started", launch_error)
    events = render_action_row(
        back="Back",
        primary="View demonstration results" if demo else "Run simulation",
        primary_disabled=False,
        key="sib-review-actions",
        interactive=True,
    )
    if events.back:
        back_to_configure(state)
        _persist(state)
        st.rerun()
    if events.primary:
        if demo:
            try:
                result = open_demo_artifacts()
            except ValueError as exc:
                state["launch_error"] = str(exc)
                _persist(state)
                st.rerun()
                return
            unlock_results(state, result)
            _persist(state)
            st.rerun()
            return
        prepared = prepare_live_job(state)
        if prepared.get("query_job"):
            st.query_params[JOB_QUERY_KEY] = str(prepared["query_job"])
        _persist(state)
        if not prepared.get("ok"):
            state["launch_error"] = str(prepared.get("error") or "The simulation could not be started.")
            _persist(state)
            st.rerun()
            return
        if prepared.get("reconnect") or not prepared.get("ready_to_start"):
            st.rerun()
            return
        started = start_prepared_job(state)
        if not started.get("ok"):
            state["launch_error"] = str(started.get("error") or "The simulation could not be started.")
        _persist(state)
        st.rerun()


def _render_review_body(snapshot: Mapping[str, Any], *, demo: bool) -> None:
    if demo:
        render_status_panel(
            "info",
            "Demo mode",
            f"{DEMO_MODE_HELP} It does not run the HiGHS solver or write output.",
        )

    period = snapshot["period"]
    asset = snapshot["asset"]
    site = snapshot["site"]
    derived = snapshot["derived"]
    markets = list(snapshot.get("markets") or [])

    render_section_heading("Markets and period", MARKETS_INDEPENDENT_COPY)
    render_table_frame(
        title="Configured markets",
        caption="Read-only configured inputs. This view does not create a request.",
        data=_rows(
            (
                ("Markets", market_labels(markets)),
                ("Period", _period_text(period)),
                (
                    "Balancing activation",
                    "Not applicable"
                    if snapshot.get("activation") is None
                    else str(snapshot.get("activation")).title(),
                ),
            )
        ),
    )
    render_section_heading("Pumped-hydro asset")
    render_table_frame(
        title="Configured asset",
        caption="Derived reservoir capacity comes from the stored snapshot.",
        data=_rows(
            (
                (
                    "Pump / turbine",
                    f"{float(asset['power_pump_mw']):.3f} / {float(asset['power_turbine_mw']):.3f} MW",
                ),
                (
                    "Efficiencies",
                    f"{float(asset['eta_pump']):.2f} / {float(asset['eta_turbine']):.2f}",
                ),
                ("Storage", _asset_storage(snapshot)),
                (
                    "Derived reservoir E_max",
                    f"{float(derived['e_max_mwh']):.3f} MWh",
                ),
            )
        ),
    )
    render_section_heading("Grid connection")
    st.write(
        f"{float(site['grid_import_mw']):.3f} MW import / "
        f"{float(site['grid_export_mw']):.3f} MW export"
    )
    render_section_heading("PV")
    st.write(_pv_text(site, demo=demo))
    render_section_heading("Balancing assumptions")
    st.write(_balancing_text(snapshot))
    render_section_heading(REVIEW_FINAL_HEADING)
    render_status_panel("success", REVIEW_READY_TITLE, REVIEW_READY_BODY)
    for warning in review_warnings(snapshot):
        render_status_panel("warning", "Check before running", warning)
    if snapshot.get("detailed_solver_output"):
        st.caption("Detailed HiGHS solver messages are requested for the later run log.")
