from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from stepinbel.config import (
    AFRRCase,
    BelgianDeliveryPeriod,
    HistoricalQuantileCapacityBid,
    SimulationConfig,
)
from stepinbel.data import load_market_data, open_published_bundle
from stepinbel.markets.afrr import build_afrr_inputs
from stepinbel.markets.bidding import (
    ENERGY_PROFILE_QUANTILE,
    collapse_capacity_blocks,
    yearly_capacity_bids,
    yearly_energy_bids,
)
from stepinbel.optimizer import solve_case

PARITY_ABS_EUR = 0.05
AFRR_ID = "afrr_balanced_2025_delivery_no_pv"


@pytest.fixture(scope="session")
def afrr_parity_slice(data_root: Path):
    bundle = open_published_bundle(data_root)
    config = SimulationConfig(
        period=BelgianDeliveryPeriod(date(2025, 1, 1), date(2025, 12, 31)),
        market_case=AFRRCase(
            activation_profile="balanced",
            capacity_bid=HistoricalQuantileCapacityBid(0.50),
            capacity_coverage_hours=4.0,
            up_capacity_fraction=1.0,
        ),
    )
    return load_market_data(bundle, config)


@pytest.fixture(scope="session")
def afrr_parity_inputs(afrr_parity_slice):
    return build_afrr_inputs(afrr_parity_slice)


@pytest.fixture(scope="session")
def afrr_parity_result(afrr_parity_slice):
    return solve_case(afrr_parity_slice)


def test_full_year_afrr_balanced_total_parity(
    afrr_parity_slice,
    afrr_parity_inputs,
    afrr_parity_result,
    reference_root: Path,
) -> None:
    index = json.loads((reference_root / "index.json").read_text(encoding="utf-8"))
    case = next(item for item in index["cases"] if item["case_id"] == AFRR_ID)
    meta = json.loads((reference_root / case["filename"]).read_text(encoding="utf-8"))
    expected_total = case["metrics"]["total_eur"]
    expected_net = case["metrics"]["energy_net_eur"]
    expected_cap = case["metrics"]["capacity_eur"]
    assert expected_total == meta["summary"]["total_revenue_eur"]
    assert expected_total == pytest.approx(233306.71125765995)
    result = afrr_parity_result
    market = afrr_parity_inputs
    observed_total = result.summary.total_site_revenue_eur
    observed_net = result.summary.market_energy_net_eur
    observed_cap = result.summary.capacity_revenue_eur

    window = afrr_parity_slice.period.window
    assert window.start_utc == datetime(2024, 12, 31, 23, tzinfo=timezone.utc)
    assert window.end_exclusive_utc == datetime(2025, 12, 31, 23, tzinfo=timezone.utc)
    assert window.interval_count == 35040

    collapsed_up = collapse_capacity_blocks(
        afrr_parity_slice.capacity_blocks, product="afrr", direction="up"
    )
    collapsed_down = collapse_capacity_blocks(
        afrr_parity_slice.capacity_blocks, product="afrr", direction="down"
    )
    assert len(collapsed_up) == 2190
    assert len(collapsed_down) == 2190
    yearly_up = yearly_capacity_bids(
        collapsed_up, afrr_parity_slice.config.market_case.capacity_bid, direction="up"
    )
    yearly_down = yearly_capacity_bids(
        collapsed_down, afrr_parity_slice.config.market_case.capacity_bid, direction="down"
    )
    assert yearly_up[2025] == pytest.approx(16.86)
    assert yearly_down[2025] == pytest.approx(5.515)
    cleared_up = sum(
        1
        for block in collapsed_up
        if np.isfinite(yearly_up.get(block.delivery_date_local.year, float("nan")))
        and yearly_up[block.delivery_date_local.year] >= 0.0
        and yearly_up[block.delivery_date_local.year] <= block.marginal_price_eur_mw_h
    )
    cleared_down = sum(
        1
        for block in collapsed_down
        if np.isfinite(yearly_down.get(block.delivery_date_local.year, float("nan")))
        and yearly_down[block.delivery_date_local.year] >= 0.0
        and yearly_down[block.delivery_date_local.year] <= block.marginal_price_eur_mw_h
    )
    assert cleared_up == 1095
    assert cleared_down == 1095
    assert len(market.capacity_commitments) == 1095
    assert {item.direction for item in market.capacity_commitments} == {"up"}

    timestamps = tuple(afrr_parity_slice.da_prices.column("datetime_utc").to_pylist())
    da = np.array(
        afrr_parity_slice.da_prices.column("da_price_eur_mwh").to_numpy(zero_copy_only=False),
        dtype=np.float64,
        copy=True,
    )
    has_up = np.array(
        [item is True for item in afrr_parity_slice.balancing.column("has_afrr_up").to_pylist()],
        dtype=bool,
    )
    has_down = np.array(
        [item is True for item in afrr_parity_slice.balancing.column("has_afrr_down").to_pylist()],
        dtype=bool,
    )
    cbmp_up = np.array(
        afrr_parity_slice.balancing.column("cbmp_afrr_up").to_numpy(zero_copy_only=False),
        dtype=np.float64,
        copy=True,
    )
    energy = yearly_energy_bids(
        timestamps,
        da,
        cbmp_up,
        has_up,
        quantile=ENERGY_PROFILE_QUANTILE["balanced"],
        eta_pump=float(afrr_parity_slice.config.asset.eta_pump),
        eta_turbine=float(afrr_parity_slice.config.asset.eta_turbine),
    )
    assert energy[2024] == pytest.approx(-71.444)
    assert energy[2025] == pytest.approx(77.63)
    assert int(has_up.sum()) == 29529
    assert int(has_down.sum()) == 31778
    assert int(np.count_nonzero(market.sell_upper_mw > 0)) == 8427
    assert int(np.count_nonzero(market.buy_price_eur_mwh != market.day_ahead_price_eur_mwh)) == 19618

    print(
        "aFRR parity",
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
    assert result.capacity_results.num_rows == 1095
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
