from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stepinbel.config import (
    BelgianDeliveryPeriod,
    DayAheadCase,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.data import load_market_data, open_published_bundle
from stepinbel.optimizer import solve_case

PARITY_ABS_EUR = 0.05
NO_PV_ID = "da_balanced_2025_utc_no_pv"
PV_ID = "da_balanced_2025_delivery_pv500"


@pytest.fixture(scope="session")
def published_bundle(data_root: Path):
    return open_published_bundle(data_root)


@pytest.fixture(scope="session")
def parity_index(reference_root: Path) -> dict:
    return json.loads((reference_root / "index.json").read_text(encoding="utf-8"))


def _case(index: dict, case_id: str) -> dict:
    for item in index["cases"]:
        if item["case_id"] == case_id:
            return item
    raise AssertionError(case_id)


@pytest.fixture(scope="session")
def no_pv_result(published_bundle):
    config = SimulationConfig(
        period=UtcPeriod(
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        market_case=DayAheadCase(),
    )
    slice_ = load_market_data(published_bundle, config)
    result = solve_case(slice_)
    return result


@pytest.fixture(scope="session")
def pv_result(published_bundle):
    from datetime import date

    config = SimulationConfig(
        period=BelgianDeliveryPeriod(date(2025, 1, 1), date(2025, 12, 31)),
        market_case=DayAheadCase(),
        site=SiteConfig(pv_ac_kw=500.0, pv_region="Belgium"),
    )
    slice_ = load_market_data(published_bundle, config)
    return solve_case(slice_)


def test_full_year_da_no_pv_total_and_energy_net_parity(
    no_pv_result, parity_index, reference_root: Path
) -> None:
    case = _case(parity_index, NO_PV_ID)
    expected_total = case["metrics"]["total_eur"]
    expected_net = case["metrics"]["energy_net_eur"]
    meta = json.loads((reference_root / case["filename"]).read_text(encoding="utf-8"))
    assert expected_total == meta["summary"]["total_revenue_eur"]
    result = no_pv_result
    assert result.period.window.interval_count == 35040
    assert abs(result.summary.total_site_revenue_eur - expected_total) <= PARITY_ABS_EUR
    assert abs(result.summary.market_energy_net_eur - expected_net) <= PARITY_ABS_EUR
    assert result.summary.capacity_revenue_eur == pytest.approx(0.0, abs=1e-5)
    assert result.summary.pv_revenue_eur == pytest.approx(0.0, abs=1e-5)
    assert result.feasibility.ok
    assert result.summary.reservoir_final_mwh == pytest.approx(
        0.5 * result.summary.e_max_mwh, abs=1e-6
    )
    assert result.solver.solver_name == "HiGHS"
    assert "gurobipy" not in __import__("sys").modules


def test_full_year_da_pv_total_parity(pv_result, parity_index) -> None:
    case = _case(parity_index, PV_ID)
    expected_total = case["metrics"]["total_eur"]
    result = pv_result
    assert result.period.window.interval_count == 35040
    assert abs(result.summary.total_site_revenue_eur - expected_total) <= PARITY_ABS_EUR
    assert result.summary.total_site_revenue_eur == pytest.approx(
        result.summary.market_energy_net_eur + result.summary.pv_revenue_eur,
        abs=1e-5,
    )
    assert result.summary.capacity_revenue_eur == pytest.approx(0.0, abs=1e-5)
    split = (
        result.summary.pv_self_consumed_mwh
        + result.summary.pv_exported_mwh
        + result.summary.pv_curtailed_mwh
    )
    assert split == pytest.approx(result.summary.pv_available_mwh, abs=1e-6)
    assert result.feasibility.ok
    assert result.summary.reservoir_final_mwh == pytest.approx(
        0.5 * result.summary.e_max_mwh, abs=1e-6
    )
    assert result.solver.solver_name == "HiGHS"
    assert "gurobipy" not in __import__("sys").modules
    print(
        "PV component split PHS vs HiGHS:",
        "phs_energy_net",
        case["metrics"]["energy_net_eur"],
        "highs_energy_net",
        result.summary.market_energy_net_eur,
        "phs_pv",
        case["metrics"]["pv_eur"],
        "highs_pv",
        result.summary.pv_revenue_eur,
        "build_s",
        result.solver.build_s,
        "solve_s",
        result.solver.solve_s,
        "end_to_end_s",
        result.solver.end_to_end_s,
    )
