from __future__ import annotations

import numpy as np
import pytest

from stepinbel.config import AssetConfig
from stepinbel.optimizer import ModelError
from stepinbel.optimizer.model import build_sparse_lp, prepare_physical
from stepinbel.optimizer.types import CAPACITY_RESULT_SCHEMA, CapacityCommitment
from tests.da_solve_helpers import solve_arrays


def test_capacity_revenue_uses_actual_block_hours() -> None:
    for hours in (3.0, 4.0, 5.0):
        result = solve_arrays(
            sell=np.zeros(4),
            buy=np.zeros(4),
            sell_ub=np.zeros(4),
            buy_ub=np.zeros(4),
            asset=AssetConfig(
                soc_initial_frac=1.0,
                enforce_terminal_soc=False,
                pump_ramp_power_frac=0.0,
                turbine_ramp_power_frac=0.0,
            ),
            commitments=[
                CapacityCommitment(
                    identifier=f"up-{hours}",
                    direction="up",
                    start_index=0,
                    end_index=4,
                    price_eur_mw_h=10.0,
                    cap_max_mw=1.0,
                    block_hours=hours,
                    coverage_hours=0.25,
                )
            ],
        )
        table = result.capacity_results
        assert table.num_rows == 1
        assert table.column("committed_mw").to_pylist()[0] == pytest.approx(1.0, abs=1e-6)
        assert table.column("capacity_revenue_eur").to_pylist()[0] == pytest.approx(
            10.0 * hours, abs=1e-6
        )
        assert result.summary.capacity_revenue_eur == pytest.approx(10.0 * hours, abs=1e-6)


def test_upward_power_and_reservoir_coverage_bind() -> None:
    result = solve_arrays(
        sell=np.array([100.0, 100.0, 100.0, 100.0]),
        buy=np.array([1e6, 1e6, 1e6, 1e6]),
        asset=AssetConfig(
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            storage_hours_basis="stored_energy",
            storage_hours=1.0,
        ),
        commitments=[
            CapacityCommitment(
                identifier="up",
                direction="up",
                start_index=0,
                end_index=4,
                price_eur_mw_h=0.0,
                cap_max_mw=1.0,
                block_hours=1.0,
                coverage_hours=4.0,
            )
        ],
    )
    turb = result.dispatch.column("p_turbine_mw").to_numpy()
    committed = result.capacity_results.column("committed_mw").to_pylist()[0]
    assert committed == pytest.approx(0.0, abs=1e-6)
    assert float(np.max(turb)) == pytest.approx(0.0, abs=1e-6)


def test_positive_upward_commitment_binds_turbine_and_reservoir_coverage() -> None:
    result = solve_arrays(
        sell=np.array([100.0, 100.0, 100.0, 100.0]),
        buy=np.array([1e6, 1e6, 1e6, 1e6]),
        asset=AssetConfig(
            soc_initial_frac=0.5,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            storage_hours_basis="stored_energy",
            storage_hours=4.0,
        ),
        commitments=[
            CapacityCommitment(
                identifier="up",
                direction="up",
                start_index=0,
                end_index=4,
                price_eur_mw_h=0.0,
                cap_max_mw=1.0,
                block_hours=1.0,
                coverage_hours=4.0,
            )
        ],
    )
    turb = result.dispatch.column("p_turbine_mw").to_numpy()
    energy0 = result.dispatch.column("reservoir_start_mwh").to_pylist()[0]
    committed = result.capacity_results.column("committed_mw").to_pylist()[0]
    eta_turbine = 0.90
    coverage_limit = energy0 * eta_turbine / 4.0
    assert committed == pytest.approx(coverage_limit, abs=1e-6)
    assert committed == pytest.approx(0.45, abs=1e-6)
    assert float(np.max(turb)) == pytest.approx(committed, abs=1e-6)
    assert float(np.max(turb)) < 1.0 - 1e-6


def test_downward_headroom_binds_and_pump_is_unconstrained_by_capacity() -> None:
    result = solve_arrays(
        sell=np.array([0.0, 0.0, 100.0, 100.0]),
        buy=np.array([0.0, 0.0, 1e6, 1e6]),
        asset=AssetConfig(
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            storage_hours_basis="stored_energy",
            storage_hours=4.0,
        ),
        commitments=[
            CapacityCommitment(
                identifier="down",
                direction="down",
                start_index=0,
                end_index=2,
                price_eur_mw_h=50.0,
                cap_max_mw=1.0,
                block_hours=0.5,
                coverage_hours=4.0,
            )
        ],
    )
    pump = result.dispatch.column("p_pump_mw").to_numpy()
    committed = result.capacity_results.column("committed_mw").to_pylist()[0]
    # Empty pond: downward headroom is E_max, so c_b can be 1 MW. Pumping is
    # not limited by c_b; the first cheap interval can still charge at 1 MW.
    assert committed == pytest.approx(1.0, abs=1e-5)
    assert float(pump[0]) == pytest.approx(1.0, abs=1e-5)


def test_built_lp_has_no_downward_pump_capacity_rows() -> None:
    from stepinbel.config import DayAheadCase, SimulationConfig, SiteConfig, UtcPeriod
    from stepinbel.markets.base import MarketDispatchInputs
    from tests.da_solve_helpers import resolved_da_period

    period, _resolved = resolved_da_period(4)
    config = SimulationConfig(
        period=period,
        market_case=DayAheadCase(),
        asset=AssetConfig(pump_ramp_power_frac=0.0, turbine_ramp_power_frac=0.0),
        site=SiteConfig(),
    )
    market = MarketDispatchInputs(
        sell_price_eur_mwh=np.zeros(4),
        buy_price_eur_mwh=np.zeros(4),
        sell_upper_mw=np.ones(4),
        buy_upper_mw=np.ones(4),
        day_ahead_price_eur_mwh=np.zeros(4),
        capacity_commitments=(
            CapacityCommitment(
                identifier="down",
                direction="down",
                start_index=0,
                end_index=4,
                price_eur_mw_h=1.0,
                cap_max_mw=1.0,
                block_hours=1.0,
                coverage_hours=1.0,
            ),
        ),
    )
    lp = build_sparse_lp(prepare_physical(config, market, None))
    assert lp.has_downward_pump_capacity_rows is False


def test_invalid_direction_and_span_fail_before_solve() -> None:
    with pytest.raises(ModelError, match="direction"):
        CapacityCommitment(
            identifier="x",
            direction="sideways",  # type: ignore[arg-type]
            start_index=0,
            end_index=1,
            price_eur_mw_h=1.0,
            cap_max_mw=1.0,
            block_hours=1.0,
            coverage_hours=1.0,
        )
    with pytest.raises(ModelError, match="span"):
        CapacityCommitment(
            identifier="x",
            direction="up",
            start_index=2,
            end_index=2,
            price_eur_mw_h=1.0,
            cap_max_mw=1.0,
            block_hours=1.0,
            coverage_hours=1.0,
        )
    with pytest.raises(ModelError, match="outside"):
        solve_arrays(
            sell=np.array([1.0, 2.0]),
            commitments=[
                CapacityCommitment(
                    identifier="x",
                    direction="up",
                    start_index=0,
                    end_index=8,
                    price_eur_mw_h=1.0,
                    cap_max_mw=1.0,
                    block_hours=1.0,
                    coverage_hours=1.0,
                )
            ],
        )


def test_da_capacity_table_is_empty_with_full_schema() -> None:
    result = solve_arrays(sell=np.array([10.0, 80.0]))
    assert result.capacity_results.num_rows == 0
    assert result.capacity_results.schema.equals(CAPACITY_RESULT_SCHEMA)
