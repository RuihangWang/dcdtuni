# Post-Processing

After the solver finishes, DCTwin provides several ways to inspect temperature, velocity, and pressure
results: the Post-Processing tab in the editor, a programmatic REST API, ParaView, and real-time
sensor probe queries. Solver results live in `case/<name>/log/<timestamp>_<name>/`; `Mesh/` is the
working directory that is reset on every mesh.

---

## 1. Browser-Based Post-Processing

When the solver run completes successfully, the **📊 Post-processing** tab becomes active in the left
sidebar. Click it to open the visualization panel. The steps below describe the React editor; the
legacy HTML editor uses the same labels and element ids.

The results can be drawn by either renderer. **Three.js** (the default) draws them in the browser.
When the launcher was started with `--ov`, the renderer switch next to the viewport offers
**Omniverse**: the same surface fields, volumes, slices and streamlines are then produced inside the
Kit display with kit-cae's native tools (IndeX volume rendering, GPU streamlines) and streamed to
the page. The post-processing panel is the same in both cases.

### 1.1 Enabling post-processing

The panel starts with a **Result Selection** section (i18n key `title.result_selection`):

1. Use the **Solve Records** dropdown (`label.solve_records`, element `pp-log-run`) to select which
   simulation run to load. The list is populated from `log/<timestamp>_<caseName>/` directories
   under the current case.
2. Optionally choose a **Time Step** (`label.iteration_step`) if the run has multiple write times.
3. Click **Enable Post-processing** (`btn.enable_postprocess`, element `btn-init-postprocess`) —
   the editor posts to `/api/pp/init` with the selected run directory and time. The server reads
   the native OpenFOAM result directly (including the processor partitions a dcCart run keeps), so
   no `foamToVTK` conversion is needed. A progress dialog appears; click Cancel to abort.

Once loaded, the controls below are shown.

### 1.2 Surface Fields

Visualize field values on every boundary patch (section header `pp.surface_field_render`):

1. Choose a **Field** from the dropdown (`pp-field-select`)
   — typical options after a buoyantBoussinesq solve: `T` (temperature, K), `U` (velocity, m/s),
   `p`, `p_rgh`, `k`, `epsilon`, `omega`, `nut`, `alphat`. The exact list comes from the result files.
   Vector fields (`U`) expose magnitude and `Ux`, `Uy`, `Uz` components; `speed` = |U| magnitude.
2. Choose a **Preset** colormap (`pp.preset`).
3. Adjust the **Min / Max** range inputs and click **Apply** (`common.apply`). Click
   **Auto Scale** (`pp.auto_scale`) to reset to the field's data range.
4. Toggle **Show Surface Field** (`pp.show_surface_field`) to show or hide it.

The 3D viewport re-colors every visible patch instantly; the legend updates to match.

### 1.3 Slices

View a 2D cross-section through the volume (section `pp.slice_settings`):

1. Check **Configure Slice** (`pp.config_slice`) to expand slice controls.
2. Set the **Normal Direction** (`pp.slice_normal`) — choose a preset axis or `Custom`
   (`pp.slice_custom`) and enter `[x, y, z]`.
3. Set the **Origin Position** (`pp.slice_origin`) — world coordinates in metres. The server
   clips the internal mesh at this plane and returns triangulated geometry + field scalars.
4. Choose the **Slice Field** (`pp.slice_field`) and **Colormap** (`pp.slice_colormap`).
5. Click **Apply** (`pp.apply_slice`), which posts to `/api/pp/slice`. The cutting plane appears in the 3D viewport.
6. Toggle **Show Slices** (`pp.show_slices`) or **Show Preview Ring** (`pp.show_preview_ring`).

### 1.4 Streamlines

Visualize air-flow paths (section `pp.streamline_settings`):

1. Check **Configure Seed Sphere** (`pp.config_seed_sphere`) to expand the seed controls.
2. Set **Seed Center** (`pp.sl_center`) and **Radius** (`pp.sl_radius`) of the seed sphere.
3. Set **Count** (`pp.quantity`) — more seeds give denser streamlines.
4. Set **Tube R** (`pp.tube_diameter`) for the rendered tube radius.
5. Choose the **Color Field** (`pp.sl_color_field`): `speed` (velocity magnitude, default), `T`,
   or any available scalar. Choose a **Colormap** (`pp.sl_colormap`).
6. Click **Add Streamline** (`pp.add_streamline`), which posts to `/api/pp/streamlines`. The server integrates polylines through the `U` field and
   returns interleaved `[x, y, z, scalar]` binary data.
7. Toggle **Show Streamlines** (`pp.show_streamlines`) or **Show Seed Sphere**
   (`pp.show_seed_sphere`).

### 1.5 Volume Rendering

Get a 3D view of a field throughout the entire room volume (section `pp.volume_settings`):

1. Check **Configure Volume** (`pp.config_volume`) to expand the controls.
2. Choose the **Field** (`pp.vol_field`) — `T` is most useful for spotting thermal gradients.
3. Set **Resolution** (`pp.vol_resolution`) — the internal mesh is resampled to an N×N×N regular
   grid (default 128). Higher values improve detail but use more memory.
4. Set **Opacity** (`pp.vol_opacity`) and choose a **Colormap** (`pp.vol_colormap`).
5. Click **Apply** (`pp.apply_volume`), which posts to `/api/pp/volume`. The 3D viewport shows a
   translucent volumetric display.
6. Toggle **Show Volume** (`pp.show_volume`).

### 1.6 AI Adaptive Region Inference

The **AI Adaptive Region Inference** section (`title.ai_adaptive_inference`) allows a
`fixedEmbedding` surrogate model to infer fields over a user-defined bounding box, supplementing
or replacing the full OpenFOAM results.

1. Enable post-processing and load results first (see section 1.1).
2. Set the **Model Weights Path** (input `pp-ai-model-path`). Use **Demo Weights** to fill in a
   demo path, or **Current Result Dir** to use the current run directory.
3. Set the **Case Path** (input `pp-ai-case-path`, label `label.case_path`) — must match the
   run directory loaded by `/api/pp/init`.
4. Click **Start Test** (`btn.start_test`), which posts `{modelPath, casePath}` to
   `/api/pp/ai/start`. The server loads the model and warms it up.
5. Once the worker is ready, a box control appears. Adjust the **X/Y/Z Min/Max** inputs for the
   inference region, or click **Reset Box** (`btn.reset_box`) to use the full case bounds.
6. Toggle **Show Inference Box** (`label.show_inference_box`) to preview the region.
7. Click **Confirm Inference** (`btn.confirm_inference`), which posts
   `{box: {min: [...], max: [...]}}` to `/api/pp/ai/infer`. The result is injected as fields `AI_T`, `AI_U`, `AI_p`,
   `AI_region_mask` and displayed automatically as a volume render of `AI_T`.

---

## 2. REST API for Post-Processing

All post-processing operations are available as REST endpoints served by `server.py`, on the port
the launcher banner prints (8000 by default). This allows you to automate result extraction without
a browser. The examples below use `PORT=8000` and a cookie jar in `_tmp/` (a Git-ignored
directory inside the checkout).

All endpoints require a valid session cookie. Log in with the credentials the server was started
with; in the launcher's local mode they are the two lines of `src/server/auth-local/admin_credentials`:

```bash
PORT=8000
CRED=src/server/auth-local/admin_credentials
curl -s -c _tmp/dcdtuni.jar -X POST http://localhost:$PORT/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$(sed -n 1p $CRED)\",\"password\":\"$(sed -n 2p $CRED)\"}"
```

Then pass `-b _tmp/dcdtuni.jar` to every subsequent request.

**Result revision.** `POST /api/pp/init` returns a `revision` string that identifies the loaded
result. Every data request afterwards (`/api/pp/mesh`, `/api/pp/mesh/binary`, `/api/pp/field/*`,
`/api/pp/slice`, `/api/pp/streamlines`, `/api/pp/volume`) must send it back in the
`X-PP-Revision` header: a missing header returns HTTP 428 and a stale one (another page or script
loaded a different result since) returns 409. This keeps two browser tabs, or a tab and a script,
from silently mixing results.

```bash
REV=$(curl -s -b _tmp/dcdtuni.jar -X POST http://localhost:$PORT/api/pp/init \
  -H "Content-Type: application/json" -d '{"runDir": ""}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['revision'])")
```

### POST /api/pp/init

Initialize the post-processor for a specific run directory. Must be called before any other
`/api/pp/*` data endpoint. The server reads the native OpenFOAM result of the run (the processor
partitions when the run kept them, otherwise the reconstructed case) for the latest complete time,
or for `timeValue` when given, and loads all boundary patches and the internal mesh into memory.
An explicitly imported `.vtm` file is still accepted as a source.

**Request body** (JSON):

| Field     | Type   | Default | Description                                                                                                                                                    |
|-----------|--------|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `runDir`  | string | `""`    | Path to the run directory (e.g. `log/2026-03-22-05-01-31_V5_CFD_Station`). Leave empty to auto-detect the latest run.        |
| `timeValue` | number | latest | Result time to load; must exist on every mesh partition.                                                                                                     |
| `timeout` | float  | 3600    | Maximum seconds to wait for loading.                                                                                                                       |

**Response** (JSON):

```json
{ "success": true, "message": "openfoam loaded, 48 patches, 5 fields, 123456 cells",
  "sourceFormat": "openfoam", "decomposed": true, "timeValue": 1000,
  "cached": false, "timing": { "total": 2.1 }, "n_cells": 123456,
  "runId": "…", "revision": "…", "caseDir": "/abs/path/to/case" }
```

A re-init of the same run returns the cached form instead: `{ "success": true, "message": "Already loaded: 48 patches", "cached": true }`. A `nan_warnings` array is added only when some field had NaNs sanitised.

**Example:**

```bash
curl -s -b _tmp/dcdtuni.jar -X POST http://localhost:$PORT/api/pp/init \
  -H "Content-Type: application/json" \
  -d '{"runDir": "log/2026-03-22-05-01-31_V5_CFD_Station"}'
```

### GET /api/pp/runs

List available run directories under `log/`.

```bash
curl -s -b _tmp/dcdtuni.jar http://localhost:$PORT/api/pp/runs
# {"runs": [{"name":"2026-03-22-05-01-31_V5_CFD_Station","path":"log/2026-03-22-05-01-31_V5_CFD_Station"}, ...]}
```

### GET /api/pp/mesh

Return patch metadata and available fields (JSON). Call after `/api/pp/init`.

```bash
curl -s -b _tmp/dcdtuni.jar -H "X-PP-Revision: $REV" http://localhost:$PORT/api/pp/mesh
```

**Response:**

```json
{
  "patches": [
    { "name": "rack_1_server_inlet_patch", "nPoints": 144, "nTriangles": 128 },
    ...
  ],
  "fields": [
    { "name": "T", "type": "scalar", "label": "T" },
    { "name": "U", "type": "vector", "label": "U", "components": ["Ux", "Uy", "Uz"] },
    { "name": "p", "type": "scalar", "label": "p" },
    { "name": "p_rgh", "type": "scalar", "label": "p_rgh" }
  ],
  "totalPoints": 12400,
  "totalTriangles": 11800
}
```

The `fields` list is discovered dynamically from the result; it varies by solver configuration.

### GET /api/pp/mesh/binary

Return geometry as a binary `ArrayBuffer` (Three.js-compatible interleaved format). Use
`/api/pp/mesh` first to obtain patch metadata (point/triangle counts) needed to parse this
binary.

```bash
curl -s -b _tmp/dcdtuni.jar -H "X-PP-Revision: $REV" http://localhost:$PORT/api/pp/mesh/binary -o mesh.bin
```

### GET /api/pp/field/{name}

Return field values as a binary `ArrayBuffer`: a 16-byte header (`float32` min, `float32` max,
`uint32` components per point, `uint32` point count) followed by little-endian `float32` values, one
value (or one vector) per surface point in the order returned by `/api/pp/mesh`. Skipping the header
shifts every value by four points; see [the field payload format](../reference/pp-field-binary-format.md).

The same metadata is returned in the `X-Field-Info` response header as JSON:

```bash
curl -s -b _tmp/dcdtuni.jar -H "X-PP-Revision: $REV" http://localhost:$PORT/api/pp/field/T \
  -o T_field.bin -D -
# X-Field-Info: {"min":290.1,"max":336.7,"nComponents":1,"nPoints":12400}
```

For vector fields (`U`), individual components are accessible as `Ux`, `Uy`, `Uz`;
magnitude is `speed`.

### POST /api/pp/slice

Slice the internal volume mesh with a plane and return triangulated geometry + field values as
binary.

**Request body** (JSON):

| Field    | Type         | Default       | Description                                    |
|----------|--------------|---------------|------------------------------------------------|
| `normal` | `[x, y, z]` | `[0,0,1]`     | Plane normal vector (need not be unit length).  |
| `origin` | `[x, y, z]` | mesh center   | A point on the plane (metres).                  |
| `field`  | string       | `"T"`         | Field to sample on the slice.                  |

**Example:**

```bash
curl -s -b _tmp/dcdtuni.jar -H "X-PP-Revision: $REV" -X POST http://localhost:$PORT/api/pp/slice \
  -H "Content-Type: application/json" \
  -d '{"normal":[0,0,1],"origin":[5.0,3.0,1.5],"field":"T"}' \
  -o slice.bin
```

The binary response has the format:
`[uint32 headerLen][JSON header][float32 positions][uint32 indices][float32 scalars]`

### POST /api/pp/streamlines

Compute streamlines seeded from a sphere and return polyline geometry + scalar coloring as binary.

**Request body** (JSON):

| Field    | Type         | Default     | Description                                                  |
|----------|--------------|-------------|--------------------------------------------------------------|
| `center` | `[x, y, z]` | mesh center | Center of the seed sphere.                                    |
| `radius` | float        | 1.0         | Radius of the seed sphere (metres).                          |
| `nSeeds` | int          | 27          | Number of seed points distributed inside the sphere.         |
| `field`  | string       | `"speed"`   | Scalar field for coloring (`speed` = \|U\|, `T`, `Ux`, …).   |

**Example:**

```bash
curl -s -b _tmp/dcdtuni.jar -H "X-PP-Revision: $REV" -X POST http://localhost:$PORT/api/pp/streamlines \
  -H "Content-Type: application/json" \
  -d '{"center":[5.0,3.0,1.5],"radius":2.0,"nSeeds":50,"field":"T"}' \
  -o streamlines.bin
```

The response binary starts with a 4-byte little-endian uint32 giving the length of a JSON
header (padded to 4-byte alignment), followed by the JSON header, then interleaved
`[x, y, z, scalar]` float32 values for every point in every streamline.

### POST /api/pp/volume

Resample the internal mesh to a regular 3D grid for volume rendering.

**Request body** (JSON):

| Field        | Type   | Default | Description                                      |
|--------------|--------|---------|--------------------------------------------------|
| `field`      | string | `"T"`   | Field to resample.                               |
| `resolution` | int    | 128     | Grid resolution along each axis (N×N×N).         |

**Example:**

```bash
curl -s -b _tmp/dcdtuni.jar -H "X-PP-Revision: $REV" -X POST http://localhost:$PORT/api/pp/volume \
  -H "Content-Type: application/json" \
  -d '{"field":"T","resolution":64}' \
  -o volume.bin
```

---

## 3. ParaView Workflow

Each solve runs in its own timestamped directory, which is a complete OpenFOAM case:

```
case/{CaseName}/log/{timestamp}_{CaseName}/
├── {timestamp}_{CaseName}.log  # solver log
├── case.foam                   # empty dummy file for OpenFOAM readers
├── constant/polyMesh/          # reconstructed mesh (when present)
├── processor0 … processorN-1/  # partitioned mesh and results (dcCart runs keep them)
├── {time}/                     # reconstructed field data (when the run reconstructs)
├── convergence.json            # convergence verdict (return-air rule; residuals as diagnostics)
├── bc-audit.json               # boundary-condition audit written before the solve
├── air-run-execution-receipt.json  # what was executed (manifest, solver command, inputs)
├── model/                      # the .cfdrun this solve executed
├── postProcessing/probes/      # probe time series written by the solver
└── sensorResults/              # harvested probe values
```

To open it in ParaView:

1. Launch ParaView on your workstation (remote or local).
2. **File → Open** — navigate to `case/{CaseName}/log/{timestamp}_{CaseName}/`.
3. Select the `case.foam` file in that directory.
4. In the Properties panel set **Case Type** to **Decomposed Case** when the run kept its
   `processor*` directories (the default for dcCart runs), otherwise **Reconstructed Case**.
5. Click **Apply**, then use the field selector (top toolbar) to switch between `T`, `U`, `p`, etc.
6. Use **Filters → Slice** or **Filters → Stream Tracer** for interactive analysis.

The server no longer converts results with `foamToVTK`; if you need VTK files, run `foamToVTK`
yourself in the run directory with the repository environment.

---

## 4. Sensor / Probe Data

Virtual sensor probes are defined in the `.cfdproj` file as `probeData` — each probe is a 3D
point in the room that the solver monitors at every write interval. These are distinct from
`sensorData`, which holds real-world measurement records.

### Real-Time Probe Query

```
GET /api/probes-latest?since=<float>
```

Returns incremental probe values across five fields (`T`, `U`, `p`, `p_rgh`, `rho`), read from
`postProcessing/probes/` files written by the solver at runtime. Only rows with a time value
greater than `since` are returned, enabling efficient incremental updates for live charts.

| Query param | Type  | Description                                           |
|-------------|-------|-------------------------------------------------------|
| `since`     | float | Return only rows with time > N (use -1 for all rows). |

**Example:**

```bash
curl -s -b _tmp/dcdtuni.jar \
  "http://localhost:$PORT/api/probes-latest?since=500"
```

**Response structure:**

```json
{
  "steps": [500, 600, 700],
  "writeInterval": 100,
  "fields": {
    "T":    [[293.1, 298.4], [293.3, 298.6], ...],
    "U":    [[[0.1,0.0,0.3], ...], ...],
    "p":    [...],
    "p_rgh": [...],
    "rho":  [...]
  }
}
```

Note: `rho` is typically absent for the Boussinesq solver (`buoyantBoussinesqSimpleFoam`); its
array will be `[]` in that case.

### Final Sensor Extraction

```
POST /api/harvest-sensor-results
```

Reads the last row from every probe file and writes a consolidated JSON to
`{run_dir}/sensorResults/{caseName}_sensor_{HH-MM-SS}.json`. Call this after the solve
completes.

**Request body** (JSON, all optional):

| Field              | Type   | Default    | Description                                                        |
|--------------------|--------|------------|--------------------------------------------------------------------|
| `caseName`         | string | `"case"`   | Used as prefix in the output filename.                             |
| `sensorNames`      | array  | required   | Names for each probe column, in order. The count must match the number of probes exactly, otherwise the request returns HTTP 500 (`sensor count mismatch`). |
| `unitTemperature`  | string | `"C"`      | Temperature unit in the output: `"C"`, `"K"`, or `"F"`.            |

**Response** (JSON):

```json
{ "path": "/abs/path/to/case/log/2026-03-22.../sensorResults/mycase_sensor_12-34-56.json" }
```

The written file contains `version`, `caseName`, `solveRunDir`, `finalStep`, `writeInterval`,
`unitLength`, `unitTemperature`, `generatedAt`, and a `sensors` array. Each sensor entry has
`name`, `location`, `T_final`, `U_final`, `p_final`, `p_rgh_final`, `rho_final`.

```bash
curl -s -b _tmp/dcdtuni.jar -X POST http://localhost:$PORT/api/harvest-sensor-results \
  -H "Content-Type: application/json" \
  -d '{"caseName":"V5","sensorNames":["probe_rack1_inlet","probe_rack2_inlet"],"unitTemperature":"C"}'
```

---

## 5. AI Inference

If a `fixedEmbedding` surrogate model is available, DCTwin can run fast AI inference over a
user-defined bounding box in the post-processing scene.

### Load model

```
POST /api/pp/ai/start
```

Requires post-processing to be initialized first (`/api/pp/init`). The `casePath` must match
the run directory currently loaded.

**Request body:**

```json
{
  "modelPath": "/path/to/surrogate_model.pt",
  "casePath": "/absolute/path/to/case/log/2026-03-22-05-01-31_V5_CFD_Station"
}
```

```bash
curl -s -b _tmp/dcdtuni.jar -X POST http://localhost:$PORT/api/pp/ai/start \
  -H "Content-Type: application/json" \
  -d '{"modelPath":"/models/dc_surrogate.pt","casePath":"/home/user/case/V5/log/2026-03-22-05-01-31_V5_CFD_Station"}'
```

### Check status

```
GET /api/pp/ai/status
```

```bash
curl -s -b _tmp/dcdtuni.jar http://localhost:$PORT/api/pp/ai/status
# {"ready": true, "model": "fixedEmbedding", "device": "cuda:0"}
```

### Run inference

```
POST /api/pp/ai/infer
```

Runs the loaded model on the cells inside the given bounding box and injects the result fields
(`AI_T`, `AI_U`, `AI_p`, `AI_region_mask`) into the post-processor cache. The results are
immediately accessible via `/api/pp/field/AI_T`, etc.

**Request body:**

```json
{
  "box": {
    "min": [x_min, y_min, z_min],
    "max": [x_max, y_max, z_max]
  }
}
```

```bash
curl -s -b _tmp/dcdtuni.jar -X POST http://localhost:$PORT/api/pp/ai/infer \
  -H "Content-Type: application/json" \
  -d '{"box":{"min":[0,0,0],"max":[10,5,3]}}'
```

---

## 6. Liquid Cooling Results

The production liquid-cooling solver is the differentiable Newton DAE (steady state, backward-Euler
transients, implicit-adjoint gradients). It runs in server-owned **runtime sessions**; configuration
and results live under `case/{CaseName}/Liquid/`.

### In the editor

1. Switch the top bar to **💧 Liquid Cooling** (loading a project does not switch it) and open the
   **🔬 Liquid solve** tab. It has five sections: **① Model readiness** (a live checklist — CDUs,
   liquid racks, cold-plate pairs, a compilable topology, every cold plate on a pump-driven loop,
   zero elevation sum round every loop; solve only when it reads all-pass), **② Operating
   conditions and boundaries**, **③ Solver settings**, **④ Run** and **⑤ Export and audit**.
2. In **④ Run**, **Solve steady state** (steady mode) or **Initialise and run** (transient; the
   top-bar steady/transient switch for liquid decides which) exports the network and starts the
   run; **Initialise only** stops at t = 0 so inputs can be queued first. **Single step**,
   **Pause** / **Resume** control a transient; **Independent steady-state query** solves the
   steady state for the pending inputs without touching the running trajectory.
3. The **Coupled simulation monitor** on the right follows the run: simulation time and frames,
   every cold plate (solid and outlet temperature, flow in L/min and kg/s) and live curves.
4. Open the **📊 Liquid results** tab for the saved result. **Solve Records** lists the runs saved
   in this case (a run saved before a server restart is restored from disk). Four views:
   - **Overview** — convergence, total flow, hottest chip, per-rack supply/return and per-server
     flow, chip and outlet temperature and the heat removed;
   - **Network** — key figures (total flow, hottest chip, highest velocity, lowest-flow branch)
     and a sortable, searchable table of every part: actual flow direction (flagged when it runs
     against the modelled direction, or stagnant behind a closed valve or dead end), flow in L/min
     and kg/s, velocity, pressure drop and loss, inlet/outlet temperature;
   - **Heat & HX** — heat input, primary rejection, storage rate, boundary exchange and the
     energy-balance residual; each heat exchanger's model, temperatures and flows; the operating
     inputs; and the solver laws the run was solved with (laws a pre-record run gets back are
     marked as inferred);
   - **Charts** — per-object time series (temperature, flow, pressure) of a transient.
5. **3D display** in the same tab colours the network by temperature, flow, velocity or pressure
   (**Colour by**, with a colour bar; **Colour scale** over the whole run or this frame, or set by
   double-clicking the bar; pipes are coloured along their length) and colours
   the servers by chip temperature. **Flow arrows** run along the rendered pipework in the actual
   flow direction, sized by flow and coloured by velocity (**Arrow velocity** bar), optionally
   animated; arrows inside a closed cabinet are counted but not drawn. Picking a part in 3D or in
   the network table shows all its values. With the Omniverse renderer Kit colours the same
   result and draws the same arrows.
6. A hybrid air–liquid project shows its **Heat ledger**: how the heat of the liquid run splits
   between the liquid loop and the air side that the next air solve carries.

The workbench details (online heat/pump/valve commands, exporting the executed inputs, numerical
limits) are in the [Liquid DAE workbench](../developer/liquid-dae-workbench.md) page.

### Runtime session API

```text
POST /api/liquid/runs                    create a run from a .liquidrun (returns its id)
POST /api/liquid/runs/{id}/start         start (or /step, /pause, /resume, /cancel)
GET  /api/liquid/runs/{id}/frames?after=N   new frames since sequence N
GET  /api/liquid/runs/{id}/result        result so far
```

The full lifecycle, the online `inputs` and `equilibrium` requests and the artifacts are documented
in [Liquid runtime API](../developer/liquid-runtime-api.md). Each accepted run writes
`case/{CaseName}/Liquid/log/run-<id>/` with `input.liquidrun` (or `input.json` for a v2 input),
`checkpoint.json` and `result.json`; DAE runs also write `executed.liquidrun` and
`executed-inputs.json`, which replay the accepted steps without the browser. Without the editor,
`python -m agent.scripted_api liquid <project>.cfdproj` solves the steady state and its gradients through
the same solver (see [Scripted API](../reference/scripted-api.md)).

### Retrieve results

```
GET /api/liquid/results[?run=<dir-name>]
```

Returns the newest liquid result of the current case: a runtime session's `run-<id>/result.json`
or an older `liquid_result.json`. `?run=` selects a specific run directory.

```bash
curl -s -b _tmp/dcdtuni.jar http://localhost:$PORT/api/liquid/results
```

Runtime results carry `time_s`, `node_pressure_Pa`, `edge_flow_kg_s`, `node_temperature_K`,
`cell_temp_K`, `cp_details` and `rack_summaries`, the network topology (`edge_ids`, `edge_from`,
`edge_to`, `edge_types`, `edge_D_m`, `edge_stagnant`), and the run identity, status, diagnostics
and `parameter_provenance.solver_laws`.

### Older endpoints

- `POST /api/liquid/solve` runs the case's exported `Liquid/model/*.liquidrun` in one request and
  streams Server-Sent Events (`stage` fields such as `compiling`, progress, `done`, `error`). A DAE
  (v3/v4) input goes to the same Newton DAE solver; v2 runs the runtime/PID path; an unversioned
  v1 input still runs the original direct solver. The editor no longer uses it.
- `POST /api/liquid/transient` is retired: it answers HTTP 410 Gone and points at the runtime
  sessions above, which run liquid transients.
- Air–liquid coupling is a one-way heat handoff (liquid → air). For a hybrid project — one that
  declares its hybrid IT heat budget in the liquid experiment panel and has been saved — complete a
  liquid experiment first; the next air solve then sends that completed run with `/api/run-mesh`
  (`hybridHeat`). The server audits the liquid result, writes the heat the liquid loop does not
  remove onto the air-side server faces of a derived `.cfdrun`, and keeps a heat ledger in the air
  run directory (`GET /api/hybrid/heat-ledger`). `POST /api/run-coupled-sim` is retired and
  answers HTTP 410 Gone.
