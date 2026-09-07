# One-case audit artifacts

A successful dedicated-market run writes exactly one new output directory. The
directory is refused if it already exists. It is never emptied, merged, or
overwritten. Source Parquet files are not copied into the run.

Schema versions:

- `CASE_RUN_REQUEST_SCHEMA_VERSION = 1`
- `RUN_ARTIFACT_SCHEMA_VERSION = 1`
- `RUN_STATUS_SCHEMA_VERSION = 1`
- `RUN_EVENT_SCHEMA_VERSION = 1`

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
`capacity_blocks`, `capacity_bids`, `pv_profile_qh`), the accepted PHS
baseline (`phs-mvp-0.1.0` at `82691dad676f85bfd345670fb8022de079ca5578`),
and the applicable Elia methodology filename and SHA-256 for mFRR or aFRR.
Those documents are methodology references, not exact
Watts.Happening-conformance claims. Day-ahead has no applicable Elia
conformance reference. FCR is not recorded.

Dedicated-market comparison writes a parent directory documented in
[COMPARISON.md](COMPARISON.md). The parent contains one child directory per
selected market. Each child remains a separate validated one-case run. Total
site revenue is energy net plus capacity plus PV. Rankings describe modelled
alternatives and are not additive.

Finite asset-parameter sweeps write a parent directory documented in
[SWEEPS.md](SWEEPS.md). Each candidate remains a separate validated one-case
run. Ranking uses modelled site revenue before asset costs and is not an
investment recommendation.
