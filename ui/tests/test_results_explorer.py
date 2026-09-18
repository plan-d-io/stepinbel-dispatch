from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest
from streamlit.testing.v1 import AppTest

from ui.flow import (
    RESULTS_VIEW_KEY,
    default_state,
    require_json_compatible,
    set_detail_market,
    set_explorer_market,
    store_snapshot,
    sync_results_view,
    unlock_results,
)
from ui.presentation.components import (
    EXPLORER_PANEL_PX,
    EXPLORER_PLOT_CONFIG,
    explorer_figure_height,
    explorer_figure_key,
    explorer_group_breaks,
    explorer_legend_id,
)
from ui.presentation.tokens import (
    EXPLORER_DA_CAPACITY_COPY,
    EXPLORER_ERROR_BODY,
    EXPLORER_ERROR_TITLE,
    EXPLORER_EXPANDER_TITLE,
    EXPLORER_GROUP_CAPACITY,
    EXPLORER_GROUP_GRID,
    EXPLORER_GROUP_MAIN,
    EXPLORER_NO_CAPACITY_COPY,
    EXPLORER_WEEK_INTRO,
    EXPLORER_X_TITLE,
    EXPLORER_ZOOM_CAPTION,
    PAGE_BG,
    PV_NOT_INCLUDED_COPY,
    RESERVED_TAB_BODY,
    SURFACE,
)
from ui.services.artifacts import open_demo_artifacts, result_is_valid
from ui.services.explorer_query import (
    DISPATCH_COLUMNS,
    ExplorerError,
    explorer_weeks_by_market,
    load_explorer_week,
    query_capacity_week,
    query_dispatch_week,
)
from ui.services.result_format import default_explorer_market
from ui.services.explorer_weeks import (
    COMPLETE_AUTUMN_ROWS,
    COMPLETE_ORDINARY_ROWS,
    COMPLETE_SPRING_ROWS,
    DEMO_DEFAULT_WEEK_ID,
    default_week_id,
    enumerate_weeks,
    find_week,
    local_hover_label,
    parse_utc,
    shared_week_ids,
    week_count_copy,
    week_id,
)
from ui.services.form import default_live_form
from ui.services.launch import TEST_HOOKS
from ui.services.paths import JOB_QUERY_KEY, KIND_CASE, KIND_COMPARISON
from ui.services.snapshot import build_snapshot
from ui.tests.test_results_overview_and_detail import (
    _planned_job,
    _resolved,
    _result_record,
    _write_case_dir,
)
from ui.views.results_explorer import explorer_chart_model, explorer_figure_from_payload

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ui" / "app.py"
CANVAS = ROOT / "ui" / "design" / "stepinbel_reference.canvas.tsx"


def _plotly_charts(at: AppTest) -> list[Any]:
    return list(at.get("plotly_chart"))


def _plotly_spec(at: AppTest) -> dict[str, Any]:
    charts = _plotly_charts(at)
    assert len(charts) == 1
    return json.loads(charts[0].proto.spec)


def _plotly_config(at: AppTest) -> dict[str, Any]:
    charts = _plotly_charts(at)
    assert len(charts) == 1
    return json.loads(charts[0].proto.config)


def _layout(figure_or_spec: Any) -> dict[str, Any]:
    if isinstance(figure_or_spec, dict):
        return figure_or_spec.get("layout", figure_or_spec)
    return figure_or_spec.to_plotly_json()["layout"]


def _xaxes(figure_or_spec: Any) -> list[dict[str, Any]]:
    layout = _layout(figure_or_spec)
    items: list[tuple[int, dict[str, Any]]] = []
    for key, value in layout.items():
        if key == "xaxis":
            items.append((1, value))
        elif key.startswith("xaxis") and key[5:].isdigit():
            items.append((int(key[5:]), value))
    return [item[1] for item in sorted(items)]


def _axis_title(axis: dict[str, Any]) -> str:
    title = axis.get("title") or {}
    if isinstance(title, str):
        return title
    return str(title.get("text") or "")


def _legends(figure_or_spec: Any) -> list[str]:
    layout = _layout(figure_or_spec)
    names: list[tuple[int, str]] = []
    for key in layout:
        if key == "legend":
            names.append((1, key))
        elif key.startswith("legend") and key[6:].isdigit():
            names.append((int(key[6:]), key))
    return [name for _index, name in sorted(names)]


def _yaxis_domains(figure_or_spec: Any) -> list[list[float]]:
    layout = _layout(figure_or_spec)
    items: list[tuple[int, list[float]]] = []
    for key, value in layout.items():
        if key == "yaxis":
            items.append((1, list(value.get("domain") or [])))
        elif key.startswith("yaxis") and key[5:].isdigit():
            items.append((int(key[5:]), list(value.get("domain") or [])))
    return [domain for _index, domain in sorted(items)]


def _annotation_text(figure_or_spec: Any) -> str:
    texts = []
    for item in _layout(figure_or_spec).get("annotations") or []:
        texts.append(str(item.get("text") or ""))
    return " ".join(texts)


def _combined(at: AppTest) -> str:
    parts: list[str] = []
    for attr in ("header", "subheader", "markdown", "caption", "text", "info", "success", "warning", "error"):
        for item in getattr(at, attr):
            parts.append(str(item.value))
    for item in at.get("html"):
        parts.append(str(item.proto.body))
    return " ".join(parts)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_belgian_weeks_clip_and_dst_counts() -> None:
    weeks = enumerate_weeks("2024-12-31T23:00:00Z", "2025-12-31T23:00:00Z")
    assert weeks[0]["week_id"] == "2025-W01"
    assert weeks[0]["partial"] is True
    assert weeks[-1]["week_id"] == "2026-W01"
    assert weeks[-1]["partial"] is True
    week13 = find_week(weeks, "2025-W13")
    week20 = find_week(weeks, "2025-W20")
    week43 = find_week(weeks, "2025-W43")
    assert week13["expected_rows"] == COMPLETE_SPRING_ROWS
    assert week20["expected_rows"] == COMPLETE_ORDINARY_ROWS
    assert week43["expected_rows"] == COMPLETE_AUTUMN_ROWS
    assert "partial" not in week20["label"]
    assert "partial" in weeks[0]["label"]
    assert default_week_id(weeks, demo=True) == DEMO_DEFAULT_WEEK_ID
    assert default_week_id(weeks, demo=False) == "2025-W02"


def test_partial_only_and_cross_year_week_ids() -> None:
    short = enumerate_weeks("2025-05-13T00:00:00+00:00", "2025-05-15T00:00:00+00:00")
    assert [item["week_id"] for item in short] == ["2025-W20"]
    assert short[0]["partial"] is True
    assert default_week_id(short, demo=False) == "2025-W20"
    assert "partial week contains 192 quarter-hours" in week_count_copy(short[0])
    cross = enumerate_weeks("2025-12-21T23:00:00Z", "2026-01-11T23:00:00Z")
    ids = [item["week_id"] for item in cross]
    assert "2025-W52" in ids
    assert "2026-W01" in ids
    assert "2026-W02" in ids


def test_demo_week_queries_row_counts_and_continuity() -> None:
    result = open_demo_artifacts()
    cases = (
        ("afrr", "2025-W20", 672),
        ("da", "2025-W20", 672),
        ("mfrr", "2025-W20", 672),
        ("afrr", "2025-W13", 668),
        ("afrr", "2025-W43", 676),
    )
    for market, week, rows in cases:
        payload = load_explorer_week(result, market=market, week_id=week)
        assert payload["row_count"] == rows
        dispatch = payload["dispatch"]
        assert all(len(dispatch[name]) == rows for name in DISPATCH_COLUMNS if name != "datetime_utc")
        stamps = [
            parse_utc(payload["week"]["start_utc"]) + timedelta(minutes=15 * index)
            for index in range(rows)
        ]
        assert payload["x_index"] == list(range(rows))
        figure = explorer_figure_from_payload(payload)
        for trace in figure.data:
            assert list(trace.x) == list(range(rows))
        xaxes = _xaxes(figure)
        assert len(xaxes) == len(explorer_chart_model(payload).panels)
        assert all(axis.get("matches") == "x" for axis in xaxes)
        assert payload["first_hover"] == local_hover_label(stamps[0])
        assert payload["last_hover"] == local_hover_label(stamps[-1])


def test_autumn_repeated_local_times_are_distinguishable() -> None:
    payload = load_explorer_week(open_demo_artifacts(), market="afrr", week_id="2025-W43")
    repeats = [label for label in payload["hover_labels"] if label.startswith("2025-10-26 02:00")]
    assert repeats == ["2025-10-26 02:00 CEST", "2025-10-26 02:00 CET"]


def test_dispatch_projection_and_week_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    original = ds.dataset

    class _Proxy:
        def __init__(self, inner: object) -> None:
            self.inner = inner

        def to_table(self, **kwargs):
            table = self.inner.to_table(**kwargs)
            if kwargs.get("columns") == list(DISPATCH_COLUMNS):
                captured["columns"] = kwargs.get("columns")
                captured["filter"] = kwargs.get("filter")
                captured["rows"] = table.num_rows
                captured["source"] = self.source
            return table

    def wrapped(source, format="parquet"):
        proxy = _Proxy(original(source, format=format))
        proxy.source = str(source)
        return proxy

    monkeypatch.setattr("ui.services.explorer_query.ds.dataset", wrapped)
    payload = load_explorer_week(open_demo_artifacts(), market="afrr", week_id="2025-W20")
    assert captured["columns"] == list(DISPATCH_COLUMNS)
    assert captured["filter"] is not None
    assert captured["rows"] == 672
    assert "dispatch.parquet" in captured["source"]
    assert "dispatch.csv" not in captured["source"]
    assert "pv_export_price_eur_mwh" not in captured["columns"]
    assert min(payload["dispatch"]["market_buy_price_eur_mwh"]) < 0
    assert payload["dispatch"]["total_revenue_eur"] == payload["dispatch"]["total_revenue_eur"]


def test_source_does_not_read_dispatch_csv() -> None:
    from ui.services import explorer_query

    source = inspect.getsource(explorer_query)
    assert "dispatch.csv" not in source
    assert "dispatch.parquet" in source
    assert "read_csv" not in source


def test_field_mapping_and_no_summed_export() -> None:
    payload = load_explorer_week(open_demo_artifacts(), market="afrr", week_id="2025-W20")
    dispatch = payload["dispatch"]
    assert dispatch["p_pump_mw"] is dispatch["p_pump_mw"]
    assert "p_pump_mw" in dispatch and "p_turbine_mw" in dispatch
    assert "reservoir_end_mwh" in dispatch
    assert payload["e_max_mwh"] == pytest.approx(4.444444444444445)
    assert "market_buy_price_eur_mwh" in dispatch
    assert "market_sell_price_eur_mwh" in dispatch
    assert "market_energy_net_eur" in dispatch
    assert "total_revenue_eur" in dispatch
    assert "grid_export_total" not in dispatch
    assert "summed_export" not in dispatch
    assert payload["pv_included"] is True
    da = load_explorer_week(open_demo_artifacts(), market="da", week_id="2025-W20")
    assert da["has_capacity"] is False
    assert da["capacity"] is None


def test_capacity_overlap_empty_and_conflict(tmp_path: Path) -> None:
    start = datetime(2025, 5, 11, 22, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15 * 8)
    origin = datetime(2024, 12, 31, 23, 0, tzinfo=timezone.utc)
    empty = tmp_path / "capacity.parquet"
    pq.write_table(
        pa.table(
            {
                "direction": pa.array([], type=pa.string()),
                "start_index": pa.array([], type=pa.int64()),
                "end_index": pa.array([], type=pa.int64()),
                "committed_mw": pa.array([], type=pa.float64()),
            }
        ),
        empty,
    )
    none = query_capacity_week(
        empty,
        resolved_start_utc=origin,
        start_utc=start,
        end_utc=end,
        expected_rows=8,
    )
    assert none["has_commitment"] is False

    overlap = tmp_path / "cases" / "capacity.parquet"
    overlap.parent.mkdir()
    week_index = int((start - origin).total_seconds() // 900)
    pq.write_table(
        pa.table(
            {
                "direction": ["up", "up"],
                "start_index": [week_index, week_index + 2],
                "end_index": [week_index + 4, week_index + 6],
                "committed_mw": [1.0, 0.5],
            }
        ),
        overlap,
    )
    with pytest.raises(ExplorerError, match=EXPLORER_ERROR_BODY):
        query_capacity_week(
            overlap,
            resolved_start_utc=origin,
            start_utc=start,
            end_utc=end,
            expected_rows=8,
        )
    both = tmp_path / "both" / "capacity.parquet"
    both.parent.mkdir()
    pq.write_table(
        pa.table(
            {
                "direction": ["up", "down"],
                "start_index": [week_index, week_index],
                "end_index": [week_index + 4, week_index + 4],
                "committed_mw": [1.0, 0.4],
            }
        ),
        both,
    )
    expanded = query_capacity_week(
        both,
        resolved_start_utc=origin,
        start_utc=start,
        end_utc=end,
        expected_rows=8,
    )
    assert expanded["has_commitment"] is True
    assert expanded["upward"][:4] == [1.0, 1.0, 1.0, 1.0]
    assert expanded["downward"][:4] == [0.4, 0.4, 0.4, 0.4]


def test_corrupt_dispatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "dispatch.parquet"
    start = datetime(2025, 5, 11, 22, 0, tzinfo=timezone.utc)
    table = pa.table(
        {
            name: (
                [start]
                if name == "datetime_utc"
                else [0.0]
            )
            for name in DISPATCH_COLUMNS
        }
    )
    pq.write_table(table, path)
    with pytest.raises(ExplorerError, match=EXPLORER_ERROR_BODY):
        query_dispatch_week(
            path,
            start_utc=start,
            end_utc=start + timedelta(minutes=15 * 4),
            expected_rows=4,
        )


def test_invalid_result_paths_rejected(tmp_path: Path) -> None:
    bogus = {
        "schema_version": 1,
        "source": "live",
        "kind": "comparison",
        "job_id": "stepinbel-20260907T000000Z-abcd1234",
        "output_directory": str(tmp_path / "missing"),
        "markets": ["da", "mfrr", "afrr"],
        "period": {"start_date": "2025-01-01", "end_date": "2025-12-31"},
        "validated": True,
    }
    assert result_is_valid(bogus, outputs_root=tmp_path) is False
    with pytest.raises(ExplorerError, match=EXPLORER_ERROR_BODY):
        load_explorer_week(bogus, market="afrr", week_id="2025-W20", outputs_root=tmp_path)


def test_default_explorer_market_prefers_day_ahead() -> None:
    assert default_explorer_market(["da", "afrr", "mfrr"]) == "da"
    assert default_explorer_market(["afrr", "da"]) == "da"
    assert default_explorer_market(["mfrr", "da"]) == "da"
    assert default_explorer_market(["afrr", "mfrr"]) == "afrr"
    assert default_explorer_market(["mfrr", "afrr"]) == "afrr"
    assert default_explorer_market(["mfrr"]) == "mfrr"
    assert default_explorer_market(["afrr"]) == "afrr"
    assert default_explorer_market(["da"]) == "da"


def test_explorer_and_detail_state_are_independent() -> None:
    demo = open_demo_artifacts()
    state = default_state()
    unlock_results(state, demo)
    weeks = explorer_weeks_by_market(demo)
    view = sync_results_view(
        state,
        result=demo,
        default_market="afrr",
        allowed_markets=["da", "afrr", "mfrr"],
        default_week=default_week_id(weeks["da"], demo=True),
        allowed_weeks=[item["week_id"] for item in weeks["da"]],
        default_explorer_market="da",
    )
    assert view["selected_detail_market"] == "afrr"
    assert view["selected_explorer_market"] == "da"
    assert view["selected_explorer_week"] == "2025-W20"
    set_detail_market(state, "mfrr")
    set_explorer_market(state, "afrr")
    require_json_compatible(state)
    assert state[RESULTS_VIEW_KEY]["selected_detail_market"] == "mfrr"
    assert state[RESULTS_VIEW_KEY]["selected_explorer_market"] == "afrr"
    kept = sync_results_view(
        state,
        result=demo,
        default_market="afrr",
        allowed_markets=["da", "afrr", "mfrr"],
        default_week="2025-W20",
        allowed_weeks=[item["week_id"] for item in weeks["afrr"]],
        default_explorer_market="da",
    )
    assert kept["selected_explorer_market"] == "afrr"
    assert kept["selected_detail_market"] == "mfrr"
    other = dict(demo)
    other["job_id"] = "demo-other-identity"
    other["output_directory"] = str(Path(demo["output_directory"])) + "-other"
    reset = sync_results_view(
        state,
        result=other,
        default_market="afrr",
        allowed_markets=["da", "afrr", "mfrr"],
        default_week="2025-W20",
        allowed_weeks=[item["week_id"] for item in weeks["da"]],
        default_explorer_market="da",
    )
    assert reset["selected_explorer_market"] == "da"
    assert reset["selected_detail_market"] == "afrr"
    assert "2025-W20" in shared_week_ids(weeks)


def test_app_demo_data_explorer_defaults_and_no_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    popen_calls: list[str] = []

    def bang(**_kwargs):
        popen_calls.append("popen")
        raise AssertionError("Data explorer must not launch a worker")

    TEST_HOOKS.clear()
    TEST_HOOKS["popen"] = bang
    try:
        at = AppTest.from_file(str(APP), default_timeout=120)
        at.run()
        next(item for item in at.checkbox if item.label == "Demo mode").set_value(True)
        at.run()
        next(item for item in at.button if item.label == "Continue").click()
        at.run()
        next(item for item in at.button if item.label == "View demonstration results").click()
        at.run()
        next(item for item in at.button if item.label == "Market detail").click()
        at.run()
        detail = next(item for item in at.selectbox if item.label == "Market")
        detail.set_value("mFRR")
        at.run()
        next(item for item in at.button if item.label == "Data explorer").click()
        at.run()
        assert not at.exception
        text = _combined(at)
        spec = _plotly_spec(at)
        annotations = _annotation_text(spec)
        assert EXPLORER_EXPANDER_TITLE in [item.label for item in at.expander]
        assert EXPLORER_WEEK_INTRO in text
        assert "This complete week contains 672 quarter-hours." in text
        assert "Pump and turbine power" in annotations
        assert EXPLORER_GROUP_MAIN in text
        assert EXPLORER_GROUP_MAIN not in annotations
        assert [item.value for item in at.subheader].count(EXPLORER_GROUP_MAIN) == 1
        assert EXPLORER_GROUP_GRID in annotations
        assert EXPLORER_GROUP_CAPACITY not in annotations
        assert EXPLORER_GROUP_CAPACITY in text
        assert EXPLORER_DA_CAPACITY_COPY in text
        assert "Upward commitment" not in [trace.get("name") for trace in spec["data"]]
        assert all(axis.get("showticklabels") is True for axis in _xaxes(spec))
        assert EXPLORER_X_TITLE in [_axis_title(axis) for axis in _xaxes(spec)]
        assert [_axis_title(axis) for axis in _xaxes(spec)].count(EXPLORER_X_TITLE) == 1
        assert "versus Belgian local time" not in text
        assert "versus Belgian local time" not in json.dumps(spec)
        assert EXPLORER_ZOOM_CAPTION in text
        assert RESERVED_TAB_BODY not in text
        assert EXPLORER_ERROR_TITLE not in text
        assert "hourly sample" not in text.lower()
        market = next(item for item in at.selectbox if item.label == "Market")
        week = next(item for item in at.selectbox if item.label == "Week")
        assert list(market.options) == ["Day-ahead", "aFRR", "mFRR"]
        assert market.value == "Day-ahead"
        assert week.value.startswith("Week 20 (2025)")
        view = at.session_state["sib"]["results_view"]
        require_json_compatible(at.session_state["sib"])
        assert set(view) == {
            "active_tab",
            "selected_detail_market",
            "selected_explorer_market",
            "selected_explorer_week",
            "selected_technical_market",
        }
        assert view["selected_detail_market"] == "mfrr"
        assert view["selected_explorer_market"] == "da"
        assert view["selected_explorer_week"] == "2025-W20"
        assert view["selected_technical_market"] == "da"
        assert "zoom" not in json.dumps(view)
        assert "xaxis.range" not in json.dumps(at.session_state["sib"])
        config = _plotly_config(at)
        assert config["displayModeBar"] == EXPLORER_PLOT_CONFIG["displayModeBar"]
        assert config["displaylogo"] is False
        removed = set(config["modeBarButtonsToRemove"])
        assert "select2d" in removed and "lasso2d" in removed
        assert "toImage" in removed
        assert "zoom2d" not in removed
        assert "pan2d" not in removed
        assert "zoomIn2d" not in removed
        assert "zoomOut2d" not in removed
        assert "resetScale2d" not in removed
        assert "autoScale2d" not in removed
        explorer_key = _plotly_charts(at)[0].key
        assert "da" in str(explorer_key)
        assert "2025-W20" in str(explorer_key)
        assert explorer_figure_key("demo:x:y", "afrr", "2025-W20") != explorer_figure_key(
            "demo:x:y", "da", "2025-W20"
        )
        assert explorer_figure_key("demo:x:y", "afrr", "2025-W20") != explorer_figure_key(
            "demo:x:y", "afrr", "2025-W13"
        )
        for key, value in at.session_state["sib"].items():
            dumped = json.dumps(value, default=str)
            assert "datetime_utc" not in dumped or key == "result"
        market.set_value("aFRR")
        at.run()
        assert not at.exception
        switched = _combined(at)
        afrr_spec = _plotly_spec(at)
        afrr_names = [trace.get("name") for trace in afrr_spec["data"]]
        assert EXPLORER_DA_CAPACITY_COPY not in switched
        assert EXPLORER_GROUP_CAPACITY in _annotation_text(afrr_spec)
        assert "Upward commitment" in afrr_names
        afrr_key = _plotly_charts(at)[0].key
        assert afrr_key != explorer_key
        assert "afrr" in str(afrr_key) and "2025-W20" in str(afrr_key)
        assert all(axis.get("matches") == "x" for axis in _xaxes(afrr_spec))
        assert at.session_state["sib"]["results_view"]["selected_explorer_market"] == "afrr"
        assert at.session_state["sib"]["results_view"]["selected_detail_market"] == "mfrr"
        next(item for item in at.button if item.label == "Overview").click()
        at.run()
        next(item for item in at.button if item.label == "Data explorer").click()
        at.run()
        assert not at.exception
        assert next(item for item in at.selectbox if item.label == "Market").value == "aFRR"
        assert at.session_state["sib"]["results_view"]["selected_explorer_market"] == "afrr"
        assert at.session_state["sib"]["results_view"]["selected_detail_market"] == "mfrr"
        assert popen_calls == []
        assert at.session_state["sib"]["results_view"]["selected_explorer_week"] == "2025-W20"
        next(item for item in at.button if item.label == "1  Configure").click()
        at.run()
        assert not at.exception
        assert at.session_state["sib"]["stage"] == 1
    finally:
        TEST_HOOKS.clear()


def test_one_market_explorer_hides_market_selector(tmp_path: Path) -> None:
    result = open_demo_artifacts()
    payload = load_explorer_week(result, market="da", week_id="2025-W20")
    assert payload["has_capacity"] is False
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20260907T010000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_CASE, markets=["da"])
    resolved = _resolved(market="da", pv_kw=0.0)
    resolved["resolved_start_utc"] = "2024-12-31T23:00:00Z"
    resolved["resolved_end_exclusive_utc"] = "2025-12-31T23:00:00Z"
    resolved["e_max_mwh"] = 4.444
    resolved["effective_grid_import_mw"] = 1.0
    resolved["effective_grid_export_mw"] = 1.0
    _write_case_dir(Path(record["output_directory"]), "da", resolved=resolved)
    (Path(record["output_directory"]) / "run_status.json").write_text(
        '{"status_schema_version": 1, "run_id": "%s", "state": "completed"}' % job_id,
        encoding="utf-8",
    )
    TEST_HOOKS.clear()
    TEST_HOOKS["outputs_root"] = tmp_path
    TEST_HOOKS["popen"] = lambda **_kwargs: (_ for _ in ()).throw(AssertionError("no worker"))
    try:
        state = default_state()
        store_snapshot(state, snapshot, snapshot["form_fingerprint"])
        state["form"] = form
        state["job"] = record
        unlock_results(state, _result_record(record, snapshot))
        at = AppTest.from_file(str(APP), default_timeout=60)
        at.run()
        at.session_state["sib"] = state
        at.query_params[JOB_QUERY_KEY] = job_id
        at.run()
        next(item for item in at.button if item.label == "Data explorer").click()
        at.run()
        text = _combined(at)
        assert EXPLORER_ERROR_TITLE in text
        assert EXPLORER_ERROR_BODY in text
        assert "Pump and turbine power" not in text
        assert not list(at.get("plotly_chart"))
        assert str(tmp_path) not in text
        assert "Traceback" not in text
        assert not any(item.label == "Market" for item in at.selectbox)
    finally:
        TEST_HOOKS.clear()


@pytest.mark.skipif(not CANVAS.is_file(), reason="design canvas is not in the public tree")
def test_preview_and_canvas_use_explorer_headings() -> None:
    canvas = CANVAS.read_text(encoding="utf-8")
    assert "Pump and turbine power" in canvas
    assert "Main results" in canvas
    assert "Grid connection loading" in canvas
    assert "Balancing capacity commitments" in canvas
    assert "Day-ahead" in canvas
    assert "This complete week contains 672 quarter-hours." in canvas
    assert "Download displayed week (CSV)" in canvas
    assert "Includes all series shown below for the selected market and full displayed week." in canvas
    assert "hourly display sample from a complete stored week" not in canvas.lower()
    assert "versus Belgian local time" not in canvas
    assert "Highest simulated total site revenue" not in canvas
    assert "Drag horizontally to zoom all panels" in canvas
    plan = (ROOT / "ui" / "PLAN.md").read_text(encoding="utf-8")
    context = (ROOT / "ui" / "STREAMLIT_AGENT_CONTEXT.md").read_text(encoding="utf-8")
    assert "shared-x Plotly subplot figure" in plan
    assert "Download displayed week (CSV)" in plan
    assert "Main results" in plan
    assert "matched x-axes" in context
    assert "Download displayed week (CSV)" in context
    assert "on_click=" in context
    assert "Do not add a separate highest-revenue" in context
    assert "versus Belgian local time" in context


def test_composite_figure_groups_units_and_no_pv() -> None:
    demo = load_explorer_week(open_demo_artifacts(), market="afrr", week_id="2025-W20")
    figure = explorer_figure_from_payload(demo)
    model = explorer_chart_model(demo)
    xaxes = _xaxes(figure)
    assert len(xaxes) == len(model.panels)
    assert all(axis.get("matches") == "x" for axis in xaxes)
    assert all(axis.get("showticklabels") is True for axis in xaxes)
    titles = [_axis_title(axis) for axis in xaxes]
    assert titles.count(EXPLORER_X_TITLE) == 1
    assert titles[-1] == EXPLORER_X_TITLE
    assert all(title != EXPLORER_X_TITLE for title in titles[:-1])
    annotations = _annotation_text(figure)
    assert EXPLORER_GROUP_MAIN not in annotations
    assert EXPLORER_GROUP_GRID in annotations
    assert EXPLORER_GROUP_CAPACITY in annotations
    assert "Pump and turbine power" in annotations
    y_titles = []
    for key, value in _layout(figure).items():
        if key == "yaxis" or (key.startswith("yaxis") and key[5:].isdigit()):
            y_titles.append(_axis_title(value))
    assert "Power (MW)" in y_titles
    assert "Stored energy (MWh)" in y_titles
    assert "Price (EUR/MWh)" in y_titles
    assert "Revenue per quarter-hour (EUR)" in y_titles
    assert "Committed capacity (MW)" in y_titles
    dumped = json.dumps(figure.to_plotly_json())
    assert "versus Belgian local time" not in dumped
    layout = _layout(figure)
    assert layout["paper_bgcolor"] == PAGE_BG
    assert layout["plot_bgcolor"] == SURFACE
    assert layout["height"] == explorer_figure_height(
        len(model.panels), explorer_group_breaks(model.panels)
    )
    assert layout["height"] >= len(model.panels) * 220
    assert EXPLORER_PANEL_PX >= 220
    domains = _yaxis_domains(figure)
    assert len(domains) == len(model.panels)
    gaps = [domains[index][0] - domains[index + 1][1] for index in range(len(domains) - 1)]
    assert all(gap > 0.02 for gap in gaps)
    assert max(gaps) > min(gaps)
    legends = _legends(figure)
    assert legends == [explorer_legend_id(index) for index in range(1, len(model.panels) + 1)]
    by_legend: dict[str, list[str]] = {}
    for trace in figure.to_plotly_json()["data"]:
        key = str(trace.get("legend") or "legend")
        by_legend.setdefault(key, []).append(str(trace.get("name")))
    assert set(by_legend) == set(legends)
    assert by_legend["legend"] == ["Pumping", "Generation"]
    assert "Reservoir capacity" in by_legend["legend2"]
    assert "Import limit" in by_legend["legend6"]
    assert "Export limit" in by_legend["legend7"]
    assert all(name != "Pumping" for name in by_legend["legend4"])
    da = load_explorer_week(open_demo_artifacts(), market="da", week_id="2025-W20")
    da_model = explorer_chart_model(da)
    da_figure = explorer_figure_from_payload(da)
    assert da_model.capacity_message == EXPLORER_DA_CAPACITY_COPY
    assert all(panel.group != EXPLORER_GROUP_CAPACITY for panel in da_model.panels)
    assert da_figure.layout.height < figure.layout.height
    da_names = [trace.name for trace in da_figure.data]
    assert "Upward commitment" not in da_names
    assert "PV allocation" in _annotation_text(da_figure)
    no_pv = dict(demo)
    no_pv["pv_included"] = False
    skipped = explorer_chart_model(no_pv)
    assert skipped.pv_message == PV_NOT_INCLUDED_COPY
    assert all(panel.title != "PV allocation" for panel in skipped.panels)
    skipped_figure = explorer_figure_from_payload(no_pv)
    assert skipped_figure.layout.height < figure.layout.height


def test_zoom_sync_is_native_matched_xaxes() -> None:
    from ui.presentation import components
    from ui.views import results_explorer

    source = inspect.getsource(results_explorer) + inspect.getsource(components)
    assert "make_subplots" in inspect.getsource(components)
    assert "shared_xaxes=True" in inspect.getsource(components)
    assert 'matches="x"' in inspect.getsource(components)
    assert 'on_select="ignore"' in inspect.getsource(components)
    assert "relayout" not in source
    assert "iframe" not in source
    assert "components.v1" not in source
    assert "Popen" not in inspect.getsource(results_explorer)


def test_week_id_helper() -> None:
    assert week_id(2025, 20) == "2025-W20"
    assert PV_NOT_INCLUDED_COPY.startswith("PV was not included")
    assert EXPLORER_NO_CAPACITY_COPY.startswith("No balancing-capacity")
    assert KIND_COMPARISON == "comparison"
