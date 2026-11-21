# 6Sigma cell-size rule (reference for dcCartMesh sizing)

> **Status (as of 2026-10-21):** the rule below still sets the dcCart sizing
> defaults. The product preset that applies it is
> `dcCartProductMeshConfig` in `src/frontend/shared/js/mesh_sizing.js`
> (`meshBaseSize` 0.25 m, `rackCellMinSize` 17.5 mm, first-layer floor 0.3 /
> 5 mm, weld 1.2 mm). The measurements and verdicts on this page are dated
> 2026-09-21/22 and used the two-leg convergence rule of that time (residuals
> and return-air probes). Since 2026-10-20 a steady run is judged on the ACU
> return-air temperatures alone (0.5 K plateau; residuals are diagnostics), and
> all ten release cases — Shinaide and Base604 included — now mesh with
> dcCart through the React product flow, pass strict `checkMesh` and
> converge: see
> [air-release-cases-2026-10-21](../benchmarks/air-release-cases-2026-10-21/README.md).

Measured 2026-09-21/22 from `Ref_code/6SigmaCases`: three `.room` files
(301 s0, C2-1.NA6211-122, C2-1.NA62-17; zip -> "Room Constructor 17.38"
zlib streams -> XML, `<Grid>` and `<Grid_Summary>` blocks) and the exported
result grid `301VTM/VTM/region_0.vtu` (room 301: 649 m², 238 cabinets, 46
in-row ACUs, 4,607,841 hex cells). Manual: `6sigma手册.pdf` p.165-168.
This page states the rule 6Sigma applies and how dcCart maps onto it. It is
a reference, not a spec; the dcCart defaults it justifies live in
`stlgen_runner.py` (`DCCART_FIRST_LAYER_*_DEFAULT`) and are documented in
`third_parties/dcCartMesh/DCCARTMESH.md` ("First-layer floor").

## What 6Sigma does

**No cut cells.** Geometry is snapped to the grid (`Snap_To_Grid Yes`) and
grid lines are generated through every object face, then the grid is
coarsened 2:1 away from objects ("Refine From Max Size", first level
2x2x2). In the VTU every one of the 783k boundary faces is entirely
fluid-fluid, entirely solid-backed or entirely outside: 0 partial faces,
238/238 cabinets with all four vertical faces and the top on grid planes.
The smallest cell is therefore chosen by rule, never by where a surface
slices a cell.

**Grid controls (identical in all three rooms, so vendor defaults):**

| control | value |
|---|---|
| Max cell size (x, y, z) | 0.25 m |
| Minimum_Gap_Size / minimum cell | 0.010 m (cells below it are ignored by the solver) |
| Aspect_Ratio_Limit | 5 |
| Expansion_Ratio_Limit | 2 |
| Refinement ratio between levels | 2 (2:1 jumps) |
| Cells across an object (Number_in_X/Y/Z) | 5 |
| Hybrid_Cells Minimum_Wall_Distance_Fraction | 0.1 |
| Hybrid_Cells Cell_Tolerance | 0.0005 m |
| Inflation | on, but a solid-conduction aid (5 cells within 1 mm of solids), not a boundary layer |

**Measured cell ladder in room 301 (VTU):**

| region | dx | dy (up) | dz |
|---|---|---|---|
| open room at rack height | 90 mm | 88.9 mm | 100 mm |
| above racks / ceiling | 90-200 | 101.7-250 | 100-200 |
| raised-floor plenum (0.6 m) | 90 | 47.3 | 100 |
| aisle between rows | 78-100 | 44-89 | 78-100 |
| inside a cabinet footprint (0.6 x 1.2 m) | 17.5, 17.5, 20, 20, 45 x10, 20, 20, 17.5, 17.5 | 44.45 (1U), 22.2 (½U) where servers need it | 50 x 24 |

Extremes: min 17.5 / 22.2 / 50 mm, max 0.225 / 0.250 / 0.200 m, min cell
volume 1.94e-5 m³, max/min volume 580, aspect ratio max 5.7 (p99 4.2, 0
cells above 10). Cells thinner than 20 mm exist only inside cabinet
footprints. Cell budget: about 7,100 cells per m² of floor, about 16,000
per cabinet, 41 % of all cells inside cabinets.

**First cell at a wall:** the local grid cell, full size. Object-backed
faces see 17.5 mm (x), 22.2 mm (y), 50-100 mm (z); the wall cell / next
cell ratio has median 1.0 and is never below 1/3. Room walls get 100-250 mm,
the slab 47 mm, the ceiling 250 mm. Standard k-epsilon with standard wall
functions; nothing resolves the boundary layer.

The structured rooms (C2, NA62) push further: smallest cells 10.5-11.8 mm,
just above the 10 mm floor, aspect up to 33, expansion up to 9.6. 6Sigma's
solver tolerates those; OpenFOAM's epsilonWallFunction does not tolerate
the 1e-5 m slivers a raw cut produces, which is the whole reason for the
rule below.

## The rule, in dcCart terms

1. **Background** `h0` = 0.20-0.25 m regardless of room size (6Sigma uses
   0.25 on 649 m² and 850 m² rooms). `meshBaseSize` 0.34 (Base604,
   Tri_angle) is coarser than anything 6Sigma uses.
2. **Rack / ACU region** `h_rack = h0 / 2^L` in 45-50 mm (about 1U): 12-14
   cells across a 0.6 m cabinet, 24 along 1.2 m. Use ½U (22 mm) only where
   server slabs demand it, never below 17.5 mm, never below 10 mm. The
   level-4 auto-raise in `_cartesian_rack_region_level` (cell <= thinnest
   fan-server slab, 12.6 mm on DAML) is finer than 6Sigma's ½U and is what
   multiplied sliver counts (307k merged on Base604 before the floor).
   The rule is a floor on refinement, not a ceiling: 6Sigma adds cells in
   gaps on top of the pitch (Gap_Refinement, 1.17M cells in room 301),
   which the rule does not model, so a finer level the project already
   carries is kept (Base_leakage diverged at the 42 mm pitch cell and
   solves at its author's 21 mm).
3. **First cell at every object surface** `t >= max(0.25 h_local, 0.010)`,
   centroid never closer than `0.1 h_local`. Achieved BEFORE cutting by
   snapping axis-aligned surfaces onto the level-L planes: that is
   `firstLayerMinFraction` (runner default 0.3, cap 5 mm) on top of the
   1.2 mm weld. 6Sigma's `Minimum_Wall_Distance_Fraction 0.1` is the
   same floor expressed on the centroid.
4. **Post-cut floor** for what the snap could not reach (tilted walls,
   free-standing sheets): a cut cell below 1e-3 of its hex must be merged
   or handed to the solid. Bracketed by data: snappy Base604 at 1.4e-3 of
   its finest hex / aspect 45 solves; dcCart at 1.4e-8 to 2e-5 / aspect
   800 diverges. The floor-0.3 Base604 mesh sits at 2.1e-3, DAML at 1.4e-8
   only in 43 baffle-carrying cells that the solver tolerated (CONVERGED).
5. **Quality caps** aspect ratio <= 5 (6Sigma limit; measured 5.7),
   level jumps 2:1 with a one-cell buffer (`nCellsBetweenLevels 1`).
6. **Budget check** about 7,000 cells per m² and 16,000 per cabinet. A
   dcCart mesh at twice that (Base604 5.5M leaves at level 4) is
   over-refined.

## Measured against the three gate cases (np8, floor 0.3, 2026-09-22)

| case | cells | min cut volume / hex | cells < 1 mm | aspect | verdict |
|---|---|---|---|---|---|
| DAML-Testbed | 290k | 1.4e-8 (43 baffle cells at Duct_riser) | 43 | 812 | CONVERGED |
| Tri_angle | 48k | 2.1e-5 | 37 | - | CONVERGED |
| Base604 | 3.55M | 2.1e-3 | 0 | 41 | CONVERGED |

DAML still carries the thinnest cells (baffle sheets not on the frame
grid); they solve, but rule 4 is what would remove them.

## The rule in the editors (2026-09-22)

The rule lives once, in `src/frontend/shared/js/mesh_sizing.js`
(`SIXSIGMA`, `describeMeshSizing`, `sixSigmaMeshConfig`). Both editors show a
"cell-size rule (6Sigma)" group under the mesh tab: the five dcCart sizing
keys that used to be runner-only defaults (`weldTolerance`,
`firstLayerMinFraction`, `firstLayerMax`, `firstLayerSealGap`,
`rackCellMinSize`), a readout of the cell edge every surface class gets at the
current settings, and an apply button that writes the preset into the
project. All five keys are `MESH_KEYS` (cfdproj_schema.js / cfdproj_migrate.py)
and travel through `cfdrun_build.js` into `meshConfig`. Blank = the runner
default. The preset never coarsens a level the project already has.

Results of the preset applied through the legacy page (np8, dcCart, 1000
steady iterations, return-air probes above every ACU, dual verdict from
`convergence.py`):

| case | preset outcome | cells | note |
|---|---|---|---|
| Slab | CONVERGED | 755,636 | first preset run |
| Tri_angle | CONVERGED | 109,794 | after the degenerate-cell rule (below); before it 244 cells of 0..3.4e-9 hex on one column NaN'd step 1 |
| Base_leakage | CONVERGED | 686,561 | only with the never-coarsen rule (rack stays L3 = 21 mm); the 42 mm pitch diverged at iteration 21 |
| Shinaide | NOT_CONVERGED (stall) | 655,204 | T 4e-3, Ux 8e-3 flat after 1000 steps, sensors plateaued; the control with its own settings (299,406 cells) stalls at the same level (T 4.2e-3, Ux 8.7e-3), so the stall is the case, not the preset |
| Base604 | NOT_CONVERGED (sensor leg) | 7,001,967 | residuals pass (T 2.7e-4, U 4e-4..7e-4); 4 of 7 return-air probes still swing 0.3-0.9 K in the last window (the floor-0.3 run at its own settings, 3.55M cells, settled within 0.1 K). Final temperatures agree with that run within 0.5 K. Twice the budget: `maxGlobalCells` caps the octree leaves (4.1M), not the cut cells; ACU/partition/baffle [2,3] plus feature level 3 over 142 racks doubles the count. Preset NOT written into case/Base604 |

The Tri_angle column was a parallel-only artefact: the serial mesher produced
none of those cells while 8 ranks did, and the 10 cells that were below the
old 1e-9 threshold were absorbed identically on both. The rule now absorbs
everything below `degenerateCutVolumeFraction` = 1e-6 (the kill threshold) in
up to 64 rounds; the 8-rank cell count then equals serial. Face counts still
differ between serial and 8 ranks (stair-step faces dropped 3081 vs 3243,
baffle pairs 1471 vs 1450) - not bisected.

## Open

- `GridRules.csv` (the per-object rule table) is referenced by the `.room`
  files but not embedded; the 17.5/20/45 mm cabinet pattern is inferred
  from the VTU, not read from the rules.
- The VTU (4.61M cells) is finer than the saved `301 s0.room` summary
  (3.68M); the `.room` that produced it is inside the unextracted
  `room 301 final.zip`.
- The exact solver threshold between 1.4e-3 (solves) and 2.1e-5
  (Tri_angle dcCart, solved only with the floor) has not been bisected.
