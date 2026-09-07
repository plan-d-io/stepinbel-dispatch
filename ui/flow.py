"""Pure Configure/Review session-state machine. No Streamlit, no core imports."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any, Mapping, MutableMapping

SESSION_KEY = "sib"
STATE_VERSION = 1
STAGE_CONFIGURE = 1
STAGE_REVIEW = 2
STAGE_RESULTS = 3
MAX_STAGE_THIS_SLICE = STAGE_RESULTS
RESULTS_VIEW_KEY = "results_view"
RESULTS_IDENTITY_KEY = "results_identity"
RESULT_TAB_OVERVIEW = "Overview"
RESULT_TAB_NAMES = (
    "Overview",
    "Market detail",
    "Data explorer",
    "Technical details",
    "Downloads",
)


def default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "stage": STAGE_CONFIGURE,
        "max_stage": STAGE_CONFIGURE,
        "demo": False,
        "form": None,
        "snapshot": None,
        "snapshot_fingerprint": None,
        "validation_error": None,
        "job": None,
        "result": None,
        "launch_error": None,
    }


def is_exact_int(value: object, expected: int) -> bool:
    return type(value) is int and value == expected


def state_is_compatible(state: Mapping[str, Any] | None) -> bool:
    return isinstance(state, dict) and is_exact_int(state.get("version"), STATE_VERSION)


def is_demo(state: Mapping[str, Any]) -> bool:
    return bool(state.get("demo"))


def current_stage(state: Mapping[str, Any]) -> int:
    try:
        stage = int(state.get("stage") or STAGE_CONFIGURE)
    except (TypeError, ValueError):
        return STAGE_CONFIGURE
    return min(max(stage, STAGE_CONFIGURE), STAGE_RESULTS)


def max_stage(state: Mapping[str, Any]) -> int:
    try:
        value = int(state.get("max_stage") or STAGE_CONFIGURE)
    except (TypeError, ValueError):
        return STAGE_CONFIGURE
    return min(max(value, STAGE_CONFIGURE), MAX_STAGE_THIS_SLICE)


def stored_form(state: Mapping[str, Any]) -> dict[str, Any] | None:
    form = state.get("form")
    return dict(form) if isinstance(form, Mapping) else None


def stored_snapshot(state: Mapping[str, Any]) -> dict[str, Any] | None:
    snapshot = state.get("snapshot")
    return dict(snapshot) if isinstance(snapshot, Mapping) else None


def set_form(state: MutableMapping[str, Any], form: Mapping[str, Any]) -> dict[str, Any]:
    state["form"] = dict(form)
    return dict(state)


def stored_job(state: Mapping[str, Any]) -> dict[str, Any] | None:
    job = state.get("job")
    return dict(job) if isinstance(job, Mapping) else None


def stored_result(state: Mapping[str, Any]) -> dict[str, Any] | None:
    result = state.get("result")
    return dict(result) if isinstance(result, Mapping) else None


def default_results_view(
    default_market: str | None = None,
    default_week: str | None = None,
    *,
    default_explorer_market: str | None = None,
    default_technical_market: str | None = None,
) -> dict[str, Any]:
    explorer = default_explorer_market if default_explorer_market is not None else default_market
    technical = default_technical_market if default_technical_market is not None else explorer
    return {
        "active_tab": RESULT_TAB_OVERVIEW,
        "selected_detail_market": default_market,
        "selected_explorer_market": explorer,
        "selected_explorer_week": default_week,
        "selected_technical_market": technical,
    }


def stored_results_view(state: Mapping[str, Any]) -> dict[str, Any] | None:
    view = state.get(RESULTS_VIEW_KEY)
    if not isinstance(view, Mapping):
        return None
    tab = view.get("active_tab")
    market = view.get("selected_detail_market")
    explorer_market = view.get("selected_explorer_market")
    explorer_week = view.get("selected_explorer_week")
    technical_market = view.get("selected_technical_market")
    if tab not in RESULT_TAB_NAMES:
        return None
    if market is not None and not isinstance(market, str):
        return None
    if explorer_market is not None and not isinstance(explorer_market, str):
        return None
    if explorer_week is not None and not isinstance(explorer_week, str):
        return None
    if technical_market is not None and not isinstance(technical_market, str):
        return None
    return {
        "active_tab": tab,
        "selected_detail_market": market,
        "selected_explorer_market": explorer_market,
        "selected_explorer_week": explorer_week,
        "selected_technical_market": technical_market,
    }


def result_open_identity(result: Mapping[str, Any]) -> str:
    return f"{result.get('source')}:{result.get('job_id')}:{result.get('output_directory')}"


def sync_results_view(
    state: MutableMapping[str, Any],
    *,
    result: Mapping[str, Any],
    default_market: str,
    allowed_markets: Sequence[str],
    default_week: str | None = None,
    allowed_weeks: Sequence[str] | None = None,
    default_explorer_market: str | None = None,
    default_technical_market: str | None = None,
) -> dict[str, Any]:
    identity = result_open_identity(result)
    explorer_default = (
        default_explorer_market if default_explorer_market is not None else default_market
    )
    technical_default = (
        default_technical_market if default_technical_market is not None else explorer_default
    )
    current = stored_results_view(state)
    if state.get(RESULTS_IDENTITY_KEY) != identity or current is None:
        view = default_results_view(
            default_market,
            default_week,
            default_explorer_market=explorer_default,
            default_technical_market=technical_default,
        )
        state[RESULTS_VIEW_KEY] = view
        state[RESULTS_IDENTITY_KEY] = identity
        return dict(view)
    tab = str(current["active_tab"])
    market = current["selected_detail_market"]
    if market not in allowed_markets:
        market = default_market
    explorer_market = current.get("selected_explorer_market")
    if explorer_market not in allowed_markets:
        explorer_market = explorer_default
    week = current.get("selected_explorer_week")
    if allowed_weeks is not None and week not in allowed_weeks:
        week = default_week
    technical_market = current.get("selected_technical_market")
    if technical_market not in allowed_markets:
        technical_market = technical_default
    view = {
        "active_tab": tab,
        "selected_detail_market": market,
        "selected_explorer_market": explorer_market,
        "selected_explorer_week": week,
        "selected_technical_market": technical_market,
    }
    state[RESULTS_VIEW_KEY] = view
    return dict(view)


def set_results_tab(state: MutableMapping[str, Any], tab: str) -> dict[str, Any]:
    if tab not in RESULT_TAB_NAMES:
        tab = RESULT_TAB_OVERVIEW
    view = stored_results_view(state) or default_results_view()
    view["active_tab"] = tab
    state[RESULTS_VIEW_KEY] = view
    return dict(view)


def set_detail_market(state: MutableMapping[str, Any], market: str) -> dict[str, Any]:
    view = stored_results_view(state) or default_results_view(market)
    view["selected_detail_market"] = market
    state[RESULTS_VIEW_KEY] = view
    return dict(view)


def set_explorer_market(state: MutableMapping[str, Any], market: str) -> dict[str, Any]:
    view = stored_results_view(state) or default_results_view(market)
    view["selected_explorer_market"] = market
    state[RESULTS_VIEW_KEY] = view
    return dict(view)


def set_explorer_week(state: MutableMapping[str, Any], week_id: str) -> dict[str, Any]:
    view = stored_results_view(state) or default_results_view()
    view["selected_explorer_week"] = week_id
    state[RESULTS_VIEW_KEY] = view
    return dict(view)


def set_technical_market(state: MutableMapping[str, Any], market: str) -> dict[str, Any]:
    view = stored_results_view(state) or default_results_view(market)
    view["selected_technical_market"] = market
    state[RESULTS_VIEW_KEY] = view
    return dict(view)


def clear_execution(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["job"] = None
    state["result"] = None
    state["launch_error"] = None
    state.pop(RESULTS_VIEW_KEY, None)
    state.pop(RESULTS_IDENTITY_KEY, None)
    return dict(state)


def clear_snapshot(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["snapshot"] = None
    state["snapshot_fingerprint"] = None
    state["validation_error"] = None
    clear_execution(state)
    if max_stage(state) > STAGE_CONFIGURE:
        state["max_stage"] = STAGE_CONFIGURE
    if current_stage(state) > STAGE_CONFIGURE:
        state["stage"] = STAGE_CONFIGURE
    return dict(state)


def apply_demo_change(state: MutableMapping[str, Any], demo: bool) -> dict[str, Any]:
    if bool(state.get("demo")) == bool(demo):
        return dict(state)
    state["demo"] = bool(demo)
    state["form"] = None
    return clear_snapshot(state)


def store_snapshot(
    state: MutableMapping[str, Any],
    snapshot: Mapping[str, Any],
    fingerprint: str,
) -> dict[str, Any]:
    state["snapshot"] = dict(snapshot)
    state["snapshot_fingerprint"] = fingerprint
    state["validation_error"] = None
    state["stage"] = STAGE_REVIEW
    state["max_stage"] = max(max_stage(state), STAGE_REVIEW)
    return dict(state)


def set_validation_error(state: MutableMapping[str, Any], message: str | None) -> dict[str, Any]:
    state["validation_error"] = message
    return dict(state)


def continue_to_review(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["stage"] = STAGE_REVIEW
    state["max_stage"] = max(max_stage(state), STAGE_REVIEW)
    return dict(state)


def mark_configure_restore(state: MutableMapping[str, Any]) -> None:
    state["restore_configure_widgets"] = True


def consume_configure_restore(state: MutableMapping[str, Any]) -> bool:
    return bool(state.pop("restore_configure_widgets", False))


def back_to_configure(state: MutableMapping[str, Any]) -> dict[str, Any]:
    state["stage"] = STAGE_CONFIGURE
    mark_configure_restore(state)
    return dict(state)


def navigate_to_stage(
    state: MutableMapping[str, Any],
    target: int,
    *,
    lock_navigation: bool = False,
) -> bool:
    try:
        step = int(target)
    except (TypeError, ValueError):
        return False
    if lock_navigation:
        return False
    if step == current_stage(state):
        return False
    if step < STAGE_CONFIGURE or step > max_stage(state):
        return False
    if step > MAX_STAGE_THIS_SLICE:
        return False
    if step == STAGE_CONFIGURE:
        mark_configure_restore(state)
    state["stage"] = step
    return True


def execution_locks_navigation(state: Mapping[str, Any]) -> bool:
    return stored_job(state) is not None


def apply_restored_job(
    state: MutableMapping[str, Any],
    *,
    job: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    state["job"] = dict(job)
    state["snapshot"] = dict(snapshot)
    form = snapshot.get("form")
    if isinstance(form, Mapping):
        state["form"] = dict(form)
    state["snapshot_fingerprint"] = snapshot.get("form_fingerprint")
    state["demo"] = False
    state["result"] = None
    state["launch_error"] = None
    state["validation_error"] = None
    return dict(state)


def return_to_review(state: MutableMapping[str, Any]) -> dict[str, Any]:
    clear_execution(state)
    state["stage"] = STAGE_REVIEW
    state["max_stage"] = STAGE_REVIEW
    return dict(state)


def configure_another_run(state: MutableMapping[str, Any]) -> dict[str, Any]:
    """Leave a completed result to edit another run. Does not touch disk."""
    clear_execution(state)
    state["stage"] = STAGE_CONFIGURE
    state["max_stage"] = STAGE_CONFIGURE
    mark_configure_restore(state)
    return dict(state)


def unlock_results(state: MutableMapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    previous = state.get("result") if isinstance(state.get("result"), Mapping) else None
    incoming = dict(result)
    state["result"] = incoming
    state["stage"] = STAGE_RESULTS
    state["max_stage"] = max(max_stage(state), STAGE_RESULTS)
    if previous is None or result_open_identity(previous) != result_open_identity(incoming):
        state[RESULTS_VIEW_KEY] = default_results_view()
        state[RESULTS_IDENTITY_KEY] = result_open_identity(incoming)
    return dict(state)


def require_json_compatible(value: Any, *, label: str = "session state") -> Any:
    """Reject non-JSON values and non-finite floats before they enter session state."""

    def _check(item: Any, path: str) -> None:
        if item is None or isinstance(item, (str, bool)):
            return
        if isinstance(item, int) and not isinstance(item, bool):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise TypeError(f"{label} {path} is not a finite float")
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise TypeError(f"{label} {path} has a non-string key")
                _check(child, f"{path}.{key}")
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                _check(child, f"{path}[{index}]")
            return
        raise TypeError(f"{label} {path} has a non-JSON value: {type(item)!r}")

    _check(value, "$")
    return json.loads(json.dumps(value, allow_nan=False))


def invalidate_if_form_changed(
    state: MutableMapping[str, Any],
    *,
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> bool:
    if previous is not None and dict(previous) == dict(current):
        return False
    if stored_snapshot(state) is None and max_stage(state) == STAGE_CONFIGURE:
        set_form(state, current)
        return False
    set_form(state, current)
    clear_snapshot(state)
    return True
