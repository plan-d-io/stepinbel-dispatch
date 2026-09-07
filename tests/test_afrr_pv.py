from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pyarrow as pa
import pytest

from stepinbel.config import (
    AFRRCase,
    AssetConfig,
    FixedMinimumCapacityBid,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.data.coverage import ResolvedPeriod, window_from_period
from stepinbel.data.load import MarketDataSlice
from stepinbel.markets.afrr import build_afrr_inputs
from stepinbel.optimizer import solve_case


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _stamps(n: int, start: datetime) -> list[datetime]:
    return [start + timedelta(minutes=15 * i) for i in range(n)]


def _pv_slice(
    stamps: list[datetime],
    *,
    da: np.ndarray,
    has_up: np.ndarray,
    has_down: np.ndarray,
    cbmp_up: np.ndarray,
    cbmp_down: np.ndarray,
    load_factor: np.ndarray,
    site: SiteConfig,
    asset: AssetConfig | None = None,
    market_case: AFRRCase | None = None,
    capacity_hours: float = 0.5,
) -> MarketDataSlice:
    start = stamps[0]
    end = stamps[-1] + timedelta(minutes=15)
    period = UtcPeriod(start, end)
    window = window_from_period(period)
    resolved = ResolvedPeriod(
        period=period,
        window=window,
        market="afrr",
        required_sources=("da_prices_qh", "balancing_qh", "capacity_blocks", "pv_profile_qh"),
        pv_region="Belgium",
    )
    rows = [
        {
            "delivery_date_local": date(2025, 1, 15),
            "block": "0-1",
            "product": "afrr",
            "direction": direction,
            "auction_step": "step1",
            "block_start_utc": start,
            "block_end_utc": start + timedelta(hours=capacity_hours),
            "block_hours": capacity_hours,
            "data_available": True,
            "awarded_volume_mw": 1.0,
            "marginal_price_eur_mw_h": 50.0,
            "vwap_price_eur_mw_h": 50.0,
        }
        for direction in ("up", "down")
    ]
    config = SimulationConfig(
        period=period,
        market_case=market_case
        or AFRRCase(capacity_bid=FixedMinimumCapacityBid(0.0, 0.0)),
        asset=asset
        or AssetConfig(
            soc_initial_frac=1.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
            storage_hours_basis="stored_energy",
        ),
        site=site,
    )
    n = len(stamps)
    return MarketDataSlice(
        config=config,
        period=resolved,
        manifest_sha256="00" * 32,
        da_prices=pa.table(
            {
                "datetime_utc": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
                "da_price_eur_mwh": da,
            }
        ),
        balancing=pa.table(
            {
                "datetime_utc": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
                "has_afrr_up": has_up,
                "has_afrr_down": has_down,
                "cbmp_afrr_up": cbmp_up,
                "cbmp_afrr_down": cbmp_down,
            }
        ),
        capacity_blocks=pa.table(
            {
                "delivery_date_local": [row["delivery_date_local"] for row in rows],
                "block": [row["block"] for row in rows],
                "product": [row["product"] for row in rows],
                "direction": [row["direction"] for row in rows],
                "auction_step": [row["auction_step"] for row in rows],
                "block_start_utc": [row["block_start_utc"] for row in rows],
                "block_end_utc": [row["block_end_utc"] for row in rows],
                "block_hours": [row["block_hours"] for row in rows],
                "data_available": [row["data_available"] for row in rows],
                "awarded_volume_mw": [row["awarded_volume_mw"] for row in rows],
                "marginal_price_eur_mw_h": [row["marginal_price_eur_mw_h"] for row in rows],
                "vwap_price_eur_mw_h": [row["vwap_price_eur_mw_h"] for row in rows],
            }
        ),
        pv_profile=pa.table(
            {
                "datetime_utc": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
                "region": ["Belgium"] * n,
                "measured_mw": [0.0] * n,
                "monitored_capacity_mw": [1.0] * n,
                "load_factor": load_factor,
            }
        ),
    )


def test_afrr_pv_export_uses_da_not_activation_prices() -> None:
    stamps = _stamps(2, _utc(2025, 1, 15, 0))
    slice_ = _pv_slice(
        stamps,
        da=np.array([10.0, 10.0]),
        has_up=np.ones(2, dtype=bool),
        has_down=np.zeros(2, dtype=bool),
        cbmp_up=np.array([200.0, 200.0]),
        cbmp_down=np.array([np.nan, np.nan]),
        load_factor=np.array([1.0, 0.0]),
        site=SiteConfig(
            pv_ac_kw=1000.0,
            pv_region="Belgium",
            grid_export_mw=2.0,
            pv_revenue_mode="da",
        ),
        asset=AssetConfig(
            soc_initial_frac=0.0,
            enforce_terminal_soc=False,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
    )
    market = build_afrr_inputs(slice_)
    np.testing.assert_array_equal(market.day_ahead_price_eur_mwh, [10.0, 10.0])
    assert market.sell_price_eur_mwh[0] == pytest.approx(200.0)
    result = solve_case(slice_)
    prices = result.dispatch.column("pv_export_price_eur_mwh").to_pylist()
    assert prices[0] == pytest.approx(10.0)
    assert prices[0] != pytest.approx(200.0)
    assert result.summary.total_site_revenue_eur == pytest.approx(
        result.summary.market_energy_net_eur
        + result.summary.capacity_revenue_eur
        + result.summary.pv_revenue_eur,
        abs=1e-7,
    )


def test_afrr_fixed_pv_shared_export_and_curtailment() -> None:
    stamps = _stamps(2, _utc(2025, 1, 15, 0))
    slice_ = _pv_slice(
        stamps,
        da=np.array([20.0, 20.0]),
        has_up=np.ones(2, dtype=bool),
        has_down=np.zeros(2, dtype=bool),
        cbmp_up=np.array([100.0, 100.0]),
        cbmp_down=np.array([np.nan, np.nan]),
        load_factor=np.array([1.0, 1.0]),
        site=SiteConfig(
            pv_ac_kw=1000.0,
            pv_region="Belgium",
            grid_export_mw=0.4,
            pv_revenue_mode="fixed",
            pv_fixed_price_eur_mwh=7.0,
        ),
    )
    result = solve_case(slice_)
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


def test_afrr_downward_buy_applies_only_to_grid_pump_and_pv_is_behind_meter() -> None:
    stamps = _stamps(4, _utc(2025, 1, 15, 0))
    slice_ = _pv_slice(
        stamps,
        da=np.full(4, 40.0),
        has_up=np.zeros(4, dtype=bool),
        has_down=np.ones(4, dtype=bool),
        cbmp_up=np.full(4, np.nan),
        cbmp_down=np.full(4, 5.0),
        load_factor=np.ones(4),
        site=SiteConfig(
            pv_ac_kw=1000.0,
            pv_region="Belgium",
            grid_import_mw=1.0,
            grid_export_mw=0.0,
            pv_revenue_mode="da",
        ),
        asset=AssetConfig(
            storage_hours=None,
            pond_energy_mwh=1.0,
            soc_initial_frac=0.0,
            soc_terminal_frac=0.4,
            enforce_terminal_soc=True,
            pump_ramp_power_frac=0.0,
            turbine_ramp_power_frac=0.0,
        ),
        market_case=AFRRCase(
            capacity_bid=FixedMinimumCapacityBid(0.0, 0.0),
            up_capacity_fraction=1.0,
        ),
        capacity_hours=1.0,
    )
    market = build_afrr_inputs(slice_)
    np.testing.assert_array_equal(market.buy_price_eur_mwh, np.full(4, 5.0))
    np.testing.assert_array_equal(market.day_ahead_price_eur_mwh, np.full(4, 40.0))
    result = solve_case(slice_)
    assert result.dispatch.column("pv_export_price_eur_mwh").to_pylist()[0] == pytest.approx(40.0)
    pv_to_pump = result.dispatch.column("pv_to_pump_mw").to_numpy()
    grid_pump = result.dispatch.column("p_pump_grid_mw").to_numpy()
    assert float(np.max(pv_to_pump)) > 0.0
    assert result.summary.total_site_revenue_eur == pytest.approx(
        result.summary.market_energy_net_eur
        + result.summary.capacity_revenue_eur
        + result.summary.pv_revenue_eur,
        abs=1e-7,
    )
    charging = result.summary.grid_charging_cost_eur
    expected_grid = float(np.sum(grid_pump) * 0.25 * 5.0)
    assert charging == pytest.approx(expected_grid, abs=1e-6)
