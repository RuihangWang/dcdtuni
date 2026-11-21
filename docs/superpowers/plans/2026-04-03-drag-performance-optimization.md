# Drag Performance & Selection Optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix severe drag latency on large cases (CMI-Guichou 343 models, Alibaba-Hangchou 287 models) and fix two selection bugs (single-click patch, nearest-to-camera).

**Architecture:** All changes are in the single file `STLGeneratorForOpenFoam/web/STL_generate.html`. The core approach is: (1) consolidate 12 pointermove listeners into one dispatcher that skips hover during drags, (2) gate edge hover raycasts to only the selected model's meshes, (3) add a spatial hash grid for snap computation, (4) fix merged-mesh selection to resolve patches in one click, (5) add nearest-to-camera hit picking.

**Tech Stack:** Three.js r128 (vanilla JS, no build system, single HTML file)

**Spec:** `docs/superpowers/specs/2026-04-03-performance-optimization-design.md`

---

### Task 1: Add `pickNearestHit()` function (Spec §6)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:1692` (after existing `pickPreferredHit`)

- [ ] **Step 1: Add `pickNearestHit` function after `pickPreferredHit` (line ~1710)**

Insert immediately after the closing brace of `pickPreferredHit()` (line 1710):

```javascript
    /**
     * Strict nearest-to-camera hit picking for viewport selection.
     * Priority only breaks ties among co-planar surfaces from the SAME model
     * (e.g., a patch on a wall face).  Hits from different models are never
     * grouped — the nearest model always wins.
     */
    function pickNearestHit(hits, predicate = null) {
        const filtered = (hits || []).filter(hit => !predicate || predicate(hit));
        if (!filtered.length) return null;
        const nearest = filtered[0]; // raycaster sorts by distance
        const nearestDist = nearest.distance || 0;
        const coplanarWindow = Math.max(0.005, nearestDist * 0.001);
        const nearestModelId = nearest.object.userData && nearest.object.userData.modelId;
        const candidates = filtered.filter(hit =>
            (hit.distance || 0) <= nearestDist + coplanarWindow
            && (hit.object.userData && hit.object.userData.modelId) === nearestModelId
        );
        return candidates.reduce((best, current) => {
            if (!best) return current;
            const bp = getHitPriority(best.object.userData || {});
            const cp = getHitPriority(current.object.userData || {});
            if (cp > bp) return current;
            if (cp === bp && (current.distance || 0) < (best.distance || 0)) return current;
            return best;
        }, null);
    }
```

- [ ] **Step 2: Update `handleViewportSelection` to use `pickNearestHit` instead of `pickPreferredHit`**

At line 11865, change:

```javascript
        const hit = pickPreferredHit(selectableHits) || selectableHits[0];
```

to:

```javascript
        const hit = pickNearestHit(selectableHits) || selectableHits[0];
```

- [ ] **Step 3: Verify `pickPreferredHit` is still used for hover**

Confirm that `updatePatchEdgeHover` (line 11509-11510) still uses `pickPreferredHit` — it should, since the wider depth window is appropriate for hover detection. No change needed there.

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add pickNearestHit for strict nearest-to-camera selection

Selection now always picks the model closest to the camera.
Priority only breaks ties for co-planar surfaces within the
same model (e.g. patch on wall face)."
```

---

### Task 2: Single-click patch selection on merged models (Spec §5)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:11854` (`handleViewportSelection`)

- [ ] **Step 1: Add merged-proxy resolution in `handleViewportSelection`**

After line 11866 (`const modelId = mesh.userData.modelId;`), insert a merged-proxy resolution block. The full function head should read:

```javascript
    function handleViewportSelection(event) {
        updatePointerFromEvent(event);

        const hits = raycaster.intersectObjects(renderMeshes, false);
        if (!hits.length) {
            setSelection('project');
            return;
        }
        const selectableHits = hits.filter(item => !(item.object.userData && item.object.userData.isRackPartition));
        if (!selectableHits.length) return;

        let hit = pickNearestHit(selectableHits) || selectableHits[0];
        let mesh = hit.object;
        let modelId = mesh.userData.modelId;

        // --- Merged-proxy resolution: unmerge → re-raycast → re-merge ---
        if (mesh.userData.isMergedProxy && modelId) {
            const modelRoot = getRenderRootByModelId(modelId);
            if (modelRoot && modelRoot.userData._origChildren) {
                _unmergeModelGroup(modelRoot);
                // Re-raycast against now-unmerged individual meshes
                const detailedHits = raycaster.intersectObjects(renderMeshes, false)
                    .filter(h => {
                        const d = h.object.userData;
                        return d && d.modelId === modelId && !d.isRackPartition;
                    });
                const detailedHit = pickNearestHit(detailedHits);
                _mergeModelGroup(modelRoot);
                if (detailedHit) {
                    hit = detailedHit;
                    mesh = detailedHit.object;
                    // modelId unchanged — same model
                }
            }
        }
```

Note: the existing `const hit = ...` and `const mesh = ...` change to `let` so they can be reassigned by the merged-proxy block.

- [ ] **Step 2: Verify the `_unmergeModelGroup` / `_mergeModelGroup` cycle is safe**

Read `_unmergeModelGroup` (line 6301) — it restores `_origChildren` meshes into the scene and removes proxy meshes. `_mergeModelGroup` (line 6199) does the reverse. The re-raycast happens between unmerge and re-merge, hitting the restored original meshes which have full `userData` (refIds, faceId, isBaseWall, etc.). After re-merge, the original meshes are removed again and proxies restored — the scene state is identical to before.

One subtlety: after `_mergeModelGroup`, the `detailedHit.object` (an original mesh) is no longer in the scene, but we only use its `.userData` for the selection dispatch — we never access its geometry or position. This is safe.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "fix: single-click patch selection on merged models

When clicking a merged proxy mesh, temporarily unmerge the model,
re-raycast to resolve the click to a specific patch/slot/face,
then re-merge. Patches, rack slots, and regions are now selectable
with a single click instead of requiring a double-click."
```

---

### Task 3: Consolidated pointermove dispatcher (Spec §1)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:12254-12265` (event listener registration)

- [ ] **Step 1: Add the `runHoverChecks` function and `onPointerMove` dispatcher**

Insert the following two functions just before the event listener registration block (before line 12219). Find a suitable location — e.g., after `endSeedSurfaceDrag` (around line 12215):

```javascript
    // --- Consolidated pointermove dispatcher ---
    let _hoverRafQueued = false;
    let _lastHoverEvent = null;

    function runHoverChecks(event) {
        _lastHoverEvent = event;
        if (_hoverRafQueued) return;
        _hoverRafQueued = true;
        requestAnimationFrame(() => {
            _hoverRafQueued = false;
            const e = _lastHoverEvent;
            if (!e) return;
            const tab = state.dctwinConfig && state.dctwinConfig.activeTab;
            if (tab === 'mesh') {
                _rzHoverCheck(e);
                updateBlockMeshHover(e);
            } else if (tab === 'postprocess') {
                _ppAiHoverCheck(e);
            } else {
                updatePatchEdgeHover(e);
            }
        });
    }

    function onPointerMove(event) {
        // During any active drag, ONLY run the relevant drag updater
        if (dragState) {
            updatePatchDrag(event);
            requestRender();
            return;
        }
        if (seedDragState) {
            updateSeedSurfaceDrag(event);
            requestRender();
            return;
        }
        if (locInMeshDragState) {
            updateLocInMeshDrag(event);
            requestRender();
            return;
        }
        if (extraLocDragState) {
            updateExtraLocDrag(event);
            requestRender();
            return;
        }
        if (bmDragState) {
            updateBlockMeshDrag(event);
            requestRender();
            return;
        }
        if (rzDragState) {
            updateRefinementZoneDrag(event);
            requestRender();
            return;
        }
        if (ppAiDragState) {
            updatePPAIBoxDrag(event);
            requestRender();
            return;
        }

        // No drag active — run hover checks (rAF-coalesced)
        runHoverChecks(event);
        requestRender();
    }
```

- [ ] **Step 2: Replace the 12 individual pointermove listeners with the single dispatcher**

Replace lines 12254-12265:

```javascript
    renderer.domElement.addEventListener('pointermove', updateSeedSurfaceDrag);
    renderer.domElement.addEventListener('pointermove', updatePatchDrag);
    renderer.domElement.addEventListener('pointermove', updatePatchEdgeHover);
    renderer.domElement.addEventListener('pointermove', updateLocInMeshDrag);
    renderer.domElement.addEventListener('pointermove', updateExtraLocDrag);
    renderer.domElement.addEventListener('pointermove', updateBlockMeshHover);
    renderer.domElement.addEventListener('pointermove', updateBlockMeshDrag);
    renderer.domElement.addEventListener('pointermove', _rzHoverCheck);
    renderer.domElement.addEventListener('pointermove', updateRefinementZoneDrag);
    renderer.domElement.addEventListener('pointermove', _ppAiHoverCheck);
    renderer.domElement.addEventListener('pointermove', updatePPAIBoxDrag);
    renderer.domElement.addEventListener('pointermove', () => { if (window.requestRender) window.requestRender(); });
```

with:

```javascript
    renderer.domElement.addEventListener('pointermove', onPointerMove);
```

- [ ] **Step 3: Verify `updatePatchDrag` handles both model-move and patch-drag modes**

`updatePatchDrag` (line 11133) is the existing handler that dispatches by `dragState.mode` — it handles `modelMove`, `wallResize`, `boxFace`, `boxCircleResize`, `boxRectResize`, and custom patch drags. By calling it when `dragState` is set, all drag modes are covered by the single dispatcher.

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: consolidate 12 pointermove listeners into single dispatcher

During active drags, only the relevant drag handler runs — all hover
raycasts (updatePatchEdgeHover, _rzHoverCheck, _ppAiHoverCheck, etc.)
are completely skipped. When idle, hover checks are coalesced via rAF
so at most one runs per frame."
```

---

### Task 4: Selection-gated edge hover (Spec §2)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:11504` (`updatePatchEdgeHover`)

- [ ] **Step 1: Add selection gate and scoped raycast to `updatePatchEdgeHover`**

Replace the top of `updatePatchEdgeHover` (lines 11504-11510):

```javascript
    function updatePatchEdgeHover(event) {
        if (dragState || panelResizeState) return;
        if (state.dctwinConfig && state.dctwinConfig._uiFrozen) return;
        if (state.dctwinConfig && state.dctwinConfig.activeTab !== 'geometry') return;
        updatePointerFromEvent(event);
        const hits = raycaster.intersectObjects(renderMeshes, false);
        const topHit = pickPreferredHit(hits);
```

with:

```javascript
    function updatePatchEdgeHover(event) {
        if (dragState || panelResizeState) return;
        if (state.dctwinConfig && state.dctwinConfig._uiFrozen) return;
        if (state.dctwinConfig && state.dctwinConfig.activeTab !== 'geometry') return;

        // Only check edge hover on the currently selected model
        const selModel = state.selection.modelId ? getModelById(state.selection.modelId) : null;
        if (!selModel) { clearHoveredPatchEdge(); return; }

        // Raycast only the selected model's meshes (5-20 vs 76K)
        const selRoot = getRenderRootByModelId(selModel.id);
        if (!selRoot) { clearHoveredPatchEdge(); return; }
        const selMeshes = [];
        selRoot.traverse(child => { if (child.isMesh) selMeshes.push(child); });
        if (!selMeshes.length) { clearHoveredPatchEdge(); return; }

        updatePointerFromEvent(event);
        const hits = raycaster.intersectObjects(selMeshes, false);
        const topHit = pickPreferredHit(hits);
```

The rest of the function body (from line 11511 onward) remains unchanged.

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: gate edge hover raycast to selected model only

updatePatchEdgeHover now only raycasts against the selected model's
meshes (5-20) instead of the entire renderMeshes array (76K).
When nothing is selected, hover detection is skipped entirely.
This also fixes the UX: resize cursors only appear on the selected
model, preventing accidental resize of unselected objects."
```

---

### Task 5: Snap cache — avgSize + spatial hash grid (Spec §3a, §3b, §4b)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:9064-9138` (snap system)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:11079` (beginModelMoveDrag — init cache)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:11020` (wallResize dragState — init cache)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:11477` (endPatchDrag — clear cache)

- [ ] **Step 1: Add snap cache variables and init/clear/query functions**

Replace the snap throttle variables and `computeProximitySnap` function (lines 9064-9138) with:

```javascript
    // --- Snap system: throttle + spatial cache ---
    let _lastSnapResult = { x: 0, y: 0, z: 0 };
    let _lastSnapTime = 0;
    const _SNAP_THROTTLE_MS = 80;

    // Cached on drag-start, cleared on drag-end
    let _snapAvgSize = null;
    let _snapThreshold = null;
    let _snapGrid = null;
    const _SNAP_CELL_SIZE = 2.0; // meters
    const _snapScratchVec = new THREE.Vector3();

    function initSnapCache() {
        const allSizes = [];
        renderRoots.forEach(root => {
            const mid = root.userData && root.userData.modelId;
            if (!mid || !isModelEnabled(mid)) return;
            const wb = getRenderRootWorldBounds(root);
            if (wb) {
                const sz = wb.getSize(_snapScratchVec);
                allSizes.push(Math.max(sz.x, sz.y, sz.z));
            }
        });
        _snapAvgSize = allSizes.length ? allSizes.reduce((a, b) => a + b, 0) / allSizes.length : 1;
        _snapThreshold = _snapAvgSize * 0.08;

        // Build 2D spatial hash grid (XY plane)
        _snapGrid = new Map();
        renderRoots.forEach(root => {
            const mid = root.userData && root.userData.modelId;
            if (!mid || !isModelEnabled(mid)) return;
            const wb = getRenderRootWorldBounds(root);
            if (!wb) return;
            const minCx = Math.floor(wb.min.x / _SNAP_CELL_SIZE);
            const minCy = Math.floor(wb.min.y / _SNAP_CELL_SIZE);
            const maxCx = Math.floor(wb.max.x / _SNAP_CELL_SIZE);
            const maxCy = Math.floor(wb.max.y / _SNAP_CELL_SIZE);
            for (let cx = minCx; cx <= maxCx; cx++) {
                for (let cy = minCy; cy <= maxCy; cy++) {
                    const key = cx + ',' + cy;
                    if (!_snapGrid.has(key)) _snapGrid.set(key, []);
                    _snapGrid.get(key).push({ id: mid, bounds: wb });
                }
            }
        });
    }

    function clearSnapCache() {
        _snapAvgSize = null;
        _snapThreshold = null;
        _snapGrid = null;
    }

    /** Query snap grid for models near the given Box3, expanded by margin. */
    function querySnapCandidates(box, margin) {
        if (!_snapGrid) return [];
        const minCx = Math.floor((box.min.x - margin) / _SNAP_CELL_SIZE);
        const minCy = Math.floor((box.min.y - margin) / _SNAP_CELL_SIZE);
        const maxCx = Math.floor((box.max.x + margin) / _SNAP_CELL_SIZE);
        const maxCy = Math.floor((box.max.y + margin) / _SNAP_CELL_SIZE);
        const seen = new Set();
        const results = [];
        for (let cx = minCx; cx <= maxCx; cx++) {
            for (let cy = minCy; cy <= maxCy; cy++) {
                const entries = _snapGrid.get(cx + ',' + cy);
                if (!entries) continue;
                for (let i = 0; i < entries.length; i++) {
                    if (!seen.has(entries[i].id)) {
                        seen.add(entries[i].id);
                        results.push(entries[i]);
                    }
                }
            }
        }
        return results;
    }

    function computeProximitySnap(movingIds, includeFace, includeEdge) {
        const now = performance.now();
        if (now - _lastSnapTime < _SNAP_THROTTLE_MS) return _lastSnapResult;

        const movingIdSet = new Set(movingIds);
        const movingBounds = [];
        movingIds.forEach(id => {
            const wb = getRenderRootWorldBounds(getRenderRootByModelId(id));
            if (wb) movingBounds.push(wb);
        });
        if (!movingBounds.length) return { x: 0, y: 0, z: 0 };

        const combined = movingBounds[0].clone();
        movingBounds.forEach(b => combined.union(b));

        // Use cached threshold if available, otherwise compute on the fly
        const threshold = _snapThreshold != null ? _snapThreshold : (() => {
            const allSizes = [];
            renderRoots.forEach(root => {
                const mid = root.userData && root.userData.modelId;
                if (!mid || !isModelEnabled(mid)) return;
                const wb = getRenderRootWorldBounds(root);
                if (wb) allSizes.push(Math.max(...wb.getSize(_snapScratchVec).toArray()));
            });
            const avg = allSizes.length ? allSizes.reduce((a, b) => a + b, 0) / allSizes.length : 1;
            return avg * 0.08;
        })();

        let snapX = 0, snapY = 0, snapZ = 0;
        let bestDistX = threshold, bestDistY = threshold, bestDistZ = threshold;

        // Use spatial grid if available, otherwise fall back to full iteration
        const candidates = _snapGrid
            ? querySnapCandidates(combined, threshold)
            : null;

        const iterateTarget = candidates || renderRoots;

        const processEntry = (otherId, ob) => {
            if (!otherId || movingIdSet.has(otherId) || !isModelEnabled(otherId)) return;
            if (!ob) return;

            const pairs = [];
            if (includeFace) {
                pairs.push(
                    { dist: Math.abs(combined.max.x - ob.min.x), snap: ob.min.x - combined.max.x, axis: 'x' },
                    { dist: Math.abs(combined.min.x - ob.max.x), snap: ob.max.x - combined.min.x, axis: 'x' },
                    { dist: Math.abs(combined.max.y - ob.min.y), snap: ob.min.y - combined.max.y, axis: 'y' },
                    { dist: Math.abs(combined.min.y - ob.max.y), snap: ob.max.y - combined.min.y, axis: 'y' },
                    { dist: Math.abs(combined.max.z - ob.min.z), snap: ob.min.z - combined.max.z, axis: 'z' },
                    { dist: Math.abs(combined.min.z - ob.max.z), snap: ob.max.z - combined.min.z, axis: 'z' }
                );
            }
            if (includeEdge) {
                pairs.push(
                    { dist: Math.abs(combined.min.x - ob.min.x), snap: ob.min.x - combined.min.x, axis: 'x' },
                    { dist: Math.abs(combined.max.x - ob.max.x), snap: ob.max.x - combined.max.x, axis: 'x' },
                    { dist: Math.abs(combined.min.y - ob.min.y), snap: ob.min.y - combined.min.y, axis: 'y' },
                    { dist: Math.abs(combined.max.y - ob.max.y), snap: ob.max.y - combined.max.y, axis: 'y' },
                    { dist: Math.abs(combined.min.z - ob.min.z), snap: ob.min.z - combined.min.z, axis: 'z' },
                    { dist: Math.abs(combined.max.z - ob.max.z), snap: ob.max.z - combined.max.z, axis: 'z' }
                );
            }
            for (let i = 0; i < pairs.length; i++) {
                const p = pairs[i];
                if (p.axis === 'x' && p.dist < bestDistX) { bestDistX = p.dist; snapX = p.snap; }
                if (p.axis === 'y' && p.dist < bestDistY) { bestDistY = p.dist; snapY = p.snap; }
                if (p.axis === 'z' && p.dist < bestDistZ) { bestDistZ = p.dist; snapZ = p.snap; }
            }
        };

        if (candidates) {
            // Spatial grid path: entries have { id, bounds }
            for (let i = 0; i < candidates.length; i++) {
                processEntry(candidates[i].id, candidates[i].bounds);
            }
        } else {
            // Fallback: full iteration
            renderRoots.forEach(root => {
                const otherId = root.userData && root.userData.modelId;
                const ob = getRenderRootWorldBounds(root);
                processEntry(otherId, ob);
            });
        }

        _lastSnapResult = { x: snapX, y: snapY, z: snapZ };
        _lastSnapTime = now;
        return _lastSnapResult;
    }
```

- [ ] **Step 2: Call `initSnapCache()` in drag-start handlers**

In `beginModelMoveDrag` (line ~11079), after `pushUndoCheckpoint('modelMoveDrag');` add:

```javascript
        initSnapCache();
```

In the wall-resize drag start (line ~11020, where `dragState = { mode: 'wallResize', ... }` is set), add after `if (controls) controls.enabled = false;` (line ~11036):

```javascript
                initSnapCache();
```

- [ ] **Step 3: Call `clearSnapCache()` in `endPatchDrag`**

In `endPatchDrag` (line ~11477), after `dragState = null;` (line 11489) add:

```javascript
        clearSnapCache();
```

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: spatial hash grid for snap computation

Build a 2D spatial grid on drag-start and cache avgSize/threshold.
computeProximitySnap now queries only nearby cells instead of
iterating all 2000+ renderRoots. Falls back to full iteration
when cache is not initialized (non-drag snap calls)."
```

---

### Task 6: Replace inline wall/floor resize snap with shared `querySnapCandidates` (Spec §3c)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:11225-11297` (inline resize snap in `updatePatchDrag`)

- [ ] **Step 1: Replace the inline resize snap code**

Replace lines 11225-11297 (the entire `// Snap moving edge to nearby model edges during resize (throttled)` block inside the `if (dragState.mode === 'wallResize')` branch) with:

```javascript
            // Snap moving edge to nearby model edges during resize (throttled)
            if ((state.snapFace || state.snapEdge) && (performance.now() - _lastSnapTime >= _SNAP_THROTTLE_MS)) {
                const wr = dragState.worldRight;
                const wu = dragState.worldUp;
                const dragDir = (edge === 'right' || edge === 'left') ? wr : wu;
                const dimKey = (edge === 'right' || edge === 'left') ? wType : hType;
                const anchorPos = new THREE.Vector3(model.anchor.x, model.anchor.y, model.anchor.z);
                const movingEdgeProj = (edge === 'right' || edge === 'top')
                    ? anchorPos.dot(dragDir) + model.base[dimKey]
                    : anchorPos.dot(dragDir);

                const threshold = _snapThreshold != null ? _snapThreshold : (() => {
                    const sizes = [];
                    renderRoots.forEach(root => {
                        const mid = root.userData && root.userData.modelId;
                        if (!mid || !isModelEnabled(mid)) return;
                        const wb = getRenderRootWorldBounds(root);
                        if (wb) sizes.push(Math.max(...wb.getSize(_snapScratchVec).toArray()));
                    });
                    return (sizes.length ? sizes.reduce((a, b) => a + b, 0) / sizes.length : 1) * 0.08;
                })();

                let bestSnap = 0;
                let bestDist = threshold;

                // Use spatial grid if available, otherwise full iteration
                const modelBounds = getRenderRootWorldBounds(getRenderRootByModelId(model.id));
                const snapEntries = (_snapGrid && modelBounds)
                    ? querySnapCandidates(modelBounds, threshold)
                    : null;

                const checkEntry = (otherId, ob) => {
                    if (!otherId || otherId === model.id || !isModelEnabled(otherId)) return;
                    if (!ob) return;
                    let oMin = Infinity, oMax = -Infinity;
                    for (let ix = 0; ix < 2; ix++) {
                        for (let iy = 0; iy < 2; iy++) {
                            for (let iz = 0; iz < 2; iz++) {
                                const p = (ix ? ob.max.x : ob.min.x) * dragDir.x
                                        + (iy ? ob.max.y : ob.min.y) * dragDir.y
                                        + (iz ? ob.max.z : ob.min.z) * dragDir.z;
                                if (p < oMin) oMin = p;
                                if (p > oMax) oMax = p;
                            }
                        }
                    }
                    [oMin, oMax].forEach(target => {
                        if (typeof dragState.initEdgeProj === 'number' && Math.abs(target - dragState.initEdgeProj) < 1e-3) return;
                        const dist = Math.abs(movingEdgeProj - target);
                        if (dist < bestDist) {
                            bestDist = dist;
                            bestSnap = target - movingEdgeProj;
                        }
                    });
                };

                if (snapEntries) {
                    for (let i = 0; i < snapEntries.length; i++) {
                        checkEntry(snapEntries[i].id, snapEntries[i].bounds);
                    }
                } else {
                    renderRoots.forEach(root => {
                        const otherId = root.userData && root.userData.modelId;
                        checkEntry(otherId, getRenderRootWorldBounds(root));
                    });
                }

                _lastSnapTime = performance.now();

                if (Math.abs(bestSnap) > 1e-8) {
                    if (edge === 'right' || edge === 'top') {
                        model.base[dimKey] = Math.max(MIN_DIM, model.base[dimKey] + bestSnap);
                    } else {
                        const newDim = Math.max(MIN_DIM, model.base[dimKey] - bestSnap);
                        const dimDelta = model.base[dimKey] - newDim;
                        model.base[dimKey] = newDim;
                        const dir = (edge === 'left') ? wr : wu;
                        model.anchor.x += dimDelta * dir.x;
                        model.anchor.y += dimDelta * dir.y;
                        model.anchor.z += dimDelta * dir.z;
                    }
                    if (model.kind === 'baffle') model.base.D = 0;
                    if (model.kind === 'floor') model.base.H = 0;
                }
            }
```

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: use spatial hash grid for wall/floor resize snap

Replace inline iterate-all-renderRoots resize snap with the shared
querySnapCandidates spatial grid, reusing the cache built on drag-start.
Falls back to full iteration when cache is unavailable."
```

---

### Task 7: Seed surface helper — transform during drag, not rebuild (Spec §4a)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:12144` (`updateSeedSurfaceDrag`)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:5883` (`updateSeedSurfaceHelper`)

- [ ] **Step 1: Add a lightweight `repositionSeedSurfaceHelper` function**

Insert after `updateSeedSurfaceHelper` (after line 5969):

```javascript
    /**
     * Lightweight update: reposition existing seed surface helper geometry
     * without destroying and recreating it. Used during drag for performance.
     * Falls back to full rebuild if helper structure doesn't match expectations.
     */
    function repositionSeedSurfaceHelper() {
        const surface = getCurrentEditableSeedSurfaceContext();
        if (!surface || !seedSurfaceHelper) {
            updateSeedSurfaceHelper();
            return;
        }
        const { anchor, positive, negative } = getSeedEndpoints(surface);

        // Update hub mesh position (the anchor sphere)
        // Update tip mesh positions (positive/negative spheres)
        // Update arrow helpers (lines + cones)
        let meshIdx = 0;
        let arrowIdx = 0;
        seedSurfaceHelper.children.forEach(child => {
            if (child.isArrowHelper) {
                const target = arrowIdx === 0 ? negative : positive;
                const direction = target.clone().sub(anchor);
                const length = Math.max(direction.length(), 1e-6);
                child.position.copy(anchor);
                child.setDirection(direction.normalize());
                child.setLength(length, Math.min(length * 0.4, child.cone.geometry.parameters?.height || 0.18), child.cone.scale.x);
                arrowIdx++;
            }
        });
        seedSurfaceHelperMeshes.forEach((mesh, i) => {
            if (mesh.userData.seedSide === 'negative') mesh.position.copy(negative);
            else if (mesh.userData.seedSide === 'positive') mesh.position.copy(positive);
            else if (mesh.userData.seedSide === 'anchor') mesh.position.copy(anchor);
        });

        if (window.requestRender) window.requestRender();
    }
```

- [ ] **Step 2: Use `repositionSeedSurfaceHelper` during drag**

In `updateSeedSurfaceDrag` (line 12144), replace the two calls to `updateSeedSurfaceHelper()` at lines 12180 and 12198 with `repositionSeedSurfaceHelper()`:

Line 12180 — change:
```javascript
            updateSeedSurfaceHelper();
```
to:
```javascript
            repositionSeedSurfaceHelper();
```

Line 12198 — same change:
```javascript
            updateSeedSurfaceHelper();
```
to:
```javascript
            repositionSeedSurfaceHelper();
```

The `endSeedSurfaceDrag` function (line 12202) already calls `updateUI()` which triggers a full rebuild on drag end — so the helper gets a clean rebuild after dragging.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: reposition seed helper during drag instead of rebuilding

During seed surface drag, update existing ArrowHelper and sphere
positions instead of destroying and recreating all geometry on
every pointermove. Full rebuild still happens on drag end."
```

---

### Task 8: Manual testing on large cases

- [ ] **Step 1: Start the server**

```bash
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir dctwin/case &
```

- [ ] **Step 2: Load CMI-Guichou and test drag performance**

Open `http://localhost:8005/STL_generate.html` in browser. Load `CMI-Guichou.cfdproj` from the projects dropdown. Then test:

1. Select a baffle → enable face snap → drag its edge to resize → verify smooth tracking
2. Select a wall → drag edge to resize → verify smooth
3. Move mouse over viewport with nothing selected → verify no resize cursor appears
4. Drag-move a model with snap enabled → verify snap works correctly

- [ ] **Step 3: Test selection fixes**

1. Click directly on a patch of a non-selected wall → verify patch is selected in one click
2. Click on a rack slot of a non-selected rack → verify single-click selection
3. Place a baffle in front of a rack → click the baffle → verify baffle (not rack) is selected
4. Click overlapping co-planar patches on same wall → verify higher-priority patch wins
5. Ctrl+click to multi-select → verify still works

- [ ] **Step 4: Load Alibaba-Hangchou and repeat key tests**

Load `Alibaba-Hangchou.cfdproj`. Repeat the drag performance and selection tests above. Verify no regressions.

- [ ] **Step 5: Final commit if any fixes were needed**

If any issues were found and fixed during testing, commit the fixes.
