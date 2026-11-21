# Proposed bounded CUDA and live-renderer acceptance

Status: **the approved one-time episode has ended**. Numerical CUDA acceptance
passed; live OV rendering did not. The sole selected card was physical GPU0,
UUID `GPU-dd97ad37-4a36-19df-d789-0ee503ba4d93`. The original episode deadline
was preserved through bounded retries and cleanup completed about nine seconds
before it. See `single-gpu-numerical-2026-09-26.json` and
`single-gpu-kit-2026-09-26/` for actual evidence. The CPU-only default is restored;
this original proposal cannot authorize another GPU episode.

## Current readiness

On 2026-09-26, a read-only socket check on 40901 found only the task's CPU
HTTP/WebSocket service at 8360/8361. The checked OV ports 8301/49100/8081 had
no listener. Main's existing official Kit launcher and `kit/kit` executable
exist at:

`/mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/Ref_code/kit-cae/_build/linux-x86_64/release/`

The task's new bridge is in the isolated worktree:

`/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform/src/frontend/omniverse/cfd_demo/kit/extensions/`

The existing demo launcher couples the runtime and bridge roots and can launch
an additional AI worker. Do not use `DC_REPO_ROOT=main` as a substitute for
testing the new worktree. The official Kit launcher can instead be invoked
with `--ext-folder` pointing to the task extension directory and
`--enable dc.cfd_bridge`; it does not require rebuilding or copying main.

## Proposed execution envelope

- Host: 40901, isolated task checkout. One currently available GPU, selected
  only after approval and a fresh resource check. Sequential numerical and
  renderer phases; no concurrent AI/training job.
- Total GPU validation ceiling: eight minutes, with explicit timeout and
  cleanup of only task-owned processes. CPU affinity remains one core, nice 10,
  and OMP/MKL/OpenBLAS threads remain one. GPU multi-device rendering is disabled.
- Numerical phase: the existing opt-in tests in `test_dae_devices.py` and
  `test_equation_graph_cuda.py`; select the actual CUDA comparison tests, rather
  than repeating all CPU finite-difference cases. The physical graph includes
  all five component families, steady initialization, three implicit transient
  steps and steady/trajectory heat, pump-speed and positive-valve gradients.
  Capture actual device, timing and GPU memory; a skip is an unverified result.
- Renderer phase: use the existing official Kit build, the new task extension,
  and a task-owned display/log/texture-cache location. The observed shared X99
  display belongs to another user and must not be stopped or commandeered.
  Do not run the legacy demo's AI worker; keep `DC_CFD_AI=0`.
- Browser: the current React workbench, saved Shinaide DAE project and completed
  run `737a0ecc25e342acb7932356222e566f`. Exercise Three → OV → Three on the actual
  t=0/600 scenes, with existing temperature overlays enabled. Check streamed
  pixels, decoded video dimensions, UID/time/camera ACK and return-state
  preservation. Transport ACK or CPU USD conversion alone is insufficient.
- Record failures or timeouts without installing dependencies, modifying main
  or silently substituting the standalone sample scene. This acceptance run
  does not add OV features or change the solver implementation.

## Required evidence before marking the remaining gates verified

CUDA must actually execute independent CPU/GPU root and adjoint trajectories
and satisfy the existing tolerances; report the fixture's scope, without
claiming large-network performance from the small acceptance graph.

The live renderer must visibly stream the actual engineering geometry,
materials and changed temperature scene, preserve UID/time/camera state across
the swap, and return to the retained Three scene. A present process, open port,
schema pass or authoring ACK cannot prove this requirement by itself.

The numerical gate now has actual float64 evidence, including the complete
1604-state NV schema/tensor entry. GPU acceleration and untested precisions are
not inferred from that result. The live-renderer gate remains failed: two client
timeouts, no video/start and no scene ACK. CPU-only configuration fixes now pin
a single existing NVIDIA ICD and two tasking workers on the same one-core
affinity; those fixes are plan-tested but have not been GPU-validated. Any next
GPU test requires a new explicit one-time authorization.
