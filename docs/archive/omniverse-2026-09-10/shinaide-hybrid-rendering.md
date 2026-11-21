# Shinaide hybrid project and renderer integration

Started 2026-09-26, updated 2026-09-27. This records development and browser
testing of the actual Shinaide air/liquid project, first in Three.js, with a
shared conversion layer for the React editor's Omniverse renderer. See the
[current requirement evidence](../liquid-2026-09-10/liquid-goal-evidence-2026-09-26.md) and
[handoff](../handoffs-2026-09-10/liquid-platform-handoff-2026-09-27.md) for achieved and open gates.

Initial constraints were CPU-only development with limited utilization,
PyTorch forward physics and differentiable tensor rollout, engineering geometry
and texture fidelity, and independent project/run schemas. The original CPU
service used one core; after the user removed that restriction, the task service
was widened to four selected cores without changing the saved experiment.
One separately authorized single-GPU episode later passed CUDA numerical checks
but failed the live OV stream test. That authorization was consumed; no further
GPU execution is implied by the later CPU resource change.

## Source of truth and paths

- `.cfdproj` owns geometry, stable object UIDs, equipment, physical settings and
  saved experiments. Preserve the supplied Shinaide source file.
- Air export uses the frontend's geometry builders and produces grouped STL plus
  `.cfdrun`; the existing OpenFOAM backend remains responsible for air CFD.
- Liquid export compiles the same project into `.liquidrun`, including time,
  load events, controller settings, equipment parameter provenance and object
  bindings. Its runtime result must be readable by the ordinary postprocess UI.
- A renderer scene snapshot is a derived representation of the built geometry,
  never a second editable project. Three.js buffers/world transforms/materials
  become a versioned packet and then USD for Kit. Both renderers refer to the
  same project UIDs, camera/view state, selection and physical result time.
- Hybrid display does not establish two-way coupled air/liquid simulation.
  Retain separate IT and liquid heat inputs and label their meaning; do not
  silently give both solvers the full IT heat load.

## Baseline inspected before implementation

At the initial inspection, Mac was at `89254f31` with existing uncommitted
result-discovery work, while the Linux worktree was at `bc798177`. React did
not yet implement the renderer switch; the independent Omniverse CFD demo
could not read the liquid geometry. These were baseline findings, not the
current branch state. Both task worktrees were subsequently aligned on
`codex/liquid-control-platform`; inspect Git HEAD before further edits.

## Original implementation order

1. Finish liquid result discovery and stable UID binding; retain old result
   compatibility. Test serial cold plates, partial runs, renamed objects,
   multiple cold plates per server, and physically mapped pipe cells.
2. Store experiment duration, timestep, controls, load event and parameter-pack
   selection in `runDefaults.liquid.experiment`. Reopening and undo/redo must
   recover settings; runtime creation must compile the saved configuration.
3. Export the actual Three.js scene into a renderer-neutral mesh packet, including
   liquid equipment and pipes. Filter editor helpers, preserve world transforms,
   instances, material groups, units and UID identities. Consume it as USD in Kit.
4. Integrate the OV viewport in the same React workspace. Keep the Three.js scene
   and project state mounted, report connection/ACK failure, and return safely to
   Three.js. Preserve camera/selection/time across the switch; verify actual
   streamed OV geometry rather than a static image or disconnected viewer.
5. Exercise the real Shinaide project in a browser: load, inspect mixed geometry,
   export both runtime formats, save/reopen experiment, run liquid, postprocess,
   temperature display and renderer switching. Record run IDs and screenshots.

## Acceptance evidence required

- Targeted regression tests plus React production build.
- `.cfdproj` save/load retains the scenario and produces equivalent `.liquidrun`
  physics while `.cfdrun` retains the air path.
- Ordinary postprocessing reads a completed dynamic run, with object names for
  display and UIDs for lookup, aligned sample/time arrays, and no invented values.
- Three.js shows the mixed case's air equipment, racks, CDU and connected piping.
- Export/import preserves enabled physical geometry and UID/transform mapping;
  real OV stream shows the same project and returns to an intact Three.js view.
- Run provenance ties project version, runtime inputs and result time together.

## Validation discipline

Continue the existing development branch and preserve pre-existing Mac setup
files. Use the repository launcher; Linux solver checks use its `dcdtuni` conda
environment and explicit worktree `src`. Sync named task files only after checking
the remote tree; do not overwrite another task's edits. Unit tests or an exported
USD alone do not prove renderer switching. Keep the full goal open until the
browser and actual streaming acceptance evidence exists.

## Verified progress, 2026-09-26

- CPU service: 40901 worktree, official launcher, port 8360 (production build),
  forwarded on this Mac at 127.0.0.1:18360. At this initial stage it used
  one-core affinity/nice 10/BLAS1 with CUDA disabled; its later four-core
  affinity is recorded in [the resource update](../../benchmarks/cpu-resource-update-2026-09-27.json).
  Launcher now puts its own `src` first on PYTHONPATH; without
  this, the editable main installation did not contain the runtime module.
- Browser loaded the real 32-object Shinaide project, including 14 racks,
  four ACUs, one CDU and two main pipe segments. Existing PBR image requests
  succeeded. Saved the configured variant as `Shinaide_hybrid_cpu.cfdproj`.
- Browser created and completed run `b2d5ae71f0f845598b26687858c52b26`:
  187 nodes, 242 edges, 56 cold plates, 10 seconds / 11 samples, 56 load events
  at 5 seconds with factor 1.4. The ordinary postprocess tab read all 14 rack
  summaries / 56 servers, and its time slider switched the displayed values
  between 0 and 10 seconds. Result and standalone v2 input are in
  `case/Shinaide_Liquid/Liquid/log/run-b2d5ae71f0f845598b26687858c52b26/`.
- Browser exported `Shinaide_cfd.zip`: one schemaVersion-2 `.cfdrun` plus 12
  grouped STL files; `CfdrunV2.model_validate` accepted the actual exported
  manifest. No OpenFOAM mesh/solve job was launched in this check.
- Original Shinaide SHA-256 remained
  `cff178754280c22f4aacae181224c6e2292a18819841e41479fbb39aa04de662`.
- Real-case PyTorch forward/backward and finite-difference evidence:
  [CPU gradient report](../../benchmarks/shinaide-cpu-gradient-2026-09-26.md).
  This numerical/software validation is not site calibration.
- Actual browser scene export first revealed mesh seed helpers without model
  identity, then standalone valve-connection tubes. Both were reproduced in
  targeted tests: semantic helper flags exclude editor aids; valve IDs join
  uniquely to their real parent pipe in `.cfdproj`. No physical model is
  discarded to obtain a successful export. Later CPU OpenUSD export/reopen
  retained all 928 relative texture references per frame; the authorized live
  OV attempt still timed out before decoded video and scene ACK. See the
  [scene evidence](../../benchmarks/shinaide-dae-scene-export-2026-09-26.json) and
  [Kit diagnosis](../../benchmarks/single-gpu-kit-2026-09-26/README.md).

## Browser cross-mode check, 2026-09-27

The same saved DAE `.cfdproj` displayed 32 models and 800 Three.js meshes when
switching from liquid to air. Its air page retained the saved snappyHexMesh
definition and room/seed geometry; the air postprocess selector had no solve
record. Returning to liquid retained the saved DAE mode, 600 s/20 s settings,
one explicit primary HX, and run `737a0ecc25e342acb7932356222e566f` at its
31st sample/600 s with pipe and chip colouring enabled. The project and
original source hashes remained unchanged on Mac and 40901. The
[browser record](../../benchmarks/shinaide-hybrid-browser-2026-09-27.json) proves
same-project configuration retention and navigation; it does not claim an
OpenFOAM mesh/solve pass or live OV pixels.

## Isolated OpenFOAM execution check, 2026-09-27

The current saved DAE `.cfdproj` was independently compiled by the existing
React air exporter into a strict v2 `.cfdrun` and 12 original STL groups. The
original project hash remained `9d193dc0…d25bafd0`. These inputs were copied
into a unique scratch case on 40901; the production Shinaide case was not
meshed or solved. The saved eight-rank mesh/solve settings were changed in the
*derived scratch run only* to four ranks, and its solve end time to ten steady
iterations. Geometry and physical boundary inputs remained unchanged.

The first attempt exposed a v2 migration trap: the manifest holds both root
`meshConfig` and a compatibility `meshGeometry.meshConfig`, which is what the
current mesher consumes. Changing only the root value left the effective count
at eight MPI ranks. An external 240 s timeout left task-owned MPI children;
they were identified by owner and scratch cwd and terminated. The
[failed-attempt record](../../benchmarks/shinaide-air-smoke-initial-2026-09-27.json)
is retained rather than presenting it as a completed mesh.

The corrected scratch input set both mesh fields to four ranks and a 240 s
internal mesh watchdog. It generated a 285,528-cell mesh with 343 boundary
patches, then ran the project's `buoyantBoussinesqSimpleFoam_new` for ten
iterations and reconstructed `U` and `T`. The supervisor finished without
remaining owned processes. **This is pipeline execution, not physical
acceptance**: `checkMesh` failed three quality checks (maximum skewness
5.1291, six under-determined and 13,363 concave cells), and the independent
convergence report says `NOT_CONVERGED` with too few sensor samples. The
`writeExplicitBC` utility was not built, so reconstructed boundary values were
not made explicit. Surface checks found no illegal triangles in the 12 exported
STL groups; 11 are nonclosed grouped surfaces, which alone does not identify
the mesh-quality cause. The [bounded smoke report](../../benchmarks/shinaide-air-smoke-2026-09-27.json)
contains original/derived manifest and STL hashes, quality counts, result
sizes and cleanup status. Full generated files remain in the 40901 archive
named in that report; no GPU or OV service was started.

A separate React export regression changes the saved project's air mesh and
solve budgets to four processes and ten iterations. The production exporter
writes the same eight execution-critical fields in the root v2 configuration
and the compatibility `meshGeometry` block, and the result passes the strict
v2 schema. This checks the normal `.cfdproj` editing path against the new
backend parity guard; it does not change the archived smoke inputs or validate
mesh quality.

The archived mesh job exposed a separate reporting gap: its process exited
zero even though the final `checkMesh` printed `Failed 3 mesh checks`. Mesh jobs
now publish the *last* quality report as `checkMeshOk`, `failedChecks` and
structured warnings. The React mesh status shows the failure, and the solve
warning gate reads it even if the solver panel opens after meshing or the
primary event stream falls back to polling. Each result is tied to the project
that started the job, and a failed new mesh cannot be masked by an older disk
scan. Serial orphan/isolated-cell cleanup rechecks the repaired mesh before
this verdict is reported, with an eight-pass limit. Reinterpreting the archived
log gives four highly skew faces, six
low-determinant cells and 13,363 concave cells; no new mesh or CFD solve was run
for this status fix. See [quality propagation evidence](../../benchmarks/shinaide-mesh-quality-propagation-2026-09-27.json).
An explicit `checkMesh` command that fails before producing a verdict now
creates a blocking warning; a mesh process that exits nonzero is reported as a
run failure before the quality status is considered.

The likely geometry focus is the raised-floor/baffle seam near
`(2.076, 0.785–0.792, 4.014)`, where snappy repeatedly reported points
crossing nearby faces. A controlled follow-up should compare quality before
and after `createBaffles` in scratch, then change one mesh parameter at a time.
The current evidence does not isolate which stage caused the final defects.
