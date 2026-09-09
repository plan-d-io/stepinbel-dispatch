"""Command handlers. Workflows own execution, ranking, and validation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from stepinbel.data import coverage_from_bundle, open_published_bundle
from stepinbel.reporting import (
    validate_asset_sweep_artifacts,
    validate_market_comparison_artifacts,
    validate_run_artifacts,
)
from stepinbel.workflows import (
    AssetSweepRun,
    CaseRun,
    MarketComparisonRun,
    RunEvent,
    execute_asset_sweep,
    execute_case_run,
    execute_market_comparison,
    load_asset_sweep_request,
    load_case_run_request,
    load_market_comparison_request,
)

from stepinbel.optimizer import (
    COMPARISON_MIP_TIME_LIMIT_WARNING,
    MIP_TIME_LIMIT_FEASIBLE_WARNING,
    TERMINATION_TIME_LIMIT_FEASIBLE,
)
from stepinbel.cli.builders import (
    build_case_request,
    build_comparison_request,
    build_sweep_request,
    has_option,
    option_value,
    reject_request_overrides,
)
from stepinbel.cli.errors import CliError
from stepinbel.cli.output import (
    _require_finite_number,
    emit_progress,
    status_payload,
    write_success,
    write_warning,
)
from stepinbel.cli.signals import run_with_cancellation

_VALIDATORS = {
    "run": validate_run_artifacts,
    "comparison": validate_market_comparison_artifacts,
    "sweep": validate_asset_sweep_artifacts,
}


def _progress(quiet: bool) -> Callable[[RunEvent], None] | None:
    if quiet:
        return None
    return emit_progress


def _absolute(path: Path) -> str:
    return str(path.expanduser().resolve())


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def success_run(result: CaseRun) -> dict[str, Any]:
    payload = {
        "ok": True,
        "kind": "run",
        "run_id": result.request.run_id,
        "market": result.request.config.market,
        "output_directory": _absolute(result.request.output_directory),
        "status": status_payload(result.status),
        "artifact_count": len(result.artifacts),
        "interval_count": _require_finite_number(result.result.summary.interval_count, "interval_count"),
        "total_site_revenue_eur": _require_finite_number(
            result.result.summary.total_site_revenue_eur,
            "total_site_revenue_eur",
        ),
        "solver_name": result.result.solver.solver_name,
        "solver_version": result.result.solver.solver_version,
    }
    payload.update(_formulation_payload(result.request.config.machine_commitment.physically_active(), result.result.solver))
    warning = _run_warning(result)
    if warning is not None:
        payload["warning"] = warning
    return payload


def _formulation_payload(active: bool, solver) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "formulation": "milp" if active else "lp",
        "continuous_lp": bool(solver.continuous_lp),
    }
    if not active:
        return payload
    diagnostics = dict(solver.diagnostics)
    requested = diagnostics.get("requested_mip_gap")
    achieved = diagnostics.get("achieved_mip_gap", diagnostics.get("mip_gap"))
    payload["termination"] = diagnostics.get("termination")
    payload["requested_mip_gap"] = None if requested is None else float(requested)
    payload["achieved_mip_gap"] = None if achieved is None else float(achieved)
    return payload


def _solver_termination(solver) -> object:
    return dict(solver.diagnostics).get("termination")


def _run_warning(result: CaseRun) -> str | None:
    if not result.request.config.machine_commitment.physically_active():
        return None
    if _solver_termination(result.result.solver) == TERMINATION_TIME_LIMIT_FEASIBLE:
        return MIP_TIME_LIMIT_FEASIBLE_WARNING
    return None


def _comparison_warning(case_runs: Mapping[str, CaseRun]) -> str | None:
    for run in case_runs.values():
        if not run.request.config.machine_commitment.physically_active():
            continue
        if _solver_termination(run.result.solver) == TERMINATION_TIME_LIMIT_FEASIBLE:
            return COMPARISON_MIP_TIME_LIMIT_WARNING
    return None


def _write_completed(payload: Mapping[str, Any]) -> int:
    warning = payload.get("warning")
    if type(warning) is str and warning:
        write_warning(warning)
    return write_success(payload)


def success_comparison(result: MarketComparisonRun) -> dict[str, Any]:
    first = next(iter(result.case_runs.values()))
    payload = {
        "ok": True,
        "kind": "comparison",
        "run_id": result.request.run_id,
        "output_directory": _absolute(result.request.output_directory),
        "status": status_payload(result.status),
        "artifact_count": len(result.artifacts),
        "highest_revenue_market": result.highest_revenue_market,
        "case_count": len(result.case_runs),
    }
    payload.update(
        _formulation_payload(
            first.request.config.machine_commitment.physically_active(),
            first.result.solver,
        )
    )
    warning = _comparison_warning(result.case_runs)
    if warning is not None:
        payload["warning"] = warning
    return payload


def success_sweep(result: AssetSweepRun) -> dict[str, Any]:
    first = next(iter(result.case_runs.values()))
    payload = {
        "ok": True,
        "kind": "sweep",
        "run_id": result.request.run_id,
        "market": result.request.market,
        "output_directory": _absolute(result.request.output_directory),
        "status": status_payload(result.status),
        "artifact_count": len(result.artifacts),
        "highest_revenue_candidate_id": result.highest_revenue_candidate_id,
        "candidate_count": len(result.request.candidate_order),
    }
    payload.update(
        _formulation_payload(
            first.request.config.machine_commitment.physically_active(),
            first.result.solver,
        )
    )
    warning = _comparison_warning(result.case_runs)
    if warning is not None:
        payload["warning"] = warning
    return payload


def _command_run(namespace: argparse.Namespace) -> int:
    quiet = bool(getattr(namespace, "quiet", False))
    if has_option(namespace, "request"):
        reject_request_overrides(namespace)
        request = load_case_run_request(option_value(namespace, "request"))
    else:
        request = build_case_request(namespace)

    def body(cancel_requested: Callable[[], bool]) -> int:
        result = execute_case_run(request, progress=_progress(quiet), cancel_requested=cancel_requested)
        return _write_completed(success_run(result))

    return run_with_cancellation(body)


def _command_compare(namespace: argparse.Namespace) -> int:
    quiet = bool(getattr(namespace, "quiet", False))
    if has_option(namespace, "request"):
        reject_request_overrides(namespace)
        request = load_market_comparison_request(option_value(namespace, "request"))
    else:
        request = build_comparison_request(namespace)

    def body(cancel_requested: Callable[[], bool]) -> int:
        result = execute_market_comparison(request, progress=_progress(quiet), cancel_requested=cancel_requested)
        return _write_completed(success_comparison(result))

    return run_with_cancellation(body)


def _command_sweep(namespace: argparse.Namespace) -> int:
    quiet = bool(getattr(namespace, "quiet", False))
    if has_option(namespace, "request"):
        reject_request_overrides(namespace)
        request = load_asset_sweep_request(option_value(namespace, "request"))
    else:
        request = build_sweep_request(namespace)

    def body(cancel_requested: Callable[[], bool]) -> int:
        result = execute_asset_sweep(request, progress=_progress(quiet), cancel_requested=cancel_requested)
        return _write_completed(success_sweep(result))

    return run_with_cancellation(body)


def _command_validate(namespace: argparse.Namespace) -> int:
    kind = option_value(namespace, "kind")
    directory = Path(option_value(namespace, "directory"))
    artifacts: Mapping[str, Path] = _VALIDATORS[kind](directory)
    return write_success(
        {
            "ok": True,
            "kind": kind,
            "directory": _absolute(directory),
            "artifact_count": len(artifacts),
        }
    )


def _window_payload(window) -> dict[str, Any]:
    return {
        "start_utc": _format_utc(window.start_utc),
        "end_exclusive_utc": _format_utc(window.end_exclusive_utc),
        "interval_count": window.interval_count,
        "duration_hours": window.duration_hours,
    }


def _command_data_info(namespace: argparse.Namespace) -> int:
    root = Path(option_value(namespace, "data_dir"))
    bundle = open_published_bundle(root)
    coverage = coverage_from_bundle(bundle)
    tables = {
        stem: {
            "row_count": table.parquet_row_count,
            "sha256": table.actual_sha256,
        }
        for stem, table in bundle.tables.items()
    }
    return write_success(
        {
            "ok": True,
            "kind": "data",
            "data_directory": _absolute(bundle.root),
            "manifest_path": _absolute(bundle.manifest_path),
            "manifest_sha256": bundle.manifest_sha256,
            "pipeline_version": bundle.pipeline_version,
            "built_at_utc": bundle.built_at_utc,
            "git_commit": bundle.git_commit,
            "partial_build": bundle.partial_build,
            "tables": tables,
            "coverage": {
                "da_prices": _window_payload(coverage.da_prices),
                "balancing": _window_payload(coverage.balancing),
                "mfrr_capacity": _window_payload(coverage.mfrr_capacity),
                "afrr_capacity": _window_payload(coverage.afrr_capacity),
                "pv": _window_payload(coverage.pv),
            },
            "pv_regions": list(coverage.pv_regions),
        }
    )


COMMANDS = {
    "run": _command_run,
    "compare": _command_compare,
    "sweep": _command_sweep,
    "validate": _command_validate,
    "data-info": _command_data_info,
}


def dispatch(namespace: argparse.Namespace) -> int:
    command = getattr(namespace, "command", None)
    handler = COMMANDS.get(command)
    if handler is None:
        raise CliError("a subcommand is required")
    return handler(namespace)
