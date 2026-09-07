"""Browser query helpers for job reconnection. Streamlit only."""

from __future__ import annotations

import streamlit as st

from ui.services.paths import JOB_QUERY_KEY


def clear_job_query() -> None:
    try:
        if JOB_QUERY_KEY in st.query_params:
            del st.query_params[JOB_QUERY_KEY]
    except Exception:
        pass
