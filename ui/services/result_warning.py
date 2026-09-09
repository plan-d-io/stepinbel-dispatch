"""Best-available MILP warning from validated schema-v2 artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ui.services.artifacts import bind_exact_result_artifacts
from ui.services.commitment import (
    BEST_AVAILABLE_TITLE,
    TERMINATION_TIME_LIMIT_FEASIBLE,
    USABLE_TERMINATIONS,
    comparison_best_available_body,
    one_market_best_available_body,
)
from ui.services.paths import KIND_CASE, KIND_COMPARISON
from ui.services.result_format import display_market_keys, is_finite_number

JSON_MAX_BYTES = 1_048_576


def _read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > JSON_MAX_BYTES:
        raise ValueError("metadata")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("metadata")
    return payload


def _require_usable_termination(value: object) -> str:
    if value not in USABLE_TERMINATIONS:
        raise ValueError("termination")
    return str(value)


def load_best_available_warning(
    result: Mapping[str, Any] | None,
    *,
    job: Mapping[str, Any] | None = None,
    outputs_root: Path | None = None,
) -> dict[str, str] | None:
    bound = bind_exact_result_artifacts(result, job=job, outputs_root=outputs_root)
    validated = bound.result
    kind = str(validated["kind"])
    if kind == KIND_CASE:
        metadata = _read_json(bound.directory / "run_metadata.json")
        solver = metadata.get("solver")
        if not isinstance(solver, Mapping):
            raise ValueError("solver")
        formulation = solver.get("formulation")
        if formulation in {None, "lp"}:
            if solver.get("termination") == TERMINATION_TIME_LIMIT_FEASIBLE:
                raise ValueError("termination")
            return None
        termination = _require_usable_termination(solver.get("termination"))
        if termination != TERMINATION_TIME_LIMIT_FEASIBLE:
            return None
        for key in ("time_limit_s", "requested_mip_gap", "achieved_mip_gap"):
            if not is_finite_number(solver.get(key)):
                raise ValueError(key)
        return {
            "title": BEST_AVAILABLE_TITLE,
            "body": one_market_best_available_body(
                time_limit_s=solver["time_limit_s"],
                requested_gap=solver["requested_mip_gap"],
                achieved_gap=solver["achieved_mip_gap"],
            ),
        }
    if kind != KIND_COMPARISON:
        raise ValueError("kind")
    metadata = _read_json(bound.directory / "comparison_metadata.json")
    children = metadata.get("children")
    if not isinstance(children, Mapping):
        raise ValueError("children")
    affected: list[tuple[str, object]] = []
    for market in display_market_keys(validated["markets"]):
        child = children.get(market)
        if not isinstance(child, Mapping):
            raise ValueError("child")
        termination = child.get("termination")
        if termination is None:
            continue
        _require_usable_termination(termination)
        if termination != TERMINATION_TIME_LIMIT_FEASIBLE:
            continue
        gap = child.get("achieved_mip_gap")
        if not is_finite_number(gap):
            raise ValueError("gap")
        affected.append((market, gap))
    warning = metadata.get("mip_termination_warning")
    if affected:
        if warning in {None, ""}:
            raise ValueError("warning")
        return {
            "title": BEST_AVAILABLE_TITLE,
            "body": comparison_best_available_body(affected),
        }
    if warning not in {None, ""}:
        raise ValueError("warning")
    return None
