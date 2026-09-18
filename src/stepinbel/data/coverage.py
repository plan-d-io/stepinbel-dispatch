"""Manifest-derived coverage and strict explicit-period resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Mapping

from stepinbel.config import (
    AFRRCase,
    BelgianDeliveryPeriod,
    MFRRCase,
    Period,
    SimulationConfig,
    UtcPeriod,
)
from stepinbel.data.bundle import OPTIONAL_WIND_TABLE, PublishedDataBundle

_QH = timedelta(minutes=15)
_SOURCE_DA = "da_prices_qh"
_SOURCE_BALANCING = "balancing_qh"
_SOURCE_MFRR_CAPACITY = "capacity_blocks.mfrr"
_SOURCE_AFRR_CAPACITY = "capacity_blocks.afrr"
_SOURCE_PV = "pv_profile_qh"
_SOURCE_WIND = OPTIONAL_WIND_TABLE


class DataAccessError(Exception):
    """Coverage metadata or a selected data slice is incomplete or incompatible."""


def _fmt_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_utc_qh(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise DataAccessError(f"{field} must be a timezone-aware UTC datetime")
    if value.tzinfo is None:
        raise DataAccessError(f"{field} is timezone-naive")
    offset = value.utcoffset()
    if offset is None or offset != timedelta(0):
        raise DataAccessError(f"{field} must have a zero UTC offset")
    utc = value.astimezone(timezone.utc)
    if utc.second != 0 or utc.microsecond != 0 or utc.minute % 15 != 0:
        raise DataAccessError(f"{field} must lie on a 15-minute UTC boundary")
    return utc


@dataclass(frozen=True)
class UtcWindow:
    """Half-open ``[start_utc, end_exclusive_utc)`` UTC interval."""

    start_utc: datetime
    end_exclusive_utc: datetime

    def __post_init__(self) -> None:
        start = _require_utc_qh(self.start_utc, "start_utc")
        end = _require_utc_qh(self.end_exclusive_utc, "end_exclusive_utc")
        if end <= start:
            raise DataAccessError("UTC window must have positive duration")
        delta = end - start
        if delta % _QH != timedelta(0):
            raise DataAccessError("UTC window duration must be a multiple of 15 minutes")
        object.__setattr__(self, "start_utc", start)
        object.__setattr__(self, "end_exclusive_utc", end)

    @property
    def interval_count(self) -> int:
        return int((self.end_exclusive_utc - self.start_utc) / _QH)

    @property
    def duration_hours(self) -> float:
        return (self.end_exclusive_utc - self.start_utc).total_seconds() / 3600.0

    @property
    def last_interval_start_utc(self) -> datetime:
        return self.end_exclusive_utc - _QH

    def contains(self, other: UtcWindow) -> bool:
        return (
            self.start_utc <= other.start_utc
            and self.end_exclusive_utc >= other.end_exclusive_utc
        )


@dataclass(frozen=True)
class DataCoverage:
    """Published coverage windows derived only from validated manifest metadata."""

    da_prices: UtcWindow
    balancing: UtcWindow
    mfrr_capacity: UtcWindow
    afrr_capacity: UtcWindow
    pv: UtcWindow
    pv_regions: tuple[str, ...]
    wind: UtcWindow | None
    wind_profiles: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedPeriod:
    """Strictly covered explicit period. The run window equals the request."""

    period: Period
    window: UtcWindow
    market: Literal["da", "mfrr", "afrr"]
    required_sources: tuple[str, ...]
    pv_region: str | None
    wind_profile_id: str | None = None


def window_from_period(period: Period) -> UtcWindow:
    start, end = period.to_utc_bounds()
    return UtcWindow(start, end)


def coverage_from_bundle(bundle: PublishedDataBundle) -> DataCoverage:
    """Parse frozen coverage metadata without mutating the bundle."""
    tables = bundle.coverage
    wind: UtcWindow | None = None
    wind_profiles: tuple[str, ...] = ()
    if _SOURCE_WIND in tables or _SOURCE_WIND in bundle.tables:
        wind = _qh_coverage(tables, _SOURCE_WIND)
        wind_profiles = _wind_profiles(tables)
    return DataCoverage(
        da_prices=_qh_coverage(tables, _SOURCE_DA),
        balancing=_qh_coverage(tables, _SOURCE_BALANCING),
        mfrr_capacity=_capacity_coverage(tables, "mfrr"),
        afrr_capacity=_capacity_coverage(tables, "afrr"),
        pv=_qh_coverage(tables, _SOURCE_PV),
        pv_regions=_pv_regions(tables),
        wind=wind,
        wind_profiles=wind_profiles,
    )


def required_sources(config: SimulationConfig) -> tuple[str, ...]:
    sources: list[str] = [_SOURCE_DA]
    if isinstance(config.market_case, (MFRRCase, AFRRCase)):
        sources.append(_SOURCE_BALANCING)
    if isinstance(config.market_case, MFRRCase):
        sources.append(_SOURCE_MFRR_CAPACITY)
    if isinstance(config.market_case, AFRRCase):
        sources.append(_SOURCE_AFRR_CAPACITY)
    if config.pv_enabled():
        sources.append(_SOURCE_PV)
    if config.wind_enabled():
        sources.append(_SOURCE_WIND)
    return tuple(sources)


def resolve_period(
    bundle: PublishedDataBundle,
    config: SimulationConfig,
) -> ResolvedPeriod:
    """Resolve an explicit request and require complete input-data coverage."""
    coverage = coverage_from_bundle(bundle)
    window = window_from_period(config.period)
    sources = required_sources(config)
    available = {
        _SOURCE_DA: coverage.da_prices,
        _SOURCE_BALANCING: coverage.balancing,
        _SOURCE_MFRR_CAPACITY: coverage.mfrr_capacity,
        _SOURCE_AFRR_CAPACITY: coverage.afrr_capacity,
        _SOURCE_PV: coverage.pv,
        _SOURCE_WIND: coverage.wind,
    }
    for source in sources:
        if source in {_SOURCE_PV, _SOURCE_WIND}:
            continue
        _require_contained(window, available[source], source)
    pv_region: str | None = None
    if config.pv_enabled():
        _require_contained(window, coverage.pv, _SOURCE_PV)
        pv_region = resolve_pv_region(config.site.pv_region, coverage.pv_regions)
    wind_profile_id: str | None = None
    if config.wind_enabled():
        if coverage.wind is None:
            raise DataAccessError(
                "wind_profile_qh is required when wind generation is enabled"
            )
        _require_contained(window, coverage.wind, _SOURCE_WIND)
        wind_profile_id = resolve_wind_profile(
            config.site.wind_profile_id, coverage.wind_profiles
        )
    return ResolvedPeriod(
        period=config.period,
        window=window,
        market=config.market,
        required_sources=sources,
        pv_region=pv_region,
        wind_profile_id=wind_profile_id,
    )


def resolve_pv_region(region: str | None, available: tuple[str, ...]) -> str:
    if not isinstance(region, str) or not region:
        raise DataAccessError("pv_region is missing")
    if region in available:
        return region
    folded = region.casefold()
    matches = [name for name in available if name.casefold() == folded]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise DataAccessError(
            f"pv_region {region!r} matches more than one published region: {matches}"
        )
    raise DataAccessError(
        f"pv_region {region!r} is not a published region. Available: {list(available)}"
    )


def resolve_wind_profile(profile_id: str | None, available: tuple[str, ...]) -> str:
    if not isinstance(profile_id, str) or not profile_id:
        raise DataAccessError("wind_profile_id is missing")
    if profile_id in available:
        return profile_id
    folded = profile_id.casefold()
    matches = [name for name in available if name.casefold() == folded]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise DataAccessError(
            f"wind_profile_id {profile_id!r} matches more than one published profile: {matches}"
        )
    raise DataAccessError(
        f"wind_profile_id {profile_id!r} is not a published profile. Available: {list(available)}"
    )


def _require_contained(requested: UtcWindow, available: UtcWindow, source: str) -> None:
    if available.contains(requested):
        return
    raise DataAccessError(
        f"requested UTC window [{_fmt_utc(requested.start_utc)}, "
        f"{_fmt_utc(requested.end_exclusive_utc)}) is not fully covered by {source} "
        f"available [{_fmt_utc(available.start_utc)}, "
        f"{_fmt_utc(available.end_exclusive_utc)})"
    )


def _table_coverage(
    tables: Mapping[str, Mapping[str, object]], stem: str
) -> Mapping[str, object]:
    if stem not in tables:
        raise DataAccessError(f"coverage metadata is missing for table {stem}")
    entry = tables[stem]
    if not isinstance(entry, Mapping):
        raise DataAccessError(f"{stem} coverage metadata must be a mapping")
    return entry


def _qh_coverage(
    tables: Mapping[str, Mapping[str, object]], stem: str
) -> UtcWindow:
    entry = _table_coverage(tables, stem)
    if "coverage_utc" not in entry:
        raise DataAccessError(f"{stem} coverage_utc is missing")
    bounds = entry["coverage_utc"]
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
        raise DataAccessError(f"{stem} coverage_utc must be [first_qh, last_qh]")
    first = _parse_utc_instant(bounds[0], f"{stem}.coverage_utc[0]")
    last = _parse_utc_instant(bounds[1], f"{stem}.coverage_utc[1]")
    return UtcWindow(first, last + _QH)


def _capacity_coverage(
    tables: Mapping[str, Mapping[str, object]], product: Literal["mfrr", "afrr"]
) -> UtcWindow:
    stem = "capacity_blocks"
    field = f"{stem}.coverage_by_product.{product}"
    entry = _table_coverage(tables, stem)
    if "coverage_by_product" not in entry:
        raise DataAccessError(f"{stem} coverage_by_product is missing")
    by_product = entry["coverage_by_product"]
    if not isinstance(by_product, Mapping) or product not in by_product:
        raise DataAccessError(f"{field} is missing")
    bounds = by_product[product]
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
        raise DataAccessError(f"{field} must be [first_delivery_day, last_delivery_day]")
    first = _parse_delivery_date(bounds[0], f"{field}[0]")
    last = _parse_delivery_date(bounds[1], f"{field}[1]")
    if last < first:
        raise DataAccessError(f"{field} is reversed")
    start, end = BelgianDeliveryPeriod(first, last).to_utc_bounds()
    return UtcWindow(start, end)


def _pv_regions(tables: Mapping[str, Mapping[str, object]]) -> tuple[str, ...]:
    entry = _table_coverage(tables, _SOURCE_PV)
    if "regions" not in entry:
        raise DataAccessError(f"{_SOURCE_PV} regions is missing")
    regions = entry["regions"]
    if not isinstance(regions, (tuple, list)) or not regions:
        raise DataAccessError(f"{_SOURCE_PV} regions must be a non-empty sequence")
    if not all(isinstance(name, str) and name for name in regions):
        raise DataAccessError(f"{_SOURCE_PV} regions must be non-empty strings")
    return tuple(str(name) for name in regions)


def _wind_profiles(tables: Mapping[str, Mapping[str, object]]) -> tuple[str, ...]:
    entry = _table_coverage(tables, _SOURCE_WIND)
    if "profiles" not in entry:
        raise DataAccessError(f"{_SOURCE_WIND} profiles is missing")
    profiles = entry["profiles"]
    if not isinstance(profiles, (tuple, list)) or not profiles:
        raise DataAccessError(f"{_SOURCE_WIND} profiles must be a non-empty sequence")
    if not all(isinstance(name, str) and name for name in profiles):
        raise DataAccessError(f"{_SOURCE_WIND} profiles must be non-empty strings")
    return tuple(str(name) for name in profiles)


def _parse_utc_instant(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DataAccessError(f"{field} must be a UTC timestamp string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DataAccessError(f"{field} is not a valid timestamp") from exc
    return _require_utc_qh(parsed, field)


def _parse_delivery_date(value: object, field: str) -> date:
    if not isinstance(value, str) or not value:
        raise DataAccessError(f"{field} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataAccessError(f"{field} is not a valid ISO date") from exc
