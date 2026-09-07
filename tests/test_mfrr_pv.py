from __future__ import annotations

import numpy as np
import pytest

from stepinbel.config import AssetConfig, SiteConfig
from stepinbel.optimizer.types import CapacityCommitment
from tests.da_solve_helpers import solve_arrays


def test_mfrr_like_pv_export_uses_da_price_not_activation_price() -> None:
    result = solve_arrays(
        sell=np.array([200.0, 200.0]),
        buy=np.array([10.0, 10.0]),
        day_ahead_price=np.array([10.0, 10.0]),
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
            pv_revenue_mode="da",
        ),
        pv_load_factor=np.array([1.0, 0.0]),
    )
    prices = result.dispatch.column("pv_export_price_eur_mwh").to_pylist()
    assert prices[0] == pytest.approx(10.0)
    assert prices[0] != pytest.approx(200.0)
    charging = result.dispatch.column("p_pump_grid_mw").to_numpy()
    buy = result.dispatch.column("market_buy_price_eur_mwh").to_numpy()
    assert buy[0] == pytest.approx(10.0)
    assert charging[0] == pytest.approx(0.0) or buy[0] == pytest.approx(10.0)
    assert result.summary.total_site_revenue_eur == pytest.approx(
        result.summary.market_energy_net_eur + result.summary.pv_revenue_eur,
        abs=1e-7,
    )


def test_mfrr_like_fixed_pv_price_and_shared_export_curtailment() -> None:
    result = solve_arrays(
        sell=np.array([100.0, 100.0]),
        buy=np.array([1e6, 1e6]),
        day_ahead_price=np.array([20.0, 20.0]),
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
            pv_fixed_price_eur_mwh=7.0,
        ),
        pv_load_factor=np.array([1.0, 1.0]),
        commitments=[
            CapacityCommitment(
                identifier="mfrr:2025-01-01:0-4:up",
                direction="up",
                start_index=0,
                end_index=2,
                price_eur_mw_h=0.0,
                cap_max_mw=0.4,
                block_hours=0.5,
                coverage_hours=0.25,
            )
        ],
    )
    prices = result.dispatch.column("pv_export_price_eur_mwh").to_pylist()
    assert prices[0] == pytest.approx(7.0)
    turb = result.dispatch.column("p_turbine_mw").to_numpy()
    pv_export = result.dispatch.column("pv_export_mw").to_numpy()
    curtail = result.dispatch.column("pv_curtail_mw").to_numpy()
    np.testing.assert_allclose(turb + pv_export, [0.4, 0.4], atol=1e-6)
    assert float(np.max(turb)) == pytest.approx(0.4, abs=1e-6)
    assert float(np.max(pv_export)) == pytest.approx(0.0, abs=1e-6)
    np.testing.assert_allclose(curtail, [1.0, 1.0], atol=1e-6)
    split = (
        result.summary.pv_self_consumed_mwh
        + result.summary.pv_exported_mwh
        + result.summary.pv_curtailed_mwh
    )
    assert split == pytest.approx(result.summary.pv_available_mwh, abs=1e-7)
    assert result.summary.total_site_revenue_eur == pytest.approx(
        result.summary.market_energy_net_eur
        + result.summary.capacity_revenue_eur
        + result.summary.pv_revenue_eur,
        abs=1e-7,
    )


def test_mfrr_like_no_pv_keeps_zero_and_null_pv_columns() -> None:
    result = solve_arrays(
        sell=np.array([80.0, 80.0]),
        buy=np.array([10.0, 10.0]),
        day_ahead_price=np.array([10.0, 10.0]),
    )
    assert result.dispatch.column("pv_export_price_eur_mwh").null_count == 2
    for name in (
        "pv_available_mw",
        "pv_to_pump_mw",
        "pv_export_mw",
        "pv_curtail_mw",
        "pv_revenue_eur",
    ):
        assert np.all(result.dispatch.column(name).to_numpy() == 0.0)
