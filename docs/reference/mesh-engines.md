# Mesh engines (`meshConfig.meshEngine`)

**Status (as of 2026-10-26):** one setting, `meshConfig.meshEngine`, chooses the program that builds the air-cooling mesh. `dcCart` is the default and the production engine; `snappy` remains selectable. A third engine, MeshGenerator (`meshgen`), was retired on 2026-10-26 by owner decision; see [Retired: meshgen](#retired-meshgen). The mesh semantics (snap on or off, background grid) follow from the engine. The three-way `meshMode` dropdown (`bodyFitted` / `cartesianLocalized` / `structured`) that this page described until 2026-09 was retired on 2026-09-09; see [History](#history-the-retired-three-way-meshmode). This page was called `mesh-modes.md` before 2026-10-21.

## The two engines

| `meshEngine` | Program and pipeline | Snap | Parallel meshing | Status |
|---|---|---|---|---|
| `dcCart` (default) | `dcCartMesh`, the repository's own cut-cell Cartesian mesher → `createBaffles` → strict `checkMesh` | off: surfaces are resolved by cutting cells, not by snapping | MPI-native: with `parallelMesh` and `meshProcess > 1` it runs as `mpirun -np N dcCartMesh -parallel`; one rank is the same algorithm without `mpirun`, and the cells do not depend on the rank count | Production. The ten release cases pass strict `checkMesh` on it and converge ([receipt](../benchmarks/air-release-cases-2026-10-21/README.md)). |
| `snappy` | `blockMesh` → `surfaceFeatureExtract` → `snappyHexMesh` → `createBaffles` → strict `checkMesh` | on (body-fitted) | `snappyHexMesh -parallel` after `decomposePar`, then `reconstructParMesh` | Kept as a selectable alternative. It has not been re-validated against the strict gate since `dcCart` became the default; [scripted-api.md](scripted-api.md) records two small cases where it does not pass the final strict check. |

### How the engine is resolved

`_resolve_mesh_engine(meshConfig)` in `src/backend/aircooling/runner/stlgen_runner.py` returns the value of `meshEngine` when it is `snappy` or `dcCart`, and `dcCart` otherwise (absent or unrecognised). The one exception is `meshgen`: the runner and the `.cfdrun` schema refuse it with "the meshgen engine was retired on 2026-10-26; choose dcCart or snappy" before any mesh step, rather than meshing with another engine. The editors (`resolveMeshEngine` in `src/frontend/react/src/run/meshConfig.ts`, and the shared `cfdrun_build.js`) apply the same rule, and new projects carry `meshEngine: "dcCart"` explicitly (`cfdproj_state_v4.js`).

`_resolve_mesh_mode(meshConfig)` still exists for code that needs the semantics: it returns `cartesianLocalized` for `dcCart` and `bodyFitted` for `snappy`. The manifest keys `meshMode`, `cartesianMode` and `structuredGrid` are accepted when reading older files and are **ignored**.

### Where you set it

- **React editor:** Mesh tab → engine selector (labels *dcCartMesh*, *snappyHexMesh*; `src/frontend/react/src/components/MeshPanel.tsx`). A project saved with `meshgen` opens on `dcCart`, with a warning in the operation log. The legacy HTML page (unmaintained since 2026-10-23) still lists MeshGenerator; the runner refuses it.
- **Project file:** `runDefaults.meshConfig.meshEngine` in the `.cfdproj`.
- **Run manifest:** `meshConfig.meshEngine` in the exported `.cfdrun` (the runner reads the copy inside `meshGeometry.meshConfig`; see [.cfdrun reference §14](cfdrun-v2-schema.md)).
- **Scripted API:** `python -m agent.scripted_api air … --mesh-engine <engine>`; see [scripted-api.md](scripted-api.md).

## dcCart in more detail

The dcCart pipeline is `_run_dcCart_mesh_pipeline` in the runner. In order:

1. The runner writes `snappyHexMeshDict` (geometry, refinement surfaces and regions, `locationsInMesh`) and `system/dcCartMeshDict` (the background grid). dcCartMesh reads both, so every patch keeps the name its boundary condition binds to.
2. `dcCartMesh -overwrite` (one rank) or `mpirun -np N dcCartMesh -overwrite -parallel`. Every rank builds the same octree and cuts its own slab of columns plus a halo; sliver merges are decided in rounds shared by all ranks. A failed parallel run is an error, never silently rerun on one rank.
3. Baffles: when the mesher already wrote the baffle master/slave pairs (`constant/dcCartPartitionInfo.json` says so), nothing more is needed. Otherwise, in parallel, `redistributePar` with a `preserveFaceZones` constraint moves every baffle face zone onto one rank (only when needed), then `mpirun createBaffles -overwrite -parallel`; on one rank, `createBaffles -overwrite`. Wall-type baffles are then converted from `cyclic` to `wall`.
4. Final `checkMesh -allGeometry -allTopology` with the clean-up rules (tiny orphan regions removed; wall-only sealed cavities kept and recorded in `sealed-regions.json`).
5. The case is completed in `Mesh/` and the strict quality receipt `case/<name>/mesh-quality-receipt.json` is written. The solver refuses a mesh whose receipt is missing or failed.

The processor partitions are kept for the solver and for post-processing; `_decompose_for_solve` reuses them when the solve uses the same rank count and only otherwise reconstructs and re-decomposes.

The sizing keys that matter for dcCart (all in `meshConfig`, all with runner defaults):

| Key | Product preset | Meaning |
|---|---|---|
| `meshBaseSize` | 0.25 m | background cell size |
| `weldTolerance` | 0.0012 m | near-coincident vertex weld (0 = off) |
| `firstLayerMinFraction` | 0.3 | minimum thickness of the first cell off a surface, as a fraction of that level's spacing |
| `firstLayerMax` | 0.005 m | cap on that first-layer floor |
| `firstLayerSealGap` | −1 (= weld tolerance) | widest solid–solid gap the floor may seal |
| `rackCellMinSize` | 0.0175 m | the rack-region cell is never auto-refined below this |
| `meshTimeLimitSeconds` | 300 | soft time target: measured and logged when exceeded, never aborts (owner decision 2026-10-20) |

A sheet edge that stops short of a face stays open in the mesh even at 1 mm (the weld joins near-coincident vertices, not an edge short of a face): a containment roof, door or slot panel whose edge stops a few millimetres short of the sheet or rack it faces leaves a long slit, and with full-block racks the whole aisle-to-aisle airflow imbalance jets through it (CMI-Guichou did not converge; with its 13 slits closed it converged in 175 steps). The React mesh pre-flight samples every baffle edge along its length and reports an edge whose every open sample meets another model within 20 mm, with a recommended one-click fix that extends the edge onto that surface (one undo step; `collectSheetGaps` in `run/meshPreflight.ts`). A sheet that bridges two models but stands a few millimetres off their faces (CMI-Guichou's aisle-end doors, 2-14 mm in front of the rack faces, so both side edges were full-height slits) is first moved onto that face plane when the models on its two opposite edges agree on it. An edge that faces open space anywhere is left as drawn, so modelled leakage is not touched.

The preset lives once in `src/frontend/shared/js/mesh_sizing.js` (`dcCartProductMeshConfig`); the reasoning behind the sizing is in [6Sigma cell-size rule](sixsigma-cell-size-rule.md). The engine's own design notes are in `src/backend/aircooling/third_parties/dcCartMesh/DCCARTMESH.md` ("Parallel run", "First-layer floor", "Memory").

## When an edit needs a new mesh

The solve and AI solve tabs compare the current export with the STL batch the case's mesh was generated from
(`POST /api/mesh-change`, `src/backend/aircooling/runner/mesh_change.py`; the batch is the `log/air-inputs/<id>/`
archive named by `Mesh/mesh-source-batch.json`, else found by the uploaded STL hashes of the mesh's source receipt):

| Result | Typical edit | What happens |
|---|---|---|
| `none` | ACU / server airflow, power, supply temperature; room temperature; porosity; fluid properties | solve or infer on the mesh as it is |
| `patches` | a patch added or removed on an existing face; a patch's role changed between wall and opening (compared with the manifest the mesh was generated from, archived beside its STL batch); a standby ACU turned on | **Update mesh patches**: the cells stay, the boundary faces of the changed patches move to the new solids (face nearest each face centre), `createPatch` reorders them (serial on a reconstructed mesh, `-parallel` on dcCart partitions), the patch types come from the runner's own `snappyHexMeshDict`, dcCart's per-rank `dcCartSourceRegions.json` is rewritten from the moves, then case setup, receipts and the final checkMesh run as after any mesh (`run_cfd --mesh-only --patch-update <plan>`) |
| `geometry` / `mesh` | anything that moves, resizes or adds a surface; a mesh setting changed (cell size, refinement level, engine, locationInMesh: reason `settings`, compared with the manifest the mesh was generated from, keys both carry; the mesher's rank count and the editor's view switches are not settings); no mesh yet | generate the mesh |

Until the check has answered for the current edit (its 1.2 s debounce and the check itself) the solve and AI solve
buttons wait as they do for a mismatch, and their click handlers ask again at the moment of the click; a check that
fails to run holds nothing, since the runner still refuses a mesh its receipt does not match.

A patch update keeps the existing faces, so a new opening's outline follows them: a whole-face opening is exact,
a partial one is within half a face of the drawn rectangle (an ACU front opening of 0.48 m² came out 0.51 m² on
Base_leakage), and the opening keeps the refinement level of the surface it was cut into. Flow-rate boundary
conditions apply the drawn airflow either way. Coupled patches (baffles) are refused: changing a baffle needs a
new mesh. After the faces move, every physical patch takes the wall / patch type of the runner's region table
(one pass over `snappyHexMeshDict`), which also covers a role changed without any STL change. A snappy mesh is
always reconstructed by the end of its pipeline, so a partitioned mesh is a dcCart one; measured: DCTS_0112
(snappy, 0.9 M cells) 30 s, Base604 (dcCart, 16 ranks) 130 s, both with their strict quality unchanged.

The dcCart source-region receipt pins the mesh input from v4 (2026-10-28) without operating values (airflows,
powers, temperatures, porosity coefficients, sensor probes), so changing them no longer refuses solve-only. A mesh
whose receipt was written before v4 is reported as needing a one-off patch update with nothing to move, which
records the receipt again. From v5 (2026-10-29) the identity also leaves out the editor's view switches saved with
the mesh settings (`locInMeshVisible`, `blockMeshEditActive`, `blockMeshMoveMode`): showing the locationInMesh marker
used to make a dcCart mesh ask for that refresh.

snappy's `meshQualityControls` use the strict checker's skewness limit, 4, for boundary faces too (2026-10-29; the
usual snappy value is 20). With 20, snappy kept boundary faces checkMesh then rejected: DCTS_0112 always came out
with two wall faces at 4.32, so both the mesh job and a manual check reported a failed quality check.

## Building the engines

| Engine | Binary | Build |
|---|---|---|
| `dcCart` | `third_parties/dcCartMesh/platforms/<WM_OPTIONS>/bin/dcCartMesh` | `./deploy.sh` (or `./Allwmake --cart-only`) in `src/backend/aircooling/third_parties/dcCartMesh/`; the bootstrap (`tools/bootstrap_dcdtuni.py --install`) runs it |
| `snappy` | stock `snappyHexMesh` from the conda OpenFOAM 2412 | nothing to build |

## Tests

- `src/backend/tests/test_mesh_engine.py` — engine resolution and dispatch, and the refusal of `meshgen`.
- `src/backend/tests/test_mesh_mode.py` — the retired `meshMode` keys are ignored; semantics follow the engine.
- `src/backend/tests/test_mesh_acceptance.py`, `test_mesh_deadline_handoff.py` — patch acceptance and the soft time budget.
- `src/frontend/react/test/retiredMeshEngine.test.ts` — the editor's load turns `meshgen` into `dcCart` and logs it.

## Retired: meshgen

MeshGenerator (`meshEngine: "meshgen"`, the vendored `meshgen-cli` under `third_parties/meshgenerator/`) was retired on 2026-10-26 by owner decision, before any case had a strict-`checkMesh` pass on it. Its source, runner pipeline, editor option, bootstrap step and tests were removed; the last commit that has them is `bc5312a9b`.

## History: the retired three-way `meshMode`

From 2026-06-16 to 2026-09-09 the mesh panel offered a single *Mesh Mode* dropdown driving `snappyHexMesh` in three ways:

| Former `meshMode` | Snap | Background |
|---|---|---|
| `bodyFitted` (then default) | on | uniform `blockMesh` |
| `cartesianLocalized` | off | uniform background plus per-object octree `refinementRegions` |
| `structured` | off | grid lines placed on object faces (`compute_aligned_grid`), which spanned the whole domain ("cross-hatch") |

`structured` was a work-around for stair-stepped boundaries in snap-off meshes. Cut cells remove the stair steps, so it was retired together with the dropdown, and the localized-octree idea lives on as dcCart's refinement scheme. A per-object embedded multi-block `blockMesh` (the 6Sigma-style localized grid) was tried in June and rejected: `blockMesh` merges an embedded block's corner points into the surrounding frame, so the merge fails for free-standing racks; a true localized grid would need overset coupling. The full June design text is in the Git history of `docs/reference/mesh-modes.md`.
