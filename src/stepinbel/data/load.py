"""Filtered PyArrow market-data slices for one explicit case and period."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

from stepinbel.config import AFRRCase, DayAheadCase, MFRRCase, SimulationConfig
from stepinbel.data.bundle import OPTIONAL_WIND_TABLE, PublishedDataBundle
from stepinbel.data.coverage import (
    DataAccessError,
    ResolvedPeriod,
    UtcWindow,
    resolve_period,
)

_QH = timedelta(minutes=15)

DA_COLUMNS: tuple[str, ...] = (
    "datetime_utc",
    "da_price_eur_mwh",
    "native_resolution",
    "upsampled_from_hourly",
)
BALANCING_COLUMNS: tuple[str, ...] = (
    "datetime_utc",
    "has_afrr_up",
    "has_afrr_down",
    "has_mfrr_up",
    "has_mfrr_down",
    "cbmp_afrr_up",
    "cbmp_afrr_down",
    "cbmp_mfrr_up",
    "cbmp_mfrr_down",
    "afrr_price_up_eur_mwh",
    "afrr_price_down_eur_mwh",
)
CAPACITY_COLUMNS: tuple[str, ...] = (
    "delivery_date_local",
    "block",
    "product",
    "direction",
    "auction_step",
    "block_start_utc",
    "block_end_utc",
    "block_hours",
    "data_available",
    "awarded_volume_mw",
    "marginal_price_eur_mw_h",
    "vwap_price_eur_mw_h",
)
PV_COLUMNS: tuple[str, ...] = (
    "datetime_utc",
    "region",
    "measured_mw",
    "monitored_capacity_mw",
    "load_factor",
)
WIND_COLUMNS: tuple[str, ...] = (
    "datetime_utc",
    "profile_id",
    "wind_type",
    "region",
    "measured_mw",
    "monitored_capacity_mw",
    "load_factor",
)


@dataclass(frozen=True)
class MarketDataSlice:
    """Case-specific Arrow inputs. Unused tables are ``None``, not empty."""

    config: SimulationConfig
    period: ResolvedPeriod
    manifest_sha256: str
    da_prices: pa.Table
    balancing: pa.Table | None
    capacity_blocks: pa.Table | None
    pv_profile: pa.Table | None
    wind_profile: pa.Table | None = None


def load_market_data(
    bundle: PublishedDataBundle,
    config: SimulationConfig,
) -> MarketDataSlice:
    """Validate the period and load only the selected case's input slice."""
    resolved = resolve_period(bundle, config)
    window = resolved.window
    da_prices = _load_qh_table(
        bundle.tables["da_prices_qh"].path,
        DA_COLUMNS,
        window,
        table_name="da_prices",
    )
    balancing: pa.Table | None = None
    capacity_blocks: pa.Table | None = None
    pv_profile: pa.Table | None = None
    wind_profile: pa.Table | None = None
    if isinstance(config.market_case, (MFRRCase, AFRRCase)):
        balancing = _load_qh_table(
            bundle.tables["balancing_qh"].path,
            BALANCING_COLUMNS,
            window,
            table_name="balancing",
        )
        product = "mfrr" if isinstance(config.market_case, MFRRCase) else "afrr"
        capacity_blocks = _load_capacity_blocks(
            bundle.tables["capacity_blocks"].path,
            product,
            window,
        )
    elif not isinstance(config.market_case, DayAheadCase):
        raise DataAccessError("unsupported market case")
    if resolved.pv_region is not None:
        pv_profile = _load_qh_table(
            bundle.tables["pv_profile_qh"].path,
            PV_COLUMNS,
            window,
            table_name="pv_profile",
            region=resolved.pv_region,
        )
    if resolved.wind_profile_id is not None:
        if OPTIONAL_WIND_TABLE not in bundle.tables:
            raise DataAccessError(
                "wind_profile_qh is required when wind generation is enabled"
            )
        wind_profile = _load_qh_table(
            bundle.tables[OPTIONAL_WIND_TABLE].path,
            WIND_COLUMNS,
            window,
            table_name="wind_profile",
            profile_id=resolved.wind_profile_id,
        )
    return MarketDataSlice(
        config=config,
        period=resolved,
        manifest_sha256=bundle.manifest_sha256,
        da_prices=da_prices,
        balancing=balancing,
        capacity_blocks=capacity_blocks,
        pv_profile=pv_profile,
        wind_profile=wind_profile,
    )


def _utc_scalar(value: datetime) -> pa.Scalar:
    return pa.scalar(value.astimezone(timezone.utc), type=pa.timestamp("us", tz="UTC"))


def _load_qh_table(
    path: object,
    columns: tuple[str, ...],
    window: UtcWindow,
    *,
    table_name: str,
    region: str | None = None,
    profile_id: str | None = None,
) -> pa.Table:
    filt = (pc.field("datetime_utc") >= _utc_scalar(window.start_utc)) & (
        pc.field("datetime_utc") < _utc_scalar(window.end_exclusive_utc)
    )
    if region is not None:
        filt = filt & (pc.field("region") == pa.scalar(region))
    if profile_id is not None:
        filt = filt & (pc.field("profile_id") == pa.scalar(profile_id))
    table = ds.dataset(path, format="parquet").to_table(columns=list(columns), filter=filt)
    if table.column_names != list(columns):
        raise DataAccessError(
            f"{table_name} columns {tuple(table.column_names)} do not match {columns}"
        )
    table = table.sort_by("datetime_utc")
    _require_complete_qh_grid(table, window, table_name)
    return table


def _load_capacity_blocks(path: object, product: str, window: UtcWindow) -> pa.Table:
    filt = (
        (pc.field("product") == pa.scalar(product))
        & (pc.field("block_end_utc") > _utc_scalar(window.start_utc))
        & (pc.field("block_start_utc") < _utc_scalar(window.end_exclusive_utc))
    )
    table = ds.dataset(path, format="parquet").to_table(
        columns=list(CAPACITY_COLUMNS), filter=filt
    )
    if table.column_names != list(CAPACITY_COLUMNS):
        raise DataAccessError(
            f"capacity_blocks columns {tuple(table.column_names)} do not match {CAPACITY_COLUMNS}"
        )
    table = table.append_column(
        "_direction_sort", pc.cast(table["direction"], pa.string())
    ).append_column("_step_sort", pc.cast(table["auction_step"], pa.string()))
    table = table.sort_by(
        [
            ("block_start_utc", "ascending"),
            ("_direction_sort", "ascending"),
            ("_step_sort", "ascending"),
        ]
    ).drop_columns(["_direction_sort", "_step_sort"])
    products = set(pc.cast(table["product"], pa.string()).to_pylist())
    if products and products != {product}:
        raise DataAccessError(f"capacity_blocks contains products other than {product!r}")
    return table


def _require_complete_qh_grid(
    table: pa.Table, window: UtcWindow, table_name: str
) -> None:
    expected = _expected_qh_grid(window)
    actual = table.column("datetime_utc")
    if actual.length() != len(expected):
        raise DataAccessError(
            f"{table_name} has {actual.length()} timestamps, expected {len(expected)} "
            f"for [{_fmt(window.start_utc)}, {_fmt(window.end_exclusive_utc)})"
        )
    unique = pc.unique(actual)
    if len(unique) != actual.length():
        raise DataAccessError(f"{table_name} contains duplicate timestamps")
    expected_array = pa.array(expected, type=pa.timestamp("us", tz="UTC"))
    if not pc.all(pc.equal(actual, expected_array)).as_py():
        raise DataAccessError(
            f"{table_name} timestamps are missing, misaligned, or out of window"
        )


def _expected_qh_grid(window: UtcWindow) -> list[datetime]:
    values: list[datetime] = []
    instant = window.start_utc
    while instant < window.end_exclusive_utc:
        values.append(instant)
        instant += _QH
    return values


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
