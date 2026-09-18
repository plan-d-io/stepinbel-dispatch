# Dedicated-market comparison

The public comparison workflow ranks two or three independent dedicated-market
alternatives under a shared period, asset, site, published-data bundle, and
solver options. Supported memberships are:

- da + mfrr
- da + afrr
- mfrr + afrr
- da + mfrr + afrr

A one-market selection is a one-case run, not a comparison. Input mapping
order is canonicalized to `da`, `mfrr`, `afrr`. That order also controls
execution, tie-breaking, artifacts, and the report.

Each alternative is a complete Brief 06 one-case run. Comparison logic ranks
the validated child summaries. It does not recalculate dispatch, add revenues
across markets, or co-optimize simultaneous participation.

## Python API

Build and execute a frozen request from `stepinbel.workflows`:

```python
from stepinbel.workflows import (
    build_market_comparison_request,
    execute_market_comparison,
)

request = build_market_comparison_request(
    {"da": da_config, "mfrr": mfrr_config, "afrr": afrr_config},
    data_directory,
    output_directory,
)
run = execute_market_comparison(request)
```

Schema versions:

- `MARKET_COMPARISON_REQUEST_SCHEMA_VERSION = 1` for ordinary LP comparisons.
  Version `2` is used when a child case enables machine commitment and wind
  is disabled. Version `3` is used whenever co-located wind is enabled.
- `MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION = 1`, `2`, or `3`, matching the
  request. Schema-v3 comparison rows add wind revenue and energy fields.

`validate_market_comparison_artifacts` independently reconstructs ranking from
the selected child `summary.json` files and the frozen comparison request. The
selected markets are the canonical keys of `case_requests`. Child schema
versions must match the parent. It does not reopen the published data
directory. A completed comparison directory may be relocated. Schema-v2
comparisons preserve each child's termination and MIP gap and record
`mip_termination_warning` when one or more children reached the time limit
outside the requested gap.

## Output tree

A successful parent directory contains the existing top-level files and one
child directory per selected market:

```text
comparison_request.json
comparison_summary.json
comparison_summary.csv
comparison_metadata.json
report.txt
run_status.json
run_events.jsonl
run.log
artifact_manifest.json
cases/
  <selected-market>/   # complete one-case audit directory
```

The `cases` directory contains exactly the selected children. The parent
manifest hashes every top-level file except itself, plus each selected child
`artifact_manifest.json`, using sorted POSIX relative paths. That hash chain is
parent manifest → child manifest → every child artifact. Source Parquet files
are not copied. An all-three request keeps the previous three-child tree.

## Ranking

The primary metric is unrounded `total_site_revenue_eur`:

```text
total_site_revenue_eur
  = market_energy_net_eur
  + capacity_revenue_eur
  + pv_revenue_eur
  + wind_revenue_eur
```

`wind_revenue_eur` is present on schema-v3 comparisons. On schema-v1 and
schema-v2 it is omitted; those totals remain energy net plus capacity plus PV.

Rows are sorted by descending total site revenue. Exact ties use canonical
order `da`, `mfrr`, `afrr` among the selected markets. Ranks are consecutive
from 1 through the selected count. The revenue difference is
`case total − highest total`. `full_cycles` is `turbined_mwh / e_max_mwh` and
is not annualized.

Each row is an alternative dedicated-market simulation. Revenues are not
additive and do not represent simultaneous market participation. The highest
modelled total revenue is a mathematical ranking, not an investment
recommendation.

## Failure and cancellation

An existing parent output directory is refused before cancellation is
consulted. Cancellation is cooperative: it is checked before every parent
stage, forwarded into every child `execute_case_run`, and checked again after
each child returns.

If a child fails or is cancelled, the parent records failed or cancelled
status, later selected markets are not executed, already completed children are
kept, and no valid completed comparison manifest is created. Those directories
are diagnostic material.

## Interpretation boundary

When day-ahead is selected, the report states that it has no applicable Elia
conformance reference. Child metadata remains authoritative for each selected
market’s methodology reference. Those documents are not exact
Watts.Happening-conformance claims. Combined markets and FCR remain outside
scope. Optional machine commitment, when enabled, is applied identically to
every dedicated-market child. Optional co-located wind, when enabled, is also
applied identically and writes schema-v3 child artifacts. The Streamlit
application presents the same public workflows.
