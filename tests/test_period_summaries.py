from __future__ import annotations

from datetime import datetime, timezone

from stepinbel.reporting import build_period_summaries
from stepinbel.reporting.periods import simultaneous_interval_energy_net_eur
from tests.workflow_helpers import capacity_table, da_config, dispatch_table, fake_result, utc


def test_utc_timestamps_group_into_brussels_periods() -> None:
    stamps = [
        utc(2024, 12, 31, 22, 45),  # 23:45 CET 2024-12
        utc(2024, 12, 31, 23, 0),  # 00:00 CET 2025-01
        utc(2025, 3, 30, 0, 45),  # 01:45 CET before DST
        utc(2025, 10, 26, 1, 0),  # 02:00 CET after fall-back
    ]
    dispatch = dispatch_table(
        stamps,
        sell=[40.0, 40.0, 40.0, 40.0],
        buy=[10.0, 10.0, 10.0, 10.0],
        pump=[1.0, 0.0, 1.0, 0.0],
        pump_grid=[1.0, 0.0, 1.0, 0.0],
        turbine=[0.0, 1.0, 1.0, 0.5],
        energy_net=[-2.5, 10.0, 7.5, 5.0],
        pv_rev=[0.0, 1.0, 0.0, 0.0],
    )
    capacity = capacity_table(
        [
            {
                "identifier": "dec",
                "direction": "up",
                "start_index": 0,
                "end_index": 1,
                "price_eur_mw_h": 1.0,
                "cap_max_mw": 1.0,
                "block_hours": 0.25,
                "coverage_hours": 0.25,
                "committed_mw": 1.0,
                "capacity_revenue_eur": 5.0,
            },
            {
                "identifier": "jan",
                "direction": "up",
                "start_index": 1,
                "end_index": 2,
                "price_eur_mw_h": 1.0,
                "cap_max_mw": 1.0,
                "block_hours": 0.25,
                "coverage_hours": 0.25,
                "committed_mw": 1.0,
                "capacity_revenue_eur": 11.0,
            },
        ]
    )
    result = fake_result(da_config(), dispatch, capacity)
    monthly, yearly = build_period_summaries(result)
    months = monthly.column("period").to_pylist()
    assert months == ["2024-12", "2025-01", "2025-03", "2025-10"]
    years = yearly.column("period").to_pylist()
    assert years == [2024, 2025]

    month_cap = dict(zip(months, monthly.column("capacity_revenue_eur").to_pylist(), strict=True))
    assert month_cap["2024-12"] == 5.0
    assert month_cap["2025-01"] == 11.0
    assert month_cap["2025-03"] == 0.0
    year_cap = dict(zip(years, yearly.column("capacity_revenue_eur").to_pylist(), strict=True))
    assert year_cap[2024] == 5.0
    assert year_cap[2025] == 11.0


def test_formulas_and_totals_reconcile_without_annualization() -> None:
    stamps = [utc(2025, 1, 1, 0, 0), utc(2025, 2, 1, 0, 0)]
    dispatch = dispatch_table(
        stamps,
        sell=[20.0, 40.0],
        buy=[8.0, 8.0],
        pump=[1.0, 0.0],
        pump_grid=[1.0, 0.0],
        turbine=[0.0, 2.0],
        energy_net=[-2.0, 20.0],
        pv_rev=[1.5, 0.5],
    )
    capacity = capacity_table(
        [
            {
                "identifier": "jan",
                "direction": "up",
                "start_index": 0,
                "end_index": 1,
                "price_eur_mw_h": 2.0,
                "cap_max_mw": 1.0,
                "block_hours": 1.0,
                "coverage_hours": 1.0,
                "committed_mw": 1.0,
                "capacity_revenue_eur": 4.0,
            }
        ]
    )
    result = fake_result(da_config(), dispatch, capacity)
    monthly, yearly = build_period_summaries(result)
    assert monthly.num_rows == 2
    assert yearly.num_rows == 1
    e_max = result.summary.e_max_mwh
    for table in (monthly, yearly):
        for i in range(table.num_rows):
            row = {name: table.column(name)[i].as_py() for name in table.column_names}
            assert row["total_site_revenue_eur"] == (
                row["market_energy_net_eur"]
                + row["capacity_revenue_eur"]
                + row["pv_revenue_eur"]
            )
            assert row["full_cycles"] == row["turbined_mwh"] / e_max
            assert row["duration_hours"] == row["interval_count"] * 0.25

    jan = {name: monthly.column(name)[0].as_py() for name in monthly.column_names}
    feb = {name: monthly.column(name)[1].as_py() for name in monthly.column_names}
    assert jan["period"] == "2025-01"
    assert feb["period"] == "2025-02"
    assert jan["capacity_revenue_eur"] == 4.0
    assert feb["capacity_revenue_eur"] == 0.0
    assert jan["interval_count"] == 1
    assert feb["interval_count"] == 1
    # Partial months stay raw; nothing is scaled to a full year.
    assert jan["duration_hours"] == 0.25
    assert yearly.column("duration_hours")[0].as_py() == 0.5

    for name in (
        "energy_gross_eur",
        "grid_charging_cost_eur",
        "market_energy_net_eur",
        "capacity_revenue_eur",
        "pv_revenue_eur",
        "total_site_revenue_eur",
        "pumped_mwh",
        "turbined_mwh",
        "pv_revenue_eur",
    ):
        monthly_sum = sum(monthly.column(name).to_pylist())
        yearly_sum = sum(yearly.column(name).to_pylist())
        expected = getattr(result.summary, name)
        assert monthly_sum == expected
        assert yearly_sum == expected


def test_simultaneous_diagnostic_is_interval_energy_net() -> None:
    stamps = [utc(2025, 6, 1, 10, 0), utc(2025, 6, 1, 10, 15)]
    dispatch = dispatch_table(
        stamps,
        pump=[1.0, 0.0],
        pump_grid=[1.0, 0.0],
        turbine=[1.0, 1.0],
        energy_net=[3.25, 8.0],
    )
    result = fake_result(da_config(), dispatch)
    diagnostic = simultaneous_interval_energy_net_eur(result)
    assert diagnostic == 3.25
    monthly, _ = build_period_summaries(result)
    assert monthly.column("simultaneous_interval_count")[0].as_py() == 1
    assert monthly.column("simultaneous_interval_energy_net_eur")[0].as_py() == 3.25
    assert monthly.column("simultaneous_overlap_mwh")[0].as_py() == 0.25
