"""Shared Arrow/NumPy capacity and energy-bid helpers.

These helpers are internal to the markets package. They are not public exports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Mapping

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from stepinbel.config import FixedMinimumCapacityBid, HistoricalQuantileCapacityBid
from stepinbel.markets.base import MarketInputError
from stepinbel.optimizer.types import CapacityCommitment

ENERGY_PROFILE_QUANTILE: Mapping[str, float] = MappingProxyType(
    {"balanced": 0.50, "passive": 0.90}
)
_CAPACITY_COLUMNS: tuple[str, ...] = (
    "product",
    "direction",
    "block",
    "data_available",
    "marginal_price_eur_mw_h",
    "block_hours",
    "block_start_utc",
    "block_end_utc",
    "delivery_date_local",
)
_CONVERSION_ERRORS = (TypeError, ValueError, OverflowError, KeyError, pa.ArrowInvalid)
_QH = timedelta(minutes=15)
_QH_HOURS = 0.25


@dataclass(frozen=True)
class CollapsedCapacityBlock:
    """One collapsed (delivery date, block, direction) capacity record."""

    delivery_date_local: date
    block: str
    direction: str
    block_hours: float
    marginal_price_eur_mw_h: float
    block_start_utc: datetime
    block_end_utc: datetime


def collapse_capacity_blocks(
    table: pa.Table,
    *,
    product: str,
    direction: str = "up",
) -> tuple[CollapsedCapacityBlock, ...]:
    """Collapse published capacity rows to one record per local date/block."""
    if table.num_rows == 0:
        return ()
    _require_columns(table, _CAPACITY_COLUMNS, "capacity")
    try:
        products = _string_values(table, "product")
        directions = _string_values(table, "direction")
        blocks = _string_values(table, "block")
        available = table.column("data_available").to_pylist()
        marginals = _float_values(table, "marginal_price_eur_mw_h")
        hours = _float_values(table, "block_hours")
        starts = _utc_values(table, "block_start_utc")
        ends = _utc_values(table, "block_end_utc")
        dates = _local_dates(table, "delivery_date_local")
    except _CONVERSION_ERRORS as exc:
        if isinstance(exc, MarketInputError):
            raise
        raise MarketInputError(
            "capacity table cannot be converted into capacity bidding inputs"
        ) from exc

    groups: dict[tuple[date, str, str], list[int]] = {}
    for i in range(table.num_rows):
        if products[i] != product:
            continue
        if directions[i] != direction:
            continue
        if available[i] is not True:
            continue
        if not np.isfinite(marginals[i]):
            continue
        key = (dates[i], blocks[i], directions[i])
        groups.setdefault(key, []).append(i)

    collapsed: list[CollapsedCapacityBlock] = []
    for (delivery_date, block, direction_name), indices in groups.items():
        group_hours = [hours[i] for i in indices]
        if not group_hours or not all(np.isfinite(group_hours)):
            raise MarketInputError(
                f"capacity block {delivery_date.isoformat()} {block} {direction_name} "
                "has a missing or non-finite published duration"
            )
        unique_hours = {float(value) for value in group_hours}
        if len(unique_hours) != 1:
            raise MarketInputError(
                f"capacity block {delivery_date.isoformat()} {block} {direction_name} "
                "has inconsistent published block_hours"
            )
        block_hours = unique_hours.pop()
        if block_hours <= 0.0:
            raise MarketInputError(
                f"capacity block {delivery_date.isoformat()} {block} {direction_name} "
                "has a non-positive published duration"
            )
        group_starts = [starts[i] for i in indices]
        group_ends = [ends[i] for i in indices]
        if any(item is None for item in group_starts + group_ends):
            raise MarketInputError(
                f"capacity block {delivery_date.isoformat()} {block} {direction_name} "
                "has a null UTC boundary"
            )
        start = min(group_starts)  # type: ignore[type-var]
        end = max(group_ends)  # type: ignore[type-var]
        _require_qh_boundary(start, "block_start_utc")
        _require_qh_boundary(end, "block_end_utc")
        if end <= start:
            raise MarketInputError(
                f"capacity block {delivery_date.isoformat()} {block} {direction_name} "
                "ends before it starts"
            )
        collapsed.append(
            CollapsedCapacityBlock(
                delivery_date_local=delivery_date,
                block=block,
                direction=direction_name,
                block_hours=float(block_hours),
                marginal_price_eur_mw_h=float(max(marginals[i] for i in indices)),
                block_start_utc=start,
                block_end_utc=end,
            )
        )
    collapsed.sort(
        key=lambda item: (
            item.block_start_utc,
            item.direction,
            item.delivery_date_local.isoformat(),
            item.block,
        )
    )
    return tuple(collapsed)


def yearly_historical_capacity_bids(
    blocks: tuple[CollapsedCapacityBlock, ...],
    quantile: float,
) -> Mapping[int, float]:
    """Local-delivery-year linear quantiles of finite collapsed marginal prices."""
    by_year: dict[int, list[float]] = {}
    for block in blocks:
        price = float(block.marginal_price_eur_mw_h)
        if not np.isfinite(price):
            continue
        by_year.setdefault(block.delivery_date_local.year, []).append(price)
    out: dict[int, float] = {}
    for year, values in by_year.items():
        array = np.asarray(values, dtype=np.float64)
        out[year] = float(np.quantile(array, quantile)) if array.size else float("nan")
    return MappingProxyType(out)


def yearly_energy_bids(
    timestamps: tuple[datetime, ...],
    da_price: np.ndarray,
    activation_price: np.ndarray,
    activated: np.ndarray,
    *,
    quantile: float,
    eta_pump: float,
    eta_turbine: float,
) -> Mapping[int, float]:
    """UTC-year energy bids: max(activation quantile, stored-energy cost)."""
    cheapest = _avg_cheapest_8h_da_per_utc_year(timestamps, da_price)
    qmap = _yearly_activation_quantile(timestamps, activation_price, activated, quantile)
    rt = float(eta_pump) * float(eta_turbine)
    years = sorted(set(cheapest) | set(qmap))
    out: dict[int, float] = {}
    for year in years:
        average_cheap = cheapest.get(year, float("nan"))
        cost = float(average_cheap / rt) if np.isfinite(average_cheap) else float("nan")
        out[year] = _safe_max(qmap.get(year, float("nan")), cost)
    return MappingProxyType(out)


def _avg_cheapest_8h_da_per_utc_year(
    timestamps: tuple[datetime, ...],
    da_price: np.ndarray,
) -> dict[int, float]:
    by_day: dict[tuple[int, datetime], list[float]] = {}
    for stamp, price in zip(timestamps, da_price, strict=True):
        utc = _as_utc(stamp)
        day_key = (utc.year, utc.replace(hour=0, minute=0, second=0, microsecond=0))
        by_day.setdefault(day_key, []).append(float(price))
    yearly_means: dict[int, list[float]] = {}
    for (year, _day), prices in by_day.items():
        if len(prices) < 32:
            continue
        cheapest = np.partition(np.asarray(prices, dtype=np.float64), 31)[:32]
        yearly_means.setdefault(year, []).append(float(np.mean(cheapest)))
    return {
        year: float(np.mean(values)) if values else float("nan")
        for year, values in yearly_means.items()
    }


def _yearly_activation_quantile(
    timestamps: tuple[datetime, ...],
    price: np.ndarray,
    activated: np.ndarray,
    quantile: float,
) -> dict[int, float]:
    by_year: dict[int, list[float]] = {}
    for stamp, value, flag in zip(timestamps, price, activated, strict=True):
        if not bool(flag) or not np.isfinite(value):
            continue
        by_year.setdefault(_as_utc(stamp).year, []).append(float(value))
    out: dict[int, float] = {}
    for year, values in by_year.items():
        array = np.asarray(values, dtype=np.float64)
        out[year] = float(np.quantile(array, quantile)) if array.size else float("nan")
    return out


def _safe_max(quantile_value: float, cost: float) -> float:
    if np.isnan(quantile_value) and np.isnan(cost):
        return float("nan")
    if np.isnan(quantile_value):
        return float(cost)
    if np.isnan(cost):
        return float(quantile_value)
    return float(max(quantile_value, cost))


def _require_columns(table: pa.Table, names: tuple[str, ...], table_name: str) -> None:
    missing = [name for name in names if name not in table.column_names]
    if missing:
        raise MarketInputError(
            f"{table_name} table is missing required column {missing[0]}"
        )


def _string_values(table: pa.Table, name: str) -> list[str]:
    try:
        column = table.column(name)
        if pa.types.is_dictionary(column.type):
            column = pc.cast(column, pa.string())
        elif not pa.types.is_string(column.type) and not pa.types.is_large_string(column.type):
            column = pc.cast(column, pa.string())
        values = column.to_pylist()
    except _CONVERSION_ERRORS as exc:
        raise MarketInputError(
            f"capacity table column {name} cannot be converted to string"
        ) from exc
    out: list[str] = []
    for item in values:
        if item is None:
            raise MarketInputError(f"{name} contains a null value")
        out.append(str(item))
    return out


def _float_values(table: pa.Table, name: str) -> np.ndarray:
    try:
        column = table.column(name)
        values = np.array(column.to_numpy(zero_copy_only=False), dtype=np.float64, copy=True)
    except _CONVERSION_ERRORS as exc:
        raise MarketInputError(
            f"capacity table column {name} cannot be converted to float64"
        ) from exc
    return values


def _utc_values(table: pa.Table, name: str) -> list[datetime | None]:
    try:
        values = table.column(name).to_pylist()
    except _CONVERSION_ERRORS as exc:
        raise MarketInputError(
            f"capacity table column {name} cannot be converted to UTC timestamps"
        ) from exc
    out: list[datetime | None] = []
    for item in values:
        if item is None:
            out.append(None)
            continue
        if not isinstance(item, datetime):
            raise MarketInputError(f"{name} is not a timestamp")
        out.append(_as_utc(item))
    return out


def _local_dates(table: pa.Table, name: str) -> list[date]:
    try:
        values = table.column(name).to_pylist()
    except _CONVERSION_ERRORS as exc:
        raise MarketInputError(
            f"capacity table column {name} cannot be converted to local dates"
        ) from exc
    out: list[date] = []
    for item in values:
        if isinstance(item, datetime):
            out.append(item.date())
        elif isinstance(item, date):
            out.append(item)
        else:
            raise MarketInputError(f"{name} is not a local delivery date")
    return out


def _require_qh_boundary(value: datetime, field: str) -> None:
    utc = _as_utc(value)
    if utc.second != 0 or utc.microsecond != 0 or utc.minute % 15 != 0:
        raise MarketInputError(f"{field} is not aligned to a 15-minute UTC boundary")


def yearly_capacity_bids(
    collapsed: tuple[CollapsedCapacityBlock, ...],
    bid: HistoricalQuantileCapacityBid | FixedMinimumCapacityBid,
    *,
    direction: str,
) -> Mapping[int, float]:
    """Read-only local-delivery-year bids for one capacity direction."""
    years = sorted({block.delivery_date_local.year for block in collapsed})
    if isinstance(bid, HistoricalQuantileCapacityBid):
        historical = yearly_historical_capacity_bids(collapsed, float(bid.quantile))
        return MappingProxyType(
            {year: float(historical.get(year, float("nan"))) for year in years}
        )
    if isinstance(bid, FixedMinimumCapacityBid):
        if direction == "up":
            price = float(bid.upward_price_eur_mw_h)
        elif direction == "down":
            if bid.downward_price_eur_mw_h is None:
                raise MarketInputError(
                    "downward capacity bid requires downward_price_eur_mw_h"
                )
            price = float(bid.downward_price_eur_mw_h)
        else:
            raise MarketInputError(f"unsupported capacity direction {direction!r}")
        return MappingProxyType({year: price for year in years})
    raise MarketInputError("unsupported capacity bid")


def reject_partial_capacity_blocks(
    collapsed: tuple[CollapsedCapacityBlock, ...],
    window_start: datetime,
    window_end: datetime,
    *,
    market_label: str,
) -> None:
    start = _as_utc(window_start)
    end = _as_utc(window_end)
    for block in collapsed:
        overlaps = block.block_start_utc < end and block.block_end_utc > start
        contained = block.block_start_utc >= start and block.block_end_utc <= end
        if overlaps and not contained:
            raise MarketInputError(
                f"{market_label} requires complete capacity blocks; "
                f"block {block.delivery_date_local.isoformat()} {block.block} "
                f"{block.direction} "
                f"[{_fmt(block.block_start_utc)}, {_fmt(block.block_end_utc)}) "
                "overlaps the requested window but is not fully contained. "
                "The period was not moved, clipped, or prorated."
            )


def build_capacity_commitments(
    collapsed: tuple[CollapsedCapacityBlock, ...],
    timestamps: tuple[datetime, ...],
    yearly_cap: Mapping[int, float],
    *,
    product: str,
    cap_mw: float,
    coverage_hours: float,
    window_end: datetime,
) -> tuple[tuple[CapacityCommitment, ...], np.ndarray]:
    """Map collapsed blocks to LP commitments and a contracted quarter-hour mask."""
    n = len(timestamps)
    index = {_as_utc(stamp): i for i, stamp in enumerate(timestamps)}
    contracted = np.zeros(n, dtype=bool)
    commitments: list[CapacityCommitment] = []
    seen: set[str] = set()
    create = cap_mw > 0.0
    for block in collapsed:
        bid = float(yearly_cap.get(block.delivery_date_local.year, float("nan")))
        clears = np.isfinite(bid) and bid >= 0.0 and bid <= float(block.marginal_price_eur_mw_h)
        start_index, end_index = _span_indices(block, index, n, window_end)
        expected_hours = (end_index - start_index) * _QH_HOURS
        if abs(expected_hours - float(block.block_hours)) > 1e-12:
            raise MarketInputError(
                f"capacity block {block.delivery_date_local.isoformat()} {block.block} "
                f"{block.direction} spans {expected_hours:g} h but published "
                f"block_hours is {block.block_hours:g}"
            )
        if not clears:
            continue
        contracted[start_index:end_index] = True
        if not create:
            continue
        identifier = (
            f"{product}:{block.delivery_date_local.isoformat()}:"
            f"{block.block}:{block.direction}"
        )
        if identifier in seen:
            raise MarketInputError(f"duplicate {product} capacity identifier {identifier}")
        seen.add(identifier)
        commitments.append(
            CapacityCommitment(
                identifier=identifier,
                direction=block.direction,  # type: ignore[arg-type]
                start_index=start_index,
                end_index=end_index,
                price_eur_mw_h=bid,
                cap_max_mw=float(cap_mw),
                block_hours=float(block.block_hours),
                coverage_hours=float(coverage_hours),
            )
        )
    _require_same_direction_nonoverlapping(commitments, product=product)
    return tuple(commitments), contracted


def require_table_columns(table: pa.Table, names: tuple[str, ...], table_name: str) -> None:
    missing = [name for name in names if name not in table.column_names]
    if missing:
        raise MarketInputError(
            f"{table_name} table is missing required column {missing[0]}"
        )


def qh_timestamps(table: pa.Table, label: str) -> tuple[datetime, ...]:
    if "datetime_utc" not in table.column_names:
        raise MarketInputError(f"{label} table is missing datetime_utc")
    try:
        values = table.column("datetime_utc").to_pylist()
    except _CONVERSION_ERRORS as exc:
        raise MarketInputError(
            f"{label} table column datetime_utc cannot be converted"
        ) from exc
    out: list[datetime] = []
    previous: datetime | None = None
    for item in values:
        if not isinstance(item, datetime) or item.tzinfo is None:
            raise MarketInputError(f"{label} timestamps are missing, misaligned, or out of window")
        stamp = item.astimezone(timezone.utc)
        if previous is not None and stamp != previous + _QH:
            raise MarketInputError(f"{label} timestamps are missing, misaligned, or out of window")
        previous = stamp
        out.append(stamp)
    return tuple(out)


def finite_series(table: pa.Table, column: str, label: str) -> np.ndarray:
    if column not in table.column_names:
        raise MarketInputError(f"{label} is missing column {column}")
    try:
        values = np.array(
            table.column(column).to_numpy(zero_copy_only=False), dtype=np.float64, copy=True
        )
    except _CONVERSION_ERRORS as exc:
        raise MarketInputError(
            f"{label} column {column} cannot be converted to float64"
        ) from exc
    if values.ndim != 1:
        raise MarketInputError(f"{label} must be one-dimensional")
    if not np.all(np.isfinite(values)):
        raise MarketInputError(f"{label} contain null, NaN, or infinite values")
    return values


def float_series(table: pa.Table, column: str, table_name: str = "balancing") -> np.ndarray:
    if column not in table.column_names:
        raise MarketInputError(f"{table_name} table is missing {column}")
    try:
        return np.array(
            table.column(column).to_numpy(zero_copy_only=False), dtype=np.float64, copy=True
        )
    except _CONVERSION_ERRORS as exc:
        raise MarketInputError(
            f"{table_name} table column {column} cannot be converted to float64"
        ) from exc


def boolean_flags(table: pa.Table, column: str, table_name: str = "balancing") -> np.ndarray:
    if column not in table.column_names:
        raise MarketInputError(f"{table_name} table is missing {column}")
    try:
        values = table.column(column).to_pylist()
    except _CONVERSION_ERRORS as exc:
        raise MarketInputError(
            f"{table_name} table column {column} cannot be converted"
        ) from exc
    return np.array([item is True for item in values], dtype=bool)


def _span_indices(
    block: CollapsedCapacityBlock,
    index: dict[datetime, int],
    n: int,
    window_end: datetime,
) -> tuple[int, int]:
    start = _as_utc(block.block_start_utc)
    end = _as_utc(block.block_end_utc)
    if start not in index:
        raise MarketInputError(
            f"capacity block {block.delivery_date_local.isoformat()} {block.block} "
            f"{block.direction} start {_fmt(start)} is not a quarter-hour in the "
            "requested window"
        )
    start_index = index[start]
    window_end_utc = _as_utc(window_end)
    if end in index:
        end_index = index[end]
    elif end == window_end_utc:
        end_index = n
    else:
        raise MarketInputError(
            f"capacity block {block.delivery_date_local.isoformat()} {block.block} "
            f"{block.direction} end {_fmt(end)} is not a quarter-hour boundary in "
            "the requested window"
        )
    if end_index <= start_index:
        raise MarketInputError(
            f"capacity block {block.delivery_date_local.isoformat()} {block.block} "
            f"{block.direction} has an empty quarter-hour span"
        )
    return start_index, end_index


def _require_same_direction_nonoverlapping(
    commitments: list[CapacityCommitment],
    *,
    product: str,
) -> None:
    by_direction: dict[str, list[CapacityCommitment]] = {}
    for item in commitments:
        by_direction.setdefault(item.direction, []).append(item)
    for group in by_direction.values():
        ordered = sorted(group, key=lambda item: (item.start_index, item.end_index, item.identifier))
        previous: CapacityCommitment | None = None
        for item in ordered:
            if previous is not None and item.start_index < previous.end_index:
                raise MarketInputError(
                    f"{product} capacity commitments {previous.identifier} and "
                    f"{item.identifier} overlap"
                )
            previous = item


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise MarketInputError("timestamps must be timezone-aware UTC datetimes")
    return value.astimezone(timezone.utc)
