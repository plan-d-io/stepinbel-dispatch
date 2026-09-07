# StepInBel linear program

This document is the authoritative description of the implemented continuous
LP. It records accepted PHS behaviour (`PHS-BASELINE`) as solved by HiGHS.
The day-ahead, mFRR, and aFRR adapters are implemented. MILP is outside
approved scope. The model does not claim Watts.Happening conformance, unique
dispatch, forecast skill, or investment adequacy.

Interval duration is `dt = 0.25` hours. Time index `t = 0, …, n-1`. Reservoir
state has `n + 1` values.

## Assumptions

- Perfect foresight over the requested window.
- Price-taker: committed day-ahead prices are exogenous.
- Pump and turbine efficiencies and head are fixed.
- Machines modulate continuously. Simultaneous pumping and turbining is
  feasible and is a diagnostic, not a constraint or tie-break.
- Start-up waste is proportional to ramp-up magnitude. It is not a fixed start
  cost and introduces no binaries.
- Pre-horizon machine power is zero: `p[-1] = 0`.

## Pond and efficiency

`E_max` is pond stored energy from `AssetConfig`:

- `discharge_at_rated` (default): `E_max = P_turbine * storage_hours / eta_turbine`
- `stored_energy`: `E_max = P_turbine * storage_hours`
- `pond_energy_mwh`: that value is `E_max`

Pumping adds `eta_pump * p_pump * dt` (less epsilon waste). Turbining removes
`p_turbine * dt / eta_turbine` (plus epsilon waste).

## Variables and bounds

Always:

- `0 <= p_pump[t] <=` prepared pump bound
- `0 <= p_turbine[t] <=` prepared turbine bound
- `0 <= E[t] <= E_max` for `t = 0, …, n`

A non-negative `r_up` variable exists for a machine only when that machine's
epsilon is positive. Without PV, `p_pump_grid` is the same solver variable as
`p_pump`. With PV the extra non-negative variables are `p_pump_grid`,
`pv_export`, `pv_to_pump`, and `pv_curtail`. One continuous `c_b` exists per
supplied capacity commitment, bounded by `[0, cap_max]`. There are no integer
or binary variables.

Prepared bounds without PV are `min(market bound, effective grid limit)`.
Omitted grid limits default to the respective machine ratings. With PV, total
pump stays within market/rated bounds; `p_pump_grid` is also bounded by
effective grid import.

## Reservoir

```
E[0] = soc_initial_frac * E_max
E[t+1] = E[t]
         + eta_pump * (p_pump[t] * dt - epsilon_pump * r_up_pump[t])
         - (p_turbine[t] * dt + epsilon_turbine * r_up_turb[t]) / eta_turbine
```

Omit an epsilon term when that machine has no `r_up` variables. If
`enforce_terminal_soc` is true, `E[n] = soc_terminal_frac * E_max`.

## Ramps

```
r_up[0] >= p[0]
r_up[t] >= p[t] - p[t-1]
p_pump[0] <= pump_ramp_up_rate * dt
p_turbine[0] <= turbine_ramp_up_rate * dt
p[t] - p[t-1] <= ramp_up_rate * dt
p[t-1] - p[t] <= ramp_down_rate * dt
```

Ramp-rate inequalities are always present.

Proportional epsilon is a known limitation around negative prices: waste is
not a bid-cost, so a machine may still ramp when energy is cheap. That PHS
behaviour is retained.

## PV and shared grid

Availability is `load_factor * pv_ac_kw / 1000` MW. Export price is always the
day-ahead series (`MarketDispatchInputs.day_ahead_price_eur_mwh`) or the
configured fixed price. It is never the mFRR activation price. The model does
not pre-reserve cable headroom for PV or capacity. PV may be curtailed to make
room for turbine export.

```
pv_export + pv_to_pump + pv_curtail = pv_available
p_pump = p_pump_grid + pv_to_pump
p_pump_grid <= effective grid import
p_turbine + pv_export <= effective grid export
```

Unused PV variables and constraints are omitted when `pv_ac_kw = 0`. Result
columns still exist: PV powers and revenue are exact zeros and the PV price is
null.

## Capacity

For commitment `b` over `[start, end)`:

```
0 <= c_b <= cap_max_b
```

Upward: `p_turbine[t] <= c_b` on the span, and
`E[start] >= c_b * coverage_hours / eta_turbine`.

Downward: `E_max - E[start] >= c_b * eta_pump * coverage_hours`.
There is no `p_pump[t] <= c_b` constraint. That older MODEL_SPEC line is not
accepted PHS behaviour.

Day-ahead supplies no commitments and returns a zero-row capacity table with
the full schema.

## mFRR market arrays

mFRR is upward-only. Grid charging uses the day-ahead price. Eligible turbine
export earns the mFRR upward CBMP.

Capacity bidding is independent from the activation profile:

- `HistoricalQuantileCapacityBid(q)`: for each local delivery year, `q` of
  finite collapsed upward block marginal prices. NumPy linear quantiles.
  A negative data-derived bid creates no commitments for that year; it is not
  clipped to zero.
- `FixedMinimumCapacityBid`: every block uses the configured non-negative
  upward price.

A block clears when `capacity_bid <= marginal_price` (inclusive). Remuneration
is `bid * committed_mw * published_block_hours`, not the marginal price or
VWAP. Identifiers are `mfrr:<YYYY-MM-DD>:<block>:up`. Spans use
`block_start_utc` and exclusive `block_end_utc`. Published `block_hours` may
be 3, 4, or 5 on DST days, producing 12, 16, or 20 quarter-hours. A window
that cuts through an overlapping block is rejected; the period is not moved,
clipped, or prorated.

`cap_max_mw` and eligible sell upper bounds use
`min(power_turbine_mw, effective_grid_export_mw)`. Asymmetric pump rating does
not change upward mFRR capacity.

Energy bids are calculated by UTC calendar year, not local delivery year:

```
energy_bid[year] = max(
    quantile(cbmp_mfrr_up | has_mfrr_up, q_profile),
    mean_over_days(mean(32 cheapest UTC QHs)) / (eta_pump * eta_turbine)
)
```

`balanced` uses `q_profile = 0.50`. `passive` uses `0.90`. A UTC date with
fewer than 32 quarter-hours is omitted from the cheapest-eight-hours estimate.
If only one side is finite, that side is used. If neither is finite, the year
has no energy bid.

A quarter-hour is eligible when `has_mfrr_up`, its local capacity block
cleared, CBMP is finite, the UTC-year energy bid is finite, and
`cbmp_mfrr_up >= energy_bid`. Eligible sell price is CBMP and sell upper bound
is the deliverable ceiling. Ineligible sell price and upper bound are exact
zeros. Buy price is the DA price every quarter-hour. Buy upper bound is the
pump rating before the shared physical model applies grid import. Pumping
outside activation remains possible.

## Objective and accounting

Maximize:

```
sum((sell_price * p_turbine - buy_price * p_pump_grid) * dt)
+ sum(pv_export_price * pv_export * dt)
+ sum(capacity_price_b * c_b * block_hours_b)
```

There is no explicit ramp cost, curtailment penalty, simultaneity penalty, or
secondary objective.

```
market_energy_net = energy_gross - grid_charging_cost
total_site_revenue = market_energy_net + capacity_revenue + pv_revenue
```

Capacity revenue is zero for day-ahead. For mFRR and aFRR it comes from solved
commitments. Quarter-hour `total_revenue_eur` excludes block capacity revenue;
that value is block-level and remains in the capacity-results table and
summary.

```
mFRR market_energy_net = mFRR turbine revenue - DA grid-charging cost
total_site_revenue = market_energy_net + capacity_revenue + pv_revenue
```

## aFRR market arrays

aFRR models upward activation revenue and downward free-bid pumping prices
(`M2`). Capacity bidding is independent from the activation profile and is
calculated separately for each direction.

- `HistoricalQuantileCapacityBid(q)`: for each local delivery year and
  direction, `q` of that direction's finite collapsed block marginal prices.
  The same `q` is used independently upward and downward. A negative
  data-derived bid creates no commitments; it is not clipped to zero.
- `FixedMinimumCapacityBid`: upward blocks use `upward_price_eur_mw_h` and
  downward blocks use `downward_price_eur_mw_h`.

A block clears when `capacity_bid <= marginal_price` (inclusive). Remuneration
is `bid * committed_mw * published_block_hours`, not the marginal price or
VWAP. Identifiers are `afrr:<YYYY-MM-DD>:<block>:up` or `:down`. Cross-direction
blocks may overlap. Same-direction overlaps are invalid. A window that cuts
through an overlapping block is rejected. Published `block_hours` may be 3, 4,
or 5 on DST days, producing 12, 16, or 20 quarter-hours.

Capacity ceilings:

```
cap_up_mw = min(power_turbine_mw * f, effective_grid_export_mw)
cap_down_mw = min(power_pump_mw * (1 - f), effective_grid_import_mw)
```

`f` is `AFRRCase.up_capacity_fraction`. A zero ceiling produces no commitment
specs for that direction. Downward capacity still uses only the existing
reservoir-headroom constraint; there is no `p_pump <= c_b_down` row.

Upward energy bids reuse the shared UTC-year helper with `cbmp_afrr_up` and
`has_afrr_up`. `balanced` uses P50 and `passive` uses P90. Eligible upward
quarter-hours receive the upward CBMP and `cap_up_mw`. Ineligible sell price
and upper bound are exact zeros.

Downward buy price starts at the day-ahead series. Where `has_afrr_down` is
true and `cbmp_afrr_down` is finite, buy price becomes
`min(day-ahead, cbmp_afrr_down)`. Buy upper bound remains `power_pump_mw` in
every quarter-hour. Downward pumping is not gated on a downward capacity
award, activation profile, or a downward yearly energy bid. Elia permits free
activation bids; the remaining simplification is the missing merit-order depth
and minute-level activation model.

`MarketDispatchInputs.day_ahead_price_eur_mwh` is an independent copy of the
unmodified DA series. PV export uses that DA series or a configured fixed
price, never an aFRR CBMP. PV-to-pump remains behind the meter. The model
does not pre-reserve the export connection for PV or capacity.

```
aFRR market_energy_net = upward turbine revenue - grid pumping cost
capacity_revenue = solved upward + solved downward capacity revenue
total_site_revenue = market_energy_net + capacity_revenue + pv_revenue
```

Simultaneous pumping and turbining remain permitted and diagnostic. aFRR can
economically favor both in the same interval because the prices differ.

## HiGHS

HiGHS is the only production solver. Internal options are `random_seed=0`,
`solver='choose'`, `presolve='on'`. Default output is quiet. Only an optimal
continuous LP is accepted. Infeasible, unbounded, infeasible-or-unbounded,
time limit, iteration limit, interrupt, model/presolve/solve error, and
unknown statuses fail.

## Post-solve tolerances

```
POWER_TOL_MW = 1e-7
ENERGY_TOL_MWH = 1e-7
ACCOUNTING_TOL_EUR = 1e-5
```

Checks cover finite values, bounds, initial/terminal reservoir, every balance
including epsilon, ramp-up magnitude and rate limits, PV split and pump
definition, grid limits, capacity constraints, interval vs summary accounting,
and HiGHS objective vs total site revenue. Failed solutions are not clipped.

Simultaneous-operation counts use a 1e-6 MW threshold. They are diagnostic.
The committed PHS no-PV day-ahead record contains one zero-price simultaneous
interval; HiGHS may return a different economically equivalent count. Full-year
parity is on total revenue (and no-PV energy-net), not dispatch equality.
