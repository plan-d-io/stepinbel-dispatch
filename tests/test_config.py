from __future__ import annotations

import math
from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timezone

import pytest

from stepinbel.config import (
    AFRRCase,
    ActivationProfile,
    AssetConfig,
    BelgianDeliveryPeriod,
    CapacityBid,
    ConfigError,
    DayAheadCase,
    FixedMinimumCapacityBid,
    HistoricalQuantileCapacityBid,
    MachineCommitmentConfig,
    MFRRCase,
    MarketCase,
    Period,
    PvRevenueMode,
    SimulationConfig,
    SiteConfig,
    WindRevenueMode,
    StorageHoursBasis,
    UtcPeriod,
)

PUBLIC_CONFIG_TYPES = (
    AssetConfig,
    SiteConfig,
    HistoricalQuantileCapacityBid,
    FixedMinimumCapacityBid,
    DayAheadCase,
    MFRRCase,
    AFRRCase,
    BelgianDeliveryPeriod,
    UtcPeriod,
    SimulationConfig,
    MachineCommitmentConfig,
)
FORBIDDEN_FIELDS = {
    "dispatch_mode",
    "solver_mode",
    "forbid_simultaneous",
    "pump_min_stable_frac",
    "turbine_min_stable_frac",
}


@pytest.mark.parametrize("cls", PUBLIC_CONFIG_TYPES)
def test_public_configuration_objects_are_frozen(cls) -> None:
    instance = _example(cls)
    name = fields(cls)[0].name
    with pytest.raises(FrozenInstanceError):
        setattr(instance, name, getattr(instance, name))


def test_default_asset_metrics_match_phs_baseline() -> None:
    asset = AssetConfig()
    assert asset.e_max_mwh() == pytest.approx(4.0 / 0.90)
    assert asset.usable_energy_mwh() == pytest.approx(4.0)
    assert asset.grid_energy_to_fill_mwh() == pytest.approx((4.0 / 0.90) / 0.84)
    assert asset.round_trip_efficiency() == pytest.approx(0.84 * 0.90)
    assert asset.discharge_duration_h() == pytest.approx(4.0)
    assert asset.charge_duration_h() == pytest.approx((4.0 / 0.90) / (1.0 * 0.84))
    assert asset.e_max_source() == "discharge_at_rated"
    assert asset.epsilon_pump_mwh_per_mw() == pytest.approx(0.10 * (10.0 / 60.0))
    assert asset.epsilon_turbine_mwh_per_mw() == 0.0
    assert asset.machines_asymmetric() is False


def test_direct_pond_energy_and_stored_energy_bases() -> None:
    pond = AssetConfig(storage_hours=None, pond_energy_mwh=10.0)
    assert pond.e_max_source() == "pond_energy_mwh"
    assert pond.e_max_mwh() == 10.0
    assert pond.usable_energy_mwh() == pytest.approx(9.0)

    stored = AssetConfig(storage_hours=4.0, storage_hours_basis="stored_energy")
    assert stored.e_max_source() == "stored_energy"
    assert stored.e_max_mwh() == 4.0
    assert stored.usable_energy_mwh() == pytest.approx(3.6)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"power_pump_mw": True}, "power_pump_mw"),
        ({"eta_pump": math.nan}, "eta_pump"),
        ({"eta_turbine": math.inf}, "eta_turbine"),
        ({"storage_hours": 0.0}, "storage_hours"),
        ({"soc_initial_frac": 1.5}, "soc_initial_frac"),
        ({"pump_ramp_up_min": 0.0}, "pump_ramp_up_min"),
        ({"pump_ramp_power_frac": 1.1}, "pump_ramp_power_frac"),
    ],
)
def test_invalid_numeric_asset_inputs_name_the_field(kwargs: dict, field: str) -> None:
    with pytest.raises(ConfigError, match=field):
        AssetConfig(**kwargs)


def test_storage_hours_and_pond_energy_are_mutually_exclusive() -> None:
    with pytest.raises(ConfigError, match="storage_hours"):
        AssetConfig(storage_hours=4.0, pond_energy_mwh=4.0)
    with pytest.raises(ConfigError, match="storage_hours"):
        AssetConfig(storage_hours=None, pond_energy_mwh=None)


def test_effective_grid_limits_default_and_explicit_zero() -> None:
    defaulted = SimulationConfig(period=_utc_day(), market_case=DayAheadCase())
    assert defaulted.effective_grid_import_mw() == 1.0
    assert defaulted.effective_grid_export_mw() == 1.0

    zero = SimulationConfig(
        period=_utc_day(),
        market_case=DayAheadCase(),
        site=SiteConfig(grid_import_mw=0.0, grid_export_mw=0.0),
    )
    assert zero.effective_grid_import_mw() == 0.0
    assert zero.effective_grid_export_mw() == 0.0
    with pytest.raises(ConfigError, match="grid_import_mw"):
        SiteConfig(grid_import_mw=-1.0)


def test_pv_region_and_fixed_price_validation() -> None:
    with pytest.raises(ConfigError, match="pv_region"):
        SiteConfig(pv_ac_kw=500.0, pv_region=None)
    with pytest.raises(ConfigError, match="pv_fixed_price_eur_mwh"):
        SiteConfig(pv_revenue_mode="fixed")
    allowed = SiteConfig(
        pv_ac_kw=100.0,
        pv_region="Belgium",
        pv_revenue_mode="fixed",
        pv_fixed_price_eur_mwh=-12.5,
    )
    assert allowed.pv_fixed_price_eur_mwh == -12.5
    assert SimulationConfig(
        period=_utc_day(),
        market_case=DayAheadCase(),
        site=allowed,
    ).pv_enabled() is True
    assert SimulationConfig(period=_utc_day(), market_case=DayAheadCase()).pv_enabled() is False


def test_wind_profile_and_fixed_price_validation() -> None:
    with pytest.raises(ConfigError, match="wind_profile_id"):
        SiteConfig(wind_capacity_kw=500.0, wind_profile_id=None)
    with pytest.raises(ConfigError, match="wind_fixed_price_eur_mwh"):
        SiteConfig(wind_revenue_mode="fixed")
    with pytest.raises(ConfigError, match="wind_capacity_kw"):
        SiteConfig(wind_capacity_kw=-1.0)
    allowed = SiteConfig(
        wind_capacity_kw=100.0,
        wind_profile_id="onshore_belgium",
        wind_revenue_mode="fixed",
        wind_fixed_price_eur_mwh=-12.5,
    )
    assert allowed.wind_fixed_price_eur_mwh == -12.5
    config = SimulationConfig(
        period=_utc_day(),
        market_case=DayAheadCase(),
        site=allowed,
    )
    assert config.wind_enabled() is True
    default = SimulationConfig(period=_utc_day(), market_case=DayAheadCase())
    assert default.wind_enabled() is False
    assert default.site.wind_capacity_kw == 0.0
    assert default.site.wind_profile_id == "onshore_belgium"
    assert default.site.wind_revenue_mode == "da"
    assert default.site.wind_fixed_price_eur_mwh is None


def test_fixed_capacity_prices_must_be_non_negative() -> None:
    assert FixedMinimumCapacityBid(upward_price_eur_mw_h=0.0).upward_price_eur_mw_h == 0.0
    assert FixedMinimumCapacityBid(1.25).upward_price_eur_mw_h == 1.25
    assert FixedMinimumCapacityBid(5.0, 0.0).downward_price_eur_mw_h == 0.0
    with pytest.raises(ConfigError, match="upward_price_eur_mw_h"):
        FixedMinimumCapacityBid(upward_price_eur_mw_h=-0.01)
    with pytest.raises(ConfigError, match="downward_price_eur_mw_h"):
        FixedMinimumCapacityBid(1.0, -0.01)


def test_capacity_bids_are_independent_of_activation_profile() -> None:
    quantile = HistoricalQuantileCapacityBid(0.50)
    mfrr = MFRRCase(activation_profile="passive", capacity_bid=quantile)
    assert mfrr.activation_profile == "passive"
    assert mfrr.capacity_bid == quantile

    mfrr_fixed = MFRRCase(
        activation_profile="balanced",
        capacity_bid=FixedMinimumCapacityBid(upward_price_eur_mw_h=10.0),
    )
    assert mfrr_fixed.capacity_bid.downward_price_eur_mw_h is None
    with pytest.raises(ConfigError, match="downward_price_eur_mw_h"):
        MFRRCase(capacity_bid=FixedMinimumCapacityBid(1.0, 2.0))

    afrr_fixed = AFRRCase(
        activation_profile="passive",
        capacity_bid=FixedMinimumCapacityBid(5.0, 5.0),
    )
    assert afrr_fixed.capacity_bid.upward_price_eur_mw_h == 5.0
    with pytest.raises(ConfigError, match="downward_price_eur_mw_h"):
        AFRRCase(capacity_bid=FixedMinimumCapacityBid(upward_price_eur_mw_h=5.0))
    with pytest.raises(ConfigError, match="quantile"):
        HistoricalQuantileCapacityBid(1.2)


@pytest.mark.parametrize("profile", ["god", "active", "unknown"])
def test_rejected_activation_profiles(profile: str) -> None:
    with pytest.raises(ConfigError, match="activation_profile"):
        MFRRCase(activation_profile=profile)  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="activation_profile"):
        AFRRCase(activation_profile=profile)  # type: ignore[arg-type]


def test_only_balanced_and_passive_are_accepted() -> None:
    assert MFRRCase(activation_profile="balanced").activation_profile == "balanced"
    assert AFRRCase(activation_profile="passive").activation_profile == "passive"


def test_day_ahead_case_has_no_activation_or_capacity_fields() -> None:
    names = {item.name for item in fields(DayAheadCase)}
    assert names == {"market"}
    case = DayAheadCase()
    assert not hasattr(case, "activation_profile")
    assert not hasattr(case, "capacity_bid")
    with pytest.raises(ConfigError, match="da"):
        DayAheadCase(market="mfrr")  # type: ignore[arg-type]


def test_production_configuration_omits_closed_solver_and_milp_fields() -> None:
    for cls in PUBLIC_CONFIG_TYPES:
        names = {item.name for item in fields(cls)}
        assert names.isdisjoint(FORBIDDEN_FIELDS)


def test_unhashable_choice_values_raise_config_error() -> None:
    with pytest.raises(ConfigError, match="storage_hours_basis"):
        AssetConfig(storage_hours_basis=[])  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="pv_revenue_mode"):
        SiteConfig(pv_revenue_mode=[])  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="activation_profile"):
        MFRRCase(activation_profile=[])  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="activation_profile"):
        AFRRCase(activation_profile=[])  # type: ignore[arg-type]


def test_belgian_delivery_period_rejects_unconvertible_end_date() -> None:
    with pytest.raises(ConfigError, match="exclusive UTC bound"):
        BelgianDeliveryPeriod(date.max, date.max)


def test_machine_commitment_defaults_are_inactive() -> None:
    options = MachineCommitmentConfig()
    assert options.fixed_speed_pump is False
    assert options.turbine_minimum_output_fraction == 0.0
    assert options.forbid_simultaneous_operation is False
    assert options.physically_active() is False
    assert SimulationConfig(period=_utc_day(), market_case=DayAheadCase()).machine_commitment == options


def test_machine_commitment_rejects_invalid_fractions() -> None:
    with pytest.raises(ConfigError, match="finite"):
        MachineCommitmentConfig(turbine_minimum_output_fraction=math.nan)
    with pytest.raises(ConfigError, match=r"\[0, 1\]"):
        MachineCommitmentConfig(turbine_minimum_output_fraction=1.01)
    with pytest.raises(ConfigError, match="boolean"):
        MachineCommitmentConfig(fixed_speed_pump=0)  # type: ignore[arg-type]


def test_exported_type_aliases_exist() -> None:
    assert ActivationProfile
    assert CapacityBid
    assert MarketCase
    assert Period
    assert PvRevenueMode
    assert WindRevenueMode
    assert StorageHoursBasis


def _utc_day() -> UtcPeriod:
    return UtcPeriod(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 16, tzinfo=timezone.utc),
    )


def _example(cls):
    if cls is AssetConfig:
        return AssetConfig()
    if cls is SiteConfig:
        return SiteConfig()
    if cls is HistoricalQuantileCapacityBid:
        return HistoricalQuantileCapacityBid()
    if cls is FixedMinimumCapacityBid:
        return FixedMinimumCapacityBid(1.0, 2.0)
    if cls is DayAheadCase:
        return DayAheadCase()
    if cls is MFRRCase:
        return MFRRCase()
    if cls is AFRRCase:
        return AFRRCase()
    if cls is BelgianDeliveryPeriod:
        return BelgianDeliveryPeriod(date(2025, 1, 1), date(2025, 1, 1))
    if cls is UtcPeriod:
        return _utc_day()
    if cls is SimulationConfig:
        return SimulationConfig(period=_utc_day(), market_case=DayAheadCase())
    if cls is MachineCommitmentConfig:
        return MachineCommitmentConfig()
    raise AssertionError(cls)
