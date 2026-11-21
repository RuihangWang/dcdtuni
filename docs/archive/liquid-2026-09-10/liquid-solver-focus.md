# Differentiable liquid solver: current priority

User correction on 2026-09-22: close the existing React frontend and focus on the **differentiable liquid-cooling solver**. Air cooling remains on OpenFOAM, with the user's own Newton/JFNK solver a future integration. Further frontend expansion, OV, agent fine-tuning and new case variants are deferred. Use focused tests and measured performance; do not keep spawning broad review/implementation loops.

## Measured first optimization

`LiquidSimulation._step_graph` now shares immutable topology/material tensors and copies only per-step containers and selected schedules. Numerical kernels do not mutate these shared tensors. Controlled pump/valve arrays remain isolated from both the original graph and other time slices.

Single-CPU-thread synthetic closed-loop fixture, 120 advancing steps / 121 samples, changing pump schedule, same process and interleaved before/after runs against commit `9c93ce2f`:

- Median stepping wall time: **0.850934 s → 0.638077 s**, approximately **25% less** (1.33× throughput in this small probe).
- Every pressure, flow, node temperature, chip temperature and edge outlet temperature sample has the same SHA-256 across all six runs.
- Timing varies between runs; this is not an NV-scale, GPU, real-time, or accuracy claim. Initialization, browser transport and checkpoint I/O are excluded.
- Evidence: `docs/benchmarks/liquid-runtime-slice-ab-2026-09-22.json`. The earlier unpaired measurements are not used to claim the speedup.
- Validation: 26 runtime/API/manager/performance tests passed, plus the interleaved A/B test. No wall-time thresholds are asserted in CI.

Reproduce from the repository with its dcdtuni Python environment:

```bash
PYTHONPATH="$PWD/src" OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 CUDA_VISIBLE_DEVICES= \
  LIQUID_BENCHMARK_AB=1 python -m pytest -q -s \
  src/backend/tests/test_liquid/test_runtime_ab_benchmark.py
```

The opt-in benchmark extracts only the committed baseline `_step_graph` function and compares it with the current implementation on identical synthetic inputs.

## Next numerical work, in order

1. Establish one differentiable tensor rollout, separate from JSON/UI sessions. Current thermal/PID kernels have local gradient tests, but the session uses `no_grad()` and `float()` controls; it is **not** an end-to-end differentiable control trajectory. Reuse the own-repository hydraulic implicit-adjoint foundations, then check multi-step gradients against finite differences in smooth regimes. Switching, exact valve closure and clamps require explicit gradient semantics.
2. Verify transient energy balance, timestep convergence, load/valve/pump events, reversed and near-zero flow, and stiff cases. Current hydraulics are quasi-steady; this does not resolve fluid inertia/water hammer. Cold-plate coolant storage and full primary-loop dynamics remain simplified.
3. Profile hydraulic Newton, thermal assembly/solve, graph preparation and I/O separately on increasing synthetic topologies. Consider reuse when hydraulic inputs are unchanged, reduced repeated differentiation/assembly and suitable linear solves only after measurements justify them. Record both runtime and numerical agreement.
4. Fix the already recorded runtime checkpoint/persistence findings: restore to the same case, validate before allocation, coherent history/state, and reliable failed status on write failure. Performance work must preserve recoverability.

## Match to NVIDIA PEK02

The supplied point list supports preparation of a CDU–secondary-loop–rack model with aggregate temperature/pressure/flow and pump/valve states. It is an interface plan, not calibrated operating data. The project priority is load disturbances and control response; use aggregate rack equivalents first and a replaceable, provenance-tracked equipment pack.

Before claiming calibrated predictions or control benefit, obtain topology and actual CDU type, aligned time series with sampling/latency/quality flags, pump/valve/HX performance, controller/actuator behavior and the IT-power-to-liquid-heat split. The supplied CDU70 liquid-to-air literature must not parameterize an assumed liquid-to-liquid installation. Current fixed facility reservoir cannot validate full primary-loop dynamics. Rack-level observations do not uniquely identify every chip/cold plate. No independent CDU/pump electrical-power point has been confirmed, so pump speed alone cannot establish energy savings.

## Progress: differentiable transient entry

The tensor-only `solver/rollout.py` and first-order implicit `solver/differentiable_hydraulic.py` are now implemented and tested. The JSON session intentionally remains nondifferentiable; optimization uses the tensor entry. Eighteen focused tests validate multi-step and closed-loop gradients, forward agreement, causal schedules and a bounded mock parameter fit. See [API and exact limits](../../developer/liquid-differentiable-rollout.md). This supersedes the earlier statement that the tensor rollout still needed implementation; scale/performance validation and existing runtime reliability findings remain open.
