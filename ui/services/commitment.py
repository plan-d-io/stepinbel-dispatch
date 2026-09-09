"""Optional machine-commitment form values, conversions, and copy."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from stepinbel.config import ConfigError, MachineCommitmentConfig

from ui.services.form import coalesce_float, is_missing_number
from ui.services.paths import MARKET_LABELS

DEFAULT_TURBINE_MINIMUM_PCT = 18.0
DEFAULT_MIP_GAP_PCT = 1.5
DEFAULT_MIP_TIME_MINUTES = 15.0
TURBINE_MINIMUM_PCT_MIN = 0.01
TURBINE_MINIMUM_PCT_MAX = 100.0
MIP_GAP_PCT_MIN = 0.01
MIP_GAP_PCT_MAX = 100.0
MIP_TIME_MINUTES_MIN = 1.0
MIP_TIME_MINUTES_MAX = 180.0

TERMINATION_ACCEPTED_WITHIN_GAP = "accepted_within_requested_mip_gap"
TERMINATION_TIME_LIMIT_FEASIBLE = "time_limit_feasible_outside_requested_gap"
USABLE_TERMINATIONS = frozenset(
    {TERMINATION_ACCEPTED_WITHIN_GAP, TERMINATION_TIME_LIMIT_FEASIBLE}
)

FIXED_SPEED_LABEL = "Fixed-speed pump"
FIXED_SPEED_HELP = "The pump is either off or operating at rated power."
TURBINE_MINIMUM_LABEL = "Minimum turbine output while operating"
TURBINE_MINIMUM_INPUT_LABEL = "Minimum output (% of rated turbine power)"
FORBID_SIMULTANEOUS_LABEL = "Prevent simultaneous pumping and generation"
COMMITMENT_EXPANDER = "Machine operating constraints"
COMMITMENT_TIME_NOTICE = (
    "These optional constraints can significantly increase calculation time, "
    "especially for long simulation periods."
)
COMMITMENT_REVIEW_WARNING_TITLE = "Longer calculation time"
WORKING_COMMITMENT_NOTICE = (
    "Detailed machine operating constraints are enabled. This run may take longer."
)
SOLVER_CONTROLS_HEADING = "Detailed optimization controls"
MIP_GAP_LABEL = "Target optimality gap (%)"
MIP_TIME_LABEL = "Maximum solve time per market (minutes)"
SOLVER_CONTROLS_HELP = (
    "The solver stops when it reaches the target gap or the time limit. "
    "A smaller gap can take much longer."
)
BEST_AVAILABLE_TITLE = "Best available solution"
COMPARISON_BEST_AVAILABLE_CAUTION = (
    "Small revenue differences should be interpreted carefully."
)
COMPARISON_BEST_AVAILABLE_SCHEDULES = (
    "The Results use the best available validated schedules."
)

ERROR_TURBINE_PCT = (
    "Enter a finite minimum turbine output greater than 0% and at most 100%."
)
ERROR_MIP_GAP = (
    "Enter a finite target optimality gap greater than 0% and at most 100%."
)
ERROR_MIP_TIME = (
    "Enter a finite maximum solve time of 1 to 180 minutes per market."
)


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def format_compact_number(value: object) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("number")
    if number == 0.0:
        return "0"
    return f"{number:.12g}"


def format_percent_points(value: object) -> str:
    return f"{format_compact_number(value)}%"


def format_gap_percent(fraction: object) -> str:
    return format_percent_points(float(fraction) * 100.0)


def minutes_from_seconds(seconds: object) -> float:
    return float(seconds) / 60.0


def format_minutes_phrase(seconds: object) -> str:
    minutes = minutes_from_seconds(seconds)
    shown = format_compact_number(minutes)
    if shown == "1":
        return "1 minute"
    return f"{shown} minutes"


def format_hyphenated_minutes(seconds: object) -> str:
    return f"{format_compact_number(minutes_from_seconds(seconds))}-minute"


def commitment_form_defaults() -> dict[str, Any]:
    return {
        "fixed_speed_pump": False,
        "turbine_minimum_enabled": False,
        "turbine_minimum_output_pct": DEFAULT_TURBINE_MINIMUM_PCT,
        "forbid_simultaneous_operation": False,
        "mip_gap_pct": DEFAULT_MIP_GAP_PCT,
        "mip_time_limit_min": DEFAULT_MIP_TIME_MINUTES,
    }


def machine_commitment_enabled(form: Mapping[str, Any]) -> bool:
    return (
        form.get("fixed_speed_pump") is True
        or form.get("turbine_minimum_enabled") is True
        or form.get("forbid_simultaneous_operation") is True
    )


def snapshot_machine_commitment_enabled(snapshot: Mapping[str, Any]) -> bool:
    form = snapshot.get("form")
    if isinstance(form, Mapping) and machine_commitment_enabled(form):
        return True
    commitment = snapshot.get("machine_commitment")
    if not isinstance(commitment, Mapping):
        return False
    return (
        commitment.get("fixed_speed_pump") is True
        or commitment.get("turbine_minimum_enabled") is True
        or commitment.get("forbid_simultaneous_operation") is True
    )


def turbine_minimum_fraction_from_form(form: Mapping[str, Any]) -> float:
    if not form.get("turbine_minimum_enabled"):
        return 0.0
    return coalesce_float(form.get("turbine_minimum_output_pct"), DEFAULT_TURBINE_MINIMUM_PCT) / 100.0


def mip_rel_gap_from_form(form: Mapping[str, Any]) -> float:
    return coalesce_float(form.get("mip_gap_pct"), DEFAULT_MIP_GAP_PCT) / 100.0


def mip_time_limit_s_from_form(form: Mapping[str, Any]) -> float:
    return coalesce_float(form.get("mip_time_limit_min"), DEFAULT_MIP_TIME_MINUTES) * 60.0


def _in_range(value: object, minimum: float, maximum: float) -> bool:
    if not _finite_number(value):
        return False
    number = float(value)
    return minimum <= number <= maximum


def commitment_continue_reason(form: Mapping[str, Any]) -> str | None:
    if form.get("turbine_minimum_enabled"):
        if not _in_range(
            form.get("turbine_minimum_output_pct"),
            TURBINE_MINIMUM_PCT_MIN,
            TURBINE_MINIMUM_PCT_MAX,
        ):
            return ERROR_TURBINE_PCT
    if not machine_commitment_enabled(form):
        if not is_missing_number(form.get("mip_gap_pct")) and not _finite_number(form.get("mip_gap_pct")):
            return ERROR_MIP_GAP
        if not is_missing_number(form.get("mip_time_limit_min")) and not _finite_number(
            form.get("mip_time_limit_min")
        ):
            return ERROR_MIP_TIME
        return None
    if not _in_range(form.get("mip_gap_pct"), MIP_GAP_PCT_MIN, MIP_GAP_PCT_MAX):
        return ERROR_MIP_GAP
    if not _in_range(form.get("mip_time_limit_min"), MIP_TIME_MINUTES_MIN, MIP_TIME_MINUTES_MAX):
        return ERROR_MIP_TIME
    return None


def build_machine_commitment(form: Mapping[str, Any]) -> MachineCommitmentConfig:
    reason = commitment_continue_reason(form)
    if reason:
        raise ValueError(reason)
    try:
        return MachineCommitmentConfig(
            fixed_speed_pump=form.get("fixed_speed_pump") is True,
            turbine_minimum_output_fraction=turbine_minimum_fraction_from_form(form),
            forbid_simultaneous_operation=form.get("forbid_simultaneous_operation") is True,
        )
    except ConfigError as exc:
        raise ValueError(str(exc)) from exc


def commitment_snapshot_payload(form: Mapping[str, Any]) -> dict[str, Any]:
    commitment = build_machine_commitment(form)
    return {
        "fixed_speed_pump": commitment.fixed_speed_pump is True,
        "turbine_minimum_enabled": form.get("turbine_minimum_enabled") is True,
        "turbine_minimum_output_fraction": float(commitment.turbine_minimum_output_fraction),
        "forbid_simultaneous_operation": commitment.forbid_simultaneous_operation is True,
    }


def solver_controls_snapshot_payload(form: Mapping[str, Any]) -> dict[str, Any]:
    reason = commitment_continue_reason(form)
    if reason and machine_commitment_enabled(form):
        raise ValueError(reason)
    gap = form.get("mip_gap_pct")
    minutes = form.get("mip_time_limit_min")
    if is_missing_number(gap):
        gap = DEFAULT_MIP_GAP_PCT
    if is_missing_number(minutes):
        minutes = DEFAULT_MIP_TIME_MINUTES
    if not _finite_number(gap) or not _finite_number(minutes):
        raise ValueError(ERROR_MIP_GAP if not _finite_number(gap) else ERROR_MIP_TIME)
    return {
        "mip_rel_gap": float(gap) / 100.0,
        "time_limit_s": float(minutes) * 60.0,
    }


def snapshot_commitment_is_valid(payload: object, form: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    required = (
        "fixed_speed_pump",
        "turbine_minimum_enabled",
        "turbine_minimum_output_fraction",
        "forbid_simultaneous_operation",
    )
    if any(key not in payload for key in required):
        return False
    for key in ("fixed_speed_pump", "turbine_minimum_enabled", "forbid_simultaneous_operation"):
        if not isinstance(payload.get(key), bool):
            return False
    fraction = payload.get("turbine_minimum_output_fraction")
    if not _finite_number(fraction):
        return False
    number = float(fraction)
    if number < 0.0 or number > 1.0:
        return False
    enabled = bool(payload["turbine_minimum_enabled"])
    if enabled:
        if number <= 0.0:
            return False
    elif number != 0.0:
        return False
    if form is None:
        return True
    for key in ("fixed_speed_pump", "turbine_minimum_enabled", "forbid_simultaneous_operation"):
        if form.get(key) is not True and form.get(key) is not False:
            return False
    if form.get("fixed_speed_pump") is not payload["fixed_speed_pump"]:
        return False
    if form.get("turbine_minimum_enabled") is not enabled:
        return False
    if form.get("forbid_simultaneous_operation") is not payload["forbid_simultaneous_operation"]:
        return False
    expected = 0.0 if not enabled else coalesce_float(form.get("turbine_minimum_output_pct"), 0.0) / 100.0
    return math.isclose(number, expected, rel_tol=0.0, abs_tol=0.0)


def snapshot_solver_controls_are_valid(
    payload: object,
    form: Mapping[str, Any] | None = None,
    *,
    required: bool,
) -> bool:
    if payload is None:
        return not required
    if not isinstance(payload, Mapping):
        return False
    if any(key not in payload for key in ("mip_rel_gap", "time_limit_s")):
        return False
    gap = payload.get("mip_rel_gap")
    limit = payload.get("time_limit_s")
    if not _finite_number(gap) or not _finite_number(limit):
        return False
    gap_n = float(gap)
    limit_n = float(limit)
    if gap_n < MIP_GAP_PCT_MIN / 100.0 or gap_n > MIP_GAP_PCT_MAX / 100.0:
        return False
    if limit_n < MIP_TIME_MINUTES_MIN * 60.0 or limit_n > MIP_TIME_MINUTES_MAX * 60.0:
        return False
    if form is None:
        return True
    expected_gap = coalesce_float(form.get("mip_gap_pct"), DEFAULT_MIP_GAP_PCT) / 100.0
    expected_limit = coalesce_float(form.get("mip_time_limit_min"), DEFAULT_MIP_TIME_MINUTES) * 60.0
    return math.isclose(gap_n, expected_gap, rel_tol=0.0, abs_tol=0.0) and math.isclose(
        limit_n, expected_limit, rel_tol=0.0, abs_tol=0.0
    )


def solver_time_envelope(seconds: object, market_count: int) -> str:
    per = format_minutes_phrase(seconds)
    if market_count <= 1:
        return f"{per} per market"
    total_seconds = float(seconds) * int(market_count)
    return (
        f"{per} per market · up to {format_minutes_phrase(total_seconds)} "
        f"across {market_count} market runs"
    )


def review_commitment_rows(
    snapshot: Mapping[str, Any],
    *,
    market_count: int,
) -> list[tuple[str, str]] | None:
    commitment = snapshot.get("machine_commitment")
    if not isinstance(commitment, Mapping):
        return None
    if not (
        commitment.get("fixed_speed_pump")
        or commitment.get("turbine_minimum_enabled")
        or commitment.get("forbid_simultaneous_operation")
    ):
        return None
    rows: list[tuple[str, str]] = []
    if commitment.get("fixed_speed_pump"):
        rows.append((FIXED_SPEED_LABEL, "Enabled"))
    if commitment.get("turbine_minimum_enabled"):
        fraction = commitment.get("turbine_minimum_output_fraction")
        rows.append((TURBINE_MINIMUM_LABEL, format_gap_percent(fraction)))
    if commitment.get("forbid_simultaneous_operation"):
        rows.append((FORBID_SIMULTANEOUS_LABEL, "Enabled"))
    controls = snapshot.get("solver_controls") if isinstance(snapshot.get("solver_controls"), Mapping) else {}
    gap = controls.get("mip_rel_gap")
    limit = controls.get("time_limit_s")
    if _finite_number(gap):
        rows.append(("Target optimality gap", format_gap_percent(gap)))
    if _finite_number(limit):
        rows.append(("Maximum solve time per market", format_minutes_phrase(limit)))
        if market_count > 1:
            rows.append(("Maximum solver-time envelope", solver_time_envelope(limit, market_count)))
    return rows


def enabled_constraint_labels(commitment: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    if commitment.get("fixed_speed_pump") is True:
        labels.append(FIXED_SPEED_LABEL)
    fraction = commitment.get("turbine_minimum_output_fraction")
    if _finite_number(fraction) and float(fraction) > 0.0:
        labels.append(f"{TURBINE_MINIMUM_LABEL}: {format_gap_percent(fraction)}")
    if commitment.get("forbid_simultaneous_operation") is True:
        labels.append(FORBID_SIMULTANEOUS_LABEL)
    return labels


def termination_label(value: object) -> str:
    if value == TERMINATION_ACCEPTED_WITHIN_GAP:
        return "Accepted within the requested optimality gap"
    if value == TERMINATION_TIME_LIMIT_FEASIBLE:
        return "Time limit with a feasible solution outside the requested gap"
    if value == "lp_optimum":
        return "Continuous LP optimum"
    if not isinstance(value, str) or not value:
        raise ValueError("termination")
    return value.replace("_", " ")


def one_market_best_available_body(
    *,
    time_limit_s: object,
    requested_gap: object,
    achieved_gap: object,
) -> str:
    return (
        f"The solver reached the {format_hyphenated_minutes(time_limit_s)} time limit "
        f"before meeting the {format_gap_percent(requested_gap)} target. "
        f"The best available result has a {format_gap_percent(achieved_gap)} optimality gap."
    )


def comparison_best_available_body(
    affected: Sequence[tuple[str, object]],
) -> str:
    parts: list[str] = []
    for market, gap in affected:
        label = MARKET_LABELS.get(market, market)
        parts.append(f"{label} has a {format_gap_percent(gap)} optimality gap.")
    parts.append(COMPARISON_BEST_AVAILABLE_SCHEDULES)
    parts.append(COMPARISON_BEST_AVAILABLE_CAUTION)
    return " ".join(parts)
