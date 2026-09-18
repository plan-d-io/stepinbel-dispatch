"""Deterministic dedicated-market comparison report."""

from __future__ import annotations

from collections.abc import Sequence

from stepinbel.config import AFRRCase, BelgianDeliveryPeriod, MFRRCase, UtcPeriod
from stepinbel.reporting.constants import COMPARISON_INTERPRETATION
from stepinbel.workflows.comparison_request import MarketComparisonRequest, MarketComparisonRow
from stepinbel.workflows.constants import BEHAVIOURAL_BASELINE
from stepinbel.workflows.serialize import format_utc, serialize_period


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _qty(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}"


def _period_text(request: MarketComparisonRequest) -> str:
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


def _strategy_text(request: MarketComparisonRequest, market: str) -> str:
    case = request.case_requests[market].config.market_case
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


def render_market_comparison_report(
    request: MarketComparisonRequest,
    rows: Sequence[MarketComparisonRow],
    *,
    highest_revenue_market: str,
    resolved_start_utc,
    resolved_end_exclusive_utc,
    mip_termination_warning: str | None = None,
) -> str:
    """Return a deterministic UTF-8 report for one completed comparison."""
    child = next(iter(request.case_requests.values()))
    asset = child.config.asset
    site = child.config.site
    pv_enabled = child.config.pv_enabled()
    wind_enabled = child.config.wind_enabled()
    lines: list[str] = [
        "StepInBel dedicated-market alternatives",
        "=======================================",
        "",
        "Run identity",
        "------------",
        f"Comparison run ID: {request.run_id}",
        f"Status: completed",
        f"Software: StepInBel {request.software_version}",
        f"Created (UTC): {format_utc(request.created_at_utc)}",
        f"Requested period: {_period_text(request)}",
        (
            "Resolved UTC window: "
            f"[{format_utc(resolved_start_utc)}, {format_utc(resolved_end_exclusive_utc)})"
        ),
        "",
        "Shared PHS and grid configuration",
        "---------------------------------",
        f"Pump rating: {_qty(asset.power_pump_mw)} MW",
        f"Turbine rating: {_qty(asset.power_turbine_mw)} MW",
        f"Pump efficiency: {asset.eta_pump:.4f}",
        f"Turbine efficiency: {asset.eta_turbine:.4f}",
        f"Round-trip efficiency: {asset.round_trip_efficiency():.4f}",
        f"Reservoir source: {asset.e_max_source()}",
        f"Reservoir E_max: {_qty(asset.e_max_mwh())} MWh",
        f"Effective grid import: {_qty(child.config.effective_grid_import_mw())} MW",
        f"Effective grid export: {_qty(child.config.effective_grid_export_mw())} MW",
        f"Co-located PV: {_qty(site.pv_ac_kw, 1)} kW AC"
        + (f" ({site.pv_region}, {site.pv_revenue_mode} settlement)" if pv_enabled else ""),
    ]
    if wind_enabled:
        lines.append(
            f"Co-located wind: {_qty(site.wind_capacity_kw, 1)} kW"
            f" ({site.wind_profile_id}, {site.wind_revenue_mode} settlement)"
        )
    lines.extend(
        [
        "",
        "Dedicated-market cases",
        "----------------------",
    ]
    )
    _CASE_LABELS = (("da", "Day-ahead"), ("mfrr", "mFRR"), ("afrr", "aFRR"))
    for market, label in _CASE_LABELS:
        if market in request.case_requests:
            lines.append(f"{label}: {_strategy_text(request, market)}")
    lines.extend(
        [
            "",
            "Ranked alternatives",
            "-------------------",
        ]
    )
    for row in rows:
        block = [
            f"Rank {row.revenue_rank}: {row.market} (run {row.case_run_id})",
            f"  Total site revenue: {_money(row.total_site_revenue_eur)} EUR",
            f"  Market energy net: {_money(row.market_energy_net_eur)} EUR",
            f"  Capacity revenue: {_money(row.capacity_revenue_eur)} EUR",
            f"  PV export revenue: {_money(row.pv_revenue_eur)} EUR",
        ]
        if wind_enabled:
            block.append(f"  Wind export revenue: {_money(row.wind_revenue_eur)} EUR")
        block.extend(
            [
                f"  Difference from highest: {_money(row.difference_from_highest_eur)} EUR",
                f"  Pumped energy: {_qty(row.pumped_mwh)} MWh",
                f"  Turbined energy: {_qty(row.turbined_mwh)} MWh",
                f"  Full cycles: {_qty(row.full_cycles)}",
                (
                    "  Simultaneous-operation diagnostic: "
                    f"{row.simultaneous_interval_count} intervals, "
                    f"{_qty(row.simultaneous_overlap_mwh)} MWh overlap, "
                    f"{_money(row.simultaneous_interval_energy_net_eur)} EUR energy-net"
                ),
            ]
        )
        lines.extend(block)
    if mip_termination_warning:
        lines.extend(
            [
                "",
                "Solver termination",
                "------------------",
                mip_termination_warning,
            ]
        )
    if pv_enabled:
        lines.extend(
            [
                "",
                "Photovoltaics",
                "-------------",
                "PV can serve pumping, be exported within the shared grid limit, or be curtailed.",
                "Self-consumed PV has no separate revenue line; it lowers grid charging cost.",
            ]
        )
    if wind_enabled:
        settlement = (
            "Day-ahead wind settlement uses day-ahead prices in every market case."
            if site.wind_revenue_mode == "da"
            else "Wind exports settle at the configured fixed price."
        )
        lines.extend(
            [
                "",
                "Wind",
                "----",
                "Wind can serve pumping, be exported within the shared grid limit, or be curtailed.",
                "Self-consumed wind has no separate revenue line; it lowers grid charging cost.",
                settlement,
            ]
        )
        if pv_enabled:
            lines.append(
                "When PV and wind export prices are equal and a shared constraint binds, "
                "the source-specific split may be non-unique. Total routing and total site "
                "revenue remain authoritative."
            )
    accounting = "Total site revenue includes market energy net, capacity revenue, and PV export revenue."
    if wind_enabled:
        accounting = (
            "Total site revenue includes market energy net, capacity revenue, "
            "PV export revenue, and wind export revenue."
        )
    pumping = "PV used for pumping reduces grid charging."
    if wind_enabled and pv_enabled:
        pumping = "PV and wind used for pumping reduce grid charging."
    elif wind_enabled:
        pumping = "Wind used for pumping reduces grid charging."
    lines.extend(
        [
            "",
            "Accounting",
            "----------",
            accounting,
            pumping,
            "",
            "Highest modelled total-revenue market",
            "-------------------------------------",
            f"Highest modelled total-revenue market: {highest_revenue_market}",
            "This ranking is a mathematical comparison of dedicated-market alternatives.",
            "It is not an investment recommendation and does not claim that market risks are equivalent.",
            "",
            "Provenance",
            "----------",
            f"Published data manifest SHA-256: {request.data_manifest_sha256}",
            (
                f"Accepted behavioural baseline: {BEHAVIOURAL_BASELINE['project']} "
                f"{BEHAVIOURAL_BASELINE['tag']} ({BEHAVIOURAL_BASELINE['commit']})"
            ),
            "Child methodology references remain in each dedicated-market audit directory.",
        ]
    )
    if "da" in request.case_requests:
        lines.append("Day-ahead has no applicable Elia conformance reference.")
    lines.extend(
        [
            "These are methodology references, not exact Watts.Happening-conformance claims.",
            "",
            "Statements",
            "----------",
            COMPARISON_INTERPRETATION,
            "This comparison does not co-optimize or combine market participation.",
            "This is a historical perfect-foresight simulation, not a forecast or operating instruction.",
            "",
        ]
    )
    return "\n".join(lines)
