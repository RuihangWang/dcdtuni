# Current requirement evidence and remaining gates

This records the full task boundaries without equating component tests with
whole-platform completion. Latest user priority is solver-first, vectorized
PyTorch with CPU multithreading and a device-independent GPU migration path.
Default execution remains CPU-only and resource-limited. The user subsequently
authorized one bounded, single-physical-GPU acceptance episode; that episode
has now ended and its processes were cleaned up.

## Proven in the current working tree

- `.cfdproj` owns geometry/connections, air configuration and saved liquid
  experiments. React `compileSavedExperiment` emits v3 only after explicit DAE
  adoption; strict standalone backend and distributable JSON schemas validate
  the physical inputs. Save/reopen/compile and legacy compatibility tests pass.
- Geometry-derived connections compile to the acausal physical graph. The NV
  inventory fixture has 34 energy valves and 68 isolation valves in real series
  branches; each has a stable project-object-to-edge binding. The authoring
  project, compiler output, point-list count references and assumptions are
  retained in `nv-pek02-mock-fixture.md` and its generated mapping report.
- One residual supports direct steady Newton, implicit backward Euler and
  consistent initialization. History/input gradients use the converged-root
  adjoint. Independent analytic cases, temporal and spatial MMS, source/energy
  checks, blind storage and invalid topology admission have numerical evidence.
- Device/precision propagate across coefficients, state, indices and scales.
  Physical float32/float64 tests pass with explicit achievable tolerances.
  Residual profiling has no host scalar extraction and constant operator counts
  for 1 and 32 component batches. CPU1/CPU2 execute identical equations and
  agree to 1.04e-13 in scaled state on the NV fixture. This does not claim
  measured SIMD instruction counts or universal multithread speedup.
  A subsequent 2026-09-27 bounded CPU1/CPU4 run of the same immutable NV
  fixture passes. Scaled state mismatch is at most `1.04e-13`, the heat
  gradient is identical, and the single BE step took `1.4582/1.2697 s` on
  one/four threads. Separate steady/adjoint stages did not show speedup.
  The browser service now has CPU affinity `{0,1,2,3}` but saved project
  execution settings remain explicit and unchanged. See
  `nv-cpu1-cpu4-2026-09-27.json` and `cpu-resource-update-2026-09-27.json`.
  React now permits an existing v3 DAE `.cfdproj` to choose a saved 1/2/4 CPU
  thread budget; new mock adoption uses two threads. A Shinaide project saved
  with one thread was edited in the React panel, serialized/reopened in memory
  and exported as strict v3 with four threads. On 40901 the actual runtime
  reported four, two and one thread in separate CPU runs of that derived input;
  one BE step agreed to scaled state `2.53e-11`. Single short timings did not
  demonstrate a four-thread speedup. A separate browser tab actually loaded
  the saved 32-model/800-mesh Shinaide DAE project, changed the selector from
  one to four threads, observed the saved-execution label update, then undid
  and closed without writing the source project. See
  `shinaide-dae-ui-cpu-threads-2026-09-27.json`.
- Real Shinaide and NV mock trajectory gradients match independent central
  differences. NV includes simultaneous heat, pump-speed and positive valve
  opening changes. All reports retain source/input hashes and execution limits.
- The real browser loaded and saved the NV engineering project, compiled and
  ran direct steady and transient modes, changed future heat input, queried a
  separate equilibrium, and displayed primary/secondary and heat/storage
  curves. Run artifacts retain the exact compiled input and live input events.
- Shinaide now has direct hybrid-chain evidence: adopting/saving DAE preserves
  the complete air input and 12 STL hashes. The actual browser completed a
  600 s v3 run, mapped its 56 cold plates and 14 rack summaries, and exported
  real t=0/600 temperature scenes. CPU USD authoring and reopening preserve
  all geometry/transforms/UIDs and 928 relative texture references per frame.
  The LFS texture-pointer problem was reproduced and repaired; 58 real asset
  files are available. See `shinaide-dae-workbench.md` and its evidence links.
  A later actual browser cross-mode check retained the same 32-model project,
  saved snappyHexMesh air settings and 600 s/31-sample liquid result after
  air→liquid navigation. The air result selector showed no solve record; an
  OpenFOAM mesh/solve was not run in that check. See
  `shinaide-hybrid-browser-2026-09-27.json`.
  A subsequent isolated 40901 OpenFOAM check compiled this same saved DAE
  project to strict v2 `.cfdrun` plus 12 STL groups, generated a four-rank
  285,528-cell air mesh and executed ten steady solver iterations. The source
  project and original air inputs were unchanged; only scratch rank/time budgets
  differed. This verifies the air execution path reaches fields and sensor
  extraction, but **not** an accepted air prediction: three mesh-quality checks
  failed and convergence was `NOT_CONVERGED`. See
  `shinaide-air-smoke-2026-09-27.json` and the separately preserved first
  timeout attempt.

- Accepted online inputs now export as standalone strict v3 `executed.liquidrun`.
  NV 120 s and Shinaide 600 s runs were replayed from that file without passing
  a checkpoint; all eight recorded physical field groups are exactly equal to
  their original browser results. The original plans and editable projects stay
  unchanged. See `accepted-input-replay-2026-09-26.json`.
  Both replay result/input/checkpoint sets are now present on Mac. The local
  standard-library verifier independently rechecks all eight finite field
  groups, IDs, configuration hashes and accepted times without solving.
  These original runs predate runtime sidecars; their explicit
  `accepted-input-provenance.posthoc.json` companions identify posthoc archive
  verification rather than claiming to be live-run provenance. See
  `local-replay-evidence-2026-09-26.json`.
- v3 DAE online control now also accepts stable-ID pump `rpm_ratio` and
  positive valve `opening` commands, atomically with or without a heat change.
  They take effect only on the next accepted step, persist as actual schedules
  in strict `executed.liquidrun`, and survive v2 checkpoints/replay; old v1
  heat-only checkpoints still load. A real 1096-state Shinaide CPU step with
  heat 1.2, pump 0.9 and one valve 0.7 changed the selected valve flow and
  primary return temperature, conserved energy and replayed seven full field
  groups exactly from the accepted input. UI offers those controls with explicit
  pending/accepted values. See `shinaide-online-controls-heat-audit-2026-09-27.json`.
- A separate cold-plate heat audit checks each server's accepted CP input,
  liquid heat removal and Backward-Euler chip storage in W. Its API verifies
  the runtime's original and executed input receipts before marking an
  artifact eligible for further handoff; consistent-initialization sample zero
  is marked unchecked. The historical Shinaide result closes 1,736 server
  samples to `5.92e-8 W` maximum but lacks its original runtime receipt, so
  its report explicitly says `identity_verified=false` and `handoff_ready=false`.
  This is not yet an air-side heat allocation or coupled OpenFOAM prediction.
  An isolated actual React browser run then used a derived Shinaide project and
  separate scratch case to queue 120% heat, pump 0.9 and one valve 0.7. The
  pending controls did not change t=0; an independent equilibrium preview left
  the accepted history untouched. One 20 s step accepted all three controls,
  moved the primary-return curve, and displayed the 56-server heat audit.
  Original/accepted inputs, checkpoint and result were preserved outside Git;
  the runtime receipt verified their identity, and standalone strict-v3 replay
  reproduced seven complete field groups exactly. The accepted prefix had
  2/31 samples, so `handoff_ready=false`. See
  `shinaide-online-controls-browser-2026-09-27.json`.
- Final CPU and compatibility checks are recorded in
  `dae-final-verification-2026-09-26.json`: the current numerical selection passes
  86 tests with one CUDA skip; the actual-physical-graph GPU acceptance helper
  passes two CPU checks and skips its CUDA comparison. The frontend i18n repair
  passes a full 2547-test snapshot. A later bounded result-refresh fix passes
  42 related tests plus the production build. Actual browser selection,
  immediate refresh and English/Chinese switching all retain the same 31-sample,
  600 s Shinaide run. These checks did not start another numerical run or GPU.
- The one authorized CUDA phase now passes both actual opt-in tests: the
  generic implicit root/adjoint and the five-component physical graph with
  steady, three BE steps, and heat/pump/positive-valve gradients. The complete
  NV project-derived manifest also ran through `load_dae_input` and the
  tensor-only `DAEExperiment` on CPU and CUDA, including the hydraulic predictor,
  steady initialization, one BE step and a heat gradient. Scaled state difference
  is `1.23e-13`; the heat gradients are equal. Core/fixture hashes match the
  current worktree and the original fixture is unchanged. This is float64
  numerical evidence, not a claim of general GPU acceleration. See
  `single-gpu-numerical-2026-09-26.json`.

## Limits and unresolved full-platform evidence

- CUDA float64 numerical parity is now verified at the scopes above. General
  GPU performance, GPU float32 and larger sparse/matrix-free networks are not
  verified. The one NV load/initialization/step/gradient measurement took CPU
  `3.077 s` and CUDA `10.513 s`; this short case does not demonstrate a GPU speedup.
  The historical CPU reports retain their explicit CUDA skips.
- A live Omniverse RTX/WebRTC positive rendering session has not passed.
  CPU scene/texture/UID-to-USD conversion and safe disconnected fallback exist,
  but neither proves a seamless live renderer swap. Further rendering expansion
  remains deferred by the solver-first user correction.
  A read-only readiness check found an existing Kit build at
  `/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/Ref_code/kit-cae/_build/linux-x86_64/release/omni.cae_streaming.kit.sh`.
  No compatible live stream was listening on the checked ports. Current demo
  launchers couple their runtime and extension roots; a future authorized test
  must load this worktree's bridge rather than main's older extension. The
  readiness check did not start Kit, access a GPU or change the main checkout.
  In the subsequent authorized episode, the official Kit build loaded the task
  bridge and its official messaging dependency into private runtime paths;
  only physical GPU0/PCI16 was active. External signaling TCP connected, but the
  browser twice timed out before video/start and scene ACK, then retained Three.
  No actual engineering snapshot reached USD authoring in Kit. Both task-owned
  Kit/display processes were stopped before the eight-minute deadline; GPU0
  returned to its baseline. The live-renderer gate therefore remains **failed**,
  not merely unattempted. Receipts/logs and scoped diagnosis are in
  `single-gpu-kit-2026-09-26/`. CPU preparation now pins a single existing NVIDIA
  ICD and permits explicit small multi-core affinity (the next candidate is
  four cores with four tasking workers and two numerical threads). These
  changes have CPU plan tests but have not been verified by another GPU run.
  Further CPU compatibility repair adds bounded official multipart-ACK
  reconstruction and a render revision for material-only overlay changes at
  the same physical time. Passive SDK update/start/stats diagnostics are also
  available. Protocol/sync, overlay/sync and launch-plan selections pass 39,
  26 and 12 tests respectively, with overlaps rather than a combined coverage
  claim; the final production build passes. These changes still need a positive
  authorized live test. See `ov-cpu-repair-2026-09-26.json`.
- The current dense DAE runtime admits at most 4000 unknowns. Larger sparse or
  matrix-free scaling is not implemented or claimed by the small CPU benchmark.
- Exact valve open/closed active-set transitions and DAE PID controllers are
  rejected explicitly. Positive valve-opening and pump trajectories are solved.
- Primary hydraulic flow is prescribed; the HX is two mixed volumes and the
  cold-plate coolant uses quasi-steady NTU. No actual PEK02 vendor curves,
  calibrated parameters, telemetry or IT-electrical-to-liquid-heat mapping are
  claimed. Field calibration and energy-saving claims remain outside these
  synthetic verification results.
- The original Shinaide project still has 56 liquid-server `cooling.power`
  fields totaling 84 kW versus 21 kW of cold-plate `Q_W`; neither field is
  silently treated as total IT power. An isolated derived `.cfdproj` now
  states a separate **synthetic** 750 W/server IT budget and front/rear air
  allocation. React exports matching V3 liquid and V2 air project hashes plus
  12 export-time STL hashes. On 40901, a 4-thread CPU run completed 31 DAE
  samples; all 1736 server heat rows and original/accepted/result receipts
  passed audit. At 0/100/300/600 s the one-way liquid→air compiler produced
  21/25.2/16.8/16.8 kW, converted 56 rear faces to heat flux and 56 front
  faces to walls, and conserved watts again after measuring actual mesh patch
  areas. `run_cfd --mesh-only` wrote a source-bound mesh receipt, while
  `run_cfd --solve-only` rejected the generated mesh's quality failures before
  starting CFD. See `shinaide-hybrid-heat-cpu-2026-09-27.{json,md}` and the
  patch-area/sign reports in `docs/benchmarks/`. This is **not** an accepted
  wind-temperature prediction, bidirectional coupling, NV calibration or a
  repair of the legacy `inheritResults` path. A single local refinement trial
  removed low-determinant cells but retained skew and concavity failures.

The broad persistent task therefore remains active; the verified solver/mock
workbench is a concrete result, while the unresolved gates above stay visible.
