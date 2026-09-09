from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from stepinbel.cli.app import main
from stepinbel.cli.builders import (
    build_case_request,
    build_machine_commitment,
    build_solver_options,
)
from stepinbel.cli.errors import CliError
from stepinbel.cli.parser import parse_args
from stepinbel.config import (
    DayAheadCase,
    MachineCommitmentConfig,
    SimulationConfig,
)
from stepinbel.optimizer import SolverOptions
from stepinbel.optimizer.highs import solve_sparse_model
from stepinbel.optimizer.types import (
    COMPARISON_MIP_TIME_LIMIT_WARNING,
    MIP_TIME_LIMIT_FEASIBLE_WARNING,
    TERMINATION_TIME_LIMIT_FEASIBLE,
)
from tests.da_solve_helpers import relabel_time_limit_feasible
from stepinbel.reporting import (
    RUN_ARTIFACT_SCHEMA_VERSION,
    RUN_ARTIFACT_SCHEMA_VERSION_V2,
    validate_asset_sweep_artifacts,
    validate_market_comparison_artifacts,
    validate_run_artifacts,
)
from stepinbel.workflows import (
    RunRequestError,
    build_asset_sweep_request,
    build_case_run_request,
    build_market_comparison_request,
    build_symmetric_asset_size_candidates,
    case_run_request_from_payload,
    execute_asset_sweep,
    execute_case_run,
    execute_market_comparison,
    serialize_case_run_request,
)
from tests.workflow_helpers import (
    comparison_configs,
    da_config,
    da_period,
    one_day_belgian,
    utc,
)

ALL_ON = MachineCommitmentConfig(
    fixed_speed_pump=True,
    turbine_minimum_output_fraction=0.18,
    forbid_simultaneous_operation=True,
)


def _commitment_config(*, market_case=None, commitment=ALL_ON) -> SimulationConfig:
    return SimulationConfig(
        period=da_period(hours=2),
        market_case=market_case or DayAheadCase(),
        machine_commitment=commitment,
    )


def test_schema_v1_lp_request_round_trip_omits_commitment(data_root: Path, tmp_path: Path) -> None:
    request = build_case_run_request(da_config(), data_root, tmp_path / "v1")
    payload = serialize_case_run_request(request)
    assert payload["request_schema_version"] == 1
    assert payload["artifact_schema_version"] == RUN_ARTIFACT_SCHEMA_VERSION
    assert "machine_commitment" not in payload["config"]
    assert set(payload["solver_options"]) == {"detailed_output"}
    restored = case_run_request_from_payload(payload)
    assert restored.config.machine_commitment == MachineCommitmentConfig()
    assert restored.request_schema_version == 1


def test_schema_v1_rejects_commitment_and_mip_fields(data_root: Path, tmp_path: Path) -> None:
    payload = serialize_case_run_request(build_case_run_request(da_config(), data_root, tmp_path / "v1b"))
    extra = copy.deepcopy(payload)
    extra["config"]["machine_commitment"] = {
        "fixed_speed_pump": False,
        "turbine_minimum_output_fraction": 0.0,
        "forbid_simultaneous_operation": False,
    }
    with pytest.raises(RunRequestError, match="unexpected"):
        case_run_request_from_payload(extra)
    mixed = copy.deepcopy(payload)
    mixed["solver_options"]["mip_rel_gap"] = 0.01
    with pytest.raises(RunRequestError, match="unexpected"):
        case_run_request_from_payload(mixed)
    swapped = copy.deepcopy(payload)
    swapped["artifact_schema_version"] = RUN_ARTIFACT_SCHEMA_VERSION_V2
    with pytest.raises(RunRequestError, match="schema-v1"):
        case_run_request_from_payload(swapped)


def test_schema_v2_round_trip_and_rejects_inactive(data_root: Path, tmp_path: Path) -> None:
    request = build_case_run_request(_commitment_config(), data_root, tmp_path / "v2")
    assert request.request_schema_version == 2
    assert request.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION_V2
    payload = serialize_case_run_request(request)
    assert payload["config"]["machine_commitment"]["fixed_speed_pump"] is True
    assert payload["solver_options"]["mip_rel_gap"] == 0.015
    assert payload["solver_options"]["time_limit_s"] == 900.0
    restored = case_run_request_from_payload(payload)
    assert restored.config.machine_commitment == ALL_ON
    inactive = copy.deepcopy(payload)
    inactive["config"]["machine_commitment"]["fixed_speed_pump"] = False
    inactive["config"]["machine_commitment"]["turbine_minimum_output_fraction"] = 0.0
    inactive["config"]["machine_commitment"]["forbid_simultaneous_operation"] = False
    with pytest.raises(RunRequestError, match="schema-v2"):
        case_run_request_from_payload(inactive)


def test_old_lp_request_is_not_reinterpreted_as_milp(data_root: Path, tmp_path: Path) -> None:
    request = build_case_run_request(da_config(), data_root, tmp_path / "lp")
    payload = serialize_case_run_request(request)
    restored = case_run_request_from_payload(payload)
    assert restored.config.machine_commitment.physically_active() is False
    assert restored.request_schema_version == 1


def test_one_market_milp_artifacts_validate_and_reopen(data_root: Path, tmp_path: Path) -> None:
    request = build_case_run_request(
        _commitment_config(),
        data_root,
        tmp_path / "milp-run",
        solver_options=SolverOptions(mip_rel_gap=0.01, time_limit_s=30.0),
        run_id="milp-core-01",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    result = execute_case_run(request)
    artifacts = validate_run_artifacts(result.directory)
    assert result.result.solver.continuous_lp is False
    assert result.result.solver.diagnostics["termination"] == "accepted_within_requested_mip_gap"
    metadata = json.loads((result.directory / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["artifact_schema_version"] == RUN_ARTIFACT_SCHEMA_VERSION_V2
    assert metadata["solver"]["formulation"] == "milp"
    assert metadata["solver"]["requested_mip_gap"] == 0.01
    frozen = case_run_request_from_payload(
        json.loads((result.directory / "run_request.json").read_text(encoding="utf-8"))
    )
    assert frozen.config.machine_commitment == ALL_ON
    assert artifacts["summary.json"].is_file()


def _with_commitment(configs: dict[str, SimulationConfig]) -> dict[str, SimulationConfig]:
    return {
        name: SimulationConfig(
            period=config.period,
            market_case=config.market_case,
            asset=config.asset,
            site=config.site,
            machine_commitment=ALL_ON,
        )
        for name, config in configs.items()
    }


def test_comparison_shares_commitment_and_solver_controls(data_root: Path, tmp_path: Path) -> None:
    configs = _with_commitment(
        comparison_configs(period=one_day_belgian(), markets=("da", "mfrr"))
    )
    request = build_market_comparison_request(
        configs,
        data_root,
        tmp_path / "milp-compare",
        solver_options=SolverOptions(mip_rel_gap=0.02, time_limit_s=30.0),
        run_id="milp-cmp-01",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    assert request.comparison_request_schema_version == 2
    for child in request.case_requests.values():
        assert child.config.machine_commitment == ALL_ON
        assert child.solver_options.mip_rel_gap == 0.02
        assert child.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION_V2
    result = execute_market_comparison(request)
    validate_market_comparison_artifacts(result.directory)
    mismatched = {
        "da": configs["da"],
        "mfrr": SimulationConfig(
            period=configs["mfrr"].period,
            market_case=configs["mfrr"].market_case,
            asset=configs["mfrr"].asset,
            site=configs["mfrr"].site,
            machine_commitment=MachineCommitmentConfig(fixed_speed_pump=True),
        ),
    }
    with pytest.raises(RunRequestError, match="machine-commitment"):
        build_market_comparison_request(mismatched, data_root, tmp_path / "mismatch")


def test_sweep_copies_commitment_onto_children(data_root: Path, tmp_path: Path) -> None:
    template = SimulationConfig(
        period=da_period(hours=2),
        market_case=DayAheadCase(),
        machine_commitment=ALL_ON,
    )
    candidates = build_symmetric_asset_size_candidates(
        template.asset,
        powers_mw=[1.0, 2.0],
        storage_hours=[4.0],
    )
    request = build_asset_sweep_request(
        template,
        candidates,
        data_root,
        tmp_path / "milp-sweep",
        solver_options=SolverOptions(time_limit_s=30.0),
        run_id="milp-swp-01",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    assert request.asset_sweep_request_schema_version == 2
    for child in request.case_requests.values():
        assert child.config.machine_commitment == ALL_ON
    result = execute_asset_sweep(request)
    validate_asset_sweep_artifacts(result.directory)


def test_cli_parses_commitment_and_mip_controls() -> None:
    ns = parse_args(
        [
            "run",
            "--market",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
            "--fixed-speed-pump",
            "--forbid-simultaneous-operation",
            "--turbine-minimum-output-fraction",
            "0.18",
            "--mip-rel-gap",
            "0.02",
            "--mip-time-limit-s",
            "30",
        ]
    )
    commitment = build_machine_commitment(ns)
    options = build_solver_options(ns)
    assert commitment == ALL_ON
    assert options.mip_rel_gap == 0.02
    assert options.time_limit_s == 30.0
    request = build_case_request(ns)
    assert request.request_schema_version == 2
    assert request.config.machine_commitment == ALL_ON


def test_cli_rejects_invalid_mip_gap() -> None:
    ns = parse_args(
        [
            "run",
            "--market",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
            "--mip-rel-gap",
            "2",
        ]
    )
    with pytest.raises(CliError, match="mip_rel_gap"):
        build_solver_options(ns)


def test_cli_executes_short_milp_run(data_root: Path, tmp_path: Path) -> None:
    out = io.StringIO()
    err = io.StringIO()
    with patch("sys.stdout", out), patch("sys.stderr", err):
        code = main(
            [
                "run",
                "--market",
                "da",
                "--utc-start",
                "2025-01-15T00:00:00Z",
                "--utc-end",
                "2025-01-15T02:00:00Z",
                "--data-dir",
                str(data_root),
                "--output-dir",
                str(tmp_path / "cli-milp"),
                "--fixed-speed-pump",
                "--quiet",
            ]
        )
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["formulation"] == "milp"
    assert payload["continuous_lp"] is False
    assert payload["termination"] == "accepted_within_requested_mip_gap"
    assert "requested_mip_gap" in payload
    assert "achieved_mip_gap" in payload
    assert "warning" not in payload


def _patch_solve(wrapper):
    return patch("stepinbel.optimizer.solve.solve_sparse_model", wrapper)


def test_time_limited_one_market_artifacts_validate_and_reopen(
    data_root: Path, tmp_path: Path
) -> None:
    original = solve_sparse_model

    def wrapper(model, options, *, build_s, require_usable=True):
        return relabel_time_limit_feasible(
            original(model, options, build_s=build_s, require_usable=False),
            achieved_gap=0.02,
        )

    request = build_case_run_request(
        _commitment_config(),
        data_root,
        tmp_path / "milp-time-limit",
        solver_options=SolverOptions(mip_rel_gap=0.01, time_limit_s=30.0),
        run_id="milp-tl-01",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    with _patch_solve(wrapper):
        result = execute_case_run(request)
    artifacts = validate_run_artifacts(result.directory)
    assert result.result.solver.status == "time_limit"
    assert result.result.solver.diagnostics["termination"] == TERMINATION_TIME_LIMIT_FEASIBLE
    metadata = json.loads((result.directory / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["solver"]["status"] == "time_limit"
    assert metadata["solver"]["termination"] == TERMINATION_TIME_LIMIT_FEASIBLE
    assert metadata["solver"]["requested_mip_gap"] == 0.01
    assert metadata["solver"]["achieved_mip_gap"] == pytest.approx(0.02)
    report = (result.directory / "report.txt").read_text(encoding="utf-8")
    assert "Requested MIP gap was not reached." in report
    assert "not optimal" in report
    frozen = case_run_request_from_payload(
        json.loads((result.directory / "run_request.json").read_text(encoding="utf-8"))
    )
    assert frozen.config.machine_commitment == ALL_ON
    assert artifacts["summary.json"].is_file()


def test_comparison_keeps_mixed_terminations_and_records_warning(
    data_root: Path, tmp_path: Path
) -> None:
    original = solve_sparse_model
    calls = {"n": 0}

    def wrapper(model, options, *, build_s, require_usable=True):
        solved = original(model, options, build_s=build_s, require_usable=False)
        calls["n"] += 1
        if calls["n"] >= 2:
            return relabel_time_limit_feasible(solved, achieved_gap=0.025)
        return solved

    configs = _with_commitment(
        comparison_configs(period=one_day_belgian(), markets=("da", "mfrr"))
    )
    request = build_market_comparison_request(
        configs,
        data_root,
        tmp_path / "milp-compare-mixed",
        solver_options=SolverOptions(mip_rel_gap=0.01, time_limit_s=30.0),
        run_id="milp-cmp-mixed",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    with _patch_solve(wrapper):
        result = execute_market_comparison(request)
    validate_market_comparison_artifacts(result.directory)
    da = result.case_runs["da"].result.solver.diagnostics["termination"]
    mfrr = result.case_runs["mfrr"].result.solver.diagnostics["termination"]
    assert {da, mfrr} == {
        "accepted_within_requested_mip_gap",
        TERMINATION_TIME_LIMIT_FEASIBLE,
    }
    metadata = json.loads(
        (result.directory / "comparison_metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["mip_termination_warning"] == COMPARISON_MIP_TIME_LIMIT_WARNING
    children = metadata["children"]
    assert children["da"]["termination"] in {
        "accepted_within_requested_mip_gap",
        TERMINATION_TIME_LIMIT_FEASIBLE,
    }
    assert children["mfrr"]["termination"] in {
        "accepted_within_requested_mip_gap",
        TERMINATION_TIME_LIMIT_FEASIBLE,
    }
    assert children["da"]["termination"] != children["mfrr"]["termination"]
    report = (result.directory / "report.txt").read_text(encoding="utf-8")
    assert COMPARISON_MIP_TIME_LIMIT_WARNING in report


def test_cli_warns_and_exits_zero_for_time_limited_incumbent(
    data_root: Path, tmp_path: Path
) -> None:
    original = solve_sparse_model

    def wrapper(model, options, *, build_s, require_usable=True):
        return relabel_time_limit_feasible(
            original(model, options, build_s=build_s, require_usable=False)
        )

    out = io.StringIO()
    err = io.StringIO()
    with _patch_solve(wrapper), patch("sys.stdout", out), patch("sys.stderr", err):
        code = main(
            [
                "run",
                "--market",
                "da",
                "--utc-start",
                "2025-01-15T00:00:00Z",
                "--utc-end",
                "2025-01-15T02:00:00Z",
                "--data-dir",
                str(data_root),
                "--output-dir",
                str(tmp_path / "cli-milp-tl"),
                "--fixed-speed-pump",
                "--quiet",
            ]
        )
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["termination"] == TERMINATION_TIME_LIMIT_FEASIBLE
    assert payload["warning"] == MIP_TIME_LIMIT_FEASIBLE_WARNING
    assert MIP_TIME_LIMIT_FEASIBLE_WARNING in err.getvalue()


def test_cli_lp_path_has_no_warning(data_root: Path, tmp_path: Path) -> None:
    out = io.StringIO()
    err = io.StringIO()
    with patch("sys.stdout", out), patch("sys.stderr", err):
        code = main(
            [
                "run",
                "--market",
                "da",
                "--utc-start",
                "2025-01-15T00:00:00Z",
                "--utc-end",
                "2025-01-15T02:00:00Z",
                "--data-dir",
                str(data_root),
                "--output-dir",
                str(tmp_path / "cli-lp"),
                "--quiet",
            ]
        )
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["formulation"] == "lp"
    assert payload["continuous_lp"] is True
    assert "warning" not in payload
    assert "termination" not in payload
    assert MIP_TIME_LIMIT_FEASIBLE_WARNING not in err.getvalue()
