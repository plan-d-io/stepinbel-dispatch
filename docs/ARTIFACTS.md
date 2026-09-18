# One-case audit artifacts

A successful dedicated-market run writes exactly one new output directory. The
directory is refused if it already exists. It is never emptied, merged, or
overwritten. Source Parquet files are not copied into the run.

Schema versions:

- `CASE_RUN_REQUEST_SCHEMA_VERSION = 1` for ordinary LP runs. Version `2` is
  used when a machine-commitment option is enabled and wind is disabled.
  Version `3` is used whenever co-located wind is enabled, whether the run is
  a continuous LP or a MILP.
- `RUN_ARTIFACT_SCHEMA_VERSION = 1` for ordinary LP artifacts. Version `2`
  records MILP termination and gap metadata. A physically valid time-limited
  incumbent is a completed v2 result; it is not recorded as optimal or as
  accepted within the requested gap. Version `3` is the wind-enabled artifact
  contract. It has one fixed key set: machine-commitment and solver MIP fields
  are always present, and MIP-only diagnostics are explicitly null on
  continuous LP wind runs.
- `RUN_STATUS_SCHEMA_VERSION = 1`
- `RUN_EVENT_SCHEMA_VERSION = 1`

Existing schema-v1 LP requests and artifacts remain readable. Ordinary LP
runs still emit the unchanged v1 structure. A v1 request is never silently
reinterpreted as a MILP. See [MACHINE_COMMITMENT.md](MACHINE_COMMITMENT.md).

The authoritative completion condition is completed status plus a present and
valid `artifact_manifest.json`. A caller is notified of `verify_artifacts`
completion only after that manifest exists and `validate_run_artifacts` has
succeeded. No further artifact, status, event, or log writes occur after a
successful validation.

## Required files

A completed directory contains exactly these names:

- `run_request.json`
- `resolved_config.json`
- `dispatch.parquet`
- `dispatch.csv`
- `capacity.parquet`
- `capacity.csv`
- `summary.json`
- `summary.csv`
- `monthly_summary.parquet`
- `monthly_summary.csv`
- `yearly_summary.parquet`
- `yearly_summary.csv`
- `run_metadata.json`
- `report.txt`
- `run_status.json`
- `run_events.jsonl`
- `run.log`
- `artifact_manifest.json`

Temporary files and unexpected child entries, including subdirectories, are
forbidden after success. `validate_run_artifacts` reconstructs the accounting
from persisted dispatch and capacity tables. Hash agreement alone is not
sufficient.

## Tables

`dispatch.parquet` preserves the solver `DispatchResult.dispatch` schema,
column order, row order, values, and UTC timestamps. `dispatch.csv` is the
same table with explicit `Z` timestamps. Reporting columns are not added.

`capacity.parquet` preserves the solver capacity-result schema. A day-ahead
run writes a typed empty Parquet table and a header-only CSV.

Monthly and yearly summaries group every Belgian market by Europe/Brussels
delivery time. The monthly key is `YYYY-MM`. The yearly key is the local
calendar year. Capacity revenue is allocated to the Brussels period that
contains the capacity row's `start_index` dispatch timestamp. Partial periods
are not annualized. Validation rebuilds those tables from the persisted
dispatch and capacity files and the frozen E_max.

## Status and events

Stages run in this order: `validate_request`, `validate_data`, `load_data`,
`solve`, `write_artifacts`, `verify_artifacts`. Cancellation is cooperative
and is checked before each stage and immediately after solve, after the run
directory and journal exist. HiGHS is not interrupted while it is solving.
An existing output directory is refused before any cancellation callback is
consulted.

Every event is written to `run_events.jsonl`, `run.log`, and an atomic
`run_status.json` snapshot. A failed or cancelled run keeps the directory for
diagnosis. A failed directory may have a stale manifest; it is diagnostic
material, not a valid completed bundle.

## Provenance

`run_metadata.json` records the published-data manifest hash, pipeline
identity, expected and actual SHA-256 plus manifest and Parquet row counts
for every table in the published bundle (`da_prices_qh`, `balancing_qh`,
`capacity_blocks`, `capacity_bids`, `pv_profile_qh`, and `wind_profile_qh` on
schema-v3 wind-enabled runs), the accepted PHS
baseline (`phs-mvp-0.1.0` at `82691dad676f85bfd345670fb8022de079ca5578`),
and the applicable Elia methodology filename and SHA-256 for mFRR or aFRR.
Those documents are methodology references, not exact
Watts.Happening-conformance claims. Day-ahead has no applicable Elia
conformance reference. FCR is not recorded. Schema-v1 and schema-v2
artifacts keep the original five-table published-data identity. Old
schema-v1 and schema-v2 artifacts remain readable without migration.

Dedicated-market comparison writes a parent directory documented in
[COMPARISON.md](COMPARISON.md). The parent contains one child directory per
selected market. Each child remains a separate validated one-case run. Total
site revenue is energy net plus capacity plus PV export, and plus wind export
when wind is enabled. Rankings describe modelled alternatives and are not
additive.

Finite asset-parameter sweeps write a parent directory documented in
[SWEEPS.md](SWEEPS.md). Each candidate remains a separate validated one-case
run. Ranking uses modelled site revenue before asset costs and is not an
investment recommendation.

The Streamlit Data explorer can download the displayed week as CSV. That file
is generated in memory on request for the selected market and complete
displayed week. It is not written into the run directory, and chart zoom does
not change the downloaded rows.
