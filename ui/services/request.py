"""Build and serialize public workflow requests from a configured snapshot."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from stepinbel.optimizer import SolverOptions
from stepinbel.workflows import (
    CaseRunRequest,
    MarketComparisonRequest,
    RunRequestError,
    build_case_run_request,
    build_market_comparison_request,
    serialize_case_run_request,
    serialize_market_comparison_request,
)

from ui.services.commitment import (
    machine_commitment_enabled,
    mip_rel_gap_from_form,
    mip_time_limit_s_from_form,
)
from ui.services.configs import build_simulation_configs
from ui.services.errors import user_facing_error
from ui.services.paths import DATA_DIRECTORY, KIND_CASE, KIND_COMPARISON
from ui.services.snapshot import (
    LIVE_IDENTITY,
    build_snapshot,
    snapshot_block_reason,
    snapshot_digest,
)

ERROR_SNAPSHOT = "Configured inputs are not ready to run."
ERROR_PARITY = "Configured settings changed. Return to Configure and continue again."
ERROR_DEMO_REQUEST = "Demo mode does not build a live request."


def _form_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    form = snapshot.get("form")
    if not isinstance(form, Mapping):
        raise ValueError(ERROR_SNAPSHOT)
    return dict(form)


def revalidate_snapshot(
    snapshot: Mapping[str, Any] | None,
    form: Mapping[str, Any] | None,
    *,
    demo: bool,
) -> dict[str, Any]:
    if demo:
        raise ValueError(ERROR_DEMO_REQUEST)
    reason = snapshot_block_reason(snapshot, form)
    if reason is not None or snapshot is None:
        raise ValueError(reason or ERROR_SNAPSHOT)
    if snapshot.get("identity") != LIVE_IDENTITY or snapshot.get("demo") is not False:
        raise ValueError(ERROR_SNAPSHOT)
    rebuilt = build_snapshot(_form_from_snapshot(snapshot), demo=False)
    stored = dict(snapshot)
    if rebuilt != stored:
        raise ValueError(ERROR_PARITY)
    if snapshot_digest(rebuilt) != snapshot_digest(stored):
        raise ValueError(ERROR_PARITY)
    return stored


def request_kind(markets: list[str]) -> str:
    if len(markets) == 1:
        return KIND_CASE
    if len(markets) in {2, 3}:
        return KIND_COMPARISON
    raise ValueError("Select at least one market.")


def build_public_request(
    snapshot: Mapping[str, Any],
    *,
    output_directory: Path,
    run_id: str,
    data_directory: Path | None = None,
    created_at_utc: datetime | None = None,
) -> CaseRunRequest | MarketComparisonRequest:
    form = _form_from_snapshot(snapshot)
    configs = build_simulation_configs(form)
    mapping = {market: config for market, config in configs}
    markets = list(snapshot.get("markets") or [])
    if list(mapping) != markets:
        raise ValueError(ERROR_PARITY)
    detailed = bool(snapshot.get("detailed_solver_output"))
    if machine_commitment_enabled(form):
        options = SolverOptions(
            detailed_output=detailed,
            mip_rel_gap=mip_rel_gap_from_form(form),
            time_limit_s=mip_time_limit_s_from_form(form),
        )
    else:
        options = SolverOptions(detailed_output=detailed)
    data_root = Path(data_directory) if data_directory is not None else DATA_DIRECTORY
    kind = request_kind(markets)
    try:
        if kind == KIND_CASE:
            return build_case_run_request(
                configs[0][1],
                data_root,
                output_directory,
                solver_options=options,
                run_id=run_id,
                created_at_utc=created_at_utc,
            )
        return build_market_comparison_request(
            mapping,
            data_root,
            output_directory,
            solver_options=options,
            run_id=run_id,
            created_at_utc=created_at_utc,
        )
    except RunRequestError as exc:
        raise ValueError(user_facing_error(exc)) from exc


def serialize_public_request(request: CaseRunRequest | MarketComparisonRequest) -> dict[str, Any]:
    if isinstance(request, CaseRunRequest):
        payload = serialize_case_run_request(request)
    elif isinstance(request, MarketComparisonRequest):
        payload = serialize_market_comparison_request(request)
    else:
        raise TypeError("unsupported request type")
    if not isinstance(payload, dict):
        raise TypeError("serialized request must be a JSON object")
    return payload


def prepare_launch_request(
    snapshot: Mapping[str, Any] | None,
    form: Mapping[str, Any] | None,
    *,
    demo: bool,
    output_directory: Path,
    run_id: str,
    data_directory: Path | None = None,
    created_at_utc: datetime | None = None,
) -> tuple[str, dict[str, Any], CaseRunRequest | MarketComparisonRequest]:
    valid = revalidate_snapshot(snapshot, form, demo=demo)
    request = build_public_request(
        valid,
        output_directory=output_directory,
        run_id=run_id,
        data_directory=data_directory,
        created_at_utc=created_at_utc,
    )
    payload = serialize_public_request(request)
    return request_kind(list(valid["markets"])), payload, request
