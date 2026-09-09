"""Synchronous one-case execution against the public solver boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from stepinbel.config import ConfigError, SimulationConfig
from stepinbel.data import DataAccessError, DataBundleError, load_market_data, open_published_bundle
from stepinbel.markets import MarketInputError
from stepinbel.optimizer import DispatchResult, ModelError, SolverError, solve_case
from stepinbel.reporting.io import ArtifactError
from stepinbel.workflows.constants import STAGES
from stepinbel.workflows.errors import (
    RunCancelledError,
    RunError,
    RunExecutionError,
    RunRequestError,
)
from stepinbel.workflows.events import RunEvent, RunJournal
from stepinbel.workflows.request import CaseRunRequest


@dataclass(frozen=True)
class CaseRun:
    """Completed or inspectable result of one dedicated-market run."""

    directory: Path
    request: CaseRunRequest
    result: DispatchResult
    status: Mapping[str, object]
    artifacts: Mapping[str, Path]

    def __post_init__(self) -> None:
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


def _validate_request(request: object) -> CaseRunRequest:
    if not isinstance(request, CaseRunRequest):
        raise RunRequestError("request must be a CaseRunRequest", category="invalid_request")
    if not isinstance(request.config, SimulationConfig):
        raise RunRequestError("config must be a SimulationConfig", category="invalid_request")
    return request


def execute_case_run(
    request: CaseRunRequest,
    *,
    progress: Callable[[RunEvent], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> CaseRun:
    """Execute one frozen request and write a validated audit directory."""
    request = _validate_request(request)
    output = request.output_directory
    if output.exists():
        raise RunRequestError(
            f"output directory already exists: {output}",
            category="invalid_output",
        )

    from stepinbel.reporting.artifacts import (
        validate_run_artifacts,
        write_artifact_manifest,
        write_run_artifacts,
    )

    output.mkdir(parents=True)
    journal = RunJournal(
        output,
        request.run_id,
        progress,
        artifact_schema_version=request.artifact_schema_version,
    )
    current = STAGES[0]
    result: DispatchResult | None = None
    artifacts: Mapping[str, Path] | None = None
    try:
        for current in STAGES:
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
            elif current == "load_data":
                bundle = open_published_bundle(request.data_directory)
                if bundle.manifest_sha256 != request.data_manifest_sha256:
                    raise RunRequestError(
                        "published data manifest hash does not match the frozen request",
                        category="data_bundle",
                    )
                data_slice = load_market_data(bundle, request.config)
            elif current == "solve":
                result = solve_case(data_slice, options=request.solver_options)
                journal.check_cancel(cancel_requested)
            elif current == "write_artifacts":
                if result is None:
                    raise RunExecutionError("solve produced no result", category="execution")
                bundle = open_published_bundle(request.data_directory)
                if bundle.manifest_sha256 != request.data_manifest_sha256:
                    raise RunRequestError(
                        "published data manifest hash does not match the frozen request",
                        category="data_bundle",
                    )
                write_run_artifacts(output, request, result, bundle)
            elif current == "verify_artifacts":
                journal.mark_completed_status()
                final_event = journal.complete_stage(current, notify=False)
                write_artifact_manifest(
                    output,
                    request.run_id,
                    artifact_schema_version=request.artifact_schema_version,
                )
                artifacts = validate_run_artifacts(output)
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
        if isinstance(exc, RunExecutionError) and exc.category == "progress":
            raise mapped from exc
        try:
            journal.record_failed(current, mapped, mapped.category)
        except Exception:
            pass
        raise mapped from exc

    if result is None or artifacts is None:
        raise RunExecutionError("run finished without a validated result", category="execution")
    return CaseRun(
        directory=output,
        request=request,
        result=result,
        status=dict(journal.status),
        artifacts=artifacts,
    )
