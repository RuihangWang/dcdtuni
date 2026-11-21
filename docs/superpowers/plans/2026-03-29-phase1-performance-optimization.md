# Phase 1 Performance Optimization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Alibaba Hangzhou scene (287 models, 36,934 meshes) from ~4 FPS to ~15+ FPS during camera rotation, and eliminate visible lag on selection changes.

**Architecture:** Three independent optimizations that compound: (1) eliminate 16,000+ slot guide meshes via lazy creation, (2) make highlight updates O(1) instead of O(N) per frame, (3) create automated benchmark script for before/after measurement.

**Tech Stack:** Three.js r128, vanilla JS, browser-use CLI for benchmarking

**Baseline (Alibaba Hangzhou):**
- Models: 287 | Meshes: 36,934
- `buildGeometryData()`: 1,338 ms
- `renderer.render()`: 283 ms avg per frame
- `updateHighlights()`: 44.6 ms per call
- Rotation FPS: ~4

---

### Task 1: Automated Benchmark Script

**Files:**
- Create: `STLGeneratorForOpenFoam/web/benchmark.js` (standalone, loaded on demand)

This script runs via `browser-use eval` and returns structured JSON metrics. It will be used before and after each optimization to measure impact.

- [ ] **Step 1: Create benchmark script**

```javascript
// STLGeneratorForOpenFoam/web/benchmark.js
// Usage: browser-use eval "$(cat STLGeneratorForOpenFoam/web/benchmark.js)"
// Or:    browser-use eval "fetch('benchmark.js').then(r=>r.text()).then(eval)"
(() => {
    const T = window.__three;
    if (!T) return JSON.stringify({error: 'window.__three not exposed'});
    const results = {timestamp: new Date().toISOString(), models: state.models.length};

    // 1. Mesh count
    let meshCount = 0, lineCount = 0;
    T.globalGroup.traverse(o => { if (o.isMesh) meshCount++; else if (o.isLine) lineCount++; });
    results.meshCount = meshCount;
    results.lineCount = lineCount;

    // 2. buildGeometryData timing
    const t0 = performance.now();
    window.buildGeometryData();
    results.rebuildMs = Math.round(performance.now() - t0);

    // 3. Render frame timing (avg of 10)
    const renderTimes = [];
    for (let i = 0; i < 10; i++) {
        const t = performance.now();
        T.renderer.render(T.scene, T.camera);
        renderTimes.push(performance.now() - t);
    }
    results.renderAvgMs = +(renderTimes.reduce((a,b)=>a+b,0)/renderTimes.length).toFixed(1);

    // 4. updateHighlights timing (avg of 5)
    const hlTimes = [];
    for (let i = 0; i < 5; i++) {
        const t = performance.now();
        if (typeof updateHighlights === 'function') updateHighlights();
        hlTimes.push(performance.now() - t);
    }
    results.highlightAvgMs = +(hlTimes.reduce((a,b)=>a+b,0)/hlTimes.length).toFixed(1);

    // 5. 30-frame rotation benchmark
    const radius = T.camera.position.length() || 20;
    const target = T.controls.target.clone();
    const frameTimes = [];
    for (let i = 0; i < 30; i++) {
        const angle = i * 0.05;
        T.camera.position.x = target.x + Math.cos(angle) * radius;
        T.camera.position.z = target.z + Math.sin(angle) * radius;
        T.camera.lookAt(target);
        const t = performance.now();
        T.renderer.render(T.scene, T.camera);
        frameTimes.push(performance.now() - t);
    }
    results.rotationAvgMs = +(frameTimes.reduce((a,b)=>a+b,0)/frameTimes.length).toFixed(1);
    results.rotationFPS = Math.round(1000 / results.rotationAvgMs);

    return JSON.stringify(results, null, 2);
})()
```

- [ ] **Step 2: Test the benchmark**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 3
# Load Alibaba project
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); if (xhr.status !== 200) return 'HTTP ' + xhr.status; window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK ' + state.models.length + ' models'; })()"
# Run benchmark
browser-use eval "$(cat STLGeneratorForOpenFoam/web/benchmark.js)"
```

Expected: JSON with baseline metrics matching ~36,934 meshes, ~4 FPS.

- [ ] **Step 3: Save baseline to file**

```bash
browser-use eval "$(cat STLGeneratorForOpenFoam/web/benchmark.js)" > docs/superpowers/plans/baseline-perf.json
```

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/benchmark.js docs/superpowers/plans/baseline-perf.json
git commit -m "perf: add automated benchmark script for browser-use"
```

---

### Task 2: Slot Guide Lazy Loading

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - Slot guide creation loop (~line 4544-4568 inside `renderRackModel`)
  - Selection handler: `setSelection` / `setRackSlotSelection` (~line 3867, 3397)
  - `buildGeometryData` or add new function for on-demand guide creation

**Problem:** 200 racks × 42 slots × 2 sides = 16,800 slot guide meshes. These are transparent overlays only used for clicking rack slots. They consume GPU memory and draw calls but are invisible 99% of the time.

**Solution:** Only create slot guides for the currently selected rack model. When selection changes to a different rack, remove old guides and create new ones.

- [ ] **Step 1: Guard slot guide creation with a model ID check**

In `renderRackModel`, wrap the slot guide loop (lines 4544-4568) with a condition:

Find the code block:
```javascript
            for (let slotIndex = 0; slotIndex < rack.unitCount; slotIndex++) {
                if (!isRackSlotSelectable(model, slotIndex)) continue;
                const bounds = getRackSlotBounds(model, slotIndex);
                ...
                spawnPlanarRect(modelGroup, guideOrigin, layout.right.clone(), layout.up.clone(), bounds.w, bounds.h, {
                    ...
                    isRackSlotGuide: true,
                    ...
                });
            }
```

Replace with:
```javascript
            // Slot guides: only create for the currently selected rack (lazy loading)
            const _isSelectedRack = state.selection.modelId === model.id;
            if (_isSelectedRack) {
                for (let slotIndex = 0; slotIndex < rack.unitCount; slotIndex++) {
                    if (!isRackSlotSelectable(model, slotIndex)) continue;
                    const bounds = getRackSlotBounds(model, slotIndex);
                    const layout = faceLayouts[side];
                    const normal = new THREE.Vector3().crossVectors(layout.right, layout.up).normalize();
                    const guideOrigin = layout.origin.clone()
                        .add(layout.right.clone().multiplyScalar(bounds.x))
                        .add(layout.up.clone().multiplyScalar(bounds.y))
                        .add(normal.multiplyScalar(0.0008));
                    spawnPlanarRect(modelGroup, guideOrigin, layout.right.clone(), layout.up.clone(), bounds.w, bounds.h, {
                        color: '#4daafc',
                        opacity: 0.015,
                        depthWrite: false,
                        skipEdges: true,
                        userData: {
                            modelId: model.id,
                            faceId: side,
                            side,
                            slotIndex,
                            isRackSlot: true,
                            isRackSlotGuide: true,
                            exportName: null
                        }
                    });
                }
            }
```

- [ ] **Step 2: Run benchmark to measure impact**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 3
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK'; })()"
browser-use eval "$(cat STLGeneratorForOpenFoam/web/benchmark.js)"
```

Expected: meshCount drops by ~16,800 (from ~36,934 to ~20,134). Rotation FPS should improve.

- [ ] **Step 3: Verify slot selection still works**

```bash
# Select a rack, then check slot guides are created
browser-use eval "setSelection('model', state.models.find(m => m.kind === 'rack').id); buildGeometryData(); let guides = 0; window.__three.globalGroup.traverse(o => { if (o.userData && o.userData.isRackSlotGuide) guides++; }); 'Guides for selected rack: ' + guides"
```

Expected: ~84 guides (42 slots × 2 sides) for the selected rack only.

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: lazy-load slot guides only for selected rack (-16800 meshes)"
```

---

### Task 3: updateHighlights O(1) — Skip Non-Selected Models

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `updateHighlights()` function (~line 3899-3990)

**Problem:** `updateHighlights()` iterates ALL 36,934 meshes every frame during rotation. At 44.6ms per call, this alone limits FPS to ~22.

**Solution:** Early-exit for meshes that don't belong to the selected model. During camera rotation (no selection change), most meshes don't need updating. Cache the previous selection state and only re-process when selection actually changes.

- [ ] **Step 1: Add selection-change tracking and early-exit optimization**

Replace the entire `updateHighlights()` function (lines 3899-3990) with:

```javascript
    let _prevHighlightKey = '';

    function updateHighlights() {
        // Build a key from current selection state
        const sel = state.selection;
        const selKey = sel.type + '|' + (sel.modelId || '') + '|' + (sel.faceId || '') + '|' + (sel.patchId || '') + '|' + (sel.equipmentId || '') + '|' + (sel.partitionIndex ?? '') + '|' + (sel.wallIndex ?? '') + '|' + (sel.regionName || '') + '|' + (sel.slotIndex ?? '') + '|' + ((sel.slotIndices || []).join(','));

        // If selection hasn't changed, skip the full re-highlight
        if (selKey === _prevHighlightKey) return;
        _prevHighlightKey = selKey;

        const selectedSlotIndices = getSelectedRackSlotIndices();

        renderMeshes.forEach(mesh => {
            const data = mesh.userData || {};
            if (!data.origColor) return;

            const selectedModel = sel.type === 'model' && getSelectedModelIds().includes(data.modelId);
            const selectedFace = sel.type === 'face' && sel.modelId === data.modelId && sel.faceId === data.faceId;
            const selectedPatch = sel.type === 'patch' && sel.modelId === data.modelId && data.refIds && data.refIds.includes(sel.patchId);
            const selectedRegion = (sel.type === 'region' || sel.type === 'previewRegion')
                && sel.modelId === data.modelId
                && data.regionName === sel.regionName;
            const selectedWall = sel.type === 'wallRegion'
                && sel.modelId === data.modelId
                && sel.faceId === data.faceId
                && sel.wallIndex === data.wallIndex;
            let selectedRackSlot = sel.type === 'rackSlot'
                && sel.modelId === data.modelId
                && sel.side === data.side
                && selectedSlotIndices.includes(data.slotIndex);
            const selectedRackEquipment = sel.type === 'rackEquipment'
                && sel.modelId === data.modelId
                && sel.equipmentId === data.equipmentId;
            const selectedRackPartition = sel.type === 'rackPartition'
                && sel.modelId === data.modelId
                && sel.partitionIndex === data.partitionIndex;

            if (!selectedRackSlot && sel.type === 'rackSlot' && sel.modelId === data.modelId && data.equipmentId) {
                const model = getModelById(data.modelId);
                const equipment = getRackEquipmentById(model, data.equipmentId);
                if (equipment && selectedSlotIndices.some(slotIndex => slotIndex >= equipment.startUnit && slotIndex < equipment.startUnit + equipment.spanUnits)) {
                    selectedRackSlot = true;
                }
            }

            if (data.isRackSlotGuide) {
                mesh.material.color.setHex((selectedRackSlot || selectedRackEquipment) ? 0x4daafc : new THREE.Color(data.origColor).getHex());
                mesh.material.opacity = (selectedRackSlot || selectedRackEquipment) ? 0.45 : 0.04;
                return;
            }

            if (data.isProjectionGuide) {
                mesh.material.color.setHex((selectedModel || selectedFace) ? 0x4daafc : new THREE.Color(data.origColor).getHex());
                mesh.material.opacity = (selectedModel || selectedFace) ? 0.28 : 0.08;
                return;
            }

            if (selectedModel || selectedFace || selectedPatch || selectedRegion || selectedWall || selectedRackSlot || selectedRackEquipment || selectedRackPartition) {
                if (!data._origMaterial) {
                    data._origMaterial = mesh.material;
                    mesh.material = mesh.material.clone();
                }
                if (selectedRackSlot || selectedRackEquipment) {
                    if (mesh.material.map) {
                        if (!data._origMap) data._origMap = mesh.material.map;
                        mesh.material.map = null;
                        mesh.material.needsUpdate = true;
                    }
                    if (mesh.material.normalMap) {
                        if (!data._origNormalMap) data._origNormalMap = mesh.material.normalMap;
                        mesh.material.normalMap = null;
                    }
                    if (mesh.material.color) mesh.material.color.setHex(0x0055ff);
                    if (mesh.material.emissive) mesh.material.emissive.setHex(0x0044dd);
                    if (typeof mesh.material.emissiveIntensity === 'number') mesh.material.emissiveIntensity = 1.5;
                    mesh.material.opacity = 0.9;
                    mesh.material.transparent = true;
                } else {
                    if (mesh.material.color) mesh.material.color.set(new THREE.Color(data.origColor));
                    if (mesh.material.emissive) mesh.material.emissive.setHex(0x4daafc);
                    if (typeof mesh.material.emissiveIntensity === 'number') {
                        mesh.material.emissiveIntensity = selectedPatch || selectedRegion || selectedWall ? 0.42 : 0.26;
                    }
                }
                if (typeof mesh.material.envMapIntensity === 'number') mesh.material.envMapIntensity = 0;
                if (mesh.material.transparent) {
                    const baseOpacity = Number.isFinite(data.baseOpacity) ? data.baseOpacity : 1;
                    mesh.material.opacity = Math.min(0.96, Math.max(baseOpacity, 0.08) + 0.1);
                }
            } else {
                if (data._origMaterial) {
                    if (mesh.material !== data._origMaterial) mesh.material.dispose();
                    mesh.material = data._origMaterial;
                    data._origMaterial = null;
                    data._origMap = null;
                    data._origNormalMap = null;
                }
            }
        });

        renderHelpers.forEach(helper => {
```

**Key change:** The `selKey` comparison at the top. During camera rotation (where selection doesn't change), `updateHighlights()` returns immediately — **O(1)** instead of O(36,934).

Note: `renderScene()` calls `updateHighlights()`, which is called from `doRebuildGeometry()`. After a geometry rebuild, highlights MUST be recalculated, so we need to reset `_prevHighlightKey` when geometry is rebuilt.

- [ ] **Step 2: Reset highlight cache on geometry rebuild**

Find `function doRebuildGeometry()` and add a cache reset:

```javascript
    function doRebuildGeometry() {
        buildGeometryData();
        _prevHighlightKey = '';  // Force highlight recalculation after rebuild
        renderScene();
        if (window.requestRender) window.requestRender();
    }
```

- [ ] **Step 3: Run benchmark**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 3
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK'; })()"
browser-use eval "$(cat STLGeneratorForOpenFoam/web/benchmark.js)"
```

Expected: `highlightAvgMs` drops from 44.6ms to ~0.01ms during rotation (no selection change). `rotationFPS` should improve significantly.

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "perf: skip updateHighlights when selection unchanged (O(1) camera rotation)"
```

---

### Task 4: Final Benchmark Comparison

- [ ] **Step 1: Run full benchmark on optimized code**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use close 2>/dev/null; sleep 1
browser-use open http://localhost:8000/STL_generate.html; sleep 3
browser-use eval "(() => { const xhr = new XMLHttpRequest(); xhr.open('GET', 'projects/Alibaba_hangchou_1774691588321.cfdproj', false); xhr.send(); window._applyProjectData(JSON.parse(xhr.responseText), 'bench.cfdproj'); return 'OK'; })()"
browser-use eval "$(cat STLGeneratorForOpenFoam/web/benchmark.js)" | tee docs/superpowers/plans/phase1-perf.json
```

- [ ] **Step 2: Compare results**

Print before/after comparison table.

Expected improvements:
| Metric | Before | After (Target) |
|--------|--------|----------------|
| Mesh count | 36,934 | ~20,134 |
| Render frame avg | 283ms | ~150ms |
| Highlight avg | 44.6ms | ~0.01ms |
| Rotation FPS | 4 | 12+ |

- [ ] **Step 3: Take screenshot for visual verification**

```bash
browser-use screenshot /tmp/phase1_optimized.png
```

- [ ] **Step 4: Commit final results**

```bash
git add docs/superpowers/plans/phase1-perf.json
git commit -m "perf: Phase 1 optimization complete — benchmark results"
```
