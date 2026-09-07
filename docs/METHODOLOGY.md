# Behavioural authority

## Purpose

StepInBel modernizes the accepted PHS MVP without silently changing its market
or physical semantics. The PHS application defines the first-release
behavioural and numerical baseline for day-ahead, mFRR, and aFRR.

Elia's Watts.Happening mFRR and aFRR methodology documents explain the approach
that informed the balancing-market work. They are important references, but the
product does not claim or require exact Watts.Happening emulation.

## Reference set

The initial local reference set contains Elia documents dated 2025-09-26 for:

- mFRR and aFRR, as balancing-market methodology references.
- Day-ahead, as context only; the different PHS implementation is accepted.
- FCR, as reference-family context only; FCR is excluded from the product.

Exact filenames and checksums are recorded in [Source provenance](PROVENANCE.md).
Official source locations and redistribution terms still need to be recorded.

## Market decisions

The first-release rules are:

- Preserve the accepted PHS day-ahead dispatch; do not replace it with Elia's
  day-ahead calculation.
- Preserve the accepted PHS mFRR and aFRR physical and eligibility behaviour
  during the HiGHS port, except where a StepInBel decision below deliberately
  changes the configuration surface.
- Exclude FCR.
- Treat any later change to an accepted rule as a StepInBel product decision
  with its own brief and tests.

The configuration surface is a deliberate exception to blanket PHS-surface
preservation (`STEPINBEL-DECISION`):

- Named PHS period presets (`common`, `history`, `modern`, `custom`) are not
  part of StepInBel. Callers supply an explicit Belgian delivery-day range or
  an exact half-open UTC interval. Requests that are not fully covered are
  rejected; the runtime never clips or moves a window.
- `god` is absent. There is no Elia `god` profile.
- `active` is not a StepInBel activation profile. Elia `mFRR_260925.pdf`
  sections 2 and 4 and `aFRR_260925.pdf` sections 2, 4, 5.1, and 5.2 describe
  `Balanced` and `Passive` as the current methodology profiles; `Active` is
  documented as a legacy profile that is not currently implemented.
- Capacity bidding is independent from activation profile. A historical
  quantile option (`0.50` for the accepted `balanced` PHS parity cases)
  preserves the PHS-baseline bridge. A fixed minimum capacity bid is an
  explicit executable option. mFRR fixed bids are upward-only; aFRR fixed bids
  require explicit upward and downward prices. Fixed capacity prices must be
  finite and non-negative; zero is allowed.

These configuration decisions do not claim exact Watts.Happening conformance.

The documents include assumptions for batteries, renewables, and other asset
classes. Their activation filters, availability factors, bidding rules,
merit-order treatment, and representative-period shortcuts are not inherited
automatically by a pumped-hydro model.

## Known accepted differences at the port boundary

The initial repository review found material differences between the Elia
documents and the accepted PHS balancing implementation. The implemented mFRR and aFRR
slices preserve these as visible modelling limitations, not parity failures:

- Yearly quantiles of realized CBMP values are used instead of a
  quarter-hour-specific percentile of the available energy-bid merit order.
- User availability factors are not modelled.
- Activation-frequency and activation-time filters are not implemented.
- Activation energy is not capped by the published total regulation volume.
- Offered asset capacity is not capped by total awarded market volume.
- `vwap_price_eur_mw_h` is ignored when calculating simulated capacity revenue.
- Capacity is remunerated at the simulated bid price rather than Elia's
  documented maximum of average auction price and bid.
- Recharge, reservoir balance, efficiency, and energy differences are
  represented directly in the physical LP instead of a separate annual energy
  difference correction based on DA percentiles.
- Only the accepted upward mFRR case is modelled, although the methodology
  document also discusses downward flexibility.
- The run is a perfect-foresight historical simulation.
- aFRR upward eligibility uses quarter-hour activation flags and yearly price
  quantiles rather than minute-level control targets, one-minute activation
  depth, or the quarter-hour merit-order stack. That missing activation model
  makes the aFRR path an upper-bound historical simulation, not exact
  Watts.Happening conformance.
- aFRR downward energy retains the documented PHS `M2` behaviour: it may pump at
  `min(day-ahead, aFRR-down)` during a system downward activation without tying
  that energy path to contracted downward capacity. Elia permits free activation
  bids without a capacity award. The absence of a capacity award alone does not
  make free activation invalid.
- The PHS capacity preprocessing collapses aFRR auction steps to one block
  marginal before applying its accepted bidding modes. Simulated capacity
  remuneration remains pay-as-bid at the directional bid times published
  `block_hours`.

These are accepted first-port behaviours, not newly discovered implementation
tasks. They must remain visible so that parity is not later mislabelled as exact
methodology conformance. Changing one requires a separate StepInBel decision.

## Verification model

The project uses three distinct forms of evidence:

1. PHS-baseline fixtures verify that the controlled solver port did not
   introduce accidental numerical changes.
2. Traceability checks identify where an accepted balancing rule was informed
   by Elia and where the PHS MVP deliberately or pragmatically differs.
3. End-to-end acceptance verifies that the resulting workflow, artifacts, and
   interface communicate assumptions and independent-market comparisons
   correctly.

Known differences must be documented, but they are not defects merely because
they differ from Watts.Happening. A later change requires explicit user approval.

The accepted PHS day-ahead formulation is the numerical baseline for this
slice, including where it differs from Elia. Full-year total-revenue parity
uses the committed reference records. The PHS no-PV metadata and output record
one simultaneous interval at a zero EUR/MWh day-ahead price
(`2025-05-03T14:45:00Z`). Later PHS closeout prose said the headline had no
simultaneous interval. The executable artifact is stronger evidence; HiGHS may
return zero, one, or other economically equivalent zero-value simultaneous
intervals. Simultaneous count and dispatch equality are not parity gates.

The linear program itself is described in [MODEL.md](MODEL.md). Day-ahead, mFRR,
and aFRR full-year total-revenue parity use the committed reference records.
Simultaneous pumping and turbining remain allowed and diagnostic.
