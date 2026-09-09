from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from ui.flow import (
    STAGE_CONFIGURE,
    STAGE_RESULTS,
    STAGE_REVIEW,
    apply_demo_change,
    back_to_configure,
    clear_snapshot,
    configure_another_run,
    consume_configure_restore,
    default_state,
    invalidate_if_form_changed,
    navigate_to_stage,
    require_json_compatible,
    state_is_compatible,
    store_snapshot,
    unlock_results,
)
from ui.services.configs import build_market_case, derived_reservoir_text
from ui.services.demo import demo_form
from ui.services.form import (
    PRESET_2025,
    PRESET_2026,
    PRESET_CUSTOM,
    apply_form_transitions,
    coalesce_float,
    default_live_form,
    selected_markets,
    suggested_grid_mw,
)
from ui.services.period import apply_period_preset, latest_inclusive_end
from ui.services.snapshot import (
    INCOMPLETE_SNAPSHOT,
    MARKET_REQUIRED,
    PRE_2025_BALANCING,
    SNAPSHOT_SCHEMA_VERSION,
    STALE_SNAPSHOT,
    UNSUPPORTED_SNAPSHOT,
    as_serialisable,
    build_snapshot,
    lightweight_continue_reason,
    review_warnings,
    snapshot_block_reason,
)

ROOT = Path(__file__).resolve().parents[2]
DEMO_REQUEST = (
    ROOT / "ui" / "demo_artifacts" / "stepinbel_2025_all_markets_pv500" / "comparison_request.json"
)


def _form(**overrides):
    form = default_live_form()
    form.update(overrides)
    return form


def test_configure_another_run_clears_execution_and_locks_results() -> None:
    state = default_state()
    store_snapshot(state, {"ok": True}, "x")
    state["form"] = {"market_da": True, "market_mfrr": False}
    unlock_results(state, {"schema_version": 1, "validated": True})
    state["job"] = {"job_id": "stepinbel-20260905T120000Z-abcd1234"}
    state["launch_error"] = "stale"
    configure_another_run(state)
    assert state["stage"] == STAGE_CONFIGURE
    assert state["max_stage"] == STAGE_CONFIGURE
    assert state["job"] is None
    assert state["result"] is None
    assert state["launch_error"] is None
    assert state["form"] == {"market_da": True, "market_mfrr": False}
    assert state["snapshot"] == {"ok": True}
    assert consume_configure_restore(state) is True
    assert navigate_to_stage(state, STAGE_RESULTS) is False


def test_returning_to_configure_marks_widget_restore() -> None:
    state = default_state()
    assert consume_configure_restore(state) is False
    store_snapshot(state, {"ok": True}, "x")
    back_to_configure(state)
    assert state["stage"] == STAGE_CONFIGURE
    assert consume_configure_restore(state) is True
    assert consume_configure_restore(state) is False
    store_snapshot(state, {"ok": True}, "x")
    assert navigate_to_stage(state, STAGE_CONFIGURE) is True
    assert consume_configure_restore(state) is True


def test_default_state_is_live_configure_without_snapshot() -> None:
    state = default_state()
    assert state["stage"] == STAGE_CONFIGURE
    assert state["max_stage"] == STAGE_CONFIGURE
    assert state["demo"] is False
    assert state["snapshot"] is None
    require_json_compatible(state)


def test_default_live_form_values() -> None:
    form = default_live_form()
    assert selected_markets(form) == ["da", "mfrr", "afrr"]
    assert form["activation"] == "balanced"
    assert form["period_preset"] == PRESET_2025
    assert form["start_date"] == "2025-01-01"
    assert form["end_date"] == "2025-12-31"
    assert form["separate_machines"] is False
    assert form["common_mw"] == 1.0
    assert form["eta_pump"] == 0.84
    assert form["eta_turbine"] == 0.90
    assert form["storage_hours"] == 4.0
    assert form["grid_follow"] is True
    assert form["grid_common_mw"] == 1.0
    assert form["pv_enabled"] is False
    assert form["bid_kind"] == "historical"
    assert form["bid_quantile"] == 0.50
    assert form["detailed_solver"] is False
    assert form["fixed_speed_pump"] is False
    assert form["turbine_minimum_enabled"] is False
    assert form["turbine_minimum_output_pct"] == 18.0
    assert form["forbid_simultaneous_operation"] is False
    assert form["mip_gap_pct"] == 1.5
    assert form["mip_time_limit_min"] == 15.0


def test_at_least_one_market_is_required() -> None:
    form = _form(market_da=False, market_mfrr=False, market_afrr=False)
    assert lightweight_continue_reason(form) == MARKET_REQUIRED
    with pytest.raises(ValueError, match="at least one market"):
        build_snapshot(form, demo=False)


def test_2025_automatic_dates() -> None:
    form = apply_period_preset(_form(period_preset=PRESET_2025, start_date="2024-01-01", end_date="2024-02-01"))
    assert form["start_date"] == "2025-01-01"
    assert form["end_date"] == "2025-12-31"


def test_2026_end_is_resolved_from_published_bundle() -> None:
    selections = (
        (("da", "mfrr", "afrr"), True),
        (("da",), False),
        (("mfrr",), False),
        (("afrr",), True),
        (("da", "mfrr"), False),
    )
    resolved: dict[str, str] = {}
    for markets, pv in selections:
        end = latest_inclusive_end(markets, pv_enabled=pv, year=2026)
        assert end.year == 2026
        assert end >= date(2026, 1, 1)
        key = f"{'+'.join(markets)}|pv={int(pv)}"
        resolved[key] = end.isoformat()
        form = apply_period_preset(
            _form(
                period_preset=PRESET_2026,
                market_da="da" in markets,
                market_mfrr="mfrr" in markets,
                market_afrr="afrr" in markets,
                pv_enabled=pv,
            )
        )
        assert form["start_date"] == "2026-01-01"
        assert form["end_date"] == end.isoformat()
        assert form["end_date"] != "2026-12-31" or end == date(2026, 12, 31)
    assert len(set(resolved.values())) >= 1


def test_custom_period_keeps_edited_dates() -> None:
    form = apply_period_preset(
        _form(period_preset=PRESET_CUSTOM, start_date="2025-03-01", end_date="2025-03-31")
    )
    assert form["start_date"] == "2025-03-01"
    assert form["end_date"] == "2025-03-31"


def test_pre_2025_balancing_is_rejected() -> None:
    form = _form(period_preset=PRESET_CUSTOM, start_date="2024-01-01", end_date="2024-01-31")
    assert lightweight_continue_reason(form) == PRE_2025_BALANCING
    da_only = _form(
        market_mfrr=False,
        market_afrr=False,
        period_preset=PRESET_CUSTOM,
        start_date="2024-01-01",
        end_date="2024-01-31",
    )
    assert lightweight_continue_reason(da_only) is None


def test_exact_coverage_failure() -> None:
    form = _form(
        period_preset=PRESET_CUSTOM,
        start_date="2026-12-01",
        end_date="2026-12-31",
    )
    with pytest.raises(ValueError, match="not fully covered"):
        build_snapshot(form, demo=False)


def test_machine_transitions_and_derived_reservoir() -> None:
    common = _form(common_mw=2.5, separate_machines=False)
    separate = apply_form_transitions(common, {**common, "separate_machines": True})
    assert separate["pump_mw"] == 2.5
    assert separate["turbine_mw"] == 2.5
    separate["pump_mw"] = 3.0
    separate["turbine_mw"] = 1.5
    back = apply_form_transitions(separate, {**separate, "separate_machines": False})
    assert back["common_mw"] == 3.0
    assert "4.444" in derived_reservoir_text(default_live_form())


def test_grid_follow_override_and_reset() -> None:
    form = default_live_form()
    assert form["grid_follow"] is True
    grown = apply_form_transitions(form, {**form, "common_mw": 2.0})
    assert grown["grid_follow"] is True
    assert grown["grid_common_mw"] == 2.0
    assert suggested_grid_mw(grown) == 2.0
    edited = apply_form_transitions(grown, {**grown, "grid_common_mw": 1.2})
    assert edited["grid_follow"] is False
    assert edited["grid_common_mw"] == 1.2
    reset = apply_form_transitions(edited, edited, reset_grid=True)
    assert reset["grid_follow"] is True
    assert reset["grid_common_mw"] == suggested_grid_mw(reset)
    split = apply_form_transitions(reset, {**reset, "separate_grid": True})
    assert split["grid_import_mw"] == split["grid_export_mw"] == reset["grid_common_mw"]


def test_live_pv_and_fixed_price_validation() -> None:
    off = default_live_form()
    assert off["pv_enabled"] is False
    on = _form(pv_enabled=True, pv_ac_kw=500.0, pv_revenue_mode="da")
    snapshot = build_snapshot(on, demo=False)
    assert snapshot["site"]["pv_ac_kw"] == 500.0
    assert snapshot["site"]["pv_region"] == "Belgium"
    missing = _form(pv_enabled=True, pv_revenue_mode="fixed", pv_fixed_price=None)
    assert lightweight_continue_reason(missing) is not None
    priced = _form(pv_enabled=True, pv_revenue_mode="fixed", pv_fixed_price=42.0)
    priced_snap = build_snapshot(priced, demo=False)
    assert priced_snap["site"]["pv_revenue_mode"] == "fixed"
    assert priced_snap["site"]["pv_fixed_price_eur_mwh"] == 42.0


def test_balancing_controls_and_snapshot_round_trip() -> None:
    da_only = _form(market_mfrr=False, market_afrr=False)
    assert da_only["market_da"] is True
    live = build_snapshot(default_live_form(), demo=False)
    encoded = json.dumps(live, allow_nan=False)
    assert json.loads(encoded) == live
    assert live["derived"]["coverage_ok"] is True
    assert live["derived"]["interval_count"] == 35040
    assert live["site"]["grid_import_mw"] == 1.0
    assert live["detailed_solver_output"] is False
    assert "output_directory" not in json.dumps(live)
    assert "run_id" not in live
    as_serialisable(live)


def test_snapshot_invalidation_and_navigation() -> None:
    state = default_state()
    snapshot = build_snapshot(default_live_form(), demo=False)
    store_snapshot(state, snapshot, snapshot["form_fingerprint"])
    assert state["stage"] == STAGE_REVIEW
    assert state["max_stage"] == STAGE_REVIEW
    back_to_configure(state)
    assert state["stage"] == STAGE_CONFIGURE
    assert state["snapshot"] is not None
    changed = default_live_form()
    changed["common_mw"] = 2.0
    assert invalidate_if_form_changed(state, previous=default_live_form(), current=changed)
    assert state["snapshot"] is None
    assert state["max_stage"] == STAGE_CONFIGURE
    assert navigate_to_stage(state, STAGE_REVIEW) is False
    apply_demo_change(state, True)
    assert state["form"] is None
    assert state["snapshot"] is None


def test_review_warnings_are_not_invented_thresholds() -> None:
    snapshot = build_snapshot(
        _form(
            grid_follow=False,
            grid_common_mw=0.5,
            separate_grid=False,
            pv_enabled=True,
            pv_ac_kw=800.0,
            period_preset=PRESET_CUSTOM,
            start_date="2025-01-01",
            end_date="2025-01-31",
        ),
        demo=False,
    )
    warnings = review_warnings(snapshot)
    assert "Grid import is below the pump rating." in warnings
    assert "Grid export is below the turbine rating." in warnings
    assert "Installed PV exceeds the export-side grid limit." in warnings
    assert "The custom period is shorter than a full year." in warnings


def test_demo_form_reads_committed_request() -> None:
    form = demo_form()
    payload = json.loads(DEMO_REQUEST.read_text(encoding="utf-8"))
    site = payload["case_requests"]["da"]["config"]["site"]
    asset = payload["case_requests"]["da"]["config"]["asset"]
    assert form["market_da"] and form["market_mfrr"] and form["market_afrr"]
    assert form["start_date"] == payload["case_requests"]["da"]["config"]["period"]["start_date"]
    assert form["end_date"] == payload["case_requests"]["da"]["config"]["period"]["end_date_inclusive"]
    assert form["common_mw"] == asset["power_pump_mw"]
    assert form["eta_pump"] == asset["eta_pump"]
    assert form["eta_turbine"] == asset["eta_turbine"]
    assert form["storage_hours"] == asset["storage_hours"]
    assert form["grid_import_mw"] == site["grid_import_mw"]
    assert form["pv_ac_kw"] == site["pv_ac_kw"]
    assert form["pv_enabled"] is True
    assert form["activation"] == "balanced"
    assert form["bid_kind"] == "historical"
    snapshot = build_snapshot(form, demo=True)
    assert snapshot["identity"] == "demo-2025-all-markets-pv500"
    assert snapshot["demo"] is True


def test_malformed_snapshot_recovery() -> None:
    assert snapshot_block_reason(None, default_live_form())
    assert snapshot_block_reason({"demo": False}, default_live_form())
    live = build_snapshot(default_live_form(), demo=False)
    stale = deepcopy(default_live_form())
    stale["common_mw"] = 9.0
    assert snapshot_block_reason(live, stale)


def test_state_schema_rejects_bool_and_non_integer_versions() -> None:
    assert state_is_compatible(default_state()) is True
    assert state_is_compatible({"version": True}) is False
    assert state_is_compatible({"version": 1.0}) is False
    assert state_is_compatible({"version": "1"}) is False
    assert True == 1
    assert state_is_compatible({"version": True}) is False


def test_zero_values_are_preserved_through_config_and_snapshot() -> None:
    form = default_live_form()
    form["bid_quantile"] = 0.0
    assert coalesce_float(form["bid_quantile"], 0.50) == 0.0
    assert build_market_case(form, "mfrr").capacity_bid.quantile == 0.0
    form["afrr_up_fraction"] = 0.0
    form["soc_initial"] = 0.0
    form["soc_terminal"] = 0.0
    form["pump_ramp_power_frac"] = 0.0
    form["turbine_ramp_power_frac"] = 0.0
    snapshot = build_snapshot(form, demo=False)
    assert snapshot["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert type(snapshot["schema_version"]) is int
    assert snapshot["balancing"]["bid_quantile"] == 0.0
    assert snapshot["balancing"]["afrr_up_fraction"] == 0.0
    assert snapshot["asset"]["soc_initial_frac"] == 0.0
    assert snapshot["asset"]["soc_terminal_frac"] == 0.0
    assert snapshot["asset"]["pump_ramp_power_frac"] == 0.0
    assert snapshot["asset"]["turbine_ramp_power_frac"] == 0.0
    assert snapshot["form"]["bid_quantile"] == 0.0
    from ui.views.configure import _KEYS, _widget_values

    widgets = _widget_values(form)
    assert widgets[_KEYS["bid_quantile"]] == 0.0
    assert widgets[_KEYS["afrr_up_fraction"]] == 0.0
    assert widgets[_KEYS["soc_initial"]] == 0.0


def test_zero_fixed_prices_are_preserved() -> None:
    form = default_live_form()
    form["bid_kind"] = "fixed"
    form["mfrr_fixed_up"] = 0.0
    form["afrr_fixed_up"] = 0.0
    form["afrr_fixed_down"] = 0.0
    form["pv_enabled"] = True
    form["pv_revenue_mode"] = "fixed"
    form["pv_fixed_price"] = 0.0
    mfrr = build_market_case(form, "mfrr").capacity_bid
    afrr = build_market_case(form, "afrr").capacity_bid
    assert mfrr.upward_price_eur_mw_h == 0.0
    assert afrr.upward_price_eur_mw_h == 0.0
    assert afrr.downward_price_eur_mw_h == 0.0
    snapshot = build_snapshot(form, demo=False)
    assert snapshot["balancing"]["mfrr_fixed_up"] == 0.0
    assert snapshot["site"]["pv_fixed_price_eur_mwh"] == 0.0


def test_configerror_is_mapped_not_swallowed_as_raw_valueerror() -> None:
    form = default_live_form()
    form["eta_pump"] = 0.0
    with pytest.raises(ValueError, match="eta_pump") as caught:
        build_snapshot(form, demo=False)
    assert "Traceback" not in str(caught.value)


def test_snapshot_fail_closed_validation() -> None:
    live = build_snapshot(default_live_form(), demo=False)
    empty_nested = deepcopy(live)
    empty_nested["asset"] = {}
    empty_nested["site"] = {}
    empty_nested["derived"] = {}
    assert snapshot_block_reason(empty_nested, live["form"]) == INCOMPLETE_SNAPSHOT
    assert review_warnings(empty_nested) == []

    bad_dates = deepcopy(live)
    bad_dates["period"]["preset"] = PRESET_CUSTOM
    bad_dates["period"]["start_date"] = "not-a-date"
    bad_dates["period"]["end_date"] = "also-bad"
    assert snapshot_block_reason(bad_dates, live["form"]) == INCOMPLETE_SNAPSHOT
    assert review_warnings(bad_dates) == []

    unknown = deepcopy(live)
    unknown["markets"] = ["fcr"]
    assert snapshot_block_reason(unknown, live["form"]) == INCOMPLETE_SNAPSHOT
    duplicated = deepcopy(live)
    duplicated["markets"] = ["da", "da"]
    assert snapshot_block_reason(duplicated, live["form"]) == INCOMPLETE_SNAPSHOT
    unordered = deepcopy(live)
    unordered["markets"] = ["afrr", "da", "mfrr"]
    assert snapshot_block_reason(unordered, live["form"]) == INCOMPLETE_SNAPSHOT

    bool_version = deepcopy(live)
    bool_version["schema_version"] = True
    assert snapshot_block_reason(bool_version, live["form"]) == UNSUPPORTED_SNAPSHOT
    fractional = deepcopy(live)
    fractional["schema_version"] = 1.0
    assert snapshot_block_reason(fractional, live["form"]) == UNSUPPORTED_SNAPSHOT

    missing = deepcopy(live)
    del missing["asset"]["power_pump_mw"]
    assert snapshot_block_reason(missing, live["form"]) == INCOMPLETE_SNAPSHOT
    wrong_type = deepcopy(live)
    wrong_type["site"]["grid_import_mw"] = "1.0"
    assert snapshot_block_reason(wrong_type, live["form"]) == INCOMPLETE_SNAPSHOT

    disagreed = deepcopy(live)
    disagreed["form_fingerprint"] = "not-the-fingerprint"
    assert snapshot_block_reason(disagreed, live["form"]) == INCOMPLETE_SNAPSHOT
    stale = deepcopy(live)
    changed = deepcopy(live["form"])
    changed["common_mw"] = 9.0
    assert snapshot_block_reason(stale, changed) == STALE_SNAPSHOT


def test_clear_snapshot_does_not_require_request_builders() -> None:
    state = default_state()
    store_snapshot(state, {"ok": True}, "x")
    clear_snapshot(state)
    assert state["snapshot"] is None
    assert state["max_stage"] == STAGE_CONFIGURE
