# P2: OBB-Based Interactions — Design Spec

**Date**: 2026-04-04
**File**: `STLGeneratorForOpenFoam/web/STL_generate.html`
**Goal**: Fix collision detection, edge snap, and auto-cutout for rotated models by replacing AABB with OBB (Oriented Bounding Box) calculations.

---

## 1. OBB Core Computation

Add `getModelOBB(model)` function that computes an OBB from model anchor, base dimensions, and transform quaternion:

```javascript
function getModelOBB(model) {
    const t = model.transform || {};
    const q = t.quaternion || {x:0, y:0, z:0, w:1};
    const quat = new THREE.Quaternion(q.x, q.y, q.z, q.w).normalize();
    const halfW = model.base.W / 2, halfD = model.base.D / 2, halfH = model.base.H / 2;
    // Anchor is the model origin (corner), center = anchor + half-extents rotated
    const localCenter = new THREE.Vector3(halfW, halfD, halfH);
    const anchor = new THREE.Vector3(model.anchor.x, model.anchor.y, model.anchor.z);
    const center = localCenter.applyQuaternion(quat).add(anchor);
    const axisX = new THREE.Vector3(1,0,0).applyQuaternion(quat);
    const axisY = new THREE.Vector3(0,1,0).applyQuaternion(quat);
    const axisZ = new THREE.Vector3(0,0,1).applyQuaternion(quat);
    return { center, half: [halfW, halfD, halfH], axes: [axisX, axisY, axisZ] };
}
```

`getOBBCorners(obb)` returns the 8 world-space corner vertices:

```javascript
function getOBBCorners(obb) {
    const corners = [];
    for (let sx = -1; sx <= 1; sx += 2) {
        for (let sy = -1; sy <= 1; sy += 2) {
            for (let sz = -1; sz <= 1; sz += 2) {
                corners.push(obb.center.clone()
                    .addScaledVector(obb.axes[0], sx * obb.half[0])
                    .addScaledVector(obb.axes[1], sy * obb.half[1])
                    .addScaledVector(obb.axes[2], sz * obb.half[2]));
            }
        }
    }
    return corners;
}
```

For axis-aligned models (identity quaternion), OBB degrades to AABB — same results as before.

---

## 2. Collision Detection — SAT

Replace `_analyticalCollisionCheck` with SAT (Separating Axis Theorem) using OBBs.

**SAT algorithm**: Two OBBs do NOT collide if there exists a separating axis on which their projections don't overlap. Test 15 candidate axes:
- 3 axes from OBB_A
- 3 axes from OBB_B  
- 9 cross products (A_axis_i × B_axis_j)

For each axis, project both OBBs' half-extents and check overlap.

**Epsilon tolerance**: `eps = 1e-4`. Overlap ≤ eps → allowed (face touching). Overlap > eps → collision.

**Performance**: Pure arithmetic (dot products), no geometry traversal. O(N × 15) per moving model vs all static models — same asymptotic cost as current AABB check.

**Fallback**: For identity-quaternion models, SAT with axis-aligned axes produces the same result as the current AABB check.

---

## 3. Edge Snap — OBB Projection

Current `computeProximitySnap` compares AABB `min/max` on each axis. This fails for rotated models.

**Fix**: For each candidate model, compute OBB corners (8 vertices), project all 8 onto each snap axis, and use the min/max projections as "edge positions".

```javascript
// Current (AABB):
const ob = getRenderRootWorldBounds(root);
// Compare: combined.max.x ↔ ob.min.x (face snap), combined.min.x ↔ ob.min.x (edge snap)

// New (OBB):
const obb = getModelOBB(other);
const corners = getOBBCorners(obb);
let oMinX = Infinity, oMaxX = -Infinity;
let oMinY = Infinity, oMaxY = -Infinity;
let oMinZ = Infinity, oMaxZ = -Infinity;
corners.forEach(c => {
    if (c.x < oMinX) oMinX = c.x; if (c.x > oMaxX) oMaxX = c.x;
    if (c.y < oMinY) oMinY = c.y; if (c.y > oMaxY) oMaxY = c.y;
    if (c.z < oMinZ) oMinZ = c.z; if (c.z > oMaxZ) oMaxZ = c.z;
});
// Then use oMinX/oMaxX/oMinY/oMaxY/oMinZ/oMaxZ in existing snap pair logic
```

**Face snap**: Disabled for rotated models (face-to-face alignment only makes sense for axis-aligned surfaces). Specifically: if the other model has a non-identity quaternion, skip face snap pairs for it, only include edge snap pairs.

**Edge snap**: Always enabled. For rotated models, the axis-projected min/max of OBB corners gives the correct "edge position" along each axis.

**Spatial hash grid**: The grid uses AABB for cell assignment. For rotated models, the AABB of the OBB (encompassing all 8 corners) is used for grid insertion. This means rotated models may occupy more grid cells than necessary, but correctness is preserved.

---

## 4. Auto-Cutout — OBB Corners

Current `autoWallBoolDiff` and `autoFloorBoolDiff` use `getRenderRootWorldBounds` (AABB) to:
1. Check if a model straddles the wall plane (project 8 AABB corners onto wall normal)
2. Project 8 AABB corners onto wall face 2D space to compute cutout rectangle

**Fix**: Replace AABB corners with OBB corners:

```javascript
// Current:
for (ix 0..1, iy 0..1, iz 0..1) {
    corner = (ix ? ob.max.x : ob.min.x, iy ? ob.max.y : ob.min.y, iz ? ob.max.z : ob.min.z)
}

// New:
const obb = getModelOBB(other);
const corners = getOBBCorners(obb);
// Use corners directly for plane distance check and 2D projection
```

The rest of the cutout logic (2D bounding box → polygon difference) remains unchanged.

---

## 5. Spatial Hash Grid Update

`initSnapCache` builds the grid using `getRenderRootWorldBounds` (AABB from geometry traverse). For rotated models, the AABB from the OBB covers the actual footprint correctly.

**Change**: In `initSnapCache`, also store the OBB alongside bounds:

```javascript
_snapGrid.get(key).push({ id: mid, bounds: wb, obb: getModelOBB(model) });
```

Then `computeProximitySnap` and resize snap use `entry.obb` for corner projection instead of `entry.bounds.min/max`.

---

## Non-Goals

- Face-to-face snap between rotated surfaces (too complex, deferred)
- Wall miter/bevel at corners (moved to P3 — needs topology from 2D editor)
- OBB visualization/debug overlay

---

## Testing

1. Place two axis-aligned racks → snap and collision should work exactly as before (regression test)
2. Rotate one rack 45° → collision detection should prevent overlap with the other rack
3. Rotate a rack 45° near a wall → edge snap should align the rack's projected edge to the wall edge
4. Auto-cutout on a wall with a rotated rack crossing it → cutout should match the rack's actual footprint, not its AABB
5. Load Tri_angle.cfdproj → auto-cutout on bottom wall should NOT delete the entire wall (diagonal walls' OBBs are smaller than their AABBs)
6. Performance: drag a model in CMI-Guichou → SAT collision check should be smooth (no perceptible lag vs current AABB)
