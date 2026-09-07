from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from ui.flow import (
    RESULTS_VIEW_KEY,
    default_state,
    require_json_compatible,
    set_detail_market,
    set_explorer_market,
    set_technical_market,
    store_snapshot,
    sync_results_view,
    unlock_results,
)
from ui.presentation.tokens import (
    RESERVED_TAB_BODY,
    TECHNICAL_ADVANCED_HEADING,
    TECHNICAL_CHECKS_HEADING,
    TECHNICAL_CONFIGURED_HEADING,
    TECHNICAL_ERROR_BODY,
    TECHNICAL_ERROR_TITLE,
    TECHNICAL_SOURCES_HEADING,
)
from ui.services.artifacts import open_demo_artifacts
from ui.services.form import default_live_form
from ui.services.launch import TEST_HOOKS
from ui.services.paths import JOB_QUERY_KEY
from ui.services.result_format import default_explorer_market
from ui.services.result_technical import (
    TechnicalError,
    format_residual,
    load_technical_details,
)
from ui.services.snapshot import build_snapshot
from ui.tests.result_artifact_fixtures import (
    genuine_two_market_live,
    identified_relocated_one_case_live,
    mismatched_one_case_live,
    mismatched_two_market_on_three_market_demo,
    relocated_demo_result,
)

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ui" / "app.py"
FORBIDDEN = ("frozen", "provenance", "only solver", "legacy solver", "gurobi")


def _combined(at: AppTest) -> str:
    parts: list[str] = []
    for attr in ("header", "subheader", "markdown", "caption", "text", "info", "success", "warning", "error"):
        for item in getattr(at, attr):
            parts.append(str(item.value))
    for item in at.get("html"):
        parts.append(str(item.proto.body))
    for item in getattr(at, "table", []):
        parts.append(str(getattr(item, "value", item)))
    for item in at.get("code"):
        parts.append(str(getattr(item, "value", item)))
    for item in at.expander:
        parts.append(str(item.label))
    return " ".join(parts).lower()


def _one_market_da(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return identified_relocated_one_case_live(tmp_path)


def _two_market_live(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return genuine_two_market_live(tmp_path)


def test_residual_formatting_keeps_nonzero_visible() -> None:
    tiny = 8.881784197001252e-16
    shown = format_residual(tiny, "MWh")
    assert "e-" in shown.lower()
    assert shown.startswith("8.881784")
    assert "0 MWh" not in shown
    assert format_residual(0.0, "MWh") == "0 MWh"
    assert format_residual(0.0) == "0"
    with pytest.raises(ValueError):
        format_residual(True)
    with pytest.raises(ValueError):
        format_residual(math.inf)


def test_demo_technical_values_from_artifacts() -> None:
    payload = load_technical_details(open_demo_artifacts(), market="da")
    values = dict(payload["run_information"])
    assert values["Run ID"] == "demo-2025-all-markets-pv500"
    assert values["Run type"] == "Saved demonstration"
    assert values["Selected markets"] == "Day-ahead · aFRR · mFRR"
    assert values["Interval count"] == "35,040"
    child = dict(payload["child_information"])
    assert child["Selected market"] == "Day-ahead"
    assert child["Child run ID"] == "demo-2025-all-markets-pv500-da-95a81c5b52a3"
    software = dict(payload["software"])
    assert software["HiGHS solver"] == "HiGHS 1.15.1"
    assert software["Model type"] == "Continuous linear program"
    assert software["Columns"] == "280,321"
    assert software["Rows"] == "315,360"
    assert software["Solve duration"] == "4.304 s"
    assert payload["solution_checks"]["ok"] is True
    residuals = {row["Check"]: row["Maximum residual"] for row in payload["solution_checks"]["rows"]}
    assert residuals["Bound residual"] == "0"
    assert "e-" in residuals["Objective residual"].lower()
    sources = payload["data_sources"]
    assert sources["manifest_sha256"] == "e13a1ab320201babf11aad8d047cb58079efa9e644a1a1b77fff16527a0a94a6"
    assert "da_prices_qh" in sources["required_sources"]
    tables = {row["Table"]: row for row in sources["rows"]}
    assert tables["da_prices_qh"]["Hashes"] == "Match"
    assert tables["da_prices_qh"]["Rows"] == "403,964"
    assert payload["parent_events"]["total"] == 14
    assert payload["child_events"]["total"] >= 1
    assert payload["parent_report"]["truncated"] is False
    groups = {title: dict(rows) for title, rows in payload["configured_groups"]}
    assert set(groups) == {"Period", "Asset", "Site", "Market"}
    assert groups["Period"]["Interval count"] == "35,040"
    assert groups["Site"]["PV valuation"] == "Day-ahead prices"
    assert groups["Market"]["Selected market"] == "Day-ahead"
    assert groups["Asset"]["Round-trip efficiency"] == "75.6%"
    assert groups["Asset"]["Usable energy"] == "4.000 MWh"
    assert "DayAheadCase" not in str(payload["configured_groups"])
    assert "discharge_at_rated" not in str(payload["configured_groups"])
    advanced = dict(payload["advanced_groups"][0][1])
    assert advanced["Storage-hours basis"] == "Discharge at rated turbine output"
    assert advanced["Pump efficiency"] == "84%"
    assert "Pond energy" not in advanced
    authored = json.dumps(
        {
            "run": payload["run_information"],
            "child": payload["child_information"],
            "software": payload["software"],
            "checks": payload["solution_checks"],
            "sources": {
                "heading_fields": [
                    payload["data_sources"]["data_vintage"],
                    payload["data_sources"]["pipeline_version"],
                ],
                "rows": payload["data_sources"]["rows"],
            },
        }
    ).lower()
    for needle in FORBIDDEN:
        assert needle not in authored


def test_one_two_three_market_technical_behaviour(tmp_path: Path) -> None:
    demo = load_technical_details(open_demo_artifacts(), market="afrr")
    assert demo["comparison"] is True
    assert demo["markets"] == ["da", "afrr", "mfrr"]
    assert demo["selected_market"] == "afrr"
    assert dict(demo["software"])["Columns"] == "281,416"
    record, result = _one_market_da(tmp_path)
    one = load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    assert one["comparison"] is False
    assert one["markets"] == ["da"]
    assert one["child_information"] is None
    assert one["parent_events"] is None
    two_record, two_result = _two_market_live(tmp_path / "two")
    two = load_technical_details(two_result, market="da", job=two_record, outputs_root=tmp_path / "two")
    assert two["markets"] == ["da", "afrr"]
    assert two["comparison"] is True
    afrr_sources = load_technical_details(open_demo_artifacts(), market="afrr")["data_sources"]
    assert "capacity_blocks.afrr" in afrr_sources["required_sources"]


def test_technical_market_default_and_independence() -> None:
    assert default_explorer_market(["da", "afrr", "mfrr"]) == "da"
    demo = open_demo_artifacts()
    state = default_state()
    unlock_results(state, demo)
    view = sync_results_view(
        state,
        result=demo,
        default_market="afrr",
        allowed_markets=["da", "afrr", "mfrr"],
        default_explorer_market="da",
        default_technical_market="da",
    )
    assert view["selected_detail_market"] == "afrr"
    assert view["selected_explorer_market"] == "da"
    assert view["selected_technical_market"] == "da"
    set_detail_market(state, "mfrr")
    set_explorer_market(state, "afrr")
    set_technical_market(state, "mfrr")
    kept = sync_results_view(
        state,
        result=demo,
        default_market="afrr",
        allowed_markets=["da", "afrr", "mfrr"],
        default_explorer_market="da",
        default_technical_market="da",
    )
    assert kept["selected_detail_market"] == "mfrr"
    assert kept["selected_explorer_market"] == "afrr"
    assert kept["selected_technical_market"] == "mfrr"
    state[RESULTS_VIEW_KEY] = {
        "active_tab": "Technical details",
        "selected_detail_market": "mfrr",
        "selected_explorer_market": "afrr",
        "selected_explorer_week": "2025-W20",
    }
    repaired = sync_results_view(
        state,
        result=demo,
        default_market="afrr",
        allowed_markets=["da", "afrr", "mfrr"],
        default_explorer_market="da",
        default_technical_market="da",
    )
    assert repaired["selected_technical_market"] == "da"
    assert repaired["selected_explorer_market"] == "afrr"
    other = dict(demo)
    other["job_id"] = "demo-other-technical"
    other["output_directory"] = str(Path(demo["output_directory"])) + "-other"
    reset = sync_results_view(
        state,
        result=other,
        default_market="afrr",
        allowed_markets=["da", "afrr", "mfrr"],
        default_explorer_market="da",
        default_technical_market="da",
    )
    assert reset["selected_technical_market"] == "da"
    assert reset["active_tab"] == "Overview"


def test_technical_fail_closed_malformed_and_identity(tmp_path: Path) -> None:
    def mutated(name: str, mutate) -> None:
        record, result = _one_market_da(tmp_path / name)
        mutate(Path(record["output_directory"]))
        with pytest.raises(TechnicalError, match=TECHNICAL_ERROR_BODY):
            load_technical_details(result, market="da", job=record, outputs_root=tmp_path / name)

    mutated("json", lambda out: (out / "run_metadata.json").write_text("{", encoding="utf-8"))

    def wrong_type(out: Path) -> None:
        payload = json.loads((out / "run_metadata.json").read_text(encoding="utf-8"))
        payload["interval_count"] = True
        (out / "run_metadata.json").write_text(json.dumps(payload), encoding="utf-8")

    mutated("types", wrong_type)

    def nonfinite(out: Path) -> None:
        payload = json.loads((out / "run_metadata.json").read_text(encoding="utf-8"))
        payload["solver"]["solve_s"] = float("nan")
        (out / "run_metadata.json").write_text(json.dumps(payload), encoding="utf-8")

    mutated("nonfinite", nonfinite)

    mutated("missing", lambda out: (out / "report.txt").unlink())

    def identity(out: Path) -> None:
        payload = json.loads((out / "run_metadata.json").read_text(encoding="utf-8"))
        payload["run_id"] = "other-id"
        (out / "run_metadata.json").write_text(json.dumps(payload), encoding="utf-8")

    mutated("identity", identity)

    def bad_events(out: Path) -> None:
        (out / "run_events.jsonl").write_text("{not json}\n", encoding="utf-8")

    mutated("events", bad_events)


def test_app_demo_technical_defaults_and_no_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    popen_calls: list[str] = []

    def bang(**_kwargs):
        popen_calls.append("popen")
        raise AssertionError("Technical details must not launch a worker")

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
        next(item for item in at.button if item.label == "Technical details").click()
        at.run()
        assert not at.exception
        text = _combined(at)
        assert TECHNICAL_ERROR_TITLE.lower() not in text
        assert RESERVED_TAB_BODY.lower() not in text
        assert TECHNICAL_CONFIGURED_HEADING.lower() in text
        assert TECHNICAL_ADVANCED_HEADING.lower() in text
        assert TECHNICAL_SOURCES_HEADING.lower() in text
        assert TECHNICAL_CHECKS_HEADING.lower() in text
        assert "highs solver" in text
        assert "day-ahead prices" in text
        assert "period" in text
        html = " ".join(str(item.proto.body) for item in at.get("html")).lower()
        assert "dayaheadcase" not in html
        assert "discharge_at_rated" not in html
        assert "data sources and verification" in text
        assert "provenance" not in [str(item.value).lower() for item in at.subheader]
        assert "gurobi" not in text
        assert next(item for item in at.selectbox if item.label == "Market").value == "Day-ahead"
        view = at.session_state["sib"]["results_view"]
        require_json_compatible(at.session_state["sib"])
        assert view["selected_technical_market"] == "da"
        assert view["selected_detail_market"] == "afrr"
        assert view["selected_explorer_market"] == "da"
        market = next(item for item in at.selectbox if item.label == "Market")
        market.set_value("aFRR")
        at.run()
        assert at.session_state["sib"]["results_view"]["selected_technical_market"] == "afrr"
        next(item for item in at.button if item.label == "Overview").click()
        at.run()
        next(item for item in at.button if item.label == "Technical details").click()
        at.run()
        assert at.session_state["sib"]["results_view"]["selected_technical_market"] == "afrr"
        assert next(item for item in at.selectbox if item.label == "Market").value == "aFRR"
        assert popen_calls == []
    finally:
        TEST_HOOKS.clear()


def test_app_one_market_hides_technical_selector(tmp_path: Path) -> None:
    record, result = _one_market_da(tmp_path)
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    snapshot = build_snapshot(form, demo=False)
    popen_calls: list[str] = []

    def bang(**_kwargs):
        popen_calls.append("popen")
        raise AssertionError("one-market technical must not launch")

    TEST_HOOKS.clear()
    TEST_HOOKS["outputs_root"] = tmp_path
    TEST_HOOKS["popen"] = bang
    try:
        state = default_state()
        store_snapshot(state, snapshot, snapshot["form_fingerprint"])
        state["form"] = form
        state["job"] = record
        unlock_results(state, result)
        at = AppTest.from_file(str(APP), default_timeout=120)
        at.run()
        at.session_state["sib"] = state
        at.query_params[JOB_QUERY_KEY] = record["job_id"]
        at.run()
        next(item for item in at.button if item.label == "Technical details").click()
        at.run()
        assert not at.exception
        assert not any(item.label == "Market" for item in at.selectbox)
        text = _combined(at)
        assert "configured simulation" in text
        assert popen_calls == []
    finally:
        TEST_HOOKS.clear()


def test_mismatched_one_case_identity_is_rejected(tmp_path: Path) -> None:
    record, result = mismatched_one_case_live(tmp_path)
    with pytest.raises(TechnicalError, match=TECHNICAL_ERROR_BODY):
        load_technical_details(result, market="da", job=record, outputs_root=tmp_path)


def test_two_market_claim_on_three_market_tree_is_rejected(tmp_path: Path) -> None:
    record, result = mismatched_two_market_on_three_market_demo(tmp_path)
    with pytest.raises(TechnicalError, match=TECHNICAL_ERROR_BODY):
        load_technical_details(result, market="da", job=record, outputs_root=tmp_path)


def test_result_period_mismatch_is_rejected(tmp_path: Path) -> None:
    record, result = identified_relocated_one_case_live(tmp_path)
    wrong = dict(result)
    wrong["period"] = {"start_date": "2024-01-01", "end_date": "2024-12-31"}
    with pytest.raises(TechnicalError, match=TECHNICAL_ERROR_BODY):
        load_technical_details(wrong, market="da", job=record, outputs_root=tmp_path)


def test_relocated_identified_one_case_is_accepted(tmp_path: Path) -> None:
    record, result = identified_relocated_one_case_live(tmp_path)
    payload = load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    assert payload["comparison"] is False
    assert dict(payload["run_information"])["Run ID"] == record["job_id"]


def test_copied_demo_tree_outside_demo_dir_is_rejected_as_demo(tmp_path: Path) -> None:
    result = relocated_demo_result(tmp_path)
    assert Path(result["output_directory"]).resolve() != Path(
        open_demo_artifacts()["output_directory"]
    ).resolve()
    with pytest.raises(TechnicalError, match=TECHNICAL_ERROR_BODY):
        load_technical_details(result, market="da")


def test_relocated_demo_comparison_is_rejected(tmp_path: Path) -> None:
    result = relocated_demo_result(tmp_path)
    with pytest.raises(TechnicalError, match=TECHNICAL_ERROR_BODY):
        load_technical_details(result, market="da")


def test_genuine_two_market_technical_is_accepted(tmp_path: Path) -> None:
    record, result = genuine_two_market_live(tmp_path)
    payload = load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    assert payload["markets"] == ["da", "afrr"]
    assert payload["comparison"] is True
    assert "mfrr" not in payload["markets"]
    assert not (Path(record["output_directory"]) / "cases" / "mfrr").exists()
