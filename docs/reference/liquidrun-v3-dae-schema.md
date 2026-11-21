# `.liquidrun` v3 — Newton DAE input contract

The editable engineering source remains `.cfdproj`. V3 is its derived numerical
input: geometry-derived topology, physical coefficients, source bindings,
primary circuits and analysis settings are captured together. An explicitly
saved DAE experiment is compiled by the React/shared exporter; runtime and
standalone-file entry points dispatch v3 to the common Newton DAE.

Authoritative implementation:
[`liquidrun_dae_schema.py`](../../src/backend/liquidcooling/liquidrun_dae_schema.py).
Distributable structural schema:
[`liquidrun_dae.schema.json`](../../src/backend/liquidcooling/liquidrun_dae.schema.json).
Graph field types and cross-reference validation reuse the existing v2 models;
v1/v2 parsing and behavior remain unchanged.

## Identity and explicit numerical choices

Required identity fields are `format: "liquidrun"`, `schemaVersion: 3`,
`version: "3.0"`, and
`equation_system: "incompressible_thermal_dae"`.
`source_project` retains the `.cfdproj` format/version, stable project UID,
display name and any genuine optional revision/hash. `object_bindings` retains
stable project-object-to-node/edge references. Names do not replace UIDs.

The root continues to carry `gravity`, `fluid`, `time`, `nodes`, `edges`,
`racks`, `events`, `initial_conditions`, `units` and optional `parameter_pack`.
Units are metres, seconds, Kelvin, Pascal, kg/s and watts, with other physical
units explicit in parameter names and parameter-profile unit declarations.
`execution` selects PyTorch, `device: cpu|cuda[:index]`, `dtype: float32|float64`
and `max_threads: 1..256`. The graph is compiled once and every tensor is moved
to the requested device/precision together; integer indices and masks retain
their types. GPU execution is explicit: no automatic CUDA selection or silent
CPU fallback. Float64 remains the scientific verification baseline; float32
requires attainable tolerances and separate accuracy checks. CPU threading is
a process-wide runtime setting, not a property of the physical equations.
Host affinity/CPU quotas still apply. Validation fixtures deliberately choose
CPU and one thread; this is a resource budget, not an algorithm restriction.

`analysis` has two required fields:

- `mode`: `steady` or `transient`.
- `initialization`: `steady` or `consistent`.

A steady input requires `initialization: "steady"`, `N_t: 1` and no events.
A transient input may initialize by solving the complete steady equations, or
by retaining its specified storage temperatures and solving consistent
algebraic variables and storage rates. The schema describes the request; the
loader itself performs neither solve.

`newton` contains only effective numerical options: `max_iterations`, `atol`,
`rtol`, and `max_backtracks`, all explicit, plus the optional `linear_solver`.
Omitted (every saved project), it is `dense` up to 4000 unknowns and `sparse`
above; `dense` named explicitly above 4000 is refused at admission. `gmres`,
`pgmres` and `block` are research paths. `time.bdf_order` is currently
restricted to `1`, matching backward Euler. There is no public `solver` block,
Adam optimizer, learning rate or loss-weight block in v3.

`controllers` may be omitted or `[]`. A nonempty list declares PI loops that
the DAE solves inside every backward-Euler step (`solver_laws.controller_law`
`pi_backward_euler_backcalc_antiwindup/v1`): with
e = direction·(setpoint − measurement), the output is
u = clip(bias + kp·e + I, output_min, output_max) and the integral is a
differential state, dI/dt = ki·e + (u − u_raw)/T_t (back-calculation
anti-windup, T_t = max(kp/ki, dt)). Measurements are `node_temperature`,
`solid_temperature`, `pressure_difference` or `mass_flow`; actuators are a
curve pump's `rpm_ratio` or a valve's `opening`. Admission refuses what the
DAE does not model rather than dropping it: `kd`, `derivative_tau_s` or
`rate_limit_per_s`, `ki` = 0, a `sample_period_s` other than `time.dt_s`, a
flow-held pump, a valve loop with `output_min` = 0 or a closed start, and an
actuator with a time schedule. Online commands to a controlled actuator are
refused. Positive valve-opening trajectories
are supported. Any known schedule/event that changes an ideal valve's
open/closed active set is rejected during admission, before graph allocation.

## Finite primary circuits and HX meaning

`primary_circuits` is an array with exactly one binding for every
`heat_exchanger` edge. `edge_id` must be unique and must name an HX, not a pump
or other edge. List order is immaterial; the loader reorders entries to the
compiled HX edge order.

Each binding requires:

- `flow_kg_s`: nonnegative scalar, or exactly `N_t` values.
- `inlet_K`: positive scalar, or exactly `N_t` values.
- `cp_J_kgK` and `rho_kg_m3`: positive primary-fluid properties.
- `primary_volume_m3` and `secondary_volume_m3`: positive fluid volumes.
- `source`: `mock`, `vendor`, or `calibrated`.
- `source_reference`: nonempty identification or explanation of that source.

These are explicit facility-primary inputs. They are not inferred from pump
speed, a generic CDU model name or a legacy temperature field. The primary
return and secondary supply are solved storage temperatures of two fluid
volumes in both HX models below.

Public HX parameters contain `dp_a`, `dp_b` and `UA_W_per_K`, supplied directly
or by a same-type embedded profile, and the edge-only field `hxModel`, which
states how `UA_W_per_K` is read:

| `hxModel` | Duty `Q` (secondary → primary) | Steady state |
|---|---|---|
| `counterflow-ntu` | `ε·C_min·(T_s,in − T_p,in) + UA·[(T_s − T_s*) − (T_p − T_p*)]` with the counterflow effectiveness `ε = (1−e^{−x})/(1−C_r e^{−x})`, `x = NTU(1−C_r)`, `NTU = UA/C_min` (`ε = NTU/(1+NTU)` at `C_r = 1`), and `T_s* = T_s,in − (ε·C_min/C_s)·ΔT_in`, `T_p* = T_p,in + (ε·C_min/C_p)·ΔT_in` the steady counterflow outlets | exactly the counterflow ε-NTU exchanger |
| `mixed-volumes` | `UA·(T_s − T_p)` between the two perfectly mixed volumes | two mixed volumes (the pre-2026-10-21 model) |

In both models the volumes keep their storage,
`C_s dT_s/dt = |m| c_p (T_s,in − T_s) − Q` and
`C_p dT_p/dt = m_p c_p,p (T_p,in − T_p) + Q`. In `counterflow-ntu` the second
term of `Q` vanishes at every steady state with flow, so the steady duty is the
counterflow exchanger's; in a transient the volumes' departures from their
steady outlets exchange through `UA` as two mixed volumes do. A side without
flow reaches the other side's inlet (`ε·C_min/C = 1`), so a pump trip or a
primary flow of 0 turns the exchanger into exactly the `mixed-volumes` law
`UA·(T_s − T_p)`: the stagnant volume keeps exchanging instead of freezing.
An HX edge without `hxModel` is read as `mixed-volumes`: every saved
`.liquidrun` written before the field existed replays exactly as it ran. A CDU
created in either editor states `cooling.hxModel: counterflow-ntu` (the CDU
factory's seed), and the React compiler writes a CDU's `cooling.hxModel` onto
its HX edge. A saved CDU without the field compiles exactly as before, with no
`hxModel`, so re-running an existing project keeps its `mixed-volumes` physics
and its compiled bytes; setting the field switches it. A manufactured
benchmark admits only `mixed-volumes`, the model its sources were derived for.
The v2 export omits the field (the v2 runtime's HX is an infinite facility
reservoir). The run provenance lists the model of every HX under
`solver_laws.hx_models`.

`T_secondary_K` is forbidden both on HX
edges and in HX parameter profiles, including unreferenced profiles. It belongs
to the older infinite-reservoir model and cannot silently coexist with finite
primary circuits.

The old `CompiledGraph` compiler still has a reservoir column. The loader
internally derives that unused column from the explicit first primary inlet
sample. `EquationGraph` does not consume it. The public typed manifest and
its serialization contain no such placeholder, and provenance labels this
compatibility detail explicitly.

Initial uniform pipe-fluid and cold-plate-solid temperatures come from
`initial_conditions`. The adapter's initial HX secondary guess uses the local
node-fluid guess, and its primary guess uses the specified initial primary
inlet. `initialization: steady` replaces guesses by a steady solution;
`consistent` retains the selected storage initial state. No new measured
initial HX temperatures are claimed by these initialization conventions.

## Validation and replay

JSON Schema checks strict fields, types, scalar bounds, units and discriminated
edge types. Python additionally checks profile completeness, all existing graph
and UID references, complete HX coverage, exact time-series dimensions, event
grids and fixed valve active sets. Events are applied at their validated integer
sample; `0.3 / 0.1` cannot accidentally floor an event to the preceding sample.

The loader returns a copied, validated public manifest. It never rewrites a
source project, automatically upgrades a v1/v2 file, contacts equipment, or
executes a solver.

```python
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input, load_dae_file

loaded = load_dae_input(raw_v3)
# Or loaded = load_dae_file("derived-case.liquidrun")

manifest_json = loaded.manifest.model_dump(
    by_alias=True, mode="json", exclude_none=True,
)
equations = loaded.equation_graph
theta = loaded.parameters(0, heat_multiplier=1.0)
guess = equations.initial_guess(theta)

# Host selects the requested operation; this loader does not solve.
mode = loaded.mode
initialization = loaded.initialization
dt = loaded.dt
sample_count = loaded.sample_count
options = loaded.newton_options
```

`heat_multiplier` can be a real CPU scalar Tensor with `requires_grad=True`;
the multiplication and theta packing preserve its gradient. It scales the
scheduled cold-plate heat for that sample, including admitted load events.
It does not scale pipe heat sources or silently alter any heat capacity,
hydraulic parameter or primary circuit.

`loaded.provenance` includes the full public-manifest SHA-256, source-project
identity, resolved equipment-pack bindings, and explicit primary-circuit
sources. The public manifest retains its complete embedded parameter pack;
the v2 resolved-provenance field is not copied into v3, where it would omit
the new primary-circuit physical definitions. `provenance.solver_laws` names
the constitutive-law versions a run used (same manifest hash, different
physics must stay distinguishable):

| Key | Value | Meaning |
|---|---|---|
| `pump_law` | `affinity_N2H0_minus_aQabsQ/v3` | `H = N²·H0 − a·Q·\|Q\|`, with `(H0, a)` the rated-speed curve; shut-off head scales with `N²`, zero-head flow with `N`, and reverse flow through the pump is a loss, so a stopped pump (a trip) cannot drive a loop backwards (v1, before 2026-10-21, was `N²·(H0 − a·Q²)`; v2, `N²·H0 − a·Q²`, equals v3 for forward flow) |
| `hydrostatics` | `all_edges_rho_g_dh/v2` | every edge adds `ρ·g·dh`: pipes from their waypoint route, pumps/valves/cold plates/HXs from their node heights (v1 applied it to pipes only) |
| `friction_law` | `laminar64_swamee_jain_blend_logcap0p5/v2` | pipe friction, below (v1 left Swamee–Jain's pole near `Re ≈ 7` uncapped) |
| `hx_models` | `{edge_id: model}` | the `hxModel` each HX was solved with |

The pipe friction factor blends laminar `64/Re` and Swamee–Jain at `Re = 2300`.
Swamee–Jain's log argument is capped at 0.5, so its pole near `Re ≈ 7` cannot
leak through the blend; every point with `Re ≳ 15` (smooth pipe) is unchanged.

**Runs saved before these records.** Each key entered the provenance in the
same 2026-10-21 change as the law it names, so a saved run without it was
solved with the one law the solver had before: `pump_law`
`affinity_N2_times_H0_minus_aQ2/v1` (`N²·(H0 − a·Q²)`), `hydrostatics`
`pipes_only_rho_g_dh/v1`, every HX `mixed-volumes`, and, for a run with no
`solver_laws` at all, `friction_law` `laminar64_swamee_jain_blend_uncapped/v1`.
A restored saved run (`/api/liquid/runs/<id>/restore`) fills these in and lists
them under `solver_laws.inferred`; the files on disk are not rewritten.

Replaying such a run's `input.liquidrun` on the current solver gives the same
numbers, bit for bit, unless one of the three law changes reaches it: a pump
below or above rated speed (`v1` and `v3` differ whenever `N ≠ 1`), a pump,
valve, cold plate or HX whose two nodes differ in height, or a Newton iterate
passing `Re ≲ 15` (the last moves results by Newton-tolerance amounts only).
With those three laws reverted the 161 saved v3/v4 inputs under `case/`
replay bit for bit, and an absent `hxModel` replays exactly as an explicit
`mixed-volumes`.

Admission and pre-solve checks report physical impossibilities by name:

- **Elevation closure** (compile time): the elevation rises around every loop
  must sum to 0 within 1e-6 m. A pipe whose waypoint end heights disagree
  with its nodes would otherwise create a phantom hydrostatic drive; the
  error lists the loop and the offending pipes.
- **Held HX secondary** (every accepted state): a set-point HX that rejects
  heat to the primary needs `T_set_K ≥` the primary inlet temperature. The
  opposite direction, heating the secondary above the primary inlet, is
  admitted as ideal control and flagged in `hx_ideal_heating` (below).
- **Heat sink** (before every steady solve): each flowing coolant network
  needs a sink, an HX with a primary flow above 0 or a held secondary supply,
  or a temperature boundary. Without one, heat makes the steady state
  nonexistent and no heat makes it non-unique (every uniform temperature);
  both stop with an error naming the exchangers and pointing to a
  `consistent` transient instead of a singular Jacobian or an arbitrary level.
- **Stagnant regions**: an edge has exactly zero flow when its biconnected
  block (open edges plus one ground node joined to every pressure datum) is a
  bridge or holds neither a pump nor a ground edge — dangling branches, and
  loops isolated by closed valves. Their transport is zero and frames and
  results report their flow as exactly 0 (their hydraulic rows stay in the
  solve, and the research tensor state keeps Newton's ~1e-14 kg/s residue); an
  unheated stagnant cold plate keeps its stored temperature at steady state. Heat on a
  stagnant plate or pipe means no steady state exists: steady initialization
  and equilibrium queries stop before Newton with an error naming the plates,
  the closed valves isolating them, and the alternative
  (`analysis.initialization: consistent` for a transient heat-up).

Results (`DAESimulation.result()`; the legacy v1/v2 runtime adds the same
topology and `node_observable`) carry, besides the solved series:

- `edge_from`, `edge_to` (node ids; signed flows are positive from→to),
  `edge_types`, `edge_D_m` (pipes and valves, otherwise `null`) and
  `node_positions_m` (scene metres, y up);
- `node_observable[node][sample]`: `false` where a node has no inflow and no
  prescribed temperature, so its temperature is a numerical gauge, not a
  mixed-cup value;
- `edge_stagnant[edge]`: `true` on closed valves and stagnant edges;
- `rack_summaries[].servers[].signed_flow_kg_s` beside the unchanged
  magnitude `flow_kg_s`, and `cp_edge_ids` listing every cold plate of a
  multi-plate server in series order;
- `hx_ideal_heating[hx][sample]`: `true` where a set-point HX holds its
  secondary above the primary inlet while taking heat from the primary. The
  set-point form admits this as ideal control (a fast set-point rise needs
  it), but no passive exchanger delivers it: the primary leaves colder than it
  came. Every frame carries the same flag per HX;
- `warnings`: one line per subject over the accepted samples (how many, when,
  and the worst sample's message), and `frame_warnings[sample]` with each
  frame's own `warnings`. Besides ideal-control heating they report coolant
  outside 253.15–373.15 K, the single-phase water/glycol range the
  constant-property model stands for: a converged state there (e.g. a heated
  plate behind a nearly closed valve, 4339 K on Shinaide at opening 0.002) is
  not physical. Equilibrium queries carry their own frame `warnings` and do not
  enter the run's summary.

## Derived Shinaide verification fixture

[`shinaide-dae-v3-mock.liquidrun`](../benchmarks/fixtures/shinaide-dae-v3-mock.liquidrun)
is a **verification fixture**, not an alternative engineering source.
It is explicitly migrated from the existing v2 browser export by
[`migrate_shinaide_dae_fixture.py`](../benchmarks/migrate_shinaide_dae_fixture.py).
[The migration record](../benchmarks/fixtures/shinaide-dae-v3-mock.migration.json)
records both file hashes, the removed Adam block and the removed reservoir
parameter. Original `.cfdproj` and v2 inputs remain read-only.

The fixture retains 187 nodes, 242 edges, 14 racks, 56 load events and 11 samples.
Its heat remains 21 kW initially and changes to 29.4 kW at the original 5 s
event. The one HX receives explicit mock primary flow 1 kg/s, inlet 293.15 K,
two 0.02 m³ volumes, and cp/rho copied explicitly from the graph's fluid.
These are numerical-validation choices, not PEK02 measurements or calibration.
Migration validates and compiles the input only; it performs no numerical solve.

Generate the distributed schema in the repository's CPU-limited `dcdtuni`
environment with `python -m backend.liquidcooling.liquidrun_dae_schema`.
The tests in
[`test_liquidrun_dae_schema.py`](../../src/backend/tests/test_liquid/test_liquidrun_dae_schema.py)
cover invalid fields/units/HX references/series, legacy-reservoir rejection,
event timing, valve admission, source preservation and Tensor multiplier
gradients, plus the derived Shinaide fixture.
