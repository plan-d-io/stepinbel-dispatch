"""StepInBel Streamlit product entry point.

StepInBel is an Energent project, supported by the FPS Economy.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PROJECT_ROOT / "src"
for _path in (_SRC, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import streamlit as st

from ui.presentation.footer import render_footer
from ui.presentation.styles import inject_styles
from ui.views.router import render_app

_PAGE_TITLE = "StepInBel"


def main() -> None:
    st.set_page_config(
        page_title=_PAGE_TITLE,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_styles()
    render_app()
    render_footer()


if __name__ == "__main__":
    main()
