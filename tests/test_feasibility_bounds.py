from __future__ import annotations

import numpy as np
import pytest

from stepinbel.config import AssetConfig, DayAheadCase, SimulationConfig, SiteConfig
from stepinbel.markets.base import MarketDispatchInputs
from stepinbel.optimizer.checks import check_solution, decode_solution
from stepinbel.optimizer.highs import solve_sparse_model
from stepinbel.optimizer.model import SparseModel, build_sparse_model, prepare_physical
from stepinbel.optimizer.types import POWER_TOL_MW, PV_ALLOCATION_TOL_MW, SolverError, SolverOptions
from tests.da_solve_helpers import resolved_da_period


def _pv_lp() -> tuple[SparseModel, np.ndarray]:
    n = 2
    period, resolved = resolved_da_period(n)
    config = SimulationConfig(
        period=period,
        market_case=DayAheadCase(),
        asset=AssetConfig(
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
        site=SiteConfig(
            pv_ac_kw=1000.0,
            pv_region="Belgium",
            grid_export_mw=0.4,
            pv_revenue_mode="da",
        ),
    )
    market = MarketDispatchInputs(
        sell_price_eur_mwh=np.array([100.0, 100.0]),
        buy_price_eur_mwh=np.array([100.0, 100.0]),
        sell_upper_mw=np.zeros(n),
        buy_upper_mw=np.zeros(n),
        day_ahead_price_eur_mwh=np.array([100.0, 100.0]),
    )
    prepared = prepare_physical(config, market, np.array([1.0, 1.0]))
    lp = build_sparse_model(prepared)
    solved = solve_sparse_model(lp, SolverOptions(), build_s=0.0)
    return lp, np.array(solved.col_value, dtype=np.float64, copy=True)


def _run_checks(lp: SparseModel, col_value: np.ndarray) -> None:
    prepared = lp.prepared
    dt = prepared.dt_h
    decoded = decode_solution(lp, col_value, 0.0)
    energy_gross = dt * prepared.sell_price * decoded.p_turbine
    charging = dt * prepared.buy_price * decoded.p_pump_grid
    energy_net = energy_gross - charging
    pv_rev = dt * decoded.pv_price * decoded.pv_export
    interval_total = energy_net + pv_rev
    summary_energy_gross = float(energy_gross.sum())
    summary_charging = float(charging.sum())
    summary_energy_net = float(energy_net.sum())
    summary_pv = float(pv_rev.sum())
    summary_total = summary_energy_net + summary_pv
    decoded = decode_solution(lp, col_value, summary_total)
    check_solution(
        lp,
        decoded,
        col_value,
        energy_gross_eur=energy_gross,
        grid_charging_cost_eur=charging,
        market_energy_net_eur=energy_net,
        pv_revenue_eur=pv_rev,
        interval_total_eur=interval_total,
        summary_energy_gross=summary_energy_gross,
        summary_charging=summary_charging,
        summary_energy_net=summary_energy_net,
        summary_capacity=0.0,
        summary_pv=summary_pv,
        summary_total=summary_total,
    )


def test_tampered_pv_split_lower_bound_is_rejected() -> None:
    lp, col_value = _pv_lp()
    assert lp.layout.idx_pv_export is not None
    assert lp.layout.idx_pv_curtail is not None
    _run_checks(lp, col_value)

    tampered = np.array(col_value, dtype=np.float64, copy=True)
    export_idx = lp.layout.idx_pv_export
    curtail_idx = lp.layout.idx_pv_curtail
    original_export = float(tampered[export_idx])
    tampered[export_idx] = -0.05
    tampered[curtail_idx] += original_export + 0.05

    decoded = decode_solution(lp, tampered, 0.0)
    split = decoded.pv_export + decoded.pv_to_pump + decoded.pv_curtail
    np.testing.assert_allclose(split, decoded.pv_available, atol=1e-12)
    assert tampered[export_idx] < lp.col_lower[export_idx]
    assert tampered.shape == (lp.num_col,)

    with pytest.raises(SolverError, match="post-solve feasibility or accounting checks failed") as caught:
        _run_checks(lp, tampered)
    message = str(caught.value)
    assert "bound=" in message
    assert "pv=" in message


def _set_pv_split_residual(decoded, residual: float) -> None:
    decoded.pv_curtail = np.array(decoded.pv_curtail, dtype=np.float64, copy=True)
    split = decoded.pv_export + decoded.pv_to_pump + decoded.pv_curtail
    decoded.pv_curtail += decoded.pv_available - split
    decoded.pv_curtail[0] += residual


def _decoded_and_kwargs(lp: SparseModel, col_value: np.ndarray):
    prepared = lp.prepared
    dt = prepared.dt_h
    decoded = decode_solution(lp, col_value, 0.0)
    energy_gross = dt * prepared.sell_price * decoded.p_turbine
    charging = dt * prepared.buy_price * decoded.p_pump_grid
    energy_net = energy_gross - charging
    pv_rev = dt * decoded.pv_price * decoded.pv_export
    interval_total = energy_net + pv_rev
    summary_energy_gross = float(energy_gross.sum())
    summary_charging = float(charging.sum())
    summary_energy_net = float(energy_net.sum())
    summary_pv = float(pv_rev.sum())
    summary_total = summary_energy_net + summary_pv
    decoded = decode_solution(lp, col_value, summary_total)
    return decoded, {
        "energy_gross_eur": energy_gross,
        "grid_charging_cost_eur": charging,
        "market_energy_net_eur": energy_net,
        "pv_revenue_eur": pv_rev,
        "interval_total_eur": interval_total,
        "summary_energy_gross": summary_energy_gross,
        "summary_charging": summary_charging,
        "summary_energy_net": summary_energy_net,
        "summary_capacity": 0.0,
        "summary_pv": summary_pv,
        "summary_total": summary_total,
    }


def test_observed_watt_scale_pv_split_residual_is_accepted() -> None:
    lp, col_value = _pv_lp()
    decoded, kwargs = _decoded_and_kwargs(lp, col_value)
    observed = 9.769e-07
    assert POWER_TOL_MW < observed <= PV_ALLOCATION_TOL_MW
    _set_pv_split_residual(decoded, observed)
    report = check_solution(lp, decoded, col_value, **kwargs)
    assert report.ok is True
    assert report.max_pv_residual_mw == pytest.approx(observed)
    assert report.max_bound_residual <= POWER_TOL_MW
    assert report.max_grid_residual_mw <= POWER_TOL_MW
    assert report.max_balance_residual_mwh <= 1e-7


def test_pv_split_residual_comfortably_above_tolerance_is_rejected() -> None:
    lp, col_value = _pv_lp()
    decoded, kwargs = _decoded_and_kwargs(lp, col_value)
    excess = 5e-6
    assert excess > PV_ALLOCATION_TOL_MW
    _set_pv_split_residual(decoded, excess)
    with pytest.raises(SolverError, match="post-solve feasibility or accounting checks failed") as caught:
        check_solution(lp, decoded, col_value, **kwargs)
    message = str(caught.value)
    assert "pv=" in message
    assert "5.000e-06" in message
