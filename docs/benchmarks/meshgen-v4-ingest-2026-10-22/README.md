# meshgen reads cfdrun v4 again (2026-10-22)

Every run here is `python -m backend.api air <copy>/<name>.cfdproj --mesh-only
--mesh-engine meshgen --max-processes 8` on a copy of `case/<name>/<name>.cfdproj`.
The headless export writes a cfdrun with `schemaVersion: 4`. `receipt.json` lists
the binaries, the causes and each run's verdict, and gives the SHA-256 of every
copied `result.json` and `mesh-job.log`.

## Cause and repair

1. **Stale binary (the campaign B4 failure).** The vendored `meshgen-cli` had not
   been built, so the runner fell back to the `Ref_code/MeshGenerator` build from
   2026-09-21. That build reads a manifest as having a `meshGeometry` blob only
   when `schemaVersion == 2`. A v4 manifest therefore reached it with no domain
   box, no room and no region levels (`ingest/devcheckout_v4.mapping.json`), and
   S7 refused the default seed as outside the domain. The repair builds the
   vendored copy with `deploy.sh`; the build from unchanged source reproduced
   the 2026-10-06 ELF bit for bit. The runner now also compares the engine's
   ingest mapping with the manifest's `blockMeshBounds` and refuses the run
   with a clear reason when they disagree (`runs/1_SingleRack_stale_devcheckout`).
2. **surfaceUid identity in the vendored v3/v4 reader.** `CfdrunGeometryView`
   keyed `boundaryBindings` by bare `surfaceUid`, which is local to its
   `objectPath`. As a result, every multi-server case was refused. The key is now
   `(objectPath, surfaceUid)`; every `case/*/model/*.cfdrun` has zero conflicts
   under it.

## Where each case stands

| Run | Result |
|---|---|
| SingleRack | the right domain and all 23 solids are read; the kernel refuses one merged level-7 cell at its S10 centroid gate |
| Base_leakage | read correctly; the kernel refuses one open cell at S10 |
| Slab | exports 142,947 cells in one region; strict `checkMesh` fails (open cells reported by `-allTopology`, skewness, face tets), so the mesh is not accepted |

The remaining refusals come from cut-cell construction inside the kernel, not
from the manifest. `receipt.json` (`diagnostics_not_kept`) records the knob
experiments that point there: the September kernel, the post-carve quality
merge, and a warn-only S10 gate followed by S11b. The next step belongs to the
MeshGenerator stream: S10/S11 closure on these cells, starting with SingleRack.
