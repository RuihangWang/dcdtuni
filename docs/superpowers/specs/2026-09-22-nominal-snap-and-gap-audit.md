# Nominal-box snapping and the gap audit (2026-09-22)

Status: IMPLEMENTED in `src/frontend/shared/js/transform_tools.js`, wired into
both editors. Tests: `src/frontend/react/test/snapNominal.test.ts`.

## The defect

Users dragged racks, ACUs and baffles flush against each other and the export
still carried 1–2 mm slots between them, which the cut-cell mesher turned into
sliver channels and the 1.2 mm weld had to close. The frontend snap was the
cause, in four ways found in the code:

1. **It snapped rendered bounds, not the geometry.** `getRenderRootWorldBounds`
   unioned every mesh under the model group, and a rack's group carries its
   door planes at `z = -0.001` and `z = size.z + 0.001`, plus hole proxies and
   slot lines at the same 1 mm offsets (`geometry_scene.js:1705/1721/1842/2019/2037`).
   Two rendered surfaces made flush left the NOMINAL boxes (what the STL export
   writes) 1 mm apart. The same bounds fed `isModelWithinProjectBounds`, so a
   rack could never touch the room wall it was dragged to.
2. **Rounding was conditional.** Only a drag that actually snapped rounded to
   the micrometre; drag-create drops, resizes, 2D plan drags and rotations wrote
   raw doubles, so met faces differed by float dust.
3. **The snap radius was 12 % of the average model size**: metres wide in a
   hall of racks, sub-pixel in a room of baffles.
4. **No hysteresis on the move drag**: the target was re-picked every frame.

Survey of the projects under `case/` with the new `findModelGaps` (gaps of at
most 5 mm between enabled models facing each other on one axis, float dust
excluded):

| project | gaps ≤ 5 mm | of which exactly 1.00 mm | closable |
|---|---|---|---|
| Alibaba_hangchou | 205 | ~200 (x) | all |
| GTC_2025 / GTC_2026 | 39 | 39 (z, containment baffle vs every rack front) | all |
| STDCT | 26 | 24 (z, `Baffle_1` / `Baffle_1_copy` vs every rack front); 2 × 3.46 mm (`Baffle_6` vs both PCUs) | all |
| the other 19 | 0 | | |

The 1.000 mm value is the door-plane offset, i.e. defect 1 verbatim.

## The fix

- `getModelAABB(model)`: the nominal world box from location + size + rotation
  (+ mirror). Unrotated models take the exact path (`min = location`,
  `max = location + size`) so a face coordinate here equals the exporter's.
- `computeProximitySnap` unions the moving models' nominal boxes and snaps them
  to the neighbours' nominal boxes (rotated neighbours: OBB corners). Face
  pairs need overlap on the other two axes; edge pairs do not.
- Threshold: `ctx.snapPixelWorld()` (metres per pixel at the selection, both
  editors supply it from the camera) × 10 px, clamped to [5 mm, 0.5 m]. Without
  the callback the old 12 % rule applies.
- Hysteresis: per axis a lock `{target, side}` engages inside the threshold and
  releases only when the raw box is 2.5 thresholds away (`snapLocks()` exposes
  it for guide lines).
- `quantize(v)` = round to 1e-6 m. Applied on EVERY write path: move drag
  (already), drag-create drop (both editors), every legacy drag end (resize
  included, size too), the legacy 2D plan drag, the React plan drag.
- `getRenderRootWorldBounds` skips `isDoor`, `isPartitionWallHoleProxy`,
  `isSlotBoundaryGuide`, `isOverlay` and `THREE.Line` children;
  `isModelWithinProjectBounds` uses the nominal box.
- Gap audit: `findModelGaps(models, maxGap)` and `closeModelGaps(models, maxGap)`
  (the smaller model of each pair moves onto the larger one's face, in rounds,
  once per model per round; a project the audit reports clean is not touched).
  Legacy: "检查缝隙 / 闭合缝隙" above the export buttons (undoable, logged).
  React: the export dialog shows the count and a close button.

Verified in the legacy page on a copy of STDCT: audit 26 → close 3 moves →
audit 0. Unit tests cover exact coincidence after a snap, the door-plane
exclusion, the pixel threshold and its clamps, the lock/release band, rotated
neighbours, and the audit/closer.

## Not done

- Existing projects still carry their gaps until someone presses "闭合缝隙"
  (or re-drags); the survey above says which.
- Rotation writes trig products into `location`; a 90° turn leaves 1e-17
  residue which `quantize` removes at the next drag end but not at the
  rotation itself.
