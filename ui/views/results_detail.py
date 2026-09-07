"""Market detail tab for one selected dedicated-market result."""

from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from ui.presentation.components import (
    render_chart_frame,
    render_display_table,
    render_expander,
    render_metric_group,
    render_readouts,
    render_section_heading,
)
from ui.presentation.tokens import (
    ABOUT_SIMULTANEOUS_BODY,
    ABOUT_SIMULTANEOUS_TITLE,
    DAY_AHEAD_CAPACITY_COPY,
    PV_NOT_INCLUDED_COPY,
)
from ui.services.paths import MARKET_LABELS
from ui.services.result_view import CAPACITY_PREVIEW_LIMIT


def render_market_detail(
    payload: Mapping[str, Any],
    *,
    selected_market: str,
    identity: str,
) -> str:
    markets = list(payload["markets"])
    if selected_market not in markets:
        selected_market = str(payload["highest_revenue_market"])
    if len(markets) > 1:
        labels = [MARKET_LABELS[item] for item in markets]
        current = MARKET_LABELS[selected_market]
        chosen = st.selectbox(
            "Market",
            labels,
            index=labels.index(current),
            key=f"sib-detail-market-{identity}",
        )
        selected_market = markets[labels.index(str(chosen))]
    child = payload["children"][selected_market]
    formatted = child["formatted"]
    metrics = [
        ("Total site revenue", formatted["total"]),
        ("Net energy revenue", formatted["energy"]),
    ]
    if child["has_capacity"]:
        metrics.append(("Capacity revenue", formatted["capacity"]))
    if child["pv_included"]:
        metrics.append(("PV revenue", formatted["pv"]))
    render_metric_group(tuple(metrics), key="sib-detail-metrics")
    _render_revenue(child)
    _render_operations(child)
    _render_pv(child)
    _render_capacity(child)
    _render_simultaneous(child)
    return selected_market


def _render_revenue(child: Mapping[str, Any]) -> None:
    composition = child["composition"]
    values = list(composition["values"])
    render_chart_frame(
        title="Revenue composition",
        x_label="Revenue component",
        y_label="EUR",
        caption="Stored revenue components.",
        categories=list(composition["categories"]),
        series=(("Revenue", values),),
        kind="bar",
        y_zero=all(item >= 0 for item in values),
        key="sib-chart-revenue-composition",
    )
    monthly = child["monthly"]
    monthly_values = list(monthly["total_site_revenue_eur"])
    render_chart_frame(
        title="Monthly total site revenue",
        x_label="Month",
        y_label="EUR",
        caption="Stored monthly totals.",
        categories=list(monthly["periods"]),
        series=(("Total site revenue", monthly_values),),
        kind="bar",
        y_zero=all(item >= 0 for item in monthly_values),
        key="sib-chart-monthly-revenue",
    )


def _render_operations(child: Mapping[str, Any]) -> None:
    formatted = child["formatted"]
    render_section_heading("Operations")
    render_readouts(
        (
            ("Pumped energy", formatted["pumped"]),
            ("Turbined energy", formatted["turbined"]),
            ("Full cycles", formatted["cycles"]),
            ("Reservoir capacity", formatted["reservoir"]),
            ("Initial reservoir level", formatted["reservoir_initial"]),
            ("Final reservoir level", formatted["reservoir_final"]),
        )
    )


def _render_pv(child: Mapping[str, Any]) -> None:
    render_section_heading("PV allocation")
    if not child["pv_included"]:
        st.write(PV_NOT_INCLUDED_COPY)
        return
    formatted = child["formatted"]
    render_readouts(
        (
            ("Available PV", formatted["pv_available"]),
            ("PV self-consumed", formatted["pv_self"]),
            ("PV exported", formatted["pv_export"]),
            ("PV curtailed", formatted["pv_curtail"]),
        )
    )


def _render_capacity(child: Mapping[str, Any]) -> None:
    render_section_heading("Capacity commitments")
    if not child["has_capacity"]:
        st.write(DAY_AHEAD_CAPACITY_COPY)
        return
    formatted = child["formatted"]
    capacity = child["capacity"]
    render_readouts(
        (
            ("Committed blocks", formatted["block_count"]),
            ("Capacity revenue", formatted["capacity_revenue"]),
        )
    )
    total = int(capacity["block_count"])
    with st.expander("Capacity commitments", expanded=False):
        if capacity["truncated"]:
            st.write(f"Showing the first {CAPACITY_PREVIEW_LIMIT} of {total} stored capacity blocks.")
        preview = list(capacity["preview"])
        if preview:
            table = {key: [row[key] for row in preview] for key in preview[0]}
            render_display_table(table)


def _render_simultaneous(child: Mapping[str, Any]) -> None:
    formatted = child["formatted"]
    render_section_heading("Simultaneous operation")
    render_readouts(
        (
            ("Intervals", formatted["simul_n"]),
            ("Overlap (MWh)", formatted["simul_mwh"]),
            ("Energy net in those intervals (EUR)", formatted["simul_eur"]),
        )
    )
    render_expander(ABOUT_SIMULTANEOUS_TITLE, ABOUT_SIMULTANEOUS_BODY, expanded=False)
