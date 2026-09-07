from __future__ import annotations

import pytest

from stepinbel.config import SiteConfig
from stepinbel.reporting import render_market_comparison_report
from stepinbel.reporting.comparison_artifacts import comparison_rows_from_child_summaries
from stepinbel.reporting.constants import COMPARISON_INTERPRETATION
from tests.workflow_helpers import comparison_configs, utc


def _request(data_root, tmp_path, site=None):
    from stepinbel.workflows import build_market_comparison_request

    return build_market_comparison_request(
        comparison_configs(site=site),
        data_root,
        tmp_path / "out",
        run_id="cmp-report-01",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )


def _summaries():
    def one(total: float, market_net: float, capacity: float, pv: float) -> dict:
        return {
            "interval_count": 96,
            "duration_hours": 24.0,
            "e_max_mwh": 4.0,
            "energy_gross_eur": market_net + 1.0,
            "grid_charging_cost_eur": 1.0,
            "market_energy_net_eur": market_net,
            "capacity_revenue_eur": capacity,
            "pv_revenue_eur": pv,
            "total_site_revenue_eur": total,
            "pumped_mwh": 3.0,
            "turbined_mwh": 2.0,
            "pv_available_mwh": 1.0 if pv else 0.0,
            "pv_self_consumed_mwh": 0.5 if pv else 0.0,
            "pv_exported_mwh": 0.5 if pv else 0.0,
            "pv_curtailed_mwh": 0.0,
            "simultaneous_interval_count": 0,
            "simultaneous_overlap_mwh": 0.0,
            "diagnostics": {"simultaneous_interval_energy_net_eur": 0.0},
        }

    return {
        "da": one(10.0, 10.0, 0.0, 0.0),
        "mfrr": one(30.0, 20.0, 10.0, 0.0),
        "afrr": one(20.0, 12.0, 8.0, 0.0),
    }


def _report(request, summaries=None):
    ids = {market: request.case_requests[market].run_id for market in ("da", "mfrr", "afrr")}
    rows, highest = comparison_rows_from_child_summaries(summaries or _summaries(), ids)
    start, end = next(iter(request.case_requests.values())).config.period.to_utc_bounds()
    return render_market_comparison_report(
        request,
        rows,
        highest_revenue_market=highest,
        resolved_start_utc=start,
        resolved_end_exclusive_utc=end,
    ), highest


def test_deterministic_comparison_report(data_root, tmp_path) -> None:
    request = _request(data_root, tmp_path)
    first, highest = _report(request)
    second, _ = _report(request)
    assert first == second
    assert "StepInBel dedicated-market alternatives" in first
    assert "cmp-report-01" in first
    assert "Dedicated-market cases" in first
    assert "Ranked alternatives" in first
    assert "da" in first and "mfrr" in first and "afrr" in first
    assert "Total site revenue" in first
    assert "Market energy net" in first
    assert "Capacity revenue" in first
    assert "PV export revenue" in first
    assert "Pumped energy" in first
    assert "Turbined energy" in first
    assert "Full cycles" in first
    assert "Simultaneous-operation diagnostic" in first
    assert "Published data manifest SHA-256" in first
    assert "phs-mvp-0.1.0" in first
    assert f"Highest modelled total-revenue market: {highest}" in first
    assert COMPARISON_INTERPRETATION in first
    assert "does not co-optimize or combine market participation" in first
    assert "Total site revenue includes market energy net, capacity revenue, and PV export revenue." in first
    assert "PV used for pumping reduces grid charging." in first
    lowered = first.lower()
    assert "fcr" not in lowered
    assert " god" not in lowered and not lowered.startswith("god")
    assert "active" not in lowered
    assert "recommended market" not in lowered
    assert "optimal market" not in lowered


@pytest.mark.parametrize("markets", (("da", "mfrr"), ("da", "afrr"), ("mfrr", "afrr")))
def test_subset_report_lists_selected_cases_only(data_root, tmp_path, markets) -> None:
    from stepinbel.workflows import build_market_comparison_request
    from tests.workflow_helpers import comparison_configs

    request = build_market_comparison_request(
        comparison_configs(markets=markets),
        data_root,
        tmp_path / ("rep-" + "-".join(markets)),
        run_id="cmp-rep-sub",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    summaries = {market: _summaries()[market] for market in markets}
    ids = {market: request.case_requests[market].run_id for market in markets}
    rows, highest = comparison_rows_from_child_summaries(summaries, ids)
    start, end = next(iter(request.case_requests.values())).config.period.to_utc_bounds()
    text = render_market_comparison_report(
        request,
        rows,
        highest_revenue_market=highest,
        resolved_start_utc=start,
        resolved_end_exclusive_utc=end,
    )
    labels = {"da": "Day-ahead:", "mfrr": "mFRR:", "afrr": "aFRR:"}
    for market, label in labels.items():
        if market in markets:
            assert label in text
        else:
            assert label not in text
            assert f"Rank " not in text or market not in [row.market for row in rows]
    omitted = next(iter(set(labels) - set(markets)))
    assert f"Rank 1: {omitted}" not in text
    assert f"Rank 2: {omitted}" not in text
    assert f"Rank 3: {omitted}" not in text
    if "da" in markets:
        assert "Day-ahead has no applicable Elia conformance reference." in text
    else:
        assert "Day-ahead has no applicable Elia conformance reference." not in text
    assert COMPARISON_INTERPRETATION in text
    assert "does not co-optimize or combine market participation" in text
    assert "perfect-foresight" in text


def test_pv_wording_is_conditional(data_root, tmp_path) -> None:
    without = _request(data_root, tmp_path / "no")
    without_text, _ = _report(without)
    assert "Photovoltaics" not in without_text

    with_pv = _request(data_root, tmp_path / "pv", site=SiteConfig(pv_ac_kw=90.0, pv_region="Belgium"))
    pv_text, _ = _report(with_pv)
    assert "Photovoltaics" in pv_text
    assert "PV can serve pumping" in pv_text
    assert "settlement" in pv_text
