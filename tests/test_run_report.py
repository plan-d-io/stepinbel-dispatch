from __future__ import annotations

import re

from stepinbel.config import SiteConfig
from stepinbel.reporting import render_run_report
from stepinbel.reporting.periods import simultaneous_interval_energy_net_eur
from tests.workflow_helpers import (
    build_request,
    capacity_table,
    da_config,
    dispatch_table,
    fake_result,
    mfrr_config,
    utc,
)

REQUIRED_STATEMENTS = (
    "This is a historical perfect-foresight simulation, not a forecast or operating instruction.",
    "This is a dedicated-market result and must not be added to other market runs.",
    "Total site revenue equals market energy net plus capacity revenue plus PV export revenue.",
    "Self-consumed PV has no separate revenue line; it lowers grid charging cost.",
    "Simultaneous-interval revenue is diagnostic and is not incremental value attributable to simultaneous operation.",
)


def _report(request, result):
    return render_run_report(
        request,
        result,
        simultaneous_interval_energy_net_eur=simultaneous_interval_energy_net_eur(result),
    )


def test_deterministic_rendering_and_required_statements(data_root, tmp_path) -> None:
    request = build_request(data_root, tmp_path / "out", da_config())
    result = fake_result(
        request.config,
        dispatch_table([utc(2025, 1, 15, 0, 0)], energy_net=[12.5]),
    )
    first = _report(request, result)
    second = _report(request, result)
    assert first == second
    for statement in REQUIRED_STATEMENTS:
        assert statement in first
    assert "Run ID: test-run-001" in first
    assert "Status: completed" in first
    assert "Dedicated market: da" in first
    assert "Belgian delivery" not in first or "UTC" in first
    assert "Asset and site" in first
    assert "Revenue" in first
    assert "Operations" in first
    assert "Solver and feasibility" in first
    assert "Provenance" in first
    lowered = first.lower()
    assert "fcr" not in lowered
    assert " god" not in lowered and not lowered.startswith("god")
    assert "active" not in lowered


def test_capacity_section_only_for_balancing(data_root, tmp_path) -> None:
    da_request = build_request(data_root, tmp_path / "da", da_config(), run_id="da-report")
    da_result = fake_result(da_request.config, dispatch_table([utc(2025, 1, 15, 0, 0)]))
    da_text = _report(da_request, da_result)
    assert "Capacity results" not in da_text

    mfrr_request = build_request(data_root, tmp_path / "mfrr", mfrr_config(), run_id="mfrr-report")
    cap = capacity_table(
        [
            {
                "identifier": "b1",
                "direction": "up",
                "start_index": 0,
                "end_index": 1,
                "price_eur_mw_h": 1.0,
                "cap_max_mw": 1.0,
                "block_hours": 4.0,
                "coverage_hours": 4.0,
                "committed_mw": 1.0,
                "capacity_revenue_eur": 12.0,
            }
        ]
    )
    mfrr_result = fake_result(
        mfrr_request.config,
        dispatch_table([utc(2025, 1, 15, 0, 0)], energy_net=[1.0]),
        cap,
    )
    mfrr_text = _report(mfrr_request, mfrr_result)
    assert "Capacity results" in mfrr_text
    assert "Capacity rows: 1" in mfrr_text


def test_pv_section_is_conditional(data_root, tmp_path) -> None:
    without = build_request(data_root, tmp_path / "no-pv", da_config(), run_id="no-pv")
    without_text = _report(
        without,
        fake_result(without.config, dispatch_table([utc(2025, 1, 15, 0, 0)])),
    )
    assert "Photovoltaics" not in without_text

    with_pv = build_request(
        data_root,
        tmp_path / "pv",
        da_config(site=SiteConfig(pv_ac_kw=80.0, pv_region="Belgium")),
        run_id="with-pv",
    )
    pv_text = _report(
        with_pv,
        fake_result(with_pv.config, dispatch_table([utc(2025, 1, 15, 0, 0)])),
    )
    assert "Photovoltaics" in pv_text
    assert "Self-consumed PV has no separate revenue line" in pv_text


def test_zero_and_nonzero_simultaneous_and_total(data_root, tmp_path) -> None:
    request = build_request(data_root, tmp_path / "sim", da_config(), run_id="sim")
    zero = fake_result(
        request.config,
        dispatch_table([utc(2025, 1, 15, 0, 0)], pump=[1.0], turbine=[0.0], energy_net=[4.0]),
    )
    zero_text = _report(request, zero)
    assert "Simultaneous intervals: 0" in zero_text
    assert "Simultaneous-interval energy-net revenue: 0.00 EUR" in zero_text

    both = fake_result(
        request.config,
        dispatch_table(
            [utc(2025, 1, 15, 0, 0)],
            pump=[1.0],
            turbine=[1.0],
            energy_net=[9.25],
        ),
    )
    both_text = _report(request, both)
    assert "Simultaneous intervals: 1" in both_text
    assert "9.25" in both_text
    match = re.search(r"Total site revenue: ([0-9.,]+) EUR", both_text)
    assert match is not None
    displayed = float(match.group(1).replace(",", ""))
    assert displayed == round(both.summary.total_site_revenue_eur, 2)
