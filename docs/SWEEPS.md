# Finite asset-parameter sweeps

The public sweep workflow evaluates a frozen set of alternative `AssetConfig`
values for one dedicated market. Period, market case, site, PV, grid
configuration, published data, solver options, and software identity stay
fixed. Only the asset configuration changes.

Each candidate is a complete one-case run. Sweep logic ranks the validated
child summaries. It does not recalculate dispatch, add candidate revenues, or
search for a size.

## Python API

Build and execute a frozen request from `stepinbel.workflows`:

```python
from stepinbel.workflows import (
    build_asset_sweep_request,
    build_symmetric_asset_size_candidates,
    execute_asset_sweep,
)

candidates = build_symmetric_asset_size_candidates(
    asset_template,
    powers_mw=(1.0, 2.0),
    storage_hours=(2.0, 4.0),
)
request = build_asset_sweep_request(
    base_config,
    candidates,
    data_directory,
    output_directory,
)
run = execute_asset_sweep(request)
```

`base_config.asset` is a template only. It is not inserted as a candidate.
Asymmetric ratings, explicit pond energy, and other `AssetConfig` fields are
valid when you construct `AssetSweepCandidate` objects directly.

Schema versions:

- `ASSET_SWEEP_REQUEST_SCHEMA_VERSION = 1`
- `ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION = 1`
- `MAX_ASSET_SWEEP_CANDIDATES = 24`

One sweep covers exactly one market: day-ahead, mFRR, or aFRR. Do not mix
markets in one request. Dedicated-market comparison is a separate workflow
after you select one asset configuration.

`validate_asset_sweep_artifacts` reconstructs ranking from each child
`summary.json`, the frozen child configs, and existing asset and grid methods.
It does not reopen published data or call HiGHS. A completed directory may be
relocated.

## Output tree

A successful parent directory contains exactly:

```text
asset_sweep_request.json
asset_sweep_summary.json
asset_sweep_summary.csv
asset_sweep_metadata.json
report.txt
run_status.json
run_events.jsonl
run.log
artifact_manifest.json
cases/
  <candidate_id>/    # complete one-case audit directory
```

The parent manifest hashes every top-level file except itself, plus each child
`artifact_manifest.json`, using sorted POSIX relative paths. The hash chain is
parent manifest → child manifest → every child artifact. Source Parquet files
are not copied.

Each candidate keeps a full dispatch audit directory. The 24-candidate limit
exists because of that disk use. Candidates run sequentially. HiGHS runs are
not parallelized.

## Grid limits

`SiteConfig` is shared. If `grid_import_mw` is set, every candidate uses that
fixed import limit. Otherwise the effective import limit is that candidate’s
pump rating. Export uses `grid_export_mw` or the candidate’s turbine rating.
Summary metadata records `fixed_site_limit` or `candidate_pump_rating` /
`candidate_turbine_rating`. PV capacity stays fixed.

## Ranking

The primary metric is unrounded `total_site_revenue_eur`:

```text
total_site_revenue_eur
  = market_energy_net_eur
  + capacity_revenue_eur
  + pv_revenue_eur
```

Rows are sorted by descending total site revenue. Exact ties use frozen
candidate order. Ranks are consecutive from 1 through the candidate count.
The revenue difference is `candidate total − highest total`. `full_cycles` is
`turbined_mwh / e_max_mwh` and is not annualized.

Each row is an alternative asset configuration for one dedicated-market
simulation. Revenues are not additive. Ranking uses modelled site revenue
before asset costs and is not an investment recommendation. The workflow does
not compute CAPEX, NPV, payback, or a recommended size.

## Failure and cancellation

An existing parent output directory is refused before cancellation is
consulted. Cancellation is cooperative: it is checked before every parent
stage, forwarded into every child `execute_case_run`, and checked again after
each child returns.

If a child fails or is cancelled, the parent records failed or cancelled
status, later candidates are not executed, already completed children are
kept, and no valid completed sweep manifest is created. Those directories are
diagnostic material.

## Interpretation boundary

Day-ahead has no applicable Elia conformance reference. mFRR and aFRR reports
cite the accepted methodology filename and hash. Those documents are not exact
Watts.Happening-conformance claims. Combined markets, MILP, god, active, and
FCR remain outside scope. CLI, charts, and UI remain unbuilt.
