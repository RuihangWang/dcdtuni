# Quick Start

This walkthrough takes you from a running server to a completed CFD simulation with post-processed results. It covers the entire browser-based workflow end to end.

**Estimated total time:** 15–30 minutes for a small case (2 racks, 1 ACU) on a 24-core machine.

---

## Step 1: Start the Server

From the repository root (the launcher finds the `dcdtuni` environment itself — the repository
one under `.codex-tmp/envs/dcdtuni`, an active `dcdtuni`, or Conda's):

```bash
./src/start_server.sh
```

The default is the React editor and its `/api` on ONE port, 8000 (WebSocket 8001). With no
`DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD` in the environment the launcher runs in
**local mode**: it creates once, and then reuses, the administrator file
`src/server/auth-local/admin_credentials`. The output ends with a banner like:

```text
管理员凭据：/path/to/dcdtuni/src/server/auth-local/admin_credentials
...
=========================================
  模式        : react (生产 / dist)
  浏览器打开  : http://localhost:8000/
  后端        : http://localhost:8000  (WS 8001)
  启动耗时    : ... ms
=========================================
```

The server is now ready. Leave this terminal open — it streams live logs from mesh and solve
operations; `Ctrl+C` stops it. Another port: `./src/start_server.sh --port 9000` (WebSocket
`--port + 1`). The launcher never moves to a different port on its own: it refuses a port another
user holds (pick another `--port`) and frees a port your own processes hold, so do not rerun it
on a port where your server is still working. The first start builds the editor
(`src/frontend/react/dist`) if it is missing, and rebuilds it whenever a UI source file is newer
than it, so a `git pull` needs no extra flag.

---

## Step 2: Open the Browser and Log In

Open your browser at the URL the banner prints:

```text
http://localhost:8000/
```

<!-- screenshot: login page -->

A login overlay appears. In local mode the credentials are the two lines of the file the banner names:

| Field    | Value |
| -------- | ----- |
| Username | line 1 of `src/server/auth-local/admin_credentials` (`admin`) |
| Password | line 2 of the same file |

```bash
sed -n 2p src/server/auth-local/admin_credentials     # the password
```

A server started with `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD` (for example a shared or LAN
deployment, see the README) takes that pair instead.

After logging in you will see the three-panel layout: the model tree on the left, the 3D viewport in the center, and the property inspector on the right. The Operation Log panel is docked at the bottom.

---

## Step 3: Load a Demo Project

The repository ships with several ready-to-run demo cases under `case/`. For a first run, use one of the smaller cases:

| Case directory | `.cfdproj` file | Description |
| -------------- | --------------- | ----------- |
| `case/SingleRack/` | `SingleRack.cfdproj` | Single rack, minimal geometry |
| `case/DAML-Testbed/` | `DMAL_Testbed-patched.cfdproj` | Lab testbed (2 racks, 4 ACUs) |
| `case/Base_leakage/` | `Base_leakage.cfdproj` | Leakage scenario (4 racks, 2 ACUs) |
| `case/Base_coldAisleIso/` | `Base_coldAisleIso.cfdproj` | Cold-aisle containment (2 racks, 1 ACU) |

Click the **Load Project** button in the top toolbar. A dialog appears. In the **Server Files** tab, browse to the case directory and select the `.cfdproj` file; or use the **Local Files** tab to upload from disk.

<!-- screenshot: empty viewport -->

After loading, the 3D viewport shows the room geometry. The case path is displayed in the property inspector on the right under **DCTwin Integration → Case Path**. If the path is not set or shows red, click **Set Case Directory** and select the matching `case/<Name>/` folder.

!!! tip
    You can also start from scratch by loading any `.cfdproj` and editing its geometry, then saving under a new name with **Save Project**.

---

## Step 4: Define the Room

Room geometry is edited in the dedicated 2D floor-plan editor, not in the model tree. Click the **🏠 Room 2D Floor Plan** button at the top of the left panel to open it.

In the 2D editor, draw the room footprint as a polygon (a rectangle or an arbitrary shape) and set the ceiling height. For a simple rectangular test room:

| Property  | Value                |
| --------- | -------------------- |
| Footprint | 10 m × 8 m rectangle |
| Height    | 3 m                  |

Close the editor to return to the 3D scene — the room walls and floor/ceiling are generated automatically from the polygon. Clicking the tree root (**Project Root**) shows project-level metadata such as room temperature and the bounding box, not the room dimensions.

---

## Step 5: Add a Rack

Click the **New Rack** button in the toolbar (available in the Geometry & Properties tab).

<!-- screenshot: rack in viewport -->

A rack appears in the center of the room. Select it and set its dimensions and position in the property inspector:

| Property | Value  |
| -------- | ------ |
| Width    | 0.6 m  |
| Depth    | 1.0 m  |
| Height   | 2.0 m  |
| X        | 3.0 m  |
| Y        | 0.0 m  |
| Z        | 4.0 m  |

### Add Server Equipment to the Rack

With the rack selected, click a U-slot in the rack panel and then click **Add Fan-Server**. Set:

- **Flow rate**: `0.15 m³/s` (cooling airflow through the server)
- **Power**: `3000 W` (heat dissipation)
- **U position**: `1` (slot number from the bottom)

Fan-server equipment creates a through-channel in the rack geometry that drives airflow in the CFD simulation.

---

## Step 6: Add an ACU

Click **New ACU** in the toolbar. Position the ACU on one of the room walls facing the racks.

<!-- screenshot: rack + ACU in viewport -->

In the property inspector, configure the ACU:

| Property           | Value    |
| ------------------ | -------- |
| Supply temperature | 15 °C    |
| Flow rate          | 4 m³/s   |
| Width              | 1.2 m    |
| Height             | 2.0 m    |

The ACU supply face becomes a velocity-inlet boundary condition in OpenFOAM, and the return face becomes a flow-rate outlet.

---

## Step 7: Add a Floor (Optional)

Click **New Floor** to insert a raised floor object. In the property inspector you can add perforated tile openings that supply cold air from the underfloor plenum.

For a standard (non-raised) floor, skip this step — the room bottom wall acts as a solid floor by default.

---

## Step 8: Configure the Mesh

Click the **Mesh** tab on the left panel. (The Mesh, Solver, and Post-processing tabs are enabled only after a case directory has been set — see Step 3.)

Key mesh settings for a small case:

| Setting | Default | Notes |
| ------- | ------- | ----- |
| Mesh engine (`meshEngine`) | `dcCart` | cut-cell Cartesian, no snapping; `snappy` (snappyHexMesh, body-fitted) is the alternative (MeshGenerator, `meshgen`, was retired on 2026-10-26) |
| Base cell size (`meshBaseSize`) | 0.25 m | the recommended cell-size rule a new project starts on |
| Parallel meshing (`parallelMesh`) | `true` | MPI meshing (strongly recommended) |
| MPI ranks (`meshProcess`) | per project | number of ranks for meshing |

!!! warning "Always use parallel mesh and solve"
    Serial meshing and solving are only suitable for tiny test cases. Real data center cases require parallel mode. The server runner handles `mpirun` correctly — do not change `parallelMesh` to `false` unless debugging a specific issue.

---

## Step 9: Run the Mesh

Click the **Run Mesh Generation** button at the bottom of the Mesh tab. The browser exports the
geometry and the `.cfdrun` manifest and the server starts the meshing pipeline. With the default
dcCart engine:

1. STL geometry is exported from the 3D scene to `case/<CaseName>/Mesh/constant/triSurface/`
2. `blockMesh` writes the uniform background mesh
3. `dcCartMesh` refines it per object (octree) and cuts the cells against the geometry — `mpirun -np N dcCartMesh -parallel` with parallel meshing (the cells do not depend on N)
4. `createBaffles` inserts the thin baffle/door patches (partitions, perforated plates)
5. `checkMesh` runs the strict quality gate and writes `case/<CaseName>/mesh-quality-receipt.json`

With the `snappy` engine the middle steps are `surfaceFeatureExtract` and `snappyHexMesh` instead.

<!-- screenshot: mesh progress in Operation Log -->

Watch the **Operation Log** at the bottom of the browser window for real-time output. When meshing completes successfully, the log ends with lines like:

```text
Strict air mesh quality: accepted (0 failed checks), mesh=...
Mesh complete. Complete case in .../case/<CaseName>/Mesh
```

A small case (one or two racks) meshes in well under a minute on a few cores.

---

## Step 10: Run the Solve

After meshing completes, switch to the **Solver** tab and click **Run Solver**. The server launches `buoyantBoussinesqSimpleFoam_new` in parallel (or `buoyantBoussinesqPimpleFoam` if the solver type is set to transient) in a new run directory `case/<CaseName>/log/<timestamp>_<CaseName>/`.

When the solve ends the runner judges convergence and writes `convergence.json` into that run
directory; the log carries one summary line:

```text
convergence: CONVERGED residuals PASS sensors PASS
```

A steady run is **CONVERGED** once every ACU return-air temperature has changed and then
plateaued within the stop tolerance (default 0.5 K, "Return-air stop tolerance" in the solver
tab) over the last 100 iterations; residuals are reported as diagnostics and do not gate the
verdict. A run shorter than 100 iterations is INCONCLUSIVE with a `too-short` reason. The end
time is the cap (a new project saves 1500): a monitored solve stops by itself as soon as the
return air is steady, and otherwise runs to the end time. Without return-air evidence (no ACU return patch, no probe) the verdict is
INCONCLUSIVE, never CONVERGED.

!!! tip "Stopping a running job"
    Click the **Abort Solver** button (replaces the Run Solver button while a job is running) to send a graceful termination signal. During meshing the button is labelled **Abort Mesh Generation**. The current time step is written before the process exits.

---

## Step 11: View Results

When the solve finishes, open the **Post-processing** tab in the left panel.

<!-- screenshot: temperature field in Post-processing tab -->

Click **Enable Post-processing** to initialise the results reader. Then select the temperature field `T` from the surface-field dropdown. The 3D viewport updates with a false-color temperature contour. Use the colormap controls to adjust the range (e.g., 288 K – 310 K for a 15 °C supply case).

Available fields:

| Field   | Description                       |
| ------- | --------------------------------- |
| `T`     | Temperature (K)                   |
| `U`     | Velocity magnitude (m/s)          |
| `p_rgh` | Hydrostatic-reduced pressure (Pa) |

---

## Step 12: ParaView Alternative

Results are written in OpenFOAM format and can be opened directly in ParaView for advanced post-processing:

```bash
paraview case/<CaseName>/log/<timestamp>_<CaseName>/case.foam
```

In ParaView:

1. Click **Apply** to load the mesh.
2. Select a time step from the toolbar.
3. Color by `T` to see the temperature distribution.
4. Use **Stream Tracer** to visualize airflow paths.
5. Use **Slice** to cut cross-sections through the room.

---

## Case Directory Structure

Every DCTwin project creates the following directory layout on the server:

```text
case/{CaseName}/
├── model/
│   └── {CaseName}.cfdrun    # Live simulation manifest written by the server
├── Mesh/
│   ├── constant/
│   │   ├── triSurface/      # Multi-solid ASCII STL geometry files
│   │   │   ├── merged_rack_frame.stl       # rack frames
│   │   │   ├── merged_server_4U.stl         # fan-server channels (per U-span)
│   │   │   ├── merged_acu.stl               # ACU supply/return faces
│   │   │   ├── merged_floor_ceiling.stl     # floor + ceiling
│   │   │   └── room_wall.stl                # room boundary walls
│   │   └── polyMesh/        # Final OpenFOAM mesh (written by the mesh engine)
│   └── system/              # OpenFOAM dictionaries (blockMeshDict, the mesher's dict, decomposeParDict, etc.)
├── mesh-quality-receipt.json  # the strict checkMesh verdict of the current mesh
└── log/                     # Timestamped solver output directories
    ├── YYYY-MM-DD-HH-MM-SS_{CaseName}/   # one solve: fields, case.foam, convergence.json
    └── api/                              # runs of the scripted API (python -m agent.scripted_api)
```

Key files:

- **`model/*.cfdrun`** — The live simulation manifest. The server rewrites the active `<name>.cfdrun` whenever you click Run Mesh or Run Solve, and `STLGenRunner` validates and consumes that file directly.
- **`Mesh/constant/triSurface/*.stl`** — Multi-solid ASCII STL files where each `solid`/`endsolid` block defines one boundary patch. These are regenerated on every mesh run. The exact set of `merged_*.stl` files depends on which geometry types are present — e.g. baffles, blanking panels, through-zones, and rack doors each produce their own `merged_*.stl`.

!!! note "`.cfdrun` vs `.cfdproj` files"
    The `.cfdproj` file in `case/{CaseName}/` is a user-saved snapshot — it is only updated when you explicitly click **Save Project**. The `.cfdrun` file in `case/{CaseName}/model/` is rewritten automatically on every run and always reflects the latest simulation input. When debugging, start from the `model/` files, not from `.cfdproj`.

---

## Next Steps

- [STL Modeling guide](modeling.md) — detailed walkthrough of all model types (Box, Rack, Wall, Baffle, Custom STL)
- [Post-Processing guide](post-processing.md) — advanced visualization and data export
- [Scripted API](../reference/scripted-api.md) — the same pipeline from one command, without a browser (`python -m agent.scripted_api`)
- [REST API Reference](../developer/api-reference.md) — automate runs via the HTTP API
