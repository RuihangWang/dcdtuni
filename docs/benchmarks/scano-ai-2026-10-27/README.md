# SCANO AI solve — accuracy and timing on the DCTS test rooms (2026-10-27)

What was checked: that the AI solve of the current dcdtuni (`backend.aircooling.ai`, solver tab → AI 推理) gives a
DCTS room the inputs SCANO was trained on and therefore its error, from the mesh to the prediction.

Reference data: Google Drive `SCANO/realtime_demo_20261027/` (checkpoints, the four timing rooms with their
JUPITER OpenFOAM case, model input cache and converged CFD target); SCANO commit `c872d2a7`; model
`scano_c118_s18`. Error = the paper's metric (mean over u, v, w, p_rgh, T of the relative L2 error, every node).
Host 40901, RTX 4090, OpenFOAM v2412 (conda).

## 1. The extraction reproduces the training inputs (room 0112, the JUPITER mesh)

`e2e_example.py` on the JUPITER case (decomposed into 8 ranks, our first iteration, our cache): geometry against
the published cache — positions within 1e-6 m, volumes / areas / normals to float32 round-off, kinds, patches and
edges identical; boundary values per role (`compare_bc.py`): T within 1.5e-5 K, p_rgh within 2e-9, U within 5e-4
m/s. Prediction error 0.4606, the same number (to the last digit, per field) as the published cache gives on this
machine (SCANO's own timing run: 0.4675 on a GH200, torch 2.10).

Three causes found and fixed on the way (each moved the inputs, none is visible in a mesh check):
- the dataset's first iteration is refine stage 2b's (fvSolution tolerance 10, maxIter 1, nCorrectors 1: linear
  solves exit at once), not the production run's iteration 1;
- the dataset's solver has no SIMPLEC form: with `consistent yes` the fixedFluxPressure faces' p_rgh came out 0.565x;
- cyclic baffles take their owner cells' values (the VTK reader on the reconstructed mesh); a processorCyclic part
  of a decomposed baffle carries an interpolated value, which is ignored.

## 2. The current main's mesh and boundary conditions (case/DCTS)

`case/DCTS/make_dcts_project.py` turns a dataset cfdproj into a project with the dataset's recipe (snappyHexMesh,
base 0.34 m, 24 processes, relaxation 0.3/0.5/0.5/0.3, baffles fully open). `compare_case.py` on room 0112
(`mesh_0112_vs_jupiter.json`): 897,361 cells against 916,595, the same 685 patches and types, the same volume
and cell-size quantiles, the same maximum skewness (4.319 on 2 faces). The `0/` comparison found four runner
differences from dcdtuni 7b53f528; perforated baffles are now fully open in the project (new blanking type
"fully open"); the damped server-outlet feedback and the pinned ACU returns are rewritten in the first iteration
(`dcts_cache.to_dataset_form`); the server-inlet T condition gives the same first-iteration value.

## 3. End to end through the server (`validate_rooms.py`, `validate_rooms_2026-10-27.jsonl`)

Mesh with `backend.api air --mesh-only --mesh-engine snappy`, then `/api/ai/scano/run` (mode exact), then the
prediction against the JUPITER CFD target carried to the new mesh by nearest node (`eval_base.py`):

| room | cells (new mesh) | error, this pipeline | error, SCANO reference (GH200) | AI job | worker | forward |
|---|---|---|---|---|---|---|
| 0112 | 897,361 | 0.4606 | 0.4675 | — | — | 1.2 s |
| 0388 | 1,101,659 | 0.4357 | 0.4402 | 34.2 s | 9.8 s | 1.5 s |
| 0390 | 879,559 | 0.3635 | 0.3620 | 26.1 s | 9.4 s | 1.2 s |
| 0414 | 983,414 | 0.3650 | 0.3669 | 32.1 s | 9.4 s | 1.3 s |

AI job: the whole request (first iteration on 8 ranks + reading it + cache + forward + writing the run directory);
worker: everything after the first iteration. A boundary-only change (one server's load doubled, one ACU's flow
halved) takes the fast path: 3.3 s in the worker, the same boundary values as a new first iteration except the
p_rgh of the changed outlet (fixed: it scales with the temperature rise; exact on the doubled load). The first
forward of a new worker costs about 10 s more; the AI tab warms the worker up when it opens.

## 5. AI-initialised CFD (room 0112, 2026-10-27 evening)

The same project solved twice through the editor (CFD 求解, 24 processes, steady, stop rule: every ACU return-air
temperature within 0.5 K over the last 200 iterations), once from the uniform field and once with 初始化 = AI 推理
(`scano_c118_s18`; the prediction reused the case's first iteration: 1.2 s, then `run_cfd --initial-fields`
seeded the 897,361 cells on 24 ranks, boundary conditions as generated). Runs `case/DCTS/log/2026-10-27-18-05-30_DCTS`
and `2026-10-27-18-14-48_DCTS`.

| start | verdict | iterations | wall time (run directory log) | final T / Ux / k residual |
|---|---|---|---|---|
| uniform | CONVERGED | 1017 | 7 min 9 s | 1.9e-4 / 4.4e-4 / 3.3e-4 |
| AI prediction | CONVERGED | 328 | 3 min 5 s | 5.7e-4 / 1.2e-3 / 1.7e-3 |

At the same residual level the uniform start needs 450–590 iterations (first iteration after which its T, Ux, k
residuals stay at or below the AI run's final ones for 20 iterations: T 588, Ux 489, k 449), so the AI start saves
about 30–45 % of the iterations at equal residuals; the stop rule ends it earlier still. The two converged fields
(both mapped to the serial cell order through cellProcAddressing): |ΔT| mean 0.48 K, 95th percentile 1.3 K, 99th
2.4 K, max 7.9 K (T range 20.0–45.9 °C); final return-air temperatures differ by 0.26–0.44 K, inside the stop
rule's 0.5 K. Both stops are the rule's, so neither field is converged further than it.
