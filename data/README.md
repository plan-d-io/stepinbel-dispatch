# Published runtime data

This directory holds the approved PHS published-data bundle: five Parquet
tables and their `MANIFEST.json`. StepInBel treats these files as runtime
inputs only. It does not contain the PHS data-production pipeline and cannot
rebuild or refresh this vintage.

## Origin

Copied byte for byte from the accepted PHS tag `phs-mvp-0.1.0`, commit
`82691dad676f85bfd345670fb8022de079ca5578`, file set `data/`.

- Manifest SHA-256:
  `e13a1ab320201babf11aad8d047cb58079efa9e644a1a1b77fff16527a0a94a6`
- `pipeline_version`: `1.0.0`
- `built_at_utc`: `2026-08-14T17:18:21Z`
- Data-pipeline commit recorded in the manifest: `8208d06`
- `partial_build`: `false`

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
