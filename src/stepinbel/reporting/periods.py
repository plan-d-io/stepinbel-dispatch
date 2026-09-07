"""Brussels monthly and yearly summaries from a solved case."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa

from stepinbel.optimizer import DispatchResult
from stepinbel.optimizer.types import DT_H, SIMULTANEOUS_TOL_MW
from stepinbel.reporting.constants import PERIOD_SUMMARY_COLUMNS
from stepinbel.reporting.io import ArtifactError

_BRUSSELS = ZoneInfo("Europe/Brussels")

MONTHLY_SUMMARY_SCHEMA = pa.schema(
    [
        pa.field("period", pa.string()),
        pa.field("interval_count", pa.int64()),
        pa.field("duration_hours", pa.float64()),
        pa.field("energy_gross_eur", pa.float64()),
        pa.field("grid_charging_cost_eur", pa.float64()),
        pa.field("market_energy_net_eur", pa.float64()),
        pa.field("capacity_revenue_eur", pa.float64()),
        pa.field("pv_revenue_eur", pa.float64()),
        pa.field("total_site_revenue_eur", pa.float64()),
        pa.field("pumped_mwh", pa.float64()),
        pa.field("turbined_mwh", pa.float64()),
        pa.field("full_cycles", pa.float64()),
        pa.field("pv_available_mwh", pa.float64()),
        pa.field("pv_self_consumed_mwh", pa.float64()),
        pa.field("pv_exported_mwh", pa.float64()),
        pa.field("pv_curtailed_mwh", pa.float64()),
        pa.field("simultaneous_interval_count", pa.int64()),
        pa.field("simultaneous_overlap_mwh", pa.float64()),
        pa.field("simultaneous_interval_energy_net_eur", pa.float64()),
    ]
)

YEARLY_SUMMARY_SCHEMA = pa.schema(
    [pa.field("period", pa.int64())] + list(MONTHLY_SUMMARY_SCHEMA)[1:]
)


def brussels_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ArtifactError("dispatch timestamps must be timezone-aware UTC")
    return value.astimezone(_BRUSSELS)


def brussels_month_key(value: datetime) -> str:
    local = brussels_local(value)
    return f"{local.year:04d}-{local.month:02d}"


def brussels_year_key(value: datetime) -> int:
    return brussels_local(value).year


def simultaneous_interval_energy_net_eur(result: DispatchResult) -> float:
    pump = np.asarray(result.dispatch.column("p_pump_mw").to_numpy(), dtype=np.float64)
    turbine = np.asarray(result.dispatch.column("p_turbine_mw").to_numpy(), dtype=np.float64)
    energy = np.asarray(
        result.dispatch.column("market_energy_net_eur").to_numpy(), dtype=np.float64
    )
    mask = (pump > SIMULTANEOUS_TOL_MW) & (turbine > SIMULTANEOUS_TOL_MW)
    return float(energy[mask].sum())


def _empty_bucket() -> dict[str, float]:
    return {
        "interval_count": 0.0,
        "duration_hours": 0.0,
        "energy_gross_eur": 0.0,
        "grid_charging_cost_eur": 0.0,
        "market_energy_net_eur": 0.0,
        "capacity_revenue_eur": 0.0,
        "pv_revenue_eur": 0.0,
        "pumped_mwh": 0.0,
        "turbined_mwh": 0.0,
        "pv_available_mwh": 0.0,
        "pv_self_consumed_mwh": 0.0,
        "pv_exported_mwh": 0.0,
        "pv_curtailed_mwh": 0.0,
        "simultaneous_interval_count": 0.0,
        "simultaneous_overlap_mwh": 0.0,
        "simultaneous_interval_energy_net_eur": 0.0,
    }


def _add_interval(bucket: dict[str, float], *, sell: float, buy: float, pump: float, pump_grid: float, turbine: float, energy_net: float, pv_rev: float, pv_available: float, pv_self: float, pv_export: float, pv_curtail: float) -> None:
    energy_gross = DT_H * sell * turbine
    charging = DT_H * buy * pump_grid
    simultaneous = pump > SIMULTANEOUS_TOL_MW and turbine > SIMULTANEOUS_TOL_MW
    bucket["interval_count"] += 1.0
    bucket["duration_hours"] += DT_H
    bucket["energy_gross_eur"] += energy_gross
    bucket["grid_charging_cost_eur"] += charging
    bucket["market_energy_net_eur"] += energy_net
    bucket["pv_revenue_eur"] += pv_rev
    bucket["pumped_mwh"] += pump * DT_H
    bucket["turbined_mwh"] += turbine * DT_H
    bucket["pv_available_mwh"] += pv_available * DT_H
    bucket["pv_self_consumed_mwh"] += pv_self * DT_H
    bucket["pv_exported_mwh"] += pv_export * DT_H
    bucket["pv_curtailed_mwh"] += pv_curtail * DT_H
    if simultaneous:
        bucket["simultaneous_interval_count"] += 1.0
        bucket["simultaneous_overlap_mwh"] += min(pump, turbine) * DT_H
        bucket["simultaneous_interval_energy_net_eur"] += energy_net


def _finalize_rows(
    buckets: dict[object, dict[str, float]],
    e_max_mwh: float,
    *,
    period_as_int: bool,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for period in sorted(buckets):
        bucket = buckets[period]
        energy_net = bucket["market_energy_net_eur"]
        capacity = bucket["capacity_revenue_eur"]
        pv_rev = bucket["pv_revenue_eur"]
        total = energy_net + capacity + pv_rev
        cycles = bucket["turbined_mwh"] / e_max_mwh
        rows.append(
            {
                "period": int(period) if period_as_int else str(period),
                "interval_count": int(bucket["interval_count"]),
                "duration_hours": float(bucket["duration_hours"]),
                "energy_gross_eur": float(bucket["energy_gross_eur"]),
                "grid_charging_cost_eur": float(bucket["grid_charging_cost_eur"]),
                "market_energy_net_eur": float(energy_net),
                "capacity_revenue_eur": float(capacity),
                "pv_revenue_eur": float(pv_rev),
                "total_site_revenue_eur": float(total),
                "pumped_mwh": float(bucket["pumped_mwh"]),
                "turbined_mwh": float(bucket["turbined_mwh"]),
                "full_cycles": float(cycles),
                "pv_available_mwh": float(bucket["pv_available_mwh"]),
                "pv_self_consumed_mwh": float(bucket["pv_self_consumed_mwh"]),
                "pv_exported_mwh": float(bucket["pv_exported_mwh"]),
                "pv_curtailed_mwh": float(bucket["pv_curtailed_mwh"]),
                "simultaneous_interval_count": int(bucket["simultaneous_interval_count"]),
                "simultaneous_overlap_mwh": float(bucket["simultaneous_overlap_mwh"]),
                "simultaneous_interval_energy_net_eur": float(
                    bucket["simultaneous_interval_energy_net_eur"]
                ),
            }
        )
    return rows


def _table_from_rows(rows: list[dict[str, object]], schema: pa.Schema) -> pa.Table:
    columns = {name: [row[name] for row in rows] for name in PERIOD_SUMMARY_COLUMNS}
    return pa.table(columns, schema=schema)


def build_period_summaries_from_tables(
    dispatch: pa.Table,
    capacity: pa.Table,
    e_max_mwh: float,
) -> tuple[pa.Table, pa.Table]:
    """Group persisted dispatch and capacity revenue by Europe/Brussels month and year."""
    timestamps = dispatch.column("datetime_utc").to_pylist()
    sell = np.asarray(dispatch.column("market_sell_price_eur_mwh").to_numpy(), dtype=np.float64)
    buy = np.asarray(dispatch.column("market_buy_price_eur_mwh").to_numpy(), dtype=np.float64)
    pump = np.asarray(dispatch.column("p_pump_mw").to_numpy(), dtype=np.float64)
    pump_grid = np.asarray(dispatch.column("p_pump_grid_mw").to_numpy(), dtype=np.float64)
    turbine = np.asarray(dispatch.column("p_turbine_mw").to_numpy(), dtype=np.float64)
    energy_net = np.asarray(dispatch.column("market_energy_net_eur").to_numpy(), dtype=np.float64)
    pv_rev = np.asarray(dispatch.column("pv_revenue_eur").to_numpy(), dtype=np.float64)
    pv_available = np.asarray(dispatch.column("pv_available_mw").to_numpy(), dtype=np.float64)
    pv_self = np.asarray(dispatch.column("pv_to_pump_mw").to_numpy(), dtype=np.float64)
    pv_export = np.asarray(dispatch.column("pv_export_mw").to_numpy(), dtype=np.float64)
    pv_curtail = np.asarray(dispatch.column("pv_curtail_mw").to_numpy(), dtype=np.float64)

    monthly: dict[str, dict[str, float]] = defaultdict(_empty_bucket)
    yearly: dict[int, dict[str, float]] = defaultdict(_empty_bucket)
    for i, stamp in enumerate(timestamps):
        month = brussels_month_key(stamp)
        year = brussels_year_key(stamp)
        kwargs = dict(
            sell=float(sell[i]),
            buy=float(buy[i]),
            pump=float(pump[i]),
            pump_grid=float(pump_grid[i]),
            turbine=float(turbine[i]),
            energy_net=float(energy_net[i]),
            pv_rev=float(pv_rev[i]),
            pv_available=float(pv_available[i]),
            pv_self=float(pv_self[i]),
            pv_export=float(pv_export[i]),
            pv_curtail=float(pv_curtail[i]),
        )
        _add_interval(monthly[month], **kwargs)
        _add_interval(yearly[year], **kwargs)

    if capacity.num_rows:
        starts = capacity.column("start_index").to_pylist()
        revenues = capacity.column("capacity_revenue_eur").to_pylist()
        for start_index, revenue in zip(starts, revenues, strict=True):
            if not isinstance(start_index, int) or isinstance(start_index, bool):
                raise ArtifactError("capacity start_index must be an integer")
            if start_index < 0 or start_index >= len(timestamps):
                raise ArtifactError("capacity start_index is outside the dispatch window")
            stamp = timestamps[start_index]
            monthly[brussels_month_key(stamp)]["capacity_revenue_eur"] += float(revenue)
            yearly[brussels_year_key(stamp)]["capacity_revenue_eur"] += float(revenue)

    monthly_rows = _finalize_rows(monthly, float(e_max_mwh), period_as_int=False)
    yearly_rows = _finalize_rows(yearly, float(e_max_mwh), period_as_int=True)
    return _table_from_rows(monthly_rows, MONTHLY_SUMMARY_SCHEMA), _table_from_rows(
        yearly_rows, YEARLY_SUMMARY_SCHEMA
    )


def build_period_summaries(result: DispatchResult) -> tuple[pa.Table, pa.Table]:
    """Group dispatch and capacity revenue by Europe/Brussels month and year."""
    if not isinstance(result, DispatchResult):
        raise ArtifactError("period summaries require a DispatchResult")
    return build_period_summaries_from_tables(
        result.dispatch,
        result.capacity_results,
        float(result.summary.e_max_mwh),
    )
