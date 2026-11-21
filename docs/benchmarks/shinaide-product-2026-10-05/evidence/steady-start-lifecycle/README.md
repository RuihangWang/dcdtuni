# Actual steady-converged initialization and failure evidence — 2026-10-05

All browser export, native mesh and solver operations used the React app through the authenticated server API, launched by start_server.sh on 40901 isolated ports 8392/8393 with CPU affinity 36–39 and CUDA disabled. Native runs did not use a direct runner CLI. Source/binary identity is preserved where relevant. Raw server log timestamps are UTC on 2026-10-04; this record date is SGT on 2026-10-05.

## Accepted scope

accepted-conduction-mpi2/ proves the finalized two-rank partition lifecycle for a **quiescent, nonuniform temperature conduction smoke test**. Actual checkMesh -allTopology -allGeometry -parallel accepted 495 cells (247/248), no failed checks. The custom SIMPLE binary reached its original residualControl convergence marker at iteration 153; the original thresholds were p=1e-4, U/T=1e-5, k/epsilon=1e-4. Full native surfaceScalarField phi, all internal fields and the actual same partition mesh were inherited into transient time 0. New time-0 temperature boundary conditions remained correct. PIMPLE reached End at 0.02 s with finite fields and converged outer loops at each step.

The accepted conduction phi is **uniform zero**. That run proves full-field inheritance and its partition/header lifecycle; the later nonzero-channel evidence below establishes genuine nonzero-flow inheritance. The per-rank initial, archived steady and final nine fields, five topology files, actual manifests/projects, complete solver logs, strict checker SSE section, execution receipt and binary SHA snapshot are preserved. audit_accepted.py verifies these artifacts without running a solver.

## Failures retained without changing their acceptance oracle

| Directory | Actual observation |
|---|---|
| empty-mesh/ | An original scratch seed lay inside the ACU solid. Native wrote globally zero cells and an old SIMPLE path produced an End/convergence message together with -nan continuity. The old path incorrectly accepted it. Preserved inputs/geometry/logs remain unchanged. The new preflight rejects global zero cells and nonfinite actual diagnostics; a later native writer guard rejects global zero as well. A different fixture seed in genuine fluid is an explicit fixture correction, not a reinterpretation of this run. |
| strict-rejected-near-wall/ | Hot block was too close to a wall on the coarse mesh. Full geometry check reported three underdetermined cells; no solver acceptance was attempted. |
| thermal-body-counterexample/ | Actual hot body STL names lacked the suffix emitted by the previous exporter. Manifest thermal bindings therefore did not match the six native hot faces, which were generated as zeroGradient. The collector now derives names from real face islands and verifies actual grouped STL geometry/area. The three committed temperature/power/scaled exporter→generator fixtures independently cover this repair. |
| nonconverged-corrected-recirculation/ | After the thermal binding fix, genuine internal ACU Q=0.005 m³/s still reached the 10000 iteration cap without SIMPLE residualControl. It was refused as a steady seed. Raw original manifest bytes were not separately retained for this older run; the actual export SHA receipt, original project and solver log are retained. |
| nonconverged-channel-q005/ | Straight channel, actual Q=0.005 m³/s, 511-cell two-rank full strict mesh, flowRateInletVelocity inlet and pressure outlet, hot block 32°C and gravity zero. At 10000 iterations, U=0.0242345, p=0.0144945, T=1.56432e-5, epsilon=0.000477251, k=0.000560575. No convergence marker; correctly refused. |
| nonconverged-channel-q005-precise/ | Same physical geometry/flow and original outer thresholds, React explicitly exported pressure PCG/GAMG tolerance 1e-12, relTol 0, maxIter 1000. At 10000 iterations, U=0.0235405, p=0.0187828, T=1.49069e-5, epsilon=0.000468688, k=0.000661523. Still correctly refused. This does not support pressure linear precision alone as the cause. |
| nonconverged-channel-q05/ | Separately saved Q=0.5 m³/s channel physics, default linear settings and unchanged original outer thresholds. At 10000 iterations, U=0.0441662, p=0.0305109, T=0.000362986, epsilon=0.00923286, k=0.0553027. Correctly refused; it did not replace the Q=0.005 evidence. Initial/final raw fields and templates are retained for source diagnosis. |

No cap-ended, globally empty, nonfinite or strict-quality-failed run is presented as an accepted steady seed. The later accepted nonzero-channel runs below supersede the earlier unaccepted nonzero status. These tiny fixtures do not establish Shinaide airflow convergence or large-case runtime.

## Subsequent derived-flow boundary fix and genuine nonzero acceptance

Commit fa4d6467 changes only flow-patch nut/alphat from fixedValue to calculated; initial internal seeds, wall/coupled boundaries, topology, turbulence backflow settings and all residual thresholds remain unchanged. Installed realizableKE.C uses a strain-dependent rCmu, not the standard k-epsilon constant 0.09. Fixed-value patch assignment ignores the model-derived update; calculated boundaries accept it. The original Q=0.005 field had inlet k=3.75e-7, epsilon=5.390521017e-10 but fixed nut=0.09. The fixed field was also above the realizable rCmu≤1/A0 bound (A0=4). Before/after raw fields and installed source are in derived-flow-diffusivity/.

Both actual React exports retain the original physical manifest exactly after excluding fresh runId/exportedAt metadata, and both final five-file mesh partitions are byte-identical to the pre-fix channel. Original full allTopology/allGeometry checks and outer criteria remain in place.

- accepted-nonzero-channel-mpi2/: default linear settings. Genuine SIMPLE convergence at iteration 337; native nonzero phi, all eight volume internal fields and both mesh partitions inherited unchanged into time 0. Inlet total phi -0.005 m³/s; return +0.0049999995505 m³/s with zero backflow faces. PIMPLE reached End at 0.02 s, two steps, neither hit the outer iteration cap.
- accepted-nonzero-channel-precise-mpi2/: same physical Q=0.005 case with the previously exported explicit pressure tolerance 1e-12/relTol 0/maxIter 1000. Genuine SIMPLE convergence at iteration 337 and PIMPLE End at 0.02 s. Canonical SIMPLE066/PIMPLE879 binaries were unchanged.

accepted-current-simple-nonzero-mpi2/ pairs the current pressure face-assembly compatibility source (8cc1c06b, exact source bytes verified against integration) with installed SIMPLE SHA7932473e304c68aba242e1c9947eeb0586c1aebea911b6e567e19e3d85316257 and unchanged PIMPLE879. Exact executable fuser checks found no users before atomic physical replacement; old066 bytes remain in the repository. The same actual React Q=0.005 default case again reached SIMPLE iteration337 and PIMPLE End0.02; all nine saved fields at seed/steady/final and both mesh partitions are byte-identical to the preceding accepted066 run. Installation, build/source and native receipts are preserved.

These are short physical lifecycle tests. The existing transient verdict treats timestep/Courant/residuals as diagnostics: PIMPLE convergence markers do not establish that every final turbulence-update initial residual is below an outer criterion. Raw final residuals remain available; no temporal-accuracy or target-case convergence claim is inferred.

## Recheck preserved accepted artifacts

From this directory, run:

    python3 audit_accepted.py

The CFD job IDs, original project/input bytes and actual API receipts are in their corresponding directories. Failed native SIMPLE logs use gzip without altering their contents. The dedicated 8392 service is retained for the subsequent source diagnosis; no shared service was stopped.
