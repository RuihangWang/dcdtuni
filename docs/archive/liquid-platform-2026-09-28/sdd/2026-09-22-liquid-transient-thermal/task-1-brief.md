### Task 1: Conservative dynamic thermal kernel and production trajectory

Owned files:
- Create `src/backend/liquidcooling/solver/thermal_step.py`.
- Modify `src/backend/liquidcooling/solver/hybrid.py`.
- Modify `src/backend/liquidcooling/runner.py` only for truthful convergence propagation/result extraction required by this change.
- Create `src/backend/tests/test_liquid/test_dynamic_thermal.py`.
- Add relevant tests to existing thermal/hybrid/runner modules only when needed; no changes to golden values to mask physics regressions.

Consumes: reviewed time-dependent `gga_solve` output `[edge,time]`, existing `CompiledGraph`, pipe volume/source data, CP Q_W/UA/C_solid and HX UA/T_secondary parameters. Cold-plate coolant is the existing quasi-steady NTU transfer model; its V_fluid is not an independently resolved storage state in this package and this limitation must be explicit (do not claim otherwise).

Produces: a tensor-first `thermal_step` API with explicit previous node/pipe/solid temperatures, current signed mass flows, timestep/index and outputs node/pipe/solid temperatures plus useful residual/energy diagnostics. Decide a compact typed return structure and document exact signature in report. The existing `_solve_thermal_direct`/`hybrid_solve` entry must consume this kernel and produce correct varying-flow trajectories. The pure kernel must preserve gradients; copying results into legacy display state may detach only after computing the result, never inside the kernel.

Required physics:
1. Recompute upstream/downstream orientation and incoming mass-weighted node mixing for each time's signed flows. Do not use `m[:,0]` or a first-time Kahn ordering for later steps. Simultaneously solve algebraic node temperatures so closed circulation loops do not rely on arbitrary traversal order or a fixed number of mixing passes.
2. Pipe cell energy: `(C/dt + abs(m)*cp)*T_new - abs(m)*cp*T_up = C/dt*T_old + Q_cell`, C=rho*cp*volume; reverse cell order for reverse flow. Preserve distributed heat-source and supplied initial-field support.
3. Cold plate: eps = 1-exp(-UA/(abs(m)*cp)); G=abs(m)*cp*eps. Solve `(C_solid/dt+G)*Ts_new - G*Tin = C_solid/dt*Ts_old+Q`. Flowing outlet `(1-eps)*Tin + eps*Ts_new`. At zero flow transfer power is zero and solid heats at Q/C; avoid fabricated advective flow or NaNs.
4. HX: flowing outlet = `(1-eps_hx)*Tin + eps_hx*T_facility`; include this in closed-loop equations, not pass-through. This package treats facility temperature as a prescribed reservoir boundary, not a fully simulated primary loop.
5. Pumps/valves are adiabatic as existing model; fixed-temperature BCs are reservoirs, including their energy exchange in the diagnostic boundary accounting. A zero-inflow massless node retains its previous display temperature; finite-volume cells/solid still obey their storage equation.
6. Support graph without pipes or without cold plates. Validate dt, finite state and solvability. Do not silently publish a valid-looking solved frame when hydraulics or thermal linear solve fails. Preserve helpful error detail and current callback compatibility.
7. Thermal kernel with torch.float64 must remain differentiable with respect to flows/UA/heat source in smooth nonzero-flow regimes. Hard flow-direction/closed-valve events may be nonsmooth; test and document scope. Reuse torch.linalg.solve, no NumPy conversion, `.item()` on differentiable physical inputs, or no_grad in the kernel.

TDD / acceptance:
- [ ] A closed loop with a real pump, pipe storage, CP heat source and HX settles with HX rejecting CP input heat; without an imposed supply temperature. Show RED on current production path's pass-through HX/loop handling.
- [ ] A flow schedule changes outlet/solid temperature relative to constant flow and does not reuse initial flow; reverse direction uses the actual upstream temperature.
- [ ] Zero-flow heated CP remains finite and accumulates Q*dt/C; passive isolated fluid does not gain energy.
- [ ] A single pipe temperature step matches implicit-Euler analytic result; coupled mixing conserves enthalpy; mass/energy balance diagnostics are evaluated independently from solver residual labels.
- [ ] Heat input is balanced by storage change, HX rejection and signed thermal-boundary exchange to scale-aware tolerance over each step.
- [ ] Kernel autograd vs centered finite difference for a nonzero-flow parameter and UA, plus multi-step heat-source sensitivity; no blanket requires_grad success assertion.
- [ ] Existing `test_hybrid_pipe_residual.py`, `test_mms.py`, `test_thermal.py`, `test_single_rack.py`, `test_cdu_hx.py` and new tests. Full liquid suite once before commit. Do not repeatedly launch the full suite during every edit.
- [ ] Record performance of modest existing graph at a short horizon (unit-level benchmark, no service launch) and note matrix size; do not claim full-scale real-time performance.

Report `.superpowers/sdd/2026-09-22-liquid-transient-thermal/task-1-report.md` with RED/GREEN, exact API, model assumptions, diagnostics and unresolved concerns. Commit only owned files and return concise status/commit/tests/report. Controller performs independent review.
