"""Overview tab for one or more dedicated-market results."""

from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from ui.presentation.components import (
    render_column_glossary,
    render_display_table,
    render_market_highlight,
    render_section_heading,
)
from ui.presentation.tokens import RESULTS_TABLE_CAPTION


def _highlight_note(row: Mapping[str, Any], *, one_market: bool) -> str | None:
    if one_market:
        return None
    return str(row["formatted"]["note"])


def render_overview(payload: Mapping[str, Any]) -> None:
    rows = list(payload["rows"])
    one_market = bool(payload["one_market"])
    pv_included = bool(payload.get("pv_included"))
    wind_included = bool(payload.get("wind_included"))
    columns = st.columns(len(rows) or 1)
    for column, row in zip(columns, rows, strict=True):
        formatted = row["formatted"]
        with column:
            render_market_highlight(
                title=formatted["market"],
                total=formatted["total"],
                energy=formatted["energy"],
                capacity=formatted["capacity"] if row["has_capacity"] else None,
                pv=formatted["pv"] if row["show_pv"] else None,
                wind=formatted.get("wind") if row.get("show_wind") else None,
                note=_highlight_note(row, one_market=one_market),
            )
    render_section_heading("Operational results" if one_market else "Operational comparison")
    render_column_glossary(pv_included=pv_included, wind_included=wind_included)
    with st.container(key="sib-overview-table"):
        st.html('<div class="sib-table-numeric"></div>')
        render_display_table(
            _overview_table(
                rows,
                one_market=one_market,
                pv_included=pv_included,
                wind_included=wind_included,
            )
        )
        st.caption(RESULTS_TABLE_CAPTION)


def _overview_table(
    rows: list[Mapping[str, Any]],
    *,
    one_market: bool,
    pv_included: bool = True,
    wind_included: bool = False,
) -> dict[str, list[str]]:
    _ = one_market
    columns: dict[str, list[str]] = {
        "Market": [],
        "Total site revenue (EUR)": [],
        "Net energy revenue (EUR)": [],
        "Capacity revenue (EUR)": [],
    }
    if pv_included:
        columns["PV revenue (EUR)"] = []
    if wind_included:
        columns["Wind revenue (EUR)"] = []
    columns.update(
        {
            "Pumped energy (MWh)": [],
            "Turbined energy (MWh)": [],
            "Full cycles": [],
        }
    )
    if pv_included:
        columns["PV self-consumed (MWh)"] = []
        columns["PV self-consumed (%)"] = []
        columns["PV exported (MWh)"] = []
        columns["PV curtailed (MWh)"] = []
    if wind_included:
        columns["Wind self-consumed (MWh)"] = []
        columns["Wind self-consumed (%)"] = []
        columns["Wind exported (MWh)"] = []
        columns["Wind curtailed (MWh)"] = []
    columns.update(
        {
            "Simultaneous intervals": [],
            "Simultaneous overlap (MWh)": [],
        }
    )
    for row in rows:
        formatted = row["formatted"]
        columns["Market"].append(formatted["market"])
        columns["Total site revenue (EUR)"].append(formatted["total_amount"])
        columns["Net energy revenue (EUR)"].append(formatted["energy_amount"])
        columns["Capacity revenue (EUR)"].append(formatted["capacity_amount"])
        if pv_included:
            columns["PV revenue (EUR)"].append(formatted["pv_amount"])
        if wind_included:
            columns["Wind revenue (EUR)"].append(formatted["wind_amount"])
        columns["Pumped energy (MWh)"].append(formatted["pumped"].removesuffix(" MWh"))
        columns["Turbined energy (MWh)"].append(formatted["turbined"].removesuffix(" MWh"))
        columns["Full cycles"].append(formatted["cycles"])
        if pv_included:
            columns["PV self-consumed (MWh)"].append(formatted["pv_self"].removesuffix(" MWh"))
            columns["PV self-consumed (%)"].append(formatted["pv_self_share"])
            columns["PV exported (MWh)"].append(formatted["pv_export"].removesuffix(" MWh"))
            columns["PV curtailed (MWh)"].append(formatted["pv_curtail"].removesuffix(" MWh"))
        if wind_included:
            columns["Wind self-consumed (MWh)"].append(formatted["wind_self"].removesuffix(" MWh"))
            columns["Wind self-consumed (%)"].append(formatted["wind_self_share"])
            columns["Wind exported (MWh)"].append(formatted["wind_export"].removesuffix(" MWh"))
            columns["Wind curtailed (MWh)"].append(formatted["wind_curtail"].removesuffix(" MWh"))
        columns["Simultaneous intervals"].append(formatted["simul_n"])
        columns["Simultaneous overlap (MWh)"].append(formatted["simul_mwh"].removesuffix(" MWh"))
    return columns
