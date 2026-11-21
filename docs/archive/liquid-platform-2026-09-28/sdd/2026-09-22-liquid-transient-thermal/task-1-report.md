# Task 1 — conservative dynamic thermal kernel

## Scope and API

Own dcdtuni implementation only. No third-party liquid solver was accessed,
imported or copied. Files: `solver/thermal_step.py`, `solver/hybrid.py`,
`runner.py`, `test_dynamic_thermal.py`, and this report.

```python
thermal_step(
    graph: CompiledGraph,
    m: Tensor,             # [N_edges], current signed kg/s
    T_node_prev: Tensor,   # [N_nodes], K
    T_w_prev: Tensor,      # [N_cells], K
    T_s_prev: Tensor,      # [N_cp], K
    *, dt: float | Tensor | None = None,
    time_index: int = 1,
) -> ThermalStepResult
```

Return fields are `T_node`, `T_w`, `T_s`, `edge_outlet` ([N_edges]) and
`diagnostics: dict[str, Tensor]`. Diagnostics contain `linear_residual_K`
([N_nodes]), `mass_residual_kg_s` ([N_nodes], incoming minus outgoing), and
scalar `heat_input_W`, `storage_rate_W`, `hx_rejection_W`,
`boundary_exchange_W`, `energy_balance_W`. Positive boundary exchange enters
the network; positive HX rejection leaves it. The energy defect is
input + boundary exchange - storage rate - HX rejection.

The kernel eliminates each pipe cell and solid equation into an affine
function of its actual upstream node temperature, then solves all node
mixing equations simultaneously using `torch.linalg.solve`. Matrix dimension
is N_nodes, not N_nodes + N_cells + N_cp. Each current signed flow determines
orientation independently. There is no topological traversal, mixing-pass
limit, NumPy conversion, physical-input `.item()`, detachment or no_grad
inside the kernel. Explicit previous state and graph inputs are not mutated.

## Production integration

`_solve_thermal_direct` consumes current `m[:,t]`, keeps differentiable tensor
histories on `model.thermal_node_history`, `thermal_water_history`, and
`thermal_solid_history`, and exposes `thermal_diagnostics` per advanced step.
`t=0` remains initial data. Only copying to legacy display parameters occurs
under no_grad after the result is computed. Existing callback signatures stay
unchanged. The higher-level GGA wrapper itself is not asserted differentiable.

Hydraulic nonconvergence now raises with failed per-step diagnostics instead
of warning and publishing a best estimate. Linear solve failure includes time
index and underlying error. Final hybrid convergence is explicit rather than
inferred from cold-plate residual alone. Legacy ODIL residual fields remain
for compatibility, but final hybrid total reports the maximum linear residual
in K; old smoothed advection/BDF residuals are not the convergence definition
of this implicit-Euler kernel. Runner result extraction uses the actual solved
node history, signed-flow CP inlet/outlet orientation and explicit hybrid
convergence. Zero-flow CP outlet display carries inlet temperature, with no
fabricated advective heat transfer.

## Model assumptions and limits

- Incompressible quasi-steady hydraulic input; pipes have finite coolant
  storage and implicit upwind advection. Distributed cell heat and initial
  temperature fields are preserved.
- CP solid has finite C_solid storage. CP coolant uses quasi-steady NTU;
  V_fluid is not separately resolved storage.
- HX is NTU transfer to prescribed facility temperature. No primary-loop
  dynamics, pump electrical losses, wall heat leakage or variable fluid
  properties are invented. Pumps and valves are adiabatic.
- Fixed-T nodes are reservoirs. Pressure BC nodes account for external
  advective flow. Zero-inflow massless nodes retain previous display T and
  act as a temperature source if they have outgoing flow. Passive zero-flow
  fluid retains energy; heated solid accumulates Q*dt/C exactly.
- Mass residual is reported for every node. External flow boundary nodes may
  have nonzero residual by design; internal imbalance in caller-supplied
  flows remains visible rather than being repaired by thermal mixing.
- Differentiability is verified in nonzero-flow regimes. Exact flow-direction
  switches/valve closure are nonsmooth. A storage-free unanchored circulation
  is singular and fails explicitly.
- Dense node solve is suitable for the tested modest graphs, with O(N_nodes^3)
  solve cost; no full-scale real-time or calibrated PEK02 claim follows.
- Existing legacy `model._build_T_all_nodes()` remains the ODIL reconstruction
  path. Hybrid/runner explicitly use solved histories; independent callers of
  that legacy method should not mistake it for this conservative rollout.

## RED / GREEN evidence

Initial new-file run: **13 failures**. Two production behavior assertions
failed directly against old code: closed-loop HX rejection was **0 W** against
500 W heat input; later flow schedule change gave **zero** solid-temperature
difference. Eleven kernel acceptance checks failed because the new public
module did not exist. After implementation: those checks passed.

A separate extraction RED run showed a fabricated zero final loss marking a
known failed hybrid result converged. The updated extraction test also checks
the actual reverse-flow upstream node temperature. GREEN after runner fix.

Current new test file: **20 passed**. Coverage includes independent per-step
storage + HX enthalpy balance, analytic pipe implicit Euler, unequal incoming
stream enthalpy mixing, zero-flow CP storage and passive fluid, varying flow,
later-step reversal with 3 pipe cells, distributed source and initial field,
no pipe / no cold-plate storage cases, invalid dt/nonfinite state/singular
system, hydraulic failure propagation, extraction and centered finite
differences for flow, CP UA, HX UA and multi-step heat sensitivity.

Named regression command (dynamic thermal, hybrid pipe residual, MMS, thermal,
single rack, CDU HX): **72 passed in 150.76 s** (at 17 new thermal tests).
Expanded thermal tests alone: **19 passed in 4.17 s**.
Full `src/backend/tests/test_liquid` suite: **326 passed in 193.00 s**.
The full-suite process had loaded the kernel before one final guard was added:
finite input storage values can overflow intermediate arithmetic even with a
finite node solve. A new test reproduced **DID NOT RAISE**; reconstruction now
rejects nonfinite fluid/solid/outlet state with the time index. After that fix,
new thermal tests plus existing hybrid pipe regressions: **23 passed in 4.10 s**
(20 thermal + 3 legacy). The entire slow suite was not repeated for this narrow
finite-state guard. Syntax compilation and `git diff --check` passed.

Commands used the existing `/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python`
with `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=$PWD/src` in the isolated
worktree. No services, mesh or business end-to-end jobs ran.

## Performance smoke measurement

CPU, existing dcdtuni environment, OMP_NUM_THREADS=MKL_NUM_THREADS=1,
PYTHONPATH=$PWD/src, single_rack fixture with N_t=10:
10 nodes, 12 edges, 18 pipe cells, 3 cold plates; matrix 10x10;
complete hybrid wall time **0.1729 s**; largest per-step energy defect
**5.99e-9 W**. Single timing run, no service or business pipeline launched.

## Handoff

Implementation commit: `91adb3b15636d23d42ab35d1394fd3412a860575`.
Parent for scoped review: `b63b7f63df772d9cdcf6b93a8e28f61cdd9e672b`.
Only the four owned source/test files were committed. This SDD report remains
ignored as requested; no push. Root-owned `test_liquid_control.py` was modified
in the shared worktree at handoff and was not staged or altered by this task.

## Minor review follow-up — current-frame live monitoring

Review `task-1-review.md` approved numerical implementation with a Minor:
intermediate callbacks read final/unfilled T_s/m columns and reconstructed
node temperatures because solved history was published only after the loop.

Added three RED regressions in `test_runner_cdu.py`: current CP chip was
reported as **340 K instead of 309.24 K**; hybrid frame had no `time_index`;
legacy dynamic supply-T series reported static **288.15 K instead of final
310 K**. Existing ten FakeGraph monitor tests passed in that RED run.

Minimal fix: before each thermal callback, publish the solved node-history
prefix and set `model.thermal_time_index=t`. Both monitor functions sample all
returned quantities at that index. The active marker is cleared in `finally`,
so callers outside callbacks retain the default final-sample behavior. CP
upstream orientation uses the same current signed flow. CDU temperatures use
solved history when present, otherwise the current prescribed series/static
BC; no alternate node reconstruction is introduced. Thermal and final hybrid
callback dictionaries add `time_index` and physical `time_s` while retaining
existing log fields and the low-level three-argument callback signature.

CDU identification intentionally still uses the legacy first pressure BC with
outgoing/incoming edges. This is not a multi-CDU asset/UID binding mechanism;
that mapping remains for the upcoming runtime layer.

Verification on the final follow-up bytes:
`test_runner_cdu.py`, `test_dynamic_thermal.py`, and
`test_hybrid_pipe_residual.py`: **36 passed in 4.08 s**. Includes ten legacy
FakeGraph tests, three new frame/time tests, twenty thermal tests and three
hybrid regressions. `git diff --check` passed. No repeat full liquid suite,
services, business runs or push. Follow-up scope is only runner, hybrid, and
monitor tests; numerical thermal kernel unchanged.

Follow-up commit: `ada9ac9d0408123665e856add7b6a6e343f9cf3a`.
Exact parent: `3b2f3137057c29ae287af41da7c0d4b856952913`.
Working tree was clean after this explicit three-file commit.
