# Published runtime data

This directory holds the approved published-data bundle: the original five
mandatory Parquet tables, the optional `wind_profile_qh` table, and their
`MANIFEST.json`. StepInBel treats these files as runtime inputs only. It does
not contain the PHS data-production pipeline and cannot rebuild or refresh
this vintage.

## Origin

Copied byte for byte from the accepted PHS pipeline bundle at commit
`2f071d22271101d0b102f7a306447c66d94033dd`, file set `data/`.

- Manifest SHA-256:
  `f74396e74c3f149b092be7529395e361afeb7811a2b7e641ed220c4b9d996c8d`
- `pipeline_version`: `1.0.0`
- `built_at_utc`: `2026-09-17T20:05:04Z`
- Data-pipeline commit recorded in the manifest: `2f071d2`
- `partial_build`: `false`

| File | SHA-256 | Rows |
|---|---|---|
| `MANIFEST.json` | `f74396e74c3f149b092be7529395e361afeb7811a2b7e641ed220c4b9d996c8d` | — |
| `da_prices_qh.parquet` | `2ee198b8829baf45b18c13fda8fac24f349a385c9021af1372bb40070aec2ca4` | 410396 |
| `balancing_qh.parquet` | `9bd5f40bb3b81086e9c1810da6ab2e03942c6088f3496d8e71c4b3ff229850b2` | 81408 |
| `capacity_blocks.parquet` | `ebc122484f46c2caaece1fdef69dda53961ef58d1a27604455da8f7b3679b8c5` | 63420 |
| `capacity_bids.parquet` | `819924867c73c348d223aef45fa8abd6bbcff28cf0189f53ae871be8250cdb7d` | 3779118 |
| `pv_profile_qh.parquet` | `fa5043887b8a61f0c0603d35f71a8f68f82e48a8fa36ed753a626ceadd9b9eb3` | 3007872 |
| `wind_profile_qh.parquet` | `0317c91d85f7185cb272f653ef46b9b495db1c16304c0200852c58d1aeabf917` | 941168 |

Wind coverage is `2019-12-31 23:00 UTC` through `2026-09-16 21:45 UTC`.
Profiles are `onshore_belgium`, `onshore_flanders`, `onshore_wallonia`, and
`offshore_belgium`.

The five original tables remain mandatory. `wind_profile_qh` is optional and
strictly validated when present. Older five-table bundles and frozen Demo
artifacts remain readable when wind is disabled.

## `pipeline_root`

`MANIFEST.json` records an absolute `pipeline_root` from the historical PHS
build machine. That path is provenance only. It is not a valid StepInBel
runtime location and must never be followed or required.

## Validation

Callers must supply the data directory explicitly to
`stepinbel.data.open_published_bundle`. The API validates hashes, row counts,
and ordered Parquet schemas. It does not infer the directory from the working
directory, source-tree parents, a PHS checkout, or `pipeline_root`.

## Licensing

Licensing, attribution, and redistribution rights for these market-data files
must be resolved before any distribution outside the authorized local
collaboration context. This repository does not yet declare a data licence.
