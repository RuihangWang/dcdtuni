# Task 1 — per-time-step hydraulics and physical valves

Status: implementation and final verification complete. Final whole-liquid suite: **278 passed in 165.14 s**. Local commit: `6964e3fe3a04e2bb29f5615999c78186b2b3aeb3` (`feat(liquid): solve control schedules with physical valve hydraulics`), based on `fa5e83ce`; no push. Final `git status --short` is empty. The report/logs are ignored task artifacts, intentionally not part of the three-file code commit.

## Scope and source isolation

Worktree: `/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform`, branch `codex/liquid-control-platform`, initial HEAD `d657cb4d5c690c3a675c501595a69299ff7e4545`. Original main checkout CLAUDE rules and local collaboration/environment rules read; main checkout, services, frontend, fixtures and DDL files unchanged. No third-party liquid twin repository accessed or reused. The equations come from this repository's valve handler and GGA.

Owned changed files:

- `src/backend/liquidcooling/solver/newton_hydraulic.py`
- `src/backend/liquidcooling/handlers/valve.py`
- `src/backend/tests/test_liquid/test_dynamic_hydraulic.py`

No existing tests or golden values changed. Parent's schema/parameter-pack edits were committed independently as `fa5e83ce`. This is the task diff base; those files are not included in this task's commit.

## Mathematical conventions and boundary decisions

- Pressure Pa; mass flow kg/s. `Kv` is m³/h/√bar. The existing ISA law is inverted once: `R = 1e5 * (rho/1000) * (3600/(rho*Kv))²`; `dp = R*m*sqrt(m²+eps²)`, with `eps=1e-8 kg/s`. Exact derivative of this smoothed expression: `R*(sqrt(m²+eps²) + m²/sqrt(m²+eps²))`. Reverse flow retains signed pressure loss and an even derivative.
- A shared `effective_valve_kv` implements existing linear, equal-percentage (rangeability 50) and quick-opening characteristics. Positive openings no longer acquire an undocumented 0.001 leakage floor. Zero/negative opening means ideal shutoff for every characteristic (equal-percentage has a deliberate shutoff discontinuity at zero).
- Shut valves enforce exactly zero mass flow and contribute zero hydraulic conductance. They have no imposed pressure-drop equation, since an ideal closed valve supports a pressure difference at zero flow. Every connected component through open edges must contain a pressure BC; otherwise the time step fails with `singular_pressure_system`. No guessed pressure or virtual leakage reference is introduced. Failed slices return flagged guesses, not validated solutions.
- Existing dead-end continuity still sets zero flow. Terminal pressures are now reconstructed from the actual zero-flow headloss, so hydrostatic/pump static head is retained rather than copying the neighbour pressure. The new residual acceptance checks every nonclosed edge, including these terminal edges.
- Preserve the repository's existing pump curve `dp = -rho*g*N²*(H0-a*(m/rho)²)` exactly. This package adds schedule evaluation and does not replace or validate that pump curve against vendor data.
- A small flow update alone cannot establish convergence. Require finite headloss/Jacobian and pressure/flow, mass residual ≤ `max(1e-10,min(tol,1e-8)) kg/s`, and momentum residual ≤ `1e-5 Pa + 1e-10*max(abs(dp))`. This pressure-relative term allows roundoff at large pressure scales without accepting gross imbalance.

## Interfaces

`gga_solve(graph, tol=1e-6, max_iter=50, m_init=None, iteration_callback=None)` remains unchanged, returning `P[N_nodes,N_t]`, `m[N_edges,N_t]`, `info`.

`_gga_solve_step(..., time_index=0)` is the single-time implementation. `_compute_all_headlosses(m, graph, time_index=0)` retains its existing two-argument/default-first-step behavior. Inspected `ddl/src/softlc.py` imports/calls: its existing two-argument headloss and `gga_solve(..., m_init=...)` callers remain compatible; no DDL edits made.

Static `[component]`, broadcast `[component,1]` and scheduled `[component,N_t]` controls are supported. Every changed pump rpm/valve opening triggers its own solve, warm-starting from the most recent converged flow. Identical adjacent control slices reuse only converged results. All other hydraulic graph parameters are static during one call; thermal schedules do not enter this solve.

Scalar `info` keys retained: `converged` is true iff every slice converges; `iterations` is the maximum iteration count over computed slices (preserves static golden diagnostics); `max_dm` is the maximum final update across computed slices.

New `info['per_step']` is a list with one dictionary per output time index. Keys: `time_index` (int), `converged` (bool), `iterations` (int, zero for reused results), `max_dm` (kg/s, zero for reuse), `mass_residual` (kg/s), `momentum_residual` (Pa), `reason` (string), `reused` (bool). Reasons: `converged`, `max_iterations`, `nonfinite_headloss`, `nonfinite_state`, `nonfinite_residual`, `singular_pressure_system`. Residuals/update can be infinity when no valid iteration was available. A later healthy slice can recover after a failed slice; aggregate success stays false.

The callback keeps its two scalar arguments `(iteration, max_dm)` and runs for actual Newton iterations, restarting its iteration counter for each computed slice. Reused slices do not call it. Consumers needing time labels should use `per_step`.

## TDD evidence

Every command below ran from the worktree using:

```sh
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= PYTHONPATH=$PWD/src ~/miniconda3/envs/dcdtuni/bin/python
```

Baseline import probe printed `/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform/src/backend/liquidcooling/solver/newton_hydraulic.py`.

1. Added initial behavioral tests before implementation. Initial run found 13 expected failures and one test already passing; tightened that test's update-tolerance trigger so it detects the intended false-success bug, and added signed valve derivative checks. Re-ran unchanged production code: **16 failed in 3.53 s**, all behavioral assertions (no collection/import errors). Evidence `task-1-red.log`.
2. Implemented valve headloss and time scheduling. Same 16 tests: **16 passed in 3.32 s**, `task-1-green.log`.
3. Required existing tests: `test_newton_hydraulic.py`, `test_newton_vectorised.py`, `test_hydraulic.py`, `test_event_timing.py`, `test_bc_ic.py`: **50 passed in 5.68 s**, `task-1-regression.log`. Includes unchanged static golden pressures, flows and iteration counts, ISA valve-handler units and sparse/vectorized assembly contracts.
4. Self-review found closed single-edge dead-end pressure could still be invented by the legacy terminal shortcut. Added a test before the fix: **1 failed, 16 deselected in 3.51 s**, `task-1-red-isolation.log`. Added open-edge pressure-reference connectivity check.
5. Self-review found terminal residual checking must retain nonzero static head. Added test first: **1 failed, 17 deselected in 3.89 s**, showing **9780.57 Pa** lost hydrostatic head. Evidence `task-1-red-static-head.log`; fixed zero-flow pressure reconstruction.
6. Final focused command: `-m pytest src/backend/tests/test_liquid/test_dynamic_hydraulic.py src/backend/tests/test_liquid/test_newton_hydraulic.py src/backend/tests/test_liquid/test_newton_vectorised.py src/backend/tests/test_liquid/test_hydraulic.py src/backend/tests/test_liquid/test_event_timing.py src/backend/tests/test_liquid/test_bc_ic.py -q`: **69 passed in 5.54 s**, `task-1-green-final.log`. Nineteen new cases cover independent analytic ISA and pump-loop oracles, all valve characteristics, direction, schedule timing, junction mass balance, closure/reopening, static broadcasting, per-step failure propagation, residual gating, isolation and static head.
7. Full suite `-m pytest src/backend/tests/test_liquid -q`: **278 passed in 165.14 s (2:45)** on final code and parent parameter-pack changes; evidence `task-1-full-verified.log`. An earlier full run before terminal-static-head refinement passed **272 tests in 179.88 s**, log `task-1-full.log`; it is not substituted for final verification. A subsequent run started before the overflow guard ended **275 passed / 1 failed in 174.58 s**, `task-1-full-final.log`: the parent was editing `test_parameter_pack.py::test_invalid_pack_binding_is_rejected_before_solving[mismatch-profile type]` during that run; its loaded older fixture expected a type mismatch but failed earlier on unsupported pump parameters. The parent fixed that fixture in `fa5e83ce` and reports 77 parameter/schema/compiler tests passing. This task did not edit those files; final combined run is authoritative.
8. Final numerical self-review added a finite-input/overflowing-trial test: **1 failed, 18 deselected in 10.28 s**, `task-1-red-overflow.log`. It exposed `inf <= inf` acceptance when the relative pressure tolerance also overflowed. The convergence gate now explicitly requires finite residuals and tolerances and reports `nonfinite_residual`.
9. `git diff --check` passed during self-review; final check passed before commit.

Logs and this report live under `.superpowers/sdd/2026-09-22-liquid-hydraulics/` and contain no source credentials.

## Limitations

- Quasi-steady hydraulics at prescribed time indices only: no fluid inertia, actuator dynamics, controller rollout, thermal propagation changes, or end-to-end trajectory differentiability.
- Ideal closure can leave pressure underdetermined; failing explicitly is intentional. Leakage, trapped-fluid compliance and isolated pressure gauges require explicit future models.
- Existing undamped GGA and minimum positive Jacobian clamp remain; difficult hydraulic topologies can still fail or limit-cycle, and failures are now visible per step. Pump reverse-flow physics beyond the existing curve are not extended.
- No vendor/PEK02 calibration or energy-saving claims; no external end-to-end service run. CPU only; no new CUDA validation.
- Callback users receive scalar per-iteration values without time labels; time-level status is in `per_step`.

## Scoped review follow-up — closed-opening gradients

Review source: `task-1-review.md`, minor finding at shared `effective_valve_kv`. Baseline HEAD `6964e3fe`. Only `handlers/valve.py` and `test_dynamic_hydraulic.py` changed; the hydraulic solver and parent's independent parameter-validation files were not touched.

Confirmed `torch.where` evaluates the unselected quick-opening square-root branch: at zero opening, its infinite derivative contaminates the backward pass through `0 * inf`, producing NaN even for linear/equal-percentage characteristics. Six new tests (helper and real `ValveBatchHandler`, each for all three characteristics) were written before the fix. Each includes negative, zero, `1e-12`, half-open and full-open controls; independent analytic oracles check both forward values and opening gradients.

RED command, with the same worktree/env/CPU thread settings as above:

```sh
python -m pytest src/backend/tests/test_liquid/test_dynamic_hydraulic.py -q -k closed_opening_gradients
```

Result: **6 failed, 19 deselected in 3.68 s**, all on nonfinite zero-opening gradients. Evidence `task-1-review-fix-red.log`.

Fix: give `sqrt` a finite dummy argument of one only where clamped opening equals zero; retain the final exact shutoff mask. Thus no leakage or positive-opening value/derivative changes are introduced. Using equality to zero preserves NaN inputs for existing diagnostics. The chosen backward value at zero/negative opening is **zero for every characteristic**. For quick-opening this is a finite numerical endpoint subgradient convention, not a claim that the mathematical right derivative of sqrt at zero is finite; equal-percentage's shutoff discontinuity likewise remains explicit.

GREEN command:

```sh
python -m pytest src/backend/tests/test_liquid/test_dynamic_hydraulic.py src/backend/tests/test_liquid/test_bc_ic.py src/backend/tests/test_liquid/test_newton_hydraulic.py src/backend/tests/test_liquid/test_newton_vectorised.py -q
```

Result: **67 passed in 4.20 s**, evidence `task-1-review-fix-green.log`. Covers all 25 dynamic-hydraulic cases (including the six new gradient cases), ISA handler units, physical zero flow/reopening, static golden pressures/flows/iterations, and sparse/vectorized assembly. `git diff --check` passed. Per the scoped follow-up request, the whole suite was not rerun for this small helper-only fix; the earlier 278-pass whole-suite result belongs to the pre-follow-up commit, not this final helper commit.

This resolves the shared helper's local backward issue. It does not establish a trajectory-gradient or closed-loop-control differentiability contract. Local follow-up commit: `fcd1a6654ca681236714ad5f8d60141b74bbcb13`, based on `6964e3fe`; no push or services. Final status contains only the parent's in-progress `parameters.py` / `test_parameter_pack.py` modifications, excluded from this commit.
