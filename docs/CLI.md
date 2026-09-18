# Command-line interface

The CLI is the supported automation and diagnostic surface. It translates
arguments into the existing immutable configuration and request objects, then
calls the public workflows. It does not reimplement period resolution, bidding,
market adaptation, optimization, revenue accounting, ranking, or artifact
validation.

The Streamlit application (`start.cmd` or `python -m streamlit run ui\app.py`)
calls the same public workflows.

Published data is not embedded in an installed wheel. Direct execution always
requires an explicit `--data-dir`.

## Installation

Use Python 3.13 or newer. From a clone of this repository:

```text
python -m pip install -e .
```

The two invocations are equivalent:

```text
stepinbel --help
python -m stepinbel --help
```

```text
stepinbel --version
python -m stepinbel --version
```

## Commands

1. `run` — one dedicated DA, mFRR, or aFRR case.
2. `compare` — independent dedicated-market alternatives under one period,
   asset, and site. Omit `--markets` for da, mfrr, and afrr, or pass two or
   three of those names.
3. `sweep` — one dedicated market, ranked by a symmetric power-by-duration
   grid.
4. `validate` — read-only artifact reconciliation.
5. `data-info` — read-only published-bundle coverage.

Comparison and sweep totals are alternatives. They are never additive revenue.
Sweep ranking excludes asset costs and is not an investment recommendation.

## Periods

Supply exactly one pair.

Belgian delivery dates, inclusive end:

```text
--delivery-start YYYY-MM-DD --delivery-end YYYY-MM-DD
```

Half-open UTC bounds, exclusive end, literal `Z` only:

```text
--utc-start YYYY-MM-DDTHH:MM:SSZ --utc-end YYYY-MM-DDTHH:MM:SSZ
```

Do not mix the pairs, omit one member, repeat a flag, or infer a timezone.
Each of `--delivery-start`, `--delivery-end`, `--utc-start`, and `--utc-end`
may appear at most once. There are no named period presets. The domain layer
still requires quarter-hour alignment and a positive duration. Incomplete
coverage is an error; the CLI does not clip to available data.

## Direct and frozen-request modes

`run`, `compare`, and `sweep` each accept either a full direct configuration or
a previously written frozen request:

```text
stepinbel run --request PATH
stepinbel compare --request PATH
stepinbel sweep --request PATH
```

A frozen request is authoritative. The CLI does not merge defaults into it and
does not rewrite the file. `--quiet` may accompany `--request` because it only
changes console presentation. Data, output, run ID, period, asset, site,
market, `--markets`, bidding, PV, wind, and solver flags are rejected with
`--request`. `--markets` may appear at most once. One name is not a
comparison. Flags for a market that was not selected are rejected.

Direct execution requires `--data-dir` and `--output-dir`. The request builders
resolve those paths. The CLI does not create the output directory first.
Existing output directories remain forbidden by the workflow.

Advanced asymmetric or explicit-pond sweep candidates are not a second CLI
schema. Build an `AssetSweepRequest` and execute it with `sweep --request`.

## Machine commitment

These flags are optional and off by default. Enabling any of the physical
flags builds a MILP. There is no separate LP/MILP mode switch.

```text
--fixed-speed-pump
--turbine-minimum-output-fraction 0.18
--forbid-simultaneous-operation
--mip-rel-gap 0.015
--mip-time-limit-s 900
```

`--turbine-minimum-output-fraction 0` disables the turbine floor. The CLI
success JSON reports `formulation` (`lp` or `milp`). A MILP also reports
`termination`, `requested_mip_gap`, and `achieved_mip_gap`. A time-limited
feasible incumbent is a successful exit (`0`) with a concise warning on
stderr and a `warning` field in the JSON. It is not described as optimal or
as accepted within the requested gap. Time limit without an incumbent,
infeasibility, and solver failure remain failed runs. See
[MACHINE_COMMITMENT.md](MACHINE_COMMITMENT.md).

## Output

Progress goes to stderr: one line per `RunEvent`, including run ID, stage
number and total, stage key, state, and message. `--quiet` suppresses those
lines only. A usable time-limited MILP warning is still written to stderr.

Successful completion writes exactly one compact JSON object and a newline to
stdout. Keys are sorted. Numbers remain numbers. Non-finite values are rejected
instead of being emitted.

Leave `--detailed-solver-output` disabled when a caller needs clean stdout.
When it is enabled, native HiGHS diagnostics may appear before the final JSON.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success, `--help`, or `--version` |
| 2 | Invalid arguments, configuration, request, published data, coverage, or artifact validation |
| 1 | Execution, optimizer, artifact writing, progress-callback, or unexpected internal failure |
| 130 | Cooperative cancellation |

Expected failures write one compact JSON object to stderr with `ok` false,
`error_category`, and `message`. They do not print a traceback.

The first Ctrl+C sets a cancellation flag. The workflows honour it at the
existing checkpoint after the current solve. HiGHS is not interrupted
directly. If the platform cannot install a SIGINT handler, a `KeyboardInterrupt`
is mapped to exit 130.

## Examples

Examples use PowerShell line continuation (a backtick with no trailing
characters). They are also valid as a single line.

Day-ahead, no PV:

```powershell
stepinbel run --market da `
  --delivery-start 2025-01-15 --delivery-end 2025-01-15 `
  --data-dir data --output-dir outputs/da-2025-01-15
```

mFRR with a fixed upward bid:

```powershell
stepinbel run --market mfrr `
  --delivery-start 2025-01-15 --delivery-end 2025-01-15 `
  --capacity-bid-mode fixed `
  --fixed-up-capacity-price-eur-mw-h 12.5 `
  --data-dir data --output-dir outputs/mfrr-fixed
```

aFRR with PV:

```powershell
stepinbel run --market afrr `
  --delivery-start 2025-01-15 --delivery-end 2025-01-15 `
  --pv-ac-kw 500 --pv-region Belgium --pv-revenue-mode da `
  --data-dir data --output-dir outputs/afrr-pv
```

`--pv-region` defaults to Belgium when omitted. Wind is optional and off by
default. `--wind-profile` selects one of `onshore_belgium` (default),
`offshore_belgium`, `onshore_flanders`, or `onshore_wallonia`.

Day-ahead with co-located wind:

```powershell
stepinbel run --market da `
  --delivery-start 2025-01-15 --delivery-end 2025-01-15 `
  --wind-capacity-kw 1000 --wind-profile onshore_belgium `
  --wind-revenue-mode da `
  --data-dir data --output-dir outputs/da-wind
```

Day-ahead with PV and co-located wind:

```powershell
stepinbel run --market da `
  --delivery-start 2025-01-15 --delivery-end 2025-01-15 `
  --pv-ac-kw 500 --pv-region Belgium --pv-revenue-mode da `
  --wind-capacity-kw 1000 --wind-profile onshore_belgium `
  --wind-revenue-mode da `
  --data-dir data --output-dir outputs/da-pv-wind
```

Day-ahead with optional machine operating constraints:

```powershell
stepinbel run --market da `
  --delivery-start 2025-01-15 --delivery-end 2025-01-15 `
  --fixed-speed-pump --turbine-minimum-output-fraction 0.18 `
  --forbid-simultaneous-operation --mip-rel-gap 0.015 --mip-time-limit-s 900 `
  --data-dir data --output-dir outputs/da-commitment
```

Dedicated-market comparison (all three markets):

```powershell
stepinbel compare `
  --delivery-start 2025-01-15 --delivery-end 2025-01-15 `
  --mfrr-activation-profile balanced `
  --afrr-activation-profile passive `
  --data-dir data --output-dir outputs/compare-2025-01-15
```

Selected-market comparison:

```powershell
stepinbel compare --markets da mfrr `
  --delivery-start 2025-01-15 --delivery-end 2025-01-15 `
  --data-dir data --output-dir outputs/compare-da-mfrr
```

Symmetric asset sweep:

```powershell
stepinbel sweep --market da `
  --delivery-start 2025-01-15 --delivery-end 2025-01-15 `
  --powers-mw 0.5,1,2 --storage-hours-grid 2,4,6 `
  --data-dir data --output-dir outputs/sweep-da
```

Artifact validation:

```text
stepinbel validate --kind run --directory outputs/da-2025-01-15
stepinbel validate --kind comparison --directory outputs/compare-2025-01-15
stepinbel validate --kind sweep --directory outputs/sweep-da
```

Data coverage inspection:

```text
stepinbel data-info --data-dir data
```

`data-info` lists each published table, coverage windows, PV regions, and the
optional wind coverage window and profile IDs when the bundle contains
`wind_profile_qh`. Older five-table bundles remain readable; wind coverage is
then null and the profile list is empty.
