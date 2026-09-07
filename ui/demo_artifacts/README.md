# Frozen all-market demonstration

This directory holds one independently validated dedicated-market comparison
for future Streamlit Demo mode. Demo mode must open the saved result
read-only. It must not run HiGHS, create another output directory, or modify
these files.

The validated comparison directory is authoritative. `index.json` is a
discovery record only.

## Configuration

- Markets, in canonical order: `da`, `mfrr`, `afrr`
- Period: Belgian delivery 2025-01-01 through 2025-12-31 inclusive
  (35,040 quarter-hours; 8,760 hours)
- Asset: 1 MW pump / 1 MW turbine, 4 h discharge-at-rated storage, accepted
  efficiencies `eta_pump=0.84` and `eta_turbine=0.90`, terminal SOC 0.5
- Site: 1 MW import and export, 500 kW Belgium PV valued at day-ahead prices
- Balancing: balanced activation; historical P50 capacity bids; 4 h coverage
- Solver: `SolverOptions(detailed_output=False)`
- Run identity: `demo-2025-all-markets-pv500`, created 2026-09-05T00:00:00Z

## Source identities

- StepInBel commit: `48cbd889f41d6f34fbbfab4c18c735d4c015c1a8`
- Published-data manifest SHA-256:
  `e13a1ab320201babf11aad8d047cb58079efa9e644a1a1b77fff16527a0a94a6`
- Parent `artifact_manifest.json`: 1916 bytes,
  SHA-256 `1ed0a0cd2592b98116fe02ccbbec19fdf814036305ada74e6ac79aae5fc48a56`

## Directory footprint

`stepinbel_2025_all_markets_pv500/` contains 63 files and 21,611,736 bytes.
The largest file is `cases/afrr/dispatch.csv` (5,550,086 bytes). Source
Parquet files are not copied into this directory.

## Validation

Read-only reconciliation, without solving:

```text
stepinbel validate --kind comparison --directory ui/demo_artifacts/stepinbel_2025_all_markets_pv500
```

The same check is `validate_market_comparison_artifacts` on that directory.
Each child under `cases/` remains a complete one-case audit directory.

## Warnings

These cases are dedicated-market alternatives. Their revenues are not additive
and do not represent simultaneous multi-market participation.

The results are historical perfect-foresight simulations, not forecasts. They
are not an investment recommendation and are not expected future revenue.

Absolute `data_directory` and `output_directory` values in the frozen request
are provenance from the original generation host. They do not need to exist
after relocation. Independent validation succeeds from this committed
directory without reopening source data.
