# Phase 2 Performance Optimization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce `buildGeometryData` from 786ms to <50ms for single-model edits via incremental per-model rebuild; reduce rotation render time from 125ms to <80ms via frustum-culling-friendly scene structure.

**Architecture:** Add `rebuildSingleModel(modelId)` that disposes and recreates only ONE model's group instead of all 287. Update all single-model-editing callsites to use it. Add a per-model bounding box cache for faster frustum culling. Benchmark after each change.

**Tech Stack:** Three.js r128, vanilla JS, browser-use CLI for benchmarking

**Baseline (Phase 1 after):**
- Models: 287 | Meshes: 13,162
- `buildGeometryData()`: 786 ms
- Rotation: 125 ms/frame, 8 FPS

---

### Task 1: Add `rebuildSingleModel(modelId)` function

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — add new function near `buildGeometryData` (~line 5026)

This is the core function. It removes ONE model's render group from the scene, disposes it, recreates just that model's geometry, and re-inserts it. The `renderMeshes` and `renderHelpers` arrays are patched (old meshes removed, new ones added) rather than rebuilt from scratch.

- [ ] **Step 1: Implement `rebuildSingleModel`**

Add this function right after `buildGeometryData` (after line 5068 `window.buildGeometryData = buildGeometryData;`):

```javascript
    // Incremental rebuild: recreate only ONE model's geometry.
    // ~3ms per model vs ~786ms for full rebuild on Alibaba (287 models).
    function rebuildSingleModel(modelId) {
        const model = getModelById(modelId);
        if (!model || !isModelEnabled(model)) return;

        // 1. Find and remove old render root
        const oldRoot = getRenderRootByModelId(modelId);
        if (oldRoot) {
            // Remove old meshes/helpers from tracking arrays
            oldRoot.traverse(child => {
                const mi = renderMeshes.indexOf(child);
                if (mi !== -1) renderMeshes.splice(mi, 1);
                const hi = renderHelpers.indexOf(child);
                if (hi !== -1) renderHelpers.splice(hi, 1);
            });
            const ri = renderRoots.indexOf(oldRoot);
            if (ri !== -1) renderRoots.splice(ri, 1);
            if (oldRoot.parent) oldRoot.parent.remove(oldRoot);
            disposeRenderObject(oldRoot);
        }

        // 2. Create new render group (same logic as buildGeometryData's inner loop)
        const modelGroup = new THREE.Group();
        modelGroup.position.set(model.anchor.x, model.anchor.y, model.anchor.z);
        modelGroup.userData = { modelId: model.id };
        applyModelTransformToObject(modelGroup, model);
        globalGroup.add(modelGroup);
        renderRoots.push(modelGroup);

        if (webglAvailable) {
            const anchorMarker = createAnchorMarkerForModel(model);
            modelGroup.add(anchorMarker);
            renderHelpers.push(anchorMarker);
        }

        const faceLayouts = getFaceLayouts(model);
        const facesIslands = buildFaceIslands(model);
        if (model.kind === 'custom' && geometryCache.has(model.id)) renderCustomModel(model, modelGroup, faceLayouts);
        else if (model.kind === 'rack') renderRackModel(model, modelGroup, faceLayouts);
        else if (model.kind === 'wall') renderWallModel(model, modelGroup, faceLayouts);
        else if (model.kind === 'baffle') renderBaffleModel(model, modelGroup, faceLayouts);
        else if (model.kind === 'floor') renderFloorModel(model, modelGroup, faceLayouts);
        else {
            renderBoxModel(model, modelGroup, faceLayouts, facesIslands);
            if (webglAvailable && model.kind === 'acu') createWallBaffleEdgeHelpers(modelGroup, model, faceLayouts);
        }

        // 3. Update world matrices and highlights
        modelGroup.updateMatrixWorld(true);
        _prevHighlightKey = '';
        renderScene();
        if (window.requestRender) window.requestRender();
    }
    window.rebuildSingleModel = rebuildSingleModel;
```

- [ ] **Step 2: Test with benchmark — measure single-model rebuild time**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 4
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK ' + state.models.length; })()"
# Measure single-model rebuild
browser-use eval "(() => { const rack = state.models.find(m => m.kind === 'rack'); const t0 = performance.now(); rebuildSingleModel(rack.id); return 'Single model rebuild: ' + (performance.now()-t0).toFixed(1) + 'ms'; })()"
# Compare with full rebuild
browser-use eval "(() => { const t0 = performance.now(); buildGeometryData(); return 'Full rebuild: ' + (performance.now()-t0).toFixed(1) + 'ms'; })()"
```

Expected: single model ~3-10ms vs full rebuild ~786ms.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: add rebuildSingleModel() for incremental geometry updates"
```

---

### Task 2: Add `doRebuildSingleModel` and `rebuildGeometryForSingleModel` wrappers

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — add wrappers near existing rebuild functions (~line 5076)

These mirror the existing `doRebuildGeometry` / `rebuildGeometry` / `rebuildGeometryForDrag` pattern but for single-model rebuilds.

- [ ] **Step 1: Add wrapper functions**

After the `rebuildSingleModel` function, add:

```javascript
    // Like doRebuildGeometry but for one model (no updateUI)
    function doRebuildSingleModel(modelId) {
        rebuildSingleModel(modelId);
    }

    // Like rebuildGeometry but for one model (with updateUI)
    function rebuildGeometrySingle(modelId) {
        rebuildSingleModel(modelId);
        updateUI();
        if (window.requestRender) window.requestRender();
    }

    // Drag-optimized single-model rebuild (rAF throttled, no updateUI)
    function rebuildSingleModelForDrag(modelId) {
        if (_dragRebuildQueued) return;
        _dragRebuildQueued = true;
        requestAnimationFrame(() => {
            _dragRebuildQueued = false;
            rebuildSingleModel(modelId);
        });
    }
```

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: add single-model rebuild wrapper functions"
```

---

### Task 3: Convert drag operations to use single-model rebuild

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `updatePatchDrag` function (~line 9557)

Currently `updatePatchDrag` calls `rebuildGeometryForDrag()` (full rebuild of all 287 models) on every drag frame. Since drags only affect ONE model, use the single-model variant.

- [ ] **Step 1: Update wallResize drag to use single-model rebuild**

Find in `updatePatchDrag`, the `wallResize` mode section (around line 9603). It ends with:
```javascript
            rebuildGeometryForDrag();
            return;
```

Replace with:
```javascript
            rebuildSingleModelForDrag(dragState.modelId);
            return;
```

- [ ] **Step 2: Update patch drag to use single-model rebuild**

Find at the end of `updatePatchDrag` (around line 9897):
```javascript
        enforceSafety(context.model, context.faceId, context.patch);
        rebuildGeometryForDrag();
```

Replace with:
```javascript
        enforceSafety(context.model, context.faceId, context.patch);
        rebuildSingleModelForDrag(dragState.modelId);
```

- [ ] **Step 3: Test drag performance**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 4
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK'; })()"
# Simulate single-model drag rebuild timing
browser-use eval "(() => { const model = state.models.find(m => m.kind === 'baffle'); const times = []; for (let i = 0; i < 10; i++) { const t = performance.now(); rebuildSingleModel(model.id); times.push(performance.now() - t); } const avg = times.reduce((a,b)=>a+b,0)/times.length; return 'Baffle drag rebuild: avg=' + avg.toFixed(1) + 'ms (was ~786ms full)'; })()"
```

Expected: ~2-5ms per drag frame (was ~786ms).

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: use single-model rebuild for drag operations"
```

---

### Task 4: Convert single-model editing functions to use `rebuildGeometrySingle`

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — multiple `window.*` functions

Many editing functions modify only ONE model but call `rebuildGeometry()` (full rebuild). Convert these to `rebuildGeometrySingle(modelId)`.

**Target functions** (each modifies one model and has access to `modelId`):

- [ ] **Step 1: Convert model property edits**

Find and replace `rebuildGeometry()` with `rebuildGeometrySingle(modelId)` in these functions. Each function already has `modelId` as a parameter or can derive it:

1. `updateModel(modelId, key, value)` — search for `window.updateModel`
2. `updateModelName(modelId, value)` — search for `window.updateModelName`
3. `updateRackModel(modelId, key, value)` — search for `window.updateRackModel`
4. `updateRackSetting(modelId, key, value)` — search for `window.updateRackSetting`
5. `toggleFacePrefix(modelId, faceId, enabled)` — search for `window.toggleFacePrefix`
6. `updateWallName(modelId, faceId, wallIndex, value)` — search for `window.updateWallName`

For each function, find the `rebuildGeometry();` call and replace with `rebuildGeometrySingle(modelId);`.

**Important:** Functions that modify MULTIPLE models or change the model list (add/delete/reorder) must KEEP `rebuildGeometry()`. These include: `removeModel`, `createDefault*Model`, `duplicateModel`, `loadProject`, `globalTransform`, and boolean operations.

- [ ] **Step 2: Convert rack editing functions**

Same replacement pattern for rack-specific functions that take `modelId`:

1. `createRackEquipmentAtSlot`
2. `createRackEquipmentFromSelection`
3. `removeRackSelectionFromEquipment`
4. `setRackSlotDeleted`
5. `toggleVoidEdgePartition`
6. `setRackSelectionDeleted`
7. `dissolveRackLeakageGroup`
8. `restoreRackOppositeForSlot`
9. `syncDeleteOppositeForSelected`
10. `updateRackThroughSlotSigma`
11. `updateRackEquipment`
12. `deleteRackEquipment`
13. `updateRackPartition`

Each has `rebuildGeometry()` — replace with `rebuildGeometrySingle(modelId)` where `modelId` is available.

- [ ] **Step 3: Convert patch/CFD editing functions**

1. `addPatch(modelId, faceId, type)` → `rebuildGeometrySingle(modelId)`
2. `updatePatch()` — gets modelId from `getSelectedPatchContext().model.id` → `rebuildGeometrySingle(context.model.id)`
3. `deletePatch()` — similar
4. `deletePatchWithCavity()` — similar
5. `updateModelCfd(modelId, ...)` → `rebuildGeometrySingle(modelId)`

- [ ] **Step 4: Test with benchmark**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 4
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK'; })()"
# Test: modify a rack and measure time
browser-use eval "(() => { const rack = state.models.find(m => m.kind === 'rack'); const t0 = performance.now(); rack.name = 'test_rename'; rebuildGeometrySingle(rack.id); return 'Single edit+rebuild: ' + (performance.now()-t0).toFixed(1) + 'ms'; })()"
```

Expected: ~3-10ms (was ~786ms for full rebuild).

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: convert single-model edits to use incremental rebuild"
```

---

### Task 5: Final Phase 2 Benchmark

- [ ] **Step 1: Run full benchmark**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 4
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK'; })()"
browser-use eval "$(cat STLGeneratorForOpenFoam/web/benchmark.js)" | tee docs/superpowers/plans/phase2-perf.json
```

- [ ] **Step 2: Measure single-model rebuild timing**

```bash
browser-use eval "(() => { const results = {}; ['rack','baffle','box','acu','wall','floor'].forEach(kind => { const m = state.models.find(x => x.kind === kind); if (!m) return; const times = []; for (let i = 0; i < 5; i++) { const t = performance.now(); rebuildSingleModel(m.id); times.push(performance.now()-t); } results[kind] = +(times.reduce((a,b)=>a+b,0)/times.length).toFixed(1); }); return JSON.stringify(results); })()"
```

- [ ] **Step 3: Print comparison table and screenshot**

```bash
browser-use screenshot /tmp/phase2_optimized.png
```

Expected improvements:
| Metric | Phase 1 | Phase 2 (Target) |
|--------|---------|-------------------|
| Full rebuild | 786ms | 786ms (unchanged — only for full reloads) |
| Single edit | 786ms | <10ms |
| Drag frame | 786ms (throttled) | <10ms |
| Rotation FPS | 8 | 8+ (unchanged — GPU-bound) |

**Key win:** Single-model edits go from 786ms → <10ms. This eliminates ALL lag when editing rack slots, moving models, adjusting patches, etc. in large scenes.

- [ ] **Step 4: Commit results**

```bash
git add docs/superpowers/plans/phase2-perf.json
git commit -m "perf: Phase 2 complete — per-model incremental rebuild"
```
