# Work package — Base604 raised-floor footprint conflict and MeshGenerator surface-dir exclusion

**Date:** 2026-09-19
**Status:** open; two independent items split out of the W2/W7 investigations.

> **Resolution of item 1 (2026-09-21, host 40902).** The 0.980 refusal was a
> MIXED triSurface on disk, not an overhang in either export: the refused runs
> measured `have` = 9.12 m² (the FRESH `part1` hole, Z ∈ [0.05, 1.0]) against
> `want` = 9.305 m² (the JUNE `patch_2`, Z ∈ [0.030697, 1.0]); the 19.3 mm × 9.6 m
> strip between the two edges is exactly the 0.185 m² deficit, and it is
> level-invariant because it is geometry. Both consistent sets mesh with
> `Room_Raising_Floor_patch_2` coverage exactly 1 (June set 9.3053/9.3053,
> fresh set 9.12/9.12, cap 6M, np16). No mesher change was needed; the runner
> now refuses a floor whose openings overhang their floor parts before meshing
> (`backend/aircooling/runner/floor_consistency.py`,
> `DCDTUNI_FLOOR_OVERLAP_CHECK=off` to bypass). The `relabelCoplanarPatches`
> theory of the 09-21 handover is also refuted: the gate runs inside
> `cutBaffles`, before relabel, and relabel never touches zone faces.
Neither is config-fixable; both need targeted edits outside the config surface.

## 1. Base604 `Room_Raising_Floor_patch_2` footprint conflict (model/export fix)

### Evidence (WP-W2, `/tmp/w2/round2_gate_summary.txt`)

- The baffle coverage gate refuses at **every** refinement level: `have` is
  9.12 m² at level 3 and at level 4 (cap raised 6M→24M unlocked level 4 —
  `level 3→4: refined 552736`, leaves 23.5M — and coverage did not move:
  0.98061 → 0.980348).
- `perforated_floor_Room_Raising_Floor_patch_2.stl` spans X[1.8,11.4],
  Z[0.0307,1.0] with a ragged low edge (0.9693 m deep), while its passing
  siblings (patch_1/3/4/5, coverage 1.0) have clean 1.2 m footprints.
- The coplanar raised-floor solid
  `wallBaffle_floor_Room_Raising_Floor_part1.stl` (same y=0.8 plane) claims
  the ~19 mm strip where patch_2 overhangs part1's hole:
  9.6 m × 0.019 m = 0.1824 m² ≡ the observed deficit
  (9.30282 − 9.12 = 0.18282). The mesher tags that strip to part1's wall
  patch, so the faceZone can never reach the 0.995 threshold.

### Fix route

Correct the patch_2 footprint at the source (case model / exporter) so it does
not overhang the floor hole: either trim patch_2's Z-min to the hole edge
(~+19 mm) or extend part1's hole to match patch_2 — whichever matches the
as-built intent. Re-export through the browser (geometry source of truth),
then re-run the gate at the existing config (cap 24M already in
`case/Base604/model/Base604.cfdrun`, harmless until needed). Acceptance:
coverage ≥ 0.995 (or informational-perforated), mesh rc=0, checkMesh OK,
steady solve converges per the standing criteria.

Do **not** "fix" this by raising the gate threshold, env-offing
`DCCART_BAFFLE_COVERAGE_MIN`, or deleting solids: the deficit is a real
coplanar-ownership ambiguity and the gate is doing its job.

## 2. MeshGenerator `scanTriSurfaceDir` must skip editor-only interface STLs

`92a80a0c` excludes `merged_server_interface_walls.stl` (and per-side
`merged_server_*_left/_right.stl`) from the snappy/dcCart consumption paths.
The `meshEngine: meshgen` path hands the whole `--surface-dir` to
`meshgen-cli`, and MG's dc-raw ingest is a disk scan
(`Ref_code/MeshGenerator/core/src/CartesianCfdrun.cpp:596 scanTriSurfaceDir`,
"bound or STL_ONLY heuristic, never silently drop"), so MG would classify the
interface bucket as geometry. Add the same exclusion via MG's existing
`ignored (file,solid)` mechanism (~:1371), keyed on the identical filenames,
with a comment cross-referencing `is_editor_only_stl()` in
`src/backend/aircooling/runner/mesh_acceptance.py` as the authority.
Acceptance: a meshgen run on a case whose export contains the interface bucket
produces the same shell/patch set as before the bucket existed.

## 3. Related but separate (already queued)

- `dcCartSurfaces.C` region-offset aliasing (Tri_angle): undeclared
  through-zone surfaces occupy global region numbers without incrementing
  `patchNames`, aliasing their area into the next declared surface's `want`
  (ratio ceiling 0.72746 < 0.995 → no config can pass). Fix = undeclared
  surfaces must not take a global region offset; queued as WP-W5 behind W1
  because both touch the dcCart build chain.
