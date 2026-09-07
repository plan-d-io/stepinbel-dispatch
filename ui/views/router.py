"""Product router for Configure, Review, working, recovery, and Results-ready."""

from __future__ import annotations

from typing import Any

import streamlit as st

from ui.flow import (
    SESSION_KEY,
    STAGE_CONFIGURE,
    STAGE_RESULTS,
    STAGE_REVIEW,
    configure_another_run,
    current_stage,
    default_state,
    execution_locks_navigation,
    is_demo,
    max_stage,
    navigate_to_stage,
    require_json_compatible,
    state_is_compatible,
    stored_result,
)
from ui.presentation.shell import app_shell
from ui.services.artifacts import result_is_valid
from ui.services.launch import TEST_HOOKS
from ui.services.paths import JOB_QUERY_KEY
from ui.services.reconnect import apply_query_reconnect
from ui.services.status import (
    classification_is_active,
    classification_is_recovery,
    reconcile_execution,
)
from ui.views.configure import render_configure
from ui.views.query import clear_job_query
from ui.views.recovery import render_recovery
from ui.views.results import render_results
from ui.views.review import render_review
from ui.views.working import poll_working_enabled, render_working


def bind_state() -> dict[str, Any]:
    raw = st.session_state.get(SESSION_KEY)
    if not state_is_compatible(raw):
        st.session_state[SESSION_KEY] = default_state()
    state = dict(st.session_state[SESSION_KEY])
    st.session_state[SESSION_KEY] = require_json_compatible(state)
    return st.session_state[SESSION_KEY]


def persist(state: dict[str, Any]) -> None:
    st.session_state[SESSION_KEY] = require_json_compatible(dict(state))


def _query_job_values() -> list[str]:
    try:
        return list(st.query_params.get_all(JOB_QUERY_KEY))
    except Exception:
        return []


def _apply_reconnect(state: dict[str, Any]) -> None:
    values = _query_job_values()
    if not values:
        return
    outcome = apply_query_reconnect(
        state,
        values,
        outputs_root=TEST_HOOKS.get("outputs_root"),
    )
    if not outcome.get("ok") and outcome.get("error"):
        state["launch_error"] = str(outcome["error"])


def render_app() -> None:
    state = bind_state()
    _apply_reconnect(state)
    persist(state)
    outputs_root = TEST_HOOKS.get("outputs_root")
    klass = None
    if state.get("job") is not None:
        klass = reconcile_execution(state, outputs_root=outputs_root)
        persist(state)
    result = stored_result(state)
    ready = result_is_valid(result, job=state.get("job") if isinstance(state.get("job"), dict) else None, outputs_root=outputs_root)
    active = klass is not None and classification_is_active(klass)
    recovery = klass is not None and classification_is_recovery(klass)
    lock = execution_locks_navigation(state) and not ready
    running = active
    stage = current_stage(state)
    available = max_stage(state)
    with app_shell(
        current_step=stage,
        max_available=available,
        width="wide" if ready and stage == STAGE_RESULTS else "form",
        demo=is_demo(state),
        running=running,
        lock_navigation=lock,
        interactive=True,
    ) as clicked:
        if (
            clicked == STAGE_CONFIGURE
            and current_stage(state) == STAGE_RESULTS
            and ready
            and not active
        ):
            configure_another_run(state)
            clear_job_query()
            persist(state)
            st.rerun()
        if clicked is not None and navigate_to_stage(state, clicked, lock_navigation=lock):
            persist(state)
            st.rerun()
        if ready and stage == STAGE_RESULTS:
            render_results(state)
            return
        if active:
            if poll_working_enabled():

                @st.fragment(run_every=1)
                def _poll() -> None:
                    current = reconcile_execution(state, outputs_root=outputs_root)
                    persist(state)
                    if classification_is_active(current):
                        render_working(state, current)
                        return
                    st.rerun()

                _poll()
            else:
                render_working(state, klass)
            return
        if recovery:
            render_recovery(state, klass)
            return
        if stage == STAGE_REVIEW:
            render_review(state)
            return
        if stage == STAGE_RESULTS and ready:
            render_results(state)
            return
        render_configure(state)
