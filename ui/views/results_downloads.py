"""Functional Downloads tab."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import streamlit as st

from ui.flow import result_open_identity
from ui.presentation.components import render_display_table, render_section_heading, render_status_panel
from ui.presentation.tokens import (
    COMPLETE_PACKAGE_HEADING,
    DOWNLOADS_ERROR_BODY,
    DOWNLOADS_ERROR_TITLE,
    DOWNLOADS_FORMAT_HELP,
    DOWNLOADS_FORMAT_HELP_TITLE,
    DOWNLOADS_PACKAGE_LABEL,
    DOWNLOADS_REPORT_LABEL,
    DOWNLOADS_SELECTED_LABEL,
    DOWNLOADS_STORED_INTRO,
    DOWNLOADS_SUMMARY_LABEL,
    DOWNLOADS_ZIP_TOO_LARGE,
)
from ui.services.artifacts import result_folder_display
from ui.services.launch import TEST_HOOKS
from ui.services.result_downloads import (
    DownloadsError,
    InventoryItem,
    build_download_inventory,
    build_result_zip,
    download_filename,
    inventory_table_rows,
    mime_type,
    quick_download_paths,
    read_inventory_file,
    scopes_from_inventory,
    zip_download_filename,
    zip_is_within_limit,
)


def _outputs_root() -> Path | None:
    hooked = TEST_HOOKS.get("outputs_root")
    return Path(hooked) if hooked is not None else None


def _file_loader(
    result: Mapping[str, Any],
    relative: str,
    job: Mapping[str, Any] | None,
    outputs_root: Path | None,
):
    def _load() -> bytes:
        return read_inventory_file(result, relative, job=job, outputs_root=outputs_root)

    return _load


def _zip_loader(
    result: Mapping[str, Any],
    job: Mapping[str, Any] | None,
    outputs_root: Path | None,
):
    def _load() -> bytes:
        return build_result_zip(result, job=job, outputs_root=outputs_root)

    return _load


def _items_for_scope(items: list[InventoryItem], scope: str) -> list[InventoryItem]:
    return [item for item in items if item.scope == scope]


def render_downloads(
    state: Mapping[str, Any],
    *,
    result: Mapping[str, Any],
) -> None:
    job = state.get("job") if isinstance(state.get("job"), Mapping) else None
    outputs_root = _outputs_root()
    identity = result_open_identity(result)
    try:
        items = build_download_inventory(result, job=job, outputs_root=outputs_root)
    except DownloadsError:
        render_status_panel("danger", DOWNLOADS_ERROR_TITLE, DOWNLOADS_ERROR_BODY)
        return
    run_id = str(result["job_id"])
    kind = str(result["kind"])
    summary_path, report_path = quick_download_paths(kind)
    allow_zip = zip_is_within_limit(items)

    st.write(DOWNLOADS_STORED_INTRO)
    st.code(result_folder_display(result))

    render_section_heading(COMPLETE_PACKAGE_HEADING)
    if allow_zip:
        st.download_button(
            DOWNLOADS_PACKAGE_LABEL,
            data=_zip_loader(dict(result), dict(job) if job else None, outputs_root),
            file_name=zip_download_filename(run_id),
            mime=mime_type("package.zip"),
            type="primary",
            key=f"sib-download-zip-{identity}",
            on_click="ignore",
        )
    else:
        st.write(DOWNLOADS_ZIP_TOO_LARGE)

    render_section_heading("Quick downloads")
    cols = st.columns(2)
    with cols[0]:
        st.download_button(
            DOWNLOADS_SUMMARY_LABEL,
            data=_file_loader(dict(result), summary_path, dict(job) if job else None, outputs_root),
            file_name=download_filename(run_id, summary_path),
            mime=mime_type(summary_path),
            key=f"sib-download-summary-{identity}",
            on_click="ignore",
        )
    with cols[1]:
        st.download_button(
            DOWNLOADS_REPORT_LABEL,
            data=_file_loader(dict(result), report_path, dict(job) if job else None, outputs_root),
            file_name=download_filename(run_id, report_path),
            mime=mime_type(report_path),
            key=f"sib-download-report-{identity}",
            on_click="ignore",
        )

    render_section_heading("Individual files")
    scopes = scopes_from_inventory(items)
    if len(scopes) > 1:
        scope_labels = [label for _, label in scopes]
        chosen_scope = st.selectbox(
            "Scope",
            scope_labels,
            key=f"sib-download-scope-{identity}",
        )
        scope_key = next(key for key, label in scopes if label == chosen_scope)
    else:
        scope_key = scopes[0][0]
    scoped = _items_for_scope(items, scope_key)
    file_labels = [f"{item.description} · {item.relative_path}" for item in scoped]
    chosen_file = st.selectbox(
        "File",
        file_labels,
        key=f"sib-download-file-{identity}-{scope_key}",
    )
    selected = scoped[file_labels.index(str(chosen_file))]
    st.caption(selected.relative_path)
    st.download_button(
        DOWNLOADS_SELECTED_LABEL,
        data=_file_loader(dict(result), selected.relative_path, dict(job) if job else None, outputs_root),
        file_name=download_filename(run_id, selected.relative_path),
        mime=mime_type(selected.filename),
        key=f"sib-download-selected-{identity}",
        on_click="ignore",
    )

    render_section_heading("Inventory")
    render_display_table(inventory_table_rows(items))
    with st.expander(DOWNLOADS_FORMAT_HELP_TITLE, expanded=False):
        st.write(DOWNLOADS_FORMAT_HELP)
