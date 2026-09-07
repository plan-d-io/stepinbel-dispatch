"""Frozen dedicated-market comparison request construction and JSON round-trip."""

from __future__ import annotations

import hashlib
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
from stepinbel.reporting.constants import (
    COMPARISON_MARKETS,
    MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION,
)
from stepinbel.workflows.constants import (
    BEHAVIOURAL_BASELINE,
    CASE_RUN_REQUEST_SCHEMA_VERSION,
    MARKET_COMPARISON_REQUEST_SCHEMA_VERSION,
    RUN_ARTIFACT_SCHEMA_VERSION,
)
from stepinbel.workflows.errors import RunRequestError
from stepinbel.workflows.request import CaseRunRequest, case_run_request_from_payload
from stepinbel.workflows.serialize import (
    dumps_json,
    loads_json,
    request_to_payload,
    require_absolute_path,
    require_keys,
    require_mapping,
    require_sha256,
    require_str,
    require_utc_seconds,
    resolve_builder_path,
    serialize_baseline,
    validate_run_id,
)


def _require_schema_version(value: object, expected: int, field: str) -> int:
    if type(value) is not int or value != expected:
        raise RunRequestError(f"{field} is not supported", category="invalid_request")
    return value


def _require_nested_case_schema(payload: object, market: str) -> dict:
    if not isinstance(payload, dict):
        raise RunRequestError(
            f"case_requests[{market}] must be an object",
            category="invalid_request",
        )
    _require_schema_version(
        payload.get("request_schema_version"),
        CASE_RUN_REQUEST_SCHEMA_VERSION,
        f"case_requests[{market}].request_schema_version",
    )
    _require_schema_version(
        payload.get("artifact_schema_version"),
        RUN_ARTIFACT_SCHEMA_VERSION,
        f"case_requests[{market}].artifact_schema_version",
    )
    return payload


def child_run_id(parent_run_id: str, market: str) -> str:
    prefix = parent_run_id[:110]
    digest = hashlib.sha256(f"{parent_run_id}\0{market}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{market}-{digest}"


def canonical_comparison_markets(keys: object, *, field: str = "case_requests") -> tuple[str, ...]:
    """Return the supported subset in canonical da, mfrr, afrr order."""
    try:
        key_set = set(keys)  # type: ignore[arg-type]
    except TypeError as exc:
        raise RunRequestError(f"{field} must be a mapping", category="invalid_request") from exc
    unknown = key_set - set(COMPARISON_MARKETS)
    if unknown:
        raise RunRequestError(
            f"{field} keys must be a subset of da, mfrr, and afrr",
            category="invalid_request",
        )
    if len(key_set) not in {2, 3}:
        raise RunRequestError(
            f"{field} must contain exactly two or three dedicated markets",
            category="invalid_request",
        )
    return tuple(market for market in COMPARISON_MARKETS if market in key_set)


def _require_case_mapping(value: object) -> dict[str, CaseRunRequest]:
    if not isinstance(value, Mapping):
        raise RunRequestError("case_requests must be a mapping", category="invalid_request")
    selected = canonical_comparison_markets(value, field="case_requests")
    cases: dict[str, CaseRunRequest] = {}
    for market in selected:
        request = value[market]
        if not isinstance(request, CaseRunRequest):
            raise RunRequestError(
                f"case_requests[{market}] must be a CaseRunRequest",
                category="invalid_request",
            )
        cases[market] = request
    return cases


def _child_output_directory(parent_output: Path, market: str) -> Path:
    return require_absolute_path(parent_output / "cases" / market, f"cases/{market}")


@dataclass(frozen=True)
class MarketComparisonRow:
    """One ranked dedicated-market alternative."""

    market: str
    case_run_id: str
    revenue_rank: int
    difference_from_highest_eur: float
    interval_count: int
    duration_hours: float
    e_max_mwh: float
    energy_gross_eur: float
    grid_charging_cost_eur: float
    market_energy_net_eur: float
    capacity_revenue_eur: float
    pv_revenue_eur: float
    total_site_revenue_eur: float
    pumped_mwh: float
    turbined_mwh: float
    full_cycles: float
    pv_available_mwh: float
    pv_self_consumed_mwh: float
    pv_exported_mwh: float
    pv_curtailed_mwh: float
    simultaneous_interval_count: int
    simultaneous_overlap_mwh: float
    simultaneous_interval_energy_net_eur: float


@dataclass(frozen=True)
class MarketComparisonRequest:
    """Immutable description of one dedicated DA/mFRR/aFRR comparison."""

    comparison_request_schema_version: int
    comparison_artifact_schema_version: int
    run_id: str
    created_at_utc: datetime
    software_version: str
    data_directory: Path
    data_manifest_sha256: str
    output_directory: Path
    case_requests: Mapping[str, CaseRunRequest]
    behavioural_baseline: Mapping[str, str]

    def __post_init__(self) -> None:
        try:
            _require_schema_version(
                self.comparison_request_schema_version,
                MARKET_COMPARISON_REQUEST_SCHEMA_VERSION,
                "comparison_request_schema_version",
            )
            _require_schema_version(
                self.comparison_artifact_schema_version,
                MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION,
                "comparison_artifact_schema_version",
            )
            run_id = validate_run_id(self.run_id)
            object.__setattr__(self, "run_id", run_id)
            object.__setattr__(
                self,
                "created_at_utc",
                require_utc_seconds(self.created_at_utc, "created_at_utc"),
            )
            if not isinstance(self.software_version, str) or not self.software_version:
                raise RunRequestError(
                    "software_version must be a non-empty string",
                    category="invalid_request",
                )
            object.__setattr__(
                self,
                "data_directory",
                require_absolute_path(self.data_directory, "data_directory"),
            )
            object.__setattr__(
                self,
                "output_directory",
                require_absolute_path(self.output_directory, "output_directory"),
            )
            object.__setattr__(
                self,
                "data_manifest_sha256",
                require_sha256(self.data_manifest_sha256, "data_manifest_sha256"),
            )
            if not isinstance(self.behavioural_baseline, Mapping):
                raise RunRequestError(
                    "behavioural_baseline must be a mapping",
                    category="invalid_request",
                )
            baseline = dict(self.behavioural_baseline)
            object.__setattr__(self, "behavioural_baseline", MappingProxyType(baseline))
            if dict(self.behavioural_baseline) != dict(BEHAVIOURAL_BASELINE):
                raise RunRequestError(
                    "behavioural baseline identity does not match the accepted PHS baseline",
                    category="invalid_request",
                )
            cases = _require_case_mapping(self.case_requests)
            object.__setattr__(self, "case_requests", MappingProxyType(cases))
            _validate_shared_children(self)
        except RunRequestError:
            raise
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise RunRequestError(str(exc), category="invalid_request") from exc


def _validate_shared_children(request: MarketComparisonRequest) -> None:
    selected = tuple(request.case_requests)
    children = [request.case_requests[market] for market in selected]
    first = children[0]
    for market, child in zip(selected, children, strict=True):
        if child.config.market != market:
            raise RunRequestError(
                f"case_requests[{market}] market must be {market!r}",
                category="invalid_request",
            )
        if child.run_id != child_run_id(request.run_id, market):
            raise RunRequestError(
                f"case_requests[{market}] run_id is not the deterministic child identifier",
                category="invalid_request",
            )
        if child.output_directory != _child_output_directory(request.output_directory, market):
            raise RunRequestError(
                f"case_requests[{market}] output_directory must be under the comparison cases path",
                category="invalid_request",
            )
        if child.created_at_utc != request.created_at_utc:
            raise RunRequestError(
                "child created_at_utc must match the comparison request",
                category="invalid_request",
            )
        if child.software_version != request.software_version:
            raise RunRequestError(
                "child software_version must match the comparison request",
                category="invalid_request",
            )
        if child.data_directory != request.data_directory:
            raise RunRequestError(
                "child data_directory must match the comparison request",
                category="invalid_request",
            )
        if child.data_manifest_sha256 != request.data_manifest_sha256:
            raise RunRequestError(
                "child data_manifest_sha256 must match the comparison request",
                category="invalid_request",
            )
        if child.solver_options != first.solver_options:
            raise RunRequestError(
                "child solver options must be identical across markets",
                category="invalid_request",
            )
        if dict(child.behavioural_baseline) != dict(request.behavioural_baseline):
            raise RunRequestError(
                "child behavioural baseline must match the comparison request",
                category="invalid_request",
            )
        if child.config.period != first.config.period:
            raise RunRequestError(
                "period configuration must be identical across markets",
                category="invalid_request",
            )
        if child.config.asset != first.config.asset:
            raise RunRequestError(
                "asset configuration must be identical across markets",
                category="invalid_request",
            )
        if child.config.site != first.config.site:
            raise RunRequestError(
                "site configuration must be identical across markets",
                category="invalid_request",
            )


def build_market_comparison_request(
    case_configs,
    data_directory: str | Path,
    output_directory: str | Path,
    *,
    solver_options: SolverOptions | None = None,
    run_id: str | None = None,
    created_at_utc: datetime | None = None,
) -> MarketComparisonRequest:
    """Freeze one validated DA/mFRR/aFRR comparison. Does not write files."""
    try:
        if not isinstance(case_configs, Mapping):
            raise RunRequestError("case_configs must be a mapping", category="invalid_request")
        selected = canonical_comparison_markets(case_configs, field="case_configs")
        configs: dict[str, SimulationConfig] = {}
        for market in selected:
            config = case_configs[market]
            if type(config) is not SimulationConfig:
                raise RunRequestError(
                    f"case_configs[{market}] must be a SimulationConfig",
                    category="invalid_request",
                )
            if config.market != market:
                raise RunRequestError(
                    f"case_configs[{market}] market must be {market!r}",
                    category="invalid_request",
                )
            configs[market] = config
        first = configs[selected[0]]
        for market, config in configs.items():
            if config.period != first.period:
                raise RunRequestError(
                    "period configuration must be identical across markets",
                    category="invalid_request",
                )
            if config.asset != first.asset:
                raise RunRequestError(
                    "asset configuration must be identical across markets",
                    category="invalid_request",
                )
            if config.site != first.site:
                raise RunRequestError(
                    "site configuration must be identical across markets",
                    category="invalid_request",
                )
        if solver_options is None:
            solver_options = SolverOptions()
        elif type(solver_options) is not SolverOptions:
            raise RunRequestError("solver_options must be a SolverOptions", category="invalid_request")
        if created_at_utc is None:
            created_at_utc = datetime.now(timezone.utc).replace(microsecond=0)
        else:
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
        if run_id is None:
            stamp = created_at_utc.strftime("%Y%m%dT%H%M%SZ")
            run_id = f"{stamp}_{uuid.uuid4().hex[:8]}"
        else:
            run_id = validate_run_id(run_id)
        data_root = resolve_builder_path(data_directory, "data_directory")
        output_root = resolve_builder_path(output_directory, "output_directory")
    except RunRequestError:
        raise
    except (TypeError, ValueError, OSError) as exc:
        raise RunRequestError(str(exc), category="invalid_request") from exc

    try:
        bundle = open_published_bundle(data_root)
    except DataBundleError as exc:
        raise RunRequestError(str(exc), category="data_bundle") from exc
    except Exception as exc:
        raise RunRequestError(str(exc), category="data_bundle") from exc

    children: dict[str, CaseRunRequest] = {}
    for market in configs:
        children[market] = CaseRunRequest(
            request_schema_version=CASE_RUN_REQUEST_SCHEMA_VERSION,
            run_id=child_run_id(run_id, market),
            created_at_utc=created_at_utc,
            software_version=__version__,
            artifact_schema_version=RUN_ARTIFACT_SCHEMA_VERSION,
            data_directory=data_root,
            data_manifest_sha256=bundle.manifest_sha256,
            output_directory=output_root / "cases" / market,
            config=configs[market],
            solver_options=solver_options,
            behavioural_baseline=dict(BEHAVIOURAL_BASELINE),
        )
    return MarketComparisonRequest(
        comparison_request_schema_version=MARKET_COMPARISON_REQUEST_SCHEMA_VERSION,
        comparison_artifact_schema_version=MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION,
        run_id=run_id,
        created_at_utc=created_at_utc,
        software_version=__version__,
        data_directory=data_root,
        data_manifest_sha256=bundle.manifest_sha256,
        output_directory=output_root,
        case_requests=children,
        behavioural_baseline=dict(BEHAVIOURAL_BASELINE),
    )


_COMPARISON_REQUEST_KEYS = (
    "comparison_request_schema_version",
    "comparison_artifact_schema_version",
    "run_id",
    "created_at_utc",
    "software_version",
    "data_directory",
    "data_manifest_sha256",
    "output_directory",
    "case_requests",
    "behavioural_baseline",
)


def serialize_market_comparison_request(request: MarketComparisonRequest) -> dict[str, object]:
    """Return the explicit JSON object for one comparison request."""
    if not isinstance(request, MarketComparisonRequest):
        raise RunRequestError(
            "request must be a MarketComparisonRequest",
            category="invalid_request",
        )
    from stepinbel.workflows.serialize import format_utc

    return {
        "comparison_request_schema_version": request.comparison_request_schema_version,
        "comparison_artifact_schema_version": request.comparison_artifact_schema_version,
        "run_id": request.run_id,
        "created_at_utc": format_utc(request.created_at_utc),
        "software_version": request.software_version,
        "data_directory": str(request.data_directory),
        "data_manifest_sha256": request.data_manifest_sha256,
        "output_directory": str(request.output_directory),
        "case_requests": {
            market: request_to_payload(request.case_requests[market])
            for market in request.case_requests
        },
        "behavioural_baseline": serialize_baseline(request.behavioural_baseline),
    }


def market_comparison_request_from_payload(payload: object) -> MarketComparisonRequest:
    """Reconstruct and revalidate a comparison request from an explicit JSON object."""
    if not isinstance(payload, dict):
        raise RunRequestError("comparison request payload must be an object", category="invalid_request")
    try:
        require_keys(payload, _COMPARISON_REQUEST_KEYS, "comparison request")
        cases_payload = require_mapping(payload["case_requests"], "case_requests")
        selected = canonical_comparison_markets(cases_payload, field="case_requests")
        children = {}
        for market in selected:
            nested = _require_nested_case_schema(cases_payload[market], market)
            children[market] = case_run_request_from_payload(nested)
        from stepinbel.workflows.serialize import parse_utc

        return MarketComparisonRequest(
            comparison_request_schema_version=_require_schema_version(
                payload["comparison_request_schema_version"],
                MARKET_COMPARISON_REQUEST_SCHEMA_VERSION,
                "comparison_request_schema_version",
            ),
            comparison_artifact_schema_version=_require_schema_version(
                payload["comparison_artifact_schema_version"],
                MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION,
                "comparison_artifact_schema_version",
            ),
            run_id=require_str(payload["run_id"], "run_id"),
            created_at_utc=parse_utc(payload["created_at_utc"], "created_at_utc"),
            software_version=require_str(payload["software_version"], "software_version"),
            data_directory=require_absolute_path(payload["data_directory"], "data_directory"),
            data_manifest_sha256=require_sha256(
                payload["data_manifest_sha256"],
                "data_manifest_sha256",
            ),
            output_directory=require_absolute_path(payload["output_directory"], "output_directory"),
            case_requests=children,
            behavioural_baseline=payload["behavioural_baseline"],
        )
    except RunRequestError:
        raise
    except Exception as exc:
        raise RunRequestError(str(exc), category="invalid_request") from exc


def load_market_comparison_request(path: str | Path) -> MarketComparisonRequest:
    """Load a comparison request JSON file without merging defaults."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (TypeError, ValueError, OSError) as exc:
        raise RunRequestError(str(exc), category="invalid_request") from exc
    payload = loads_json(text)
    return market_comparison_request_from_payload(payload)


def dumps_comparison_request(request: MarketComparisonRequest) -> str:
    return dumps_json(serialize_market_comparison_request(request))
