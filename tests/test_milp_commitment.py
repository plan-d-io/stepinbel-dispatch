from __future__ import annotations

import json
import math
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest

from stepinbel.config import (
    AssetConfig,
    ConfigError,
    DayAheadCase,
    MachineCommitmentConfig,
    SimulationConfig,
    SiteConfig,
)
from stepinbel.markets.base import MarketDispatchInputs
from stepinbel.optimizer import ModelError, SolverError, SolverOptions
from stepinbel.optimizer.commitment import resolve_machine_commitment
from stepinbel.optimizer.highs import (
    UnusableSolveError,
    _classify,
    solve_sparse_model,
)
from stepinbel.optimizer.model import build_sparse_model, prepare_physical
from stepinbel.optimizer.types import (
    ACCOUNTING_TOL_EUR,
    POWER_TOL_MW,
    SIMULTANEOUS_TOL_MW,
    TERMINATION_ACCEPTED_WITHIN_GAP,
    TERMINATION_NO_FEASIBLE_SOLUTION,
    TERMINATION_SOLVER_FAILURE,
    TERMINATION_TIME_LIMIT_FEASIBLE,
)
from tests.da_solve_helpers import relabel_time_limit_feasible, resolved_da_period, solve_arrays

FIXED = MachineCommitmentConfig(fixed_speed_pump=True)
TURBINE_MIN = MachineCommitmentConfig(turbine_minimum_output_fraction=0.18)
STRICT = MachineCommitmentConfig(forbid_simultaneous_operation=True)
ALL_ON = MachineCommitmentConfig(
    fixed_speed_pump=True,
    turbine_minimum_output_fraction=0.18,
    forbid_simultaneous_operation=True,
)


def _on_or_off(values: np.ndarray, rated: float, *, atol: float = 1e-6) -> None:
    for value in np.asarray(values, dtype=np.float64):
        assert value == pytest.approx(0.0, abs=atol) or value == pytest.approx(
            rated, abs=atol
        )


def _zero_or_at_least(values: np.ndarray, minimum: float, *, atol: float = 1e-6) -> None:
    for value in np.asarray(values, dtype=np.float64):
        if value > atol:
            assert value + atol >= minimum


def _arrays_match(left, right) -> None:
    np.testing.assert_array_equal(left.col_cost, right.col_cost)
    np.testing.assert_array_equal(left.col_lower, right.col_lower)
    np.testing.assert_array_equal(left.col_upper, right.col_upper)
    np.testing.assert_array_equal(left.row_lower, right.row_lower)
    np.testing.assert_array_equal(left.row_upper, right.row_upper)
    np.testing.assert_array_equal(left.a_start, right.a_start)
    np.testing.assert_array_equal(left.a_index, right.a_index)
    np.testing.assert_array_equal(left.a_value, right.a_value)
    assert left.num_col == right.num_col
    assert left.num_row == right.num_row
    assert left.num_nz == right.num_nz
    assert left.layout.num_col == right.layout.num_col
    assert left.layout.idx_pump == right.layout.idx_pump
    assert left.layout.idx_turb == right.layout.idx_turb
    assert left.layout.idx_e == right.layout.idx_e
    assert left.integrality is None
    assert right.integrality is None
    assert left.resolved_commitment is None
    assert right.resolved_commitment is None


def _prepared_two_interval():
    n = 2
    period, _resolved = resolved_da_period(n)
    config = SimulationConfig(
        period=period,
        market_case=DayAheadCase(),
        asset=AssetConfig(
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            storage_hours_basis="stored_energy",
        ),
    )
    market = MarketDispatchInputs(
        sell_price_eur_mwh=np.array([10.0, 80.0]),
        buy_price_eur_mwh=np.array([10.0, 80.0]),
        sell_upper_mw=np.ones(n),
        buy_upper_mw=np.ones(n),
        day_ahead_price_eur_mwh=np.array([10.0, 80.0]),
    )
    return prepare_physical(config, market, None), config


def test_disabled_commitment_keeps_continuous_lp_matrix() -> None:
    prepared, config = _prepared_two_interval()
    plain = build_sparse_model(prepared)
    disabled = build_sparse_model(prepared, resolve_machine_commitment(config))
    omitted = build_sparse_model(prepared, None)
    _arrays_match(plain, disabled)
    _arrays_match(plain, omitted)
    assert plain.layout.idx_u_pump is None
    assert plain.layout.idx_u_turb is None
    assert plain.num_col == disabled.num_col
    assert resolve_machine_commitment(config) is None


def _assert_json_safe(payload: object) -> None:
    json.dumps(payload, allow_nan=False)


def test_ordinary_lp_solve_does_not_acquire_binaries() -> None:
    prices = np.array([10.0, 80.0, 10.0, 80.0])
    plain = solve_arrays(sell=prices)
    disabled = solve_arrays(sell=prices, commitment=MachineCommitmentConfig())
    assert plain.solver.continuous_lp is True
    assert disabled.solver.continuous_lp is True
    assert plain.solver.num_integer == 0
    assert plain.solver.num_binary == 0
    assert disabled.solver.num_integer == 0
    assert disabled.solver.num_binary == 0
    assert plain.solver.num_col == disabled.solver.num_col
    assert plain.solver.num_row == disabled.solver.num_row
    assert plain.solver.num_nz == disabled.solver.num_nz
    assert plain.summary.total_site_revenue_eur == pytest.approx(
        disabled.summary.total_site_revenue_eur, abs=ACCOUNTING_TOL_EUR
    )
    np.testing.assert_allclose(
        plain.dispatch.column("p_pump_mw").to_numpy(),
        disabled.dispatch.column("p_pump_mw").to_numpy(),
        atol=1e-9,
    )
    np.testing.assert_allclose(
        plain.dispatch.column("p_turbine_mw").to_numpy(),
        disabled.dispatch.column("p_turbine_mw").to_numpy(),
        atol=1e-9,
    )
    assert "mip_rel_gap" not in plain.solver.options
    assert "time_limit" not in plain.solver.options
    assert "threads" not in plain.solver.options
    assert "formulation" not in plain.solver.diagnostics
    assert "mip_gap" not in plain.solver.diagnostics
    assert "classification" not in plain.solver.diagnostics
    assert plain.solver.diagnostics.keys() == disabled.solver.diagnostics.keys()
    _assert_json_safe(dict(plain.solver.options))
    _assert_json_safe(dict(plain.solver.diagnostics))
    _assert_json_safe(dict(disabled.solver.options))
    _assert_json_safe(dict(disabled.solver.diagnostics))


def test_fixed_speed_pump_is_off_or_rated() -> None:
    asset = AssetConfig(
        storage_hours=4.0,
        storage_hours_basis="stored_energy",
        soc_initial_frac=0.0,
        enforce_terminal_soc=False,
        pump_ramp_power_frac=0.0,
        turbine_ramp_power_frac=0.0,
    )
    lp = solve_arrays(
        sell=np.array([0.0, 0.0, 120.0]),
        buy=np.array([5.0, 5.0, 1e6]),
        asset=asset,
    )
    milp = solve_arrays(
        sell=np.array([0.0, 0.0, 120.0]),
        buy=np.array([5.0, 5.0, 1e6]),
        asset=asset,
        commitment=FIXED,
    )
    pump = milp.dispatch.column("p_pump_mw").to_numpy()
    _on_or_off(pump, 1.0)
    assert milp.solver.num_binary == 3
    assert milp.solver.num_integer == 3
    assert milp.solver.continuous_lp is False
    assert milp.solver.diagnostics["formulation"] == "milp"
    assert milp.solver.diagnostics["termination"] == TERMINATION_ACCEPTED_WITHIN_GAP
    assert milp.summary.total_site_revenue_eur <= lp.summary.total_site_revenue_eur + ACCOUNTING_TOL_EUR
    assert milp.feasibility.ok
    _assert_json_safe(dict(milp.solver.diagnostics))


def test_turbine_minimum_is_off_or_at_least_floor() -> None:
    asset = AssetConfig(
        storage_hours=4.0,
        storage_hours_basis="stored_energy",
        soc_initial_frac=1.0,
        enforce_terminal_soc=False,
        pump_ramp_power_frac=0.0,
        turbine_ramp_power_frac=0.0,
    )
    sell = np.array([40.0, 12.0, 40.0])
    lp = solve_arrays(sell=sell, asset=asset)
    milp = solve_arrays(sell=sell, asset=asset, commitment=TURBINE_MIN)
    turb = milp.dispatch.column("p_turbine_mw").to_numpy()
    _zero_or_at_least(turb, 0.18)
    assert milp.solver.num_binary == 3
    assert milp.solver.continuous_lp is False
    assert milp.summary.total_site_revenue_eur <= lp.summary.total_site_revenue_eur + ACCOUNTING_TOL_EUR
    assert milp.feasibility.ok


def test_turbine_minimum_resolves_against_configured_rating() -> None:
    asset = AssetConfig(
        power_turbine_mw=2.0,
        storage_hours=4.0,
        storage_hours_basis="stored_energy",
        soc_initial_frac=1.0,
        enforce_terminal_soc=False,
        pump_ramp_power_frac=0.0,
        turbine_ramp_power_frac=0.0,
    )
    milp = solve_arrays(
        sell=np.array([80.0, 12.0, 80.0]),
        asset=asset,
        commitment=MachineCommitmentConfig(turbine_minimum_output_fraction=0.18),
    )
    _zero_or_at_least(milp.dispatch.column("p_turbine_mw").to_numpy(), 0.36)
    assert milp.feasibility.ok


def test_strict_mode_forbids_simultaneous_operation() -> None:
    asset = AssetConfig(
        storage_hours=4.0,
        storage_hours_basis="stored_energy",
        soc_initial_frac=0.5,
        enforce_terminal_soc=False,
        pump_ramp_power_frac=0.0,
        turbine_ramp_power_frac=0.0,
    )
    sell = np.array([80.0, 80.0])
    buy = np.array([-50.0, -50.0])
    lp = solve_arrays(sell=sell, buy=buy, asset=asset)
    milp = solve_arrays(sell=sell, buy=buy, asset=asset, commitment=STRICT)
    pump = milp.dispatch.column("p_pump_mw").to_numpy()
    turb = milp.dispatch.column("p_turbine_mw").to_numpy()
    simultaneous = (pump > SIMULTANEOUS_TOL_MW) & (turb > SIMULTANEOUS_TOL_MW)
    assert int(np.count_nonzero(simultaneous)) == 0
    assert milp.summary.simultaneous_interval_count == 0
    assert milp.solver.num_binary == 4
    assert milp.summary.total_site_revenue_eur <= lp.summary.total_site_revenue_eur + ACCOUNTING_TOL_EUR
    assert milp.feasibility.ok


def test_options_work_independently_and_together() -> None:
    asset = AssetConfig(
        storage_hours=4.0,
        storage_hours_basis="stored_energy",
        soc_initial_frac=0.5,
        enforce_terminal_soc=False,
        pump_ramp_power_frac=0.0,
        turbine_ramp_power_frac=0.0,
    )
    sell = np.array([0.0, 100.0])
    buy = np.array([8.0, 8.0])
    lp = solve_arrays(sell=sell, buy=buy, asset=asset)
    cases = (
        (FIXED, 2, 0),
        (TURBINE_MIN, 0, 2),
        (STRICT, 2, 2),
        (ALL_ON, 2, 2),
    )
    for options, pump_bins, turb_bins in cases:
        result = solve_arrays(sell=sell, buy=buy, asset=asset, commitment=options)
        assert result.solver.num_binary == pump_bins + turb_bins
        assert result.solver.num_integer == pump_bins + turb_bins
        assert result.solver.continuous_lp is False
        assert result.feasibility.ok
        assert (
            result.summary.total_site_revenue_eur
            <= lp.summary.total_site_revenue_eur + ACCOUNTING_TOL_EUR
        )
        if options.fixed_speed_pump:
            _on_or_off(result.dispatch.column("p_pump_mw").to_numpy(), 1.0)
        if options.turbine_minimum_active():
            _zero_or_at_least(result.dispatch.column("p_turbine_mw").to_numpy(), 0.18)
        if options.forbid_simultaneous_operation:
            pump = result.dispatch.column("p_pump_mw").to_numpy()
            turb = result.dispatch.column("p_turbine_mw").to_numpy()
            assert result.summary.simultaneous_interval_count == 0
            assert not np.any((pump > SIMULTANEOUS_TOL_MW) & (turb > SIMULTANEOUS_TOL_MW))


def test_commitment_variables_are_reused() -> None:
    period, _resolved = resolved_da_period(3)
    config = SimulationConfig(
        period=period,
        market_case=DayAheadCase(),
        asset=AssetConfig(
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            enforce_terminal_soc=False,
            storage_hours_basis="stored_energy",
        ),
        machine_commitment=ALL_ON,
    )
    market = MarketDispatchInputs(
        sell_price_eur_mwh=np.array([10.0, 80.0, 10.0]),
        buy_price_eur_mwh=np.array([10.0, 80.0, 10.0]),
        sell_upper_mw=np.ones(3),
        buy_upper_mw=np.ones(3),
        day_ahead_price_eur_mwh=np.array([10.0, 80.0, 10.0]),
    )
    prepared = prepare_physical(config, market, None)
    resolved = resolve_machine_commitment(config)
    model = build_sparse_model(prepared, resolved)
    assert resolved is not None
    assert resolved.use_pump_commitment is True
    assert resolved.use_turbine_commitment is True
    assert resolved.binary_count(3) == 6
    assert model.layout.idx_u_pump is not None
    assert model.layout.idx_u_turb is not None
    assert model.layout.idx_u_turb == model.layout.idx_u_pump + 3
    assert int(np.count_nonzero(model.integrality)) == 6


def test_invalid_minimum_output_settings_fail_clearly() -> None:
    with pytest.raises(ConfigError, match="finite"):
        MachineCommitmentConfig(turbine_minimum_output_fraction=True)  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="finite"):
        MachineCommitmentConfig(turbine_minimum_output_fraction=math.inf)
    with pytest.raises(ConfigError, match=r"\[0, 1\]"):
        MachineCommitmentConfig(turbine_minimum_output_fraction=-0.1)
    with pytest.raises(ConfigError, match=r"\[0, 1\]"):
        MachineCommitmentConfig(turbine_minimum_output_fraction=1.5)


def test_grid_limit_below_fixed_speed_rating_cannot_part_load() -> None:
    result = solve_arrays(
        sell=np.array([0.0, 200.0]),
        buy=np.array([1.0, 1e6]),
        asset=AssetConfig(
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            storage_hours_basis="stored_energy",
        ),
        site=SiteConfig(grid_import_mw=0.4),
        commitment=FIXED,
    )
    pump = result.dispatch.column("p_pump_mw").to_numpy()
    np.testing.assert_allclose(pump, 0.0, atol=POWER_TOL_MW)
    _on_or_off(pump, 1.0)
    assert result.feasibility.ok


def test_pv_allocation_stays_physically_consistent() -> None:
    result = solve_arrays(
        sell=np.array([5.0, 5.0, 100.0, 100.0]),
        buy=np.array([50.0, 50.0, 50.0, 50.0]),
        asset=AssetConfig(
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
        site=SiteConfig(
            pv_ac_kw=1000.0,
            pv_region="Belgium",
            grid_export_mw=2.0,
            grid_import_mw=2.0,
        ),
        pv_load_factor=np.array([1.0, 1.0, 0.0, 0.0]),
        commitment=ALL_ON,
    )
    pump = result.dispatch.column("p_pump_mw").to_numpy()
    grid = result.dispatch.column("p_pump_grid_mw").to_numpy()
    to_pump = result.dispatch.column("pv_to_pump_mw").to_numpy()
    avail = result.dispatch.column("pv_available_mw").to_numpy()
    export = result.dispatch.column("pv_export_mw").to_numpy()
    curtail = result.dispatch.column("pv_curtail_mw").to_numpy()
    np.testing.assert_allclose(pump, grid + to_pump, atol=1e-7)
    np.testing.assert_allclose(avail, export + to_pump + curtail, atol=1e-7)
    _on_or_off(pump, 1.0)
    _zero_or_at_least(result.dispatch.column("p_turbine_mw").to_numpy(), 0.18)
    assert result.summary.simultaneous_interval_count == 0
    assert result.feasibility.ok
    assert result.solver.num_binary == 8


def test_existing_ramp_and_terminal_soc_checks_still_run() -> None:
    result = solve_arrays(
        sell=np.array([8.0, 90.0, 8.0, 90.0]),
        commitment=ALL_ON,
    )
    assert result.feasibility.ok
    assert result.feasibility.max_ramp_residual_mw <= POWER_TOL_MW
    assert result.summary.reservoir_final_mwh == pytest.approx(
        0.5 * result.summary.e_max_mwh, abs=1e-7
    )
    assert result.summary.n_pump_ramp_up_vars == 4
    _on_or_off(result.dispatch.column("p_pump_mw").to_numpy(), 1.0)
    _zero_or_at_least(result.dispatch.column("p_turbine_mw").to_numpy(), 0.18)
    assert result.summary.simultaneous_interval_count == 0


def test_fixed_speed_respects_existing_ramp_limits() -> None:
    result = solve_arrays(
        sell=np.array([0.0, 0.0, 200.0]),
        buy=np.array([1.0, 1.0, 1e6]),
        asset=AssetConfig(
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_up_min=60.0,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            storage_hours_basis="stored_energy",
        ),
        commitment=FIXED,
    )
    pump = result.dispatch.column("p_pump_mw").to_numpy()
    np.testing.assert_allclose(pump, 0.0, atol=POWER_TOL_MW)
    assert result.feasibility.max_ramp_residual_mw <= POWER_TOL_MW
    assert result.feasibility.ok


def test_turbine_startup_respects_minimum_and_ramp() -> None:
    result = solve_arrays(
        sell=np.array([200.0, 200.0, 200.0]),
        asset=AssetConfig(
            soc_initial_frac=1.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_up_min=30.0,
            turbine_ramp_power_frac=0.0,
            storage_hours_basis="stored_energy",
        ),
        commitment=TURBINE_MIN,
    )
    turb = result.dispatch.column("p_turbine_mw").to_numpy()
    _zero_or_at_least(turb, 0.18)
    assert turb[0] <= 0.5 + 1e-6
    assert result.feasibility.max_ramp_residual_mw <= POWER_TOL_MW
    assert result.feasibility.ok


def test_zero_turbine_minimum_does_not_create_binaries() -> None:
    result = solve_arrays(
        sell=np.array([10.0, 80.0]),
        commitment=MachineCommitmentConfig(turbine_minimum_output_fraction=0.0),
    )
    assert result.solver.continuous_lp is True
    assert result.solver.num_binary == 0
    assert result.solver.num_integer == 0


def test_boolean_and_non_finite_inputs_fail() -> None:
    with pytest.raises(ConfigError, match="boolean"):
        MachineCommitmentConfig(fixed_speed_pump=1)  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="boolean"):
        MachineCommitmentConfig(forbid_simultaneous_operation="yes")  # type: ignore[arg-type]
    with pytest.raises(ModelError, match="finite"):
        SolverOptions(mip_rel_gap=math.nan)
    with pytest.raises(ModelError, match="finite"):
        SolverOptions(time_limit_s=math.inf)
    with pytest.raises(ModelError, match="boolean"):
        SolverOptions(detailed_output=1)  # type: ignore[arg-type]
    with pytest.raises(UnusableSolveError) as caught:
        solve_arrays(
            sell=np.array([100.0]),
            sell_ub=np.array([0.0]),
            buy_ub=np.array([0.0]),
            asset=AssetConfig(
                soc_initial_frac=0.0,
                soc_terminal_frac=1.0,
                enforce_terminal_soc=True,
            ),
            commitment=FIXED,
        )
    assert isinstance(caught.value, SolverError)
    assert caught.value.solved.classification == TERMINATION_NO_FEASIBLE_SOLUTION


def test_termination_classification_meanings() -> None:
    assert _classify("optimal", True, False) == "optimal"
    assert _classify("optimal", True, True) == TERMINATION_ACCEPTED_WITHIN_GAP
    assert _classify("time_limit", True, True) == TERMINATION_TIME_LIMIT_FEASIBLE
    assert _classify("time_limit", False, True) == TERMINATION_NO_FEASIBLE_SOLUTION
    assert _classify("infeasible", False, True) == TERMINATION_NO_FEASIBLE_SOLUTION
    assert _classify("unknown", False, True) == TERMINATION_SOLVER_FAILURE
    assert _classify("optimal", False, False) == TERMINATION_SOLVER_FAILURE


def _patch_solve(wrapper):
    return patch("stepinbel.optimizer.solve.solve_sparse_model", wrapper)


def test_injected_accepted_gap_completes() -> None:
    original = solve_sparse_model

    def wrapper(model, options, *, build_s, require_usable=True):
        solved = original(model, options, build_s=build_s, require_usable=require_usable)
        assert solved.classification == TERMINATION_ACCEPTED_WITHIN_GAP
        return solved

    with _patch_solve(wrapper):
        result = solve_arrays(sell=np.array([10.0, 80.0, 10.0, 80.0]), commitment=ALL_ON)
    assert result.feasibility.ok is True
    assert result.solver.diagnostics["termination"] == TERMINATION_ACCEPTED_WITHIN_GAP
    assert result.solver.status == "optimal"


def test_injected_time_limit_with_valid_incumbent_completes() -> None:
    original = solve_sparse_model

    def wrapper(model, options, *, build_s, require_usable=True):
        solved = original(model, options, build_s=build_s, require_usable=False)
        return relabel_time_limit_feasible(solved, achieved_gap=0.02)

    with _patch_solve(wrapper):
        result = solve_arrays(sell=np.array([10.0, 80.0, 10.0, 80.0]), commitment=ALL_ON)
    assert result.feasibility.ok is True
    assert result.solver.status == "time_limit"
    assert result.solver.status_raw == "kTimeLimit"
    assert result.solver.diagnostics["termination"] == TERMINATION_TIME_LIMIT_FEASIBLE
    assert result.solver.diagnostics["achieved_mip_gap"] == pytest.approx(0.02)
    assert result.solver.diagnostics["requested_mip_gap"] == pytest.approx(0.015)


def test_injected_time_limit_without_incumbent_is_unusable() -> None:
    original = solve_sparse_model

    def wrapper(model, options, *, build_s, require_usable=True):
        solved = original(model, options, build_s=build_s, require_usable=False)
        diagnostics = dict(solved.diagnostics)
        diagnostics["termination"] = TERMINATION_NO_FEASIBLE_SOLUTION
        diagnostics["has_incumbent"] = False
        empty = replace(
            solved,
            col_value=np.zeros(0, dtype=np.float64),
            status="time_limit",
            status_raw="kTimeLimit",
            classification=TERMINATION_NO_FEASIBLE_SOLUTION,
            has_incumbent=False,
            diagnostics=diagnostics,
        )
        raise UnusableSolveError(
            "HiGHS reached the time limit without a feasible solution (kTimeLimit)",
            empty,
        )

    with _patch_solve(wrapper):
        with pytest.raises(UnusableSolveError, match="without a feasible solution"):
            solve_arrays(sell=np.array([10.0, 80.0]), commitment=FIXED)


def test_injected_physically_invalid_incumbent_is_rejected() -> None:
    original = solve_sparse_model

    def wrapper(model, options, *, build_s, require_usable=True):
        solved = original(model, options, build_s=build_s, require_usable=False)
        values = np.array(solved.col_value, dtype=np.float64, copy=True)
        values[model.layout.idx_pump] = 0.5 * float(model.resolved_commitment.rated_pump_mw)
        values.setflags(write=False)
        return replace(relabel_time_limit_feasible(solved), col_value=values)

    with _patch_solve(wrapper):
        with pytest.raises(SolverError, match="feasibility"):
            solve_arrays(sell=np.array([10.0, 80.0, 10.0, 80.0]), commitment=FIXED)
