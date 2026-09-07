from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from stepinbel.config import (
    BelgianDeliveryPeriod,
    HistoricalQuantileCapacityBid,
    MFRRCase,
    SimulationConfig,
)
from stepinbel.data import load_market_data, open_published_bundle
from stepinbel.optimizer import solve_case

PARITY_ABS_EUR = 0.05
MFRR_ID = "mfrr_balanced_2025_delivery_no_pv"


@pytest.fixture(scope="session")
def mfrr_parity_result(data_root: Path):
    bundle = open_published_bundle(data_root)
    config = SimulationConfig(
        period=BelgianDeliveryPeriod(date(2025, 1, 1), date(2025, 12, 31)),
        market_case=MFRRCase(
            activation_profile="balanced",
            capacity_bid=HistoricalQuantileCapacityBid(0.50),
            capacity_coverage_hours=4.0,
        ),
    )
    slice_ = load_market_data(bundle, config)
    return solve_case(slice_)


def test_full_year_mfrr_balanced_total_parity(
    mfrr_parity_result, reference_root: Path
) -> None:
    index = json.loads((reference_root / "index.json").read_text(encoding="utf-8"))
    case = next(item for item in index["cases"] if item["case_id"] == MFRR_ID)
    meta = json.loads((reference_root / case["filename"]).read_text(encoding="utf-8"))
    expected_total = case["metrics"]["total_eur"]
    expected_net = case["metrics"]["energy_net_eur"]
    expected_cap = case["metrics"]["capacity_eur"]
    assert expected_total == meta["summary"]["total_revenue_eur"]
    result = mfrr_parity_result
    observed_total = result.summary.total_site_revenue_eur
    observed_net = result.summary.market_energy_net_eur
    observed_cap = result.summary.capacity_revenue_eur
    print(
        "mFRR parity",
        "expected_total",
        expected_total,
        "observed_total",
        observed_total,
        "delta_total",
        observed_total - expected_total,
        "expected_energy_net",
        expected_net,
        "observed_energy_net",
        observed_net,
        "delta_energy_net",
        observed_net - expected_net,
        "expected_capacity",
        expected_cap,
        "observed_capacity",
        observed_cap,
        "delta_capacity",
        observed_cap - expected_cap,
        "commitments",
        result.capacity_results.num_rows,
        "num_col",
        result.solver.num_col,
        "num_row",
        result.solver.num_row,
        "num_nz",
        result.solver.num_nz,
        "num_integer",
        result.solver.num_integer,
        "num_binary",
        result.solver.num_binary,
        "build_s",
        result.solver.build_s,
        "solve_s",
        result.solver.solve_s,
        "end_to_end_s",
        result.solver.end_to_end_s,
    )
    assert result.period.window.interval_count == 35040
    assert abs(observed_total - expected_total) <= PARITY_ABS_EUR
    assert result.capacity_results.num_rows == 1092
    assert set(result.capacity_results.column("direction").to_pylist()) == {"up"}
    assert result.summary.pv_revenue_eur == pytest.approx(0.0, abs=1e-5)
    assert observed_total == pytest.approx(observed_net + observed_cap, abs=1e-5)
    assert result.summary.reservoir_final_mwh == pytest.approx(
        0.5 * result.summary.e_max_mwh, abs=1e-6
    )
    assert result.feasibility.ok
    assert result.solver.solver_name == "HiGHS"
    assert result.solver.num_integer == 0
    assert result.solver.num_binary == 0
    assert "gurobipy" not in __import__("sys").modules
