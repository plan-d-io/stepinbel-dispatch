"""Functional Technical details tab."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import streamlit as st

from ui.flow import SESSION_KEY, require_json_compatible, result_open_identity, set_technical_market
from ui.presentation.components import (
    render_config_cards,
    render_display_table,
    render_section_heading,
    render_status_panel,
)
from ui.presentation.tokens import (
    TECHNICAL_ADVANCED_HEADING,
    TECHNICAL_CHECKS_HEADING,
    TECHNICAL_CHECKS_HELP,
    TECHNICAL_CHECKS_HELP_TITLE,
    TECHNICAL_CONFIG_RECORD,
    TECHNICAL_CONFIGURED_HEADING,
    TECHNICAL_ERROR_BODY,
    TECHNICAL_ERROR_TITLE,
    TECHNICAL_EVENTS_LIMIT_NOTE,
    TECHNICAL_SOURCES_HEADING,
    TECHNICAL_TEXT_TRUNCATED,
)
from ui.services.launch import TEST_HOOKS
from ui.services.paths import MARKET_LABELS
from ui.services.result_technical import TechnicalError, load_technical_details


def _persist(state: Mapping[str, Any]) -> None:
    st.session_state[SESSION_KEY] = require_json_compatible(dict(state))


def _outputs_root() -> Path | None:
    hooked = TEST_HOOKS.get("outputs_root")
    return Path(hooked) if hooked is not None else None


def _pairs_table(rows: list[tuple[str, str]]) -> dict[str, list[str]]:
    return {"Item": [row[0] for row in rows], "Value": [row[1] for row in rows]}


def _event_table(payload: Mapping[str, Any]) -> dict[str, list[str]]:
    rows = list(payload["rows"])
    return {
        "UTC": [str(item["timestamp"]) for item in rows],
        "Stage": [str(item["stage"]) for item in rows],
        "State": [str(item["state"]) for item in rows],
        "Message": [str(item["message"]) for item in rows],
    }


def _render_text_block(title: str, payload: Mapping[str, Any] | None) -> None:
    if payload is None:
        return
    with st.expander(title, expanded=False):
        st.code(str(payload["text"]), language="text")
        if payload.get("truncated"):
            st.caption(TECHNICAL_TEXT_TRUNCATED)


def render_technical_details(
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
        preview = load_technical_details(
            result,
            market=str(view.get("selected_technical_market") or default_market),
            job=job,
            outputs_root=outputs_root,
        )
    except TechnicalError:
        render_status_panel("danger", TECHNICAL_ERROR_TITLE, TECHNICAL_ERROR_BODY)
        return
    markets = list(preview["markets"])
    selected = str(preview["selected_market"])
    if len(markets) > 1:
        labels = [MARKET_LABELS[item] for item in markets]
        chosen = st.selectbox(
            "Market",
            labels,
            index=labels.index(MARKET_LABELS[selected]),
            key=f"sib-technical-market-{identity}",
        )
        next_market = markets[labels.index(str(chosen))]
        if next_market != selected:
            set_technical_market(state, next_market)
            _persist(state)
            st.rerun()
            return
    payload = preview

    render_section_heading("Run information")
    render_display_table(_pairs_table(list(payload["run_information"])))
    if payload.get("child_information"):
        render_section_heading("Selected market run")
        render_display_table(_pairs_table(list(payload["child_information"])))

    render_section_heading(TECHNICAL_CONFIGURED_HEADING)
    render_config_cards(list(payload["configured_groups"]))
    with st.expander(TECHNICAL_ADVANCED_HEADING, expanded=False):
        advanced_rows: list[tuple[str, str]] = []
        for _title, rows in payload["advanced_groups"]:
            advanced_rows.extend(list(rows))
        render_display_table(_pairs_table(advanced_rows))
    with st.expander(TECHNICAL_CONFIG_RECORD, expanded=False):
        st.code(str(payload["configuration_record"]), language="json")

    render_section_heading("Software and model")
    render_display_table(_pairs_table(list(payload["software"])))

    checks = payload["solution_checks"]
    render_section_heading(TECHNICAL_CHECKS_HEADING)
    st.write(str(checks["label"]))
    render_display_table(
        {
            "Check": [row["Check"] for row in checks["rows"]],
            "Maximum residual": [row["Maximum residual"] for row in checks["rows"]],
        }
    )
    with st.expander(TECHNICAL_CHECKS_HELP_TITLE, expanded=False):
        st.write(TECHNICAL_CHECKS_HELP)

    sources = payload["data_sources"]
    render_section_heading(TECHNICAL_SOURCES_HEADING)
    render_display_table(
        _pairs_table(
            [
                ("Data vintage", str(sources["data_vintage"])),
                ("Pipeline version", str(sources["pipeline_version"])),
                ("Pipeline commit", str(sources["pipeline_commit"])),
                ("Published-data manifest SHA-256", str(sources["manifest_sha256"])),
                ("Required source tables", ", ".join(str(item) for item in sources["required_sources"])),
            ]
        )
    )
    render_display_table(
        {
            "Table": [row["Table"] for row in sources["rows"]],
            "Rows": [row["Rows"] for row in sources["rows"]],
            "Required": [row["Required"] for row in sources["rows"]],
            "Hashes": [row["Hashes"] for row in sources["rows"]],
        }
    )
    with st.expander("Complete table hashes", expanded=False):
        render_display_table(
            {
                "Table": [row["Table"] for row in sources["hashes"]],
                "Expected SHA-256": [row["Expected SHA-256"] for row in sources["hashes"]],
                "Actual SHA-256": [row["Actual SHA-256"] for row in sources["hashes"]],
            }
        )

    render_section_heading("Workflow history")
    if payload.get("parent_events") is not None:
        st.markdown("**Overall comparison**")
        parent_events = payload["parent_events"]
        render_display_table(_event_table(parent_events))
        if parent_events.get("limited"):
            st.caption(
                TECHNICAL_EVENTS_LIMIT_NOTE.format(
                    count=len(parent_events["rows"]),
                    total=parent_events["total"],
                )
            )
        st.markdown("**Selected market**")
    child_events = payload["child_events"]
    render_display_table(_event_table(child_events))
    if child_events.get("limited"):
        st.caption(
            TECHNICAL_EVENTS_LIMIT_NOTE.format(
                count=len(child_events["rows"]),
                total=child_events["total"],
            )
        )

    render_section_heading("Stored report and run log")
    if payload["comparison"]:
        _render_text_block("Overall report", payload.get("parent_report"))
        _render_text_block("Overall run log", payload.get("parent_log"))
        _render_text_block("Selected market report", payload.get("child_report"))
        _render_text_block("Selected market run log", payload.get("child_log"))
    else:
        _render_text_block("Report", payload.get("child_report"))
        _render_text_block("Run log", payload.get("child_log"))
