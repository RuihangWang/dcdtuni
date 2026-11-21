# E8 — one simulationConfig, separate air/liquid modes, nonlinear convergence (design + plan, 2026-10-01)

User rulings (2026-10-01, after E7): (1) keep ONE simulationConfig; (2) split the steady/transient choice into an air
mode and a liquid mode, both set only in the top bar — the air Solve tab no longer offers it, and the liquid Solve tab's
「分析模式」 moves to the top bar too; (3) transient steps must converge (the E7 gate runs used a fixed 2 outer
correctors with no residual control; p_rgh started each step's last corrector near 0.15), and the Solve tab lets the
user set the nonlinear thresholds for both steady and transient — two separate sets, default max 20 outer correctors
per step. (4) 8360 was stopped; gates start a temporary instance only. A visual redesign of the liquid Solve panel,
the UI and the 3D materials is a separate stage (E9), design-first.

## E8a — one simulationConfig (cfdrun v4)

Today the export carries simulationConfig twice: at the root (the documented contract, schema-validated) and inside the
legacy `meshGeometry` blob, which the runner actually executes from; a parity check compares five fields. The blob copy
is `buildCfdrunJson`'s projection of the same `runDefaults.simulationConfig` with defaults filled, plus three values
from elsewhere (room temperature from `geometry.temperature`, the monitoring config, gravity as a nested object).
`derived_config.derive_sim_config` already reproduces it from the root (golden parity tests).

- **cfdrun v4** = v3's contract with ONE simulationConfig: the root. `meshGeometry.simulationConfig` is absent (a v4
  manifest carrying it is refused); the root carries `roomTemperatureC` (required) and `monitoring`. The transient
  block is optional in v4 (present exactly when the air run is transient; schedules only with it).
- **Runner:** for v4 the executed blob's simulationConfig is `derive_sim_config(root, room_temperature=roomTemperatureC,
  monitoring=root.monitoring)`, injected before `CFDExport` validation, so every downstream reader is unchanged. v2/v3
  keep the blob path and the parity check.
- **Exporter (React):** always writes v4; drops the blob's simulationConfig after building it. The legacy HTML editor
  and blob-only tools keep writing v2 (still accepted).
- **Tests:** v4 load = derived config; v4 with a blob copy / without roomTemperatureC refused; every v2 golden fixture
  rewritten as v4 (blob copy moved to the root) loads to the identical runner config; schema accept/reject (Python +
  JS); exporter emits v4 without the blob copy. Gate: a real Slab solve from a browser-exported v4 manifest.

## E8c — nonlinear convergence (after E8a, so new keys live only at the root)

Probe (40901, OpenFOAM v2412, hotRoom tutorial): `residualControl { field { tolerance; relTol; } }` inside `PIMPLE`
converges each time step's outer loop ("PIMPLE: converged in 12 iterations") and then continues; at the limit it logs
"PIMPLE: not converged within N iterations" and continues; the run ends normally. v2412 has no
`outerCorrectorResidualControl`.

- **Keys:** steady keeps `residualControl` (SIMPLE stop thresholds) and `endTime` (max iterations). Transient adds
  `transientResidualControl {p_rgh, U, T, k_epsilon}` (per-step outer-loop tolerance, relTol 0) and uses
  `nOuterCorrectors` as the per-step maximum, default 20 (was 1). Separate sets, so switching mode never overwrites
  the other's thresholds.
- **fvSolution:** the transient template writes `residualControl` inside PIMPLE from `transientResidualControl` when
  `nOuterCorrectors > 1`; relaxation for the outer loop keeps today's rule (written when nOuterCorrectors > 1).
- **Verdict:** `convergence.py` counts steps "converged in N" vs "not converged within N" (last solver stage), reports
  the fraction that did not reach the in-step tolerance and the outer-iteration median/max; still no invented pass
  threshold for TRANSIENT_COMPLETED, but the count is surfaced in the summary line.
- **Solve tab:** a 「非线性收敛」 group shows the set for the current air mode (steady: stop thresholds + max
  iterations; transient: per-step thresholds + max outer correctors per step + inner correctors); hint text corrected.
- **Tests:** template text (PIMPLE residualControl present/absent), schema key, derive passthrough, verdict parsing of
  both log lines, Solve tab rows per mode. Gate: a Slab transient run where steps report converged-in-N.
- **Relaxation study (40901, Slab, 2026-10-01).** The outer loop uses the project's relaxation factors (one set, shared
  with steady: fields p_rgh 0.3, equations 0.7). At a fixed 0.01 s step (max Courant 23) every step hit the 20-iteration
  limit (p_rgh fell ~0.8× per iteration). With the adaptive step at maxCo 5 the first step hits the limit and every
  later one converges to p_rgh 1e-3 in 15–17 outer iterations (≈7 s per step on 8 ranks). p_rgh 0.5 / equations 0.7
  converged each step in 9–10 but the run diverged (Δt collapsed to 1e-9 s); 0.7 / 0.9 likewise; 0.7 / 0.9 at the
  fixed step blew up in two steps. Decision: keep the shared relaxation (stable), defaults p_rgh 1e-3 / others 1e-4
  and a per-step maximum of 20; a run that is too coarse in time shows up in the verdict as steps hitting the limit.
- **Also found in the gate:** a transient air run without keyframes had no transient block, so its timeline start
  never reached the runner (a T4 gap); the exporter now writes the block for every transient air run (v4 allows it
  without schedules).

## E8b — separate air and liquid modes

- **Model (cfdproj v5, additive):** `transient.airMode` and `transient.liquidMode` ('steady'|'transient');
  `transient.mode` becomes derived (transient iff either is) and is kept in step for older readers. The timeline, the
  freeze at t > 0 and playback exist while either mode is transient. `airMode` mirrors to `solverType` (the runner's
  truth); `liquidMode` maps to the DAE `analysis.mode`.
- **Writers:** one — `setSimulationMode(domain, mode)` from the top bar, which switches the mode of the active cooling
  domain (风冷/液冷). The air Solve tab loses its mode row; the liquid Solve tab's 分析模式 becomes a read-only line.
  The automation `setSolverConfig('solverType')` bypass routes through the same writer.
- **Keyframes:** air tracks apply only while `airMode` is transient, liquid tracks only while `liquidMode` is; the
  track panel says so when the active domain is steady.
- **Migration:** v5 files without the two fields derive them (air from `solverType`, liquid from the DAE analysis mode
  or, without a DAE, from `transient.mode`) — the migrated Shinaide/NV_PEK02 hybrids become air steady + liquid
  transient, which is what they already ran as. JS and Python twins, parity tests.
- **Tests:** mode writer per domain, derived timeline rule, migration, Solve tab rows, liquid panel read-only mode,
  exporters (air schedules only with airMode transient; liquid sampling only with liquidMode transient). Gate: switch
  each domain in the top bar and check the timeline, the track panel and both exports.

Order: E8a → E8c → E8b; each stage tests + browser gate + receipt before merging.
