"""Terminal recovery views for failed, unexpected, incomplete, and launch errors."""

from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from ui.flow import SESSION_KEY, require_json_compatible, return_to_review
from ui.views.query import clear_job_query
from ui.presentation.components import render_action_row, render_page_header, render_status_panel
from ui.services.jobs import trusted_job_or_none
from ui.services.launch import TEST_HOOKS
from ui.services.status import (
    CLASS_FAILED,
    CLASS_INCOMPLETE,
    CLASS_LAUNCH_FAILED,
    CLASS_UNEXPECTED,
    CLASS_UNTRUSTED,
    CONSOLE_MAX_BYTES,
    CONSOLE_MAX_LINES,
    LOG_MAX_BYTES,
    LOG_MAX_LINES,
    diagnostic_paths,
    read_status_payload,
    safe_error_message,
    tail_text,
    trusted_status,
)

_PARTIAL = "Partial results are not opened."

_COPY = {
    CLASS_FAILED: (
        "Simulation failed",
        "The workflow stopped before a complete result was written.",
    ),
    CLASS_UNEXPECTED: (
        "The worker ended unexpectedly",
        "The worker stopped before a completed result was written.",
    ),
    CLASS_INCOMPLETE: (
        "Results could not be opened",
        "The completed run has incomplete or incompatible artifacts.",
    ),
    CLASS_LAUNCH_FAILED: (
        "The simulation could not be started",
        "The worker process did not start. No result was created.",
    ),
    CLASS_UNTRUSTED: (
        "The stored run record is not valid",
        "The stored run record is not valid.",
    ),
}


def render_recovery(state: dict[str, Any], klass: str) -> None:
    job = trusted_job_or_none(
        state.get("job") if isinstance(state.get("job"), Mapping) else None,
        outputs_root=TEST_HOOKS.get("outputs_root"),
    )
    raw = read_status_payload(job.get("output_directory") if job else None)
    status = trusted_status(job, raw) if job else None
    title, fallback = _COPY.get(klass, _COPY[CLASS_FAILED])
    if klass == CLASS_FAILED and status is not None:
        body = safe_error_message(status.get("error_message") or fallback)
    elif klass == CLASS_LAUNCH_FAILED:
        body = safe_error_message(state.get("launch_error") or fallback)
    elif klass == CLASS_UNTRUSTED:
        body = fallback
    else:
        body = fallback
    render_page_header("Stage 2 of 3", "Review & run")
    render_status_panel("danger", title, body)
    st.write(_PARTIAL)
    files = diagnostic_paths(job)
    log_text = tail_text(files.get("log"), max_lines=LOG_MAX_LINES, max_bytes=LOG_MAX_BYTES)
    console_text = tail_text(
        files.get("console"), max_lines=CONSOLE_MAX_LINES, max_bytes=CONSOLE_MAX_BYTES
    )
    with st.expander("Diagnostics", expanded=False):
        if log_text:
            st.code(log_text, language="text")
        elif console_text:
            st.code(console_text, language="text")
        else:
            st.caption("No diagnostic output is available.")
        if status and status.get("error_category"):
            st.caption(f"Error category: {status.get('error_category')}")
        stage = None if status is None else status.get("current_stage")
        if isinstance(stage, str) and stage:
            st.caption(f"Stage key: {stage}")
    events = render_action_row(
        back=None,
        primary="Return to Review",
        primary_disabled=False,
        key="sib-recovery-actions",
        interactive=True,
    )
    if events.primary:
        return_to_review(state)
        clear_job_query()
        st.session_state[SESSION_KEY] = require_json_compatible(dict(state))
        st.rerun()
