"""Application shell: identity, presentational stepper, content width."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Iterator

import streamlit as st
from stepinbel import __version__ as SIMULATOR_VERSION

from ui.presentation.tokens import (
    APP_TITLE,
    DEMO_MODE_LABEL,
    FORM_WIDTH_PX,
    PAGE_BG,
    RUN_IN_PROGRESS_LABEL,
    STEPINBEL_LOGO_NAME,
    STEPINBEL_LOGO_WIDTH_PX,
    STEPS,
    WIDE_WIDTH_PX,
    StepStatus,
    WidthVariant,
)
from ui.version import UI_VERSION

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_LOGO_PATH = _ASSETS / STEPINBEL_LOGO_NAME


@dataclass(frozen=True)
class StepItem:
    number: int
    name: str
    label: str
    status: StepStatus


def validate_step_window(current: int, max_available: int) -> None:
    if current not in range(1, len(STEPS) + 1):
        raise ValueError(f"current step must be 1–{len(STEPS)}, got {current}")
    if max_available not in range(1, len(STEPS) + 1):
        raise ValueError(
            f"max_available must be 1–{len(STEPS)}, got {max_available}"
        )


def step_label(number: int) -> str:
    return STEPS[number - 1]


def step_status(number: int, current: int, max_available: int) -> StepStatus:
    if number > max_available:
        return "unavailable"
    if number == current:
        return "current"
    if number < current:
        return "complete"
    return "unlocked"


def step_items(current: int, max_available: int) -> list[StepItem]:
    validate_step_window(current, max_available)
    shown_current = min(current, max_available)
    items: list[StepItem] = []
    for index, name in enumerate(STEPS, start=1):
        items.append(
            StepItem(
                number=index,
                name=name,
                label=step_label(index),
                status=step_status(index, shown_current, max_available),
            )
        )
    return items


def content_width_px(width: WidthVariant) -> int:
    if width == "form":
        return FORM_WIDTH_PX
    if width == "wide":
        return WIDE_WIDTH_PX
    raise ValueError(f"width must be 'form' or 'wide', got {width!r}")


def body_container_key(width: WidthVariant) -> str:
    if width == "form":
        return "sib-body-form"
    if width == "wide":
        return "sib-body-wide"
    raise ValueError(f"width must be 'form' or 'wide', got {width!r}")


def escape_html(text: str) -> str:
    return escape(text, quote=True)


def logo_path() -> Path:
    return _LOGO_PATH


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.removeprefix("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def composite_asset_bytes(path: Path, *, crop: bool = False, pad: int = 0) -> bytes:
    """Composite a logo onto the page background for display only.

    Source files are not rewritten. Optional crop uses the opaque bounding
    box and optional padding is applied around that crop.
    """
    from io import BytesIO

    from PIL import Image

    logo = Image.open(path).convert("RGBA")
    if crop:
        bbox = logo.getbbox()
        if bbox is not None:
            logo = logo.crop(bbox)
    if pad:
        padded = Image.new(
            "RGBA",
            (logo.width + pad * 2, logo.height + pad * 2),
            (0, 0, 0, 0),
        )
        padded.paste(logo, (pad, pad), logo)
        logo = padded
    background = Image.new("RGBA", logo.size, (*_hex_to_rgb(PAGE_BG), 255))
    composed = Image.alpha_composite(background, logo)
    buffer = BytesIO()
    composed.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def header_logo_bytes() -> bytes:
    """Composite the trimmed StepInBel PNG without cropping or padding."""
    return composite_asset_bytes(_LOGO_PATH, crop=False, pad=0)


@st.cache_data
def logo_image_bytes() -> bytes:
    """Return the StepInBel PNG composited for display, at intrinsic 213 × 80."""
    return header_logo_bytes()


def header_logo_size() -> tuple[int, int]:
    from io import BytesIO

    from PIL import Image

    return Image.open(BytesIO(logo_image_bytes())).size


def simulator_version_label(*, simulator: str | None = None) -> str:
    sim = SIMULATOR_VERSION if simulator is None else simulator
    return f"Simulator {sim}"


def frontend_version_label(*, frontend: str | None = None) -> str:
    fe = UI_VERSION if frontend is None else frontend
    return f"Front-end {fe}"


def _quiet_status(label: str, *, running: bool = False) -> str:
    dot = "sib-quiet-dot sib-quiet-dot-running" if running else "sib-quiet-dot"
    return (
        '<span class="sib-quiet-status" aria-label="'
        + escape_html(label)
        + '">'
        f'<span class="{dot}" aria-hidden="true"></span>'
        f"{escape_html(label)}"
        "</span>"
    )


def identity_html(*, demo: bool = False, running: bool = False) -> str:
    extras: list[str] = []
    if demo:
        extras.append(_quiet_status(DEMO_MODE_LABEL))
    if running:
        extras.append(_quiet_status(RUN_IN_PROGRESS_LABEL, running=True))
    extra = ""
    if extras:
        extra = f'<span class="sib-identity-status">{"".join(extras)}</span>'
    return (
        '<div class="sib-identity">'
        '<div class="sib-identity-stack">'
        f'<span class="sib-identity-name">{escape_html(APP_TITLE)}</span>'
        '<span class="sib-identity-versions">'
        f'<span class="sib-identity-version">{escape_html(simulator_version_label())}</span>'
        f'<span class="sib-identity-version">{escape_html(frontend_version_label())}</span>'
        "</span>"
        "</div>"
        f"{extra}"
        "</div>"
    )


def stage_button_label(number: int, label: str) -> str:
    return f"{number}  {label}"


def step_control_disabled(
    item: StepItem,
    *,
    interactive: bool,
    lock_navigation: bool,
) -> bool:
    if not interactive or lock_navigation:
        return True
    return item.status in {"current", "unavailable"}


def render_stepper(
    current: int,
    max_available: int,
    *,
    lock_navigation: bool = False,
    interactive: bool = False,
) -> int | None:
    """Render stage controls. Returns the clicked unlocked stage, if any."""
    clicked: int | None = None
    with st.container(
        key="sib-stepper",
        horizontal=True,
        gap="small",
        horizontal_alignment="left",
    ):
        for item in step_items(current, max_available):
            if _render_step_control(
                item,
                interactive=interactive,
                lock_navigation=lock_navigation,
            ):
                clicked = item.number
    return clicked


def _render_step_control(
    item: StepItem,
    *,
    interactive: bool,
    lock_navigation: bool,
) -> bool:
    label = stage_button_label(item.number, item.label)
    with st.container(key=f"sib-step-{item.status}-{item.number}"):
        return bool(
            st.button(
                label,
                type="secondary",
                width="content",
                help=item.label,
                disabled=step_control_disabled(
                    item,
                    interactive=interactive,
                    lock_navigation=lock_navigation,
                ),
                key=f"sib-nav-step-{item.number}",
            )
        )


def render_identity(*, demo: bool = False, running: bool = False) -> None:
    with st.container(key="sib-identity-row", horizontal=False):
        row = st.container(horizontal=True, vertical_alignment="center", gap="small")
        with row:
            st.image(logo_image_bytes(), width=STEPINBEL_LOGO_WIDTH_PX)
            st.html(identity_html(demo=demo, running=running))


@contextmanager
def app_shell(
    *,
    current_step: int,
    max_available: int,
    width: WidthVariant,
    demo: bool = False,
    running: bool = False,
    lock_navigation: bool = False,
    interactive: bool = False,
) -> Iterator[int | None]:
    """Product chrome. Yields a clicked unlocked stage when interactive."""
    validate_step_window(current_step, max_available)
    locked = lock_navigation or running
    with st.container(key="sib-shell"):
        with st.container(key=body_container_key(width)):
            with st.container(key="sib-chrome", horizontal=False, gap=None):
                render_identity(demo=demo, running=running)
                clicked = render_stepper(
                    current_step,
                    max_available,
                    lock_navigation=locked,
                    interactive=interactive,
                )
            st.markdown('<hr class="sib-rule" />', unsafe_allow_html=True)
            yield clicked
