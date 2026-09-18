# Target architecture

## Design principle

Port the accepted PHS domain behaviour behind clean public boundaries and keep
its methodological provenance visible. Do not make the existing PHS project or
Battery dispatch project a runtime dependency.

The new repository should use six layers.

## 1. Behavioural provenance

A versioned index records accepted PHS baseline behaviour, exact Elia source
documents, checksums, applicable markets, known differences, and later
StepInBel decisions. Tests link to stable requirement identifiers rather than
relying on prose remembered from a chat.

The source PDFs need not be runtime inputs. Whether copies may be committed is a
licensing and provenance decision; an index with source locations and hashes is
sufficient until that decision is made.

## 2. Published data package

The repository will carry the approved Parquet files and their manifest as
runtime inputs. The application validates their hashes, schemas, timestamps,
coverage, and data vintage before a run.

The data-production pipeline remains outside the repository.

## 3. Domain core

The domain package owns:

- Immutable configuration and validation.
- Market-independent pumped-hydro physics.
- Day-ahead, mFRR, and aFRR adapters.
- Solver-neutral model inputs and result types.
- HiGHS and optional differential solver backends.
- Revenue, operational, and provenance calculations.

The implemented core boundary today includes:

- Frozen asset, site, market-case, bidding, and period objects. Invalid
  market/strategy combinations are unrepresentable or rejected immediately.
- Explicit Belgian delivery-day ranges and half-open UTC intervals. Named PHS
  period presets are not accepted.
- Manifest-derived coverage with strict containment. Incomplete coverage is an
  error; the runtime does not clip or invent a different run window.
- Filtered PyArrow tables for the selected case only, with projected columns,
  deterministic ordering, and a complete quarter-hour UTC grid.
- The shared continuous LP, optional machine-commitment MILP, HiGHS
  production backend, and the day-ahead, mFRR, and aFRR adapters with
  optional co-located PV and wind.

The LP and optional MILP are specified in [MODEL.md](MODEL.md). HiGHS is the
only backend. The one-case workflow and its audit-artifact contract are
implemented. Dedicated DA/mFRR/aFRR comparison is implemented as two or three
independent validated one-case runs. Finite asset-parameter sweeps are
implemented as sequential one-case runs under one dedicated market. Combined
or co-optimized markets remain unbuilt. The command-line interface is
implemented. The Streamlit application (`ui/app.py`) is the supported
interactive interface. An Elia
reference does not authorize an unreviewed change to the signed-off
baseline.

## 4. Workflows

Public workflow services coordinate:

- One-case evaluation (implemented).
- Dedicated-market comparison (implemented).
- Finite parameter sweeps (implemented).
- Progress events, cancellation, failure reporting, and artifact validation
  (implemented for one-case, comparison, and sweep paths).

Each workflow freezes its request before solving. The command line and user
interface call these services instead of importing private model modules.

The implemented `stepinbel.workflows` boundary accepts a frozen
`CaseRunRequest` or `MarketComparisonRequest`. Comparison executes the selected
dedicated markets through `execute_case_run` and returns a
`MarketComparisonRun` only after the parent directory validates. Rankings
describe modelled alternatives
and are not additive. Sweep services accept a frozen `AssetSweepRequest`,
execute each candidate through `execute_case_run`, and return an
`AssetSweepRun` after the parent directory validates.

## 5. Audit artifacts

Every completed one-case run writes one self-contained output directory. The
implemented contract is specified in [ARTIFACTS.md](ARTIFACTS.md). It includes:

- Frozen request and resolved configuration.
- Status, progress events, and a human-readable log.
- Dispatch, capacity, summary, monthly, and yearly tables.
- Solver metadata, feasibility, data-manifest hashes, and the accepted PHS
  behavioural baseline.
- A deterministic text report and a hashed artifact manifest.

Charts and ZIP packages remain unbuilt. Dedicated-market comparison writes a
parent directory documented in [COMPARISON.md](COMPARISON.md). Finite
asset-parameter sweeps write a parent directory documented in
[SWEEPS.md](SWEEPS.md). Artifact schemas are versioned independently from the
user interface.

## 6. Interfaces

The command line is the implemented automation and diagnostic interface. It
constructs the existing public request objects and calls `execute_case_run`,
`execute_market_comparison`, and `execute_asset_sweep`. The Streamlit
application (`ui/app.py`) is the supported interactive interface. Direct
execution requires an explicit `--data-dir`; published data is not shipped
inside an installed wheel.

The Streamlit product uses three user-facing stages:

1. Configure
2. Review & run
3. Results

During an active run, stage 2 is the working/progress view. Optional
co-located wind is configured after PV and stays off by default. Co-located PV
exposes a regional profile selector with Belgium as the default. Optional
machine operating constraints remain under Advanced asset assumptions. Results
include Overview, Market detail, Data explorer, Technical details, and
Downloads. Data explorer can download the displayed week as CSV for the
selected market and complete displayed week, independently of chart zoom. The
interface must label dedicated-market results as alternatives, not additive
revenue streams.

## Proposed repository layout

```text
StepInBel/
|-- README.md
|-- configs/
|-- data/                    # Published runtime Parquet and manifest only
|-- docs/
|   `-- methodology/        # Reference index and approved adaptations
|-- prompts/                 # Accepted implementation briefs and context
|-- src/stepinbel/
|   |-- config/
|   |-- data/
|   |-- markets/
|   |-- optimizer/
|   |-- reporting/
|   |-- workflows/
|   `-- cli/
|-- tests/
|-- ui/
`-- outputs/                 # Generated locally; not committed
```

This layout is a target. An implementation brief must authorize each directory
and public interface before production code is added.
