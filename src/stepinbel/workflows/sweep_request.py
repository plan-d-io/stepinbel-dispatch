"""Frozen finite asset-parameter sweep request construction and JSON round-trip."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from stepinbel import __version__
from stepinbel.config import AssetConfig, ConfigError, SimulationConfig
from stepinbel.data import DataBundleError, open_published_bundle
from stepinbel.optimizer import SolverOptions
from stepinbel.reporting.constants import (
    ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION,
    ASSET_SWEEP_ROW_FIELDS,
    COMPARISON_MARKETS,
)
from stepinbel.workflows.constants import (
    ASSET_SWEEP_REQUEST_SCHEMA_VERSION,
    BEHAVIOURAL_BASELINE,
    CASE_RUN_REQUEST_SCHEMA_VERSION,
    MAX_ASSET_SWEEP_CANDIDATES,
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

_CANDIDATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_WINDOWS_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_SWEEP_REQUEST_KEYS = (
    "asset_sweep_request_schema_version",
    "asset_sweep_artifact_schema_version",
    "run_id",
    "created_at_utc",
    "software_version",
    "data_directory",
    "data_manifest_sha256",
    "output_directory",
    "market",
    "candidate_order",
    "candidate_labels",
    "case_requests",
    "behavioural_baseline",
)


def _require_schema_version(value: object, expected: int, field: str) -> int:
    if type(value) is not int or value != expected:
        raise RunRequestError(f"{field} is not supported", category="invalid_request")
    return value


def _wrap_public_error(exc: Exception) -> RunRequestError:
    if isinstance(exc, RunRequestError):
        return exc
    return RunRequestError(str(exc), category="invalid_request")


def validate_candidate_id(value: object) -> str:
    if type(value) is not str:
        raise RunRequestError("candidate_id must be a string", category="invalid_request")
    if not _CANDIDATE_ID_RE.fullmatch(value) or value.lower() in _WINDOWS_RESERVED:
        raise RunRequestError("candidate_id is not a path-safe identifier", category="invalid_request")
    return value


def validate_candidate_label(value: object) -> str:
    if type(value) is not str:
        raise RunRequestError("label must be a string", category="invalid_request")
    if not value or value != value.strip() or len(value) > 120:
        raise RunRequestError("label is invalid", category="invalid_request")
    if "\r" in value or "\n" in value or not value.isprintable():
        raise RunRequestError("label is invalid", category="invalid_request")
    return value


def child_run_id(parent_run_id: str, candidate_id: str) -> str:
    digest = hashlib.sha256(f"{parent_run_id}\0{candidate_id}".encode("utf-8")).hexdigest()[:12]
    suffix = f"-{candidate_id}-{digest}"
    prefix = parent_run_id[: max(1, 128 - len(suffix))]
    return f"{prefix}{suffix}"


def _child_output_directory(parent_output: Path, candidate_id: str) -> Path:
    return require_absolute_path(parent_output / "cases" / candidate_id, f"cases/{candidate_id}")


def grid_import_limit_mode(site) -> str:
    return "fixed_site_limit" if site.grid_import_mw is not None else "candidate_pump_rating"


def grid_export_limit_mode(site) -> str:
    return "fixed_site_limit" if site.grid_export_mw is not None else "candidate_turbine_rating"


def _require_ordered_candidates(value: object) -> tuple[AssetSweepCandidate, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise RunRequestError(
            "candidates must be an ordered sequence of AssetSweepCandidate",
            category="invalid_request",
        )
    if not isinstance(value, Sequence):
        raise RunRequestError(
            "candidates must be an ordered sequence of AssetSweepCandidate",
            category="invalid_request",
        )
    if len(value) < 1 or len(value) > MAX_ASSET_SWEEP_CANDIDATES:
        raise RunRequestError(
            f"candidates must contain between 1 and {MAX_ASSET_SWEEP_CANDIDATES} entries",
            category="invalid_request",
        )
    items: list[AssetSweepCandidate] = []
    seen_ids: set[str] = set()
    seen_assets: list[AssetConfig] = []
    for item in value:
        if type(item) is not AssetSweepCandidate:
            raise RunRequestError(
                "candidates must contain only AssetSweepCandidate objects",
                category="invalid_request",
            )
        if item.candidate_id in seen_ids:
            raise RunRequestError("candidate IDs must be unique", category="invalid_request")
        if any(item.asset == existing for existing in seen_assets):
            raise RunRequestError(
                "candidate AssetConfig values must be unique",
                category="invalid_request",
            )
        seen_ids.add(item.candidate_id)
        seen_assets.append(item.asset)
        items.append(item)
    return tuple(items)


def _require_positive_axis(values: object, field: str) -> list[float]:
    if isinstance(values, (str, bytes, Mapping)):
        raise RunRequestError(f"{field} must be an ordered numeric sequence", category="invalid_request")
    if not isinstance(values, Sequence):
        raise RunRequestError(f"{field} must be an ordered numeric sequence", category="invalid_request")
    if len(values) == 0:
        raise RunRequestError(f"{field} must be non-empty", category="invalid_request")
    numbers: list[float] = []
    for item in values:
        if type(item) is bool or type(item) not in (int, float):
            raise RunRequestError(f"{field} values must be finite numbers", category="invalid_request")
        number = float(item)
        if not math.isfinite(number) or number <= 0.0:
            raise RunRequestError(f"{field} values must be finite and greater than zero", category="invalid_request")
        numbers.append(number)
    if len(set(numbers)) != len(numbers):
        raise RunRequestError(f"{field} contains duplicate values", category="invalid_request")
    return sorted(numbers)


def _format_quantity(value: float) -> str:
    return format(value, ".15g")


@dataclass(frozen=True)
class AssetSweepCandidate:
    """One explicitly frozen alternative asset configuration."""

    candidate_id: str
    label: str
    asset: AssetConfig

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "candidate_id", validate_candidate_id(self.candidate_id))
            object.__setattr__(self, "label", validate_candidate_label(self.label))
            if type(self.asset) is not AssetConfig:
                raise RunRequestError("asset must be an AssetConfig", category="invalid_request")
        except RunRequestError:
            raise
        except (TypeError, ValueError, AttributeError, OverflowError, ConfigError) as exc:
            raise RunRequestError(str(exc), category="invalid_request") from exc


def build_symmetric_asset_size_candidates(
    asset_template,
    *,
    powers_mw,
    storage_hours,
) -> tuple[AssetSweepCandidate, ...]:
    """Build a duration-then-power grid of symmetric machine ratings."""
    try:
        if type(asset_template) is not AssetConfig:
            raise RunRequestError("asset_template must be an AssetConfig", category="invalid_request")
        powers = _require_positive_axis(powers_mw, "powers_mw")
        hours = _require_positive_axis(storage_hours, "storage_hours")
        if len(powers) * len(hours) > MAX_ASSET_SWEEP_CANDIDATES:
            raise RunRequestError(
                f"symmetric grid exceeds {MAX_ASSET_SWEEP_CANDIDATES} candidates",
                category="invalid_request",
            )
        candidates: list[AssetSweepCandidate] = []
        index = 1
        for duration in hours:
            for power in powers:
                asset = replace(
                    asset_template,
                    power_pump_mw=power,
                    power_turbine_mw=power,
                    storage_hours=duration,
                    pond_energy_mwh=None,
                )
                candidates.append(
                    AssetSweepCandidate(
                        candidate_id=f"asset-{index:03d}",
                        label=f"{_format_quantity(power)} MW / {_format_quantity(duration)} h symmetric",
                        asset=asset,
                    )
                )
                index += 1
        return tuple(candidates)
    except RunRequestError:
        raise
    except (TypeError, ValueError, AttributeError, OverflowError, ConfigError) as exc:
        raise RunRequestError(str(exc), category="invalid_request") from exc


@dataclass(frozen=True)
class AssetSweepRow:
    """One ranked alternative asset configuration."""

    candidate_id: str
    candidate_label: str
    case_run_id: str
    market: str
    revenue_rank: int
    difference_from_highest_eur: float
    power_pump_mw: float
    power_turbine_mw: float
    storage_source: str
    storage_hours_basis: str
    configured_storage_hours: float | None
    configured_pond_energy_mwh: float | None
    e_max_mwh: float
    usable_energy_mwh: float
    grid_energy_to_fill_mwh: float
    charge_duration_h: float
    discharge_duration_h: float
    effective_grid_import_mw: float
    effective_grid_export_mw: float
    interval_count: int
    duration_hours: float
    energy_gross_eur: float
    grid_charging_cost_eur: float
    market_energy_net_eur: float
    capacity_revenue_eur: float
    pv_revenue_eur: float
    total_site_revenue_eur: float
    period_revenue_per_turbine_mw_eur: float
    period_revenue_per_usable_mwh_eur: float
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


def row_to_payload(row: AssetSweepRow) -> dict[str, object]:
    return {name: getattr(row, name) for name in ASSET_SWEEP_ROW_FIELDS}


def sweep_stages(candidate_order: Sequence[str]) -> tuple[str, ...]:
    return (
        "validate_request",
        "validate_data",
        *(f"execute_{candidate_id}" for candidate_id in candidate_order),
        "aggregate",
        "verify_artifacts",
    )


@dataclass(frozen=True)
class AssetSweepRequest:
    """Immutable description of one dedicated-market asset-parameter sweep."""

    asset_sweep_request_schema_version: int
    asset_sweep_artifact_schema_version: int
    run_id: str
    created_at_utc: datetime
    software_version: str
    data_directory: Path
    data_manifest_sha256: str
    output_directory: Path
    market: str
    candidate_order: tuple[str, ...]
    candidate_labels: Mapping[str, str]
    case_requests: Mapping[str, CaseRunRequest]
    behavioural_baseline: Mapping[str, str]

    def __post_init__(self) -> None:
        try:
            _require_schema_version(
                self.asset_sweep_request_schema_version,
                ASSET_SWEEP_REQUEST_SCHEMA_VERSION,
                "asset_sweep_request_schema_version",
            )
            _require_schema_version(
                self.asset_sweep_artifact_schema_version,
                ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION,
                "asset_sweep_artifact_schema_version",
            )
            object.__setattr__(self, "run_id", validate_run_id(self.run_id))
            object.__setattr__(
                self,
                "created_at_utc",
                require_utc_seconds(self.created_at_utc, "created_at_utc"),
            )
            if type(self.software_version) is not str or not self.software_version:
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
            if self.market not in COMPARISON_MARKETS:
                raise RunRequestError("market must be da, mfrr, or afrr", category="invalid_request")
            order = _freeze_candidate_order(self.candidate_order)
            object.__setattr__(self, "candidate_order", order)
            labels = _freeze_labels(self.candidate_labels, order)
            object.__setattr__(self, "candidate_labels", MappingProxyType(labels))
            cases = _freeze_case_requests(self.case_requests, order)
            object.__setattr__(self, "case_requests", MappingProxyType(cases))
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
            _validate_shared_children(self)
        except RunRequestError:
            raise
        except (TypeError, ValueError, KeyError, AttributeError, OverflowError, ConfigError) as exc:
            raise RunRequestError(str(exc), category="invalid_request") from exc


def _freeze_candidate_order(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise RunRequestError("candidate_order must be an ordered sequence", category="invalid_request")
    if not isinstance(value, Sequence):
        raise RunRequestError("candidate_order must be an ordered sequence", category="invalid_request")
    if len(value) < 1 or len(value) > MAX_ASSET_SWEEP_CANDIDATES:
        raise RunRequestError(
            f"candidates must contain between 1 and {MAX_ASSET_SWEEP_CANDIDATES} entries",
            category="invalid_request",
        )
    order: list[str] = []
    seen: set[str] = set()
    for item in value:
        candidate_id = validate_candidate_id(item)
        if candidate_id in seen:
            raise RunRequestError("candidate IDs must be unique", category="invalid_request")
        seen.add(candidate_id)
        order.append(candidate_id)
    return tuple(order)


def _freeze_labels(value: object, order: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RunRequestError("candidate_labels must be a mapping", category="invalid_request")
    if set(value) != set(order):
        raise RunRequestError(
            "candidate_labels must be keyed exactly by candidate_order",
            category="invalid_request",
        )
    return {candidate_id: validate_candidate_label(value[candidate_id]) for candidate_id in order}


def _freeze_case_requests(value: object, order: tuple[str, ...]) -> dict[str, CaseRunRequest]:
    if not isinstance(value, Mapping):
        raise RunRequestError("case_requests must be a mapping", category="invalid_request")
    if set(value) != set(order):
        raise RunRequestError(
            "case_requests must be keyed exactly by candidate_order",
            category="invalid_request",
        )
    cases: dict[str, CaseRunRequest] = {}
    for candidate_id in order:
        request = value[candidate_id]
        if not isinstance(request, CaseRunRequest):
            raise RunRequestError(
                f"case_requests[{candidate_id}] must be a CaseRunRequest",
                category="invalid_request",
            )
        cases[candidate_id] = request
    return cases


def _validate_shared_children(request: AssetSweepRequest) -> None:
    children = [request.case_requests[candidate_id] for candidate_id in request.candidate_order]
    first = children[0]
    seen_assets: list[AssetConfig] = []
    for candidate_id, child in zip(request.candidate_order, children, strict=True):
        if child.config.market != request.market:
            raise RunRequestError(
                f"case_requests[{candidate_id}] market must be {request.market!r}",
                category="invalid_request",
            )
        if child.run_id != child_run_id(request.run_id, candidate_id):
            raise RunRequestError(
                f"case_requests[{candidate_id}] run_id is not the deterministic child identifier",
                category="invalid_request",
            )
        if child.output_directory != _child_output_directory(request.output_directory, candidate_id):
            raise RunRequestError(
                f"case_requests[{candidate_id}] output_directory must be under the sweep cases path",
                category="invalid_request",
            )
        if child.created_at_utc != request.created_at_utc:
            raise RunRequestError(
                "child created_at_utc must match the sweep request",
                category="invalid_request",
            )
        if child.software_version != request.software_version:
            raise RunRequestError(
                "child software_version must match the sweep request",
                category="invalid_request",
            )
        if child.data_directory != request.data_directory:
            raise RunRequestError(
                "child data_directory must match the sweep request",
                category="invalid_request",
            )
        if child.data_manifest_sha256 != request.data_manifest_sha256:
            raise RunRequestError(
                "child data_manifest_sha256 must match the sweep request",
                category="invalid_request",
            )
        if child.solver_options != first.solver_options:
            raise RunRequestError(
                "child solver options must be identical across candidates",
                category="invalid_request",
            )
        if dict(child.behavioural_baseline) != dict(request.behavioural_baseline):
            raise RunRequestError(
                "child behavioural baseline must match the sweep request",
                category="invalid_request",
            )
        if child.config.period != first.config.period:
            raise RunRequestError(
                "period configuration must be identical across candidates",
                category="invalid_request",
            )
        if child.config.market_case != first.config.market_case:
            raise RunRequestError(
                "market case must be identical across candidates",
                category="invalid_request",
            )
        if child.config.site != first.config.site:
            raise RunRequestError(
                "site configuration must be identical across candidates",
                category="invalid_request",
            )
        if any(child.config.asset == existing for existing in seen_assets):
            raise RunRequestError(
                "candidate AssetConfig values must be unique",
                category="invalid_request",
            )
        seen_assets.append(child.config.asset)


def build_asset_sweep_request(
    base_config,
    candidates,
    data_directory: str | Path,
    output_directory: str | Path,
    *,
    solver_options: SolverOptions | None = None,
    run_id: str | None = None,
    created_at_utc: datetime | None = None,
) -> AssetSweepRequest:
    """Freeze one validated dedicated-market asset sweep. Does not write files."""
    try:
        if type(base_config) is not SimulationConfig:
            raise RunRequestError("base_config must be a SimulationConfig", category="invalid_request")
        frozen_candidates = _require_ordered_candidates(candidates)
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
    except (TypeError, ValueError, OSError, OverflowError, ConfigError) as exc:
        raise RunRequestError(str(exc), category="invalid_request") from exc

    try:
        bundle = open_published_bundle(data_root)
    except DataBundleError as exc:
        raise RunRequestError(str(exc), category="data_bundle") from exc
    except Exception as exc:
        raise RunRequestError(str(exc), category="data_bundle") from exc

    children: dict[str, CaseRunRequest] = {}
    labels: dict[str, str] = {}
    order: list[str] = []
    for candidate in frozen_candidates:
        try:
            config = SimulationConfig(
                period=base_config.period,
                market_case=base_config.market_case,
                asset=candidate.asset,
                site=base_config.site,
            )
        except ConfigError as exc:
            raise RunRequestError(str(exc), category="invalid_request") from exc
        order.append(candidate.candidate_id)
        labels[candidate.candidate_id] = candidate.label
        children[candidate.candidate_id] = CaseRunRequest(
            request_schema_version=CASE_RUN_REQUEST_SCHEMA_VERSION,
            run_id=child_run_id(run_id, candidate.candidate_id),
            created_at_utc=created_at_utc,
            software_version=__version__,
            artifact_schema_version=RUN_ARTIFACT_SCHEMA_VERSION,
            data_directory=data_root,
            data_manifest_sha256=bundle.manifest_sha256,
            output_directory=output_root / "cases" / candidate.candidate_id,
            config=config,
            solver_options=solver_options,
            behavioural_baseline=dict(BEHAVIOURAL_BASELINE),
        )
    return AssetSweepRequest(
        asset_sweep_request_schema_version=ASSET_SWEEP_REQUEST_SCHEMA_VERSION,
        asset_sweep_artifact_schema_version=ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION,
        run_id=run_id,
        created_at_utc=created_at_utc,
        software_version=__version__,
        data_directory=data_root,
        data_manifest_sha256=bundle.manifest_sha256,
        output_directory=output_root,
        market=base_config.market,
        candidate_order=tuple(order),
        candidate_labels=labels,
        case_requests=children,
        behavioural_baseline=dict(BEHAVIOURAL_BASELINE),
    )


def serialize_asset_sweep_request(request: AssetSweepRequest) -> dict[str, object]:
    """Return the explicit JSON object for one sweep request."""
    if not isinstance(request, AssetSweepRequest):
        raise RunRequestError(
            "request must be an AssetSweepRequest",
            category="invalid_request",
        )
    from stepinbel.workflows.serialize import format_utc

    return {
        "asset_sweep_request_schema_version": request.asset_sweep_request_schema_version,
        "asset_sweep_artifact_schema_version": request.asset_sweep_artifact_schema_version,
        "run_id": request.run_id,
        "created_at_utc": format_utc(request.created_at_utc),
        "software_version": request.software_version,
        "data_directory": str(request.data_directory),
        "data_manifest_sha256": request.data_manifest_sha256,
        "output_directory": str(request.output_directory),
        "market": request.market,
        "candidate_order": list(request.candidate_order),
        "candidate_labels": {candidate_id: request.candidate_labels[candidate_id] for candidate_id in request.candidate_order},
        "case_requests": {
            candidate_id: request_to_payload(request.case_requests[candidate_id])
            for candidate_id in request.candidate_order
        },
        "behavioural_baseline": serialize_baseline(request.behavioural_baseline),
    }


def _require_nested_case_schema(payload: object, candidate_id: str) -> dict:
    if type(payload) is not dict:
        raise RunRequestError(
            f"case_requests[{candidate_id}] must be an object",
            category="invalid_request",
        )
    _require_schema_version(
        payload.get("request_schema_version"),
        CASE_RUN_REQUEST_SCHEMA_VERSION,
        f"case_requests[{candidate_id}].request_schema_version",
    )
    _require_schema_version(
        payload.get("artifact_schema_version"),
        RUN_ARTIFACT_SCHEMA_VERSION,
        f"case_requests[{candidate_id}].artifact_schema_version",
    )
    return payload


def asset_sweep_request_from_payload(payload: object) -> AssetSweepRequest:
    """Reconstruct and revalidate a sweep request from an explicit JSON object."""
    if type(payload) is not dict:
        raise RunRequestError("sweep request payload must be an object", category="invalid_request")
    try:
        require_keys(payload, _SWEEP_REQUEST_KEYS, "sweep request")
        order_payload = payload["candidate_order"]
        if type(order_payload) is not list:
            raise RunRequestError("candidate_order must be an array", category="invalid_request")
        order = _freeze_candidate_order(order_payload)
        labels_payload = require_mapping(payload["candidate_labels"], "candidate_labels")
        cases_payload = require_mapping(payload["case_requests"], "case_requests")
        if set(cases_payload) != set(order):
            raise RunRequestError(
                "case_requests must be keyed exactly by candidate_order",
                category="invalid_request",
            )
        children = {}
        for candidate_id in order:
            nested = _require_nested_case_schema(cases_payload[candidate_id], candidate_id)
            children[candidate_id] = case_run_request_from_payload(nested)
        from stepinbel.workflows.serialize import parse_utc

        market = payload["market"]
        if type(market) is not str:
            raise RunRequestError("market must be a string", category="invalid_request")
        return AssetSweepRequest(
            asset_sweep_request_schema_version=_require_schema_version(
                payload["asset_sweep_request_schema_version"],
                ASSET_SWEEP_REQUEST_SCHEMA_VERSION,
                "asset_sweep_request_schema_version",
            ),
            asset_sweep_artifact_schema_version=_require_schema_version(
                payload["asset_sweep_artifact_schema_version"],
                ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION,
                "asset_sweep_artifact_schema_version",
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
            market=market,
            candidate_order=order,
            candidate_labels=labels_payload,
            case_requests=children,
            behavioural_baseline=payload["behavioural_baseline"],
        )
    except RunRequestError:
        raise
    except Exception as exc:
        raise _wrap_public_error(exc) from exc


def load_asset_sweep_request(path: str | Path) -> AssetSweepRequest:
    """Load a sweep request JSON file without merging defaults."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (TypeError, ValueError, OSError) as exc:
        raise RunRequestError(str(exc), category="invalid_request") from exc
    payload = loads_json(text)
    return asset_sweep_request_from_payload(payload)


def dumps_asset_sweep_request(request: AssetSweepRequest) -> str:
    return dumps_json(serialize_asset_sweep_request(request))
