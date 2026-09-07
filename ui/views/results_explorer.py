"""Functional Data explorer tab: selected-week Parquet charts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, NamedTuple

import streamlit as st
from plotly import graph_objects as go

from ui.flow import (
    SESSION_KEY,
    require_json_compatible,
    result_open_identity,
    set_explorer_market,
    set_explorer_week,
)
from ui.presentation.components import (
    ExplorerPanel,
    build_explorer_figure,
    explorer_figure_key,
    render_explorer_figure,
    render_section_heading,
    render_status_panel,
)
from ui.presentation.tokens import (
    EXPLORER_CAPACITY_BOTH_CAPTION,
    EXPLORER_DA_CAPACITY_COPY,
    EXPLORER_ERROR_BODY,
    EXPLORER_ERROR_TITLE,
    EXPLORER_EXPANDER_TITLE,
    EXPLORER_GROUP_CAPACITY,
    EXPLORER_GROUP_GRID,
    EXPLORER_GROUP_MAIN,
    EXPLORER_INTERVAL_CAPTION,
    EXPLORER_NO_CAPACITY_COPY,
    EXPLORER_PANEL_CAPACITY,
    EXPLORER_WEEK_INTRO,
    EXPLORER_ZOOM_CAPTION,
    PV_NOT_INCLUDED_COPY,
)
from ui.services.explorer_query import (
    ExplorerError,
    explorer_cache_key,
    explorer_weeks_by_market,
    file_identity,
    load_explorer_week,
    trusted_dispatch_path,
)
from ui.services.explorer_weeks import default_week_id, shared_week_ids, week_count_copy
from ui.services.launch import TEST_HOOKS
from ui.services.paths import MARKET_LABELS
from ui.services.result_format import display_market_keys


def _persist(state: Mapping[str, Any]) -> None:
    st.session_state[SESSION_KEY] = require_json_compatible(dict(state))


@st.cache_data(max_entries=12, show_spinner=False)
def _cached_explorer_week(
    cache_key: tuple[object, ...],
    result: dict[str, Any],
    market: str,
    week_id: str,
    job: dict[str, Any] | None,
    outputs_root: str | None,
) -> dict[str, Any]:
    _ = cache_key
    root = Path(outputs_root) if outputs_root else None
    payload = load_explorer_week(result, market=market, week_id=week_id, job=job, outputs_root=root)
    payload.pop("dispatch_identity", None)
    return payload


def _outputs_root() -> Path | None:
    hooked = TEST_HOOKS.get("outputs_root")
    return Path(hooked) if hooked is not None else None


def _axis(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "x_index": list(payload["x_index"]),
        "hover_labels": list(payload["hover_labels"]),
        "tick_vals": list(payload["tick_vals"]),
        "tick_text": list(payload["tick_text"]),
    }


class ExplorerChartModel(NamedTuple):
    panels: tuple[ExplorerPanel, ...]
    captions: tuple[str, ...]
    pv_message: str | None
    capacity_message: str | None


def explorer_chart_model(payload: Mapping[str, Any]) -> ExplorerChartModel:
    dispatch = payload["dispatch"]
    n = int(payload["row_count"])
    pv_included = bool(payload["pv_included"])

    def require(name: str) -> list[float]:
        values = list(dispatch[name])
        if len(values) != n:
            raise ExplorerError(EXPLORER_ERROR_BODY)
        return values

    panels: list[ExplorerPanel] = [
        ExplorerPanel(
            group=EXPLORER_GROUP_MAIN,
            title="Pump and turbine power",
            y_label="Power (MW)",
            series=(
                ("Pumping", require("p_pump_mw")),
                ("Generation", require("p_turbine_mw")),
            ),
        ),
        ExplorerPanel(
            group=EXPLORER_GROUP_MAIN,
            title="Reservoir level",
            y_label="Stored energy (MWh)",
            series=(("Reservoir level", require("reservoir_end_mwh")),),
            reference_lines=((float(payload["e_max_mwh"]), "Reservoir capacity"),),
        ),
        ExplorerPanel(
            group=EXPLORER_GROUP_MAIN,
            title="Market buy and sell prices",
            y_label="Price (EUR/MWh)",
            series=(
                ("Buy price", require("market_buy_price_eur_mwh")),
                ("Sell price", require("market_sell_price_eur_mwh")),
            ),
            y_zero=False,
        ),
    ]
    revenue_series = [
        ("Net energy revenue", require("market_energy_net_eur")),
    ]
    if pv_included:
        revenue_series.append(("PV revenue", require("pv_revenue_eur")))
    revenue_series.append(("Total interval revenue", require("total_revenue_eur")))
    panels.append(
        ExplorerPanel(
            group=EXPLORER_GROUP_MAIN,
            title="Interval revenue",
            y_label="Revenue per quarter-hour (EUR)",
            series=tuple(revenue_series),
            y_zero=False,
        )
    )
    pv_message = None
    if pv_included:
        panels.append(
            ExplorerPanel(
                group=EXPLORER_GROUP_MAIN,
                title="PV allocation",
                y_label="Power (MW)",
                series=(
                    ("Available PV", require("pv_available_mw")),
                    ("PV used for pumping", require("pv_to_pump_mw")),
                    ("PV exported", require("pv_export_mw")),
                    ("PV curtailed", require("pv_curtail_mw")),
                ),
            )
        )
    else:
        pv_message = PV_NOT_INCLUDED_COPY
    export_series = [("Turbine output", require("p_turbine_mw"))]
    if pv_included:
        export_series.append(("PV export", require("pv_export_mw")))
    panels.extend(
        [
            ExplorerPanel(
                group=EXPLORER_GROUP_GRID,
                title="Import",
                y_label="Power (MW)",
                series=(("Grid import for pumping", require("p_pump_grid_mw")),),
                reference_lines=((float(payload["effective_grid_import_mw"]), "Import limit"),),
            ),
            ExplorerPanel(
                group=EXPLORER_GROUP_GRID,
                title="Export",
                y_label="Power (MW)",
                series=tuple(export_series),
                reference_lines=((float(payload["effective_grid_export_mw"]), "Export limit"),),
            ),
        ]
    )
    captions = [EXPLORER_ZOOM_CAPTION, EXPLORER_INTERVAL_CAPTION]
    capacity_message = None
    if not payload["has_capacity"]:
        capacity_message = EXPLORER_DA_CAPACITY_COPY
        return ExplorerChartModel(tuple(panels), tuple(captions), pv_message, capacity_message)
    capacity = payload["capacity"]
    if not capacity or not capacity.get("has_commitment"):
        capacity_message = EXPLORER_NO_CAPACITY_COPY
        return ExplorerChartModel(tuple(panels), tuple(captions), pv_message, capacity_message)
    series: list[tuple[str, list[float]]] = []
    if capacity.get("upward") is not None:
        upward = list(capacity["upward"])
        if len(upward) != n:
            raise ExplorerError(EXPLORER_ERROR_BODY)
        series.append(("Upward commitment", upward))
    if capacity.get("downward") is not None:
        downward = list(capacity["downward"])
        if len(downward) != n:
            raise ExplorerError(EXPLORER_ERROR_BODY)
        series.append(("Downward commitment", downward))
    if not series:
        capacity_message = EXPLORER_NO_CAPACITY_COPY
        return ExplorerChartModel(tuple(panels), tuple(captions), pv_message, capacity_message)
    if len(series) == 2:
        captions.append(EXPLORER_CAPACITY_BOTH_CAPTION)
    panels.append(
        ExplorerPanel(
            group=EXPLORER_GROUP_CAPACITY,
            title=EXPLORER_PANEL_CAPACITY,
            y_label="Committed capacity (MW)",
            series=tuple(series),
        )
    )
    return ExplorerChartModel(tuple(panels), tuple(captions), pv_message, capacity_message)


def explorer_figure_from_payload(payload: Mapping[str, Any]) -> go.Figure:
    model = explorer_chart_model(payload)
    axis = _axis(payload)
    return build_explorer_figure(
        x_index=axis["x_index"],
        hover_labels=axis["hover_labels"],
        tick_vals=axis["tick_vals"],
        tick_text=axis["tick_text"],
        panels=model.panels,
    )


def render_data_explorer(
    state: dict[str, Any],
    *,
    result: Mapping[str, Any],
    view: Mapping[str, Any],
    default_market: str,
) -> None:
    job = state.get("job") if isinstance(state.get("job"), Mapping) else None
    outputs_root = _outputs_root()
    identity = result_open_identity(result)
    try:
        weeks_by_market = explorer_weeks_by_market(result, job=job, outputs_root=outputs_root)
    except ExplorerError:
        render_status_panel("danger", EXPLORER_ERROR_TITLE, EXPLORER_ERROR_BODY)
        return
    markets = display_market_keys(weeks_by_market)
    if not markets:
        render_status_panel("danger", EXPLORER_ERROR_TITLE, EXPLORER_ERROR_BODY)
        return
    shared = shared_week_ids(weeks_by_market)
    selected_market = str(view.get("selected_explorer_market") or default_market)
    if selected_market not in markets:
        selected_market = default_market if default_market in markets else markets[0]
    market_weeks = weeks_by_market[selected_market]
    demo = result.get("source") == "demo"
    fallback_week = default_week_id(market_weeks, demo=bool(demo))
    selected_week = view.get("selected_explorer_week")
    if selected_week not in {item["week_id"] for item in market_weeks}:
        selected_week = fallback_week

    if len(markets) > 1:
        left, right = st.columns(2)
        labels = [MARKET_LABELS[item] for item in markets]
        with left:
            chosen_market = st.selectbox(
                "Market",
                labels,
                index=labels.index(MARKET_LABELS[selected_market]),
                key=f"sib-explorer-market-{identity}",
            )
        next_market = markets[labels.index(str(chosen_market))]
        with right:
            week_labels = [item["label"] for item in market_weeks]
            week_ids = [item["week_id"] for item in market_weeks]
            chosen_week = st.selectbox(
                "Week",
                week_labels,
                index=week_ids.index(selected_week),
                key=f"sib-explorer-week-{identity}",
            )
        next_week = week_ids[week_labels.index(str(chosen_week))]
        if next_market != selected_market:
            set_explorer_market(state, next_market)
            if selected_week in shared and selected_week in {
                item["week_id"] for item in weeks_by_market[next_market]
            }:
                set_explorer_week(state, selected_week)
            else:
                set_explorer_week(
                    state,
                    default_week_id(weeks_by_market[next_market], demo=bool(demo)),
                )
            _persist(state)
            st.rerun()
            return
        if next_week != selected_week:
            set_explorer_week(state, next_week)
            _persist(state)
            st.rerun()
            return
    else:
        week_labels = [item["label"] for item in market_weeks]
        week_ids = [item["week_id"] for item in market_weeks]
        chosen_week = st.selectbox(
            "Week",
            week_labels,
            index=week_ids.index(selected_week),
            key=f"sib-explorer-week-{identity}",
        )
        next_week = week_ids[week_labels.index(str(chosen_week))]
        if next_week != selected_week:
            set_explorer_week(state, next_week)
            _persist(state)
            st.rerun()
            return

    week = next(item for item in market_weeks if item["week_id"] == selected_week)
    with st.expander(EXPLORER_EXPANDER_TITLE, expanded=False):
        st.write(EXPLORER_WEEK_INTRO)
        st.write(week_count_copy(week))

    try:
        dispatch_path = trusted_dispatch_path(
            result, market=selected_market, job=job, outputs_root=outputs_root
        )
        cache_key = explorer_cache_key(
            result_identity=identity,
            market=selected_market,
            week_id=str(selected_week),
            dispatch_identity=file_identity(dispatch_path),
        )
        payload = _cached_explorer_week(
            cache_key,
            dict(result),
            selected_market,
            str(selected_week),
            dict(job) if isinstance(job, Mapping) else None,
            str(outputs_root) if outputs_root is not None else None,
        )
    except ExplorerError:
        render_status_panel("danger", EXPLORER_ERROR_TITLE, EXPLORER_ERROR_BODY)
        return
    try:
        _render_charts(payload, identity=identity)
    except ExplorerError:
        render_status_panel("danger", EXPLORER_ERROR_TITLE, EXPLORER_ERROR_BODY)


def _render_charts(payload: Mapping[str, Any], *, identity: str) -> None:
    market = str(payload["market"])
    week_id = str(payload["week"]["week_id"])
    model = explorer_chart_model(payload)
    figure = explorer_figure_from_payload(payload)
    render_section_heading(EXPLORER_GROUP_MAIN)
    render_explorer_figure(
        figure,
        key=explorer_figure_key(identity, market, week_id),
        captions=model.captions,
    )
    if model.pv_message:
        st.write(model.pv_message)
    if model.capacity_message:
        render_section_heading(EXPLORER_GROUP_CAPACITY)
        st.write(model.capacity_message)
