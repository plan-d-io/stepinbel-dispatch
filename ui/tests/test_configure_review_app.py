from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from ui.flow import default_state
from ui.presentation.tokens import PERIOD_2026, PERIOD_CUSTOM, PERIOD_END_LABEL, PERIOD_SELECTOR_LABEL, PERIOD_START_LABEL
from ui.services.form import default_live_form
from ui.services.snapshot import build_snapshot

APP = Path(__file__).resolve().parents[2] / "ui" / "app.py"


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


def _button(at: AppTest, label: str):
    return next(item for item in at.button if item.label == label)


def _checkbox(at: AppTest, label: str):
    return next(item for item in at.checkbox if item.label == label)


def _select(at: AppTest, label: str):
    return next(item for item in at.selectbox if item.label == label)


def _text_input(at: AppTest, label: str):
    return next(item for item in at.text_input if item.label == label)


def _number(at: AppTest, label: str):
    return next(item for item in at.number_input if item.label == label)


def test_default_live_configure_composition() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not at.exception
    assert _checkbox(at, "Day-ahead").value is True
    assert _checkbox(at, "mFRR").value is True
    assert _checkbox(at, "aFRR").value is True
    assert _select(at, "Balancing activation").value == "Balanced"
    assert _text_input(at, PERIOD_START_LABEL).value == "2025-01-01"
    assert _text_input(at, PERIOD_END_LABEL).value == "2025-12-31"
    assert _text_input(at, PERIOD_START_LABEL).disabled is True
    assert _number(at, "Common rating (MW)").value == 1.0
    assert "4.444 MWh" in _text(at)
    assert _checkbox(at, "Include co-located PV").value is False
    assert _checkbox(at, "Include co-located wind").value is False
    assert _button(at, "1  Configure").disabled is True
    assert _button(at, "2  Review & run").disabled is True
    assert _button(at, "3  Results").disabled is True


def test_market_visibility_and_required_market() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    _checkbox(at, "mFRR").set_value(False)
    _checkbox(at, "aFRR").set_value(False)
    at.run()
    assert not at.exception
    assert not any(item.label == "Balancing activation" for item in at.selectbox)
    _checkbox(at, "Day-ahead").set_value(False)
    at.run()
    assert _button(at, "Continue").disabled is True
    assert "Select at least one market." in _text(at)
    _checkbox(at, "mFRR").set_value(True)
    at.run()
    assert any(item.label == "Balancing activation" for item in at.selectbox)
    assert _button(at, "Continue").disabled is False


def test_2026_and_custom_date_editing() -> None:
    at = AppTest.from_file(str(APP), default_timeout=40)
    at.run()
    _select(at, PERIOD_SELECTOR_LABEL).set_value(PERIOD_2026)
    at.run()
    assert not at.exception
    start = _text_input(at, PERIOD_START_LABEL)
    end = _text_input(at, PERIOD_END_LABEL)
    assert start.value == "2026-01-01"
    assert start.disabled is True
    assert end.disabled is True
    assert str(end.value).startswith("2026-")
    assert "Latest completely covered Belgian delivery date" in _text(at)
    _select(at, PERIOD_SELECTOR_LABEL).set_value(PERIOD_CUSTOM)
    at.run()
    assert _text_input(at, PERIOD_START_LABEL).disabled is False
    assert _text_input(at, PERIOD_END_LABEL).disabled is False


def test_continue_review_back_and_snapshot_not_widget_state() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    assert "Review & run" in [item.value for item in at.header]
    text = _text(at)
    assert "1.000 MW import / 1.000 MW export" in text
    assert "4.444 MWh" in text
    assert "Final check" in [item.value for item in at.subheader]
    assert "Ready to run" in text
    assert "Your settings are valid, and market data are available for the full selected period." in text
    assert "Run simulation" in [item.label for item in at.button]
    assert _button(at, "Run simulation").disabled is False
    assert "Cancel" not in [item.label for item in at.button]
    assert _button(at, "2  Review & run").disabled is True
    assert _button(at, "1  Configure").disabled is False
    assert _button(at, "3  Results").disabled is True
    at.session_state["sib-cfg-common-mw"] = 9.0
    at.run()
    text = _text(at)
    assert "1.000 MW import / 1.000 MW export" in text
    assert "4.444 MWh" in text
    assert "9.000" not in text
    _button(at, "Back").click()
    at.run()
    assert "PHS dispatch simulator" in [item.value for item in at.header]
    assert _button(at, "2  Review & run").disabled is False
    _number(at, "Common rating (MW)").set_value(2.0)
    at.run()
    assert _button(at, "2  Review & run").disabled is True


def test_demo_controls_are_read_only_and_open_review() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    _checkbox(at, "Demo mode").set_value(True)
    at.run()
    assert not at.exception
    for label in ("Day-ahead", "mFRR", "aFRR", "Include co-located PV"):
        box = _checkbox(at, label)
        assert box.value is True
        assert box.disabled is True
    wind = _checkbox(at, "Include co-located wind")
    assert wind.value is False
    assert wind.disabled is True
    assert _number(at, "Common rating (MW)").disabled is True
    assert _number(at, "Common rating (MW)").value == 1.0
    assert _number(at, "Installed PV (kW)").value == 500.0
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    text = _text(at)
    assert "View demonstration results" in [item.label for item in at.button]
    assert _button(at, "View demonstration results").disabled is False
    assert "500 kW Belgium" in text
    assert "frozen" not in text.lower()
    assert "provenance" not in text.lower()


def test_malformed_snapshot_has_active_back() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    broken = default_state()
    broken["stage"] = 2
    broken["max_stage"] = 2
    broken["snapshot"] = {"demo": False}
    at.session_state["sib"] = broken
    at.run()
    assert not at.exception
    assert "Cannot open Review" in _text(at)
    assert _button(at, "Back").disabled is False
    _button(at, "Back").click()
    at.run()
    assert "PHS dispatch simulator" in [item.value for item in at.header]


def test_zero_historical_quantile_survives_continue_to_review() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    quantile = next(item for item in at.number_input if item.label == "Historical capacity quantile")
    quantile.set_value(0.0)
    at.run()
    assert not at.exception
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    snapshot = at.session_state["sib"]["snapshot"]
    assert snapshot["balancing"]["bid_quantile"] == 0.0
    assert snapshot["form"]["bid_quantile"] == 0.0
    assert "Review & run" in [item.value for item in at.header]


def test_empty_nested_snapshot_blocks_without_crash() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    live = build_snapshot(default_live_form(), demo=False)
    live["asset"] = {}
    live["site"] = {}
    live["derived"] = {}
    broken = default_state()
    broken["stage"] = 2
    broken["max_stage"] = 2
    broken["form"] = live["form"]
    broken["snapshot"] = live
    at.session_state["sib"] = broken
    at.run()
    assert not at.exception
    assert "Cannot open Review" in _text(at)
    assert _button(at, "Back").disabled is False
    assert _button(at, "3  Results").disabled is True


def test_fixed_pv_survives_review_back_to_configure() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    _checkbox(at, "Include co-located PV").set_value(True)
    at.run()
    _select(at, "Export valuation").set_value("Fixed price")
    at.run()
    _number(at, "Fixed PV export price (EUR/MWh)").set_value(42.5)
    at.run()
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    form = at.session_state["sib"]["form"]
    assert form["pv_revenue_mode"] == "fixed"
    assert form["pv_fixed_price"] == 42.5
    _button(at, "Back").click()
    at.run()
    assert not at.exception
    assert "PHS dispatch simulator" in [item.value for item in at.header]
    assert _checkbox(at, "Include co-located PV").value is True
    assert _select(at, "Export valuation").value == "Fixed price"
    assert _number(at, "Fixed PV export price (EUR/MWh)").value == 42.5
    form = at.session_state["sib"]["form"]
    assert form["pv_revenue_mode"] == "fixed"
    assert form["pv_fixed_price"] == 42.5


def test_conditional_configure_choices_survive_stepper_return() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    _checkbox(at, "Configure pump and turbine separately").set_value(True)
    at.run()
    _number(at, "Pump rating (MW)").set_value(2.0)
    _number(at, "Turbine rating (MW)").set_value(1.5)
    _checkbox(at, "Configure import and export separately").set_value(True)
    at.run()
    _number(at, "Grid import (MW)").set_value(2.0)
    _number(at, "Grid export (MW)").set_value(1.5)
    at.run()
    _select(at, "Capacity bidding").set_value("Fixed minimum prices")
    at.run()
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    _button(at, "1  Configure").click()
    at.run()
    assert not at.exception
    assert _checkbox(at, "Configure pump and turbine separately").value is True
    assert _number(at, "Pump rating (MW)").value == 2.0
    assert _number(at, "Turbine rating (MW)").value == 1.5
    assert _checkbox(at, "Configure import and export separately").value is True
    assert _number(at, "Grid import (MW)").value == 2.0
    assert _number(at, "Grid export (MW)").value == 1.5
    assert _select(at, "Capacity bidding").value == "Fixed minimum prices"
    form = at.session_state["sib"]["form"]
    assert form["separate_machines"] is True
    assert form["pump_mw"] == 2.0
    assert form["turbine_mw"] == 1.5
    assert form["separate_grid"] is True
    assert form["bid_kind"] == "fixed"


def test_advanced_section_follows_wind() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    headings = [item.value for item in at.subheader]
    assert headings.index("PV") < headings.index("Wind") < headings.index("Advanced")
    labels = [item.label for item in at.expander]
    assert "Machine operating constraints" not in labels
    assert "Advanced asset assumptions" in labels
    assert labels.index("Advanced asset assumptions") < labels.index("Advanced balancing assumptions")
    assert labels.index("Advanced balancing assumptions") < labels.index("Solver and diagnostics")
    source = (Path(__file__).resolve().parents[2] / "ui" / "views" / "configure.py").read_text(
        encoding="utf-8"
    )
    asset_idx = source.index('with st.expander("Advanced asset assumptions"')
    constraint_idx = source.index("st.markdown(f\"**{COMMITMENT_EXPANDER}**\")")
    pv_idx = source.index('render_section_heading("PV")')
    wind_idx = source.index('render_section_heading("Wind")')
    advanced_idx = source.index('render_section_heading("Advanced")')
    assert pv_idx < wind_idx < advanced_idx
    assert asset_idx < constraint_idx < advanced_idx


def test_configure_uses_narrow_safe_columns() -> None:
    source = (Path(__file__).resolve().parents[2] / "ui" / "views" / "configure.py").read_text(encoding="utf-8")
    assert "st.columns(3)" in source
    assert "st.columns(2)" in source
    assert "render_action_row" in source
    assert "render_readouts" in source
