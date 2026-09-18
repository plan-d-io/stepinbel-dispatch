from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from stepinbel.reporting import (
    RUN_ARTIFACT_SCHEMA_VERSION_V2,
    RUN_ARTIFACT_SCHEMA_VERSION_V3,
    validate_run_artifacts,
)
from stepinbel.workflows import execute_case_run
from ui.flow import (
    RESULTS_VIEW_KEY,
    STAGE_CONFIGURE,
    configure_another_run,
    default_state,
    mark_configure_restore,
    require_json_compatible,
    result_open_identity,
    store_snapshot,
    sync_results_view,
    unlock_results,
)
from ui.presentation.components import column_glossary_entries, column_glossary_html
from ui.presentation.tokens import DOWNLOADS_STORED_INTRO, REVIEW_READY_TITLE, WIND_HELP_OFF
from ui.services.artifacts import ERROR_BINDING, bind_exact_result_artifacts, open_demo_artifacts
from ui.services.commitment import (
    COMMITMENT_REVIEW_WARNING_TITLE,
    COMMITMENT_TIME_NOTICE,
    review_commitment_rows,
)
from ui.services.configs import build_simulation_configs
from ui.services.demo import demo_form
from ui.services.explorer_query import load_explorer_week
from ui.services.form import (
    PRESET_CUSTOM,
    PV_REGION_BELGIUM,
    PV_REGION_LABELS,
    PV_REGION_ORDER,
    WIND_PROFILE_LABELS,
    WIND_PROFILE_OFFSHORE_BELGIUM,
    WIND_PROFILE_ONSHORE_BELGIUM,
    WIND_PROFILE_ONSHORE_FLANDERS,
    WIND_PROFILE_ONSHORE_WALLONIA,
    WIND_PROFILE_ORDER,
    apply_form_transitions,
    default_live_form,
    resolve_form_pv_region,
)
from ui.services.launch import TEST_HOOKS
from ui.services.paths import JOB_QUERY_KEY, KIND_CASE, KIND_COMPARISON
from ui.services.request import build_public_request, serialize_public_request
from ui.services.result_downloads import build_download_inventory, build_result_zip
from ui.services.result_format import format_kw_capacity, format_pv_capacity, format_wind_capacity
from ui.services.result_technical import load_technical_details
from ui.services.result_view import ResultViewError, load_result_display, read_result_artifacts
from ui.services.snapshot import (
    FIXED_WIND_PRICE_REQUIRED,
    WIND_CAPACITY_REQUIRED,
    WIND_PROFILE_REQUIRED,
    build_snapshot,
    lightweight_continue_reason,
)
from ui.services.status import SUPPORTED_STATUS_ARTIFACT_SCHEMAS
from ui.tests.result_artifact_fixtures import _planned_job, live_result_record
from ui.tests.test_results_explorer import _annotation_text, _xaxes
from ui.tests.test_results_overview_and_detail import (
    _overview_table,
    _resolved,
    _row,
    _summary,
    _write_case_dir,
    _write_comparison,
)
from ui.views import results as results_view
from ui.views.configure import _KEYS, _widget_values
from ui.views.results_explorer import explorer_chart_model, explorer_figure_from_payload
from ui.views.review import _pv_text, _wind_text

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ui" / "app.py"


def _form(**overrides):
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    form["period_preset"] = PRESET_CUSTOM
    form["start_date"] = "2025-01-15"
    form["end_date"] = "2025-01-15"
    form.update(overrides)
    return form


def _text(at: AppTest) -> str:
    parts: list[str] = []
    for attr in ("header", "subheader", "markdown", "caption", "text", "info", "success", "warning", "error"):
        for item in getattr(at, attr):
            parts.append(str(item.value))
    for item in at.get("html"):
        parts.append(str(item.proto.body))
    for item in at.table:
        parts.append(str(item.value))
    return " ".join(parts)


def _checkbox(at: AppTest, label: str):
    return next(item for item in at.checkbox if item.label == label)


def _number(at: AppTest, label: str):
    return next(item for item in at.number_input if item.label == label)


def _select(at: AppTest, label: str):
    return next(item for item in at.selectbox if item.label == label)


def _button(at: AppTest, label: str):
    return next(item for item in at.button if item.label == label)


def _zeros(count: int) -> list[float]:
    return [0.0] * count


def _explorer_payload(*, pv: bool = False, wind: bool = False, count: int = 4) -> dict:
    zeros = _zeros(count)
    dispatch = {
        "p_pump_mw": zeros,
        "p_turbine_mw": zeros,
        "reservoir_end_mwh": zeros,
        "market_buy_price_eur_mwh": zeros,
        "market_sell_price_eur_mwh": zeros,
        "market_energy_net_eur": zeros,
        "total_revenue_eur": zeros,
        "p_pump_grid_mw": zeros,
    }
    if pv:
        dispatch.update(
            {
                "pv_available_mw": zeros,
                "pv_to_pump_mw": zeros,
                "pv_export_mw": zeros,
                "pv_curtail_mw": zeros,
                "pv_revenue_eur": zeros,
            }
        )
    if wind:
        dispatch.update(
            {
                "wind_available_mw": zeros,
                "wind_to_pump_mw": zeros,
                "wind_export_mw": zeros,
                "wind_curtail_mw": zeros,
                "wind_revenue_eur": zeros,
            }
        )
    return {
        "dispatch": dispatch,
        "row_count": count,
        "pv_included": pv,
        "wind_included": wind,
        "e_max_mwh": 4.0,
        "effective_grid_import_mw": 1.0,
        "effective_grid_export_mw": 1.0,
        "has_capacity": False,
        "capacity": None,
        "market": "da",
        "x_index": list(range(count)),
        "hover_labels": [f"t{index}" for index in range(count)],
        "tick_vals": [0],
        "tick_text": ["day"],
    }


def _execute_da(tmp_path: Path, form: dict, *, job_id: str) -> tuple[dict, dict, dict]:
    snapshot = build_snapshot(form, demo=False)
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_CASE, markets=["da"])
    output = Path(record["output_directory"])
    request = build_public_request(snapshot, output_directory=output, run_id=job_id)
    execute_case_run(request)
    validate_run_artifacts(output)
    return record, live_result_record(record, snapshot), snapshot


def test_wind_disabled_keeps_schema_v1_and_omits_wind_fields(tmp_path: Path) -> None:
    form = _form()
    assert form["wind_enabled"] is False
    snapshot = build_snapshot(form, demo=False)
    assert snapshot["site"]["wind_capacity_kw"] == 0.0
    request = build_public_request(
        snapshot,
        output_directory=tmp_path / "off",
        run_id="stepinbel-20250115T000000Z-abcd1234",
    )
    payload = serialize_public_request(request)
    assert payload["request_schema_version"] == 1
    assert payload["artifact_schema_version"] == 1
    assert "wind_capacity_kw" not in payload["config"]["site"]
    assert review_commitment_rows(snapshot, market_count=1) is None


def test_enabled_wind_round_trips_through_snapshot_and_request(tmp_path: Path) -> None:
    form = _form(
        wind_enabled=True,
        wind_capacity_kw=1000.0,
        wind_profile_id=WIND_PROFILE_ONSHORE_BELGIUM,
        wind_revenue_mode="da",
    )
    snapshot = build_snapshot(form, demo=False)
    assert snapshot["site"]["wind_capacity_kw"] == 1000.0
    assert snapshot["site"]["wind_profile_id"] == "onshore_belgium"
    assert snapshot["site"]["wind_revenue_mode"] == "da"
    restored = build_snapshot(snapshot["form"], demo=False)
    assert restored["site"] == snapshot["site"]
    request = build_public_request(
        snapshot,
        output_directory=tmp_path / "wind-da",
        run_id="stepinbel-20250115T000000Z-abcd1234",
    )
    payload = serialize_public_request(request)
    assert payload["request_schema_version"] == 3
    assert payload["artifact_schema_version"] == RUN_ARTIFACT_SCHEMA_VERSION_V3
    assert payload["config"]["site"]["wind_capacity_kw"] == 1000.0
    assert payload["config"]["site"]["wind_profile_id"] == "onshore_belgium"
    assert payload["config"]["site"]["wind_revenue_mode"] == "da"


@pytest.mark.parametrize(
    ("profile_id", "label"),
    [
        (WIND_PROFILE_ONSHORE_BELGIUM, "Onshore Belgium"),
        (WIND_PROFILE_OFFSHORE_BELGIUM, "Offshore Belgium"),
        (WIND_PROFILE_ONSHORE_FLANDERS, "Onshore Flanders"),
        (WIND_PROFILE_ONSHORE_WALLONIA, "Onshore Wallonia"),
    ],
)
def test_wind_profile_labels_map_to_stored_ids(profile_id: str, label: str) -> None:
    assert WIND_PROFILE_LABELS[profile_id] == label
    assert WIND_PROFILE_ORDER[0] == WIND_PROFILE_ONSHORE_BELGIUM
    snapshot = build_snapshot(_form(wind_enabled=True, wind_profile_id=profile_id), demo=False)
    assert snapshot["site"]["wind_profile_id"] == profile_id
    configs = build_simulation_configs(snapshot["form"])
    assert configs[0][1].site.wind_profile_id == profile_id


def test_fixed_wind_valuation_request_and_invalid_inputs(tmp_path: Path) -> None:
    missing = _form(wind_enabled=True, wind_revenue_mode="fixed", wind_fixed_price=None)
    assert lightweight_continue_reason(missing) == FIXED_WIND_PRICE_REQUIRED
    zero = _form(wind_enabled=True, wind_capacity_kw=0.0)
    assert lightweight_continue_reason(zero) == WIND_CAPACITY_REQUIRED
    bad_profile = _form(wind_enabled=True, wind_profile_id="not_a_fleet")
    assert lightweight_continue_reason(bad_profile) == WIND_PROFILE_REQUIRED
    priced = _form(
        wind_enabled=True,
        wind_revenue_mode="fixed",
        wind_fixed_price=42.0,
        wind_capacity_kw=750.0,
    )
    snapshot = build_snapshot(priced, demo=False)
    assert snapshot["site"]["wind_revenue_mode"] == "fixed"
    assert snapshot["site"]["wind_fixed_price_eur_mwh"] == 42.0
    request = build_public_request(
        snapshot,
        output_directory=tmp_path / "wind-fixed",
        run_id="stepinbel-20250115T000000Z-abcd1234",
    )
    payload = serialize_public_request(request)
    assert payload["request_schema_version"] == 3
    assert payload["config"]["site"]["wind_fixed_price_eur_mwh"] == 42.0


def test_wind_coverage_failure_prevents_launch() -> None:
    from datetime import timedelta

    from ui.services.period import latest_inclusive_end

    da_end = latest_inclusive_end(["da"], pv_enabled=False, wind_enabled=False, year=2026)
    wind_end = latest_inclusive_end(["da"], pv_enabled=False, wind_enabled=True, year=2026)
    assert wind_end < da_end
    gap = (wind_end + timedelta(days=1)).isoformat()
    form = _form(
        wind_enabled=True,
        period_preset=PRESET_CUSTOM,
        start_date=gap,
        end_date=gap,
    )
    with pytest.raises(ValueError, match="chosen wind data"):
        build_snapshot(form, demo=False)
    assert lightweight_continue_reason(form) is None


def test_wind_with_and_without_pv_and_commitment(tmp_path: Path) -> None:
    wind_only = build_snapshot(_form(wind_enabled=True, pv_enabled=False), demo=False)
    both = build_snapshot(
        _form(wind_enabled=True, pv_enabled=True, pv_ac_kw=500.0),
        demo=False,
    )
    assert wind_only["site"]["pv_ac_kw"] == 0.0
    assert both["site"]["pv_ac_kw"] == 500.0
    assert both["site"]["wind_capacity_kw"] == 1000.0
    committed = build_snapshot(_form(wind_enabled=True, fixed_speed_pump=True), demo=False)
    plain = build_public_request(
        wind_only,
        output_directory=tmp_path / "wind-only",
        run_id="stepinbel-20250115T000000Z-abcd1234",
    )
    milp = build_public_request(
        committed,
        output_directory=tmp_path / "wind-milp",
        run_id="stepinbel-20250115T000000Z-abcd1234",
    )
    assert serialize_public_request(plain)["request_schema_version"] == 3
    milp_payload = serialize_public_request(milp)
    assert milp_payload["request_schema_version"] == 3
    assert milp_payload["config"]["machine_commitment"]["fixed_speed_pump"] is True
    assert review_commitment_rows(wind_only, market_count=1) is None
    assert review_commitment_rows(committed, market_count=1) is not None


def test_configure_and_review_preserve_enabled_wind() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert _checkbox(at, "Include co-located wind").value is False
    assert WIND_HELP_OFF in _text(at)
    _checkbox(at, "Include co-located wind").set_value(True)
    at.run()
    assert not at.exception
    assert _number(at, "Wind capacity (kW)").value == 1000.0
    assert _select(at, "Wind profile").value == "Onshore Belgium"
    _select(at, "Export valuation").set_value("Fixed price")
    at.run()
    _number(at, "Fixed wind export price (EUR/MWh)").set_value(18.5)
    at.run()
    _select(at, "Wind profile").set_value("Onshore Flanders")
    at.run()
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    text = _text(at)
    assert REVIEW_READY_TITLE in text
    assert "Wind" in [item.value for item in at.subheader]
    assert "1,000 kW" in text
    assert "Onshore Flanders" in text
    assert "18.5" in text
    assert COMMITMENT_TIME_NOTICE not in text
    assert COMMITMENT_REVIEW_WARNING_TITLE not in text
    form = at.session_state["sib"]["form"]
    assert form["wind_enabled"] is True
    assert form["wind_profile_id"] == WIND_PROFILE_ONSHORE_FLANDERS
    assert form["wind_revenue_mode"] == "fixed"
    assert form["wind_fixed_price"] == 18.5
    _button(at, "Back").click()
    at.run()
    assert not at.exception
    assert _checkbox(at, "Include co-located wind").value is True
    assert _select(at, "Wind profile").value == "Onshore Flanders"
    assert _select(at, "Export valuation").value == "Fixed price"
    assert _number(at, "Fixed wind export price (EUR/MWh)").value == 18.5


def test_configure_maps_all_profile_labels() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    _checkbox(at, "Include co-located wind").set_value(True)
    at.run()
    for profile_id, label in WIND_PROFILE_LABELS.items():
        _select(at, "Wind profile").set_value(label)
        at.run()
        assert at.session_state["sib"]["form"]["wind_profile_id"] == profile_id


PV_TABLE_COLUMNS = (
    "PV revenue (EUR)",
    "PV self-consumed (MWh)",
    "PV self-consumed (%)",
    "PV exported (MWh)",
    "PV curtailed (MWh)",
)
WIND_TABLE_COLUMNS = (
    "Wind revenue (EUR)",
    "Wind self-consumed (MWh)",
    "Wind self-consumed (%)",
    "Wind exported (MWh)",
    "Wind curtailed (MWh)",
)
PV_GLOSSARY_NAMES = (
    "PV revenue",
    "PV self-consumed",
    "PV self-consumed (%)",
    "PV exported",
    "PV curtailed",
)
WIND_GLOSSARY_NAMES = (
    "Wind revenue",
    "Wind self-consumed",
    "Wind self-consumed (%)",
    "Wind exported",
    "Wind curtailed",
)


def _glossary_names(*, pv_included: bool, wind_included: bool) -> list[str]:
    return [name for name, _ in column_glossary_entries(pv_included=pv_included, wind_included=wind_included)]


def _assert_resource_columns(table: dict[str, list[str]], glossary: str, *, pv: bool, wind: bool) -> None:
    names = _glossary_names(pv_included=pv, wind_included=wind)
    for column in PV_TABLE_COLUMNS:
        assert (column in table) is pv
    for column in WIND_TABLE_COLUMNS:
        assert (column in table) is wind
    for name in PV_GLOSSARY_NAMES:
        assert (name in names) is pv
        assert (f"<dt><strong>{name}</strong></dt>" in glossary) is pv
    for name in WIND_GLOSSARY_NAMES:
        assert (name in names) is wind
        assert (f"<dt><strong>{name}</strong></dt>" in glossary) is wind


def _wind_site(resolved: dict, *, capacity: float = 1000.0, profile: str = "onshore_belgium") -> dict:
    resolved["config"]["site"]["wind_capacity_kw"] = capacity
    resolved["config"]["site"]["wind_profile_id"] = profile
    return resolved


def test_overview_and_detail_include_wind_only_when_configured(tmp_path: Path) -> None:
    _write_case_dir(tmp_path / "off", "da", resolved=_resolved(market="da", pv_kw=0.0))
    off = read_result_artifacts(
        kind=KIND_CASE,
        markets=["da"],
        directory=tmp_path / "off",
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert off["wind_included"] is False
    assert off["pv_included"] is False
    assert off["header"].get("wind") is None
    off_table = _overview_table(off["rows"], one_market=True, pv_included=False, wind_included=False)
    _assert_resource_columns(
        off_table,
        column_glossary_html(pv_included=False, wind_included=False),
        pv=False,
        wind=False,
    )

    resolved = _wind_site(_resolved(market="da", pv_kw=0.0))
    _write_case_dir(
        tmp_path / "on",
        "da",
        resolved=resolved,
        summary=_summary(
            "da",
            wind_revenue_eur=12.5,
            wind_available_mwh=10.0,
            wind_self_consumed_mwh=2.5,
            wind_exported_mwh=6.0,
            wind_curtailed_mwh=1.5,
        ),
    )
    on = read_result_artifacts(
        kind=KIND_CASE,
        markets=["da"],
        directory=tmp_path / "on",
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert on["wind_included"] is True
    assert on["pv_included"] is False
    assert on["header"]["wind"] == "1,000 kW · Onshore Belgium"
    assert on["rows"][0]["formatted"]["wind"] == "EUR 12.50"
    assert on["rows"][0]["formatted"]["wind_self"] == "2.500 MWh"
    assert on["rows"][0]["formatted"]["wind_self_share"] == "25.0%"
    assert on["rows"][0]["formatted"]["wind_export"] == "6.000 MWh"
    assert on["rows"][0]["formatted"]["wind_curtail"] == "1.500 MWh"
    table = _overview_table(on["rows"], one_market=True, pv_included=False, wind_included=True)
    assert table["Wind revenue (EUR)"] == ["12.50"]
    assert table["Wind self-consumed (MWh)"] == ["2.500"]
    assert table["Wind self-consumed (%)"] == ["25.0%"]
    assert table["Wind exported (MWh)"] == ["6.000"]
    assert table["Wind curtailed (MWh)"] == ["1.500"]
    glossary = column_glossary_html(pv_included=False, wind_included=True)
    _assert_resource_columns(table, glossary, pv=False, wind=True)
    child = on["children"]["da"]
    assert child["formatted"]["wind_available"] == "10.000 MWh"
    assert child["formatted"]["wind_self"] == "2.500 MWh"

    missing = _wind_site(_resolved(market="da", pv_kw=0.0))
    _write_case_dir(tmp_path / "missing", "da", resolved=missing, summary=_summary("da"))
    with pytest.raises(ResultViewError):
        read_result_artifacts(
            kind=KIND_CASE,
            markets=["da"],
            directory=tmp_path / "missing",
            source="live",
            period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
        )


def test_overview_resource_columns_follow_configuration(tmp_path: Path) -> None:
    _write_case_dir(tmp_path / "pv", "da", resolved=_resolved(market="da", pv_kw=500.0))
    pv_only = read_result_artifacts(
        kind=KIND_CASE,
        markets=["da"],
        directory=tmp_path / "pv",
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert pv_only["pv_included"] is True
    assert pv_only["wind_included"] is False
    pv_table = _overview_table(pv_only["rows"], one_market=True, pv_included=True, wind_included=False)
    assert pv_table["PV revenue (EUR)"] == ["50.00"]
    assert pv_table["PV self-consumed (MWh)"] == ["1.000"]
    assert pv_table["PV self-consumed (%)"] == ["25.0%"]
    assert pv_table["PV exported (MWh)"] == ["2.000"]
    assert pv_table["PV curtailed (MWh)"] == ["1.000"]
    _assert_resource_columns(
        pv_table,
        column_glossary_html(pv_included=True, wind_included=False),
        pv=True,
        wind=False,
    )

    both_resolved = _wind_site(_resolved(market="da", pv_kw=500.0, pv_region="Flanders"))
    _write_case_dir(
        tmp_path / "both",
        "da",
        resolved=both_resolved,
        summary=_summary(
            "da",
            wind_revenue_eur=8.0,
            wind_available_mwh=4.0,
            wind_self_consumed_mwh=1.0,
            wind_exported_mwh=2.0,
            wind_curtailed_mwh=1.0,
        ),
    )
    both = read_result_artifacts(
        kind=KIND_CASE,
        markets=["da"],
        directory=tmp_path / "both",
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert both["pv_included"] is True
    assert both["wind_included"] is True
    assert both["header"]["pv"] == "500 kW · Flanders"
    both_table = _overview_table(both["rows"], one_market=True, pv_included=True, wind_included=True)
    for column in (*PV_TABLE_COLUMNS, *WIND_TABLE_COLUMNS):
        assert column in both_table
    _assert_resource_columns(
        both_table,
        column_glossary_html(pv_included=True, wind_included=True),
        pv=True,
        wind=True,
    )

    zero_resolved = _wind_site(_resolved(market="da", pv_kw=100.0))
    _write_case_dir(
        tmp_path / "zero",
        "da",
        resolved=zero_resolved,
        summary=_summary(
            "da",
            pv_revenue_eur=0.0,
            pv_available_mwh=0.0,
            pv_self_consumed_mwh=0.0,
            pv_exported_mwh=0.0,
            pv_curtailed_mwh=0.0,
            wind_revenue_eur=0.0,
            wind_available_mwh=0.0,
            wind_self_consumed_mwh=0.0,
            wind_exported_mwh=0.0,
            wind_curtailed_mwh=0.0,
        ),
    )
    zero = read_result_artifacts(
        kind=KIND_CASE,
        markets=["da"],
        directory=tmp_path / "zero",
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert zero["pv_included"] is True
    assert zero["wind_included"] is True
    zero_table = _overview_table(zero["rows"], one_market=True, pv_included=True, wind_included=True)
    assert "PV revenue (EUR)" in zero_table
    assert "Wind revenue (EUR)" in zero_table
    assert zero_table["PV revenue (EUR)"] == ["0.00"]
    assert zero_table["Wind revenue (EUR)"] == ["0.00"]
    assert zero["rows"][0]["formatted"]["pv_self_share"] == "—"
    assert zero["rows"][0]["formatted"]["wind_self_share"] == "—"

    rows = [
        _row(
            "mfrr",
            1,
            90.0,
            0.0,
            wind_revenue_eur=5.0,
            wind_available_mwh=10.0,
            wind_self_consumed_mwh=2.5,
            wind_exported_mwh=6.0,
            wind_curtailed_mwh=1.5,
        ),
        _row(
            "afrr",
            2,
            70.0,
            -20.0,
            wind_revenue_eur=4.0,
            wind_available_mwh=10.0,
            wind_self_consumed_mwh=1.0,
            wind_exported_mwh=7.0,
            wind_curtailed_mwh=2.0,
        ),
    ]
    _write_comparison(
        tmp_path / "multi",
        rows,
        highest="mfrr",
        resolved=_wind_site(_resolved(market="mfrr", pv_kw=500.0, activation="passive")),
    )
    multi = read_result_artifacts(
        kind=KIND_COMPARISON,
        markets=["mfrr", "afrr"],
        directory=tmp_path / "multi",
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert multi["one_market"] is False
    assert multi["pv_included"] is True
    assert multi["wind_included"] is True
    multi_table = _overview_table(
        multi["rows"], one_market=False, pv_included=True, wind_included=True
    )
    _assert_resource_columns(
        multi_table,
        column_glossary_html(pv_included=True, wind_included=True),
        pv=True,
        wind=True,
    )
    assert multi_table["Wind self-consumed (MWh)"] == ["1.000", "2.500"]
    assert multi_table["Wind exported (MWh)"] == ["7.000", "6.000"]
    assert multi_table["Wind curtailed (MWh)"] == ["2.000", "1.500"]

    none_rows = [_row("da", 1, 100.0, 0.0), _row("mfrr", 2, 80.0, -20.0)]
    _write_comparison(
        tmp_path / "none",
        none_rows,
        highest="da",
        resolved=_resolved(market="da", pv_kw=0.0),
    )
    none = read_result_artifacts(
        kind=KIND_COMPARISON,
        markets=["da", "mfrr"],
        directory=tmp_path / "none",
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    none_table = _overview_table(
        none["rows"], one_market=False, pv_included=False, wind_included=False
    )
    _assert_resource_columns(
        none_table,
        column_glossary_html(pv_included=False, wind_included=False),
        pv=False,
        wind=False,
    )


def test_explorer_wind_panel_and_synchronized_axes() -> None:
    none = explorer_chart_model(_explorer_payload())
    assert [panel.title for panel in none.panels if panel.title == "Wind allocation"] == []
    assert [panel.title for panel in none.panels if panel.title == "PV allocation"] == []
    wind_only = explorer_chart_model(_explorer_payload(wind=True))
    titles = [panel.title for panel in wind_only.panels]
    assert "Wind allocation" in titles
    assert "PV allocation" not in titles
    both = explorer_chart_model(_explorer_payload(pv=True, wind=True))
    both_titles = [panel.title for panel in both.panels]
    assert both_titles.index("PV allocation") < both_titles.index("Wind allocation")
    figure = explorer_figure_from_payload(_explorer_payload(pv=True, wind=True))
    axes = _xaxes(figure)
    assert all(axis.get("matches") == "x" for axis in axes[1:])
    assert "Wind allocation" in _annotation_text(figure)
    assert "PV allocation" in _annotation_text(figure)
    demo = explorer_chart_model(
        load_explorer_week(open_demo_artifacts(), market="da", week_id="2025-W20")
    )
    assert all(panel.title != "Wind allocation" for panel in demo.panels)


def test_schema_v3_lp_and_milp_bind_and_open(tmp_path: Path) -> None:
    lp_record, lp_result, lp_snapshot = _execute_da(
        tmp_path / "lp",
        _form(wind_enabled=True),
        job_id="stepinbel-20250115T120000Z-aaaa0001",
    )
    bound = bind_exact_result_artifacts(lp_result, job=lp_record, outputs_root=tmp_path / "lp")
    assert bound.result["job_id"] == lp_record["job_id"]
    metadata = json.loads((Path(lp_record["output_directory"]) / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["artifact_schema_version"] == RUN_ARTIFACT_SCHEMA_VERSION_V3
    assert metadata["solver"]["formulation"] == "lp"
    display = load_result_display(lp_result, job=lp_record, outputs_root=tmp_path / "lp")
    assert display["wind_included"] is True
    assert display["header"]["wind"] is not None
    technical = load_technical_details(lp_result, market="da", job=lp_record, outputs_root=tmp_path / "lp")
    site = dict(dict(technical["configured_groups"])["Site"])
    assert "Wind" in site
    assert "Onshore Belgium" in site["Wind"]
    inventory = build_download_inventory(lp_result, job=lp_record, outputs_root=tmp_path / "lp")
    assert inventory
    package = build_result_zip(lp_result, job=lp_record, outputs_root=tmp_path / "lp")
    assert package
    assert RUN_ARTIFACT_SCHEMA_VERSION_V3 in SUPPORTED_STATUS_ARTIFACT_SCHEMAS
    assert DOWNLOADS_STORED_INTRO == "The output results can also be found at:"

    milp_record, milp_result, _snapshot = _execute_da(
        tmp_path / "milp",
        _form(wind_enabled=True, fixed_speed_pump=True),
        job_id="stepinbel-20250115T120000Z-aaaa0002",
    )
    milp_meta = json.loads((Path(milp_record["output_directory"]) / "run_metadata.json").read_text(encoding="utf-8"))
    assert milp_meta["artifact_schema_version"] == RUN_ARTIFACT_SCHEMA_VERSION_V3
    assert milp_meta["solver"]["formulation"] == "milp"
    bind_exact_result_artifacts(milp_result, job=milp_record, outputs_root=tmp_path / "milp")
    milp_tech = load_technical_details(milp_result, market="da", job=milp_record, outputs_root=tmp_path / "milp")
    software = dict(milp_tech["software"])
    assert software["Formulation"] == "MILP"
    assert "max_wind_residual_mw" in json.dumps(milp_meta["feasibility"])


def test_existing_schema_v1_demo_and_schema_v2_still_open(tmp_path: Path) -> None:
    demo = open_demo_artifacts()
    bound = bind_exact_result_artifacts(demo)
    assert bound.result["source"] == "demo"
    payload = load_result_display(demo)
    assert payload["wind_included"] is False
    assert payload["header"].get("wind") is None
    v2_record, v2_result, _snapshot = _execute_da(
        tmp_path,
        _form(fixed_speed_pump=True),
        job_id="stepinbel-20250115T120000Z-aaaa0003",
    )
    metadata = json.loads((Path(v2_record["output_directory"]) / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["artifact_schema_version"] == RUN_ARTIFACT_SCHEMA_VERSION_V2
    bind_exact_result_artifacts(v2_result, job=v2_record, outputs_root=tmp_path)
    v2_display = load_result_display(v2_result, job=v2_record, outputs_root=tmp_path)
    assert v2_display["wind_included"] is False


def test_new_result_identity_resets_wind_results_state() -> None:
    demo = open_demo_artifacts()
    state = default_state()
    unlock_results(state, demo)
    first = sync_results_view(
        state,
        result=demo,
        default_market="afrr",
        allowed_markets=["da", "mfrr", "afrr"],
        default_explorer_market="da",
    )
    state[RESULTS_VIEW_KEY] = {
        **first,
        "active_tab": "Data explorer",
        "selected_explorer_week": "2025-W20",
    }
    other = dict(demo)
    other["job_id"] = "demo-other-wind"
    other["output_directory"] = str(Path(demo["output_directory"])) + "-other"
    reset = sync_results_view(
        state,
        result=other,
        default_market="da",
        allowed_markets=["da"],
        default_explorer_market="da",
    )
    assert reset["active_tab"] == "Overview"
    assert reset["selected_explorer_market"] == "da"
    assert result_open_identity(other) != result_open_identity(demo)


def test_tampered_schema_v3_fails_closed(tmp_path: Path) -> None:
    record, result, _snapshot = _execute_da(
        tmp_path,
        _form(wind_enabled=True),
        job_id="stepinbel-20250115T120000Z-aaaa0004",
    )
    summary_path = Path(record["output_directory"]) / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["wind_revenue_eur"] = "not-a-number"
    summary_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=ERROR_BINDING):
        bind_exact_result_artifacts(result, job=record, outputs_root=tmp_path)
    swapped = dict(result)
    swapped["output_directory"] = str(tmp_path / "missing")
    with pytest.raises(ValueError, match=ERROR_BINDING):
        bind_exact_result_artifacts(swapped, job=record, outputs_root=tmp_path)


def test_results_tabs_do_not_launch_or_rerun(tmp_path: Path) -> None:
    record, result, snapshot = _execute_da(
        tmp_path,
        _form(wind_enabled=True),
        job_id="stepinbel-20250115T120000Z-aaaa0005",
    )
    calls: list[str] = []

    def bang(**_kwargs):
        calls.append("popen")
        raise AssertionError("Results tabs must not launch a worker")

    TEST_HOOKS.clear()
    TEST_HOOKS["popen"] = bang
    TEST_HOOKS["outputs_root"] = tmp_path
    try:
        with patch("stepinbel.workflows.execute_case_run") as execute_run:
            state = default_state()
            store_snapshot(state, snapshot, snapshot["form_fingerprint"])
            state["form"] = snapshot["form"]
            state["job"] = record
            unlock_results(state, result)
            at = AppTest.from_file(str(APP), default_timeout=60)
            at.run()
            at.session_state["sib"] = state
            at.query_params[JOB_QUERY_KEY] = record["job_id"]
            at.run()
            assert not at.exception
            text = _text(at)
            assert "Wind" in text
            assert "Onshore Belgium" in text
            _button(at, "Market detail").click()
            at.run()
            _button(at, "Data explorer").click()
            at.run()
            spec = json.loads(at.get("plotly_chart")[0].proto.spec)
            assert "Wind allocation" in _annotation_text(spec)
            assert all(axis.get("matches") == "x" for axis in _xaxes(spec)[1:])
            _button(at, "Technical details").click()
            at.run()
            _button(at, "Downloads").click()
            at.run()
            assert not at.exception
            assert DOWNLOADS_STORED_INTRO in _text(at)
            assert calls == []
            execute_run.assert_not_called()
            require_json_compatible(at.session_state["sib"])
            _button(at, "1  Configure").click()
            at.run()
            assert at.session_state["sib"]["stage"] == 1
            assert _checkbox(at, "Include co-located wind").value is True
    finally:
        TEST_HOOKS.clear()
    assert "Popen" not in inspect.getsource(results_view)


def test_demo_market_order_and_configure_another_run_keep_working() -> None:
    demo = open_demo_artifacts()
    payload = load_result_display(demo)
    assert payload["markets"] == ["da", "afrr", "mfrr"]
    state = default_state()
    store_snapshot(state, {"ok": True}, "x")
    unlock_results(state, demo)
    state["job"] = {"job_id": "demo"}
    configure_another_run(state)
    assert state["stage"] == STAGE_CONFIGURE
    assert state["job"] is None
    assert state["result"] is None
    from ui.services.result_format import default_explorer_market

    assert default_explorer_market(["mfrr", "afrr", "da"]) == "da"


def test_pv_region_labels_map_to_stored_values() -> None:
    expected = {
        "Belgium": "Belgium",
        "Flanders": "Flanders",
        "Wallonia": "Wallonia",
        "Brussels": "Brussels",
        "Antwerp": "Antwerp",
        "East-Flanders": "East Flanders",
        "Flemish-Brabant": "Flemish Brabant",
        "Limburg": "Limburg",
        "West-Flanders": "West Flanders",
        "Hainaut": "Hainaut",
        "Liège": "Liège",
        "Luxembourg": "Luxembourg",
        "Namur": "Namur",
        "Walloon-Brabant": "Walloon Brabant",
    }
    assert PV_REGION_LABELS == expected
    assert PV_REGION_ORDER[0] == PV_REGION_BELGIUM
    assert default_live_form()["pv_region"] == PV_REGION_BELGIUM
    assert demo_form()["pv_region"] == "Belgium"
    for stored, label in PV_REGION_LABELS.items():
        assert resolve_form_pv_region(label) == stored
        snapshot = build_snapshot(_form(pv_enabled=True, pv_ac_kw=500.0, pv_region=stored), demo=False)
        assert snapshot["site"]["pv_region"] == stored
        assert build_simulation_configs(snapshot["form"])[0][1].site.pv_region == stored


def test_older_pv_region_state_repairs_to_belgium() -> None:
    form = _form(pv_enabled=True, pv_ac_kw=500.0)
    form.pop("pv_region", None)
    repaired = apply_form_transitions(None, form)
    assert repaired["pv_region"] == PV_REGION_BELGIUM
    assert _widget_values(form)[_KEYS["pv_region"]] == "Belgium"
    snapshot = build_snapshot(form, demo=False)
    assert snapshot["site"]["pv_region"] == "Belgium"


def test_pv_region_is_carried_on_requests_and_invalid_region_fails(tmp_path: Path) -> None:
    snapshot = build_snapshot(_form(pv_enabled=True, pv_ac_kw=500.0, pv_region="Liège"), demo=False)
    request = build_public_request(
        snapshot,
        output_directory=tmp_path / "pv-liege",
        run_id="stepinbel-20250115T000000Z-abcd1234",
    )
    payload = serialize_public_request(request)
    assert payload["config"]["site"]["pv_region"] == "Liège"
    with pytest.raises(ValueError):
        build_snapshot(_form(pv_enabled=True, pv_ac_kw=500.0, pv_region="Not-A-Region"), demo=False)


def test_pv_region_survives_configure_review_back_and_results_configure() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    _checkbox(at, "Include co-located PV").set_value(True)
    at.run()
    assert _select(at, "PV region").value == "Belgium"
    _number(at, "Installed PV (kW)").set_value(1000.0)
    _select(at, "PV region").set_value("East Flanders")
    at.run()
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    snapshot = at.session_state["sib"]["snapshot"]
    assert snapshot["site"]["pv_region"] == "East-Flanders"
    assert snapshot["site"]["pv_ac_kw"] == 1000.0
    text = _text(at)
    assert "1,000 kW" in text
    assert "East Flanders" in text
    _button(at, "Back").click()
    at.run()
    assert _checkbox(at, "Include co-located PV").value is True
    assert _select(at, "PV region").value == "East Flanders"
    assert _number(at, "Installed PV (kW)").value == 1000.0

    restored = default_state()
    store_snapshot(restored, snapshot, snapshot["form_fingerprint"])
    restored["form"] = snapshot["form"]
    restored["stage"] = STAGE_CONFIGURE
    restored["max_stage"] = STAGE_CONFIGURE
    mark_configure_restore(restored)
    at2 = AppTest.from_file(str(APP), default_timeout=60)
    at2.session_state["sib"] = restored
    at2.run()
    assert not at2.exception
    assert _checkbox(at2, "Include co-located PV").value is True
    assert _select(at2, "PV region").value == "East Flanders"


def test_pv_and_wind_capacities_use_identical_thousands_formatting() -> None:
    assert format_kw_capacity(1000) == "1,000 kW"
    assert format_pv_capacity(1000) == "1,000 kW"
    assert format_wind_capacity(1000) == "1,000 kW"
    assert format_pv_capacity(1000) == format_wind_capacity(1000)
    assert format_pv_capacity(1000, "Flanders") == "1,000 kW · Flanders"
    snapshot = build_snapshot(
        _form(pv_enabled=True, pv_ac_kw=1000.0, pv_region="Flanders", wind_enabled=True),
        demo=False,
    )
    assert _pv_text(snapshot["site"], demo=False) == "1,000 kW Flanders, day-ahead valuation"
    assert _wind_text(snapshot["site"]).startswith("1,000 kW · Onshore Belgium")
