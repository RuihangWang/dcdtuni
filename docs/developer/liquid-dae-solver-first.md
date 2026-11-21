# Liquid solver priority: acausal differentiable DAE

User correction, 2026-09-26: stop expanding the frontend/OV. First deliver a
trustworthy CPU PyTorch solver and NV-shaped mock verification platform, with
power disturbances, facility-primary return-temperature response, consistent
steady/transient modes and MMS. Existing UI/render work remains preserved.

## Physical definition and equation graph

The editable `.cfdproj` owns geometry, connections, material/equipment data,
boundary conditions, heat inputs and analysis settings. The frontend compiler
produces a validated `.liquidrun`; `compile_graph` supplies static incidence,
component indices and spatial cells. A new equation-graph adapter assembles
`F(t,y,ydot,theta)=0`. Edge `from/to` are reference orientations, not a causal
evaluation order. Do not create a separate DDL project as the truth.

First model: incompressible algebraic hydraulics, pipe-fluid/solid heat storage,
direction-aware transport and node enthalpy mixing, plus two finite mixed HX
volumes. Pressure constraints and mass flow are algebraic. Facility-primary
return and rack-secondary HX outlet temperatures are differential states.
Cold-plate coolant initially retains the existing quasi-steady NTU closure;
that assumption must be explicit and cannot imply resolved coolant storage.

Facility primary / rack secondary nomenclature is mandatory. The existing
legacy `T_secondary` meant a prescribed facility reservoir, and is not a new
prediction of primary return. The two-volume mock HX instead satisfies:

```
Cs dTs/dt = |ms| cps (Ts,in - Ts) - UA (Ts - Tp)
Cp dTp/dt = mp cpp (Tp,in - Tp) + UA (Ts - Tp)
```

`Tp` is facility return. Primary supply/flow and both volumes/heat capacities are
explicit inputs with `mock/vendor/calibrated` provenance. This lumped closure
is not a vendor counterflow plate-HX model. No CDU70 specifications are used.
For the NV mock plant, adopt only the point-list asset scope; as-built topology,
sensor timing and actual controller dynamics remain unconfirmed.

## One nonlinear root path

- Steady: solve `F(t,y,0,theta)=0`, not an arbitrary long time march.
- Transient: initially backward Euler, with Newton Jacobian
  `Fy + Fydot/dt`, residual/state scaling and backtracking.
- Consistent initialization: preserve differential storage states, solve
  algebraic values and differential rates together.
- Backward: solve the converged root's transposed Jacobian; propagate gradients
  to parameters **and previous accepted states**. Do not differentiate an
  unconverged root or replace a singular adjoint with invented regularization.
- Fixed valve/flow regimes are explicit. Isolated thermal algebraic-node gauges
  must be marked unobservable; pressure reference failures must fail visibly.
- A transient-to-steady request produces a separately labelled equilibrium
  analysis, not an instantaneous physical jump disguised in a transient trace.

This formulation follows the residual/consistent-initialization convention in
[SUNDIALS IDA](https://sundials.readthedocs.io/en/latest/ida/Mathematics_link.html)
and the pressure/mass/enthalpy connector interpretation in
[Modelica Fluid](https://doc.modelica.org/Modelica%204.0.0/Resources/helpWSM/Modelica/Modelica.Fluid.UsersGuide.ComponentDefinition.FluidConnectors.html).
These are formulation references; the implementation remains independent
PyTorch code in this repository.

## Required validation before a control-panel demo

1. Generic DAE tests: nonlinear algebraic constraint, consistent initialization,
   direct steady solve, load change, analytic discrete history/parameter gradients,
   smooth independently forced MMS showing first-order BE convergence.
2. Physical equation graph: mass/pressure constraints, pipe and solid storage,
   finite-primary HX energy balance, analytic steady primary-return rise and
   a globally conservative closed loop.
3. Physical MMS: hand-derived forcing independent of the production residual;
   separate temporal and spatial refinement; primary/secondary signals included.
4. Full-trajectory adjoint vs independent analytic cases and central differences;
   failure/zero-flow/closed-valve semantics and mode switches.
5. Same `.cfdproj` chain on Shinaide and an explicitly assumed NV mock topology;
   report sizes, conservation and actual single-CPU time/memory.
6. Only then add minimal power +/- controls, pause/step, steady/transient choice,
   and primary-return/secondary-supply/return curves to the existing workbench.
   Online changes become recorded future input events, never edits to past frames.

## Current implementation entry points

- `solver/dae_newton.py`: common root solver, steady/BE and consistent IC APIs.
- `solver/equation_graph.py`: vectorized physical graph adapter, including the
  two-volume primary/secondary HX and pipe/solid storage.
- `liquidrun_dae_schema.py`: strict v3 data, execution and topology admission.
- `dae_experiment.py` / `dae_runtime.py`: accepted steps, future heat commands,
  separate equilibrium query, tensor histories and persisted replay inputs.
- `test_dae_newton.py`: independent analytic and MMS checks of the new root path.
- Existing `rollout.py`/`thermal_step.py` remain comparison baselines. Their
  earlier gradient checks and old fixed-flow MMS do not certify this new path.

## Vectorization and device contract (user correction, 2026-09-26)

Single-core verification is a resource budget, never an algorithm restriction.
All components and cells are assembled in batched tensors; topology scatter
and gather do not evaluate a Python loop per component. The equation residual
must have no scalar extraction or CPU transfers. Newton uses batched Jacobian
VJPs and device-resident PyTorch linear solves; its outer iteration and line
search retain explicit scalar convergence checks. UI/JSON serialization is an
output boundary and is not used by the differentiable tensor API.

Device, dtype and thread budget belong to execution configuration. Graph
indices, masks, coefficients and states move together. No NumPy/SciPy solve or
automatic CPU fallback is allowed inside the Newton/adjoint path. Float64 is
the MMS baseline; float32 is separately exercised with reachable tolerances.
CUDA admission/transfer code is not evidence of GPU numerical/performance
validation. Historical CPU-only checks left it unrun. The subsequent single-GPU
exception now provides the scoped float64 numerical evidence recorded below;
normal execution has returned to CPU-only after that episode ended.

Validation runs use 40901, limited CPU affinity, nice 10 and CUDA disabled;
the small 1-versus-2-thread comparison is an explicit bounded exception to the
single-thread benchmark setup. Source evidence is saved with source hashes.
No NSCC login/control-node computation or diagnostic commands are permitted;
this work does not use NSCC. Do not report the platform complete until all
applicable numerical and case-level gates above have actual evidence.

## Recorded numerical evidence, 2026-09-26

- [Current numerical rerun](../benchmarks/dae-final-verification-2026-09-26.json):
  86 passed, one explicitly skipped CUDA execution test, 20.46 s on one CPU.
  This selection includes the final source-temperature guard and accepted-input
  exporter; the six recorded implementation hashes agree between Mac and
  40901. It has a different file selection from the historical regression below.
  The new `test_equation_graph_cuda.py` additionally passes two CPU checks and
  explicitly skips one CUDA comparison. Its 20-state physical graph contains
  CP/HX/pipe/pump/valve components, checks steady plus three BE steps, and
  verifies heat/pump/positive-valve gradients against CPU central differences.
  At this CPU snapshot its CUDA comparison had not run. The later authorized
  episode executes that comparison as recorded below. The default guard remains
  tested to skip before any GPU availability probe when opt-in is absent.
- [Unified regression](../benchmarks/dae-regression-2026-09-26.json): 86 passed,
  one explicitly skipped CUDA execution test; physical MMS, analytic gradients,
  physical graph, precision/device propagation, loader, runtime, real HTTP,
  persistence and existing result compatibility are included.
- [Residual profiler](../benchmarks/dae-residual-vectorization-2026-09-26.json):
  1 pipe/4 cells and 32 pipes/128 cells have identical operator counts (599
  ATen calls, four index_add calls), with zero item/local-scalar/equal/nonzero
  host decisions inside the residual. This measures batching, not GPU speed.
- [Current-core Shinaide adjoint](../benchmarks/shinaide-dae-gradient-2026-09-26.json):
  a 3-step coupled trajectory, 1096 unknowns; AD vs central-difference relative
  error 3.15e-11 for heat and 2.23e-8 for pump speed. The primary-return steady
  gradient agrees with the independent total-heat balance. Tensor-only runs
  can use `DAEExperiment(definition, record_frames=False)` to avoid converting
  state arrays to presentation/JSON values at each accepted step.
- [NV mock CPU1/CPU2](../benchmarks/nv-vectorized-cpu-2026-09-26.md): 1604 unknowns,
  34 equivalent racks, 102 actual series valves; steady, three short transient
  steps and a separate equilibrium pass conservation. Thread-count state
  difference is at most 1.04e-13 after scaling. This small case does not show
  universal multithread acceleration; default resource budgets remain small.
- [NV mock CPU1/CPU4](../benchmarks/nv-cpu1-cpu4-2026-09-27.md): after the
  user's resource clarification, the same immutable 1604-state manifest ran
  in separate one-core and four-core processes. Steady and one BE step agree
  within `1.04e-13` after state scaling; both heat adjoints equal
  `0.19631667626512342`. One-step timing was `1.4582 s` versus `1.2697 s`,
  while steady and adjoint stages did not become faster. These are single-run
  shared-host observations, not a general speedup claim. The input file still
  declares one execution thread; the benchmark explicitly overrides the
  process thread budget without changing physical inputs or source.
- [Earlier 600-second Shinaide response](../benchmarks/shinaide-dae-load-step-2026-09-26.json)
  and [plot](../benchmarks/shinaide-dae-response-2026-09-26.png) show delayed
  primary-return response and remaining storage at 600 s. That source snapshot
  predates device refactoring; its source hashes are preserved in its report.

The scalar Newton convergence decisions remain explicit host synchronization;
vectorization does not mean the outer nonlinear/time iterations are a captured
GPU graph. The current linear solve is dense, with a runtime size guard; larger
networks will need measured sparse or matrix-free work. General GPU performance
still requires a suitable measured workload; the actual numerical acceptance
below does not imply a speedup.

### Subsequent authorized single-GPU acceptance

[Actual CUDA evidence](../benchmarks/single-gpu-numerical-2026-09-26.json) records
two executed CUDA tests, including the physical steady/three-step trajectory
and heat/pump/positive-valve gradients. The full NV manifest's 1604-state
`load_dae_input → DAEExperiment` tensor path also runs on CPU and CUDA, including
the optional hydraulic predictor, steady initialization, one implicit step
and its heat gradient. Scaled CPU/CUDA state error is `1.23e-13`; both gradients
are `0.19631667626512342`. No original fixture or solver source was changed.

The sole physical card was GPU0 UUID
`GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93`; all CUDA tensors saw only logical
`cuda:0`. Peak PyTorch allocated/reserved memory was 150168576/211812352 bytes.
The single measured full-entry sequence took CPU 3.077 s and CUDA 10.513 s,
so this small float64 case proves migration and numerical parity, not speedup.
The initial report-hook error and its bounded retry are preserved in the JSON;
the original shared eight-minute deadline was never reset.

The [NV mock trajectory gradient check](../benchmarks/nv-dae-gradient-2026-09-26.json)
now also exercises simultaneous heat, pump-speed and positive valve-opening
changes on the 1604-state plant. Independent central differences agree with
AD to relative errors 2.31e-10, 7.48e-9 and 3.36e-8, respectively. Pump flow
changes from 4.33214 to 4.05602 kg/s per zone, so this is not a fixed-flow
thermal replay. Exact valve closure and field calibration are not implied.

The minimal [workbench](liquid-dae-workbench.md) is now exercised through the
actual browser: saved `.cfdproj`, both analysis modes, future heat commands,
separate equilibrium and 25 accepted samples through 120 s. Evidence is in
[nv-dae-browser-2026-09-26.json](../benchmarks/nv-dae-browser-2026-09-26.json).
