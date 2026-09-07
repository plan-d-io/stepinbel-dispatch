from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stepinbel.config import AssetConfig
from stepinbel.optimizer import SolverError, SolverOptions
from stepinbel.optimizer.highs import import_highspy
from tests.da_solve_helpers import solve_arrays


def test_hand_computable_arbitrage_without_epsilon() -> None:
    result = solve_arrays(
        sell=np.array([0.0, 100.0]),
        buy=np.array([0.0, 1e6]),
        sell_ub=np.array([0.0, 1.0]),
        buy_ub=np.array([1.0, 0.0]),
        asset=AssetConfig(
            storage_hours=4.0,
            storage_hours_basis="stored_energy",
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
    )
    pump = result.dispatch.column("p_pump_mw").to_pylist()
    turb = result.dispatch.column("p_turbine_mw").to_pylist()
    energy_end = result.dispatch.column("reservoir_end_mwh").to_pylist()
    assert pump[0] == pytest.approx(1.0, abs=1e-9)
    assert turb[1] == pytest.approx(0.756, abs=1e-9)
    assert result.summary.total_site_revenue_eur == pytest.approx(18.9, abs=1e-9)
    assert energy_end[0] == pytest.approx(0.21, abs=1e-9)
    assert result.summary.reservoir_final_mwh == pytest.approx(0.0, abs=1e-9)


def test_asymmetric_machines_conserve_energy() -> None:
    result = solve_arrays(
        sell=np.array([0.0, 80.0]),
        buy=np.array([0.0, 1e6]),
        sell_ub=np.array([0.0, 2.0]),
        buy_ub=np.array([1.0, 0.0]),
        asset=AssetConfig(
            power_pump_mw=1.0,
            power_turbine_mw=2.0,
            storage_hours=4.0,
            storage_hours_basis="stored_energy",
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
    )
    assert result.feasibility.ok
    assert result.summary.reservoir_final_mwh == pytest.approx(0.0, abs=1e-6)


def test_storage_bases_and_direct_pond() -> None:
    rated = solve_arrays(
        sell=np.array([10.0, 80.0]),
        asset=AssetConfig(storage_hours=4.0, storage_hours_basis="discharge_at_rated"),
    )
    stored = solve_arrays(
        sell=np.array([10.0, 80.0]),
        asset=AssetConfig(storage_hours=4.0, storage_hours_basis="stored_energy"),
    )
    pond = solve_arrays(
        sell=np.array([10.0, 80.0]),
        asset=AssetConfig(storage_hours=None, pond_energy_mwh=10.0),
    )
    assert rated.summary.e_max_mwh == pytest.approx(4.0 / 0.90)
    assert stored.summary.e_max_mwh == pytest.approx(4.0)
    assert pond.summary.e_max_mwh == pytest.approx(10.0)


def test_enforced_and_free_terminal_state() -> None:
    prices = np.array([0.0, 100.0, 0.0, 100.0])
    enforced = solve_arrays(
        sell=prices,
        asset=AssetConfig(
            storage_hours=4.0,
            storage_hours_basis="stored_energy",
            soc_initial_frac=0.5,
            soc_terminal_frac=0.5,
            enforce_terminal_soc=True,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
    )
    free = solve_arrays(
        sell=prices,
        asset=AssetConfig(
            storage_hours=4.0,
            storage_hours_basis="stored_energy",
            soc_initial_frac=0.5,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
    )
    assert enforced.summary.reservoir_final_mwh == pytest.approx(
        0.5 * enforced.summary.e_max_mwh, abs=1e-7
    )
    assert free.summary.total_site_revenue_eur >= enforced.summary.total_site_revenue_eur - 1e-8


def test_default_and_zero_epsilon_variable_counts() -> None:
    prices = np.array([10.0, 80.0, 10.0, 80.0])
    defaulted = solve_arrays(sell=prices)
    zero = solve_arrays(
        sell=prices,
        asset=AssetConfig(pump_ramp_power_frac=0.0, turbine_ramp_power_frac=0.0),
    )
    assert defaulted.summary.n_pump_ramp_up_vars == 4
    assert defaulted.summary.n_turbine_ramp_up_vars == 0
    assert zero.summary.n_pump_ramp_up_vars == 0
    assert zero.summary.n_turbine_ramp_up_vars == 0


def test_slow_pump_rate_constraint_binds() -> None:
    result = solve_arrays(
        sell=np.array([0.0, 100.0]),
        buy=np.array([0.0, 1e6]),
        sell_ub=np.array([0.0, 1.0]),
        buy_ub=np.array([1.0, 0.0]),
        asset=AssetConfig(
            pump_ramp_up_min=60.0,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            storage_hours_basis="stored_energy",
        ),
    )
    pump0 = result.dispatch.column("p_pump_mw").to_pylist()[0]
    assert pump0 == pytest.approx(0.25, abs=1e-6)


def test_infeasible_terminal_target_fails() -> None:
    with pytest.raises(SolverError):
        solve_arrays(
            sell=np.array([100.0]),
            sell_ub=np.array([0.0]),
            buy_ub=np.array([0.0]),
            asset=AssetConfig(
                soc_initial_frac=0.0,
                soc_terminal_frac=1.0,
                enforce_terminal_soc=True,
            ),
        )


def test_highs_metadata_is_optimal_continuous_lp() -> None:
    result = solve_arrays(sell=np.array([10.0, 80.0]))
    assert result.solver.solver_name == "HiGHS"
    assert result.solver.status == "optimal"
    assert result.solver.continuous_lp is True
    assert result.solver.num_integer == 0
    assert result.solver.num_binary == 0
    assert result.solver.options["random_seed"] == 0
    assert result.solver.options["solver"] == "choose"
    assert result.solver.options["presolve"] == "on"


def test_missing_highspy_fails_clearly(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "highspy", None)
    with pytest.raises(SolverError, match="highspy"):
        import_highspy()


def test_quiet_solve_creates_no_solver_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    solve_arrays(sell=np.array([10.0, 80.0]), options=SolverOptions(detailed_output=False))
    leftover = list(tmp_path.rglob("*"))
    names = [path.name.lower() for path in leftover if path.is_file()]
    assert not any("highs" in name or name.endswith(".log") for name in names)
