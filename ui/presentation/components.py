"""Reusable presentation components. No session, core, or artifact knowledge."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, NamedTuple

import streamlit as st
from plotly import graph_objects as go
from plotly.subplots import make_subplots

from ui.presentation.shell import escape_html
from ui.presentation.tokens import (
    BORDER,
    CHART_AFRR,
    CHART_AXIS,
    CHART_DA,
    CHART_GRID,
    CHART_LIMIT,
    CHART_MFRR,
    CHART_PAPER,
    CHART_PRICE_BUY,
    CHART_PRICE_SELL,
    CHART_PUMP,
    CHART_PV,
    CHART_PV_CURTAIL,
    CHART_PV_EXPORT,
    CHART_RESERVOIR,
    CHART_REVENUE,
    CHART_TURBINE,
    CHART_WIND,
    CHART_WIND_CURTAIL,
    CHART_WIND_EXPORT,
    PAGE_BG,
    PRIMARY,
    SURFACE,
    TEXT,
    TEXT_SECONDARY,
    StatusTone,
)

EXPLORER_PLOT_CONFIG: dict[str, Any] = {
    "displayModeBar": "hover",
    "displaylogo": False,
    "modeBarButtonsToRemove": (
        "toImage",
        "sendDataToCloud",
        "select2d",
        "lasso2d",
        "hoverClosestCartesian",
        "hoverCompareCartesian",
        "toggleSpikelines",
    ),
}

EXPLORER_HEADER_PX = 52
EXPLORER_PLOT_PX = 200
EXPLORER_PANEL_PX = EXPLORER_HEADER_PX + EXPLORER_PLOT_PX
EXPLORER_GAP_PX = 32
EXPLORER_GROUP_EXTRA_PX = 48
EXPLORER_MARGIN_T_PX = 12
EXPLORER_MARGIN_B_PX = 68
EXPLORER_MARGIN_L_PX = 72
EXPLORER_MARGIN_R_PX = 18

ChartKind = Literal["line", "bar"]

_SERIES_COLOURS = {
    "aFRR": CHART_AFRR,
    "Day-ahead": CHART_DA,
    "mFRR": CHART_MFRR,
    "Pump (MW)": CHART_PUMP,
    "Pumping": CHART_PUMP,
    "Turbine (MW)": CHART_TURBINE,
    "Turbine output (MW)": CHART_TURBINE,
    "Turbine output": CHART_TURBINE,
    "Generation": CHART_TURBINE,
    "Reservoir end (MWh)": CHART_RESERVOIR,
    "Reservoir level": CHART_RESERVOIR,
    "Sell (EUR/MWh)": CHART_PRICE_SELL,
    "Sell price": CHART_PRICE_SELL,
    "Buy (EUR/MWh)": CHART_PRICE_BUY,
    "Buy price": CHART_PRICE_BUY,
    "Interval revenue (EUR)": CHART_REVENUE,
    "Total interval revenue": CHART_REVENUE,
    "Available (MW)": CHART_PV,
    "Available PV": CHART_PV,
    "To pump (MW)": CHART_PUMP,
    "PV used for pumping": CHART_PUMP,
    "Export (MW)": CHART_PV_EXPORT,
    "PV export (MW)": CHART_PV_EXPORT,
    "PV export": CHART_PV_EXPORT,
    "PV exported": CHART_PV_EXPORT,
    "Curtail (MW)": CHART_PV_CURTAIL,
    "PV curtailed": CHART_PV_CURTAIL,
    "Grid import (MW)": CHART_GRID,
    "Grid import for pumping": CHART_GRID,
    "Upward commitment": CHART_TURBINE,
    "Downward commitment": CHART_PUMP,
    "Reservoir capacity": TEXT_SECONDARY,
    "Import limit": TEXT_SECONDARY,
    "Export limit": TEXT_SECONDARY,
    "Total site revenue": PRIMARY,
    "Capacity revenue": CHART_GRID,
    "Net energy revenue": CHART_REVENUE,
    "PV revenue": CHART_PV,
    "Wind revenue": CHART_WIND,
    "Available wind": CHART_WIND,
    "Wind used for pumping": CHART_PUMP,
    "Wind exported": CHART_WIND_EXPORT,
    "Wind export": CHART_WIND_EXPORT,
    "Wind curtailed": CHART_WIND_CURTAIL,
    "Revenue": PRIMARY,
}


def render_page_header(kicker: str, title: str, lead: str | None = None) -> None:
    with st.container(key="sib-page-header", horizontal=False):
        st.html(f'<div class="sib-kicker">{escape_html(kicker)}</div>')
        st.header(title)
        if lead:
            st.html(f'<p class="sib-page-lead">{escape_html(lead)}</p>')


def render_section_heading(title: str, caption: str | None = None) -> None:
    st.subheader(title)
    if caption:
        st.html(f'<p class="sib-section-lead">{escape_html(caption)}</p>')


def render_status_panel(tone: StatusTone, title: str, body: str) -> None:
    if tone == "success":
        st.success(f"**{title}**\n\n{body}")
    elif tone == "warning":
        st.warning(f"**{title}**\n\n{body}")
    elif tone == "danger":
        st.error(f"**{title}**\n\n{body}")
    else:
        st.info(f"**{title}**\n\n{body}")


def render_readouts(items: Sequence[tuple[str, str]]) -> None:
    cols = st.columns(len(items) or 1)
    for column, (label, value) in zip(cols, items, strict=True):
        column.html(
            '<div class="sib-readout">'
            f'<p class="sib-readout-label">{escape_html(label)}</p>'
            f'<p class="sib-readout-value">{escape_html(value)}</p>'
            "</div>"
        )


def render_config_cards(groups: Sequence[tuple[str, Sequence[tuple[str, str]]]]) -> None:
    cards: list[str] = []
    for title, rows in groups:
        body = "".join(
            '<div class="sib-config-row">'
            f'<span class="sib-readout-label">{escape_html(label)}</span>'
            f'<span class="sib-readout-value">{escape_html(value)}</span>'
            "</div>"
            for label, value in rows
        )
        cards.append(
            '<div class="sib-config-card">'
            f'<p class="sib-config-card-title">{escape_html(title)}</p>'
            f"{body}"
            "</div>"
        )
    st.html(f'<div class="sib-config-grid">{"".join(cards)}</div>')


def render_status_summary(
    items: Sequence[tuple[str, str]],
    *,
    key: str = "sib-status-summary",
) -> None:
    """Compact execution-status row. Do not use for large Results KPIs."""
    cells: list[str] = []
    for label, value in items:
        cells.append(
            '<div class="sib-status-item">'
            f'<p class="sib-status-label">{escape_html(label)}</p>'
            f'<p class="sib-status-value">{escape_html(value)}</p>'
            "</div>"
        )
    with st.container(key=key):
        st.html(f'<div class="sib-status-summary">{"".join(cells)}</div>')


def render_metric_group(
    items: Sequence[tuple[str, str]],
    *,
    key: str = "sib-metrics",
) -> None:
    with st.container(key=key):
        cols = st.columns(len(items) or 1)
        for column, (label, value) in zip(cols, items, strict=True):
            column.metric(label, value)


def render_market_highlight(
    *,
    title: str,
    total: str,
    energy: str,
    capacity: str | None = None,
    pv: str | None = None,
    wind: str | None = None,
    note: str | None = None,
) -> None:
    parts = [
        f'<p class="sib-highlight-title">{escape_html(title)}</p>',
        f'<p class="sib-highlight-total">{escape_html(total)}</p>',
        f'<p class="sib-caption">Total site revenue</p>',
        f'<p class="sib-readout-label">Net energy revenue</p>',
        f'<p class="sib-readout-value">{escape_html(energy)}</p>',
    ]
    if capacity is not None:
        parts.append('<p class="sib-readout-label">Capacity revenue</p>')
        parts.append(f'<p class="sib-readout-value">{escape_html(capacity)}</p>')
    if pv is not None:
        parts.append('<p class="sib-readout-label">PV revenue</p>')
        parts.append(f'<p class="sib-readout-value">{escape_html(pv)}</p>')
    if wind is not None:
        parts.append('<p class="sib-readout-label">Wind revenue</p>')
        parts.append(f'<p class="sib-readout-value">{escape_html(wind)}</p>')
    if note:
        parts.append(f'<p class="sib-highlight-note">{escape_html(note)}</p>')
    st.html(f'<div class="sib-highlight">{"".join(parts)}</div>')


def render_display_table(
    data: Mapping[str, Sequence[Any]] | Sequence[Any] | Any,
    *,
    hide_index: bool = True,
) -> None:
    st.table(data, width="stretch", height="content", hide_index=hide_index)


def render_table_frame(
    *,
    title: str,
    caption: str,
    data: Mapping[str, Sequence[Any]] | Any,
    hide_index: bool = True,
    key: str | None = None,
) -> None:
    frame_key = key or f"sib-table-{title}"
    with st.container(key=frame_key):
        st.markdown(f"**{title}**")
        render_display_table(data, hide_index=hide_index)
        st.caption(caption)


def _series_colour(name: str, index: int) -> str:
    if name in _SERIES_COLOURS:
        return _SERIES_COLOURS[name]
    palette = (PRIMARY, CHART_TURBINE, CHART_RESERVOIR, CHART_PV, CHART_GRID, TEXT)
    return palette[index % len(palette)]


def render_chart_frame(
    *,
    title: str,
    x_label: str,
    y_label: str,
    caption: str,
    categories: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
    kind: ChartKind = "line",
    y_zero: bool = True,
    reference_lines: Sequence[tuple[float, str]] = (),
    key: str | None = None,
) -> None:
    figure = go.Figure()
    for index, (name, values) in enumerate(series):
        colour = _series_colour(name, index)
        if kind == "bar":
            figure.add_bar(name=name, x=list(categories), y=list(values), marker_color=colour)
        else:
            figure.add_scatter(
                name=name,
                x=list(categories),
                y=list(values),
                mode="lines",
                line={"color": colour, "width": 2},
            )
    for value, label in reference_lines:
        figure.add_hline(
            y=value,
            line={"color": CHART_LIMIT, "width": 1, "dash": "dash"},
            annotation_text=label,
            annotation_font_color=CHART_AXIS,
        )
    figure.update_layout(
        paper_bgcolor=CHART_PAPER,
        plot_bgcolor=SURFACE,
        font={"color": TEXT, "size": 12},
        xaxis={
            "title": x_label,
            "color": CHART_AXIS,
            "gridcolor": BORDER,
            "zeroline": False,
        },
        yaxis={
            "title": y_label,
            "color": CHART_AXIS,
            "gridcolor": BORDER,
            "zeroline": False,
            "rangemode": "tozero" if y_zero else "normal",
        },
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 56, "r": 16, "t": 32, "b": 48},
        height=280,
    )
    frame_key = key or f"sib-chart-{title}"
    with st.container(key=frame_key):
        st.markdown(f"**{title}**")
        st.html(
            f'<p class="sib-axis-label">{escape_html(y_label)} versus {escape_html(x_label)}</p>'
        )
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(caption)


class ExplorerPanel(NamedTuple):
    group: str
    title: str
    y_label: str
    series: tuple[tuple[str, Sequence[float]], ...]
    reference_lines: tuple[tuple[float, str], ...] = ()
    y_zero: bool = True


def explorer_figure_key(identity: str, market: str, week_id: str) -> str:
    safe_identity = "".join(ch if ch.isalnum() else "-" for ch in identity)[:48]
    return f"sib-explorer-{safe_identity}-{market}-{week_id}"


def explorer_xaxis_name(row: int) -> str:
    return "xaxis" if row == 1 else f"xaxis{row}"


def explorer_legend_id(row: int) -> str:
    return "legend" if row == 1 else f"legend{row}"


def explorer_group_breaks(panels: Sequence[ExplorerPanel]) -> int:
    breaks = 0
    previous = None
    for panel in panels:
        if previous is not None and panel.group != previous:
            breaks += 1
        previous = panel.group
    return breaks


def explorer_figure_height(panel_count: int, group_breaks: int) -> int:
    gaps = max(panel_count - 1, 0)
    return (
        EXPLORER_MARGIN_T_PX
        + EXPLORER_MARGIN_B_PX
        + panel_count * EXPLORER_PANEL_PX
        + gaps * EXPLORER_GAP_PX
        + group_breaks * EXPLORER_GROUP_EXTRA_PX
    )


def _explorer_domains(
    panels: Sequence[ExplorerPanel],
) -> tuple[int, list[tuple[float, float]], list[float], list[tuple[int, str, float]]]:
    rows = list(panels)
    count = len(rows)
    extras = [0]
    for index, panel in enumerate(rows[1:], start=1):
        extras.append(EXPLORER_GROUP_EXTRA_PX if panel.group != rows[index - 1].group else 0)
    inner = (
        count * EXPLORER_PANEL_PX
        + max(count - 1, 0) * EXPLORER_GAP_PX
        + sum(extras)
    )
    height = EXPLORER_MARGIN_T_PX + EXPLORER_MARGIN_B_PX + inner
    y = 1.0
    domains: list[tuple[float, float]] = []
    title_ys: list[float] = []
    group_labels: list[tuple[int, str, float]] = []

    for row_number, panel in enumerate(rows, start=1):
        extra = extras[row_number - 1] / inner if inner else 0.0
        if extra:
            group_labels.append((row_number, panel.group, y - extra * 0.22))
            y -= extra
        header = EXPLORER_HEADER_PX / inner if inner else 0.0
        title_ys.append(y - header * 0.12)
        y -= header
        plot = EXPLORER_PLOT_PX / inner if inner else 0.0
        top = y
        y -= plot
        bottom = max(0.0, y)
        top = min(1.0, max(bottom, top))
        domains.append((bottom, top))
        if row_number < count:
            y -= EXPLORER_GAP_PX / inner if inner else 0.0
    return height, domains, title_ys, group_labels


def build_explorer_figure(
    *,
    x_index: Sequence[int],
    hover_labels: Sequence[str],
    tick_vals: Sequence[int],
    tick_text: Sequence[str],
    panels: Sequence[ExplorerPanel],
) -> go.Figure:
    from ui.presentation.tokens import EXPLORER_GROUP_MAIN, EXPLORER_X_TITLE

    rows = list(panels)
    if not rows:
        raise ValueError("panels")
    xs = list(x_index)
    hover = list(hover_labels)
    count = len(rows)
    last = xs[-1] if xs else 0
    height, domains, title_ys, group_labels = _explorer_domains(rows)
    figure = make_subplots(
        rows=count,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=min(0.02, 0.12 / max(count - 1, 1)),
        start_cell="top-left",
    )
    for row_number, panel in enumerate(rows, start=1):
        legend = explorer_legend_id(row_number)
        for index, (name, values) in enumerate(panel.series):
            if len(values) != len(xs):
                raise ValueError("series length")
            figure.add_scatter(
                name=name,
                x=xs,
                y=list(values),
                mode="lines",
                line={"color": _series_colour(name, index), "width": 1.6},
                customdata=hover,
                hovertemplate=("%{customdata}<br>" f"{name}: %{{y}}<extra></extra>"),
                legend=legend,
                showlegend=True,
                row=row_number,
                col=1,
            )
        for value, label in panel.reference_lines:
            figure.add_scatter(
                name=label,
                x=xs,
                y=[value] * len(xs),
                mode="lines",
                line={"color": TEXT_SECONDARY, "width": 1.2, "dash": "dash"},
                legend=legend,
                showlegend=True,
                hoverinfo="skip",
                row=row_number,
                col=1,
            )
        bottom, top = domains[row_number - 1]
        figure.update_yaxes(
            title_text=panel.y_label,
            color=CHART_AXIS,
            gridcolor=BORDER,
            zeroline=False,
            rangemode="tozero" if panel.y_zero else "normal",
            automargin=True,
            domain=[bottom, top],
            showline=True,
            mirror=True,
            linecolor=BORDER,
            linewidth=1,
            row=row_number,
            col=1,
        )
        figure.update_xaxes(
            matches="x",
            color=CHART_AXIS,
            gridcolor=BORDER,
            zeroline=False,
            tickmode="array",
            tickvals=list(tick_vals),
            ticktext=list(tick_text),
            range=[-0.5, last + 0.5],
            showticklabels=True,
            ticks="outside",
            title_text=EXPLORER_X_TITLE if row_number == count else "",
            automargin=True,
            domain=[0.0, 1.0],
            showline=True,
            mirror=True,
            linecolor=BORDER,
            linewidth=1,
            row=row_number,
            col=1,
        )
        figure.add_annotation(
            text=panel.title,
            x=0,
            y=title_ys[row_number - 1],
            xref="paper",
            yref="paper",
            xanchor="left",
            yanchor="top",
            showarrow=False,
            font={"size": 13, "color": TEXT},
            align="left",
        )
    for row_number, label, y_pos in group_labels:
        if label == EXPLORER_GROUP_MAIN:
            continue
        figure.add_annotation(
            text=f"<b>{label}</b>",
            x=0,
            y=y_pos,
            xref="paper",
            yref="paper",
            xanchor="left",
            yanchor="top",
            showarrow=False,
            font={"size": 15, "color": TEXT},
            align="left",
        )
    legend_layout: dict[str, Any] = {}
    for row_number, domain in enumerate(domains, start=1):
        _bottom, top = domain
        legend_layout[explorer_legend_id(row_number)] = {
            "orientation": "h",
            "x": 0,
            "xanchor": "left",
            "y": top,
            "yanchor": "bottom",
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 0,
            "font": {"size": 11, "color": TEXT},
            "itemsizing": "constant",
            "tracegroupgap": 0,
            "itemwidth": 30,
        }
    figure.update_layout(
        paper_bgcolor=PAGE_BG,
        plot_bgcolor=SURFACE,
        font={"color": TEXT, "size": 12},
        margin={
            "l": EXPLORER_MARGIN_L_PX,
            "r": EXPLORER_MARGIN_R_PX,
            "t": EXPLORER_MARGIN_T_PX,
            "b": EXPLORER_MARGIN_B_PX,
        },
        height=height,
        hovermode="x unified",
        dragmode="zoom",
        **legend_layout,
    )
    return figure


def render_explorer_figure(
    figure: go.Figure,
    *,
    key: str,
    captions: Sequence[str] = (),
) -> go.Figure:
    config = {
        "displayModeBar": EXPLORER_PLOT_CONFIG["displayModeBar"],
        "displaylogo": EXPLORER_PLOT_CONFIG["displaylogo"],
        "modeBarButtonsToRemove": list(EXPLORER_PLOT_CONFIG["modeBarButtonsToRemove"]),
    }
    st.plotly_chart(
        figure,
        width="stretch",
        config=config,
        key=key,
        on_select="ignore",
        theme=None,
    )
    for caption in captions:
        st.caption(caption)
    return figure


def render_result_tabs(active: str, *, interactive: bool = False) -> str | None:
    from ui.presentation.tokens import RESULT_TABS

    if not interactive:
        pills: list[str] = []
        for tab in RESULT_TABS:
            cls = "sib-pill sib-pill-active" if tab == active else "sib-pill"
            pills.append(f'<span class="{cls}">{escape_html(tab)}</span>')
        st.html(f'<div class="sib-tabs" role="tablist">{"".join(pills)}</div>')
        return None
    clicked: str | None = None
    with st.container(key="sib-result-tabs"):
        cols = st.columns(len(RESULT_TABS))
        for column, tab in zip(cols, RESULT_TABS, strict=True):
            with column:
                slug = tab.lower().replace(" ", "-")
                prefix = "sib-tab-active" if tab == active else "sib-tab"
                if st.button(
                    tab,
                    type="primary" if tab == active else "secondary",
                    width="stretch",
                    key=f"{prefix}-{slug}",
                ):
                    clicked = tab
    return clicked


def render_reserved_results_tab(title: str) -> None:
    from ui.presentation.tokens import RESERVED_TAB_BODY

    render_section_heading(title)
    st.write(RESERVED_TAB_BODY)


def render_expander(title: str, body: str, *, expanded: bool = False) -> None:
    with st.expander(title, expanded=expanded):
        st.write(body)


HOW_TO_READ_MULTI = (
    "The simulation uses historical prices and assumes perfect foresight: the optimiser knows all prices in the selected period when scheduling the asset.",
    "Each selected market is simulated separately. Revenue figures cannot be added together.",
    "The results describe the configured historical scenario. They are not a forecast or investment recommendation.",
)
HOW_TO_READ_ONE = (
    "The simulation uses historical prices and assumes perfect foresight: the optimiser knows all prices in the selected period when scheduling the asset.",
    "This result represents operation in one dedicated market.",
    "The results describe the configured historical scenario. They are not a forecast or investment recommendation.",
)
COLUMN_GLOSSARY: tuple[tuple[str, str], ...] = (
    ("Total site revenue", "Net energy revenue + capacity revenue + PV revenue."),
    (
        "Net energy revenue",
        "Revenue from generated electricity sold to the market, minus the cost of grid electricity used for pumping. Capacity revenue and direct PV-export revenue are reported separately.",
    ),
    ("Capacity revenue", "Payments for committed reserve capacity; not applicable to Day-ahead."),
    ("PV revenue", "Revenue from PV exported directly to the market."),
    ("Pumped energy", "Total energy consumed by pumping."),
    ("Turbined energy", "Total electrical energy generated."),
    ("Full cycles", "Turbined energy divided by maximum reservoir energy."),
    ("PV self-consumed", "PV used directly for pumping."),
    ("PV self-consumed (%)", "Share of available PV energy used directly for pumping."),
    ("PV exported", "PV delivered to the grid."),
    ("PV curtailed", "Available PV that could not be used or exported."),
)


def column_glossary_entries(
    *,
    pv_included: bool = True,
    wind_included: bool = False,
) -> tuple[tuple[str, str], ...]:
    from ui.presentation.tokens import SIMULTANEOUS_DIAGNOSTIC

    revenue_parts = ["Net energy revenue", "capacity revenue"]
    if pv_included:
        revenue_parts.append("PV revenue")
    if wind_included:
        revenue_parts.append("wind revenue")
    total = " + ".join(revenue_parts) + "."
    extras: list[str] = ["Capacity revenue"]
    if pv_included:
        extras.append("direct PV-export revenue")
    if wind_included:
        extras.append("direct wind-export revenue")
    if pv_included and wind_included:
        extra_text = "Capacity revenue and direct PV-export and wind-export revenue are reported separately."
    elif len(extras) == 1:
        extra_text = "Capacity revenue is reported separately."
    else:
        extra_text = f"{extras[0]} and {extras[1]} are reported separately."
    energy = (
        "Revenue from generated electricity sold to the market, minus the cost of grid electricity used for pumping. "
        + extra_text
    )
    entries: list[tuple[str, str]] = [
        ("Total site revenue", total),
        ("Net energy revenue", energy),
        ("Capacity revenue", "Payments for committed reserve capacity; not applicable to Day-ahead."),
    ]
    if pv_included:
        entries.append(("PV revenue", "Revenue from PV exported directly to the market."))
    if wind_included:
        entries.append(("Wind revenue", "Revenue from wind exported directly to the market."))
    entries.extend(
        [
            ("Pumped energy", "Total energy consumed by pumping."),
            ("Turbined energy", "Total electrical energy generated."),
            ("Full cycles", "Turbined energy divided by maximum reservoir energy."),
        ]
    )
    if pv_included:
        entries.extend(
            [
                ("PV self-consumed", "PV used directly for pumping."),
                ("PV self-consumed (%)", "Share of available PV energy used directly for pumping."),
            ]
        )
    if wind_included:
        entries.extend(
            [
                ("Wind self-consumed", "Wind used directly for pumping."),
                ("Wind self-consumed (%)", "Share of available wind energy used directly for pumping."),
            ]
        )
    if pv_included:
        entries.extend(
            [
                ("PV exported", "PV delivered to the grid."),
                ("PV curtailed", "Available PV that could not be used or exported."),
            ]
        )
    if wind_included:
        entries.extend(
            [
                ("Wind exported", "Wind delivered to the grid."),
                ("Wind curtailed", "Available wind that could not be used or exported."),
            ]
        )
    entries.append(("Simultaneous operation", SIMULTANEOUS_DIAGNOSTIC))
    return tuple(entries)


def column_glossary_html(
    *,
    pv_included: bool = True,
    wind_included: bool = False,
) -> str:
    rows: list[str] = []
    for name, explanation in column_glossary_entries(
        pv_included=pv_included, wind_included=wind_included
    ):
        rows.append(
            '<div class="sib-glossary-row">'
            f"<dt><strong>{escape_html(name)}</strong></dt>"
            f"<dd>{escape_html(explanation)}</dd>"
            "</div>"
        )
    return f'<dl class="sib-glossary">{"".join(rows)}</dl>'


def render_how_to_read_results(*, one_market: bool = False) -> None:
    from ui.presentation.tokens import HOW_TO_READ_TITLE

    lines = HOW_TO_READ_ONE if one_market else HOW_TO_READ_MULTI
    with st.expander(HOW_TO_READ_TITLE, expanded=False):
        for line in lines:
            st.write(line)


def render_column_glossary(*, pv_included: bool = True, wind_included: bool = False) -> None:
    from ui.presentation.tokens import COLUMN_GLOSSARY_TITLE

    with st.expander(COLUMN_GLOSSARY_TITLE, expanded=False):
        st.html(column_glossary_html(pv_included=pv_included, wind_included=wind_included))


def render_stage_progress(*, completed: int, total: int) -> None:
    ratio = 0.0 if total <= 0 else min(max(completed / total, 0.0), 1.0)
    percent = f"{ratio * 100:.1f}"
    st.html(
        f'<div class="sib-progress" role="progressbar" aria-valuemin="0" '
        f'aria-valuemax="{total}" aria-valuenow="{completed}" '
        f'aria-label="Workflow stage {completed} of {total}">'
        f'<div class="sib-progress-fill" style="width: {percent}%"></div>'
        "</div>"
    )


class ActionRowEvent(NamedTuple):
    back: bool
    primary: bool


def action_row_alignment(*, has_back: bool) -> str:
    return "distribute" if has_back else "right"


def continue_reason_text(reason: str | None, *, primary_disabled: bool) -> str | None:
    if not primary_disabled or not reason:
        return None
    return f"To continue: {reason}"


def render_action_row(
    *,
    primary: str,
    back: str | None = None,
    primary_disabled: bool = True,
    caption: str | None = None,
    disabled_reason: str | None = None,
    key: str = "sib-actions",
    interactive: bool = False,
) -> ActionRowEvent:
    shown_reason = continue_reason_text(
        disabled_reason,
        primary_disabled=(not interactive) or primary_disabled,
    )
    with st.container(key="sib-action-row"):
        if shown_reason:
            st.html(
                f'<p class="sib-continue-reason" role="status">{escape_html(shown_reason)}</p>'
            )
        row = st.container(
            horizontal=True,
            horizontal_alignment=action_row_alignment(has_back=bool(back)),
        )
        with row:
            back_clicked = False
            if back:
                back_clicked = bool(
                    st.button(
                        back,
                        type="secondary",
                        width="content",
                        disabled=not interactive,
                        key=f"{key}-back",
                    )
                )
            primary_clicked = bool(
                st.button(
                    primary,
                    type="primary",
                    disabled=(not interactive) or primary_disabled,
                    width="content",
                    key=f"{key}-primary",
                )
            )
        if caption:
            st.caption(caption)
    return ActionRowEvent(back=back_clicked, primary=primary_clicked)


def render_empty_state(message: str) -> None:
    st.html(f'<div class="sib-empty">{escape_html(message)}</div>')


def render_loading_state(message: str) -> None:
    render_status_panel("info", "Working", message)


def render_blocked_state(title: str, body: str) -> None:
    render_status_panel("danger", title, body)
