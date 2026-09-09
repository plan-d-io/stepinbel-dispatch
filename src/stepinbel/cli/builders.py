"""Translate parsed CLI arguments into existing public configuration objects."""

from __future__ import annotations

import argparse
import math
import re
from datetime import date, datetime, timezone
from typing import Any

from stepinbel.config import (
    AFRRCase,
    AssetConfig,
    BelgianDeliveryPeriod,
    DayAheadCase,
    FixedMinimumCapacityBid,
    HistoricalQuantileCapacityBid,
    MachineCommitmentConfig,
    MFRRCase,
    Period,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.optimizer import ModelError, SolverOptions
from stepinbel.workflows import (
    build_asset_sweep_request,
    build_case_run_request,
    build_market_comparison_request,
    build_symmetric_asset_size_candidates,
)

from stepinbel.cli.errors import CliError
from stepinbel.cli.parser import CONSTRUCTION_DESTS, SWEEP_FORBIDDEN_DESTS

_UTC_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BOOLEAN_CELLS = frozenset({"true", "false"})
_NON_FINITE_CELLS = frozenset({"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"})
_DA_FORBIDDEN = (
    "activation_profile",
    "capacity_bid_mode",
    "capacity_quantile",
    "fixed_up_capacity_price_eur_mw_h",
    "fixed_down_capacity_price_eur_mw_h",
    "capacity_coverage_hours",
    "afrr_up_capacity_fraction",
)


def has_option(namespace: argparse.Namespace, name: str) -> bool:
    return hasattr(namespace, name)


def option_value(namespace: argparse.Namespace, name: str) -> Any:
    return getattr(namespace, name)


def supplied_construction_options(namespace: argparse.Namespace) -> tuple[str, ...]:
    names = [name for name in sorted(CONSTRUCTION_DESTS) if has_option(namespace, name)]
    return tuple(names)


def reject_request_overrides(namespace: argparse.Namespace) -> None:
    present = supplied_construction_options(namespace)
    if present:
        flags = ", ".join(f"--{name.replace('_', '-')}" for name in present)
        raise CliError(f"frozen requests do not accept construction overrides: {flags}")


def parse_iso_date(value: object, field: str) -> date:
    if type(value) is not str or not _DATE_RE.fullmatch(value):
        raise CliError(f"{field} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CliError(f"{field} is not a valid calendar date") from exc


def parse_utc_z(value: object, field: str) -> datetime:
    if type(value) is not str or not _UTC_Z_RE.fullmatch(value):
        raise CliError(f"{field} must be YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise CliError(f"{field} is not a valid UTC timestamp") from exc


def build_period(namespace: argparse.Namespace) -> Period:
    has_delivery_start = has_option(namespace, "delivery_start")
    has_delivery_end = has_option(namespace, "delivery_end")
    has_utc_start = has_option(namespace, "utc_start")
    has_utc_end = has_option(namespace, "utc_end")
    has_delivery = has_delivery_start or has_delivery_end
    has_utc = has_utc_start or has_utc_end
    if has_delivery and has_utc:
        raise CliError("choose either delivery dates or UTC bounds, not both")
    if not has_delivery and not has_utc:
        raise CliError("a delivery-date pair or a UTC bound pair is required")
    if has_delivery:
        if not has_delivery_start or not has_delivery_end:
            raise CliError("both --delivery-start and --delivery-end are required")
        return BelgianDeliveryPeriod(
            parse_iso_date(option_value(namespace, "delivery_start"), "--delivery-start"),
            parse_iso_date(option_value(namespace, "delivery_end"), "--delivery-end"),
        )
    if not has_utc_start or not has_utc_end:
        raise CliError("both --utc-start and --utc-end are required")
    return UtcPeriod(
        parse_utc_z(option_value(namespace, "utc_start"), "--utc-start"),
        parse_utc_z(option_value(namespace, "utc_end"), "--utc-end"),
    )


def _asset_kwargs(namespace: argparse.Namespace, *, template: bool) -> dict[str, Any]:
    if template:
        forbidden = [name for name in SWEEP_FORBIDDEN_DESTS if has_option(namespace, name)]
        if forbidden:
            flags = ", ".join(f"--{name.replace('_', '-')}" for name in forbidden)
            raise CliError(f"direct symmetric sweep does not accept {flags}")
    mapping = (
        ("pump_mw", "power_pump_mw"),
        ("turbine_mw", "power_turbine_mw"),
        ("pump_efficiency", "eta_pump"),
        ("turbine_efficiency", "eta_turbine"),
        ("storage_hours_basis", "storage_hours_basis"),
        ("initial_soc", "soc_initial_frac"),
        ("terminal_soc", "soc_terminal_frac"),
        ("enforce_terminal_soc", "enforce_terminal_soc"),
        ("pump_ramp_up_min", "pump_ramp_up_min"),
        ("pump_ramp_down_min", "pump_ramp_down_min"),
        ("pump_ramp_power_fraction", "pump_ramp_power_frac"),
        ("turbine_ramp_up_min", "turbine_ramp_up_min"),
        ("turbine_ramp_down_min", "turbine_ramp_down_min"),
        ("turbine_ramp_power_fraction", "turbine_ramp_power_frac"),
    )
    kwargs: dict[str, Any] = {}
    for dest, field in mapping:
        if has_option(namespace, dest):
            kwargs[field] = option_value(namespace, dest)
    has_hours = has_option(namespace, "storage_hours")
    has_pond = has_option(namespace, "pond_energy_mwh")
    if has_hours and has_pond:
        raise CliError("supply only one of --storage-hours and --pond-energy-mwh")
    if has_hours:
        kwargs["storage_hours"] = option_value(namespace, "storage_hours")
        kwargs["pond_energy_mwh"] = None
    elif has_pond:
        kwargs["pond_energy_mwh"] = option_value(namespace, "pond_energy_mwh")
        kwargs["storage_hours"] = None
    return kwargs


def build_asset(namespace: argparse.Namespace, *, template: bool = False) -> AssetConfig:
    return AssetConfig(**_asset_kwargs(namespace, template=template))


def build_site(namespace: argparse.Namespace) -> SiteConfig:
    kwargs: dict[str, Any] = {}
    mapping = (
        ("grid_import_mw", "grid_import_mw"),
        ("grid_export_mw", "grid_export_mw"),
        ("pv_ac_kw", "pv_ac_kw"),
        ("pv_region", "pv_region"),
        ("pv_revenue_mode", "pv_revenue_mode"),
        ("pv_fixed_price_eur_mwh", "pv_fixed_price_eur_mwh"),
    )
    for dest, field in mapping:
        if has_option(namespace, dest):
            kwargs[field] = option_value(namespace, dest)
    mode = kwargs.get("pv_revenue_mode")
    has_price = "pv_fixed_price_eur_mwh" in kwargs
    if has_price and mode != "fixed":
        raise CliError("--pv-fixed-price-eur-mwh requires --pv-revenue-mode fixed")
    if mode == "fixed" and not has_price:
        raise CliError("fixed PV revenue mode requires --pv-fixed-price-eur-mwh")
    return SiteConfig(**kwargs)


def build_solver_options(namespace: argparse.Namespace) -> SolverOptions:
    kwargs: dict[str, Any] = {}
    if has_option(namespace, "detailed_solver_output"):
        kwargs["detailed_output"] = bool(option_value(namespace, "detailed_solver_output"))
    if has_option(namespace, "mip_rel_gap"):
        kwargs["mip_rel_gap"] = option_value(namespace, "mip_rel_gap")
    if has_option(namespace, "mip_time_limit_s"):
        kwargs["time_limit_s"] = option_value(namespace, "mip_time_limit_s")
    try:
        return SolverOptions(**kwargs)
    except ModelError as exc:
        raise CliError(str(exc)) from exc


def build_machine_commitment(namespace: argparse.Namespace) -> MachineCommitmentConfig:
    kwargs: dict[str, Any] = {}
    if has_option(namespace, "fixed_speed_pump"):
        kwargs["fixed_speed_pump"] = bool(option_value(namespace, "fixed_speed_pump"))
    if has_option(namespace, "turbine_minimum_output_fraction"):
        kwargs["turbine_minimum_output_fraction"] = option_value(
            namespace, "turbine_minimum_output_fraction"
        )
    if has_option(namespace, "forbid_simultaneous_operation"):
        kwargs["forbid_simultaneous_operation"] = bool(
            option_value(namespace, "forbid_simultaneous_operation")
        )
    return MachineCommitmentConfig(**kwargs)


def _prefixed(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def build_market_case(namespace: argparse.Namespace, market: str, *, prefix: str = ""):
    if market == "da":
        for name in _DA_FORBIDDEN:
            dest = _prefixed(prefix, name)
            if has_option(namespace, dest):
                raise CliError(f"DA does not accept --{dest.replace('_', '-')}")
        return DayAheadCase()
    if market not in {"mfrr", "afrr"}:
        raise CliError("--market must be one of da, mfrr, afrr")

    profile_dest = _prefixed(prefix, "activation_profile")
    mode_dest = _prefixed(prefix, "capacity_bid_mode")
    quantile_dest = _prefixed(prefix, "capacity_quantile")
    up_dest = _prefixed(prefix, "fixed_up_capacity_price_eur_mw_h")
    down_dest = _prefixed(prefix, "fixed_down_capacity_price_eur_mw_h")
    coverage_dest = _prefixed(prefix, "capacity_coverage_hours")
    fraction_dest = _prefixed(prefix, "afrr_up_capacity_fraction")

    # fraction_dest resolution:
    # - run subparser with no prefix: dest is "afrr_up_capacity_fraction"
    # - compare subparser with prefix "afrr_": _prefixed gives "afrr_afrr_up_capacity_fraction"
    #   which doesn't exist; the compare parser uses dest "afrr_up_capacity_fraction" directly.
    # Only reject mFRR if the fraction option was actually added by the run parser
    # (i.e., fraction_dest itself is present, not the compare-afrr spillover).
    if market == "mfrr" and has_option(namespace, fraction_dest):
        # fraction_dest is "afrr_up_capacity_fraction" (run) or the double-prefixed name (compare).
        # For compare with prefix "mfrr_", fraction_dest is "mfrr_afrr_up_capacity_fraction" which
        # won't be present, so this is safe.
        raise CliError("mFRR does not accept --" + fraction_dest.replace("_", "-"))

    # For compare aFRR builds (prefix="afrr_"), _prefixed gives "afrr_afrr_up_capacity_fraction"
    # which doesn't exist; fall back to the plain name that compare parser actually registers.
    if not has_option(namespace, fraction_dest):
        fraction_dest = "afrr_up_capacity_fraction"

    profile = option_value(namespace, profile_dest) if has_option(namespace, profile_dest) else "balanced"
    bid_mode = option_value(namespace, mode_dest) if has_option(namespace, mode_dest) else "historical"
    coverage = option_value(namespace, coverage_dest) if has_option(namespace, coverage_dest) else 4.0

    if bid_mode == "historical":
        if has_option(namespace, up_dest) or has_option(namespace, down_dest):
            raise CliError("historical capacity bidding does not accept fixed-price flags")
        quantile = option_value(namespace, quantile_dest) if has_option(namespace, quantile_dest) else 0.50
        bid = HistoricalQuantileCapacityBid(quantile)
    elif bid_mode == "fixed":
        if has_option(namespace, quantile_dest):
            raise CliError("fixed capacity bidding does not accept a historical quantile")
        if not has_option(namespace, up_dest):
            raise CliError("fixed capacity bidding requires an upward price")
        upward = option_value(namespace, up_dest)
        if market == "mfrr":
            if has_option(namespace, down_dest):
                raise CliError("fixed mFRR does not accept a downward capacity price")
            bid = FixedMinimumCapacityBid(upward)
        else:
            if not has_option(namespace, down_dest):
                raise CliError("fixed aFRR requires both upward and downward capacity prices")
            bid = FixedMinimumCapacityBid(upward, option_value(namespace, down_dest))
    else:
        raise CliError("capacity-bid-mode must be historical or fixed")

    if market == "mfrr":
        return MFRRCase(
            activation_profile=profile,
            capacity_bid=bid,
            capacity_coverage_hours=coverage,
        )
    fraction = option_value(namespace, fraction_dest) if has_option(namespace, fraction_dest) else 1.0
    return AFRRCase(
        activation_profile=profile,
        capacity_bid=bid,
        capacity_coverage_hours=coverage,
        up_capacity_fraction=fraction,
    )


def require_direct_paths(namespace: argparse.Namespace) -> tuple[str, str]:
    if not has_option(namespace, "data_dir"):
        raise CliError("direct execution requires --data-dir")
    if not has_option(namespace, "output_dir"):
        raise CliError("direct execution requires --output-dir")
    return str(option_value(namespace, "data_dir")), str(option_value(namespace, "output_dir"))


def optional_run_id(namespace: argparse.Namespace) -> str | None:
    if not has_option(namespace, "run_id"):
        return None
    return str(option_value(namespace, "run_id"))


def build_simulation_config(
    namespace: argparse.Namespace,
    market: str,
    *,
    prefix: str = "",
    asset: AssetConfig | None = None,
) -> SimulationConfig:
    return SimulationConfig(
        period=build_period(namespace),
        market_case=build_market_case(namespace, market, prefix=prefix),
        asset=asset if asset is not None else build_asset(namespace),
        site=build_site(namespace),
        machine_commitment=build_machine_commitment(namespace),
    )


def parse_numeric_axis(value: object, field: str) -> list[float]:
    if type(value) is not str or not value:
        raise CliError(f"{field} must be a comma-separated numeric sequence")
    cells = value.split(",")
    numbers: list[float] = []
    seen: set[float] = set()
    for raw in cells:
        cell = raw.strip()
        if not cell:
            raise CliError(f"{field} contains an empty cell")
        folded = cell.casefold()
        if folded in _BOOLEAN_CELLS:
            raise CliError(f"{field} values must not be booleans")
        if folded in _NON_FINITE_CELLS:
            raise CliError(f"{field} values must be finite numbers")
        try:
            number = float(cell)
        except ValueError as exc:
            raise CliError(f"{field} values must be finite numbers") from exc
        if type(number) is bool or not math.isfinite(number):
            raise CliError(f"{field} values must be finite numbers")
        if number in seen:
            raise CliError(f"{field} contains duplicate values")
        seen.add(number)
        numbers.append(number)
    if not numbers:
        raise CliError(f"{field} must be a comma-separated numeric sequence")
    return numbers


def build_case_request(namespace: argparse.Namespace):
    if not has_option(namespace, "market"):
        raise CliError("direct execution requires --market")
    market = option_value(namespace, "market")
    data_dir, output_dir = require_direct_paths(namespace)
    return build_case_run_request(
        build_simulation_config(namespace, market),
        data_dir,
        output_dir,
        solver_options=build_solver_options(namespace),
        run_id=optional_run_id(namespace),
    )


_COMPARE_MFRR_DESTS: tuple[str, ...] = (
    "mfrr_activation_profile",
    "mfrr_capacity_bid_mode",
    "mfrr_capacity_quantile",
    "mfrr_fixed_up_capacity_price_eur_mw_h",
    "mfrr_capacity_coverage_hours",
)
_COMPARE_AFRR_DESTS: tuple[str, ...] = (
    "afrr_activation_profile",
    "afrr_capacity_bid_mode",
    "afrr_capacity_quantile",
    "afrr_fixed_up_capacity_price_eur_mw_h",
    "afrr_fixed_down_capacity_price_eur_mw_h",
    "afrr_capacity_coverage_hours",
    "afrr_up_capacity_fraction",
)


def selected_compare_markets(namespace: argparse.Namespace) -> tuple[str, ...]:
    if not has_option(namespace, "markets"):
        return ("da", "mfrr", "afrr")
    values = list(option_value(namespace, "markets"))
    if len(values) != len(set(values)):
        raise CliError("--markets must not contain duplicate names")
    if len(values) == 1:
        raise CliError("a comparison requires two or three dedicated markets")
    if len(values) not in {2, 3}:
        raise CliError("a comparison requires two or three dedicated markets")
    unknown = set(values) - {"da", "mfrr", "afrr"}
    if unknown:
        raise CliError("--markets values must be da, mfrr, or afrr")
    return tuple(market for market in ("da", "mfrr", "afrr") if market in values)


def _reject_omitted_market_flags(namespace: argparse.Namespace, selected: tuple[str, ...]) -> None:
    if "mfrr" not in selected:
        present = [name for name in _COMPARE_MFRR_DESTS if has_option(namespace, name)]
        if present:
            flags = ", ".join(f"--{name.replace('_', '-')}" for name in present)
            raise CliError(f"omitted mFRR does not accept {flags}")
    if "afrr" not in selected:
        present = [name for name in _COMPARE_AFRR_DESTS if has_option(namespace, name)]
        if present:
            flags = ", ".join(f"--{name.replace('_', '-')}" for name in present)
            raise CliError(f"omitted aFRR does not accept {flags}")


def build_comparison_request(namespace: argparse.Namespace):
    selected = selected_compare_markets(namespace)
    _reject_omitted_market_flags(namespace, selected)
    data_dir, output_dir = require_direct_paths(namespace)
    period = build_period(namespace)
    asset = build_asset(namespace)
    site = build_site(namespace)
    commitment = build_machine_commitment(namespace)
    builders = {
        "da": lambda: DayAheadCase(),
        "mfrr": lambda: build_market_case(namespace, "mfrr", prefix="mfrr_"),
        "afrr": lambda: build_market_case(namespace, "afrr", prefix="afrr_"),
    }
    configs = {
        market: SimulationConfig(
            period=period,
            market_case=builders[market](),
            asset=asset,
            site=site,
            machine_commitment=commitment,
        )
        for market in selected
    }
    return build_market_comparison_request(
        configs,
        data_dir,
        output_dir,
        solver_options=build_solver_options(namespace),
        run_id=optional_run_id(namespace),
    )


def build_sweep_request(namespace: argparse.Namespace):
    if not has_option(namespace, "market"):
        raise CliError("direct execution requires --market")
    if not has_option(namespace, "powers_mw"):
        raise CliError("direct sweep requires --powers-mw")
    if not has_option(namespace, "storage_hours_grid"):
        raise CliError("direct sweep requires --storage-hours-grid")
    data_dir, output_dir = require_direct_paths(namespace)
    template = build_asset(namespace, template=True)
    candidates = build_symmetric_asset_size_candidates(
        template,
        powers_mw=parse_numeric_axis(option_value(namespace, "powers_mw"), "--powers-mw"),
        storage_hours=parse_numeric_axis(option_value(namespace, "storage_hours_grid"), "--storage-hours-grid"),
    )
    market = option_value(namespace, "market")
    return build_asset_sweep_request(
        build_simulation_config(namespace, market, asset=template),
        candidates,
        data_dir,
        output_dir,
        solver_options=build_solver_options(namespace),
        run_id=optional_run_id(namespace),
    )
