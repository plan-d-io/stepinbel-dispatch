# Optional machine operating constraints

Optional physical assumptions on the default continuous optimization path.
They are disabled by default. Ordinary configurations keep that fast path.
Enabling any assumption selects a mixed-integer model automatically. There is
no separate LP/MILP mode setting.

In the Streamlit application these settings live under **Advanced asset
assumptions**. The same options are available on the command line. Enabling
them can significantly increase calculation time. Users can set a target
optimality gap (default 1.5%) and a maximum solve time per market (default
15 minutes). A physically valid schedule found when the time limit is reached
is presented as the best available solution, together with the gap that was
achieved.

## Physical settings

Public configuration is `MachineCommitmentConfig` on `SimulationConfig`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `fixed_speed_pump` | `false` | Pump is off or at rated electrical power. |
| `turbine_minimum_output_fraction` | `0` | `0` disables. An enabled value must be finite, non-boolean, `> 0` and `<= 1`, and is resolved as `fraction * power_turbine_mw`. |
| `forbid_simultaneous_operation` | `false` | Pump and turbine commitment cannot both be on in one interval. |

Binary commitment variables are an implementation consequence. The model
creates only the binaries required by the enabled assumptions and reuses
them when several constraints need the same state.

Existing ramp-up and ramp-down constraints remain active. This release does
not add startup duration, shutdown duration, startup cost, minimum up-time,
or minimum down-time.

## Solver controls

MIP controls live on `SolverOptions`, not on the physical commitment object:

- Default relative MIP gap: `0.015`.
- Default time limit: `900` seconds per dedicated-market run.
- HiGHS thread selection remains automatic (`threads=0` internally for MILP).
- These controls are applied only when a commitment option is enabled.

A 0.5% gap is an advanced/research setting, not the production default.
“Accepted within a 1.5% MIP gap” means the feasible incumbent is certified
against the solver’s best mathematical bound. It is not a statement of
physical accuracy and is not the LP–MILP revenue difference. Existing LP
results remain optimistic upper bounds because they allow continuous machine
output and potentially simultaneous pumping and generation.

## Automatic LP / MILP selection

- Every commitment option disabled: existing continuous LP, no integer
  variables, `continuous_lp = true`.
- Any option enabled: MILP with HiGHS integer variables bounded to `[0, 1]`.

## Termination

A time-limited incumbent is a completed result only when it is physically
valid. It is not described as optimal or as accepted within the requested
gap.

| Termination | Meaning |
| --- | --- |
| `accepted_within_requested_mip_gap` | Feasible incumbent inside the configured relative gap. Complete normally. |
| `time_limit_feasible_outside_requested_gap` | Time limit with a physically valid feasible incumbent. Complete with the best available solution; requested gap was not reached. |
| `no_feasible_solution` | Infeasible, or time limit without an incumbent. Failed/unusable. |
| `solver_failure` | Other HiGHS failure. Failed/unusable. |

HiGHS status `optimal` under a configured MIP tolerance means accepted
within that tolerance, not a proven zero-gap optimum.

Successful MILP artifacts record requested gap, achieved gap, incumbent
objective, best bound, termination, time limit, node count when available,
integer and binary counts, and solve time.

A market comparison completes when every child has a validated feasible
solution. Each child keeps its own termination and gap. If any child reached
the time limit outside its requested gap, the parent records
`mip_termination_warning`. The comparison fails if any child has no valid
feasible solution.

## Requests and artifacts

Ordinary LP runs keep schema v1. Enabling machine commitment emits schema
v2. Unknown, malformed, mixed-version, and inconsistent records are
rejected. A schema-v1 LP request is not reinterpreted as a MILP.
Comparison and sweep children receive the same physical assumptions and
solver controls.

Public reporting exports include `RUN_ARTIFACT_SCHEMA_VERSION_V2`.

## CLI

```text
stepinbel run --market da --delivery-start 2025-01-15 --delivery-end 2025-01-15 `
  --fixed-speed-pump --turbine-minimum-output-fraction 0.18 `
  --forbid-simultaneous-operation --mip-rel-gap 0.015 --mip-time-limit-s 900 `
  --data-dir data --output-dir outputs/da-commitment
```
