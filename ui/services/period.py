"""Belgian delivery period helpers and published-data coverage."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Mapping, Sequence

from stepinbel.config import (
    AFRRCase,
    BelgianDeliveryPeriod,
    DayAheadCase,
    MFRRCase,
    SimulationConfig,
    SiteConfig,
)
from stepinbel.data import (
    DataAccessError,
    PublishedDataBundle,
    coverage_from_bundle,
    open_published_bundle,
    resolve_period,
)

from ui.services.form import PRESET_2025, PRESET_2026, PRESET_CUSTOM, selected_markets
from ui.services.paths import DATA_DIRECTORY

_MARKET_CASES = {
    "da": DayAheadCase,
    "mfrr": MFRRCase,
    "afrr": AFRRCase,
}


@lru_cache(maxsize=1)
def published_bundle() -> PublishedDataBundle:
    return open_published_bundle(DATA_DIRECTORY)


def parse_iso_date(value: object) -> date | None:
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def apply_period_preset(
    form: Mapping[str, object],
    *,
    markets: Sequence[str] | None = None,
    pv_enabled: bool | None = None,
    wind_enabled: bool | None = None,
) -> dict[str, object]:
    updated = dict(form)
    preset = str(updated.get("period_preset") or PRESET_2025)
    chosen = list(markets) if markets is not None else selected_markets(updated)
    pv = bool(updated.get("pv_enabled")) if pv_enabled is None else bool(pv_enabled)
    wind = bool(updated.get("wind_enabled")) if wind_enabled is None else bool(wind_enabled)
    if preset == PRESET_2025:
        updated["start_date"] = "2025-01-01"
        updated["end_date"] = "2025-12-31"
    elif preset == PRESET_2026:
        end = latest_inclusive_end(chosen, pv_enabled=pv, wind_enabled=wind, year=2026)
        updated["start_date"] = "2026-01-01"
        updated["end_date"] = end.isoformat()
    return updated


def latest_inclusive_end(
    markets: Sequence[str],
    *,
    pv_enabled: bool,
    wind_enabled: bool = False,
    year: int = 2026,
) -> date:
    if not markets:
        markets = ("da",)
    start = date(year, 1, 1)
    low = start
    high = date(year, 12, 31)
    best: date | None = None
    bundle = published_bundle()
    while low <= high:
        mid = low + timedelta(days=(high - low).days // 2)
        if _window_is_covered(bundle, markets, start, mid, pv_enabled, wind_enabled):
            best = mid
            low = mid + timedelta(days=1)
        else:
            high = mid - timedelta(days=1)
    if best is None:
        raise DataAccessError(
            f"No complete {year} Belgian delivery window for the selected markets."
        )
    return best


def _window_is_covered(
    bundle: PublishedDataBundle,
    markets: Sequence[str],
    start: date,
    end: date,
    pv_enabled: bool,
    wind_enabled: bool = False,
) -> bool:
    try:
        resolve_selected_period(
            markets,
            start,
            end,
            pv_enabled=pv_enabled,
            wind_enabled=wind_enabled,
            bundle=bundle,
        )
    except DataAccessError:
        return False
    return True


def resolve_selected_period(
    markets: Sequence[str],
    start: date,
    end: date,
    *,
    pv_enabled: bool,
    wind_enabled: bool = False,
    bundle: PublishedDataBundle | None = None,
):
    opened = bundle or published_bundle()
    period = BelgianDeliveryPeriod(start, end)
    site = SiteConfig(
        grid_import_mw=1.0,
        grid_export_mw=1.0,
        pv_ac_kw=500.0 if pv_enabled else 0.0,
        pv_region="Belgium",
        wind_capacity_kw=1000.0 if wind_enabled else 0.0,
        wind_profile_id="onshore_belgium",
    )
    resolved = None
    for market in markets:
        factory = _MARKET_CASES[market]
        config = SimulationConfig(period=period, market_case=factory(), site=site)
        resolved = resolve_period(opened, config)
    return resolved


def coverage_probe_available() -> bool:
    coverage_from_bundle(published_bundle())
    return True
