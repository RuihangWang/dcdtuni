# Mesh Castellation Performance — Findings & Dead Ends

**Status:** living note · **Last updated:** 2026-06-19
**Context:** acceleration push on `Alibaba-hongchou` (anonymized `case_14_large_C`,
200 racks, 41.3×20.7×7 m room, `meshMode=structured`, 24 procs).

This note records what we measured, what we *disproved*, and the open anomaly, so
the next person does not re-walk the same dead ends.

---

## 1. Where the mesh time actually goes

Clean **uncontended** baseline, `Alibaba-hongchou`, 24 procs, structured:

| Phase | Time | Share |
|------|------|-------|
| blockMesh → snappy | 52 s | 3 % |
| **snappyHexMesh castellation** | **1123 s (18.7 min)** | **75 %** |
| reconstructParMesh + createBaffles + cleanup | 315 s | 21 % |
| **Total mesh** | **1490 s (24.8 min)** | — |
| Final cells | 3,897,943 | (below the 5 M cap) |
| Quality | max non-ortho 85.4° (44,396 severe), max aspect ≈20, skew 1.0, **Mesh OK** | |

**Castellation is the whole game (75 %).** "Snap off" (structured / cartesian
modes) does **not** skip it — `castellatedMesh` and `snap` are independent switches;
the octree refinement still runs. Optimising anything else is rounding error until
castellation moves.

---

## 2. Confirmed dead ends (do not retry without new evidence)

### Method A — more processors (`meshProcess` 16 → 24)
Useless for this case. Castellation cell **count** is decomposition-independent, and
snappy castellation parallel-scales poorly (rebalances every refinement iteration).
hongchou at 24 procs (1123 s) was actually **slower** than comparable Alibaba-sige at
16 procs (697 s) — proof the cost is driven by geometry/region structure, not cores.

### Method B — parallel `createBaffles`
Net-neutral on typical cases (the `redistributePar` overhead cancels the
serial→parallel gain). Committed anyway (`4f26bc7`) for the baffle-heavy huge-case
edge. See `parallel-createBaffles-faceZone-gotcha` (the `preserveFaceZones` fix).

### P6 — resolve thin gaps via blockMesh grid lines instead of octree (**FAILED**)
**Idea:** the 500 `rackThinGapRegion` level-4 octree boxes (1U blanking-panel U-slot
gaps) are isotropic — they refine the gap's in-plane directions too. Replace them
with anisotropic blockMesh grid lines (fine across the gap, coarse in-plane) so the
gap resolves as a thin *block*. Predicted ~43 % fewer cells + 500 fewer inside-test
shells → big castellation win, no quality loss.

**Result (A/B, both clean, 24 procs, same geometry):**

| metric | octree (baseline) | grid-line (P6) | Δ |
|---|---|---|---|
| castellation | 1123 s | **1268 s** | **+13 % slower** |
| total | 1490 s | 1650 s | +11 % slower |
| cells | 3,897,943 | 3,755,904 | −3.6 % (≈flat) |
| severe non-ortho | 44,396 | 47,994 | +8 % worse |

Same direction on `CMI-China` (small case). **Reverted; `main` clean at `4f26bc7`.**
Full implementation (schema flag `thinGapGridLines`, `_collect_gap_y_intervals`,
`_apply_gap_segments`, blockMesh wiring, snappy skip, 4 regression tests) is in git
history if ever revisited.

**Two premises this disproved:**
1. *"The 500 level-4 thin-gap boxes are ~1.68 M cells (~43 %)."* — **WRONG, 12× too
   high.** A/B shows they are only **~140 k cells (~3.6 %)**. A 1U gap box is
   geometrically small; isotropic level-4 fill is far fewer cells than the
   slot-width×depth estimate assumed (boxes overlap rackRegion/each other and the
   refinement is bounded). **Thin gaps are not the bottleneck.**
2. *"O(cells × n_regions) inside-testing dominates castellation."* — **also wrong.**
   Removing 500 of 700 refinement regions made castellation **slower**, not faster.
   The extra blockMesh background cells (cross-hatch from the gap grid lines) cost
   more than the saved inside-tests. Region count is not the dominant term.

---

## 3. Why castellation is slow — RESOLVED (the cost is distributed, no silver bullet)

`3.90 M cells / 1123 s / 24 procs ≈ 145 cells/s/proc` — ~10–100× "too slow" vs a
simple mesh. Profiled the clean snappy log (`log.mpi_solver`, phase timestamps from
the loguru SSE stream). Castellation breaks down as:

| Phase | Time | Cuttable? |
|------|------|-----------|
| setup (build surface octrees, 200-rack STL) | 115 s | no |
| **feature refinement** (9 iters) | **275 s** | yes — but raises cell count (see §3.1) |
| surface refinement + removing | 38 s | no |
| shell/surface determine | 124 s | partly (region count) |
| **splitting mesh + introducing zones** (11k patches, 2.7 M intersected faces) | **299 s** | no — inherent to the patch count |
| baffles (1.8 M faces) + merging faces | 225 s | no — inherent |
| writing | 18 s | no |

**There is no single bottleneck.** The two co-dominant costs are (a) **feature
refinement (275 s)** and (b) **boundary surgery for the 11 000-patch geometry
(~524 s: split + zones + baffles + merge)**. The boundary surgery is the bigger
chunk and is *inherent* to creating one inlet/outlet/door patch per rack/server on
200 racks — it cannot be cut without reducing the patch count (a physics decision).

### 3.1 Feature refinement — the attackable 275 s, and why cutting it is a *trade*

`markFeatureCellLevel` (meshRefinementRefine.C) particle-tracks **every** feature
edge from the single `locationInMesh` on **every** feature iteration. The dict emits
`merged_rack_frame.eMesh` at `level = rackRegionLevel = 3`
([stlgen_runner.py:3991](../../src/backend/aircooling/runner/stlgen_runner.py)), so
200 racks' frame edges are re-traced 9 times (~28 s each) — and the last 3 iterations
refine 353 / 25 / **0** cells yet still pay full tracing. The code's own comment
(lines ~3998) already flagged feature refinement as "the dominant cost for 200-rack
cases" and skipped rack-*internal* eMesh — but never the rack *frame*.

**Experiment (Alibaba-hongchou, snap-off, drop `merged_rack_frame` from features):**

| metric | baseline | frame-features off | Δ |
|---|---|---|---|
| feature-refinement determine | ~264 s | **~32 s** | −232 s |
| total mesh wall | ~1490 s | ~1369 s | ~−8 % |
| **cells** | 3.90 M | **4.27 M** | **+9.4 %** |
| max aspect ratio | 20.1 | **6.50** | better |
| max non-orthogonality | 85.4° | **71.3°** | better |

Cell histogram: the +370 k cells are all at **level 3** (2.88 M→3.39 M); with the
frame's feature refinement gone, the `rackRegion` volume refinement fills the racks
more uniformly (and 10× fewer level-4 cells get bumped). So dropping frame features
yields a **higher-quality, more-uniform mesh and ~8–14 % faster castellation, but
~9 % more cells** → a ~9 % *slower solve*. **Net for the full pipeline it is a wash
to slightly negative on speed, but a clear quality win.** It is a trade, not a free
speedup — same shape as P6 (§2), except the quality gain made it worth adopting
(see §4 decision).

**Bottom line:** Alibaba-hongchou is slow because it is genuinely large and complex
(3.9 M cells, 11 k patches, 200 racks). The cost is spread across feature refinement
and inherent boundary surgery; no micro-optimisation moves it much without trading
either resolution (cells) or patch count. Real speedups require coarser resolution or
fewer patches — both physics-fidelity decisions for the user.

**Decision:** the frame-feature drop was **adopted** (gated on snap-off modes) for the
mesh-quality win (aspect 20→6.5, non-ortho 85→71), accepting the +9.4 % cells. See the
`perf(mesh): drop rack-frame feature refinement in snap-off modes` commit + the
`test_snapoff_frame_features.py` regression test. The boundary-surgery cost (~524 s) is
untouched and remains the largest single chunk.

---

## 4. What actually helped

Killing **CPU over-subscription**: during this push a benchmark meshing another case
ran concurrently (48 snappy procs on 24 physical cores, load avg 67). Aborting it gave
the live job all 24 cores 1:1 — roughly **2× faster**, the only real win of the
session. Lesson: never run two `mpirun -np N snappyHexMesh` jobs on an N-core box.
