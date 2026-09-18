"""Deterministic human-readable run report."""

from __future__ import annotations

from stepinbel.config import AFRRCase, BelgianDeliveryPeriod, MFRRCase, UtcPeriod
from stepinbel.optimizer import DispatchResult
from stepinbel.optimizer.types import (
    TERMINATION_ACCEPTED_WITHIN_GAP,
    TERMINATION_TIME_LIMIT_FEASIBLE,
)
from stepinbel.workflows.constants import BEHAVIOURAL_BASELINE, ELIA_METHODOLOGY
from stepinbel.workflows.request import CaseRunRequest
from stepinbel.workflows.serialize import format_utc, serialize_period


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _qty(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}"


def _period_text(request: CaseRunRequest) -> str:
    period = request.config.period
    payload = serialize_period(period)
    if isinstance(period, BelgianDeliveryPeriod):
        return (
            f"Belgian delivery {payload['start_date']} to "
            f"{payload['end_date_inclusive']} inclusive"
        )
    if isinstance(period, UtcPeriod):
        return f"UTC [{payload['start_utc']}, {payload['end_exclusive_utc']})"
    return "explicit period"


def _strategy_text(request: CaseRunRequest) -> str:
    case = request.config.market_case
    market = request.config.market
    if isinstance(case, (MFRRCase, AFRRCase)):
        bid = type(case.capacity_bid).__name__
        extra = ""
        if isinstance(case, AFRRCase):
            extra = f", up-capacity fraction {case.up_capacity_fraction:.4f}"
        return (
            f"{market} / {case.activation_profile} / {bid} "
            f"(coverage {case.capacity_coverage_hours:.4g} h{extra})"
        )
    return f"{market} energy-only"


def render_run_report(
    request: CaseRunRequest,
    result: DispatchResult,
    *,
    simultaneous_interval_energy_net_eur: float,
) -> str:
    """Return a deterministic UTF-8 report for one completed dedicated-market run."""
    summary = result.summary
    asset = request.config.asset
    site = request.config.site
    window = result.period.window
    market = request.config.market
    lines: list[str] = [
        "StepInBel dedicated-market run",
        "==============================",
        "",
        "Run identity",
        "------------",
        f"Run ID: {request.run_id}",
        f"Status: completed",
        f"Software: StepInBel {request.software_version}",
        f"Created (UTC): {format_utc(request.created_at_utc)}",
        "",
        "Market and strategy",
        "-------------------",
        f"Dedicated market: {market}",
        f"Strategy: {_strategy_text(request)}",
        "",
        "Period",
        "------",
        f"Requested period: {_period_text(request)}",
        f"Resolved UTC window: [{format_utc(window.start_utc)}, {format_utc(window.end_exclusive_utc)})",
        f"Interval count: {summary.interval_count}",
        f"Duration: {_qty(summary.duration_hours, 2)} h",
        "",
        "Asset and site",
        "--------------",
        f"Pump rating: {_qty(asset.power_pump_mw)} MW",
        f"Turbine rating: {_qty(asset.power_turbine_mw)} MW",
        f"Pump efficiency: {asset.eta_pump:.4f}",
        f"Turbine efficiency: {asset.eta_turbine:.4f}",
        f"Round-trip efficiency: {asset.round_trip_efficiency():.4f}",
        f"Reservoir source: {asset.e_max_source()}",
        f"Reservoir E_max: {_qty(asset.e_max_mwh())} MWh",
        f"Usable energy: {_qty(asset.usable_energy_mwh())} MWh",
        f"Effective grid import: {_qty(request.config.effective_grid_import_mw())} MW",
        f"Effective grid export: {_qty(request.config.effective_grid_export_mw())} MW",
        f"Co-located PV: {_qty(site.pv_ac_kw, 1)} kW AC"
        + (f" ({site.pv_region}, {site.pv_revenue_mode})" if request.config.pv_enabled() else ""),
    ]
    if request.config.wind_enabled():
        lines.append(
            f"Co-located wind: {_qty(site.wind_capacity_kw, 1)} kW"
            f" ({site.wind_profile_id}, {site.wind_revenue_mode})"
        )
    formula = (
        "Total site revenue equals market energy net plus capacity revenue plus PV export revenue."
    )
    if request.config.wind_enabled():
        formula = (
            "Total site revenue equals market energy net plus capacity revenue "
            "plus PV export revenue plus wind export revenue."
        )
    lines.extend(
        [
        "",
        "Revenue",
        "-------",
        f"Total site revenue: {_money(summary.total_site_revenue_eur)} EUR",
        f"Market energy net: {_money(summary.market_energy_net_eur)} EUR",
        f"Capacity revenue: {_money(summary.capacity_revenue_eur)} EUR",
        f"PV export revenue: {_money(summary.pv_revenue_eur)} EUR",
        ]
    )
    if request.config.wind_enabled():
        lines.append(f"Wind export revenue: {_money(summary.wind_revenue_eur)} EUR")
    lines.extend(
        [
        formula,
        "",
        "Operations",
        "----------",
        f"Pumped energy: {_qty(summary.pumped_mwh)} MWh",
        f"Turbined energy: {_qty(summary.turbined_mwh)} MWh",
        f"Full cycles: {_qty(summary.turbined_mwh / summary.e_max_mwh)}",
        f"Initial reservoir: {_qty(summary.reservoir_initial_mwh)} MWh",
        f"Final reservoir: {_qty(summary.reservoir_final_mwh)} MWh",
    ]
    )
    if market in {"mfrr", "afrr"}:
        lines.extend(
            [
                "",
                "Capacity results",
                "----------------",
                f"Capacity rows: {result.capacity_results.num_rows}",
                f"Capacity revenue: {_money(summary.capacity_revenue_eur)} EUR",
            ]
        )
    if request.config.pv_enabled():
        lines.extend(
            [
                "",
                "Photovoltaics",
                "-------------",
                f"Available: {_qty(summary.pv_available_mwh)} MWh",
                f"Self-consumed: {_qty(summary.pv_self_consumed_mwh)} MWh",
                f"Exported: {_qty(summary.pv_exported_mwh)} MWh",
                f"Curtailed: {_qty(summary.pv_curtailed_mwh)} MWh",
                "Self-consumed PV has no separate revenue line; it lowers grid charging cost.",
            ]
        )
    if request.config.wind_enabled():
        settlement = (
            "Day-ahead wind settlement uses day-ahead prices in every market case."
            if site.wind_revenue_mode == "da"
            else (
                "Wind exports settle at the configured fixed price "
                f"{_qty(site.wind_fixed_price_eur_mwh)} EUR/MWh."
            )
        )
        lines.extend(
            [
                "",
                "Wind",
                "----",
                f"Available: {_qty(summary.wind_available_mwh)} MWh",
                f"Self-consumed: {_qty(summary.wind_self_consumed_mwh)} MWh",
                f"Exported: {_qty(summary.wind_exported_mwh)} MWh",
                f"Curtailed: {_qty(summary.wind_curtailed_mwh)} MWh",
                "Self-consumed wind has no separate revenue line; it lowers grid charging cost.",
                settlement,
            ]
        )
        if request.config.pv_enabled():
            lines.append(
                "When PV and wind export prices are equal and a shared constraint binds, "
                "the source-specific split may be non-unique. Total routing and total site "
                "revenue remain authoritative."
            )
    lines.extend(
        [
            "",
            "Simultaneous-operation diagnostic",
            "---------------------------------",
            f"Simultaneous intervals: {summary.simultaneous_interval_count}",
            f"Overlap energy: {_qty(summary.simultaneous_overlap_mwh)} MWh",
            f"Simultaneous-interval energy-net revenue: {_money(simultaneous_interval_energy_net_eur)} EUR",
            "Simultaneous-interval revenue is diagnostic and is not incremental value attributable to simultaneous operation.",
        ]
    )
    commitment = request.config.machine_commitment
    if commitment.physically_active():
        rated_turbine = float(asset.power_turbine_mw)
        floor_mw = commitment.turbine_minimum_output_fraction * rated_turbine
        lines.extend(
            [
                "",
                "Machine commitment",
                "------------------",
                f"Formulation: mixed-integer (MILP)",
                f"Fixed-speed pump: {'enabled' if commitment.fixed_speed_pump else 'disabled'}",
                (
                    f"Turbine minimum output: {commitment.turbine_minimum_output_fraction:.4g} of "
                    f"rated electrical power ({_qty(floor_mw)} MW)"
                    if commitment.turbine_minimum_active()
                    else "Turbine minimum output: disabled"
                ),
                f"Strict mutual exclusion: {'enabled' if commitment.forbid_simultaneous_operation else 'disabled'}",
            ]
        )
    lines.extend(
        [
            "",
            "Solver and feasibility",
            "----------------------",
            f"Solver: {result.solver.solver_name} {result.solver.solver_version}",
            f"Status: {result.solver.status}",
        ]
    )
    if commitment.physically_active():
        diagnostics = result.solver.diagnostics
        achieved = diagnostics.get("achieved_mip_gap", diagnostics.get("mip_gap"))
        bound = diagnostics.get("best_bound", diagnostics.get("mip_dual_bound"))
        incumbent = diagnostics.get("incumbent_objective", summary.total_site_revenue_eur)
        termination = diagnostics.get("termination", TERMINATION_ACCEPTED_WITHIN_GAP)
        if termination == TERMINATION_TIME_LIMIT_FEASIBLE:
            lines.extend(
                [
                    "Requested MIP gap was not reached.",
                    "This completed result is the best available feasible incumbent at the time limit.",
                    "It is not optimal and is not accepted within the requested MIP gap.",
                ]
            )
        else:
            lines.append(
                "HiGHS status 'optimal' under a configured MIP gap means accepted within that tolerance, not a proven zero-gap optimum."
            )
        lines.extend(
            [
                f"Termination: {termination}",
                f"Requested MIP relative gap: {request.solver_options.mip_rel_gap:g}",
                f"Achieved MIP relative gap: {achieved}",
                f"Incumbent objective: {incumbent}",
                f"Best bound: {bound}",
                f"Time limit: {request.solver_options.time_limit_s:g} s",
                f"Integer variables: {result.solver.num_integer}",
                f"Binary variables: {result.solver.num_binary}",
            ]
        )
    lines.extend(
        [
            f"Feasibility ok: {result.feasibility.ok}",
            f"Columns / rows / nonzeros: {result.solver.num_col} / {result.solver.num_row} / {result.solver.num_nz}",
            "",
            "Provenance",
            "----------",
            f"Published data manifest SHA-256: {request.data_manifest_sha256}",
            (
                f"Accepted behavioural baseline: {BEHAVIOURAL_BASELINE['project']} "
                f"{BEHAVIOURAL_BASELINE['tag']} ({BEHAVIOURAL_BASELINE['commit']})"
            ),
        ]
    )
    if market == "da":
        lines.append(
            "Day-ahead has no applicable Elia conformance reference; the accepted PHS day-ahead behaviour is used."
        )
    elif market in ELIA_METHODOLOGY:
        ref = ELIA_METHODOLOGY[market]
        lines.append(
            f"Applicable methodology reference: {ref['filename']} SHA-256 {ref['sha256']}."
        )
        lines.append(ref["statement"])
    lines.extend(
        [
            "These are methodology references, not exact Watts.Happening-conformance claims.",
            "",
            "Statements",
            "----------",
            "This is a historical perfect-foresight simulation, not a forecast or operating instruction.",
            "This is a dedicated-market result and must not be added to other market runs.",
            "Total site revenue equals market energy net plus capacity revenue plus PV export revenue.",
            "Self-consumed PV has no separate revenue line; it lowers grid charging cost.",
            "Simultaneous-interval revenue is diagnostic and is not incremental value attributable to simultaneous operation.",
            "",
        ]
    )
    return "\n".join(lines)
