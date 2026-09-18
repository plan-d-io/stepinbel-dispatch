from __future__ import annotations

import csv
import inspect
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from ui.presentation.tokens import EXPLORER_CSV_CAPTION, EXPLORER_CSV_LABEL
from ui.services.artifacts import open_demo_artifacts
from ui.services.explorer_csv import (
    build_explorer_week_csv,
    deferred_explorer_week_csv,
    encode_explorer_week_csv,
    explorer_csv_columns,
    explorer_csv_filename,
    explorer_week_timestamps,
    format_datetime_belgium,
    format_datetime_utc,
)
from ui.services.explorer_query import ExplorerError, load_explorer_week
from ui.services.explorer_weeks import (
    COMPLETE_AUTUMN_ROWS,
    COMPLETE_ORDINARY_ROWS,
    COMPLETE_SPRING_ROWS,
)
from ui.services.launch import TEST_HOOKS
from ui.tests.result_artifact_fixtures import identified_relocated_one_case_live
from ui.views.results_explorer import explorer_chart_model

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ui" / "app.py"
DEMO_DIR = ROOT / "ui" / "demo_artifacts" / "stepinbel_2025_all_markets_pv500"

DA_PV_COLUMNS = (
    "datetime_utc",
    "datetime_belgium",
    "p_pump_mw",
    "p_turbine_mw",
    "reservoir_end_mwh",
    "e_max_mwh",
    "market_buy_price_eur_mwh",
    "market_sell_price_eur_mwh",
    "market_energy_net_eur",
    "pv_revenue_eur",
    "total_revenue_eur",
    "pv_available_mw",
    "pv_to_pump_mw",
    "pv_export_mw",
    "pv_curtail_mw",
    "p_pump_grid_mw",
    "effective_grid_import_mw",
    "effective_grid_export_mw",
)


def _parse(payload: bytes) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(payload.decode("utf-8")))
    header = next(reader)
    return header, list(reader)


def _audit() -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(DEMO_DIR)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in DEMO_DIR.rglob("*")
        if path.is_file()
    }


def _tiny_payload(
    *,
    pv: bool = False,
    wind: bool = False,
    has_capacity: bool = False,
    upward: list[float] | None = None,
    downward: list[float] | None = None,
    n: int = 4,
) -> dict[str, Any]:
    start = datetime(2025, 1, 15, 23, 0, tzinfo=timezone.utc)
    stamps = [start + timedelta(minutes=15 * index) for index in range(n)]
    zeros = [0.0] * n
    ones = [1.0] * n
    dispatch = {
        "p_pump_mw": list(ones),
        "p_turbine_mw": list(zeros),
        "reservoir_end_mwh": list(ones),
        "market_buy_price_eur_mwh": list(ones),
        "market_sell_price_eur_mwh": list(ones),
        "market_energy_net_eur": list(zeros),
        "total_revenue_eur": list(zeros),
        "p_pump_grid_mw": list(ones),
    }
    if pv:
        dispatch.update(
            {
                "pv_revenue_eur": list(zeros),
                "pv_available_mw": list(ones),
                "pv_to_pump_mw": list(zeros),
                "pv_export_mw": list(ones),
                "pv_curtail_mw": list(zeros),
            }
        )
    if wind:
        dispatch.update(
            {
                "wind_revenue_eur": list(zeros),
                "wind_available_mw": list(ones),
                "wind_to_pump_mw": list(zeros),
                "wind_export_mw": list(ones),
                "wind_curtail_mw": list(zeros),
            }
        )
    capacity = None
    if has_capacity:
        capacity = {
            "upward": upward,
            "downward": downward,
            "has_commitment": upward is not None or downward is not None,
        }
    end = start + timedelta(minutes=15 * n)
    return {
        "week": {
            "start_utc": start.isoformat(),
            "end_utc": end.isoformat(),
            "expected_rows": n,
        },
        "datetime_utc": stamps,
        "row_count": n,
        "pv_included": pv,
        "wind_included": wind,
        "e_max_mwh": 4.5,
        "effective_grid_import_mw": 2.5,
        "effective_grid_export_mw": 3.5,
        "dispatch": dispatch,
        "capacity": capacity,
        "has_capacity": has_capacity,
    }


def _open_demo_explorer(at: AppTest) -> None:
    next(item for item in at.checkbox if item.label == "Demo mode").set_value(True)
    at.run()
    next(item for item in at.button if item.label == "Continue").click()
    at.run()
    next(item for item in at.button if item.label == "View demonstration results").click()
    at.run()
    next(item for item in at.button if item.label == "Data explorer").click()
    at.run()


def test_render_and_selection_do_not_encode_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = build_explorer_week_csv

    def wrapped(*args: Any, **kwargs: Any) -> bytes:
        calls.append("csv")
        return real(*args, **kwargs)

    monkeypatch.setattr("ui.services.explorer_csv.build_explorer_week_csv", wrapped)
    TEST_HOOKS.clear()
    try:
        at = AppTest.from_file(str(APP), default_timeout=120)
        at.run()
        _open_demo_explorer(at)
        assert not at.exception
        assert EXPLORER_CSV_LABEL in [item.label for item in at.download_button]
        assert EXPLORER_CSV_CAPTION in " ".join(str(item.value) for item in at.caption)
        assert calls == []
        market = next(item for item in at.selectbox if item.label == "Market")
        market.set_value("aFRR")
        at.run()
        assert not at.exception
        assert calls == []
        week = next(item for item in at.selectbox if item.label == "Week")
        week_options = list(week.options)
        next_week = next(option for option in week_options if option != week.value)
        week.set_value(next_week)
        at.run()
        assert not at.exception
        assert calls == []
        next(item for item in at.button if item.label == "Overview").click()
        at.run()
        next(item for item in at.button if item.label == "Data explorer").click()
        at.run()
        assert not at.exception
        assert calls == []
        loader = deferred_explorer_week_csv(
            open_demo_artifacts(), market="da", week_id="2025-W20", job=None, outputs_root=None
        )
        assert calls == []
        payload = loader()
        assert calls == ["csv"]
        header, rows = _parse(payload)
        assert header == list(DA_PV_COLUMNS)
        assert len(rows) == COMPLETE_ORDINARY_ROWS
    finally:
        TEST_HOOKS.clear()


def test_callable_matches_displayed_week_and_boundaries() -> None:
    result = open_demo_artifacts()
    week = load_explorer_week(result, market="da", week_id="2025-W20")
    payload = build_explorer_week_csv(result, market="da", week_id="2025-W20")
    header, rows = _parse(payload)
    assert header[0:2] == ["datetime_utc", "datetime_belgium"]
    assert len(rows) == week["row_count"] == COMPLETE_ORDINARY_ROWS
    stamps = explorer_week_timestamps(week)
    assert [row[0] for row in rows] == [format_datetime_utc(item) for item in stamps]
    assert rows[0][0] == format_datetime_utc(stamps[0])
    assert rows[-1][0] == format_datetime_utc(stamps[-1])
    assert rows[0][1] == format_datetime_belgium(stamps[0])
    assert rows[-1][1] == format_datetime_belgium(stamps[-1])
    assert rows[0][0].endswith("Z")
    assert rows[0][1].endswith("+02:00")
    assert "T" in rows[0][0] and "T" in rows[0][1]
    assert payload.count(b"p_turbine_mw") == 1
    assert payload.count(b"pv_export_mw") == 1
    e_max_index = header.index("e_max_mwh")
    import_index = header.index("effective_grid_import_mw")
    export_index = header.index("effective_grid_export_mw")
    assert {float(row[e_max_index]) for row in rows} == {float(week["e_max_mwh"])}
    assert {float(row[import_index]) for row in rows} == {float(week["effective_grid_import_mw"])}
    assert {float(row[export_index]) for row in rows} == {float(week["effective_grid_export_mw"])}
    pump_index = header.index("p_pump_mw")
    assert float(rows[0][pump_index]) == float(week["dispatch"]["p_pump_mw"][0])


def test_dst_weeks_preserve_established_row_counts() -> None:
    result = open_demo_artifacts()
    spring = build_explorer_week_csv(result, market="afrr", week_id="2025-W13")
    autumn = build_explorer_week_csv(result, market="afrr", week_id="2025-W43")
    _header_s, spring_rows = _parse(spring)
    header_a, autumn_rows = _parse(autumn)
    assert len(spring_rows) == COMPLETE_SPRING_ROWS
    assert len(autumn_rows) == COMPLETE_AUTUMN_ROWS
    belgian = [row[1] for row in autumn_rows]
    repeated = [item for item in belgian if item.startswith("2025-10-26T02:00:00")]
    assert repeated == ["2025-10-26T02:00:00+02:00", "2025-10-26T02:00:00+01:00"]
    ordinary = _parse(build_explorer_week_csv(result, market="afrr", week_id="2025-W20"))[0]
    assert "upward_commitment_mw" in ordinary
    assert "downward_commitment_mw" not in ordinary
    assert "upward_commitment_mw" not in header_a
    assert "downward_commitment_mw" not in header_a


def test_day_ahead_omits_capacity_and_balancing_includes_plotted() -> None:
    result = open_demo_artifacts()
    da = _parse(build_explorer_week_csv(result, market="da", week_id="2025-W20"))[0]
    afrr_payload = load_explorer_week(result, market="afrr", week_id="2025-W20")
    model = explorer_chart_model(afrr_payload)
    afrr = _parse(build_explorer_week_csv(result, market="afrr", week_id="2025-W20"))[0]
    assert "upward_commitment_mw" not in da
    assert "downward_commitment_mw" not in da
    assert da == list(DA_PV_COLUMNS)
    names = [name for panel in model.panels for name, _series in panel.series]
    assert "Upward commitment" in names
    assert "Downward commitment" not in names
    assert afrr[-1] == "upward_commitment_mw"
    assert "downward_commitment_mw" not in afrr
    upward_only = encode_explorer_week_csv(
        _tiny_payload(has_capacity=True, upward=[1.0, 1.0, 1.0, 1.0], downward=None)
    )
    header, _rows = _parse(upward_only)
    assert "upward_commitment_mw" in header
    assert "downward_commitment_mw" not in header
    none = encode_explorer_week_csv(_tiny_payload(has_capacity=True, upward=None, downward=None))
    assert "upward_commitment_mw" not in _parse(none)[0]


def test_resource_column_sets_are_exact() -> None:
    none = _parse(encode_explorer_week_csv(_tiny_payload()))[0]
    pv = _parse(encode_explorer_week_csv(_tiny_payload(pv=True)))[0]
    wind = _parse(encode_explorer_week_csv(_tiny_payload(wind=True)))[0]
    both = _parse(encode_explorer_week_csv(_tiny_payload(pv=True, wind=True)))[0]
    assert "pv_available_mw" not in none and "wind_available_mw" not in none
    assert "pv_revenue_eur" not in none and "wind_revenue_eur" not in none
    assert pv.count("p_turbine_mw") == 1
    assert pv.count("pv_export_mw") == 1
    assert "wind_available_mw" not in pv
    assert "pv_available_mw" not in wind
    assert wind.index("wind_revenue_eur") < wind.index("total_revenue_eur")
    assert both.index("pv_available_mw") < both.index("wind_available_mw")
    assert "pv_export_mw" in both and "wind_export_mw" in both
    assert list(explorer_csv_columns(_tiny_payload(pv=True, wind=True))).count("p_turbine_mw") == 1


def test_zoom_is_irrelevant_to_generated_bytes() -> None:
    source = inspect.getsource(build_explorer_week_csv) + inspect.getsource(encode_explorer_week_csv)
    assert "zoom" not in source
    assert "xaxis.range" not in source
    import ui.services.explorer_csv as module

    assert "pandas" not in inspect.getsource(module)
    first = build_explorer_week_csv(open_demo_artifacts(), market="da", week_id="2025-W20")
    second = build_explorer_week_csv(open_demo_artifacts(), market="da", week_id="2025-W20")
    assert first == second


def test_one_market_and_comparison_child_selection(tmp_path: Path) -> None:
    record, result = identified_relocated_one_case_live(tmp_path)
    one = build_explorer_week_csv(
        result, market="da", week_id="2025-W20", job=record, outputs_root=tmp_path
    )
    header, rows = _parse(one)
    assert header == list(DA_PV_COLUMNS)
    assert len(rows) == COMPLETE_ORDINARY_ROWS
    demo = open_demo_artifacts()
    da = _parse(build_explorer_week_csv(demo, market="da", week_id="2025-W20"))[0]
    afrr = _parse(build_explorer_week_csv(demo, market="afrr", week_id="2025-W20"))[0]
    mfrr = _parse(build_explorer_week_csv(demo, market="mfrr", week_id="2025-W20"))[0]
    assert da == header
    assert afrr != da
    assert mfrr[-1] == "upward_commitment_mw"
    assert "downward_commitment_mw" not in mfrr
    name = explorer_csv_filename("demo-2025-all-markets-pv500", "da", "2025-W20")
    assert name == "demo-2025-all-markets-pv500-da-2025-W20-data-explorer.csv"
    assert "/" not in name and "\\" not in name


def test_schema_v1_v2_v3_csv(tmp_path: Path) -> None:
    from ui.tests.test_wind_ui import _execute_da, _form

    demo = build_explorer_week_csv(open_demo_artifacts(), market="da", week_id="2025-W20")
    assert _parse(demo)[0] == list(DA_PV_COLUMNS)
    v2_record, v2_result, _snapshot = _execute_da(
        tmp_path / "v2",
        _form(fixed_speed_pump=True),
        job_id="stepinbel-20250115T120000Z-c5c00002",
    )
    v2 = build_explorer_week_csv(
        v2_result,
        market="da",
        week_id="2025-W03",
        job=v2_record,
        outputs_root=tmp_path / "v2",
    )
    v2_header, v2_rows = _parse(v2)
    assert "pv_revenue_eur" not in v2_header
    assert "wind_revenue_eur" not in v2_header
    assert "upward_commitment_mw" not in v2_header
    assert v2_rows
    v3_record, v3_result, _snapshot = _execute_da(
        tmp_path / "v3",
        _form(
            pv_enabled=True,
            pv_ac_kw=500.0,
            wind_enabled=True,
            wind_capacity_kw=1000.0,
        ),
        job_id="stepinbel-20250115T120000Z-c5c00003",
    )
    v3 = build_explorer_week_csv(
        v3_result,
        market="da",
        week_id="2025-W03",
        job=v3_record,
        outputs_root=tmp_path / "v3",
    )
    v3_header, v3_rows = _parse(v3)
    assert "wind_available_mw" in v3_header
    assert "pv_available_mw" in v3_header
    assert v3_header.count("p_turbine_mw") == 1
    assert v3_header.count("pv_export_mw") == 1
    assert v3_header.count("wind_export_mw") == 1
    assert v3_rows
    assert "upward_commitment_mw" not in v3_header


def test_tamper_between_render_and_click_fails_closed(tmp_path: Path) -> None:
    record, result = identified_relocated_one_case_live(tmp_path)
    loader = deferred_explorer_week_csv(
        result, market="da", week_id="2025-W20", job=record, outputs_root=tmp_path
    )
    original = loader()
    assert original
    target = Path(record["output_directory"]) / "dispatch.parquet"
    target.write_bytes(target.read_bytes() + b"x")
    with pytest.raises(ExplorerError):
        loader()
    swapped = dict(result)
    swapped["period"] = {"start_date": "2024-01-01", "end_date": "2024-12-31"}
    with pytest.raises(ExplorerError):
        build_explorer_week_csv(
            swapped, market="da", week_id="2025-W20", job=record, outputs_root=tmp_path
        )


def test_csv_does_not_launch_or_write() -> None:
    popen_calls: list[str] = []

    def bang(**_kwargs: Any) -> None:
        popen_calls.append("popen")
        raise AssertionError("weekly CSV must not launch a worker")

    before = _audit()
    TEST_HOOKS.clear()
    TEST_HOOKS["popen"] = bang
    try:
        payload = build_explorer_week_csv(open_demo_artifacts(), market="da", week_id="2025-W20")
        assert payload.startswith(b"datetime_utc,")
        assert popen_calls == []
        import ui.views.results_explorer as view

        assert "Popen" not in inspect.getsource(view)
        assert 'on_click="ignore"' in inspect.getsource(view)
    finally:
        TEST_HOOKS.clear()
    assert _audit() == before
    generated = list(DEMO_DIR.rglob("*data-explorer.csv"))
    assert generated == []
    assert not list(ROOT.glob("*-data-explorer.csv"))


def test_button_key_and_filename_follow_selection() -> None:
    TEST_HOOKS.clear()
    try:
        at = AppTest.from_file(str(APP), default_timeout=120)
        at.run()
        _open_demo_explorer(at)
        button = next(item for item in at.download_button if item.label == EXPLORER_CSV_LABEL)
        da_key = str(button.key)
        da_name = str(getattr(button.proto, "file_name", ""))
        assert "da" in da_key and "2025-W20" in da_key
        if da_name:
            assert da_name == "demo-2025-all-markets-pv500-da-2025-W20-data-explorer.csv"
        next(item for item in at.selectbox if item.label == "Market").set_value("aFRR")
        at.run()
        switched = next(item for item in at.download_button if item.label == EXPLORER_CSV_LABEL)
        afrr_key = str(switched.key)
        assert afrr_key != da_key
        assert "afrr" in afrr_key
        assert "2025-W20" in afrr_key
    finally:
        TEST_HOOKS.clear()
