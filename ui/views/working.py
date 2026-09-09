"""Stage 2 working/progress view for an active detached job."""

from __future__ import annotations

import os
from typing import Any, Mapping

import streamlit as st

from ui.presentation.components import (
    render_page_header,
    render_section_heading,
    render_stage_progress,
    render_status_panel,
    render_status_summary,
)
from ui.services.jobs import trusted_job_or_none
from ui.services.launch import TEST_HOOKS
from ui.services.commitment import WORKING_COMMITMENT_NOTICE, snapshot_machine_commitment_enabled
from ui.services.snapshot import market_labels
from ui.services.status import (
    CLASS_QUEUED,
    CLASS_RUNNING,
    CLASS_VALIDATING,
    CONSOLE_MAX_BYTES,
    CONSOLE_MAX_LINES,
    LOG_MAX_BYTES,
    LOG_MAX_LINES,
    diagnostic_paths,
    format_elapsed,
    friendly_stage_label,
    last_complete_event,
    live_elapsed_seconds,
    progress_from_event,
    read_status_payload,
    tail_text,
    trusted_status,
)

_STAGE_CAPTION = "The bar follows workflow stages and is not solver percentage."
_REFRESH_CAPTION = "Refreshing the browser reconnects to the same run."
_STATE_LABELS = {
    CLASS_QUEUED: "Queued",
    CLASS_RUNNING: "Running",
    CLASS_VALIDATING: "Validating results",
}


def _period_text(snapshot: Mapping[str, Any] | None) -> str:
    if not isinstance(snapshot, Mapping):
        return "—"
    period = snapshot.get("period") if isinstance(snapshot.get("period"), Mapping) else {}
    start = period.get("start_date") or "—"
    end = period.get("end_date") or "—"
    return f"Belgian delivery {start} to {end}"


def render_working(state: dict[str, Any], klass: str) -> None:
    job = trusted_job_or_none(state.get("job") if isinstance(state.get("job"), Mapping) else None, outputs_root=TEST_HOOKS.get("outputs_root")) or {}
    snapshot = state.get("snapshot") if isinstance(state.get("snapshot"), Mapping) else {}
    raw = read_status_payload(job.get("output_directory")) if job else None
    status = trusted_status(job, raw) if job else None
    event = last_complete_event(job.get("output_directory"), run_id=str(job.get("job_id") or "")) if job else None
    stage_key = None if event is None else event.get("stage_key")
    if stage_key is None and status is not None:
        current = status.get("current_stage")
        stage_key = current if isinstance(current, str) else None
    pair = progress_from_event(event)
    elapsed = format_elapsed(
        live_elapsed_seconds(status, launched_at_utc=str(job.get("launch_utc") or ""))
    )
    markets = list(job.get("markets") or snapshot.get("markets") or [])
    label = _STATE_LABELS.get(klass, "Working")
    if klass == CLASS_QUEUED:
        body = "Waiting for the worker to start."
    elif klass == CLASS_VALIDATING:
        body = "The worker finished. Stored artifacts are being validated."
    else:
        body = "The worker is in progress."
    render_page_header("Stage 2 of 3", "Review & run", "Simulation run in progress.")
    render_status_panel("warning", label, body)
    if snapshot_machine_commitment_enabled(snapshot if isinstance(snapshot, Mapping) else {}):
        st.caption(WORKING_COMMITMENT_NOTICE)
    render_status_summary(
        (
            ("State", label),
            ("Workflow stage", friendly_stage_label(stage_key)),
            (
                "Stage",
                "—" if pair is None else f"{event['stage_number'] if event else '—'} of {pair[1]}",
            ),
            ("Elapsed", elapsed),
        ),
        key="sib-working-status",
    )
    render_section_heading("Selected markets and period")
    st.write(f"{market_labels(markets) or '—'} · {_period_text(snapshot)}")
    if pair is not None:
        completed, total = pair
        render_stage_progress(completed=completed, total=total)
        st.caption(_STAGE_CAPTION)
    st.caption(_REFRESH_CAPTION)
    files = diagnostic_paths(job)
    log_text = tail_text(files.get("log"), max_lines=LOG_MAX_LINES, max_bytes=LOG_MAX_BYTES)
    console_text = tail_text(
        files.get("console"), max_lines=CONSOLE_MAX_LINES, max_bytes=CONSOLE_MAX_BYTES
    )
    with st.expander("Run log", expanded=False):
        if log_text:
            st.code(log_text, language="text")
        elif console_text:
            st.caption("run.log is not available yet. Worker console output:")
            st.code(console_text, language="text")
        else:
            st.caption("No log lines yet.")
        if stage_key:
            st.caption(f"Stage key: {stage_key}")
    if log_text and console_text:
        with st.expander("Worker console", expanded=False):
            st.code(console_text, language="text")


def poll_working_enabled() -> bool:
    return os.environ.get("PYTEST_CURRENT_TEST") is None
