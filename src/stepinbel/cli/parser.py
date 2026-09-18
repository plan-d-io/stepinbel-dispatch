"""Argparse surface. Construction options use suppressed defaults."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from stepinbel import __version__

_SUPPRESS = argparse.SUPPRESS


class _OnceStore(argparse.Action):
    """Reject a second occurrence of the same option. Argparse keeps the last value otherwise."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        marker = f"_seen_{self.dest}"
        if getattr(namespace, marker, False):
            parser.error(f"{option_string} may be supplied at most once")
        setattr(namespace, marker, True)
        setattr(namespace, self.dest, values)

CONSTRUCTION_DESTS: frozenset[str] = frozenset(
    {
        "delivery_start",
        "delivery_end",
        "utc_start",
        "utc_end",
        "pump_mw",
        "turbine_mw",
        "pump_efficiency",
        "turbine_efficiency",
        "storage_hours",
        "pond_energy_mwh",
        "storage_hours_basis",
        "initial_soc",
        "terminal_soc",
        "enforce_terminal_soc",
        "pump_ramp_up_min",
        "pump_ramp_down_min",
        "pump_ramp_power_fraction",
        "turbine_ramp_up_min",
        "turbine_ramp_down_min",
        "turbine_ramp_power_fraction",
        "grid_import_mw",
        "grid_export_mw",
        "pv_ac_kw",
        "pv_region",
        "pv_revenue_mode",
        "pv_fixed_price_eur_mwh",
        "wind_capacity_kw",
        "wind_profile",
        "wind_revenue_mode",
        "wind_fixed_price_eur_mwh",
        "detailed_solver_output",
        "fixed_speed_pump",
        "turbine_minimum_output_fraction",
        "forbid_simultaneous_operation",
        "mip_rel_gap",
        "mip_time_limit_s",
        "data_dir",
        "output_dir",
        "run_id",
        "market",
        "activation_profile",
        "capacity_bid_mode",
        "capacity_quantile",
        "fixed_up_capacity_price_eur_mw_h",
        "fixed_down_capacity_price_eur_mw_h",
        "capacity_coverage_hours",
        "afrr_up_capacity_fraction",
        "mfrr_activation_profile",
        "mfrr_capacity_bid_mode",
        "mfrr_capacity_quantile",
        "mfrr_fixed_up_capacity_price_eur_mw_h",
        "mfrr_capacity_coverage_hours",
        "afrr_activation_profile",
        "afrr_capacity_bid_mode",
        "afrr_capacity_quantile",
        "afrr_fixed_up_capacity_price_eur_mw_h",
        "afrr_fixed_down_capacity_price_eur_mw_h",
        "afrr_capacity_coverage_hours",
        "powers_mw",
        "storage_hours_grid",
        "markets",
    }
)

SWEEP_FORBIDDEN_DESTS: frozenset[str] = frozenset(
    {"pump_mw", "turbine_mw", "storage_hours", "pond_energy_mwh"}
)


def _add_period(parser: argparse.ArgumentParser) -> None:
    period = parser.add_argument_group("period")
    period.add_argument(
        "--delivery-start",
        dest="delivery_start",
        default=_SUPPRESS,
        metavar="YYYY-MM-DD",
        action=_OnceStore,
    )
    period.add_argument(
        "--delivery-end",
        dest="delivery_end",
        default=_SUPPRESS,
        metavar="YYYY-MM-DD",
        action=_OnceStore,
    )
    period.add_argument(
        "--utc-start",
        dest="utc_start",
        default=_SUPPRESS,
        metavar="YYYY-MM-DDTHH:MM:SSZ",
        action=_OnceStore,
    )
    period.add_argument(
        "--utc-end",
        dest="utc_end",
        default=_SUPPRESS,
        metavar="YYYY-MM-DDTHH:MM:SSZ",
        action=_OnceStore,
    )


def _add_asset(parser: argparse.ArgumentParser, *, allow_storage_and_power: bool) -> None:
    asset = parser.add_argument_group("asset")
    if allow_storage_and_power:
        asset.add_argument("--pump-mw", dest="pump_mw", type=float, default=_SUPPRESS)
        asset.add_argument("--turbine-mw", dest="turbine_mw", type=float, default=_SUPPRESS)
        asset.add_argument("--storage-hours", dest="storage_hours", type=float, default=_SUPPRESS)
        asset.add_argument("--pond-energy-mwh", dest="pond_energy_mwh", type=float, default=_SUPPRESS)
    asset.add_argument("--pump-efficiency", dest="pump_efficiency", type=float, default=_SUPPRESS)
    asset.add_argument("--turbine-efficiency", dest="turbine_efficiency", type=float, default=_SUPPRESS)
    asset.add_argument(
        "--storage-hours-basis",
        dest="storage_hours_basis",
        choices=("discharge_at_rated", "stored_energy"),
        default=_SUPPRESS,
    )
    asset.add_argument("--initial-soc", dest="initial_soc", type=float, default=_SUPPRESS)
    asset.add_argument("--terminal-soc", dest="terminal_soc", type=float, default=_SUPPRESS)
    terminal = asset.add_mutually_exclusive_group()
    terminal.add_argument(
        "--enforce-terminal-soc",
        dest="enforce_terminal_soc",
        action="store_true",
        default=_SUPPRESS,
    )
    terminal.add_argument(
        "--no-enforce-terminal-soc",
        dest="enforce_terminal_soc",
        action="store_false",
        default=_SUPPRESS,
    )
    asset.add_argument("--pump-ramp-up-min", dest="pump_ramp_up_min", type=float, default=_SUPPRESS)
    asset.add_argument("--pump-ramp-down-min", dest="pump_ramp_down_min", type=float, default=_SUPPRESS)
    asset.add_argument(
        "--pump-ramp-power-fraction",
        dest="pump_ramp_power_fraction",
        type=float,
        default=_SUPPRESS,
    )
    asset.add_argument("--turbine-ramp-up-min", dest="turbine_ramp_up_min", type=float, default=_SUPPRESS)
    asset.add_argument("--turbine-ramp-down-min", dest="turbine_ramp_down_min", type=float, default=_SUPPRESS)
    asset.add_argument(
        "--turbine-ramp-power-fraction",
        dest="turbine_ramp_power_fraction",
        type=float,
        default=_SUPPRESS,
    )


def _add_site(parser: argparse.ArgumentParser) -> None:
    site = parser.add_argument_group("site, PV, and wind")
    site.add_argument("--grid-import-mw", dest="grid_import_mw", type=float, default=_SUPPRESS)
    site.add_argument("--grid-export-mw", dest="grid_export_mw", type=float, default=_SUPPRESS)
    site.add_argument("--pv-ac-kw", dest="pv_ac_kw", type=float, default=_SUPPRESS)
    site.add_argument("--pv-region", dest="pv_region", default=_SUPPRESS)
    site.add_argument("--pv-revenue-mode", dest="pv_revenue_mode", choices=("da", "fixed"), default=_SUPPRESS)
    site.add_argument(
        "--pv-fixed-price-eur-mwh",
        dest="pv_fixed_price_eur_mwh",
        type=float,
        default=_SUPPRESS,
    )
    site.add_argument("--wind-capacity-kw", dest="wind_capacity_kw", type=float, default=_SUPPRESS)
    site.add_argument("--wind-profile", dest="wind_profile", default=_SUPPRESS)
    site.add_argument(
        "--wind-revenue-mode",
        dest="wind_revenue_mode",
        choices=("da", "fixed"),
        default=_SUPPRESS,
    )
    site.add_argument(
        "--wind-fixed-price-eur-mwh",
        dest="wind_fixed_price_eur_mwh",
        type=float,
        default=_SUPPRESS,
    )


def _add_solver(parser: argparse.ArgumentParser) -> None:
    solver = parser.add_argument_group("solver")
    solver.add_argument(
        "--detailed-solver-output",
        dest="detailed_solver_output",
        action="store_true",
        default=_SUPPRESS,
    )
    solver.add_argument(
        "--mip-rel-gap",
        dest="mip_rel_gap",
        type=float,
        default=_SUPPRESS,
        help="Relative MIP gap used only when a machine-commitment option is enabled. Default 0.015.",
    )
    solver.add_argument(
        "--mip-time-limit-s",
        dest="mip_time_limit_s",
        type=float,
        default=_SUPPRESS,
        help="HiGHS time limit in seconds used only for MILP runs. Default 900.",
    )


def _add_machine_commitment(parser: argparse.ArgumentParser) -> None:
    commitment = parser.add_argument_group("machine commitment")
    commitment.add_argument(
        "--fixed-speed-pump",
        dest="fixed_speed_pump",
        action="store_true",
        default=_SUPPRESS,
        help="Pump is off or at rated electrical power.",
    )
    commitment.add_argument(
        "--turbine-minimum-output-fraction",
        dest="turbine_minimum_output_fraction",
        type=float,
        default=_SUPPRESS,
        help="Minimum turbine output as a fraction of rated electrical power. 0 disables.",
    )
    commitment.add_argument(
        "--forbid-simultaneous-operation",
        dest="forbid_simultaneous_operation",
        action="store_true",
        default=_SUPPRESS,
        help="Forbid simultaneous pumping and generation.",
    )


def _add_paths(parser: argparse.ArgumentParser, *, include_request: bool) -> None:
    paths = parser.add_argument_group("paths and identity")
    if include_request:
        paths.add_argument("--request", dest="request", default=_SUPPRESS, metavar="PATH")
    paths.add_argument("--data-dir", dest="data_dir", default=_SUPPRESS, metavar="PATH")
    paths.add_argument("--output-dir", dest="output_dir", default=_SUPPRESS, metavar="PATH")
    paths.add_argument("--run-id", dest="run_id", default=_SUPPRESS)
    paths.add_argument("--quiet", dest="quiet", action="store_true", default=False)


def _add_run_market(parser: argparse.ArgumentParser, *, include_market: bool) -> None:
    market = parser.add_argument_group("market case")
    if include_market:
        market.add_argument("--market", dest="market", choices=("da", "mfrr", "afrr"), default=_SUPPRESS)
    market.add_argument(
        "--activation-profile",
        dest="activation_profile",
        choices=("balanced", "passive"),
        default=_SUPPRESS,
    )
    market.add_argument(
        "--capacity-bid-mode",
        dest="capacity_bid_mode",
        choices=("historical", "fixed"),
        default=_SUPPRESS,
    )
    market.add_argument("--capacity-quantile", dest="capacity_quantile", type=float, default=_SUPPRESS)
    market.add_argument(
        "--fixed-up-capacity-price-eur-mw-h",
        dest="fixed_up_capacity_price_eur_mw_h",
        type=float,
        default=_SUPPRESS,
    )
    market.add_argument(
        "--fixed-down-capacity-price-eur-mw-h",
        dest="fixed_down_capacity_price_eur_mw_h",
        type=float,
        default=_SUPPRESS,
    )
    market.add_argument(
        "--capacity-coverage-hours",
        dest="capacity_coverage_hours",
        type=float,
        default=_SUPPRESS,
    )
    market.add_argument(
        "--afrr-up-capacity-fraction",
        dest="afrr_up_capacity_fraction",
        type=float,
        default=_SUPPRESS,
    )


def _add_compare_markets(parser: argparse.ArgumentParser) -> None:
    mfrr = parser.add_argument_group("mFRR case")
    mfrr.add_argument(
        "--mfrr-activation-profile",
        dest="mfrr_activation_profile",
        choices=("balanced", "passive"),
        default=_SUPPRESS,
    )
    mfrr.add_argument(
        "--mfrr-capacity-bid-mode",
        dest="mfrr_capacity_bid_mode",
        choices=("historical", "fixed"),
        default=_SUPPRESS,
    )
    mfrr.add_argument("--mfrr-capacity-quantile", dest="mfrr_capacity_quantile", type=float, default=_SUPPRESS)
    mfrr.add_argument(
        "--mfrr-fixed-up-capacity-price-eur-mw-h",
        dest="mfrr_fixed_up_capacity_price_eur_mw_h",
        type=float,
        default=_SUPPRESS,
    )
    mfrr.add_argument(
        "--mfrr-capacity-coverage-hours",
        dest="mfrr_capacity_coverage_hours",
        type=float,
        default=_SUPPRESS,
    )
    afrr = parser.add_argument_group("aFRR case")
    afrr.add_argument(
        "--afrr-activation-profile",
        dest="afrr_activation_profile",
        choices=("balanced", "passive"),
        default=_SUPPRESS,
    )
    afrr.add_argument(
        "--afrr-capacity-bid-mode",
        dest="afrr_capacity_bid_mode",
        choices=("historical", "fixed"),
        default=_SUPPRESS,
    )
    afrr.add_argument("--afrr-capacity-quantile", dest="afrr_capacity_quantile", type=float, default=_SUPPRESS)
    afrr.add_argument(
        "--afrr-fixed-up-capacity-price-eur-mw-h",
        dest="afrr_fixed_up_capacity_price_eur_mw_h",
        type=float,
        default=_SUPPRESS,
    )
    afrr.add_argument(
        "--afrr-fixed-down-capacity-price-eur-mw-h",
        dest="afrr_fixed_down_capacity_price_eur_mw_h",
        type=float,
        default=_SUPPRESS,
    )
    afrr.add_argument(
        "--afrr-capacity-coverage-hours",
        dest="afrr_capacity_coverage_hours",
        type=float,
        default=_SUPPRESS,
    )
    afrr.add_argument(
        "--afrr-up-capacity-fraction",
        dest="afrr_up_capacity_fraction",
        type=float,
        default=_SUPPRESS,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stepinbel",
        description="StepInBel automation and diagnostic command line.",
    )
    parser.add_argument("--version", action="version", version=f"stepinbel {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Execute one dedicated-market case.")
    _add_paths(run, include_request=True)
    _add_period(run)
    _add_asset(run, allow_storage_and_power=True)
    _add_site(run)
    _add_machine_commitment(run)
    _add_solver(run)
    _add_run_market(run, include_market=True)

    compare = sub.add_parser("compare", help="Rank dedicated DA, mFRR, and aFRR alternatives.")
    _add_paths(compare, include_request=True)
    _add_period(compare)
    _add_asset(compare, allow_storage_and_power=True)
    _add_site(compare)
    _add_machine_commitment(compare)
    _add_solver(compare)
    compare.add_argument(
        "--markets",
        dest="markets",
        nargs="+",
        choices=("da", "mfrr", "afrr"),
        default=_SUPPRESS,
        action=_OnceStore,
        metavar="MARKET",
        help="Dedicated markets to compare. Omitted means da mfrr afrr.",
    )
    _add_compare_markets(compare)

    sweep = sub.add_parser("sweep", help="Rank a symmetric power-by-duration asset grid.")
    _add_paths(sweep, include_request=True)
    _add_period(sweep)
    _add_asset(sweep, allow_storage_and_power=False)
    _add_site(sweep)
    _add_machine_commitment(sweep)
    _add_solver(sweep)
    _add_run_market(sweep, include_market=True)
    axes = sweep.add_argument_group("symmetric sweep axes")
    axes.add_argument("--powers-mw", dest="powers_mw", default=_SUPPRESS, metavar="V1,V2,...")
    axes.add_argument("--storage-hours-grid", dest="storage_hours_grid", default=_SUPPRESS, metavar="V1,V2,...")

    validate = sub.add_parser("validate", help="Independently validate a completed artifact directory.")
    validate.add_argument("--kind", dest="kind", choices=("run", "comparison", "sweep"), required=True)
    validate.add_argument("--directory", dest="directory", required=True, metavar="PATH")

    info = sub.add_parser("data-info", help="Describe a published data bundle and its coverage.")
    info.add_argument("--data-dir", dest="data_dir", required=True, metavar="PATH")
    return parser


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(None if args is None else list(args))
