# Runner Internals

This page covers `STLGenRunner`, the class that takes a browser-exported `.cfdrun` manifest through meshing, case setup and solving. It describes the code as of 2026-10-21; functions are named rather than given by line number, because `src/backend/aircooling/runner/stlgen_runner.py` changes often.

`STLGenRunner` is the only runner. The older `CaseRunner` (`config.toml` + `room.json`) has been removed, and no intermediate `room.json` or `config.toml` is written. Never restore a `room.json → GeometryManager → regenerate STL` round trip: the editor's exported STL files are the geometry source of truth.

The runner is driven through the server (`POST /api/run-mesh`) or the scripted API, which also goes through a server. Do not call it directly for end-to-end mesh/solve work; unit tests (`src/backend/tests/`) exercise its internals.

## Constructor and manifest discovery

```python
runner = STLGenRunner(case_dir, cfdrun_path=None)
```

- `case_dir` — the case directory, e.g. `case/Base_leakage/`
- `mesh_dir` — `case_dir / "Mesh"`, the working OpenFOAM case (reset on every mesh)
- `model_dir` — `case_dir / "model"`, which holds the `.cfdrun`
- `tri_surface_dir` — `mesh_dir / "constant" / "triSurface"`, the exported STL files

With `cfdrun_path=None`, exactly one `*.cfdrun` in `model/` is used; none raises `FileNotFoundError` and two or more raise `RuntimeError` asking for an explicit path.

## Loading

`_load()` (full) and `_load_cfd_json_only()` (manifest only, used by `validate_geometry()` and the mesh-only preflights) both:

1. read the manifest and validate it with `CfdrunV2` (strict; schemaVersion 2, 3 or 4);
2. validate the geometry blob with `CFDExport.model_validate(execution_blob(raw))`. `execution_blob()` (`derived_config.py`) returns `meshGeometry` as is for v2/v3; for v4 it derives the executed `simulationConfig` from the root one and puts it into the blob;
3. store the result as `self._data` (`model_dump(mode="json")`) — the mesh pipeline reads geometry and `meshConfig` from here;
4. check that the execution fields present in both the root and the blob (`parallelMesh`, `meshProcess`, `maxGlobalCells`, `parallelSolve`, `solveProcess`, `endTime`, `writeInterval`, `startFrom`) agree, and refuse the run if they do not.

`_load()` also scans `Mesh/constant/triSurface/*.stl` for `solid <name>` headers. `_prepare_runtime_metadata()` then calls `cfdrun_reader.resolve_run()` to turn the manifest's `boundaryBindings`, `porousBindings` and `sourceTerms` into the in-memory `BridgeBCRegistry` (see [Boundary Conditions](bridge-bc.md)).

With `DCDT_CFDRUN_V3_SHADOW=1` the runner also logs whether the backend-derived configuration matches the blob (`derived_config.shadow_drift`).

## Public API

```python
runner.run_mesh_only()    # preflights → mesh dictionaries → engine pipeline → case files → quality receipt
runner.run_solve_only()   # re-verify the mesh → regenerate case files → solve in log/<timestamp>_<name>/
runner.run_simulation()   # both
```

`/api/run-mesh` calls them with `meshOnly` / `solveOnly`; a job holds the case lock (`_case_locked`) so two jobs cannot run on one case at once.

## Mesh stage (`run_mesh_only`)

In order:

1. **Preflights** — `_preflight_case_path` (OpenFOAM refuses paths with spaces), `_verify_hybrid_source_stls` (hybrid runs: exported STL hashes), `_load`, `_preflight_floor_openings`, `_check_dcCart_memory` (dcCart: refuses a mesh that would not fit in memory), `validate_geometry` (no floor above the room height), `_preflight_partition_wall_thickness`.
2. **`_ensure_watertight_room_enclosure()`** — builds the closed room shell, including polygon rooms.
3. **`_generate_mesh_dicts()`** — writes `blockMeshDict`, `surfaceFeatureExtractDict`, `snappyHexMeshDict` (geometry, refinement surfaces and regions, `locationsInMesh`) and `createBafflesDict` from Python. dcCartMesh reads the same `snappyHexMeshDict`, so patch names match the bindings for every engine.
4. **Engine dispatch** on `_resolve_mesh_engine(meshConfig)` — `dcCart` (default) or `snappy` (an explicit `meshgen`, retired on 2026-10-26, is refused here with a clear error); see [Mesh engines](../reference/mesh-engines.md):
   - **`_run_dcCart_mesh_pipeline(n)`** — writes `system/dcCartMeshDict`; runs `dcCartMesh -overwrite` on one rank or `mpirun -np N dcCartMesh -overwrite -parallel` (no serial fallback). If the mesher did not split the baffles itself (`constant/dcCartPartitionInfo.json`), it runs `redistributePar` with `preserveFaceZones` when a baffle zone straddles ranks, then `mpirun createBaffles -overwrite -parallel` (or serial `createBaffles` on one rank), and converts wall-type baffle patches from `cyclic` to `wall`. The partitions are kept.
   - **`_run_parallel_mesh_pipeline(n)`** (snappy, `parallelMesh` and `meshProcess > 1`) — `blockMesh`, `surfaceFeatureExtract`, `decomposePar -force -no-fields` (hierarchical), `mpirun snappyHexMesh -overwrite -parallel`, then `redistributePar` (`preserveFaceZones`) + `mpirun createBaffles -overwrite -parallel` (falls back to serial `createBaffles` after reconstruction if that fails), `reconstructParMesh -constant`, wall-baffle conversion. `createBaffles -parallel` alone would drop face-zone faces that straddle a processor boundary; the `preserveFaceZones` redistribution prevents that.
   - **`_run_serial_mesh_pipeline()`** (snappy, one process) — the same steps without MPI.
5. **Final check and clean-up** — `_remove_disconnected_cells()` runs `checkMesh -allGeometry -allTopology`: isolated cells (zero or one internal face) are always removed; disconnected regions smaller than 1 % of the largest are removed as artefacts; larger ones are kept and reported (they can be legitimate containment). A region bounded only by walls carries no flow: dcCart keeps it and records it in `sealed-regions.json`, snappy removes it.
6. **`_setup_complete_case_in_mesh()`** — writes `0/` fields, `controlDict`, `fvSolution`, `fvSchemes` and, for parallel solves, `decomposeParDict`. Mesh-only mode tolerates a missing fan-outlet pairing with a placeholder and a warning; the solve path is strict.
7. **Receipts** — `_record_air_mesh_quality()` runs the strict `checkMesh` and writes `case/<name>/mesh-quality-receipt.json`, bound to the manifest and the mesh files. Hybrid and source-surface runs also record their mesh sources.

**Time budget.** `meshConfig.meshTimeLimitSeconds` (default 300 s) is a soft target since 2026-10-20 (`mesh_budget.py`): a mesh that runs longer is logged once as a warning and continues; nothing is killed because of it. (The batch tool `tools/regen_all_meshes.py` still applies its own `--timeout` per case; see its README.)

## Solve stage (`run_solve_only` → `_run_solve_from_mesh`)

1. Reload the manifest, regenerate the case files in `Mesh/` (the user may have changed settings) and **bind the solve to a mesh-quality record** of exactly this mesh and input (`_require_air_mesh_quality`: a missing or stale record is checked again, a rejected verdict is logged as a warning -- quality is reported, never required; a hybrid / source-surface mesh whose input changed after its source gate needs a re-mesh); hybrid runs apply the liquid heat handoff first (`_apply_hybrid_air_gate`).
2. Copy `Mesh/` into `case/<name>/log/<timestamp>_<name>/` (timestamp `%Y-%m-%d-%H-%M-%S`) and solve there; the log file is `<timestamp>_<name>.log`.
3. Select the solver (`_solver_for_simulation_config`): `buoyantBoussinesqSimpleFoam_new` for steady, `buoyantBoussinesqPimpleFoam_new` for transient, unless `solverName` or `DCDTUNI_SOLVER_OVERRIDE` says otherwise. `_resolve_solver_binary` prefers the repository build in `third_parties/foam/platforms/*/bin/`; a missing `PimpleFoam_new` is an error, a missing `SimpleFoam_new` falls back to `PATH` with a warning.
4. Transient runs that start from a converged steady state run that steady start first (`_run_steady_start`).
5. Partitions: `_decompose_for_solve` reuses the mesh partitions when the rank count matches, otherwise reconstructs and decomposes again.
6. **Boundary-condition audit** — `_audit_boundary_conditions()` compares the written `0/` fields, patch by patch, with the manifest's bindings and the registry, writes `bc-audit.json` and logs each issue. It never changes a field or stops a solve.
7. `mpirun -np N <solver> -parallel` inside `_watch_steady_stop()`: for steady runs with return-air monitors, a watcher re-runs the convergence assessment while the solver runs and writes `stopAt writeNow;` into `controlDict` once the verdict passes over a long enough window. Runs that keep partitions (dcCart) are not reconstructed; the others run `reconstructPar -latestTime` and `_rewrite_explicit_boundary_values()`.
8. **Results** — `_auto_harvest_sensors()` writes `sensorResults/<name>_sensor_<HH-MM-SS>.json`; `convergence.assess_run()` writes `convergence.json`; `air-run-execution-receipt.json` records the executed manifest, solver command and inputs.

### Convergence verdict

`convergence.py` judges a steady run on the **ACU return-air temperatures** (owner rule, 2026-10-20): every return-air probe must have changed during the run and stay flat within 0.5 K (or 1 % of its excursion, whichever is larger) over the final window. Residuals (`Ux/Uy/Uz`, `p_rgh`, `T`, `k`, `epsilon`), bounding events and continuity errors are still computed and written as diagnostics. Without return-air probes the verdict is `INCONCLUSIVE`. Transient runs get their own per-time-step outer-convergence verdict.

## OpenFOAM dictionary generation

Mesh dictionaries and `0/` fields are generated directly in Python. Solver dictionaries use Jinja2 templates from `src/backend/aircooling/third_parties/foam/template/`:

| Template file | Output | Purpose |
|---|---|---|
| `system/steady/controlDict.j2` | `system/controlDict` | end time, write interval, function objects (steady) |
| `system/steady/fvSolution_cpu.j2` | `system/fvSolution` | linear solvers, relaxation (steady) |
| `system/steady/fvSchemes.j2` | `system/fvSchemes` | discretisation (steady) |
| `system/transient/controlDict.j2` | `system/controlDict` | time stepping (PIMPLE) |
| `system/transient/fvSolution.j2` | `system/fvSolution` | PIMPLE controls and tolerances |
| `system/transient/fvSchemes.j2` | `system/fvSchemes` | discretisation (transient) |
| `system/share/fvOptions.j2` | `system/fvOptions` | partition-wall porous sources |

`system/decomposeParDict` is written by `generate_decompose_par_dict()` in `openfoam_case.py` (scotch or hierarchical; with `preserve_face_zones=True` it adds the `preserveFaceZones` constraint used before `createBaffles -parallel`).

## Running external commands

`_run_cmd()` runs every OpenFOAM tool. MPI commands write to `log.mpi_solver` (each step overwrites it) and are tailed in the background to avoid pipe deadlocks; other commands stream stdout through loguru into the job log the editor shows. The MPI guard also exports `FOAM_SIGFPE=false` and, when the hwloc GL plugin is present, points `HWLOC_PLUGINS_PATH` at a copy without it (the plugin can hang `mpirun` by probing X11 ports). A non-zero exit raises `RuntimeError`, which the server reports as a failed job; the abort button sends SIGTERM to the registered process group.

## Related tests

`src/backend/tests/` covers the stages above, for example `test_mesh_engine.py`, `test_mesh_mode.py`, `test_mesh_acceptance.py`, `test_mesh_deadline_handoff.py`, `test_bc_audit.py`, `test_convergence_verdict.py`, `test_steady_stop_watcher.py`, `test_transient_outer_convergence.py`, `test_solver_binary_not_built.py`, `test_acu_return_pinned_flow.py`, `test_fan_outlet_area_split.py`.
