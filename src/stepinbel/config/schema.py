"""Immutable StepInBel configuration objects."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

StorageHoursBasis = Literal["discharge_at_rated", "stored_energy"]
PvRevenueMode = Literal["da", "fixed"]
ActivationProfile = Literal["balanced", "passive"]

_BRUSSELS = ZoneInfo("Europe/Brussels")
_QH = timedelta(minutes=15)
_ACTIVATION_PROFILES: frozenset[str] = frozenset({"balanced", "passive"})
_STORAGE_BASES: frozenset[str] = frozenset({"discharge_at_rated", "stored_energy"})
_PV_MODES: frozenset[str] = frozenset({"da", "fixed"})


class ConfigError(ValueError):
    """Invalid configuration. Refuse to proceed rather than silently relax."""


def _require_finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"{field} must be a finite number")
    return number


def _require_optional_finite(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _require_finite(value, field)


def _require_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ConfigError(f"{field} must be a boolean")
    return value


def _require_plain_date(value: object, field: str) -> date:
    if type(value) is not date:
        raise ConfigError(f"{field} must be a datetime.date, not a datetime")
    return value


def _require_utc_qh(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ConfigError(f"{field} must be a timezone-aware UTC datetime")
    if value.tzinfo is None:
        raise ConfigError(f"{field} is timezone-naive; refusing to guess UTC")
    offset = value.utcoffset()
    if offset is None or offset != timedelta(0):
        raise ConfigError(f"{field} must have a zero UTC offset")
    utc = value.astimezone(timezone.utc)
    if utc.second != 0 or utc.microsecond != 0 or utc.minute % 15 != 0:
        raise ConfigError(f"{field} must lie on a 15-minute UTC boundary")
    return utc


def _require_allowed_str(value: object, field: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ConfigError(f"{field} must be one of {sorted(allowed)}")
    return value


def _exclusive_delivery_end_date(day: date) -> date:
    try:
        return day + timedelta(days=1)
    except OverflowError as exc:
        raise ConfigError(
            "Belgian delivery period end date cannot be converted to an exclusive UTC bound"
        ) from exc


def brussels_midnight_utc(day: date) -> datetime:
    """Local midnight in Europe/Brussels, converted to UTC."""
    local = datetime(day.year, day.month, day.day, tzinfo=_BRUSSELS)
    return local.astimezone(timezone.utc)


@dataclass(frozen=True)
class AssetConfig:
    """Physical pumped-hydro asset. Pond size is separate from machine ratings."""

    power_pump_mw: float = 1.0
    power_turbine_mw: float = 1.0
    eta_pump: float = 0.84
    eta_turbine: float = 0.90
    storage_hours: float | None = 4.0
    storage_hours_basis: StorageHoursBasis = "discharge_at_rated"
    pond_energy_mwh: float | None = None
    soc_initial_frac: float = 0.5
    soc_terminal_frac: float = 0.5
    enforce_terminal_soc: bool = True
    pump_ramp_up_min: float = 10.0
    pump_ramp_down_min: float = 1.0
    pump_ramp_power_frac: float = 0.10
    turbine_ramp_up_min: float = 2.0
    turbine_ramp_down_min: float = 2.0
    turbine_ramp_power_frac: float = 0.0

    def __post_init__(self) -> None:
        power_pump = _require_finite(self.power_pump_mw, "power_pump_mw")
        power_turbine = _require_finite(self.power_turbine_mw, "power_turbine_mw")
        if power_pump <= 0 or power_turbine <= 0:
            raise ConfigError("power_pump_mw and power_turbine_mw must be > 0")
        eta_pump = _require_finite(self.eta_pump, "eta_pump")
        eta_turbine = _require_finite(self.eta_turbine, "eta_turbine")
        if not 0.0 < eta_pump <= 1.0 or not 0.0 < eta_turbine <= 1.0:
            raise ConfigError("eta_pump and eta_turbine must be in (0, 1]")
        _require_allowed_str(
            self.storage_hours_basis, "storage_hours_basis", _STORAGE_BASES
        )
        hours = _require_optional_finite(self.storage_hours, "storage_hours")
        pond = _require_optional_finite(self.pond_energy_mwh, "pond_energy_mwh")
        if (hours is None) == (pond is None):
            raise ConfigError(
                "exactly one of storage_hours and pond_energy_mwh must be set"
            )
        if hours is not None and hours <= 0:
            raise ConfigError("storage_hours must be > 0")
        if pond is not None and pond <= 0:
            raise ConfigError("pond_energy_mwh must be > 0")
        soc_initial = _require_finite(self.soc_initial_frac, "soc_initial_frac")
        soc_terminal = _require_finite(self.soc_terminal_frac, "soc_terminal_frac")
        if not 0.0 <= soc_initial <= 1.0 or not 0.0 <= soc_terminal <= 1.0:
            raise ConfigError("soc_initial_frac and soc_terminal_frac must be in [0, 1]")
        _require_bool(self.enforce_terminal_soc, "enforce_terminal_soc")
        for name in (
            "pump_ramp_up_min",
            "pump_ramp_down_min",
            "turbine_ramp_up_min",
            "turbine_ramp_down_min",
        ):
            minutes = _require_finite(getattr(self, name), name)
            if minutes <= 0:
                raise ConfigError(f"{name} must be > 0")
        for name in ("pump_ramp_power_frac", "turbine_ramp_power_frac"):
            fraction = _require_finite(getattr(self, name), name)
            if not 0.0 <= fraction <= 1.0:
                raise ConfigError(f"{name} must be in [0, 1]")

    def e_max_source(self) -> str:
        if self.pond_energy_mwh is not None:
            return "pond_energy_mwh"
        return self.storage_hours_basis

    def e_max_mwh(self) -> float:
        if self.pond_energy_mwh is not None:
            return float(self.pond_energy_mwh)
        assert self.storage_hours is not None
        raw = self.power_turbine_mw * self.storage_hours
        if self.storage_hours_basis == "stored_energy":
            return raw
        return raw / self.eta_turbine

    def usable_energy_mwh(self) -> float:
        return self.e_max_mwh() * self.eta_turbine

    def grid_energy_to_fill_mwh(self) -> float:
        return self.e_max_mwh() / self.eta_pump

    def round_trip_efficiency(self) -> float:
        return self.eta_pump * self.eta_turbine

    def discharge_duration_h(self) -> float:
        return self.usable_energy_mwh() / self.power_turbine_mw

    def charge_duration_h(self) -> float:
        return self.e_max_mwh() / (self.power_pump_mw * self.eta_pump)

    def machines_asymmetric(self) -> bool:
        return abs(self.power_pump_mw - self.power_turbine_mw) > 1e-12

    def epsilon_pump_mwh_per_mw(self) -> float:
        return float(self.pump_ramp_power_frac) * (float(self.pump_ramp_up_min) / 60.0)

    def epsilon_turbine_mwh_per_mw(self) -> float:
        return float(self.turbine_ramp_power_frac) * (
            float(self.turbine_ramp_up_min) / 60.0
        )

    def pump_ramp_up_mw_per_h(self) -> float:
        return self.power_pump_mw / (float(self.pump_ramp_up_min) / 60.0)

    def pump_ramp_down_mw_per_h(self) -> float:
        return self.power_pump_mw / (float(self.pump_ramp_down_min) / 60.0)

    def turbine_ramp_up_mw_per_h(self) -> float:
        return self.power_turbine_mw / (float(self.turbine_ramp_up_min) / 60.0)

    def turbine_ramp_down_mw_per_h(self) -> float:
        return self.power_turbine_mw / (float(self.turbine_ramp_down_min) / 60.0)


@dataclass(frozen=True)
class SiteConfig:
    """Grid connection and optional co-located PV."""

    grid_export_mw: float | None = None
    grid_import_mw: float | None = None
    pv_ac_kw: float = 0.0
    pv_region: str | None = "Belgium"
    pv_revenue_mode: PvRevenueMode = "da"
    pv_fixed_price_eur_mwh: float | None = None

    def __post_init__(self) -> None:
        export = _require_optional_finite(self.grid_export_mw, "grid_export_mw")
        imported = _require_optional_finite(self.grid_import_mw, "grid_import_mw")
        if export is not None and export < 0:
            raise ConfigError("grid_export_mw must be >= 0")
        if imported is not None and imported < 0:
            raise ConfigError("grid_import_mw must be >= 0")
        pv = _require_finite(self.pv_ac_kw, "pv_ac_kw")
        if pv < 0:
            raise ConfigError("pv_ac_kw must be >= 0")
        _require_allowed_str(self.pv_revenue_mode, "pv_revenue_mode", _PV_MODES)
        if pv > 0:
            if not isinstance(self.pv_region, str) or not self.pv_region.strip():
                raise ConfigError("pv_region must be a non-empty string when PV is enabled")
        if self.pv_revenue_mode == "fixed":
            _require_finite(self.pv_fixed_price_eur_mwh, "pv_fixed_price_eur_mwh")
        elif self.pv_fixed_price_eur_mwh is not None:
            _require_finite(self.pv_fixed_price_eur_mwh, "pv_fixed_price_eur_mwh")


@dataclass(frozen=True)
class HistoricalQuantileCapacityBid:
    """Historical block-marginal quantile used for PHS parity."""

    quantile: float = 0.50

    def __post_init__(self) -> None:
        quantile = _require_finite(self.quantile, "quantile")
        if not 0.0 <= quantile <= 1.0:
            raise ConfigError("quantile must be in [0, 1]")


@dataclass(frozen=True)
class FixedMinimumCapacityBid:
    """Explicit minimum capacity-bid prices. Zero is allowed; negatives are not."""

    upward_price_eur_mw_h: float
    downward_price_eur_mw_h: float | None = None

    def __post_init__(self) -> None:
        upward = _require_finite(self.upward_price_eur_mw_h, "upward_price_eur_mw_h")
        if upward < 0.0:
            raise ConfigError("upward_price_eur_mw_h must be >= 0")
        downward = _require_optional_finite(
            self.downward_price_eur_mw_h, "downward_price_eur_mw_h"
        )
        if downward is not None and downward < 0.0:
            raise ConfigError("downward_price_eur_mw_h must be >= 0")


CapacityBid = HistoricalQuantileCapacityBid | FixedMinimumCapacityBid


def _require_activation_profile(value: object) -> ActivationProfile:
    if not isinstance(value, str) or value not in _ACTIVATION_PROFILES:
        raise ConfigError("activation_profile must be 'balanced' or 'passive'")
    return value  # type: ignore[return-value]


def _validate_capacity_bid(bid: CapacityBid, market: Literal["mfrr", "afrr"]) -> None:
    if not isinstance(bid, (HistoricalQuantileCapacityBid, FixedMinimumCapacityBid)):
        raise ConfigError("capacity_bid must be a HistoricalQuantileCapacityBid or FixedMinimumCapacityBid")
    if not isinstance(bid, FixedMinimumCapacityBid):
        return
    if market == "mfrr" and bid.downward_price_eur_mw_h is not None:
        raise ConfigError("mFRR fixed capacity bids must not set downward_price_eur_mw_h")
    if market == "afrr" and bid.downward_price_eur_mw_h is None:
        raise ConfigError("aFRR fixed capacity bids must supply downward_price_eur_mw_h")


@dataclass(frozen=True)
class DayAheadCase:
    """Dedicated day-ahead energy case. No activation profile or capacity bid."""

    market: Literal["da"] = "da"

    def __post_init__(self) -> None:
        if self.market != "da":
            raise ConfigError("DayAheadCase.market must be 'da'")
        names = {item.name for item in fields(self)}
        if "activation_profile" in names or "capacity_bid" in names:
            raise ConfigError("DayAheadCase must not contain activation_profile or capacity_bid")


@dataclass(frozen=True)
class MFRRCase:
    """Dedicated mFRR case with independent activation profile and capacity bid."""

    activation_profile: ActivationProfile = "balanced"
    capacity_bid: CapacityBid = field(default_factory=HistoricalQuantileCapacityBid)
    capacity_coverage_hours: float = 4.0
    market: Literal["mfrr"] = "mfrr"

    def __post_init__(self) -> None:
        if self.market != "mfrr":
            raise ConfigError("MFRRCase.market must be 'mfrr'")
        _require_activation_profile(self.activation_profile)
        hours = _require_finite(self.capacity_coverage_hours, "capacity_coverage_hours")
        if hours <= 0:
            raise ConfigError("capacity_coverage_hours must be > 0")
        _validate_capacity_bid(self.capacity_bid, "mfrr")


@dataclass(frozen=True)
class AFRRCase:
    """Dedicated aFRR case with independent activation profile and capacity bid."""

    activation_profile: ActivationProfile = "balanced"
    capacity_bid: CapacityBid = field(default_factory=HistoricalQuantileCapacityBid)
    capacity_coverage_hours: float = 4.0
    up_capacity_fraction: float = 1.0
    market: Literal["afrr"] = "afrr"

    def __post_init__(self) -> None:
        if self.market != "afrr":
            raise ConfigError("AFRRCase.market must be 'afrr'")
        _require_activation_profile(self.activation_profile)
        hours = _require_finite(self.capacity_coverage_hours, "capacity_coverage_hours")
        if hours <= 0:
            raise ConfigError("capacity_coverage_hours must be > 0")
        fraction = _require_finite(self.up_capacity_fraction, "up_capacity_fraction")
        if not 0.0 <= fraction <= 1.0:
            raise ConfigError("up_capacity_fraction must be in [0, 1]")
        _validate_capacity_bid(self.capacity_bid, "afrr")


MarketCase = DayAheadCase | MFRRCase | AFRRCase


@dataclass(frozen=True)
class MachineCommitmentConfig:
    """Optional physical machine-commitment assumptions.

    Disabled by default. Any enabled assumption builds a MILP; with every
    option off, the existing continuous LP is unchanged. Binary commitment
    variables are an implementation consequence, not a separate user setting.
    """

    fixed_speed_pump: bool = False
    turbine_minimum_output_fraction: float = 0.0
    forbid_simultaneous_operation: bool = False

    def __post_init__(self) -> None:
        _require_bool(self.fixed_speed_pump, "fixed_speed_pump")
        _require_bool(self.forbid_simultaneous_operation, "forbid_simultaneous_operation")
        fraction = _require_finite(
            self.turbine_minimum_output_fraction, "turbine_minimum_output_fraction"
        )
        if not 0.0 <= fraction <= 1.0:
            raise ConfigError("turbine_minimum_output_fraction must be in [0, 1]")

    def turbine_minimum_active(self) -> bool:
        return self.turbine_minimum_output_fraction > 0.0

    def physically_active(self) -> bool:
        return (
            self.fixed_speed_pump
            or self.forbid_simultaneous_operation
            or self.turbine_minimum_active()
        )

    def needs_pump_commitment(self) -> bool:
        return self.fixed_speed_pump or self.forbid_simultaneous_operation

    def needs_turbine_commitment(self) -> bool:
        return self.turbine_minimum_active() or self.forbid_simultaneous_operation


@dataclass(frozen=True)
class BelgianDeliveryPeriod:
    """Inclusive Belgian delivery-date range, converted via Europe/Brussels."""

    start_date: date
    end_date_inclusive: date

    def __post_init__(self) -> None:
        start = _require_plain_date(self.start_date, "start_date")
        end = _require_plain_date(self.end_date_inclusive, "end_date_inclusive")
        if end < start:
            raise ConfigError("Belgian delivery period is reversed or empty")
        start_utc = brussels_midnight_utc(start)
        end_utc = brussels_midnight_utc(_exclusive_delivery_end_date(end))
        if end_utc <= start_utc:
            raise ConfigError("Belgian delivery period is reversed or empty")
        _require_utc_qh(start_utc, "start_date")
        _require_utc_qh(end_utc, "end_date_inclusive")

    def to_utc_bounds(self) -> tuple[datetime, datetime]:
        start = brussels_midnight_utc(self.start_date)
        end = brussels_midnight_utc(_exclusive_delivery_end_date(self.end_date_inclusive))
        return start, end


@dataclass(frozen=True)
class UtcPeriod:
    """Exact half-open UTC interval ``[start_utc, end_exclusive_utc)``."""

    start_utc: datetime
    end_exclusive_utc: datetime

    def __post_init__(self) -> None:
        start = _require_utc_qh(self.start_utc, "start_utc")
        end = _require_utc_qh(self.end_exclusive_utc, "end_exclusive_utc")
        if end <= start:
            raise ConfigError("UTC period is reversed or empty")
        object.__setattr__(self, "start_utc", start)
        object.__setattr__(self, "end_exclusive_utc", end)

    def to_utc_bounds(self) -> tuple[datetime, datetime]:
        return self.start_utc, self.end_exclusive_utc


Period = BelgianDeliveryPeriod | UtcPeriod


@dataclass(frozen=True, kw_only=True)
class SimulationConfig:
    """One asset, site, explicit period, and dedicated market case."""

    period: Period
    market_case: MarketCase
    asset: AssetConfig = field(default_factory=AssetConfig)
    site: SiteConfig = field(default_factory=SiteConfig)
    machine_commitment: MachineCommitmentConfig = field(
        default_factory=MachineCommitmentConfig
    )

    def __post_init__(self) -> None:
        if not isinstance(self.period, (BelgianDeliveryPeriod, UtcPeriod)):
            raise ConfigError("period must be a BelgianDeliveryPeriod or UtcPeriod")
        if not isinstance(self.market_case, (DayAheadCase, MFRRCase, AFRRCase)):
            raise ConfigError("market_case must be a DayAheadCase, MFRRCase, or AFRRCase")
        if not isinstance(self.asset, AssetConfig):
            raise ConfigError("asset must be an AssetConfig")
        if not isinstance(self.site, SiteConfig):
            raise ConfigError("site must be a SiteConfig")
        if not isinstance(self.machine_commitment, MachineCommitmentConfig):
            raise ConfigError("machine_commitment must be a MachineCommitmentConfig")

    def pv_enabled(self) -> bool:
        return self.site.pv_ac_kw > 0.0

    def effective_grid_import_mw(self) -> float:
        if self.site.grid_import_mw is not None:
            return self.site.grid_import_mw
        return self.asset.power_pump_mw

    def effective_grid_export_mw(self) -> float:
        if self.site.grid_export_mw is not None:
            return self.site.grid_export_mw
        return self.asset.power_turbine_mw

    @property
    def market(self) -> Literal["da", "mfrr", "afrr"]:
        return self.market_case.market
