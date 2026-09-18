"""Reporting schema, artifact names, and period-summary columns."""

from __future__ import annotations

from stepinbel.optimizer.types import DISPATCH_COLUMNS, DISPATCH_COLUMNS_V3
import pyarrow as pa

RUN_ARTIFACT_SCHEMA_VERSION = 1
RUN_ARTIFACT_SCHEMA_VERSION_V2 = 2
RUN_ARTIFACT_SCHEMA_VERSION_V3 = 3
MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION = 1
MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION_V2 = 2
MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION_V3 = 3
ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION = 1
ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION_V2 = 2
ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION_V3 = 3

PUBLISHED_TABLE_STEMS: tuple[str, ...] = (
    "da_prices_qh",
    "balancing_qh",
    "capacity_blocks",
    "capacity_bids",
    "pv_profile_qh",
)
PUBLISHED_TABLE_STEMS_V3: tuple[str, ...] = (
    *PUBLISHED_TABLE_STEMS,
    "wind_profile_qh",
)

WIND_DISPATCH_COLUMNS: tuple[str, ...] = (
    "wind_available_mw",
    "wind_to_pump_mw",
    "wind_export_mw",
    "wind_curtail_mw",
    "wind_export_price_eur_mwh",
    "wind_revenue_eur",
)

DISPATCH_SCHEMA = pa.schema(
    [pa.field("datetime_utc", pa.timestamp("us", tz="UTC"))]
    + [pa.field(name, pa.float64()) for name in DISPATCH_COLUMNS[1:]]
)
DISPATCH_SCHEMA_V3 = pa.schema(
    [pa.field("datetime_utc", pa.timestamp("us", tz="UTC"))]
    + [pa.field(name, pa.float64()) for name in DISPATCH_COLUMNS_V3[1:]]
)

REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "run_request.json",
    "resolved_config.json",
    "dispatch.parquet",
    "dispatch.csv",
    "capacity.parquet",
    "capacity.csv",
    "summary.json",
    "summary.csv",
    "monthly_summary.parquet",
    "monthly_summary.csv",
    "yearly_summary.parquet",
    "yearly_summary.csv",
    "run_metadata.json",
    "report.txt",
    "run_status.json",
    "run_events.jsonl",
    "run.log",
    "artifact_manifest.json",
)

__all__ = [
    "RUN_ARTIFACT_SCHEMA_VERSION",
    "RUN_ARTIFACT_SCHEMA_VERSION_V2",
    "RUN_ARTIFACT_SCHEMA_VERSION_V3",
    "MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION",
    "MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION_V2",
    "MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION_V3",
    "ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION",
    "ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION_V2",
    "ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION_V3",
    "REQUIRED_ARTIFACTS",
    "MANIFEST_EXCLUDED",
    "PERIOD_SUMMARY_COLUMNS",
    "DISPATCH_SUMMARY_FIELDS",
    "BRUSSELS_TZ",
]

MANIFEST_EXCLUDED = "artifact_manifest.json"

PERIOD_SUMMARY_COLUMNS: tuple[str, ...] = (
    "period",
    "interval_count",
    "duration_hours",
    "energy_gross_eur",
    "grid_charging_cost_eur",
    "market_energy_net_eur",
    "capacity_revenue_eur",
    "pv_revenue_eur",
    "total_site_revenue_eur",
    "pumped_mwh",
    "turbined_mwh",
    "full_cycles",
    "pv_available_mwh",
    "pv_self_consumed_mwh",
    "pv_exported_mwh",
    "pv_curtailed_mwh",
    "simultaneous_interval_count",
    "simultaneous_overlap_mwh",
    "simultaneous_interval_energy_net_eur",
)

DISPATCH_SUMMARY_FIELDS: tuple[str, ...] = (
    "interval_count",
    "duration_hours",
    "e_max_mwh",
    "reservoir_initial_mwh",
    "reservoir_final_mwh",
    "energy_gross_eur",
    "grid_charging_cost_eur",
    "market_energy_net_eur",
    "capacity_revenue_eur",
    "pv_revenue_eur",
    "total_site_revenue_eur",
    "pumped_mwh",
    "turbined_mwh",
    "pv_available_mwh",
    "pv_self_consumed_mwh",
    "pv_exported_mwh",
    "pv_curtailed_mwh",
    "simultaneous_interval_count",
    "simultaneous_pump_mwh",
    "simultaneous_turbine_mwh",
    "simultaneous_overlap_mwh",
    "n_pump_ramp_up_vars",
    "n_turbine_ramp_up_vars",
)

BRUSSELS_TZ = "Europe/Brussels"

COMPARISON_MARKETS: tuple[str, ...] = ("da", "mfrr", "afrr")

COMPARISON_TOP_LEVEL_FILES: tuple[str, ...] = (
    "comparison_request.json",
    "comparison_summary.json",
    "comparison_summary.csv",
    "comparison_metadata.json",
    "report.txt",
    "run_status.json",
    "run_events.jsonl",
    "run.log",
    "artifact_manifest.json",
)

COMPARISON_ROW_FIELDS: tuple[str, ...] = (
    "market",
    "case_run_id",
    "revenue_rank",
    "difference_from_highest_eur",
    "interval_count",
    "duration_hours",
    "e_max_mwh",
    "energy_gross_eur",
    "grid_charging_cost_eur",
    "market_energy_net_eur",
    "capacity_revenue_eur",
    "pv_revenue_eur",
    "total_site_revenue_eur",
    "pumped_mwh",
    "turbined_mwh",
    "full_cycles",
    "pv_available_mwh",
    "pv_self_consumed_mwh",
    "pv_exported_mwh",
    "pv_curtailed_mwh",
    "simultaneous_interval_count",
    "simultaneous_overlap_mwh",
    "simultaneous_interval_energy_net_eur",
)

COMPARISON_SUMMARY_KEYS: tuple[str, ...] = (
    "comparison_artifact_schema_version",
    "run_id",
    "state",
    "resolved_start_utc",
    "resolved_end_exclusive_utc",
    "interval_count",
    "duration_hours",
    "highest_revenue_market",
    "interpretation",
    "rows",
)

COMPARISON_INTERPRETATION = (
    "Each row is an alternative dedicated-market simulation. Revenues are not "
    "additive and do not represent simultaneous market participation."
)

def comparison_manifest_entries(markets: tuple[str, ...] | list[str] = COMPARISON_MARKETS) -> tuple[str, ...]:
    """Parent-manifest paths for the selected dedicated markets, sorted POSIX."""
    selected = tuple(market for market in COMPARISON_MARKETS if market in set(markets))
    return tuple(
        sorted(
            [
                *(name for name in COMPARISON_TOP_LEVEL_FILES if name != "artifact_manifest.json"),
                *(f"cases/{market}/artifact_manifest.json" for market in selected),
            ]
        )
    )


COMPARISON_MANIFEST_ENTRIES: tuple[str, ...] = comparison_manifest_entries(COMPARISON_MARKETS)

ASSET_SWEEP_TOP_LEVEL_FILES: tuple[str, ...] = (
    "asset_sweep_request.json",
    "asset_sweep_summary.json",
    "asset_sweep_summary.csv",
    "asset_sweep_metadata.json",
    "report.txt",
    "run_status.json",
    "run_events.jsonl",
    "run.log",
    "artifact_manifest.json",
)

ASSET_SWEEP_ROW_FIELDS: tuple[str, ...] = (
    "candidate_id",
    "candidate_label",
    "case_run_id",
    "market",
    "revenue_rank",
    "difference_from_highest_eur",
    "power_pump_mw",
    "power_turbine_mw",
    "storage_source",
    "storage_hours_basis",
    "configured_storage_hours",
    "configured_pond_energy_mwh",
    "e_max_mwh",
    "usable_energy_mwh",
    "grid_energy_to_fill_mwh",
    "charge_duration_h",
    "discharge_duration_h",
    "effective_grid_import_mw",
    "effective_grid_export_mw",
    "interval_count",
    "duration_hours",
    "energy_gross_eur",
    "grid_charging_cost_eur",
    "market_energy_net_eur",
    "capacity_revenue_eur",
    "pv_revenue_eur",
    "total_site_revenue_eur",
    "period_revenue_per_turbine_mw_eur",
    "period_revenue_per_usable_mwh_eur",
    "pumped_mwh",
    "turbined_mwh",
    "full_cycles",
    "pv_available_mwh",
    "pv_self_consumed_mwh",
    "pv_exported_mwh",
    "pv_curtailed_mwh",
    "simultaneous_interval_count",
    "simultaneous_overlap_mwh",
    "simultaneous_interval_energy_net_eur",
)

ASSET_SWEEP_SUMMARY_KEYS: tuple[str, ...] = (
    "asset_sweep_artifact_schema_version",
    "run_id",
    "state",
    "market",
    "resolved_start_utc",
    "resolved_end_exclusive_utc",
    "interval_count",
    "duration_hours",
    "candidate_count",
    "candidate_order",
    "highest_revenue_candidate_id",
    "highest_revenue_candidate_label",
    "grid_import_limit_mode",
    "grid_export_limit_mode",
    "interpretation",
    "rows",
)

ASSET_SWEEP_INTERPRETATION = (
    "Each row is an alternative asset configuration for one dedicated-market "
    "simulation. Revenues are not additive. Ranking uses modelled site revenue "
    "before asset costs and is not an investment recommendation."
)

ASSET_SWEEP_HIGHEST_WORDING = (
    "Highest modelled site revenue among the tested asset configurations."
)

ADDITIVE_PERIOD_FIELDS: tuple[str, ...] = (
    "interval_count",
    "duration_hours",
    "energy_gross_eur",
    "grid_charging_cost_eur",
    "market_energy_net_eur",
    "capacity_revenue_eur",
    "pv_revenue_eur",
    "total_site_revenue_eur",
    "pumped_mwh",
    "turbined_mwh",
    "pv_available_mwh",
    "pv_self_consumed_mwh",
    "pv_exported_mwh",
    "pv_curtailed_mwh",
    "simultaneous_interval_count",
    "simultaneous_overlap_mwh",
    "simultaneous_interval_energy_net_eur",
)

WIND_REVENUE_AND_ENERGY_FIELDS: tuple[str, ...] = (
    "wind_revenue_eur",
    "wind_available_mwh",
    "wind_self_consumed_mwh",
    "wind_exported_mwh",
    "wind_curtailed_mwh",
)


def is_wind_schema(version: int) -> bool:
    return version == 3


def dispatch_columns_for(version: int) -> tuple[str, ...]:
    return DISPATCH_COLUMNS_V3 if is_wind_schema(version) else DISPATCH_COLUMNS


def dispatch_schema_for(version: int) -> pa.Schema:
    return DISPATCH_SCHEMA_V3 if is_wind_schema(version) else DISPATCH_SCHEMA


def published_table_stems_for(version: int) -> tuple[str, ...]:
    return PUBLISHED_TABLE_STEMS_V3 if is_wind_schema(version) else PUBLISHED_TABLE_STEMS


def _insert_after(fields: tuple[str, ...], anchor: str, extra: tuple[str, ...]) -> tuple[str, ...]:
    index = fields.index(anchor) + 1
    return fields[:index] + extra + fields[index:]


def dispatch_summary_fields_for(version: int) -> tuple[str, ...]:
    if not is_wind_schema(version):
        return DISPATCH_SUMMARY_FIELDS
    fields = _insert_after(DISPATCH_SUMMARY_FIELDS, "pv_revenue_eur", ("wind_revenue_eur",))
    return _insert_after(
        fields,
        "pv_curtailed_mwh",
        (
            "wind_available_mwh",
            "wind_self_consumed_mwh",
            "wind_exported_mwh",
            "wind_curtailed_mwh",
        ),
    )


def period_summary_columns_for(version: int) -> tuple[str, ...]:
    if not is_wind_schema(version):
        return PERIOD_SUMMARY_COLUMNS
    fields = _insert_after(PERIOD_SUMMARY_COLUMNS, "pv_revenue_eur", ("wind_revenue_eur",))
    return _insert_after(
        fields,
        "pv_curtailed_mwh",
        (
            "wind_available_mwh",
            "wind_self_consumed_mwh",
            "wind_exported_mwh",
            "wind_curtailed_mwh",
        ),
    )


def comparison_row_fields_for(version: int) -> tuple[str, ...]:
    if not is_wind_schema(version):
        return COMPARISON_ROW_FIELDS
    fields = _insert_after(COMPARISON_ROW_FIELDS, "pv_revenue_eur", ("wind_revenue_eur",))
    return _insert_after(
        fields,
        "pv_curtailed_mwh",
        (
            "wind_available_mwh",
            "wind_self_consumed_mwh",
            "wind_exported_mwh",
            "wind_curtailed_mwh",
        ),
    )


def asset_sweep_row_fields_for(version: int) -> tuple[str, ...]:
    if not is_wind_schema(version):
        return ASSET_SWEEP_ROW_FIELDS
    fields = _insert_after(ASSET_SWEEP_ROW_FIELDS, "pv_revenue_eur", ("wind_revenue_eur",))
    return _insert_after(
        fields,
        "pv_curtailed_mwh",
        (
            "wind_available_mwh",
            "wind_self_consumed_mwh",
            "wind_exported_mwh",
            "wind_curtailed_mwh",
        ),
    )


def additive_period_fields_for(version: int) -> tuple[str, ...]:
    if not is_wind_schema(version):
        return ADDITIVE_PERIOD_FIELDS
    fields = _insert_after(ADDITIVE_PERIOD_FIELDS, "pv_revenue_eur", ("wind_revenue_eur",))
    return _insert_after(
        fields,
        "pv_curtailed_mwh",
        (
            "wind_available_mwh",
            "wind_self_consumed_mwh",
            "wind_exported_mwh",
            "wind_curtailed_mwh",
        ),
    )
