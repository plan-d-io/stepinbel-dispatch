"""Frozen one-case request construction and JSON round-trip."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from stepinbel import __version__
from stepinbel.config import SimulationConfig
from stepinbel.data import DataBundleError, open_published_bundle
from stepinbel.optimizer import SolverOptions
from stepinbel.workflows.constants import (
    BEHAVIOURAL_BASELINE,
    CASE_RUN_REQUEST_SCHEMA_VERSION,
    CASE_RUN_REQUEST_SCHEMA_VERSION_V2,
    CASE_RUN_REQUEST_SCHEMA_VERSION_V3,
    SUPPORTED_CASE_SCHEMA_VERSIONS,
)
from stepinbel.workflows.errors import RunRequestError
from stepinbel.workflows.serialize import (
    dumps_json,
    loads_json,
    payload_to_request_fields,
    preferred_case_schema_versions,
    request_to_payload,
    require_absolute_path,
    require_matching_case_schema_versions,
    require_schema_version,
    require_sha256,
    require_utc_seconds,
    resolve_builder_path,
    validate_run_id,
)


def _require_type(value: object, expected: type, field: str) -> None:
    if type(value) is not expected and not isinstance(value, expected):
        raise RunRequestError(
            f"{field} must be a {expected.__name__}",
            category="invalid_request",
        )


@dataclass(frozen=True)
class CaseRunRequest:
    """Immutable, serializable description of one dedicated-market run."""

    request_schema_version: int
    run_id: str
    created_at_utc: datetime
    software_version: str
    artifact_schema_version: int
    data_directory: Path
    data_manifest_sha256: str
    output_directory: Path
    config: SimulationConfig
    solver_options: SolverOptions
    behavioural_baseline: Mapping[str, str]

    def __post_init__(self) -> None:
        require_schema_version(
            self.request_schema_version,
            SUPPORTED_CASE_SCHEMA_VERSIONS,
            "request_schema_version",
        )
        require_schema_version(
            self.artifact_schema_version,
            SUPPORTED_CASE_SCHEMA_VERSIONS,
            "artifact_schema_version",
        )
        require_matching_case_schema_versions(
            self.request_schema_version, self.artifact_schema_version
        )
        validate_run_id(self.run_id)
        object.__setattr__(self, "created_at_utc", require_utc_seconds(self.created_at_utc, "created_at_utc"))
        if not isinstance(self.software_version, str) or not self.software_version:
            raise RunRequestError("software_version must be a non-empty string", category="invalid_request")
        if not isinstance(self.config, SimulationConfig):
            raise RunRequestError("config must be a SimulationConfig", category="invalid_request")
        if not isinstance(self.solver_options, SolverOptions):
            raise RunRequestError("solver_options must be a SolverOptions", category="invalid_request")
        if (
            self.request_schema_version == CASE_RUN_REQUEST_SCHEMA_VERSION
            and self.config.machine_commitment.physically_active()
        ):
            raise RunRequestError(
                "schema-v1 requests cannot represent enabled machine-commitment options",
                category="invalid_request",
            )
        if (
            self.request_schema_version == CASE_RUN_REQUEST_SCHEMA_VERSION_V2
            and not self.config.machine_commitment.physically_active()
        ):
            raise RunRequestError(
                "schema-v2 requests require at least one enabled machine-commitment option",
                category="invalid_request",
            )
        if (
            self.request_schema_version in {
                CASE_RUN_REQUEST_SCHEMA_VERSION,
                CASE_RUN_REQUEST_SCHEMA_VERSION_V2,
            }
            and self.config.wind_enabled()
        ):
            raise RunRequestError(
                "schema-v1 and schema-v2 requests cannot represent enabled co-located wind",
                category="invalid_request",
            )
        if (
            self.request_schema_version == CASE_RUN_REQUEST_SCHEMA_VERSION_V3
            and not self.config.wind_enabled()
        ):
            raise RunRequestError(
                "schema-v3 requests require enabled co-located wind",
                category="invalid_request",
            )
        object.__setattr__(self, "data_directory", require_absolute_path(self.data_directory, "data_directory"))
        object.__setattr__(self, "output_directory", require_absolute_path(self.output_directory, "output_directory"))
        object.__setattr__(
            self,
            "data_manifest_sha256",
            require_sha256(self.data_manifest_sha256, "data_manifest_sha256"),
        )
        try:
            if not isinstance(self.behavioural_baseline, Mapping):
                raise RunRequestError(
                    "behavioural_baseline must be a mapping",
                    category="invalid_request",
                )
            baseline = dict(self.behavioural_baseline)
        except RunRequestError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise RunRequestError(
                "behavioural_baseline is invalid",
                category="invalid_request",
            ) from exc
        object.__setattr__(self, "behavioural_baseline", MappingProxyType(baseline))
        if dict(self.behavioural_baseline) != dict(BEHAVIOURAL_BASELINE):
            raise RunRequestError(
                "behavioural baseline identity does not match the accepted PHS baseline",
                category="invalid_request",
            )


def build_case_run_request(
    config: SimulationConfig,
    data_directory: str | Path,
    output_directory: str | Path,
    *,
    solver_options: SolverOptions | None = None,
    run_id: str | None = None,
    created_at_utc: datetime | None = None,
) -> CaseRunRequest:
    """Freeze one validated request. Does not write files."""
    if type(config) is not SimulationConfig:
        raise RunRequestError("config must be a SimulationConfig", category="invalid_request")
    if solver_options is None:
        solver_options = SolverOptions()
    elif type(solver_options) is not SolverOptions:
        raise RunRequestError("solver_options must be a SolverOptions", category="invalid_request")
    if created_at_utc is None:
        created_at_utc = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        try:
            if not isinstance(created_at_utc, datetime):
                raise RunRequestError(
                    "created_at_utc must be a datetime",
                    category="invalid_request",
                )
            if created_at_utc.tzinfo is None:
                raise RunRequestError(
                    "created_at_utc must be timezone-aware UTC",
                    category="invalid_request",
                )
            created_at_utc = require_utc_seconds(
                created_at_utc.astimezone(timezone.utc).replace(microsecond=0),
                "created_at_utc",
            )
        except RunRequestError:
            raise
        except Exception as exc:
            raise RunRequestError(str(exc), category="invalid_request") from exc
    if run_id is None:
        stamp = created_at_utc.strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{stamp}_{uuid.uuid4().hex[:8]}"
    else:
        run_id = validate_run_id(run_id)

    data_root = resolve_builder_path(data_directory, "data_directory")
    output_root = resolve_builder_path(output_directory, "output_directory")
    try:
        bundle = open_published_bundle(data_root)
    except DataBundleError as exc:
        raise RunRequestError(str(exc), category="data_bundle") from exc
    except Exception as exc:
        raise RunRequestError(str(exc), category="data_bundle") from exc

    request_version, artifact_version = preferred_case_schema_versions(config)
    return CaseRunRequest(
        request_schema_version=request_version,
        run_id=run_id,
        created_at_utc=created_at_utc,
        software_version=__version__,
        artifact_schema_version=artifact_version,
        data_directory=data_root,
        data_manifest_sha256=bundle.manifest_sha256,
        output_directory=output_root,
        config=config,
        solver_options=solver_options,
        behavioural_baseline=dict(BEHAVIOURAL_BASELINE),
    )


def serialize_case_run_request(request: CaseRunRequest) -> dict[str, object]:
    """Return the explicit JSON object for one request."""
    if not isinstance(request, CaseRunRequest):
        raise RunRequestError("request must be a CaseRunRequest", category="invalid_request")
    return request_to_payload(request)


def case_run_request_from_payload(payload: object) -> CaseRunRequest:
    """Reconstruct and revalidate a request from an explicit JSON object."""
    if not isinstance(payload, dict):
        raise RunRequestError("request payload must be an object", category="invalid_request")
    try:
        fields = payload_to_request_fields(payload)
        return CaseRunRequest(**fields)
    except RunRequestError:
        raise
    except Exception as exc:
        raise RunRequestError(str(exc), category="invalid_request") from exc


def load_case_run_request(path: str | Path) -> CaseRunRequest:
    """Load a request JSON file without merging defaults."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (TypeError, ValueError, OSError) as exc:
        raise RunRequestError(str(exc), category="invalid_request") from exc
    payload = loads_json(text)
    return case_run_request_from_payload(payload)


def write_case_run_request_json(path: Path, request: CaseRunRequest) -> str:
    """Serialize the request to a JSON document string."""
    return dumps_json(serialize_case_run_request(request))
