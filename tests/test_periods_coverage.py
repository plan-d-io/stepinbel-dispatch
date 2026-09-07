from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from stepinbel.config import (
    AFRRCase,
    BelgianDeliveryPeriod,
    ConfigError,
    DayAheadCase,
    MFRRCase,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.data import (
    DataAccessError,
    coverage_from_bundle,
    open_published_bundle,
    resolve_period,
)
from stepinbel.data.coverage import UtcWindow, window_from_period


def test_belgian_delivery_year_2025_resolves_to_delivery_utc_window() -> None:
    period = BelgianDeliveryPeriod(date(2025, 1, 1), date(2025, 12, 31))
    window = window_from_period(period)
    assert window.start_utc == datetime(2024, 12, 31, 23, 0, tzinfo=timezone.utc)
    assert window.end_exclusive_utc == datetime(2025, 12, 31, 23, 0, tzinfo=timezone.utc)
    assert window.interval_count == 35040
    assert window.duration_hours == 8760.0
    assert window.last_interval_start_utc == datetime(
        2025, 12, 31, 22, 45, tzinfo=timezone.utc
    )


def test_utc_calendar_year_2025_resolves_to_utc_window() -> None:
    period = UtcPeriod(
        datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    window = window_from_period(period)
    assert window.start_utc == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert window.end_exclusive_utc == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert window.interval_count == 35040


def test_the_two_2025_period_forms_differ() -> None:
    delivery = window_from_period(
        BelgianDeliveryPeriod(date(2025, 1, 1), date(2025, 12, 31))
    )
    utc = window_from_period(
        UtcPeriod(
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    assert delivery.start_utc != utc.start_utc
    assert delivery.end_exclusive_utc != utc.end_exclusive_utc
    assert delivery.interval_count == utc.interval_count == 35040


def test_belgian_dst_days_have_92_and_100_quarter_hours() -> None:
    spring = window_from_period(
        BelgianDeliveryPeriod(date(2025, 3, 30), date(2025, 3, 30))
    )
    autumn = window_from_period(
        BelgianDeliveryPeriod(date(2025, 10, 26), date(2025, 10, 26))
    )
    assert spring.interval_count == 92
    assert autumn.interval_count == 100
    assert spring.duration_hours == 23.0
    assert autumn.duration_hours == 25.0


def test_naive_non_utc_unaligned_empty_and_reversed_utc_periods_fail() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 2, tzinfo=timezone.utc)
    with pytest.raises(ConfigError, match="timezone-naive"):
        UtcPeriod(datetime(2025, 1, 1), end)
    with pytest.raises(ConfigError, match="zero UTC offset"):
        UtcPeriod(datetime(2025, 1, 1, tzinfo=ZoneInfo("Europe/Brussels")), end)
    with pytest.raises(ConfigError, match="15-minute"):
        UtcPeriod(datetime(2025, 1, 1, 0, 1, tzinfo=timezone.utc), end)
    with pytest.raises(ConfigError, match="reversed or empty"):
        UtcPeriod(start, start)
    with pytest.raises(ConfigError, match="reversed or empty"):
        UtcPeriod(end, start)


def test_utc_window_rejects_non_datetime_bounds() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(DataAccessError, match="start_utc"):
        UtcWindow("2025-01-01T00:00:00Z", end)  # type: ignore[arg-type]
    with pytest.raises(DataAccessError, match="end_exclusive_utc"):
        UtcWindow(start, "2025-01-01T01:00:00Z")  # type: ignore[arg-type]


def test_datetime_masquerading_as_delivery_date_fails() -> None:
    with pytest.raises(ConfigError, match="datetime.date"):
        BelgianDeliveryPeriod(datetime(2025, 1, 1, tzinfo=timezone.utc), date(2025, 1, 1))


def test_published_coverage_uses_inclusive_last_qh_and_delivery_dates(
    data_root,
) -> None:
    coverage = coverage_from_bundle(open_published_bundle(data_root))
    assert coverage.da_prices.start_utc == datetime(2015, 1, 4, 23, 0, tzinfo=timezone.utc)
    assert coverage.da_prices.end_exclusive_utc == datetime(
        2026, 7, 13, 22, 0, tzinfo=timezone.utc
    )
    assert coverage.da_prices.last_interval_start_utc == datetime(
        2026, 7, 13, 21, 45, tzinfo=timezone.utc
    )
    assert coverage.balancing.start_utc == datetime(2024, 5, 21, 22, 0, tzinfo=timezone.utc)
    assert coverage.balancing.end_exclusive_utc == datetime(
        2026, 8, 13, 22, 0, tzinfo=timezone.utc
    )
    assert coverage.mfrr_capacity.start_utc == datetime(
        2020, 12, 31, 23, 0, tzinfo=timezone.utc
    )
    assert coverage.mfrr_capacity.end_exclusive_utc == datetime(
        2026, 8, 15, 22, 0, tzinfo=timezone.utc
    )
    assert coverage.afrr_capacity.start_utc == datetime(
        2022, 5, 3, 22, 0, tzinfo=timezone.utc
    )
    assert coverage.pv.start_utc == datetime(2020, 7, 31, 22, 0, tzinfo=timezone.utc)
    assert "Belgium" in coverage.pv_regions
    assert coverage.pv_regions == tuple(coverage.pv_regions)


def test_required_source_sets(data_root) -> None:
    bundle = open_published_bundle(data_root)
    period = UtcPeriod(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 15, 2, tzinfo=timezone.utc),
    )
    da = resolve_period(bundle, SimulationConfig(period=period, market_case=DayAheadCase()))
    assert da.required_sources == ("da_prices_qh",)
    mfrr = resolve_period(bundle, SimulationConfig(period=period, market_case=MFRRCase()))
    assert mfrr.required_sources == (
        "da_prices_qh",
        "balancing_qh",
        "capacity_blocks.mfrr",
    )
    afrr = resolve_period(bundle, SimulationConfig(period=period, market_case=AFRRCase()))
    assert afrr.required_sources == (
        "da_prices_qh",
        "balancing_qh",
        "capacity_blocks.afrr",
    )
    pv = resolve_period(
        bundle,
        SimulationConfig(
            period=period,
            market_case=DayAheadCase(),
            site=SiteConfig(pv_ac_kw=500.0, pv_region="Belgium"),
        ),
    )
    assert pv.required_sources == ("da_prices_qh", "pv_profile_qh")
    assert pv.pv_region == "Belgium"
    assert da.pv_region is None


def test_request_outside_coverage_names_binding_source_and_windows(data_root) -> None:
    bundle = open_published_bundle(data_root)
    period = UtcPeriod(
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2020, 1, 1, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(DataAccessError, match="balancing_qh") as caught:
        resolve_period(bundle, SimulationConfig(period=period, market_case=MFRRCase()))
    message = str(caught.value)
    assert "[2020-01-01T00:00:00Z, 2020-01-01T01:00:00Z)" in message
    assert "[2024-05-21T22:00:00Z, 2026-08-13T22:00:00Z)" in message
    assert "clip" not in message.lower()


def test_one_quarter_hour_end_overshoot_fails(data_root) -> None:
    bundle = open_published_bundle(data_root)
    coverage = coverage_from_bundle(bundle)
    overshoot = UtcPeriod(
        coverage.da_prices.end_exclusive_utc - timedelta(minutes=15),
        coverage.da_prices.end_exclusive_utc + timedelta(minutes=15),
    )
    with pytest.raises(DataAccessError, match="da_prices_qh") as caught:
        resolve_period(
            bundle, SimulationConfig(period=overshoot, market_case=DayAheadCase())
        )
    assert "2026-07-13T22:00:00Z" in str(caught.value)
    assert "2026-07-13T22:15:00Z" in str(caught.value)


def test_pv_region_matching(data_root) -> None:
    bundle = open_published_bundle(data_root)
    period = UtcPeriod(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 15, 1, tzinfo=timezone.utc),
    )
    resolved = resolve_period(
        bundle,
        SimulationConfig(
            period=period,
            market_case=DayAheadCase(),
            site=SiteConfig(pv_ac_kw=10.0, pv_region="belgium"),
        ),
    )
    assert resolved.pv_region == "Belgium"
    with pytest.raises(DataAccessError, match="pv_region"):
        resolve_period(
            bundle,
            SimulationConfig(
                period=period,
                market_case=DayAheadCase(),
                site=SiteConfig(pv_ac_kw=10.0, pv_region="Bel"),
            ),
        )
    with pytest.raises(DataAccessError, match="pv_region"):
        resolve_period(
            bundle,
            SimulationConfig(
                period=period,
                market_case=DayAheadCase(),
                site=SiteConfig(pv_ac_kw=10.0, pv_region="Atlantis"),
            ),
        )


def test_named_phs_period_strings_are_rejected() -> None:
    for label in ("common", "history", "modern", "custom"):
        with pytest.raises(ConfigError, match="BelgianDeliveryPeriod or UtcPeriod"):
            SimulationConfig(period=label, market_case=DayAheadCase())
