# OBB-Based Interactions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix collision detection, edge snap, and auto-cutout for rotated models by replacing AABB with OBB calculations.

**Architecture:** Add `getModelOBB(model)` and `getOBBCorners(obb)` core functions. Replace `_analyticalCollisionCheck` with SAT. Replace AABB min/max in snap and auto-cutout with OBB corner projections. All changes in single file `STLGeneratorForOpenFoam/web/STL_generate.html`.

**Tech Stack:** Three.js r128 (Quaternion, Vector3), polygon-clipping library

**Spec:** `docs/superpowers/specs/2026-04-04-obb-interactions-design.md`

---

### Task 1: OBB core functions — getModelOBB + getOBBCorners

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add `getModelOBB` and `getOBBCorners` after `_analyticalCollisionCheck` (~line 4101)**

Insert after the closing `}` of `_analyticalCollisionCheck`:

```javascript
    /**
     * Compute Oriented Bounding Box from model anchor, base dims, and transform quaternion.
     * Returns { center: Vector3, half: [halfW, halfD, halfH], axes: [axisX, axisY, axisZ] }
     * For axis-aligned models (identity quaternion), OBB degrades to AABB.
     */
    function getModelOBB(model) {
        const t = model.transform || {};
        const q = t.quaternion || { x: 0, y: 0, z: 0, w: 1 };
        const quat = new THREE.Quaternion(q.x, q.y, q.z, q.w).normalize();
        const halfW = model.base.W / 2, halfD = model.base.D / 2, halfH = model.base.H / 2;
        const localCenter = new THREE.Vector3(halfW, halfD, halfH);
        const anchor = new THREE.Vector3(model.anchor.x, model.anchor.y, model.anchor.z);
        const center = localCenter.applyQuaternion(quat).add(anchor);
        const axisX = new THREE.Vector3(1, 0, 0).applyQuaternion(quat);
        const axisY = new THREE.Vector3(0, 1, 0).applyQuaternion(quat);
        const axisZ = new THREE.Vector3(0, 0, 1).applyQuaternion(quat);
        return { center, half: [halfW, halfD, halfH], axes: [axisX, axisY, axisZ] };
    }

    /** Return the 8 world-space corner vertices of an OBB. */
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

    /**
     * SAT (Separating Axis Theorem) overlap test for two OBBs.
     * Returns true if the OBBs overlap by more than eps on ALL separating axes.
     * Returns false if a separating axis exists (no collision).
     */
    function obbOverlapSAT(a, b, eps) {
        // Helper: project OBB onto axis, return half-projection length
        function projectOBB(obb, axis) {
            return Math.abs(obb.axes[0].dot(axis)) * obb.half[0]
                 + Math.abs(obb.axes[1].dot(axis)) * obb.half[1]
                 + Math.abs(obb.axes[2].dot(axis)) * obb.half[2];
        }
        const d = b.center.clone().sub(a.center);
        // Test 15 separating axes
        const testAxes = [
            // 3 axes from A
            a.axes[0], a.axes[1], a.axes[2],
            // 3 axes from B
            b.axes[0], b.axes[1], b.axes[2]
        ];
        // 9 cross products
        for (let i = 0; i < 3; i++) {
            for (let j = 0; j < 3; j++) {
                const cross = new THREE.Vector3().crossVectors(a.axes[i], b.axes[j]);
                if (cross.lengthSq() > 1e-10) testAxes.push(cross.normalize());
            }
        }
        for (let i = 0; i < testAxes.length; i++) {
            const axis = testAxes[i];
            const projA = projectOBB(a, axis);
            const projB = projectOBB(b, axis);
            const dist = Math.abs(d.dot(axis));
            const overlap = projA + projB - dist;
            if (overlap <= eps) return false; // separating axis found
        }
        return true; // all axes overlap → collision
    }
```

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(obb): add getModelOBB, getOBBCorners, obbOverlapSAT core functions

OBB computed from model anchor + base + transform quaternion.
SAT tests 15 axes (3+3+9 cross products) with epsilon tolerance.
Degrades to AABB for axis-aligned models (identity quaternion)."
```

---

### Task 2: Replace collision detection with SAT

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Replace `_analyticalCollisionCheck` body (~line 4077)**

Replace the entire function body with OBB-SAT:

```javascript
    function _analyticalCollisionCheck(movingModels, movingIds) {
        const movingIdSet = new Set(movingIds);
        const eps = 1e-4;
        const movingOBBs = movingModels.map(m => getModelOBB(m));
        for (let i = 0; i < state.models.length; i++) {
            const other = state.models[i];
            if (movingIdSet.has(other.id) || !isModelEnabled(other)) continue;
            const otherOBB = getModelOBB(other);
            for (let j = 0; j < movingOBBs.length; j++) {
                if (obbOverlapSAT(movingOBBs[j], otherOBB, eps)) return true;
            }
        }
        return false;
    }
```

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(obb): replace AABB collision detection with OBB-SAT

_analyticalCollisionCheck now uses SAT with OBBs. Correctly detects
collision for rotated models. Face-touching (overlap <= eps) allowed."
```

---

### Task 3: Edge snap with OBB corner projections

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Modify `computeProximitySnap` to use OBB corners (~line 9740)**

Find the section inside `computeProximitySnap` where `processEntry` is defined. It currently takes `(otherId, ob)` where `ob` is an AABB Box3. Change it to compute OBB corner projections:

Replace the `processEntry` function (from `const processEntry = (otherId, ob) => {` to its closing `};`) with:

```javascript
        const processEntry = (otherId, ob, model) => {
            if (!otherId || movingIdSet.has(otherId) || !isModelEnabled(otherId)) return;
            if (!ob) return;

            // Use OBB corners for accurate projection of rotated models
            let oMinX, oMaxX, oMinY, oMaxY, oMinZ, oMaxZ;
            const otherModel = model || getModelById(otherId);
            const hasRotation = otherModel && otherModel.transform && otherModel.transform.quaternion
                && (Math.abs(otherModel.transform.quaternion.x) > 1e-6
                    || Math.abs(otherModel.transform.quaternion.y) > 1e-6
                    || Math.abs(otherModel.transform.quaternion.z) > 1e-6);

            if (hasRotation) {
                const obb = getModelOBB(otherModel);
                const corners = getOBBCorners(obb);
                oMinX = Infinity; oMaxX = -Infinity;
                oMinY = Infinity; oMaxY = -Infinity;
                oMinZ = Infinity; oMaxZ = -Infinity;
                corners.forEach(c => {
                    if (c.x < oMinX) oMinX = c.x; if (c.x > oMaxX) oMaxX = c.x;
                    if (c.y < oMinY) oMinY = c.y; if (c.y > oMaxY) oMaxY = c.y;
                    if (c.z < oMinZ) oMinZ = c.z; if (c.z > oMaxZ) oMaxZ = c.z;
                });
            } else {
                oMinX = ob.min.x; oMaxX = ob.max.x;
                oMinY = ob.min.y; oMaxY = ob.max.y;
                oMinZ = ob.min.z; oMaxZ = ob.max.z;
            }

            const pairs = [];
            if (includeFace && !hasRotation) {
                // Face snap: only for axis-aligned models
                pairs.push(
                    { dist: Math.abs(combined.max.x - oMinX), snap: oMinX - combined.max.x, axis: 'x' },
                    { dist: Math.abs(combined.min.x - oMaxX), snap: oMaxX - combined.min.x, axis: 'x' },
                    { dist: Math.abs(combined.max.y - oMinY), snap: oMinY - combined.max.y, axis: 'y' },
                    { dist: Math.abs(combined.min.y - oMaxY), snap: oMaxY - combined.min.y, axis: 'y' },
                    { dist: Math.abs(combined.max.z - oMinZ), snap: oMinZ - combined.max.z, axis: 'z' },
                    { dist: Math.abs(combined.min.z - oMaxZ), snap: oMaxZ - combined.min.z, axis: 'z' }
                );
            }
            if (includeEdge) {
                // Edge snap: works for ALL models (rotated uses OBB projected min/max)
                pairs.push(
                    { dist: Math.abs(combined.min.x - oMinX), snap: oMinX - combined.min.x, axis: 'x' },
                    { dist: Math.abs(combined.max.x - oMaxX), snap: oMaxX - combined.max.x, axis: 'x' },
                    { dist: Math.abs(combined.min.y - oMinY), snap: oMinY - combined.min.y, axis: 'y' },
                    { dist: Math.abs(combined.max.y - oMaxY), snap: oMaxY - combined.max.y, axis: 'y' },
                    { dist: Math.abs(combined.min.z - oMinZ), snap: oMinZ - combined.min.z, axis: 'z' },
                    { dist: Math.abs(combined.max.z - oMaxZ), snap: oMaxZ - combined.max.z, axis: 'z' }
                );
            }
            for (let i = 0; i < pairs.length; i++) {
                const p = pairs[i];
                if (p.axis === 'x' && p.dist < bestDistX) { bestDistX = p.dist; snapX = p.snap; }
                if (p.axis === 'y' && p.dist < bestDistY) { bestDistY = p.dist; snapY = p.snap; }
                if (p.axis === 'z' && p.dist < bestDistZ) { bestDistZ = p.dist; snapZ = p.snap; }
            }
        };
```

Then update the callers. The spatial grid path currently calls `processEntry(candidates[i].id, candidates[i].bounds)`. Add the model as third arg:

```javascript
        if (candidates) {
            for (let i = 0; i < candidates.length; i++) {
                processEntry(candidates[i].id, candidates[i].bounds, getModelById(candidates[i].id));
            }
        } else {
            renderRoots.forEach(root => {
                const otherId = root.userData && root.userData.modelId;
                const ob = getRenderRootWorldBounds(root);
                processEntry(otherId, ob, getModelById(otherId));
            });
        }
```

- [ ] **Step 2: Apply same OBB projection to wall resize snap**

Find the `checkEntry` function inside the wallResize snap section (~line 12108). Replace the AABB 8-corner projection with OBB corners:

```javascript
                const checkEntry = (otherId, ob) => {
                    if (!otherId || otherId === model.id || !isModelEnabled(otherId)) return;
                    if (!ob) return;

                    // Use OBB corners for rotated models
                    const otherModel = getModelById(otherId);
                    let corners;
                    const hasRot = otherModel && otherModel.transform && otherModel.transform.quaternion
                        && (Math.abs(otherModel.transform.quaternion.x) > 1e-6
                            || Math.abs(otherModel.transform.quaternion.y) > 1e-6
                            || Math.abs(otherModel.transform.quaternion.z) > 1e-6);
                    if (hasRot) {
                        corners = getOBBCorners(getModelOBB(otherModel));
                    } else {
                        corners = [];
                        for (let ix = 0; ix < 2; ix++) {
                            for (let iy = 0; iy < 2; iy++) {
                                for (let iz = 0; iz < 2; iz++) {
                                    corners.push(new THREE.Vector3(
                                        ix ? ob.max.x : ob.min.x,
                                        iy ? ob.max.y : ob.min.y,
                                        iz ? ob.max.z : ob.min.z));
                                }
                            }
                        }
                    }

                    let oMin = Infinity, oMax = -Infinity;
                    corners.forEach(c => {
                        const p = c.x * dragDir.x + c.y * dragDir.y + c.z * dragDir.z;
                        if (p < oMin) oMin = p;
                        if (p > oMax) oMax = p;
                    });

                    [oMin, oMax].forEach(target => {
                        if (typeof dragState.initEdgeProj === 'number' && Math.abs(target - dragState.initEdgeProj) < 1e-3) return;
                        const dist = Math.abs(movingEdgeProj - target);
                        if (dist < bestDist) {
                            bestDist = dist;
                            bestSnap = target - movingEdgeProj;
                        }
                    });
                };
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(obb): edge snap uses OBB corner projections for rotated models

computeProximitySnap: face snap disabled for rotated models, edge snap
uses OBB-projected min/max instead of AABB. Wall resize snap also
uses OBB corners. Axis-aligned models unchanged (fast AABB path)."
```

---

### Task 4: Auto-cutout with OBB corners

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Replace AABB corners with OBB corners in `autoWallBoolDiff` (~line 8647)**

Find the two `for (let ix = 0; ix < 2; ix++)` loops in `autoWallBoolDiff`. The first checks plane distance, the second does 2D projection. Replace both with OBB corner computation:

Find the block starting with:
```javascript
            // Check if other model's bounding box crosses the wall plane
```

Replace the plane distance check AND the 2D projection (both loops) up to the `wall.cutouts.push` line with:

```javascript
            // Use OBB corners for accurate projection of rotated models
            const otherOBB = getModelOBB(other);
            const otherCorners = getOBBCorners(otherOBB);

            // Check if other model straddles the wall plane
            let dMin = Infinity, dMax = -Infinity;
            otherCorners.forEach(corner => {
                const d = corner.clone().sub(faceOrigin).dot(faceNormal);
                if (d < dMin) dMin = d;
                if (d > dMax) dMax = d;
            });
            if (dMin > 0.01 || dMax < -0.01) return;

            // Project OBB corners onto wall face 2D space
            const corners2D = [];
            otherCorners.forEach(wp => {
                const lp = wp.clone().applyMatrix4(faceWorldInv);
                corners2D.push([lp.x, lp.y]);
            });
```

The rest of the function (minX/maxX/minY/maxY computation, clipping, cutout push) remains unchanged.

- [ ] **Step 2: Replace AABB corners with OBB corners in `autoFloorBoolDiff` (~line 8571)**

Find the `for (let ix = 0; ix < 2; ix++)` loop in `autoFloorBoolDiff`. Replace with:

```javascript
            // Use OBB corners for accurate projection of rotated models
            const otherOBB = getModelOBB(other);
            const otherCorners = getOBBCorners(otherOBB);

            const corners2D = [];
            otherCorners.forEach(wp => {
                const lp = wp.clone().applyMatrix4(faceWorldInv);
                corners2D.push([lp.x, lp.y]);
            });
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(obb): auto-cutout uses OBB corners for rotated models

autoWallBoolDiff and autoFloorBoolDiff now project OBB corners
instead of AABB corners. Rotated models produce accurate cutout
rectangles matching their actual footprint on the wall/floor plane."
```

---

### Task 5: Manual testing

- [ ] **Step 1: Test axis-aligned regression**

Place two axis-aligned racks side by side → enable face snap → verify they snap flush. Try to push one into the other → verify collision prevents overlap. This should work identically to before.

- [ ] **Step 2: Test rotated model collision**

Rotate one rack 45° → drag it toward the other rack → verify SAT collision prevents overlap.

- [ ] **Step 3: Test rotated model edge snap**

Enable edge snap → drag a rotated rack near an axis-aligned wall → verify edges align.

- [ ] **Step 4: Test auto-cutout with Tri_angle.cfdproj**

Load `projects/Tri_angle.cfdproj` → select bottom wall → click auto-cutout → verify diagonal walls' footprints are small rectangles, NOT the full wall width. The bottom wall should NOT be entirely deleted.

- [ ] **Step 5: Performance check**

Load CMI-Guichou → drag a model with snap enabled → verify no perceptible lag (SAT is O(15) arithmetic per pair, same order as AABB).
