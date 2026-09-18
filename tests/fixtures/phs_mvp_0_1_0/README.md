# PHS MVP 0.1.0 parity fixtures

These are frozen regression fixtures. They compare current StepInBel results
with accepted PHS MVP 0.1.0 metadata.

The records come from the accepted PHS tag `phs-mvp-0.1.0`, commit
`82691dad676f85bfd345670fb8022de079ca5578`. They contain metadata only, not
complete dispatch outputs. Automated parity tests use them; they are not
runtime application data.

Dispatch CSVs, plots, reports, and solver outputs are excluded because parity
compares objective values and aggregates within stated tolerances. Identical
dispatch is not required when alternate optima exist.

These values are PHS-baseline parity evidence. They are not forecasts and they
are not a claim of exact Elia Watts.Happening conformance.

## Window semantics

`da_balanced_2025_utc_no_pv` uses the exact UTC window
`[2025-01-01 00:00 UTC, 2026-01-01 00:00 UTC)`, 35,040 quarter-hours. Its
source metadata labels the period as `common`; that label is not sufficient to
reproduce the run. The UTC window in `index.json` is authoritative.

The other three cases use Belgian delivery year 2025:
`[2024-12-31 23:00 UTC, 2025-12-31 23:00 UTC)`, also 35,040 quarter-hours.

The two day-ahead records therefore do not share a window. Their destination
filenames keep that distinction explicit.
