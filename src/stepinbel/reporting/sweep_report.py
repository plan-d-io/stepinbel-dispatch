"""Deterministic finite asset-parameter sweep report."""

from __future__ import annotations

from collections.abc import Sequence

from stepinbel.config import AFRRCase, BelgianDeliveryPeriod, MFRRCase, UtcPeriod
from stepinbel.reporting.constants import ASSET_SWEEP_HIGHEST_WORDING, ASSET_SWEEP_INTERPRETATION
from stepinbel.workflows.constants import BEHAVIOURAL_BASELINE, ELIA_METHODOLOGY
from stepinbel.workflows.serialize import format_utc, serialize_period
from stepinbel.workflows.sweep_request import (
    AssetSweepRequest,
    AssetSweepRow,
    grid_export_limit_mode,
    grid_import_limit_mode,
)


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _qty(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}"


def _period_text(request: AssetSweepRequest) -> str:
    period = next(iter(request.case_requests.values())).config.period
    payload = serialize_period(period)
    if isinstance(period, BelgianDeliveryPeriod):
        return (
            f"Belgian delivery {payload['start_date']} to "
            f"{payload['end_date_inclusive']} inclusive"
        )
    if isinstance(period, UtcPeriod):
        return f"UTC [{payload['start_utc']}, {payload['end_exclusive_utc']})"
    return "explicit period"


def _strategy_text(request: AssetSweepRequest) -> str:
    child = request.case_requests[request.candidate_order[0]]
    case = child.config.market_case
    market = request.market
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


def render_asset_sweep_report(
    request: AssetSweepRequest,
    rows: Sequence[AssetSweepRow],
    *,
    highest_revenue_candidate_id: str,
    resolved_start_utc,
    resolved_end_exclusive_utc,
    solver_name: str,
    solver_version: str,
) -> str:
    """Return a deterministic UTF-8 report for one completed asset sweep."""
    child = request.case_requests[request.candidate_order[0]]
    site = child.config.site
    pv_enabled = child.config.pv_enabled()
    import_mode = grid_import_limit_mode(site)
    export_mode = grid_export_limit_mode(site)
    highest = next(row for row in rows if row.candidate_id == highest_revenue_candidate_id)
    lines: list[str] = [
        "StepInBel asset-parameter sweep",
        "===============================",
        "",
        "Run identity",
        "------------",
        f"Sweep run ID: {request.run_id}",
        "Status: completed",
        f"Software: StepInBel {request.software_version}",
        f"Created (UTC): {format_utc(request.created_at_utc)}",
        f"Dedicated market: {request.market}",
        f"Strategy: {_strategy_text(request)}",
        f"Requested period: {_period_text(request)}",
        (
            "Resolved UTC window: "
            f"[{format_utc(resolved_start_utc)}, {format_utc(resolved_end_exclusive_utc)})"
        ),
        f"Interval count: {rows[0].interval_count}",
        f"Candidate count: {len(rows)}",
        "",
        "Grid-connection modes",
        "---------------------",
        f"Import limit mode: {import_mode}",
        f"Export limit mode: {export_mode}",
        "Explicit site limits stay fixed. Otherwise each candidate uses its pump or turbine rating.",
        f"Co-located PV: {_qty(site.pv_ac_kw, 1)} kW AC"
        + (f" ({site.pv_region}, {site.pv_revenue_mode} settlement)" if pv_enabled else ""),
        "",
        "Ranked asset configurations",
        "---------------------------",
    ]
    for row in rows:
        lines.extend(
            [
                f"Rank {row.revenue_rank}: {row.candidate_label} ({row.candidate_id})",
                f"  Pump / turbine: {_qty(row.power_pump_mw)} / {_qty(row.power_turbine_mw)} MW",
                f"  Usable energy: {_qty(row.usable_energy_mwh)} MWh",
                (
                    "  Charge / discharge duration: "
                    f"{_qty(row.charge_duration_h)} / {_qty(row.discharge_duration_h)} h"
                ),
                f"  Total site revenue: {_money(row.total_site_revenue_eur)} EUR",
                f"  Market energy net: {_money(row.market_energy_net_eur)} EUR",
                f"  Capacity revenue: {_money(row.capacity_revenue_eur)} EUR",
                f"  PV export revenue: {_money(row.pv_revenue_eur)} EUR",
                f"  Difference from highest: {_money(row.difference_from_highest_eur)} EUR",
                f"  Full cycles: {_qty(row.full_cycles)}",
            ]
        )
    if pv_enabled:
        lines.extend(
            [
                "",
                "Photovoltaics",
                "-------------",
            ]
        )
        for row in rows:
            lines.append(
                (
                    f"{row.candidate_id}: available {_qty(row.pv_available_mwh)} MWh, "
                    f"self-consumed {_qty(row.pv_self_consumed_mwh)} MWh, "
                    f"exported {_qty(row.pv_exported_mwh)} MWh, "
                    f"curtailed {_qty(row.pv_curtailed_mwh)} MWh"
                )
            )
        lines.append("Self-consumed PV has no separate revenue line; it lowers grid charging cost.")
    lines.extend(
        [
            "",
            "Highest modelled site revenue among the tested asset configurations",
            "------------------------------------------------------------------",
            f"Highest-revenue tested candidate: {highest.candidate_label} ({highest.candidate_id})",
            ASSET_SWEEP_HIGHEST_WORDING,
            "Ranking excludes asset costs and is not an investment recommendation.",
            "Candidate revenues are alternatives and are not additive.",
            "",
            "Provenance",
            "----------",
            f"Published data manifest SHA-256: {request.data_manifest_sha256}",
            (
                f"Accepted behavioural baseline: {BEHAVIOURAL_BASELINE['project']} "
                f"{BEHAVIOURAL_BASELINE['tag']} ({BEHAVIOURAL_BASELINE['commit']})"
            ),
            f"Solver: {solver_name} {solver_version}",
        ]
    )
    if request.market == "da":
        lines.append(
            "Day-ahead has no applicable Elia conformance reference; the accepted PHS day-ahead behaviour is used."
        )
    elif request.market in ELIA_METHODOLOGY:
        ref = ELIA_METHODOLOGY[request.market]
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
            ASSET_SWEEP_INTERPRETATION,
            "This is a historical perfect-foresight simulation, not a forecast or operating instruction.",
            "Selecting a candidate and then running dedicated-market comparison is a separate workflow.",
            "",
        ]
    )
    return "\n".join(lines)
