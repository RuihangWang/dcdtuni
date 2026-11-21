# DAML thin-wall wedge — upstream weld/snap work package

**Date:** 2026-09-18
**Status:** open work package (follow-up to commit `90806050`)

> **Resolution (2026-09-21, host 40902).** Route ② (weld) is the fix and is now
> the runner DEFAULT: `dcCartMeshDict` gets `weldTolerance 0.0012;` (manifest
> `meshConfig.weldTolerance`, 0 turns it off). Weld OFF reproduces §1 exactly
> (min wall distance 1.30e-4 m, `k` collapse at step 20, 474/500 zero-iteration
> `k` steps, limitNut 11 %). Weld ON (17 planes, 274 vertices moved) converges:
> 1500 steady steps, Ux/p_rgh/T/k/ε down 3.1/2.5/3.0/3.0/2.6 orders, cumulative
> continuity 1.3e-4, sensors 289.81/289.90 K drifting < 0.01 K per 20 steps.
> Note the mechanism in §2 is not what OpenFOAM v2412 computes with the shipped
> BCs: `epsilonWallFunction` without `lowReCorrection` evaluates only the
> log-law branch, so the 2kν/y² term is never executed — the collapse is the
> poisoned pressure matrix of the wedge cells, which the weld removes.
> Red line re-passed with the weld: DAML np1 ≡ np4 on coverage lines and on
> the merged geometry (points, faces, cells identical).
**Scope:** `dcCartMesh` cut-cell pipeline and/or the DAML-Testbed source surfaces; whichever route is chosen must re-pass the any-np ≡ serial red line.

## 1. Symptom and evidence

DAML-Testbed (`case/DAML-Testbed`, rack-mounted GPU boxes riding a slab at a
slight tilt / near-coincident junction) meshes cleanly at every np after
`90806050`, but the steady 500-step solve never converges:

- `k` collapses 0.32 → 3.2e-5 (step ~20) → 3.46e-7 with "No Iterations 0";
  `Ux` oscillates 0.31–0.56 and `p_rgh` 0.18–0.50 with no decay over 500 steps.
- Cumulative continuity error grows −2.4 → −21 → −45.8: mass is not
  conserved, i.e. the p_rgh matrix is poisoned (the MeshGenerator
  "lowWeight / lowVolRatio" signature).
- Return-air sensors stay pinned at room temperature (flow never develops);
  sensor2 tail drift 0.057 K per 20 steps against a 0.01 K criterion.
- `checkMesh` reports Mesh OK; the mesh is topologically sound.

Evidence persisted in this repo: `assets/daml-thin-wedge/` next to this file
(`SUMMARY.txt`, `residuals.txt`, `comparison.json`, `compare.py`,
`scan_slivers.py`). Solve logs also live under
`case/DAML-Testbed/log/<timestamp>_DAML-Testbed/`.

## 2. Root cause (located, not yet fixed)

The collapse driver is a **thin wall wedge whose nearest wall distance is
y_P = 1.30e-4 m**. Healthy dcCart meshes floor at y_P ≈ 5.0e-4 m; below that,
`epsilonWallFunction`'s `eps = 2 k nu / y^2` blows up and the k-equation
degenerates. The wedge is carved where a near-coincident / slightly tilted
junction between two solids intersects the leaf grid.

Why the merge machinery cannot cure it (all verified in WP-V6):

- It *is* a band-absorb candidate (`t = V/A_iface < h_leaf/8`), but its union
  with **every** conformal neighbour is **concave**, so the mandatory
  `foamOut` gate refuses (a concave cell would fail checkMesh's face-pyramid
  test — the gate is not negotiable).
- It is too voluminous for the sliver kill pass.
- `repairThinWallCells` (`minWallCellDistance 4e-4`) is OFF and documented as
  "not a win" on this case: 75 stranded cells, skew 3.97 → 4.81,
  Mesh OK → Failed (`dcCartCutCells.H:162-179`).

Conclusion: the defect is created **upstream of merging**, at the carve of the
near-coincident tilted junction. Fixing it means changing what surfaces the
carver sees, or welding the junction before carve.

## 3. Candidate routes (pick one per attempt, in this order)

1. **PTWELD-style junction welding in the carver.** Detect solid-solid
   junctions whose surfaces are within a weld tolerance (candidate:
   0.2–0.5 × h_leaf) and locally replace the pair by a single planar
   interface before cutting, so no wedge thinner than the y_P floor can be
   carved. Reference: MeshGenerator's PTWELD pass if present, else implement
   against the shell BVH. Must be a pure function of the global STL data so
   every rank welds identically (red-line precondition).
2. **Surface snapping of near-coincident tilted faces.** Snap the tilted
   face onto the coincident plane when the dihedral and offset are below
   tolerance, removing the sliver-generating intersection curve. Riskier for
   watertightness; needs the same rank-invariance argument.
3. **Gated `repairThinWallCells` revival.** Re-enable with stricter admission
   (only wedges whose every neighbour union is concave *and* whose y_P is
   below the floor), accepting a local skew increase bounded by a new gate;
   the previous "not a win" verdict was for the ungated version.

Route 1 is preferred: it removes the wedge instead of repairing it, and keeps
checkMesh admission untouched.

## 4. Acceptance criteria

- y_P floor of the DAML mesh ≥ 5.0e-4 m (report the histogram tail).
- Faces < 1e-6 m² in single digits (currently 61 after band absorb).
- Steady 500: p_rgh/U/T/k/epsilon decay ≥ 2 orders smoothly, no collapse;
  return-sensor tail drift < 0.01 K per 20 steps; cumulative continuity error
  bounded (no monotone growth).
- Red line re-pass on DAML: np1 ≡ np4 (sorted point cloud max |Δ| = 0,
  counts equal, face-vertex multiset identical) and coverage-gate numbers
  identical across np.
- No regression on Tri_angle / Base604 / Slab / Shinaide decision-level
  behaviour (refusals stay refusals, acceptances stay acceptances, same
  numbers at every np).

## 5. Out of scope

- Base604's refused raised-floor sheet: a refinement-ceiling config issue
  (`meshConfig.maxGlobalCells` caps the level the sheet needs to tile); fix
  by raising the cap or adding local level-4 refinement over that sheet.
- Tri_angle's diagonal wall: under-resolved at its shipped refinement level
  (`refinementLevel [1,1]`); now refused consistently at every np, which is
  the gate working. Fix in the case config, not the mesher.
- The frontend feature work of 2026-09-17/18 (stacked servers, opacity
  split, adaptive helpers) — unrelated, already committed.
