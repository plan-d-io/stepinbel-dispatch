from __future__ import annotations

import pytest

from ui.presentation.shell import (
    body_container_key,
    content_width_px,
    escape_html,
    frontend_version_label,
    identity_html,
    simulator_version_label,
    stage_button_label,
    step_control_disabled,
    step_items,
    step_status,
    validate_step_window,
)
from ui.presentation.tokens import (
    APP_TITLE,
    DEMO_MODE_LABEL,
    FORM_WIDTH_PX,
    RESULT_TABS,
    RUN_IN_PROGRESS_LABEL,
    STEPS,
    WIDE_WIDTH_PX,
)


def test_three_exact_stage_labels() -> None:
    assert STEPS == ("Configure", "Review & run", "Results")
    assert len(STEPS) == 3


def test_exact_result_tab_labels() -> None:
    assert RESULT_TABS == (
        "Overview",
        "Market detail",
        "Data explorer",
        "Technical details",
        "Downloads",
    )


def test_step_window_validation() -> None:
    validate_step_window(1, 1)
    validate_step_window(3, 3)
    with pytest.raises(ValueError):
        validate_step_window(0, 1)
    with pytest.raises(ValueError):
        step_items(1, 4)
    with pytest.raises(ValueError):
        step_items(4, 3)


def test_step_status_classification() -> None:
    items = step_items(current=2, max_available=2)
    assert [item.status for item in items] == ["complete", "current", "unavailable"]
    assert step_status(1, 2, 3) == "complete"
    assert step_status(2, 2, 3) == "current"
    assert step_status(3, 2, 3) == "unlocked"
    assert step_status(3, 2, 2) == "unavailable"
    clamped = step_items(current=3, max_available=1)
    assert [item.status for item in clamped] == ["current", "unavailable", "unavailable"]


def test_content_width_handling() -> None:
    assert content_width_px("form") == FORM_WIDTH_PX == 1120
    assert content_width_px("wide") == WIDE_WIDTH_PX == 1120
    assert body_container_key("form") == "sib-body-form"
    assert body_container_key("wide") == "sib-body-wide"
    with pytest.raises(ValueError):
        content_width_px("full")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        body_container_key("full")  # type: ignore[arg-type]


def test_html_escaping() -> None:
    raw = '<img src=x onerror="alert(1)">'
    escaped = escape_html(raw)
    assert "<img" not in escaped
    assert "&lt;img" in escaped
    assert "onerror" in escaped


def test_identity_html_escapes_demo_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ui.presentation.shell.DEMO_MODE_LABEL",
        '<img src=x onerror="alert(1)">',
    )
    markup = identity_html(demo=True)
    assert "<img" not in markup
    assert "&lt;img" in markup


def test_identity_shows_separate_version_lines() -> None:
    from stepinbel import __version__ as simulator_version

    from ui.version import UI_VERSION

    markup = identity_html(demo=False, running=False)
    sim = simulator_version_label()
    front = frontend_version_label()
    assert APP_TITLE == "PHS market dispatch"
    assert APP_TITLE in markup
    assert sim == f"Simulator {simulator_version}"
    assert front == f"Front-end {UI_VERSION}"
    assert sim in markup
    assert front in markup
    assert markup.index(sim) < markup.index(front)
    assert markup.count('class="sib-identity-version"') == 2
    assert " · " not in markup
    assert "pyproject.toml" not in markup


def test_quiet_demo_and_running_status() -> None:
    demo = identity_html(demo=True, running=False)
    running = identity_html(demo=False, running=True)
    both = identity_html(demo=True, running=True)
    assert DEMO_MODE_LABEL in demo
    assert RUN_IN_PROGRESS_LABEL in running
    assert DEMO_MODE_LABEL in both
    assert RUN_IN_PROGRESS_LABEL in both
    assert "sib-pill" not in demo
    assert "sib-pill" not in running


def test_stage_button_labels_keep_full_name() -> None:
    assert stage_button_label(1, "Configure") == "1  Configure"
    assert stage_button_label(2, "Review & run") == "2  Review & run"


def test_step_control_disabled_rules() -> None:
    items = step_items(current=2, max_available=2)
    current = items[1]
    complete = items[0]
    unavailable = items[2]
    assert step_control_disabled(current, interactive=True, lock_navigation=False) is True
    assert step_control_disabled(complete, interactive=True, lock_navigation=False) is False
    assert step_control_disabled(unavailable, interactive=True, lock_navigation=False) is True
    assert step_control_disabled(complete, interactive=False, lock_navigation=False) is True
    assert step_control_disabled(complete, interactive=True, lock_navigation=True) is True
