from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from streamlit.testing.v1 import AppTest

from ui.flow import (
    RESULTS_VIEW_KEY,
    STAGE_RESULTS,
    default_state,
    require_json_compatible,
    result_open_identity,
    store_snapshot,
    sync_results_view,
    unlock_results,
)
from ui.presentation.components import column_glossary_html
from ui.presentation.tokens import RESULT_TABS, RESERVED_TAB_BODY
from ui.views.results_overview import _overview_table
from ui.services.artifacts import open_demo_artifacts
from ui.services.form import default_live_form
from ui.services.jobs import (
    LAUNCH_LAUNCHED,
    atomic_write_json,
    iso_utc,
    job_paths,
    job_record,
    write_job_record,
)
from ui.services.launch import TEST_HOOKS
from ui.services.paths import CANONICAL_MARKETS, JOB_QUERY_KEY, KIND_CASE, KIND_COMPARISON, DEMO_COMPARISON_DIR
from ui.services.result_format import (
    RESULTS_DISPLAY_MARKETS,
    display_market_keys,
    format_eur,
    format_eur_amount,
    format_pv_self_share,
)
from ui.services.result_view import (
    CAPACITY_PREVIEW_LIMIT,
    ERROR_RESULTS_BODY,
    ERROR_RESULTS_TITLE,
    ResultViewError,
    load_result_display,
    read_result_artifacts,
)
from ui.services.snapshot import build_snapshot, snapshot_digest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ui" / "app.py"


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
    return " ".join(parts)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _resolved(
    *,
    market: str,
    pump: float = 1.0,
    turbine: float = 1.0,
    grid_import: float = 1.0,
    grid_export: float = 1.0,
    pv_kw: float = 500.0,
    hours: float | None = 4.0,
    activation: str = "balanced",
    e_max: float = 4.444,
) -> dict[str, Any]:
    return {
        "e_max_mwh": e_max,
        "config": {
            "asset": {
                "power_pump_mw": pump,
                "power_turbine_mw": turbine,
                "storage_hours": hours,
            },
            "site": {
                "grid_import_mw": grid_import,
                "grid_export_mw": grid_export,
                "pv_ac_kw": pv_kw,
            },
            "market_case": {"activation_profile": activation, "market": market},
            "period": {"start_date": "2025-01-01", "end_date_inclusive": "2025-12-31"},
        },
    }


def _summary(market: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "market": market,
        "total_site_revenue_eur": 1000.0,
        "market_energy_net_eur": 800.0,
        "capacity_revenue_eur": 150.0 if market != "da" else 0.0,
        "pv_revenue_eur": 50.0,
        "pumped_mwh": 10.0,
        "turbined_mwh": 8.888,
        "e_max_mwh": 4.444,
        "reservoir_initial_mwh": 2.222,
        "reservoir_final_mwh": 2.222,
        "pv_available_mwh": 4.0,
        "pv_self_consumed_mwh": 1.0,
        "pv_exported_mwh": 2.0,
        "pv_curtailed_mwh": 1.0,
        "simultaneous_interval_count": 3,
        "simultaneous_overlap_mwh": 0.125,
        "diagnostics": {"simultaneous_interval_energy_net_eur": 12.5},
    }
    payload.update(overrides)
    return payload


def _row(market: str, rank: int, total: float, diff: float, **overrides: Any) -> dict[str, Any]:
    payload = {
        "market": market,
        "revenue_rank": rank,
        "difference_from_highest_eur": diff,
        "total_site_revenue_eur": total,
        "market_energy_net_eur": total - 100.0,
        "capacity_revenue_eur": 0.0 if market == "da" else 40.0,
        "pv_revenue_eur": 20.0,
        "pumped_mwh": 11.0,
        "turbined_mwh": 9.0,
        "full_cycles": 2.25,
        "pv_available_mwh": 4.0,
        "pv_self_consumed_mwh": 1.1,
        "pv_exported_mwh": 2.2,
        "pv_curtailed_mwh": 0.3,
        "simultaneous_interval_count": 4,
        "simultaneous_overlap_mwh": 0.2,
        "simultaneous_interval_energy_net_eur": 7.5,
        "e_max_mwh": 4.444,
    }
    payload.update(overrides)
    return payload


def _monthly_table(periods: list[str], values: list[float]) -> pa.Table:
    return pa.table({"period": periods, "total_site_revenue_eur": values})


def _capacity_table(count: int) -> pa.Table:
    return pa.table(
        {
            "identifier": [f"block-{index}" for index in range(count)],
            "direction": ["up"] * count,
            "price_eur_mw_h": [16.86] * count,
            "cap_max_mw": [1.0] * count,
            "committed_mw": [0.5] * count,
            "block_hours": [4.0] * count,
            "capacity_revenue_eur": [33.72] * count,
        }
    )


def _write_case_dir(
    directory: Path,
    market: str,
    *,
    resolved: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    periods: list[str] | None = None,
    monthly_values: list[float] | None = None,
    capacity_rows: int = 2,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / "summary.json", summary or _summary(market))
    _write_json(directory / "resolved_config.json", resolved or _resolved(market=market))
    _write_json(directory / "run_metadata.json", {"run_id": market})
    pq.write_table(
        _monthly_table(periods or ["2025-01", "2025-02"], monthly_values or [10.0, 20.0]),
        directory / "monthly_summary.parquet",
    )
    pq.write_table(_capacity_table(capacity_rows), directory / "capacity.parquet")


def _write_comparison(
    directory: Path,
    rows: list[dict[str, Any]],
    *,
    highest: str,
    resolved: dict[str, Any] | None = None,
    periods: list[str] | None = None,
    monthly_values: list[float] | None = None,
    capacity_rows: int = 2,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(
        directory / "comparison_summary.json",
        {"highest_revenue_market": highest, "rows": rows},
    )
    _write_json(directory / "comparison_request.json", {"markets": [row["market"] for row in rows]})
    _write_json(directory / "comparison_metadata.json", {"run_id": "cmp"})
    for row in rows:
        market = str(row["market"])
        _write_case_dir(
            directory / "cases" / market,
            market,
            resolved=resolved or _resolved(market=market),
            summary=_summary(
                market,
                total_site_revenue_eur=row["total_site_revenue_eur"],
                market_energy_net_eur=row["market_energy_net_eur"],
                capacity_revenue_eur=row["capacity_revenue_eur"],
                pv_revenue_eur=row["pv_revenue_eur"],
            ),
            periods=periods,
            monthly_values=monthly_values,
            capacity_rows=capacity_rows,
        )


def _planned_job(outputs_root: Path, snapshot: dict[str, Any], job_id: str, *, kind: str, markets: list[str]) -> dict[str, Any]:
    paths = job_paths(job_id, kind=kind, outputs_root=outputs_root)
    paths["staging_directory"].mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["configured_snapshot_path"], snapshot)
    atomic_write_json(paths["request_path"], {"run_id": job_id})
    record = job_record(
        job_id=job_id,
        kind=kind,
        markets=markets,
        snapshot_fingerprint=snapshot_digest(snapshot),
        launch_state=LAUNCH_LAUNCHED,
        launch_utc=iso_utc(),
        paths=paths,
        pid=4242,
        outputs_root=outputs_root,
    )
    write_job_record(paths["job_path"], record, outputs_root=outputs_root)
    return record


def _result_record(record: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "live",
        "kind": record["kind"],
        "job_id": record["job_id"],
        "output_directory": record["output_directory"],
        "markets": list(record["markets"]),
        "period": {
            "start_date": snapshot["period"]["start_date"],
            "end_date": snapshot["period"]["end_date"],
        },
        "validated": True,
    }


def test_demo_overview_values_and_ranking() -> None:
    result = open_demo_artifacts()
    assert result["markets"] == ["da", "mfrr", "afrr"]
    payload = load_result_display(result)
    assert payload["markets"] == ["da", "afrr", "mfrr"]
    assert payload["header"]["markets"] == "Day-ahead · aFRR · mFRR"
    assert payload["highest_revenue_market"] == "afrr"
    assert [row["market"] for row in payload["rows"]] == ["da", "afrr", "mfrr"]
    da, afrr, mfrr = payload["rows"]
    assert afrr["formatted"]["total"] == "EUR 254,183.10"
    assert afrr["formatted"]["energy"] == "EUR 169,601.23"
    assert afrr["formatted"]["capacity"] == "EUR 64,993.03"
    assert afrr["formatted"]["pv"] == "EUR 19,588.84"
    assert afrr["formatted"]["note"] == "Highest simulated revenue"
    assert afrr["revenue_rank"] == 1
    assert afrr["formatted"]["pv_self_share"] == "5.3%"
    assert da["formatted"]["total"] == "EUR 136,405.72"
    assert da["formatted"]["energy"] == "EUR 122,691.08"
    assert da["formatted"]["capacity"] == "EUR 0.00"
    assert da["formatted"]["pv"] == "EUR 13,714.63"
    assert da["formatted"]["difference"] == "-117,777.38"
    assert da["formatted"]["note"] == "EUR 117,777.38 below highest"
    assert da["revenue_rank"] == 2
    assert da["formatted"]["pv_self_share"] == "37.8%"
    assert mfrr["formatted"]["total"] == "EUR 115,971.24"
    assert mfrr["formatted"]["energy"] == "EUR 59,827.72"
    assert mfrr["formatted"]["capacity"] == "EUR 35,538.93"
    assert mfrr["formatted"]["pv"] == "EUR 20,604.59"
    assert mfrr["formatted"]["difference"] == "-138,211.86"
    assert mfrr["formatted"]["note"] == "EUR 138,211.86 below highest"
    assert mfrr["revenue_rank"] == 3
    assert mfrr["formatted"]["pv_self_share"] == "1.6%"


def test_two_market_comparison_renders_only_selected(tmp_path: Path) -> None:
    rows = [
        _row("da", 1, 200.0, 0.0),
        _row("mfrr", 2, 80.0, -120.0),
    ]
    _write_comparison(tmp_path, rows, highest="da")
    payload = read_result_artifacts(
        kind=KIND_COMPARISON,
        markets=["da", "mfrr"],
        directory=tmp_path,
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert [row["market"] for row in payload["rows"]] == ["da", "mfrr"]
    assert payload["markets"] == ["da", "mfrr"]
    assert payload["header"]["markets"] == "Day-ahead · mFRR"
    assert "afrr" not in payload["children"]
    assert payload["header"]["balancing"] == "Balanced"


def test_two_market_afrr_mfrr_uses_presentation_order(tmp_path: Path) -> None:
    rows = [
        _row("mfrr", 2, 80.0, -20.0),
        _row("afrr", 1, 100.0, 0.0),
    ]
    _write_comparison(tmp_path, rows, highest="afrr")
    payload = read_result_artifacts(
        kind=KIND_COMPARISON,
        markets=["mfrr", "afrr"],
        directory=tmp_path,
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert payload["markets"] == ["afrr", "mfrr"]
    assert [row["market"] for row in payload["rows"]] == ["afrr", "mfrr"]
    assert payload["header"]["markets"] == "aFRR · mFRR"
    assert payload["highest_revenue_market"] == "afrr"
    assert payload["rows"][0]["revenue_rank"] == 1


def test_canonical_order_unchanged() -> None:
    assert CANONICAL_MARKETS == ("da", "mfrr", "afrr")
    assert RESULTS_DISPLAY_MARKETS == ("da", "afrr", "mfrr")
    assert display_market_keys(["mfrr", "afrr", "da"]) == ["da", "afrr", "mfrr"]
    result = open_demo_artifacts()
    assert result["markets"] == list(CANONICAL_MARKETS)
    stored = json.loads((DEMO_COMPARISON_DIR / "comparison_summary.json").read_text(encoding="utf-8"))
    assert [row["market"] for row in stored["rows"]] == ["afrr", "da", "mfrr"]
    assert [row["revenue_rank"] for row in stored["rows"]] == [1, 2, 3]
    assert stored["highest_revenue_market"] == "afrr"


def test_operational_table_labels_and_no_difference_column() -> None:
    payload = load_result_display(open_demo_artifacts())
    table = _overview_table(payload["rows"], one_market=False)
    one = _overview_table(payload["rows"][:1], one_market=True)
    assert "Rank" not in table
    assert "Rank" not in one
    assert "Difference from highest (EUR)" not in table
    assert "Market energy net (EUR)" not in table
    assert list(table)[:5] == [
        "Market",
        "Total site revenue (EUR)",
        "Net energy revenue (EUR)",
        "Capacity revenue (EUR)",
        "PV revenue (EUR)",
    ]
    assert table["Market"] == ["Day-ahead", "aFRR", "mFRR"]
    assert table["PV self-consumed (%)"] == ["37.8%", "5.3%", "1.6%"]
    assert list(table) == list(one)
    assert "Difference from highest (EUR)" not in one
    assert "Net energy revenue (EUR)" in one
    glossary = column_glossary_html()
    assert "Net energy revenue" in glossary
    assert "Market energy net" not in glossary
    assert "Difference from highest" not in glossary
    assert "Rank" not in glossary
    assert "PV self-consumed (%)" in glossary


def test_one_market_overview_omits_ranking(tmp_path: Path) -> None:
    _write_case_dir(tmp_path, "da", resolved=_resolved(market="da", pv_kw=0.0))
    payload = read_result_artifacts(
        kind=KIND_CASE,
        markets=["da"],
        directory=tmp_path,
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert payload["one_market"] is True
    assert payload["rows"][0]["formatted"]["note"] == "Highest simulated revenue"
    assert payload["header"]["balancing"] is None
    assert payload["header"]["pv"] == "Off"
    assert payload["pv_included"] is False
    assert payload["rows"][0]["formatted"]["pv_self_share"] == "—"
    table = _overview_table(payload["rows"], one_market=True)
    assert "Rank" not in table
    assert table["PV self-consumed (%)"] == ["—"]
    assert "Difference from highest (EUR)" not in table
    assert "Net energy revenue (EUR)" in table


def test_zero_available_pv_shows_placeholder(tmp_path: Path) -> None:
    _write_case_dir(
        tmp_path,
        "da",
        resolved=_resolved(market="da", pv_kw=100.0),
        summary=_summary("da", pv_available_mwh=0.0, pv_self_consumed_mwh=0.0),
    )
    payload = read_result_artifacts(
        kind=KIND_CASE,
        markets=["da"],
        directory=tmp_path,
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert payload["pv_included"] is True
    assert payload["rows"][0]["formatted"]["pv_self_share"] == "—"


def test_negative_pv_energy_fails_closed(tmp_path: Path) -> None:
    rows = [_row("da", 1, 100.0, 0.0, pv_available_mwh=-1.0)]
    _write_comparison(tmp_path, rows, highest="da")
    with pytest.raises(ResultViewError, match=ERROR_RESULTS_BODY):
        read_result_artifacts(
            kind=KIND_COMPARISON,
            markets=["da"],
            directory=tmp_path,
            source="live",
            period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
        )
    with pytest.raises(ValueError):
        format_pv_self_share(self_consumed=-0.1, available=4.0, pv_included=True)


def test_dynamic_heading_and_header_variants(tmp_path: Path) -> None:
    _write_case_dir(
        tmp_path / "one",
        "da",
        resolved=_resolved(market="da", pump=0.8, turbine=1.25, grid_import=0.6, grid_export=1.4, pv_kw=0.0),
    )
    one = read_result_artifacts(
        kind=KIND_CASE,
        markets=["da"],
        directory=tmp_path / "one",
        source="live",
        period={"start_date": "2025-03-01", "end_date": "2025-04-30"},
    )
    assert one["one_market"] is True
    assert one["header"]["pump_turbine"] == "0.800 / 1.250 MW"
    assert one["header"]["grid"] == "0.600 MW import / 1.400 MW export"
    assert one["header"]["pv"] == "Off"
    assert one["header"]["balancing"] is None
    assert one["header"]["period"] == "Belgian delivery 2025-03-01 to 2025-04-30"
    assert one["header"]["run_type"] is None

    rows = [_row("mfrr", 1, 90.0, 0.0), _row("afrr", 2, 70.0, -20.0)]
    _write_comparison(
        tmp_path / "two",
        rows,
        highest="mfrr",
        resolved=_resolved(market="mfrr", pump=1.0, turbine=1.0, pv_kw=250.5, activation="passive"),
    )
    multi = read_result_artifacts(
        kind=KIND_COMPARISON,
        markets=["mfrr", "afrr"],
        directory=tmp_path / "two",
        source="demo",
        period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    assert multi["one_market"] is False
    assert multi["header"]["balancing"] == "Passive"
    assert multi["header"]["pv"] == "250.500 kW"
    assert multi["header"]["run_type"] == "Saved demonstration"
    assert multi["header"]["pump_turbine"] == "1.000 / 1.000 MW"


def test_child_artifacts_monthly_order_and_capacity(tmp_path: Path) -> None:
    rows = [
        _row("afrr", 1, 300.0, 0.0),
        _row("da", 2, 100.0, -200.0),
        _row("mfrr", 3, 50.0, -250.0),
    ]
    _write_comparison(tmp_path / "full", rows, highest="afrr", capacity_rows=51)
    da_dir = tmp_path / "full" / "cases" / "da"
    pq.write_table(_monthly_table(["2025-12", "2026-01"], [5.0, 7.5]), da_dir / "monthly_summary.parquet")
    mfrr_dir = tmp_path / "full" / "cases" / "mfrr"
    pq.write_table(_monthly_table(["2025-03", "2025-04"], [1.0, 2.0]), mfrr_dir / "monthly_summary.parquet")
    payload = read_result_artifacts(
        kind=KIND_COMPARISON,
        markets=["da", "mfrr", "afrr"],
        directory=tmp_path / "full",
        source="live",
        period={"start_date": "2025-01-01", "end_date": "2026-01-31"},
    )
    assert payload["children"]["da"]["monthly"]["periods"] == ["2025-12", "2026-01"]
    assert payload["children"]["da"]["monthly"]["total_site_revenue_eur"] == [5.0, 7.5]
    assert payload["children"]["mfrr"]["monthly"]["periods"] == ["2025-03", "2025-04"]
    assert payload["children"]["da"]["has_capacity"] is False
    assert payload["children"]["afrr"]["capacity"]["block_count"] == 51
    assert payload["children"]["afrr"]["capacity"]["truncated"] is True
    assert len(payload["children"]["afrr"]["capacity"]["preview"]) == CAPACITY_PREVIEW_LIMIT
    assert payload["children"]["mfrr"]["formatted"]["total"] != payload["children"]["afrr"]["formatted"]["total"]


def test_demo_detail_simultaneous_and_capacity() -> None:
    payload = load_result_display(open_demo_artifacts())
    afrr = payload["children"]["afrr"]
    assert afrr["formatted"]["simul_n"] == "1,743"
    assert afrr["formatted"]["simul_mwh"] == "216.663 MWh"
    assert afrr["formatted"]["simul_eur"] == "EUR 68,492.67"
    assert afrr["capacity"]["block_count"] > CAPACITY_PREVIEW_LIMIT
    assert afrr["capacity"]["truncated"] is True
    assert len(afrr["capacity"]["preview"]) == CAPACITY_PREVIEW_LIMIT
    da = payload["children"]["da"]
    assert da["has_capacity"] is False
    assert da["monthly"]["periods"][0] < da["monthly"]["periods"][-1]


def test_display_formatting_keeps_negative_eur() -> None:
    assert format_eur(-117777.38340447578) == "EUR -117,777.38"
    assert format_eur_amount(-117777.38340447578) == "-117,777.38"
    assert format_eur(254183.10064814897) == "EUR 254,183.10"


def test_new_result_resets_tab_and_default_market() -> None:
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
    assert first["active_tab"] == "Overview"
    assert first["selected_detail_market"] == "afrr"
    assert first["selected_explorer_market"] == "da"
    assert first["selected_technical_market"] == "da"
    state[RESULTS_VIEW_KEY] = {
        "active_tab": "Market detail",
        "selected_detail_market": "mfrr",
        "selected_explorer_market": "da",
        "selected_explorer_week": "2025-W20",
        "selected_technical_market": "afrr",
    }
    other = dict(demo)
    other["job_id"] = "demo-other"
    other["output_directory"] = str(Path(demo["output_directory"])) + "-other"
    reset = sync_results_view(
        state,
        result=other,
        default_market="da",
        allowed_markets=["da"],
    )
    assert reset["active_tab"] == "Overview"
    assert reset["selected_detail_market"] == "da"
    assert reset["selected_explorer_market"] == "da"
    assert reset["selected_technical_market"] == "da"
    assert result_open_identity(other) != result_open_identity(demo)


def test_malformed_result_files_fail_closed(tmp_path: Path) -> None:
    _write_case_dir(tmp_path, "da")
    (tmp_path / "monthly_summary.parquet").unlink()
    with pytest.raises(ResultViewError, match=ERROR_RESULTS_BODY):
        read_result_artifacts(
            kind=KIND_CASE,
            markets=["da"],
            directory=tmp_path,
            source="live",
            period={"start_date": "2025-01-01", "end_date": "2025-12-31"},
        )


def test_app_demo_tabs_reserved_state_and_session(monkeypatch: pytest.MonkeyPatch) -> None:
    popen_calls: list[str] = []

    def bang(**_kwargs):
        popen_calls.append("popen")
        raise AssertionError("result navigation must not launch a worker")

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
        assert not at.exception
        text = _combined(at)
        assert "Operational comparison" in text
        assert "Highest simulated total site revenue" not in text
        assert not any("Highest simulated total site revenue" in str(item.value) for item in at.info)
        assert "Highest simulated revenue" in text
        assert "below highest" in text
        assert "EUR 254,183.10" in text
        assert "Day-ahead · aFRR · mFRR" in text
        assert "Net energy revenue" in text
        assert "Market energy net" not in text
        assert "Difference from highest" not in text
        assert "Rank" not in text
        assert at.session_state["sib"]["results_view"]["active_tab"] == "Overview"
        require_json_compatible(at.session_state["sib"])
        assert set(at.session_state["sib"]["results_view"]) == {
            "active_tab",
            "selected_detail_market",
            "selected_explorer_market",
            "selected_explorer_week",
            "selected_technical_market",
        }
        assert at.session_state["sib"]["results_view"]["selected_detail_market"] == "afrr"
        assert at.session_state["sib"]["results_view"]["selected_explorer_market"] == "da"
        assert at.session_state["sib"]["results_view"]["selected_explorer_week"] == "2025-W20"
        assert at.session_state["sib"]["results_view"]["selected_technical_market"] == "da"

        for tab in RESULT_TABS[1:]:
            next(item for item in at.button if item.label == tab).click()
            at.run()
            assert not at.exception
            shown = _combined(at)
            if tab == "Market detail":
                assert "Revenue composition" in shown
                assert "Monthly total site revenue" in shown
                assert any(item.label == "Market" for item in at.selectbox)
                assert next(item for item in at.selectbox if item.label == "Market").value == "aFRR"
                assert "About simultaneous operation" in [item.label for item in at.expander]
                about = next(item for item in at.expander if item.label == "About simultaneous operation")
                assert bool(getattr(about, "value", False)) is False
            elif tab == "Data explorer":
                plotly_text = " ".join(item.proto.spec for item in at.get("plotly_chart"))
                assert "Pump and turbine power" in plotly_text
                assert "Main results" in shown
                assert "Main results" not in plotly_text
                assert next(item for item in at.selectbox if item.label == "Market").value == "Day-ahead"
                assert "Balancing capacity commitments do not apply to Day-ahead." in shown
                assert "Stored dispatch week" in [item.label for item in at.expander]
                assert RESERVED_TAB_BODY not in shown
                assert "Revenue composition" not in shown
                assert len(list(at.get("plotly_chart"))) == 1
            elif tab == "Technical details":
                assert "Configured simulation" in shown
                assert "HiGHS solver" in shown
                assert "Data sources and verification" in shown
                assert "Solution checks" in shown
                assert next(item for item in at.selectbox if item.label == "Market").value == "Day-ahead"
                assert RESERVED_TAB_BODY not in shown
                assert "Revenue composition" not in shown
            else:
                assert "Complete result package" in shown
                assert "The output results can also be found at:" in shown
                assert "Individual downloads use the stored files unchanged." not in shown
                assert "ui/demo_artifacts/stepinbel_2025_all_markets_pv500/" in shown
                labels = [item.label for item in at.download_button]
                assert "Download complete result package" in labels
                assert RESERVED_TAB_BODY not in shown
                assert "Revenue composition" not in shown
        assert popen_calls == []
        assert at.session_state["sib"]["stage"] == STAGE_RESULTS
    finally:
        TEST_HOOKS.clear()


def test_app_one_market_overview_and_no_selector(tmp_path: Path) -> None:
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20260906T210000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_CASE, markets=["da"])
    _write_case_dir(Path(record["output_directory"]), "da", resolved=_resolved(market="da", pv_kw=0.0))
    (Path(record["output_directory"]) / "run_status.json").write_text(
        '{"status_schema_version": 1, "run_id": "%s", "state": "completed"}' % job_id,
        encoding="utf-8",
    )
    popen_calls: list[str] = []

    def bang(**_kwargs):
        popen_calls.append("popen")
        raise AssertionError("one-market Results must not launch")

    TEST_HOOKS.clear()
    TEST_HOOKS["outputs_root"] = tmp_path
    TEST_HOOKS["popen"] = bang
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
        assert not at.exception
        text = _combined(at)
        assert "Operational results" in text
        assert "Operational comparison" not in text
        assert "Highest simulated total site revenue" not in text
        assert "below highest" not in text
        assert "Rank" not in text
        assert "Balancing strategy" not in text
        assert "Off" in text
        next(item for item in at.button if item.label == "Market detail").click()
        at.run()
        assert not at.exception
        detail = _combined(at)
        assert "Capacity payments do not apply to Day-ahead." in detail
        assert "PV was not included in this simulation." in detail
        assert not any(item.label == "Market" for item in at.selectbox)
        assert popen_calls == []
    finally:
        TEST_HOOKS.clear()


def test_app_fail_closed_without_partials(tmp_path: Path) -> None:
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20260906T210100Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_CASE, markets=["da"])
    Path(record["output_directory"]).mkdir(parents=True, exist_ok=True)
    TEST_HOOKS.clear()
    TEST_HOOKS["outputs_root"] = tmp_path
    TEST_HOOKS["popen"] = lambda **_k: (_ for _ in ()).throw(AssertionError("no popen"))
    try:
        state = default_state()
        store_snapshot(state, snapshot, snapshot["form_fingerprint"])
        state["form"] = form
        state["job"] = record
        unlock_results(state, _result_record(record, snapshot))
        at = AppTest.from_file(str(APP), default_timeout=40)
        at.run()
        at.session_state["sib"] = state
        at.query_params[JOB_QUERY_KEY] = job_id
        at.run()
        assert not at.exception
        text = _combined(at)
        assert ERROR_RESULTS_TITLE in text
        assert ERROR_RESULTS_BODY in text
        assert "Operational comparison" not in text
        assert "EUR 254,183.10" not in text
        assert next(item for item in at.button if item.label == "1  Configure")
    finally:
        TEST_HOOKS.clear()
