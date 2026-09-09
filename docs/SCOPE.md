# Scope

## Objective

Build an auditable and usable simulator that estimates the historical revenue
potential of a pumped-hydro storage asset on Belgian electricity markets.

For a selected period and asset configuration, the simulator should answer:

- What revenue could the asset have earned if it had been dedicated to the
  day-ahead market?
- What revenue could it have earned if it had been dedicated to mFRR?
- What revenue could it have earned if it had been dedicated to aFRR?
- Which pumping, generating, reservoir, capacity, and optional PV flows produced
  each result?
- How do the independent market cases compare under the same physical and
  reporting assumptions?

Results are simulations under stated assumptions. They are not forecasts,
operating schedules, or a complete investment case.

## Behavioural and methodology boundary

The accepted PHS MVP defines the first-release behaviour to port for day-ahead,
mFRR, and aFRR. The implemented HiGHS day-ahead, mFRR, and aFRR paths reproduce agreed
PHS reference totals within stated tolerances. Dispatch equality is not required
when several optima exist. The committed PHS no-PV day-ahead metadata records
one zero-value simultaneous interval; that count is not a parity gate.

Elia's Watts.Happening mFRR and aFRR documents are methodology references. They
explain the origin of important balancing-market choices and help identify
assumptions, but exact conformance is not a first-release acceptance gate. Any
known difference must be documented; changing accepted PHS behaviour requires a
separate StepInBel decision and implementation brief.

The current PHS day-ahead dispatch is accepted as the StepInBel baseline even
where it differs from Elia's day-ahead simulator. The Elia day-ahead document is
context only. FCR is outside the StepInBel product boundary.

See [Behavioural authority](METHODOLOGY.md).

## First-release product model

The first release compares independent, dedicated-market cases:

- **Day-ahead:** energy arbitrage.
- **mFRR:** the supported upward energy and capacity case.
- **aFRR:** the supported upward and downward energy and capacity case.

Each market case uses the same selected period and asset configuration where
the market permits it. Day-ahead has no activation profile and no capacity bid.
mFRR and aFRR expose only the `balanced` and `passive` activation profiles.
`god` and `active` are not StepInBel product modes. Capacity bidding is
configured independently from the activation profile.

Revenue from separate cases must never be summed. "Market comparison" does not
mean cross-market co-optimisation.

## Physical boundary

The target model covers:

- Separate pump and turbine power ratings.
- A finite upper reservoir represented by usable stored energy.
- Pumping and generating efficiencies.
- Initial and terminal reservoir state.
- The accepted continuous operating and ramp behaviour from the PHS MVP.
- Optional co-located PV behind the same grid connection.
- Explicit grid-import and grid-export limits.

The initial port must reproduce the accepted continuous optimization baseline.
Optional fixed-speed pump operation, minimum turbine output, and prevention of
simultaneous pumping and generation are product settings. They are off by
default. See [MACHINE_COMMITMENT.md](MACHINE_COMMITMENT.md).

## Data boundary

The runtime consumes published, validated Parquet files and a manifest copied
from the PHS project under an explicit porting brief.

The following are outside the porting exercise:

- Raw Elia and ENTSO-E downloads.
- The data-building scripts and their source-data investigation history.
- Automated refreshing of the published dataset.

The runtime must validate the packaged data and refuse incompatible or
out-of-coverage requests.

## Solver boundary

HiGHS is the required production solver for the implemented day-ahead, mFRR,
and aFRR paths. Gurobi is not a runtime dependency. The one-case workflow, its
persistent audit artifacts, and dedicated-market comparison of two or three
markets are implemented. The command-line interface is implemented and calls
those workflows. The Streamlit application is implemented and calls the same
workflows. Optional machine-commitment mixed-integer optimization is
implemented behind those public workflows; it is off by default.

## User workflows

The target product supports three workflows:

1. Evaluate one asset and strategy configuration (implemented as
   `execute_case_run`).
2. Compare independent dedicated-market cases (implemented as
   `execute_market_comparison`).
3. Explore a finite parameter grid or asset-size sweep (implemented as
   `execute_asset_sweep`).

The implemented command line and Streamlit application call the same
public workflow services and read the same result artifacts. Direct CLI
execution requires an explicit `--data-dir`; published data is not embedded in
the installed package.

## Explicit non-goals for the first release

- Simultaneous cross-market co-optimisation or revenue stacking.
- FCR, intraday, imbalance, or other unimplemented markets. FCR is excluded from
  the product, not merely deferred from the first release.
- Fixed-speed, turbine-minimum, and strict mutual-exclusion machine
  commitment are optional and off by default. See
  [MACHINE_COMMITMENT.md](MACHINE_COMMITMENT.md).
- A hybrid battery and pumped-hydro asset.
- Multiple individually modelled machines or full fleet commitment.
- Forecasting, rolling-horizon operation, or forecast-error analysis.
- A full investment model with financing, degradation, tax, or detailed OPEX.
- Rebuilding or embedding the PHS data pipeline.
- Reproducing the old PHS Streamlit interface.
- Reproducing the Battery dispatch domain model or behind-the-meter scenarios.

These items require their own scope decision and implementation brief.

The available Elia FCR document is retained as reference-family context only;
it does not place FCR on the StepInBel roadmap.
