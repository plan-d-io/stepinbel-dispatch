from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from stepinbel.config import MachineCommitmentConfig
from stepinbel.optimizer.highs import solve_sparse_model
from stepinbel.reporting import RUN_ARTIFACT_SCHEMA_VERSION_V2, validate_run_artifacts
from stepinbel.workflows import execute_case_run, execute_market_comparison
from tests.da_solve_helpers import relabel_time_limit_feasible
from ui.flow import (
    STAGE_CONFIGURE,
    configure_another_run,
    default_state,
    store_snapshot,
    unlock_results,
)
from ui.services.artifacts import (
    ERROR_BINDING,
    accept_live_artifacts,
    bind_exact_result_artifacts,
)
from ui.presentation.tokens import DOWNLOADS_ERROR_BODY, REVIEW_READY_TITLE
from ui.services.commitment import (
    BEST_AVAILABLE_TITLE,
    COMMITMENT_EXPANDER,
    COMMITMENT_REVIEW_WARNING_TITLE,
    COMMITMENT_TIME_NOTICE,
    ERROR_MIP_GAP,
    ERROR_MIP_TIME,
    ERROR_TURBINE_PCT,
    FIXED_SPEED_LABEL,
    FORBID_SIMULTANEOUS_LABEL,
    MIP_GAP_LABEL,
    MIP_TIME_LABEL,
    TERMINATION_TIME_LIMIT_FEASIBLE,
    TURBINE_MINIMUM_INPUT_LABEL,
    TURBINE_MINIMUM_LABEL,
    WORKING_COMMITMENT_NOTICE,
    review_commitment_rows,
)
from ui.services.configs import build_simulation_configs
from ui.services.form import PRESET_CUSTOM, default_live_form
from ui.services.launch import TEST_HOOKS
from ui.services.paths import JOB_QUERY_KEY, KIND_CASE, KIND_COMPARISON
from ui.services.request import build_public_request, prepare_launch_request, serialize_public_request
from ui.services.result_downloads import (
    DownloadsError,
    build_download_inventory,
    build_result_zip,
    read_inventory_file,
)
from ui.services.result_technical import load_technical_details
from ui.services.result_warning import load_best_available_warning
from ui.services.snapshot import (
    INCOMPLETE_SNAPSHOT,
    build_snapshot,
    snapshot_block_reason,
)
from ui.services.status import (
    CLASS_FAILED,
    POST_SOLVE_USER_MESSAGE,
    classify_job,
    safe_error_message,
    trusted_status,
)
from ui.tests.result_artifact_fixtures import _planned_job, live_result_record

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


def _button(at: AppTest, label: str):
    return next(item for item in at.button if item.label == label)


def test_lp_defaults_hide_solver_controls_and_emit_schema_v1(tmp_path: Path) -> None:
    form = _form()
    snapshot = build_snapshot(form, demo=False)
    assert snapshot["machine_commitment"]["fixed_speed_pump"] is False
    assert snapshot["machine_commitment"]["turbine_minimum_enabled"] is False
    assert snapshot["machine_commitment"]["turbine_minimum_output_fraction"] == 0.0
    request = build_public_request(
        snapshot,
        output_directory=tmp_path / "lp",
        run_id="stepinbel-20250115T000000Z-abcd1234",
    )
    payload = serialize_public_request(request)
    assert payload["request_schema_version"] == 1
    assert payload["artifact_schema_version"] == 1
    assert "machine_commitment" not in payload["config"]
    assert set(payload["solver_options"]) == {"detailed_output"}
    assert review_commitment_rows(snapshot, market_count=1) is None


def test_each_physical_option_independently_selects_v2(tmp_path: Path) -> None:
    cases = (
        {"fixed_speed_pump": True},
        {"turbine_minimum_enabled": True, "turbine_minimum_output_pct": 18.0},
        {"forbid_simultaneous_operation": True},
    )
    for overrides in cases:
        snapshot = build_snapshot(_form(**overrides), demo=False)
        request = build_public_request(
            snapshot,
            output_directory=tmp_path / f"opt-{next(iter(overrides))}",
            run_id="stepinbel-20250115T000000Z-abcd1234",
        )
        payload = serialize_public_request(request)
        assert payload["request_schema_version"] == 2
        assert payload["artifact_schema_version"] == RUN_ARTIFACT_SCHEMA_VERSION_V2
        assert request.config.machine_commitment.physically_active() is True


def test_all_options_combined_and_conversions(tmp_path: Path) -> None:
    form = _form(
        fixed_speed_pump=True,
        turbine_minimum_enabled=True,
        turbine_minimum_output_pct=18.0,
        forbid_simultaneous_operation=True,
        mip_gap_pct=1.0,
        mip_time_limit_min=15.0,
    )
    configs = build_simulation_configs(form)
    commitment = configs[0][1].machine_commitment
    assert commitment == MachineCommitmentConfig(
        fixed_speed_pump=True,
        turbine_minimum_output_fraction=0.18,
        forbid_simultaneous_operation=True,
    )
    snapshot = build_snapshot(form, demo=False)
    assert snapshot["machine_commitment"]["turbine_minimum_output_fraction"] == 0.18
    assert snapshot["solver_controls"]["mip_rel_gap"] == 0.01
    assert snapshot["solver_controls"]["time_limit_s"] == 900.0
    request = build_public_request(
        snapshot,
        output_directory=tmp_path / "all",
        run_id="stepinbel-20250115T000000Z-abcd1234",
    )
    assert request.solver_options.mip_rel_gap == 0.01
    assert request.solver_options.time_limit_s == 900.0
    rows = dict(review_commitment_rows(snapshot, market_count=1))
    assert rows[FIXED_SPEED_LABEL] == "Enabled"
    assert rows[TURBINE_MINIMUM_LABEL] == "18%"
    assert rows[FORBID_SIMULTANEOUS_LABEL] == "Enabled"
    assert rows["Target optimality gap"] == "1%"
    assert rows["Maximum solve time per market"] == "15 minutes"
    three = dict(review_commitment_rows(snapshot, market_count=3))
    assert three["Maximum solver-time envelope"] == (
        "15 minutes per market · up to 45 minutes across 3 market runs"
    )


def test_disabled_turbine_minimum_is_zero_but_form_keeps_percentage() -> None:
    form = _form(turbine_minimum_enabled=False, turbine_minimum_output_pct=22.5)
    snapshot = build_snapshot(form, demo=False)
    assert snapshot["machine_commitment"]["turbine_minimum_enabled"] is False
    assert snapshot["machine_commitment"]["turbine_minimum_output_fraction"] == 0.0
    assert snapshot["form"]["turbine_minimum_output_pct"] == 22.5
    restored = build_snapshot(snapshot["form"], demo=False)
    restored["form"]["turbine_minimum_enabled"] = True
    enabled = build_snapshot(
        {**snapshot["form"], "turbine_minimum_enabled": True},
        demo=False,
    )
    assert enabled["machine_commitment"]["turbine_minimum_output_fraction"] == 0.225


def test_exact_zero_boolean_nan_inf_and_range_validation() -> None:
    live = build_snapshot(_form(fixed_speed_pump=True), demo=False)
    tampered = deepcopy(live)
    tampered["machine_commitment"]["fixed_speed_pump"] = 1
    assert snapshot_block_reason(tampered, live["form"]) == INCOMPLETE_SNAPSHOT
    zero_gap = deepcopy(live)
    zero_gap["solver_controls"]["mip_rel_gap"] = 0.0
    assert snapshot_block_reason(zero_gap, live["form"]) == INCOMPLETE_SNAPSHOT
    with pytest.raises(ValueError, match=ERROR_TURBINE_PCT):
        build_snapshot(_form(turbine_minimum_enabled=True, turbine_minimum_output_pct=0.0), demo=False)
    with pytest.raises(ValueError, match=ERROR_MIP_GAP):
        build_snapshot(_form(fixed_speed_pump=True, mip_gap_pct=math.nan), demo=False)
    with pytest.raises(ValueError, match=ERROR_MIP_GAP):
        build_snapshot(_form(fixed_speed_pump=True, mip_gap_pct=math.inf), demo=False)
    with pytest.raises(ValueError, match=ERROR_MIP_TIME):
        build_snapshot(_form(fixed_speed_pump=True, mip_time_limit_min=0.0), demo=False)
    with pytest.raises(ValueError, match=ERROR_MIP_GAP):
        build_snapshot(_form(fixed_speed_pump=True, mip_gap_pct=True), demo=False)


def test_disabled_commitment_cannot_emit_schema_v2(tmp_path: Path) -> None:
    snapshot = build_snapshot(_form(turbine_minimum_output_pct=18.0), demo=False)
    request = build_public_request(
        snapshot,
        output_directory=tmp_path / "still-lp",
        run_id="stepinbel-20250115T000000Z-abcd1234",
    )
    assert request.request_schema_version == 1
    assert request.config.machine_commitment.physically_active() is False


def test_snapshot_tamper_of_fraction_is_rejected() -> None:
    form = _form(turbine_minimum_enabled=True, turbine_minimum_output_pct=18.0)
    snapshot = build_snapshot(form, demo=False)
    tampered = deepcopy(snapshot)
    tampered["machine_commitment"]["turbine_minimum_output_fraction"] = 0.5
    assert snapshot_block_reason(tampered, form) == INCOMPLETE_SNAPSHOT
    fingerprint = deepcopy(snapshot)
    fingerprint["form"]["mip_gap_pct"] = 2.0
    assert snapshot_block_reason(fingerprint, fingerprint["form"]) == INCOMPLETE_SNAPSHOT


def test_launch_payload_contains_public_core_values(tmp_path: Path) -> None:
    snapshot = build_snapshot(
        _form(
            fixed_speed_pump=True,
            turbine_minimum_enabled=True,
            turbine_minimum_output_pct=18.0,
            mip_gap_pct=1.0,
            mip_time_limit_min=15.0,
        ),
        demo=False,
    )
    kind, payload, request = prepare_launch_request(
        snapshot,
        snapshot["form"],
        demo=False,
        output_directory=tmp_path / "launch",
        run_id="stepinbel-20250115T000000Z-abcd1234",
        created_at_utc=datetime(2025, 1, 15, tzinfo=timezone.utc),
    )
    assert kind == KIND_CASE
    assert payload["config"]["machine_commitment"]["turbine_minimum_output_fraction"] == 0.18
    assert payload["solver_options"]["mip_rel_gap"] == 0.01
    assert payload["solver_options"]["time_limit_s"] == 900.0
    assert request.config.machine_commitment.turbine_minimum_output_fraction == 0.18


def test_configure_hides_controls_until_a_constraint_is_enabled() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    labels = [item.label for item in at.number_input]
    assert MIP_GAP_LABEL not in labels
    assert MIP_TIME_LABEL not in labels
    assert TURBINE_MINIMUM_INPUT_LABEL not in labels
    assert COMMITMENT_TIME_NOTICE not in _text(at)
    _checkbox(at, FIXED_SPEED_LABEL).set_value(True)
    at.run()
    assert not at.exception
    assert COMMITMENT_TIME_NOTICE in _text(at)
    assert _number(at, MIP_GAP_LABEL).value == 1.5
    assert _number(at, MIP_TIME_LABEL).value == 15.0


def test_turbine_percentage_restore_and_continue_back() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    _checkbox(at, "mFRR").set_value(False)
    _checkbox(at, "aFRR").set_value(False)
    at.run()
    _checkbox(at, TURBINE_MINIMUM_LABEL).set_value(True)
    at.run()
    _number(at, TURBINE_MINIMUM_INPUT_LABEL).set_value(22.5)
    at.run()
    _checkbox(at, TURBINE_MINIMUM_LABEL).set_value(False)
    at.run()
    assert TURBINE_MINIMUM_INPUT_LABEL not in [item.label for item in at.number_input]
    form = at.session_state["sib"]["form"]
    assert form["turbine_minimum_enabled"] is False
    assert form["turbine_minimum_output_pct"] == 22.5
    _checkbox(at, TURBINE_MINIMUM_LABEL).set_value(True)
    at.run()
    assert _number(at, TURBINE_MINIMUM_INPUT_LABEL).value == 22.5
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    assert COMMITMENT_EXPANDER in _text(at)
    assert "22.5%" in _text(at)
    assert COMMITMENT_REVIEW_WARNING_TITLE in _text(at)
    assert COMMITMENT_TIME_NOTICE in _text(at)
    assert REVIEW_READY_TITLE in _text(at)
    _button(at, "Back").click()
    at.run()
    assert _checkbox(at, TURBINE_MINIMUM_LABEL).value is True
    assert _number(at, TURBINE_MINIMUM_INPUT_LABEL).value == 22.5
    _button(at, "2  Review & run").click()
    at.run()
    assert "22.5%" in _text(at)


def test_ordinary_lp_review_has_no_runtime_warning() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    _checkbox(at, "mFRR").set_value(False)
    _checkbox(at, "aFRR").set_value(False)
    at.run()
    _button(at, "Continue").click()
    at.run()
    assert not at.exception
    text = _text(at)
    assert REVIEW_READY_TITLE in text
    assert COMMITMENT_TIME_NOTICE not in text
    assert COMMITMENT_REVIEW_WARNING_TITLE not in text
    assert COMMITMENT_EXPANDER not in text


def _patch_time_limit(achieved_gap: float = 0.025):
    original = solve_sparse_model

    def wrapper(model, options, *, build_s, require_usable=True):
        return relabel_time_limit_feasible(
            original(model, options, build_s=build_s, require_usable=False),
            achieved_gap=achieved_gap,
        )

    return patch("stepinbel.optimizer.solve.solve_sparse_model", wrapper)


def _execute_da_commitment(tmp_path: Path, *, time_limit: bool = False) -> tuple[dict, dict, dict]:
    form = _form(fixed_speed_pump=True)
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20250115T120000Z-aaaa0001" if time_limit else "stepinbel-20250115T120000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_CASE, markets=["da"])
    output = Path(record["output_directory"])
    request = build_public_request(snapshot, output_directory=output, run_id=job_id)
    if time_limit:
        with _patch_time_limit():
            execute_case_run(request)
    else:
        execute_case_run(request)
    validate_run_artifacts(output)
    result = live_result_record(record, snapshot)
    return record, result, snapshot


def test_accepted_within_gap_has_no_warning(tmp_path: Path) -> None:
    record, result, _snapshot = _execute_da_commitment(tmp_path)
    warning = load_best_available_warning(result, job=record, outputs_root=tmp_path)
    assert warning is None
    payload = load_technical_details(result, market="da", job=record, outputs_root=tmp_path)
    software = dict(payload["software"])
    assert software["Model type"] == "Mixed-integer linear program (MILP)"
    assert software["Formulation"] == "MILP"
    assert software["Requested optimality gap"] == "1.5%"
    assert "Termination" in software
    groups = dict(payload["configured_groups"])
    assert "Machine operating constraints" in groups


def test_time_limited_one_market_opens_with_warning(tmp_path: Path) -> None:
    record, result, _snapshot = _execute_da_commitment(tmp_path, time_limit=True)
    warning = load_best_available_warning(result, job=record, outputs_root=tmp_path)
    assert warning is not None
    assert warning["title"] == BEST_AVAILABLE_TITLE
    assert "15-minute time limit" in warning["body"]
    assert "1.5% target" in warning["body"]
    assert "2.5% optimality gap" in warning["body"]
    bind_exact_result_artifacts(result, job=record, outputs_root=tmp_path)
    accepted = accept_live_artifacts(record, outputs_root=tmp_path)
    assert accepted["job_id"] == record["job_id"]


def test_mixed_comparison_identifies_affected_markets(tmp_path: Path) -> None:
    form = default_live_form()
    form["market_afrr"] = False
    form["period_preset"] = PRESET_CUSTOM
    form["start_date"] = "2025-01-15"
    form["end_date"] = "2025-01-15"
    form["fixed_speed_pump"] = True
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20250115T130000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_COMPARISON, markets=["da", "mfrr"])
    output = Path(record["output_directory"])
    request = build_public_request(snapshot, output_directory=output, run_id=job_id)
    original = solve_sparse_model
    calls = {"n": 0}

    def wrapper(model, options, *, build_s, require_usable=True):
        solved = original(model, options, build_s=build_s, require_usable=False)
        calls["n"] += 1
        if calls["n"] >= 2:
            return relabel_time_limit_feasible(solved, achieved_gap=0.025)
        return solved

    with patch("stepinbel.optimizer.solve.solve_sparse_model", wrapper):
        execute_market_comparison(request)
    result = live_result_record(record, snapshot)
    warning = load_best_available_warning(result, job=record, outputs_root=tmp_path)
    assert warning is not None
    assert "mFRR" in warning["body"] or "Day-ahead" in warning["body"]
    assert "best available validated schedules" in warning["body"]
    assert "interpreted carefully" in warning["body"]
    lowered = warning["body"].lower()
    assert "accepted within" not in lowered
    assert " is optimal" not in lowered
    assert "are optimal" not in lowered


def test_time_limit_without_incumbent_remains_failed(tmp_path: Path) -> None:
    form = _form(fixed_speed_pump=True)
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20250115T140000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_CASE, markets=["da"])
    output = Path(record["output_directory"])
    output.mkdir(parents=True)
    status = {
        "status_schema_version": 1,
        "run_id": job_id,
        "state": "failed",
        "artifact_schema_version": 2,
        "current_stage": "solve",
        "started_at_utc": "2025-01-15T12:00:00Z",
        "updated_at_utc": "2025-01-15T12:01:00Z",
        "completed_at_utc": "2025-01-15T12:01:00Z",
        "elapsed_seconds": 60.0,
        "error_category": "execution",
        "error_message": "no feasible solution",
    }
    (output / "run_status.json").write_text(json.dumps(status), encoding="utf-8")
    trusted = trusted_status(record, status)
    assert trusted is not None
    assert classify_job(record, status=status, pid_alive=lambda _pid: False, outputs_root=tmp_path) == CLASS_FAILED


def test_malformed_termination_fails_closed(tmp_path: Path) -> None:
    record, result, _snapshot = _execute_da_commitment(tmp_path, time_limit=True)
    meta_path = Path(record["output_directory"]) / "run_metadata.json"
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["solver"]["termination"] = "accepted_within_requested_mip_gap"
    meta_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bind_exact_result_artifacts(result, job=record, outputs_root=tmp_path)
    with pytest.raises(ValueError):
        load_best_available_warning(result, job=record, outputs_root=tmp_path)


def test_demo_and_schema_v1_technical_unchanged() -> None:
    from ui.services.artifacts import open_demo_artifacts

    payload = load_technical_details(open_demo_artifacts(), market="da")
    software = dict(payload["software"])
    assert software["Model type"] == "Continuous linear program"
    assert "Formulation" not in software
    assert load_best_available_warning(open_demo_artifacts()) is None


def test_downloads_accept_v2_and_reject_swapped_tree(tmp_path: Path) -> None:
    record, result, _snapshot = _execute_da_commitment(tmp_path)
    items = build_download_inventory(result, job=record, outputs_root=tmp_path)
    names = {item.relative_path for item in items}
    assert "run_request.json" in names
    assert "run_metadata.json" in names
    raw = read_inventory_file(result, "run_request.json", job=record, outputs_root=tmp_path)
    payload = json.loads(raw.decode("utf-8"))
    assert payload["config"]["machine_commitment"]["fixed_speed_pump"] is True
    package = build_result_zip(result, job=record, outputs_root=tmp_path)
    assert package[:2] == b"PK"
    original = (Path(record["output_directory"]) / "run_request.json").read_bytes()
    (Path(record["output_directory"]) / "run_request.json").write_bytes(original + b"x")
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        read_inventory_file(result, "run_request.json", job=record, outputs_root=tmp_path)
    with pytest.raises(DownloadsError, match=DOWNLOADS_ERROR_BODY):
        build_result_zip(result, job=record, outputs_root=tmp_path)
    (Path(record["output_directory"]) / "run_request.json").write_bytes(original)
    other = tmp_path / "other"
    other.mkdir()
    swapped = dict(result)
    swapped["output_directory"] = str(other)
    with pytest.raises(ValueError, match=ERROR_BINDING):
        bind_exact_result_artifacts(swapped, job=record, outputs_root=tmp_path)


def test_results_configure_clears_v2_job(tmp_path: Path) -> None:
    record, result, snapshot = _execute_da_commitment(tmp_path)
    state = default_state()
    store_snapshot(state, snapshot, snapshot["form_fingerprint"])
    unlock_results(state, result)
    state["job"] = record
    configure_another_run(state)
    assert state["stage"] == STAGE_CONFIGURE
    assert state["job"] is None
    assert state["result"] is None


def test_tab_navigation_does_not_launch(tmp_path: Path) -> None:
    record, result, snapshot = _execute_da_commitment(tmp_path)
    calls: list[str] = []

    def bang(**_kwargs):
        calls.append("popen")
        raise AssertionError("must not launch")

    TEST_HOOKS.clear()
    TEST_HOOKS["popen"] = bang
    TEST_HOOKS["outputs_root"] = tmp_path
    try:
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
        warning_text = _text(at)
        if BEST_AVAILABLE_TITLE in warning_text:
            pytest.fail("accepted fixture must not show the best-available warning")
        next(item for item in at.button if item.label == "Technical details").click()
        at.run()
        next(item for item in at.button if item.label == "Downloads").click()
        at.run()
        assert not at.exception
        assert calls == []
        assert WORKING_COMMITMENT_NOTICE not in _text(at)
    finally:
        TEST_HOOKS.clear()


def test_post_solve_failure_uses_plain_language() -> None:
    raw = (
        "post-solve feasibility or accounting checks failed: "
        "bound=0.000e+00 init/term=0.000e+00 balance=1.110e-16 "
        "ramp=0.000e+00 pv=9.769e-07 grid=2.220e-16 capacity=0.000e+00 "
        "interval_acc=0.000e+00 summary_acc=0.000e+00 objective=1.421e-14 "
        "status=280321x315360"
    )
    shown = safe_error_message(raw)
    assert shown == POST_SOLVE_USER_MESSAGE
    assert "9.769e-07" not in shown
    assert "status=" not in shown
    assert "pv=" not in shown
