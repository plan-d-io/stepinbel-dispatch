from __future__ import annotations

import numpy as np
import pytest

from stepinbel.config import AssetConfig, SiteConfig
from tests.da_solve_helpers import solve_arrays


def test_pv_kw_load_factor_conversion_and_self_consumption() -> None:
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
    )
    avail = result.dispatch.column("pv_available_mw").to_numpy()
    to_pump = result.dispatch.column("pv_to_pump_mw").to_numpy()
    grid = result.dispatch.column("p_pump_grid_mw").to_numpy()
    np.testing.assert_allclose(avail[:2], [1.0, 1.0], atol=1e-8)
    assert to_pump[0] == pytest.approx(1.0, abs=1e-6)
    assert grid[0] == pytest.approx(0.0, abs=1e-6)


def test_pv_curtailment_when_export_cable_is_full() -> None:
    result = solve_arrays(
        sell=np.array([100.0, 100.0]),
        buy=np.array([100.0, 100.0]),
        sell_ub=np.array([0.0, 0.0]),
        buy_ub=np.array([0.0, 0.0]),
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
        pv_load_factor=np.array([1.0, 1.0]),
    )
    export = result.dispatch.column("pv_export_mw").to_numpy()
    curtail = result.dispatch.column("pv_curtail_mw").to_numpy()
    assert float(np.max(export)) == pytest.approx(0.4, abs=1e-6)
    assert float(np.min(curtail)) == pytest.approx(0.6, abs=1e-6)


def test_fixed_pv_settlement_and_split_reconciliation() -> None:
    result = solve_arrays(
        sell=np.array([10.0, 80.0]),
        asset=AssetConfig(pump_ramp_power_frac=0.0, turbine_ramp_power_frac=0.0),
        site=SiteConfig(
            pv_ac_kw=500.0,
            pv_region="Belgium",
            pv_revenue_mode="fixed",
            pv_fixed_price_eur_mwh=40.0,
        ),
        pv_load_factor=np.array([0.5, 0.0]),
    )
    prices = result.dispatch.column("pv_export_price_eur_mwh").to_pylist()
    assert prices[0] == pytest.approx(40.0)
    split = (
        result.summary.pv_self_consumed_mwh
        + result.summary.pv_exported_mwh
        + result.summary.pv_curtailed_mwh
    )
    assert split == pytest.approx(result.summary.pv_available_mwh, abs=1e-7)
    assert result.summary.total_site_revenue_eur == pytest.approx(
        result.summary.market_energy_net_eur + result.summary.pv_revenue_eur,
        abs=1e-7,
    )


def test_turbine_and_pv_export_share_the_export_connection() -> None:
    result = solve_arrays(
        sell=np.array([100.0, 100.0]),
        buy=np.array([1e6, 1e6]),
        asset=AssetConfig(
            soc_initial_frac=1.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            storage_hours_basis="stored_energy",
        ),
        site=SiteConfig(
            pv_ac_kw=1000.0,
            pv_region="Belgium",
            grid_export_mw=0.4,
            pv_revenue_mode="fixed",
            pv_fixed_price_eur_mwh=0.0,
        ),
        pv_load_factor=np.array([1.0, 1.0]),
    )
    turb = result.dispatch.column("p_turbine_mw").to_numpy()
    pv_export = result.dispatch.column("pv_export_mw").to_numpy()
    pv_to_pump = result.dispatch.column("pv_to_pump_mw").to_numpy()
    curtail = result.dispatch.column("pv_curtail_mw").to_numpy()
    np.testing.assert_allclose(turb, [0.4, 0.4], atol=1e-6)
    np.testing.assert_allclose(turb + pv_export, [0.4, 0.4], atol=1e-6)
    assert float(np.max(pv_export)) == pytest.approx(0.0, abs=1e-6)
    assert float(np.max(pv_to_pump)) == pytest.approx(0.0, abs=1e-6)
    np.testing.assert_allclose(curtail, [1.0, 1.0], atol=1e-6)


def test_disabled_pv_has_no_pv_variables_and_null_price() -> None:
    result = solve_arrays(sell=np.array([10.0, 80.0]))
    assert result.dispatch.column("pv_export_price_eur_mwh").null_count == 2
    for name in (
        "pv_available_mw",
        "pv_to_pump_mw",
        "pv_export_mw",
        "pv_curtail_mw",
        "pv_revenue_eur",
    ):
        values = result.dispatch.column(name).to_numpy()
        assert np.all(values == 0.0)
    no_pv_cols = result.solver.num_col
    with_pv = solve_arrays(
        sell=np.array([10.0, 80.0]),
        site=SiteConfig(pv_ac_kw=100.0, pv_region="Belgium"),
        pv_load_factor=np.array([0.0, 0.0]),
    )
    assert with_pv.solver.num_col == no_pv_cols + 8


def test_explicit_grid_limits_bind_with_and_without_pv() -> None:
    no_pv = solve_arrays(
        sell=np.array([0.0, 100.0]),
        buy=np.array([0.0, 1e6]),
        sell_ub=np.array([1.0, 1.0]),
        buy_ub=np.array([1.0, 1.0]),
        asset=AssetConfig(
            soc_initial_frac=1.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            storage_hours_basis="stored_energy",
        ),
        site=SiteConfig(grid_import_mw=0.2, grid_export_mw=0.3),
    )
    assert max(no_pv.dispatch.column("p_turbine_mw").to_pylist()) == pytest.approx(
        0.3, abs=1e-5
    )
    import_limited = solve_arrays(
        sell=np.array([0.0, 100.0]),
        buy=np.array([0.0, 1e6]),
        asset=AssetConfig(
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
        site=SiteConfig(grid_import_mw=0.2),
    )
    assert import_limited.dispatch.column("p_pump_mw").to_pylist()[0] == pytest.approx(
        0.2, abs=1e-6
    )

    omitted = solve_arrays(
        sell=np.array([0.0, 100.0]),
        buy=np.array([0.0, 1e6]),
        asset=AssetConfig(
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
    )
    assert omitted.dispatch.column("p_pump_mw").to_pylist()[0] == pytest.approx(1.0, abs=1e-6)
