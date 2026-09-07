from __future__ import annotations

from ui.presentation.styles import stylesheet
from ui.presentation.tokens import (
    BORDER,
    ENERGENT_FOOTER_DISPLAY_HEIGHT_PX,
    ENERGENT_FOOTER_DISPLAY_WIDTH_PX,
    FPS_FOOTER_DISPLAY_HEIGHT_PX,
    FPS_FOOTER_DISPLAY_WIDTH_PX,
    NARROW_BREAKPOINT_PX,
    PAGE_BG,
    PRIMARY,
    PRIMARY_FOCUS,
    PRIMARY_HOVER,
    RADIUS_PX,
    SURFACE,
    TEXT,
    TEXT_MUTED,
    TEXT_SECONDARY,
    WIDE_WIDTH_PX,
)


def test_stylesheet_uses_central_tokens() -> None:
    css = stylesheet()
    for token in (
        PRIMARY,
        PRIMARY_HOVER,
        PRIMARY_FOCUS,
        PAGE_BG,
        SURFACE,
        TEXT,
        TEXT_SECONDARY,
        TEXT_MUTED,
        BORDER,
        str(RADIUS_PX),
        str(WIDE_WIDTH_PX),
        str(NARROW_BREAKPOINT_PX),
    ):
        assert token in css
    assert str(FPS_FOOTER_DISPLAY_HEIGHT_PX) in css
    assert str(FPS_FOOTER_DISPLAY_WIDTH_PX) in css
    assert str(ENERGENT_FOOTER_DISPLAY_HEIGHT_PX) in css
    assert str(ENERGENT_FOOTER_DISPLAY_WIDTH_PX) in css
    assert ".sib-glossary" in css
    assert ".sib-status-summary" in css
    assert ".sib-status-label" in css
    assert ".sib-status-value" in css
    assert "font-weight: 700" in css
    assert ".sib-footer-rule-wrap" in css
    assert ".sib-footer-block-energent" in css
    assert "justify-self: end" in css


def test_stylesheet_has_no_forbidden_patterns() -> None:
    css = stylesheet().lower()
    assert "linear-gradient" not in css
    assert "radial-gradient" not in css
    assert "gradient" not in css
    assert "nth-child" not in css
    assert "nth_child" not in css
    assert "<script" not in css
    assert "javascript:" not in css
    assert "<iframe" not in css
    assert "iframe" not in css
    assert "box-shadow" not in css
    assert "text-shadow" not in css
    assert "drop-shadow" not in css
    for line in css.splitlines():
        if "shadow" in line:
            raise AssertionError(f"shadow styling is forbidden: {line}")
