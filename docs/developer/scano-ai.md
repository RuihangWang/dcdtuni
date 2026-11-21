# The SCANO AI solve (steady air)

The solver tab of an air-cooled steady project has two sub-tabs: **CFD 求解** (the OpenFOAM solve) and **AI 推理**,
which predicts the steady U, p_rgh and T of the room in seconds with a SCANO neural operator trained on DCTS (500
parametric GTC rooms). The prediction is an OpenFOAM run directory in `log/`, so the post-processing tab opens it
like any CFD result. Accuracy and timing: [docs/benchmarks/scano-ai-2026-10-27](../benchmarks/scano-ai-2026-10-27/README.md).

## Where things are

| Path | Role |
|---|---|
| `src/backend/aircooling/scano/vendored/` | vendored SCANO forward code (verbatim, `VENDOR.json`), checkpoints and model cards, DCTS statistics and training ranges ([README](../../src/backend/aircooling/scano/vendored/README.md)) |
| `src/backend/aircooling/scano/foam_mesh.py` | OpenFOAM mesh (reconstructed or per rank, joined without reconstructPar) → the cache's cell / boundary-face graph, with refine `c018c80`'s geometry |
| `src/backend/aircooling/scano/foam_fields.py` | first-iteration boundary values (ASCII field files; cyclic faces take their owner cells' values) |
| `src/backend/aircooling/scano/models.py` | the model cards: adapter, first-iteration recipe, training ranges, mesh ([the card format](../../src/backend/aircooling/scano/vendored/README.md#model-card-everything-dcdtuni-knows-about-a-checkpoint)); nothing else names a model or a dataset |
| `src/backend/aircooling/scano/adapters/` | one module per input format; `scano_dcts_v1.py`: DCTS cache v1, the in-memory room, the warm-up input |
| `src/backend/aircooling/scano/dcts_cache.py` | the DCTS cache v1 (geometry once per mesh, one directory per boundary state), patch roles from the cfdrun, the dataset-form rewrite, the fast-path rewrite |
| `src/backend/aircooling/scano/infer.py` | `ScanoModel`: a DCTS checkpoint on one device; the whole-room forward or the paper's TTA (`mode`, `passes`) |
| `src/backend/aircooling/scano/memory.py` | the GPU-memory estimate of a run and the automatic choice between whole room and TTA |
| `src/backend/aircooling/scano/foam_writer.py` | the prediction as an OpenFOAM run directory (meshes hard-linked, U / p_rgh / T binary) |
| `src/backend/aircooling/scano/pipeline.py` | work directories, plan (first iteration or not), stages, compatibility warnings |
| `src/backend/aircooling/scano/worker.py` | the long-lived inference process (one GPU, models kept loaded) |
| `src/server/scano_ai.py`, `server.py` `/api/ai/scano/*` | worker management (GPU chosen by free memory, `DCDTUNI_SCANO_GPU` to pin), the job |
| `STLGenRunner.run_first_iteration`, `run_cfd --first-iteration-dir --first-iteration-recipe` | the first iteration by the card's recipe (the runner knows no dataset) |
| `src/frontend/react/src/components/ScanoSolvePanel.tsx`, `ScanoGpuStatus.tsx`, `run/scanoInference.ts` | the AI sub-tab, its live GPU line, the inference choice |
| `case/DCTS/` | a DCTS room with the dataset's recipe ([README](../../case/DCTS/README.md)) |

## Pipeline

```
editor (solve body: the .cfdrun of what is on screen, no STL)
  -> POST /api/ai/scano/run {model, mode}                                   job, log in the editor
  -> [new mesh / new physics / new model input / mode exact]
       run_cfd --first-iteration-dir --first-iteration-recipe                <case>/ai/scano/<mesh>-<physics>/t1/
       case files regenerated from the cfdrun as solve-only does, solve-time dictionaries patched by the card's
       recipe (DCTS: refine stage 2b, tolerance 10, maxIter 1, one corrector; consistent no; boundary write
       fixedValue), one SIMPLE iteration on the recipe's ranks
  -> worker: graph + first-iteration values -> the card's adapter (DCTS: dataset form -> cache geometry + room)
             [auto, same mesh and physics]  the changed server / ACU patches rewritten, no solver run
           -> SCANO forward: whole room (every node query and context) or TTA (Deployment below)
           -> <case>/log/<stamp>_<case>__SCANO-<model>/
  -> post-processing opens the run (time 1: U, p_rgh, T)
```

The first iteration is the model's boundary input: SCANO reads the boundary-face values the solver writes after
one iteration, not the 0/ dictionaries. Two runner conditions differ from the dataset's (dcdtuni 7b53f528) at the
first iteration and are rewritten (`to_dataset_form`): server outlets T = paired inlet T + P / (rho cp Q) (the
current runner damps the outlet feedback and carries half the rise at iteration 1); ACU returns U = 0, p_rgh = 0
(the current runner pins returns to their supply flow). Both leave the converged CFD unchanged.

**Fast path** (mode auto): the work directory is keyed by the mesh identity, by the model input (adapter and
recipe: checkpoints that share them share the directory) and by the inputs that are not server or ACU parameters (patch set and types, porous baffles, sources, room temperature, gravity). When both match, the
boundary values come from the stored first iteration: changed outlets get -(Q/A) n, their temperature and p_rgh
following the new rise; supplies -(Q/A) n and the set point; inlets their profile scaled. Everything else keeps
its first-iteration value. Any other change runs a new first iteration.

## Real-time tuning (post-processing)

While post-processing shows an AI result, its left panel carries sliders for each ACU's supply airflow and
temperature and each rack's load (`ScanoRealtimePanel.tsx`). Releasing a slider writes the project (one undo step)
and posts the manifest of what is on screen to `POST /api/ai/scano/realtime` (synchronous, no job):

- the browser keeps the last full manifest and, while nothing but boundary values, timeline keyframe values and
  display state changed (`geometryKey`), recomputes only the ACU and server bindings with the export's own
  translators (`rebindAirParameters`, about 0.3 s instead of a full export);
- the server answers 409 `needs_first_iteration` when the mesh or the physics key changed (run the AI solve once);
  otherwise the worker takes the fast path on the room it keeps in memory (`pipeline.Room`) and the prediction
  replaces the loaded result's fields in place (`CachedPostProcessor.replace_fields`: sparse cell→point averaging
  operators equal to vtkCellDataToPointData, VTK arrays written in place, the display triangles through their
  source points) and the panel redraws every layer on the new revision;
- edits made during an inference collapse into one follow-up with the latest state.

DCTS room on an RTX 4090, warm: about 1.9 s from release to the new field (manifest 0.3 s, worker 1.2 s of which
forward 0.71 s with TF32 matmul, injection 0.3 s), plus the redraw. The status line's `data-timing` attribute
carries the breakdown.

`./src/start_server.sh --scano [MODEL]` starts the worker with the server and warms the model up (the first CUDA
forward of a process costs about ten seconds; the warm-up uses the newest work directory's room, else a two-cell
synthetic one), `--scano-gpu <uuid|index|cpu>` pins its card. Matmul precision: TF32 by default (same accuracy on
the validation rooms, forward 1.2 → 0.71 s); `DCDTUNI_SCANO_MATMUL=fp32` restores full precision.

## AI-initialised CFD

The CFD sub-tab's first row, 初始化: 均匀场初始化 (the generated uniform field) or AI 推理初始化. With AI the
solve request carries `aiInit: {model}` (the AI sub-tab's model, else the recommended card; `run/solveInit.ts`).
Inside the solve job the server first runs the AI solve of the same mesh and boundary conditions
(`_scano_infer_in_job`: a reused work directory and room cost about 1–2 s; otherwise the first iteration plus a
forward), turns the prediction's cells into the case's serial cell order through the first iteration's
cellProcAddressing (`pipeline.initial_fields`; a partition-only mesh keeps rank order, and a solve on another rank
count maps it through the addressing `reconstructParMesh` wrote, kept in `<run>/reconstructed-from/`), and runs the
solver with `run_cfd --initial-fields <npz>`: after the solve decomposition
`runner/initial_fields.py` replaces the internalField of U, p_rgh and T in every 0/ file (ASCII or binary,
processor patch values untouched), boundary conditions as generated; the run directory keeps
`initial_fields.json`. Room 0112: 328 instead of 1017 iterations to the stop rule, about 30–45 % fewer at equal
residuals ([receipt §5](../benchmarks/scano-ai-2026-10-27/README.md)). The scripted API takes the same path with
`--ai-init <model>` (`run_air_case(ai_init=...)`); `DCDTUNI_SCANO_GPU` picks the card of its private server.

## Post-processing helpers

- **Max / min points** (surface-field group, 显示最高 / 最低值点): markers and a list for the surface (visible patches
  only) and every visible slice (`postprocess/ppExtremes.ts`).
- **Snapshots** (real-time panel, 📸 记录快照 / 快照对比): up to six real-time results kept with their boundary
  conditions (`postprocess/scanoSnapshots.ts`) and shown side by side over the viewport (`ScanoSnapshotView.tsx`):
  one renderer, a scissor pane each, one camera (orbiting one pane turns all), one shared colour range per layer,
  each pane's BCs and max / min. Headless browser-use screenshots do not capture this second WebGL canvas; read it
  with `canvas.toDataURL()` inside a requestAnimationFrame callback.

## Deployment: whole room or TTA (2026-10-29)

The AI tab's 推理方式 (auto / whole / TTA, plus the TTA passes R) goes with every AI solve and real-time update
(`inference`, `passes` in the request body; the worker's `_predict`).

- **whole**: one forward with every node as query and context. Its memory grows with the room: a DCTS room
  (1.39 M nodes) peaks at about 11 GB with SCANO, Base2-9 (8.69 M nodes) would need about 64 GB.
- **TTA** (the paper's test-time aggregation, `scano cfg_common.tta`): the node pool in chunks, each chunk's
  2-hop subgraph predicted with anchors drawn as in training, predictions averaged by coverage; R passes repeat it
  over other permutations. A chunk holds as many pivots as one training draw on a training room
  (`sample_ratio x 1,387,891`), so each forward is the size the model was trained on. Seeds are fixed: a
  request repeated gives the same prediction.
- **auto**: the whole room when its estimated peak (with a 10 % margin) fits the free memory the worker can use
  (driver free plus its own unused cache), else TTA (chunks halved, down to 1/8, until it fits); an auto
  whole-room forward that still runs out of memory is repeated as TTA. A card's `"inference": {"auto": "tta"}`
  makes auto take TTA whenever it fits (AB-UPT: its whole-room forward lets every query attend to every node,
  minutes per room, at the same error as TTA in seconds).
- The estimate (`memory.py`): `node_bytes x N + row_bytes x rows`, coefficients in the card's `memory`, fit on an
  RTX 4090 ([receipt](../benchmarks/scano-tta-2026-10-29/README.md)); a room's measured peaks
  (`<base>/memory.json`) replace it after its first run.
- The tab's GPU line (`GET /api/ai/scano/gpu`, every 2 s): the worker's card, used / total memory, what the worker
  holds, the memory available to the next run, the room's nodes, the deployment auto would pick with its estimate,
  and both estimates. The result says how the model ran (mode, passes, forwards, peak).
- A room larger than a training room draws a training room's anchor count in TTA (`infer.anchor_scale_for`, not below
  1/10 of the trained fraction; on the DCTS rooms x1/10 changed the error by +0.0015), and a TTA chunk that runs out of
  memory continues the room at half its size (down to 1/8 of a training draw). SCANO's memory law has a boundary-face
  term: every query row attends to the room's boundary records.
- The warm-up at load uses the case's room only when its whole-room forward fits, else the synthetic input (a
  Base2-9 warm-up ran the GPU out of memory and kept 9.4 GB cached). Every run gives the allocator's cached
  blocks back (the worker shares its GPU; keeping them saved nothing measurable), so between runs the worker holds
  the model and the CUDA context, about 0.5 GB.

## Models are cards

A future checkpoint (a larger model trained on more rooms) is a new directory under
`src/backend/aircooling/scano/checkpoints/` with its `model_card.json`; the model selector lists it, the runner takes its
first-iteration recipe, the result and the real-time sliders take its training ranges, the AI tab its training mesh.
Code changes only for a new input format: one adapter module ([format and contract](../../src/backend/aircooling/scano/vendored/README.md#model-card-everything-dcdtuni-knows-about-a-checkpoint)).

## Accuracy limits (what the UI says)

Everything below comes from the card of the chosen model; the numbers are those of the two DCTS checkpoints.

- Trained on snappyHexMesh meshes with a 0.34 m base cell. A dcCart mesh or a different refinement runs, but its
  accuracy is not established (the tab warns when the project's engine is not snappy). For a room the model
  should serve, build the project with `case/DCTS/make_dcts_project.py` or the same mesh settings.
- Outside the training ranges (`training_ranges.json`: server 400–2200 W and 0.03–0.27 m³/s, ACU supply
  0.47–7.4 m³/s at 20 °C, 18–273 servers and 2–6 ACUs per room, 1.3–1.8 M nodes) the result lists a warning;
  the prediction has no support there.
- A fast estimate for screening; the CFD solve remains the reference.

## Operation

- Worker GPU: `--ai-gpu` / `DCDTUNI_SCANO_GPU=<uuid|index|cpu>` (default GPU 0), started only with
  `DCDTUNI_SCANO_MIN_FREE_MIB` free (default 6000: a DCTS room under TTA peaks at about 3.8 GB). One request at a
  time; the whole-room forward of a DCTS room peaks at about 11 GB.
- Work directories: `<case>/ai/scano/` (git-ignored), the newest three kept; result directories in `log/` keep
  their meshes through hard links.
- Tests: `src/backend/tests/test_scano_ai.py`, `test_scano_tta.py`, `src/server/tests/test_scano_gpu_status.py`, `src/frontend/react/test/scanoSolvePanel.test.tsx`,
  `scanoRealtimeKey.test.ts`; the validation
  scripts in the benchmark receipt rerun the accuracy check.
