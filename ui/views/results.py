"""Adaptive Results page: Overview, Market detail, Data explorer, Technical details, Downloads."""

from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from ui.flow import (
    RESULTS_IDENTITY_KEY,
    SESSION_KEY,
    require_json_compatible,
    result_open_identity,
    set_detail_market,
    set_results_tab,
    sync_results_view,
)
from ui.presentation.components import (
    render_how_to_read_results,
    render_page_header,
    render_readouts,
    render_result_tabs,
    render_status_panel,
)
from ui.presentation.tokens import (
    RESULTS_ERROR_BODY,
    RESULTS_ERROR_TITLE,
    RESULTS_SUBTITLE_MULTI,
    RESULTS_SUBTITLE_ONE,
)
from ui.services.artifacts import ERROR_BINDING, result_is_valid
from ui.services.launch import TEST_HOOKS
from ui.services.result_format import default_explorer_market
from ui.services.result_view import ResultViewError, load_result_display
from ui.services.result_warning import load_best_available_warning
from ui.services.explorer_query import ExplorerError, explorer_weeks_by_market
from ui.services.explorer_weeks import default_week_id
from ui.views.results_detail import render_market_detail
from ui.views.results_downloads import render_downloads
from ui.views.results_explorer import render_data_explorer
from ui.views.results_overview import render_overview
from ui.views.results_technical import render_technical_details


def _persist(state: Mapping[str, Any]) -> None:
    st.session_state[SESSION_KEY] = require_json_compatible(dict(state))


def _header_items(header: Mapping[str, Any]) -> list[tuple[str, str]]:
    items = [
        ("Markets", str(header["markets"])),
        ("Simulation period", str(header["period"])),
        ("Pump / turbine", str(header["pump_turbine"])),
        ("Storage", str(header["storage"])),
        ("Grid connection", str(header["grid"])),
        ("PV", str(header["pv"])),
    ]
    if header.get("balancing"):
        items.append(("Balancing strategy", str(header["balancing"])))
    if header.get("run_type"):
        items.append(("Run type", str(header["run_type"])))
    return items


def _render_header(payload: Mapping[str, Any]) -> None:
    one_market = bool(payload["one_market"])
    render_page_header(
        "Stage 3 of 3",
        "Results",
        RESULTS_SUBTITLE_ONE if one_market else RESULTS_SUBTITLE_MULTI,
    )
    items = _header_items(payload["header"])
    mid = 4 if len(items) > 4 else len(items)
    render_readouts(items[:mid])
    if len(items) > mid:
        render_readouts(items[mid:])
    render_how_to_read_results(one_market=one_market)


def render_results(state: dict[str, Any]) -> None:
    result = state.get("result") if isinstance(state.get("result"), Mapping) else None
    job = state.get("job") if isinstance(state.get("job"), Mapping) else None
    if not result_is_valid(result, job=job, outputs_root=TEST_HOOKS.get("outputs_root")):
        render_page_header("Stage 3 of 3", "Results")
        render_status_panel("danger", RESULTS_ERROR_TITLE, RESULTS_ERROR_BODY)
        return
    try:
        payload = load_result_display(result, job=job, outputs_root=TEST_HOOKS.get("outputs_root"))
    except ResultViewError:
        render_page_header("Stage 3 of 3", "Results")
        render_status_panel("danger", RESULTS_ERROR_TITLE, RESULTS_ERROR_BODY)
        return
    assert result is not None
    default_market = str(payload["highest_revenue_market"])
    explorer_default = default_explorer_market(payload["markets"])
    default_week = None
    allowed_weeks = None
    try:
        weeks_by_market = explorer_weeks_by_market(
            result, job=job, outputs_root=TEST_HOOKS.get("outputs_root")
        )
        current_view = state.get("results_view") if isinstance(state.get("results_view"), Mapping) else {}
        stored_explorer = current_view.get("selected_explorer_market") if isinstance(current_view, Mapping) else None
        same_result = state.get(RESULTS_IDENTITY_KEY) == result_open_identity(result)
        if same_result and stored_explorer in weeks_by_market:
            explorer_for_weeks = str(stored_explorer)
        else:
            explorer_for_weeks = explorer_default if explorer_default in weeks_by_market else next(iter(weeks_by_market))
        default_week = default_week_id(
            weeks_by_market[explorer_for_weeks],
            demo=result.get("source") == "demo",
        )
        allowed_weeks = [item["week_id"] for item in weeks_by_market[explorer_for_weeks]]
    except ExplorerError:
        default_week = None
        allowed_weeks = None
    view = sync_results_view(
        state,
        result=result,
        default_market=default_market,
        allowed_markets=list(payload["markets"]),
        default_week=default_week,
        allowed_weeks=allowed_weeks,
        default_explorer_market=explorer_default,
        default_technical_market=explorer_default,
    )
    _persist(state)
    _render_header(payload)
    warning = None
    try:
        warning = load_best_available_warning(
            result, job=job, outputs_root=TEST_HOOKS.get("outputs_root")
        )
    except ValueError as exc:
        if str(exc) != ERROR_BINDING:
            render_status_panel("danger", RESULTS_ERROR_TITLE, RESULTS_ERROR_BODY)
            return
    except (OSError, TypeError):
        render_status_panel("danger", RESULTS_ERROR_TITLE, RESULTS_ERROR_BODY)
        return
    if warning is not None:
        render_status_panel("warning", warning["title"], warning["body"])
    clicked = render_result_tabs(str(view["active_tab"]), interactive=True)
    if clicked and clicked != view["active_tab"]:
        set_results_tab(state, clicked)
        _persist(state)
        st.rerun()
        return
    tab = str(view["active_tab"])
    if tab == "Overview":
        render_overview(payload)
        return
    if tab == "Market detail":
        identity = result_open_identity(result)
        selected = render_market_detail(
            payload,
            selected_market=str(view["selected_detail_market"] or payload["highest_revenue_market"]),
            identity=identity,
        )
        if selected != view["selected_detail_market"]:
            set_detail_market(state, selected)
            _persist(state)
        return
    if tab == "Data explorer":
        render_data_explorer(
            state,
            result=result,
            view=view,
            default_market=explorer_default,
        )
        return
    if tab == "Technical details":
        render_technical_details(
            state,
            result=result,
            view=view,
            default_market=explorer_default,
        )
        return
    render_downloads(state, result=result)
