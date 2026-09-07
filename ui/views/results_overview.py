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
                note=_highlight_note(row, one_market=one_market),
            )
    render_section_heading("Operational results" if one_market else "Operational comparison")
    render_column_glossary()
    with st.container(key="sib-overview-table"):
        st.html('<div class="sib-table-numeric"></div>')
        render_display_table(_overview_table(rows, one_market=one_market))
        st.caption(RESULTS_TABLE_CAPTION)


def _overview_table(rows: list[Mapping[str, Any]], *, one_market: bool) -> dict[str, list[str]]:
    _ = one_market
    columns: dict[str, list[str]] = {
        "Market": [],
        "Total site revenue (EUR)": [],
        "Net energy revenue (EUR)": [],
        "Capacity revenue (EUR)": [],
        "PV revenue (EUR)": [],
        "Pumped energy (MWh)": [],
        "Turbined energy (MWh)": [],
        "Full cycles": [],
        "PV self-consumed (MWh)": [],
        "PV self-consumed (%)": [],
        "PV exported (MWh)": [],
        "PV curtailed (MWh)": [],
        "Simultaneous intervals": [],
        "Simultaneous overlap (MWh)": [],
    }
    for row in rows:
        formatted = row["formatted"]
        columns["Market"].append(formatted["market"])
        columns["Total site revenue (EUR)"].append(formatted["total_amount"])
        columns["Net energy revenue (EUR)"].append(formatted["energy_amount"])
        columns["Capacity revenue (EUR)"].append(formatted["capacity_amount"])
        columns["PV revenue (EUR)"].append(formatted["pv_amount"])
        columns["Pumped energy (MWh)"].append(formatted["pumped"].removesuffix(" MWh"))
        columns["Turbined energy (MWh)"].append(formatted["turbined"].removesuffix(" MWh"))
        columns["Full cycles"].append(formatted["cycles"])
        columns["PV self-consumed (MWh)"].append(formatted["pv_self"].removesuffix(" MWh"))
        columns["PV self-consumed (%)"].append(formatted["pv_self_share"])
        columns["PV exported (MWh)"].append(formatted["pv_export"].removesuffix(" MWh"))
        columns["PV curtailed (MWh)"].append(formatted["pv_curtail"].removesuffix(" MWh"))
        columns["Simultaneous intervals"].append(formatted["simul_n"])
        columns["Simultaneous overlap (MWh)"].append(formatted["simul_mwh"].removesuffix(" MWh"))
    return columns
