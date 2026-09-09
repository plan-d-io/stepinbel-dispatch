"""Synchronous finite asset-parameter sweep against the one-case workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from stepinbel.config import ConfigError
from stepinbel.data import DataAccessError, DataBundleError, open_published_bundle
from stepinbel.markets import MarketInputError
from stepinbel.optimizer import ModelError, SolverError
from stepinbel.reporting.io import ArtifactError, atomic_write_text
from stepinbel.workflows.errors import (
    RunCancelledError,
    RunError,
    RunExecutionError,
    RunRequestError,
)
from stepinbel.workflows.events import RunEvent, RunJournal
from stepinbel.workflows.execute import CaseRun, execute_case_run
from stepinbel.workflows.sweep_request import (
    AssetSweepRequest,
    AssetSweepRow,
    dumps_asset_sweep_request,
    sweep_stages,
)


@dataclass(frozen=True)
class AssetSweepRun:
    """Completed or inspectable result of one dedicated-market asset sweep."""

    directory: Path
    request: AssetSweepRequest
    case_runs: Mapping[str, CaseRun]
    rows: tuple[AssetSweepRow, ...]
    highest_revenue_candidate_id: str
    status: Mapping[str, object]
    artifacts: Mapping[str, Path]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_runs", MappingProxyType(dict(self.case_runs)))
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "status", MappingProxyType(dict(self.status)))
        object.__setattr__(self, "artifacts", MappingProxyType(dict(self.artifacts)))

    @property
    def ok(self) -> bool:
        return self.status.get("state") == "completed"


def _map_exception(exc: Exception) -> RunError:
    if isinstance(exc, RunError):
        return exc
    if isinstance(exc, ConfigError):
        return RunRequestError(str(exc), category="invalid_configuration")
    if isinstance(exc, DataBundleError):
        return RunRequestError(str(exc), category="data_bundle")
    if isinstance(exc, DataAccessError):
        return RunRequestError(str(exc), category="data_coverage")
    if isinstance(exc, MarketInputError):
        return RunRequestError(str(exc), category="market_input")
    if isinstance(exc, (ModelError, SolverError)):
        return RunExecutionError(str(exc), category="optimizer")
    if isinstance(exc, ArtifactError):
        return RunExecutionError(str(exc), category="artifact_validation")
    if isinstance(exc, OSError):
        return RunExecutionError(str(exc), category="artifact_write")
    return RunExecutionError(str(exc), category="execution")


def _validate_request(request: object) -> AssetSweepRequest:
    if type(request) is not AssetSweepRequest:
        raise RunRequestError(
            "request must be an AssetSweepRequest",
            category="invalid_request",
        )
    return request


def execute_asset_sweep(
    request: AssetSweepRequest,
    *,
    progress: Callable[[RunEvent], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> AssetSweepRun:
    """Execute frozen asset candidates sequentially and write a validated sweep directory."""
    request = _validate_request(request)
    output = request.output_directory
    if output.exists():
        raise RunRequestError(
            f"output directory already exists: {output}",
            category="invalid_output",
        )

    from stepinbel.reporting.sweep_artifacts import (
        ranked_rows_from_case_runs,
        validate_asset_sweep_artifacts,
        write_asset_sweep_artifact_manifest,
        write_asset_sweep_artifacts,
    )

    stages = sweep_stages(request.candidate_order)
    output.mkdir(parents=True)
    journal = RunJournal(
        output,
        request.run_id,
        progress,
        stages=stages,
        artifact_schema_version=request.asset_sweep_artifact_schema_version,
    )
    atomic_write_text(output / "asset_sweep_request.json", dumps_asset_sweep_request(request))
    current = stages[0]
    case_runs: dict[str, CaseRun] = {}
    rows: tuple[AssetSweepRow, ...] | None = None
    highest: str | None = None
    artifacts: Mapping[str, Path] | None = None
    try:
        for current in stages:
            journal.check_cancel(cancel_requested)
            journal.start_stage(current)
            if current == "validate_request":
                _validate_request(request)
            elif current == "validate_data":
                bundle = open_published_bundle(request.data_directory)
                if bundle.manifest_sha256 != request.data_manifest_sha256:
                    raise RunRequestError(
                        "published data manifest hash does not match the frozen request",
                        category="data_bundle",
                    )
            elif current.startswith("execute_"):
                candidate_id = current.removeprefix("execute_")
                case_runs[candidate_id] = execute_case_run(
                    request.case_requests[candidate_id],
                    progress=progress,
                    cancel_requested=cancel_requested,
                )
                journal.check_cancel(cancel_requested)
            elif current == "aggregate":
                if set(case_runs) != set(request.candidate_order):
                    raise RunExecutionError(
                        "sweep cannot aggregate before all child cases complete",
                        category="execution",
                    )
                rows, highest = ranked_rows_from_case_runs(request, case_runs)
                write_asset_sweep_artifacts(output, request, case_runs, rows, highest)
            elif current == "verify_artifacts":
                if rows is None or highest is None:
                    raise RunExecutionError("sweep produced no ranked rows", category="execution")
                journal.mark_completed_status()
                final_event = journal.complete_stage(current, notify=False)
                write_asset_sweep_artifact_manifest(output, request)
                artifacts = validate_asset_sweep_artifacts(output)
                journal.notify(final_event)
                continue
            else:
                raise RunExecutionError(f"unknown stage {current}", category="execution")
            journal.complete_stage(current)
    except RunCancelledError as exc:
        journal.record_cancelled(current)
        raise RunCancelledError(str(exc), category="cancelled") from exc
    except Exception as exc:
        mapped = _map_exception(exc)
        already_failed = str(journal.status.get("state")) == "failed"
        if isinstance(exc, RunExecutionError) and exc.category == "progress":
            if not already_failed:
                try:
                    journal.record_failed(current, mapped, mapped.category, notify=False)
                except Exception:
                    pass
            raise mapped from exc
        try:
            journal.record_failed(current, mapped, mapped.category)
        except Exception:
            pass
        raise mapped from exc

    if rows is None or highest is None or artifacts is None:
        raise RunExecutionError("sweep finished without a validated result", category="execution")
    return AssetSweepRun(
        directory=output,
        request=request,
        case_runs=case_runs,
        rows=rows,
        highest_revenue_candidate_id=highest,
        status=dict(journal.status),
        artifacts=artifacts,
    )
