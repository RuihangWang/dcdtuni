# Air product presets and mesh budget (2026-10-05)

The editor now creates projects with a 0.25 m dcCart background, eight mesh ranks, the existing 1.2 mm weld and 0.3 first-layer fraction capped at 5 mm, and a 17.5 mm rack-cell floor. Applying the production preset retains user local-refinement levels, geometry, seeds and cell-count limits. The same mesh fields were migrated in 51 current case, room-template and manuscript experiment inputs. Historical benchmark and schema-baseline files retain their original identity; the separately preserved user-edited GTC2026 input is handled by the integrating agent.

The whole mesh pipeline now has a 300 second ceiling. Strict `.cfdrun` schema, frontend export and backend derivation carry `meshTimeLimitSeconds`. Shorter positive project budgets still work; missing, disabled, invalid or larger values use the ceiling. dcCart's native dictionary and the runner watchdog agree, and the runner clears its deadline before solving. A timeout is a failed mesh, never a successful five-minute performance measurement.

## Regression evidence

- A new regression reproduced six failures before the fix: strict schema rejected the budget, legacy disabled values removed the ceiling, and the native dictionary was unlimited.
- Targeted backend regression: 68 passed, including process-group timeout and budget clearance before solve.
- Targeted React regression: five files / 24 tests passed, including new document defaults, explicit-refinement preservation and schema contracts.
- The existing meshgen command-shape test was updated to assert the sanctioned environment flags while allowing the additional production flags already present on the baseline.

## Acceptance still required

These checks validate the preset and budget contract. They do not establish that every case finishes inside five minutes, passes checkMesh or reaches physical convergence. Fresh server-path dcCart/checkMesh, patch acceptance and steady/transient solver receipts are recorded separately.

## Strict target mesh repair

The same real React-exported Shinaide liquid geometry passed the complete server mesh path in 116.54 s (job `b2197a80`): 3,755,177 cells, zero failed final checkMesh checks. The two disconnected-region and high non-orthogonality warnings remain visible; they are not suppressed. Three earlier ACU wall faces had a boundary-skew offset along Y of 13.19 mm. A preceding X seam cut appended a narrow half; the following Y seam applied only to the retained original index. The fix follows every original cell's descendants through the globally ordered seam planes and rechecks each current point set, bbox share and whole-cell zone guard before a cut. A convex tilted-slab counterexample in `guard_sliver_audit.py` demonstrates why inherited eligibility alone is unsafe.

The native face/cell centroid calculation now uses the installed OpenFOAM operation order. A mathematically ideal skewness-four floor face crossed the strict threshold by a few ULPs when written; the repair target now lies slightly below four while the final checkMesh threshold remains unchanged. Boundary absorption uses the existing pyramid, convexity, zone, coupled-face and skewness guards. Stage-only debug bounds can capture the selected cells without enabling costly per-shell fingerprints. The saved audit reads raw written binary meshes and reproduces the original faces and centroids.

The same native build passed its serial and two-rank refined-box smoke (5,704 cells and volume one). Native readmission work is partitioned over global leaf IDs; all ranks combine the candidate classification and apply each gathered wedge round in the same order. Hash preallocation avoids an oversized point table, and intermediate reach sets omit redundant sorting while the final reach order is unchanged. Target MPI wedge parity and the large-case timing matrix remain separate acceptance work. No claim that every case is already below five minutes is made here.

## Nonzero wedge MPI validation

Tri_angle was exported by real React separately for serial and eight ranks. Both complete mesh jobs passed strict final checkMesh: 15.11 s and 14.25 s. They classify the same 35,496 requested corners, re-admit 15,431 candidates including 152 fluid-only wedges, and produce the same 4,576 clipped cells / 4,647 cut faces. Both report fluid volume 41.315 m³. Summing written physical patch areas, including `processorCyclic` faces under their `referPatch`, agrees within 1.8e-15 m²; no physical patch is missing. The final serial and parallel cell counts differ (109,830 and 110,494) because existing processor-coupled merge guards retain different cell tessellation; no bitwise whole-mesh identity is claimed. Both retain the non-orthogonality warning. Raw receipts and the repeatable polygon-area audit accompany the candidate/wedge parity record.

A rank without an initial candidate group for a globally unioned seam plane now skips the local incidence rebuild. Its descendants cannot be eligible for that plane. The clip has no collective calls, the common global plane order and final reduction remain unchanged, and every current-geometry guard still runs on ranks with candidates. Base604 full timing is under separate verification; its previous ancestry run timed out before final checkMesh and remains a failure.

## Opt-in requested-corner differential validation

An experimental requested-corner builder queries integer finest-grid octants and the live leaf maps instead of assembling every raw face and every point. It preserves the raw builder's registration rule: a requested vertex must be an incident live-leaf corner or a quadrant corner on its finer face neighbour. Unregistered points merely lying on a coarse face are excluded. Production continues to use the original builder unless `DCCART_DIRECT_CORNERS` is explicitly present. `DCCART_COMPARE_CORNERS` runs both implementations and aborts on any coordinate-bit or sorted incident-live-cell mismatch.

Four genuine React exports passed that differential gate and final strict checkMesh: Tri_angle (35,496 requested vertices, 15.22 s), DAML-Testbed (38,367, 22.41 s), ordinary Shinaide (160,557, 38.78 s), and Shinaide liquid (646,014, 128.95 s). Those timings include the slower reference comparison and prove correctness on these geometries; they do not establish the large-case performance target. Conditions, source/native hashes and raw SSE are saved under `matrix-corner-differential/`. The original refined-box serial and two-rank native smoke also passed after compilation.

### MPI startup and repository boundary

Mesh MPI steps now select one rank per CPU allowed by the parent process,
with distinct physical cores before SMT siblings. An existing HYDRA_BINDING
or DCDTUNI_MPI_BINDING=inherit remains authoritative; solves keep their
previous policy after the mesh budget is disarmed. This scheduling change
does not establish a five-minute acceptance result on its own.

The X11-scanning hwloc GL plugin remains excluded, but the plugin cache is
created under the owning checkout's .codex-tmp rather than a global temp
folder. A resolved path guard rejects a cache redirected outside the checkout.
The runtime and existing budget regression suite passed 31 tests; the raw
output is in mesh-mpi-runtime-tests.log.

### Final geometry scope correction

Earlier campaign PASS records used checkMesh -allTopology with its standard
geometry checks. They do not establish the extended -allGeometry scope in
the geometry/topology ADR. A separate real Tri_angle check against the same
written mesh found five extended failures: face tetrahedra, determinant,
cell concavity, interpolation weight and volume ratio. The raw diagnostic
is in Tri_angle-allGeometry-diagnostic.log; its zero process exit does not
mean success. No geometry threshold has been relaxed.

The final product cleanup now always invokes both -allTopology and
-allGeometry, regardless of engine or the legacy snap_off override. It
requires a complete Mesh OK verdict after any cleanup and rejects a Failed
N report even when the checker process exits zero. The scope and verdict
regression suite, together with MPI/budget checks, passed 42 tests.
Representative native geometry repair is still required before the current
room-capable matrix can be accepted under this corrected scope.


## Full geometry stage diagnosis and registry index (2026-10-05)

The production final check now includes `-allTopology -allGeometry`. The earlier
campaign passes are limited to `-allTopology` and its ordinary geometry checks.
The new Tri_angle full-scope runs all reject the delivered mesh; no stage skip
is enabled by default. Inputs and physical source geometry are unchanged.

| Stage condition | Face-tet failures | Small determinants | Concave or planar cells | Low weights | Low volume ratios | Total failed checks |
|---|---:|---:|---:|---:|---:|---:|
| Current baseline | 1914 | 107 | 16617 | 1062 | 1369 | 5 |
| Skip only face coalescing | 10 | 106 | 27476 | 1065 | 1378 | 8 |
| Skip only cell merging | 2 | 1517 | 9333 | 5452 | 5090 | 5 |
| Skip both | 7 | 1517 | 9359 | 5451 | 5091 | 8 |

The two runs skipping face coalescing also contain one zero-volume cell and
fail additional geometry checks. These are diagnosis results, not accepted
presets. The raw strict-stage folders contain the real React export, native
artifact hashes, full API wall times and checker stdout via SSE.

The installed OpenFOAM `primitiveMeshCheck.C` lines 810–900 also classifies
coplanar face centres as a concavity failure (`planarCosAngle_ = 1e-6`, line 43).
`strict-planar-convex-example.json` gives one actual flagged cell whose complete
vertices satisfy every outward face halfspace: the maximum outside distance is
roundoff scale. `audit_strict_planarity.py` reproduces this observation. This
classification does not excuse independent face-tet, determinant, interpolation
weight or volume-ratio defects, and no checker threshold has been changed.

A new opt-in region triangle membership index retains surface/triangle traversal
and floating-point addition order, reads current points after constructor-time
welding, and compares area, plane and thin-sheet results exactly with the old
implementation. The final audit sweeps all actual and declared region IDs,
including holes, repeated IDs and missing sentinels. Thin-sheet queries on
non-carving triangles have no shell in the reference implementation; this
undefined domain is explicitly reported and excluded from that oracle. The
current Tri_angle fresh runs complete the full registry sweep on every rank.
The index remains experimental until final product quality and timing pass.

The native writer now rejects a globally empty mesh before any topology write.
The original bad-seed case was reproduced through the real React/export/mesh
API and rejected on both ranks in `strict-stage-native-globalzero`. A separate
one-cell/two-rank diagnostic uncovered an earlier empty-local-rank baffle-stage
collective hang; it was stopped within its owned fixture and remains unresolved.
Empty local partitions must remain legal when the global mesh is positive.


The empty-local coverage collective hang is repaired by making every rank
participate in the domain min/max reductions; an empty rank contributes the
inverted-box identity. `empty-local-rank-fixed/` records a separate eight-cell
fixture on three ranks with one empty local partition. Native generation and
`checkMesh -allTopology -allGeometry -parallel` complete successfully with
`Mesh OK.`. The earlier one-cell fixture additionally fails the checker cell
determinant test because it has no internal stencil; it is retained as the
original hang reproducer and is not called a strict quality pass.


The main checkout also held1502 ignored current generated input projects:501 GTC inputs and1001 Slab inputs (including its legacy template). They are outside the52 tracked-current-input manifest. They now use the same0.25/dcCart/300-second preset. Legacy v2 files are updated at their real cfdRoom configuration source; current v4 files at runDefaults.meshConfig. The original bytes are backed up in the repository, and the migration verifies that no non-preset geometry, thermal load, boundary condition, path or threshold leaf changed. A second audit verifies every resulting file hash and effective preset. Existing model/cfdrun/log snapshots and sampling plans retain their historical identity. No dataset mesh or solve was executed, and the preset deadline is not a timing result.
