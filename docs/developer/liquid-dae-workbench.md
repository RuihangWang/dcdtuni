# Liquid DAE workbench

> **Status (as of 2026-10-22):** this is the default liquid workflow in the React editor: new experiments use the Newton DAE. The physics extensions of the 2026-10-21 campaign have landed (selectable HX model, pump affinity law, friction cap, hydrostatics on every edge, explicit stagnation and no-steady-state errors); "Numerical and execution boundaries" below describes the current model. The editor's solver and results tabs are described in [post-processing §6](../user-guide/post-processing.md#6-liquid-cooling-results).

The engineering source is the saved `.cfdproj`. Its geometry and connections
compile through React/shared graph construction into a strict v3 `.liquidrun` (v4 when the project timeline is transient),
then the backend compiles the same incidence/component graph into
`F(t, y, ydot, theta)=0`. OpenFOAM air settings remain in the same project and
retain the separate `.cfdrun` path.

## Open the verified mock

Load `case/NV_PEK02_Mock/NV_PEK02_interactive.cfdproj`, switch the top bar to
**💧 液冷模拟** and open the **🔬 求解** tab (the same tab name as in air mode). The saved experiment is CPU float64, one thread, 120 s at dt=5 s,
with explicit primary circuits and no scheduled heat event. The original
`NV_PEK02_mock.cfdproj` (now `src/frontend/react/test/fixtures/cases/`) remains the reproducible 10 s fixture with its 5 s event.
The interactive copy was saved through the browser and returned to the Mac.

Use **准备实验** (it reads **新建实验** once a session exists) to initialize the full steady equations. In transient mode,
**80% / 100% / 120%** changes the next accepted step's heat input. These factors
multiply the authored schedule; they never edit the past. **单步**, **开始**,
**暂停** and **继续** operate on the server-owned trajectory. The monitor plots
both HX primary returns/secondary supplies and heat input, primary rejection
and thermal storage rate.

**独立稳态查询** solves the same residual with zero storage derivative, using
the pending multiplier. It creates a separately labelled equilibrium card
without changing the transient state/time.

The analysis mode is shown read-only in the panel (**分析模式**: **稳态 · 单一工况**
or **瞬态 · 负载响应**). It is set by the top bar's steady/transient switch for
the liquid domain (since E8b). A steady experiment produces one steady sample and
drops time events, which the panel states; a transient experiment follows the
project timeline when the timeline is transient, otherwise its own duration.
Switching back to transient retains the physical project and restores the time
settings.

One-time live changes are recorded in `checkpoint.json` and `result.json`.
They do not rewrite the source project. `input.liquidrun` preserves the
compiled scenario, while the replay checkpoint also preserves accepted and
pending live input multipliers. Restore recomputes accepted roots; it does not
claim to serialize an autograd tape.

## Recorded browser result

[Actual browser evidence](../benchmarks/nv-dae-browser-2026-09-26.json) and the
saved run `case/NV_PEK02_Mock/Liquid/log/run-86175f9b07df4f428763a7eff04de056`
come from the running React frontend, not a fabricated chart. A button applied
340 -> 408 kW at t=5 s. Both primary returns rose from 28.1301 to 28.7872 °C at
120 s. Their independent 408 kW equilibrium is 29.7561 °C. At 120 s, primary
rejection is 367.483 kW and storage is 40.517 kW: the plant is still transient.
The largest energy imbalance across the accepted samples is 2.33e-7 W.

The browser demonstrated steady creation, saved project compilation,
transient creation, future heat commands, independent equilibrium, one step,
continuous execution and completed curves. The attempted pause happened after
the short run had completed; pause/resume behavior is covered separately by
actual HTTP lifecycle tests. No field calibration or electrical energy saving
is inferred from these synthetic heat inputs.

## Numerical and execution boundaries

The vectorized residual batches pipe, cold-plate, pump, valve and HX tensors,
then aggregates graph connections with device tensor indexing. Newton uses a
vectorized Jacobian and PyTorch linear solves; the implicit backward solves
the transposed converged Jacobian and includes previous-state dependence.
`execution.device`, `dtype` and `max_threads` are recorded in v3 and may be
saved under the project's DAE settings. CPU and CUDA use the same residual
and solver. CPU float32/float64 are tested, and CUDA forward results and
gradients match CPU on the full Shinaide graph
([receipt](../benchmarks/liquid-fullcase-gpu-parity-gradient-20261007/README.md);
see also [the solver-first page](liquid-dae-solver-first.md)). At the current
network sizes CUDA is not faster than CPU, so CPU float64 stays the default.

The scientific tensor API is `DAEExperiment(definition, record_frames=False)`.
It retains tensor histories and gradients without converting each state array
to JSON. The interactive API uses explicit presentation snapshots. No gradient
is expected through HTTP/JSON values.

Current model: algebraic incompressible hydraulics, finite-volume pipe storage,
cold-plate solid storage with quasi-steady coolant NTU, and per HX edge either a
counterflow ε-NTU exchanger (what a new CDU states) or finite mixed primary and
secondary volumes (a saved file without `hxModel`); see
[HX meaning](../reference/liquidrun-v3-dae-schema.md#finite-primary-circuits-and-hx-meaning).
The constitutive laws are versioned in every result's
`parameter_provenance.solver_laws`: pump head follows the affinity law
`H = N²·H₀ − a·Q·|Q|` (a stopped or slowed pump dissipates reverse flow);
friction blends laminar 64/Re with Swamee–Jain, its log argument capped at 0.5
so there is no pole at low Reynolds number; `ρ·g·Δh` acts on every edge and
admission checks that each loop's elevation changes sum to zero. A block with no
pump-driven path is reported stagnant (zero flow, no defined temperature), and a
steady state that cannot exist (no heat sink) is refused with the reason instead
of being iterated. Primary inlet/flow are supplied boundaries; primary
hydraulics are not a second solved network. The valve active set is fixed:
positive opening trajectories work, but exact open/closed transitions require
new topology initialization and are rejected before a v3 run. The panel's
pump PI loop (temperature or pressure mode) is compiled into the manifest's
`controllers` and solved as a continuous PI law inside each DAE step (no
sample lag, no slew limit; see the v3 schema reference); a loop without
integral gain is refused. Frames report each loop's output, measurement,
error and saturation, and the pump speed the plant actually ran.

Up to 4000 runtime unknowns (the NV mock has 1604) Newton and the implicit
adjoint use the dense Jacobian. Above that the runtime picks the sparse path
(`solver/dae_sparse.py`): a coloured-JVP Jacobian factored once per step with
SuperLU, the adjoint solved with the same factorisation, and every step checked
against one exact random-direction JVP so a dependency the stored pattern lacks
(a reversed flow, or an advection term that is zero at a steady state and
nonzero after a heat step) is found and added. `newton.linear_solver` may name the path
explicitly; `dense` above the limit is refused at admission. Values and
gradients match the dense path ([receipt](../benchmarks/liquid-dae-sparse-2026-10-22/README.md)).
Vendor/as-built calibration remains separate work.

A subsequent independent review fixed admission of pressure-only boundaries
with undeclared net fluid inflow; [the review and red/green evidence](../benchmarks/dae-review-2026-09-26.md)
record the exact case. [Deployment hashes](../benchmarks/dae-deployment-2026-09-26.json)
verify that the named Mac and 40901 numerical modules/build are identical.

The final browser check also verified **pause at 20 s, independent equilibrium
while still paused at 20 s, and resume to 120 s** on the corrected backend.
[Reviewed browser evidence](../benchmarks/nv-dae-browser-reviewed-2026-09-26.json)
records run `a6dcf80290bf4aa8ae4fda622e6ee585`. Its primary-return and complete
node-temperature trajectories are exactly equal to the earlier 120 s run;
the new boundary guard does not perturb this valid closed-loop mock case.

## Standalone replay of accepted online inputs

DAE runs now persist `executed.liquidrun` beside `input.liquidrun`. The original
file preserves the compiled plan. The executed file contains only accepted
samples: authored events and online heat multipliers are materialized into
explicit edge schedules; primary/pump/valve trajectories and pipe source
profiles retain their physical values. Pending commands and independent
steady queries are excluded. Original project UIDs, parameter-pack sources,
geometry and initial conditions are preserved.

`executed-inputs.json` records the parent and executed configuration hashes,
accepted sample count/time, original horizon count and whether that original
horizon has completed. A partial export reproduces its accepted prefix; its
shorter horizon does not claim the original experiment finished. The
checkpoint remains the artifact for continuing a run with its pending input.

**导出已计算输入** returns the strict standalone v3 file from the current run.
It can be passed directly to the existing liquid file runner without a
checkpoint or frontend. Export creates derived numerical data and never edits
the `.cfdproj` source or original compiled plan.
