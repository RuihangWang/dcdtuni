# Phase 3 Performance Optimization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce draw calls from 19,064 to ~2,000 and boost rotation FPS from 8.7 to 20+ by per-model frustum culling and per-model geometry merging for non-selected models.

**Architecture:** Two complementary optimizations: (1) Group-level frustum culling — check 287 bounding boxes per frame, skip invisible model groups entirely (0 cost per hidden model). (2) Per-model mesh merging — after building each non-selected model's geometry, merge all its child meshes by shared material into merged geometries, reducing 64 meshes/rack to ~5-8. Selected model keeps full detail for interaction.

**Tech Stack:** Three.js r128, `THREE.BufferGeometryUtils.mergeBufferGeometries()`, `THREE.Frustum`, `THREE.Box3`

**Baseline (Phase 2 after):**
- Models: 287 | Meshes: 13,162 | Draw calls: 19,064
- Rotation: 115 ms/frame, 8.7 FPS
- Rack breakdown: 200 racks × 64 meshes = 12,849 (98% of draw calls)
- Per-rack: ~11 unique materials → mergeable to ~11 draw calls

---

### Task 1: Group-Level Frustum Culling

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — render loop (`_doRender`) and build pipeline

**Problem:** Three.js frustum-culls each of the 13,162 meshes individually. Checking at the model GROUP level (287 checks) and setting `group.visible = false` skips all children instantly.

- [ ] **Step 1: Add frustum culling to the render loop**

Find `function _doRender()` (search for this exact string). It currently looks like:

```javascript
    function _doRender() {
        _renderPending = false;
        if (_cameraTransitionActive) updateCameraTransition(performance.now());
        _updateGridPosition();
        if (renderer) {
            renderer.setScissorTest(false);
            renderer.setViewport(0, 0, viewer.clientWidth, viewer.clientHeight);
            renderer.clear();
            renderer.render(scene, camera);
        }
```

Add frustum culling BEFORE `renderer.render(scene, camera)`:

```javascript
    const _frustum = new THREE.Frustum();
    const _projScreenMatrix = new THREE.Matrix4();

    function _doRender() {
        _renderPending = false;
        if (_cameraTransitionActive) updateCameraTransition(performance.now());
        _updateGridPosition();
        if (renderer) {
            // Group-level frustum culling: hide model groups outside camera view
            _projScreenMatrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
            _frustum.setFromProjectionMatrix(_projScreenMatrix);
            const _selId = state.selection.modelId;
            renderRoots.forEach(root => {
                if (!root.userData || !root.userData.modelId) return;
                // Always show selected model (for interaction)
                if (root.userData.modelId === _selId) { root.visible = true; return; }
                // Compute world-space bounding box for this model group
                if (!root.userData._bbox) root.userData._bbox = new THREE.Box3();
                root.userData._bbox.setFromObject(root);
                root.visible = _frustum.intersectsBox(root.userData._bbox);
            });

            renderer.setScissorTest(false);
            renderer.setViewport(0, 0, viewer.clientWidth, viewer.clientHeight);
            renderer.clear();
            renderer.render(scene, camera);

            // Restore visibility after render (so raycasting still works on all models)
            renderRoots.forEach(root => { root.visible = true; });
        }
```

**Key design:** Visibility is restored AFTER render so raycasting (which runs on `pointerup`, not during render) sees all models.

- [ ] **Step 2: Test with benchmark**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 5
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK'; })()"
browser-use eval "$(cat STLGeneratorForOpenFoam/web/benchmark.js)"
```

Expected: Rotation FPS should improve since not all 287 models are visible from every angle. The improvement depends on camera angle — looking at 50% of the scene should halve draw calls.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: add group-level frustum culling for model groups"
```

---

### Task 2: Per-Model Geometry Merging for Non-Selected Models

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — add merge logic in `buildGeometryData` and `rebuildSingleModel`

**Problem:** Each rack has ~64 individual meshes but only ~11 unique materials. Merging meshes that share a material reduces draw calls per rack from 64 to ~11.

**Approach:** After a model group's geometry is built, if the model is NOT currently selected, merge its child meshes by material into merged geometries. Store the original individual meshes in `userData._origChildren` so they can be restored when the model is selected (needed for per-face/per-slot raycasting and highlighting).

- [ ] **Step 1: Add the `_mergeModelGroup` helper function**

Add this function near `rebuildSingleModel` (after line ~5160):

```javascript
    // Merge child meshes of a model group by material to reduce draw calls.
    // Stores original children in userData._origChildren for later restoration.
    function _mergeModelGroup(modelGroup) {
        const meshes = [];
        modelGroup.traverse(child => {
            if (child.isMesh && child !== modelGroup) meshes.push(child);
        });
        if (meshes.length <= 2) return; // Not worth merging

        // Group meshes by material uuid
        const byMaterial = new Map();
        meshes.forEach(mesh => {
            const key = mesh.material.uuid;
            if (!byMaterial.has(key)) byMaterial.set(key, { material: mesh.material, meshes: [] });
            byMaterial.get(key).meshes.push(mesh);
        });

        // Only merge if it actually reduces mesh count
        if (byMaterial.size >= meshes.length) return;

        // Save originals for restoration
        const origChildren = [];
        meshes.forEach(mesh => {
            origChildren.push({ mesh, parent: mesh.parent });
        });
        modelGroup.userData._origChildren = origChildren;
        modelGroup.userData._origRenderMeshes = meshes.slice();

        // Remove originals from scene and renderMeshes
        meshes.forEach(mesh => {
            if (mesh.parent) mesh.parent.remove(mesh);
            const idx = renderMeshes.indexOf(mesh);
            if (idx !== -1) renderMeshes.splice(idx, 1);
        });

        // Create merged meshes per material group
        const mergedMeshes = [];
        byMaterial.forEach(({ material, meshes: groupMeshes }) => {
            if (groupMeshes.length === 1) {
                // Single mesh — just re-add it
                modelGroup.add(groupMeshes[0]);
                renderMeshes.push(groupMeshes[0]);
                mergedMeshes.push(groupMeshes[0]);
                return;
            }
            // Transform geometries to model-group local space and merge
            const geomsToMerge = [];
            groupMeshes.forEach(mesh => {
                const cloned = mesh.geometry.clone();
                cloned.applyMatrix4(mesh.matrixWorld);
                geomsToMerge.push(cloned);
            });
            try {
                const merged = THREE.BufferGeometryUtils.mergeBufferGeometries(geomsToMerge, false);
                if (merged) {
                    // Undo the model group's world transform since the merged geo is in world space
                    const invMatrix = new THREE.Matrix4().copy(modelGroup.matrixWorld).invert();
                    merged.applyMatrix4(invMatrix);
                    const mergedMesh = new THREE.Mesh(merged, material);
                    mergedMesh.userData = { isMergedProxy: true, modelId: modelGroup.userData.modelId };
                    modelGroup.add(mergedMesh);
                    renderMeshes.push(mergedMesh);
                    mergedMeshes.push(mergedMesh);
                }
            } catch (e) {
                // If merge fails (incompatible attributes), fall back to individual meshes
                groupMeshes.forEach(mesh => {
                    modelGroup.add(mesh);
                    renderMeshes.push(mesh);
                });
            }
            // Dispose cloned geometries (merged result keeps its own data)
            geomsToMerge.forEach(g => g.dispose());
        });
        modelGroup.userData._mergedMeshes = mergedMeshes;
    }

    // Restore original individual meshes (for selection/highlighting)
    function _unmergeModelGroup(modelGroup) {
        if (!modelGroup.userData._origChildren) return;
        // Remove merged proxies
        (modelGroup.userData._mergedMeshes || []).forEach(mesh => {
            if (mesh.userData.isMergedProxy) {
                if (mesh.parent) mesh.parent.remove(mesh);
                const idx = renderMeshes.indexOf(mesh);
                if (idx !== -1) renderMeshes.splice(idx, 1);
                mesh.geometry.dispose();
            }
        });
        // Restore originals
        modelGroup.userData._origChildren.forEach(({ mesh }) => {
            modelGroup.add(mesh);
            renderMeshes.push(mesh);
        });
        modelGroup.userData._origChildren = null;
        modelGroup.userData._mergedMeshes = null;
        modelGroup.userData._origRenderMeshes = null;
    }
```

- [ ] **Step 2: Apply merging in `buildGeometryData`**

In `buildGeometryData()`, after the `state.models.forEach(...)` loop (just before the `wrapperGroup.updateMatrixWorld(true);` line), add:

```javascript
        // Merge non-selected model groups to reduce draw calls
        const _selModelId = state.selection.modelId;
        renderRoots.forEach(root => {
            if (root.userData.modelId !== _selModelId) {
                root.updateMatrixWorld(true);
                _mergeModelGroup(root);
            }
        });
```

- [ ] **Step 3: Unmerge on selection change**

In `rebuildSingleModel(modelId)`, the function already destroys and recreates the model group. But when the PREVIOUSLY selected model is no longer selected, it needs to be merged. And the NEWLY selected model needs to be unmerged.

The simplest approach: in `setSelection()`, after changing selection, rebuild the old and new models:

Find `function setSelection(` and look at its body. At the end, it calls `updateUI()` and `updateHighlights()`. BEFORE `updateUI()`, add logic to handle merge/unmerge:

```javascript
        // Merge/unmerge for draw call optimization
        const prevModelId = _prevSelectionModelId;
        const newModelId = state.selection.modelId;
        _prevSelectionModelId = newModelId;
        if (prevModelId && prevModelId !== newModelId) {
            // Previously selected model → merge it (reduce draw calls)
            const prevRoot = getRenderRootByModelId(prevModelId);
            if (prevRoot && !prevRoot.userData._origChildren) {
                prevRoot.updateMatrixWorld(true);
                _mergeModelGroup(prevRoot);
            }
        }
        if (newModelId && newModelId !== prevModelId) {
            // Newly selected model → unmerge it (restore individual meshes for interaction)
            const newRoot = getRenderRootByModelId(newModelId);
            if (newRoot && newRoot.userData._origChildren) {
                _unmergeModelGroup(newRoot);
            }
        }
```

Also add `let _prevSelectionModelId = null;` near the top of the script (near other state variables).

- [ ] **Step 4: Test with benchmark**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 5
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK'; })()"
browser-use eval "$(cat STLGeneratorForOpenFoam/web/benchmark.js)"
# Also check draw call reduction
browser-use eval "(() => { window.__three.renderer.render(window.__three.scene, window.__three.camera); return JSON.stringify(window.__three.renderer.info.render); })()"
```

Expected: Draw calls should drop from ~19,064 to ~2,000-3,000 (200 racks × 11 materials + other models). Rotation FPS should reach 15-25+.

- [ ] **Step 5: Verify selection still works**

```bash
# Select a rack and verify individual meshes are restored
browser-use eval "(() => { const rack = state.models.find(m => m.kind === 'rack'); setSelection('model', rack.id); const root = window.__three.globalGroup.children.find(g => g.userData && g.userData.modelId === rack.id); return 'merged=' + !!root.userData._origChildren + ' meshes=' + root.children.filter(c => c.isMesh).length; })()"
```

Expected: `merged=false` (unmerged for selection), meshes should be the full count (~19 direct children).

- [ ] **Step 6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: merge non-selected model meshes by material to reduce draw calls"
```

---

### Task 3: Final Phase 3 Benchmark

- [ ] **Step 1: Run complete benchmark**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 5
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK'; })()"
browser-use eval "$(cat STLGeneratorForOpenFoam/web/benchmark.js)" | tee docs/superpowers/plans/phase3-perf.json
```

- [ ] **Step 2: Print full comparison table**

Expected:
| Metric | Original | Phase 1 | Phase 2 | Phase 3 (Target) |
|--------|---------|---------|---------|-------------------|
| Meshes | 30,362 | 13,162 | 13,162 | ~2,000-3,000 (merged) |
| Draw calls | 36,264 | 19,064 | 19,064 | ~2,000-3,000 |
| Rotation FPS | 3.9 | 8.0 | 8.7 | **20+** |
| Single edit | 800ms | 800ms | 8-25ms | 8-25ms |

- [ ] **Step 3: Screenshot and commit**

```bash
browser-use screenshot /tmp/phase3_optimized.png
git add docs/superpowers/plans/phase3-perf.json
git commit -m "perf: Phase 3 complete — benchmark results"
```
