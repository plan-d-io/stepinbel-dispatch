"""Global attribution footer.

StepInBel is an Energent project, supported by the FPS Economy.
Personal credit belongs in AUTHORS.md and project metadata, not on this page.
"""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

from ui.presentation.shell import composite_asset_bytes, escape_html
from ui.presentation.tokens import (
    ENERGENT_FOOTER_DISPLAY_HEIGHT_PX,
    ENERGENT_FOOTER_DISPLAY_WIDTH_PX,
    ENERGENT_LOGO_NAME,
    FOOTER_ENERGENT_CAPTION,
    FOOTER_FPS_CAPTION,
    FPS_FOOTER_DISPLAY_HEIGHT_PX,
    FPS_FOOTER_DISPLAY_WIDTH_PX,
    FPS_LOGO_NAME,
)

FOOTER_KEY = "app-footer"
_ASSETS = Path(__file__).resolve().parent.parent / "assets"
FPS_ASSET = _ASSETS / FPS_LOGO_NAME
ENERGENT_ASSET = _ASSETS / ENERGENT_LOGO_NAME
ENERGENT_CROP_PAD_PX = 6


def fps_logo_bytes() -> bytes:
    return FPS_ASSET.read_bytes()


def energent_logo_bytes() -> bytes:
    return ENERGENT_ASSET.read_bytes()


@st.cache_data
def energent_footer_image_bytes() -> bytes:
    """Crop to the opaque mark and pad by 6 px before compositing for display."""
    return composite_asset_bytes(ENERGENT_ASSET, crop=True, pad=ENERGENT_CROP_PAD_PX)


def _data_url(png_bytes: bytes) -> str:
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def footer_html() -> str:
    fps_src = _data_url(fps_logo_bytes())
    energent_src = _data_url(energent_footer_image_bytes())
    fps_caption = escape_html(FOOTER_FPS_CAPTION)
    energent_caption = escape_html(FOOTER_ENERGENT_CAPTION)
    return (
        '<footer class="sib-footer">'
        '<div class="sib-footer-rule-wrap">'
        '<hr class="sib-footer-rule" />'
        "</div>"
        '<div class="sib-footer-row">'
        '<div class="sib-footer-block sib-footer-block-fps">'
        '<div class="sib-footer-logo-box sib-footer-logo-box-fps">'
        f'<img class="sib-footer-logo sib-footer-logo-fps" src="{fps_src}" '
        f'alt="{fps_caption}" width="{FPS_FOOTER_DISPLAY_WIDTH_PX}" '
        f'height="{FPS_FOOTER_DISPLAY_HEIGHT_PX}" />'
        "</div>"
        f'<p class="sib-footer-caption sib-footer-caption-fps">{fps_caption}</p>'
        "</div>"
        '<div class="sib-footer-block sib-footer-block-energent">'
        '<div class="sib-footer-logo-box sib-footer-logo-box-energent">'
        f'<img class="sib-footer-logo sib-footer-logo-energent" src="{energent_src}" '
        f'alt="{energent_caption}" width="{ENERGENT_FOOTER_DISPLAY_WIDTH_PX}" '
        f'height="{ENERGENT_FOOTER_DISPLAY_HEIGHT_PX}" />'
        "</div>"
        f'<p class="sib-footer-caption sib-footer-caption-energent">{energent_caption}</p>'
        "</div>"
        "</div>"
        "</footer>"
    )


def render_footer() -> None:
    with st.container(key=FOOTER_KEY):
        st.html(footer_html())
