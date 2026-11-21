# STL Generator Drag Performance Optimization — Design Spec

**Date**: 2026-04-03
**Goal**: Fix severe drag latency (resize handle doesn't follow mouse smoothly) in `STL_generate.html` on large cases like CMI-Guichou (343 models, ~76K meshes) and Alibaba-Hangchou (287 models, ~60K meshes).

**Primary symptom**: Resizing baffle/floor/wall edges feels sluggish — mouse moves but the edge doesn't follow. This is main-thread latency, not GPU rendering throughput.

## Root Cause Analysis

The critical path during a drag pointermove:

```
pointermove fires
  12 separate handlers all execute (lines 12254-12265)
    updatePatchEdgeHover() raycasts ALL 76K renderMeshes — NO throttle (line 11509)
    computeProximitySnap() iterates ALL 2000+ renderRoots (line 9069, 80ms throttle)
    rebuildSingleModelForDrag() — rAF throttled, already efficient
    requestRender()
```

The two dominant costs per pointermove during drag:
1. **updatePatchEdgeHover()** — does `raycaster.intersectObjects(renderMeshes, false)` against the entire scene on every pointermove, even during an active drag when hover is irrelevant
2. **computeProximitySnap()** — iterates all renderRoots twice: once for avgSize (O(N)), once for snap candidates (O(N)), with N=2000+ enabled models

## Changes

### 1. Consolidated Pointermove Dispatcher

**File**: `web/STL_generate.html`, lines 12254-12265
**Problem**: 12 separate `addEventListener('pointermove', ...)` handlers fire on every mouse move. During an active drag, hover checks (raycasts, highlight updates) serve no purpose but still execute.

**Fix**: Replace the 12 individual listeners with a single dispatcher:

```javascript
renderer.domElement.addEventListener('pointermove', onPointerMove);

function onPointerMove(event) {
    // During any active drag — ONLY run the relevant drag updater
    if (dragState) {
        // dispatch by dragState.mode: wallResize, modelMove, boxFace, etc.
        updateActiveDrag(event);
        requestRender();
        return;
    }
    if (seedDragState) { updateSeedSurfaceDrag(event); requestRender(); return; }
    if (patchDragState) { updatePatchDrag(event); requestRender(); return; }
    if (locInMeshDragState) { updateLocInMeshDrag(event); requestRender(); return; }
    if (extraLocDragState) { updateExtraLocDrag(event); requestRender(); return; }
    if (blockMeshDragState) { updateBlockMeshDrag(event); requestRender(); return; }
    if (rzDragState) { updateRefinementZoneDrag(event); requestRender(); return; }
    if (ppaiDragState) { updatePPAIBoxDrag(event); requestRender(); return; }

    // No drag active — run hover checks (throttled via rAF)
    runHoverChecks(event);
    requestRender();
}
```

**Key behavior**: During any active drag, all hover raycasts are completely skipped.

### 2. Selection-Gated Edge Hover (Bug Fix + Optimization)

**File**: `web/STL_generate.html`, `updatePatchEdgeHover()` at line 11504
**Problem**: Currently raycasts against ALL `renderMeshes` (76K) on every pointermove to detect edge proximity on any model. This is both a UX bug (shouldn't be able to resize-drag an unselected object) and the single largest performance bottleneck.

**Fix**: Only check edge hover on the currently selected model's meshes:

```javascript
function updatePatchEdgeHover(event) {
    if (dragState || panelResizeState) return;
    if (state.dctwinConfig && state.dctwinConfig._uiFrozen) return;
    if (state.dctwinConfig && state.dctwinConfig.activeTab !== 'geometry') return;

    // Must have a selected model to show resize cursors
    const selModel = state.selection.modelId ? getModelById(state.selection.modelId) : null;
    if (!selModel) { clearHoveredPatchEdge(); return; }

    // Only raycast the selected model's meshes (5-20 vs 76K)
    const selRoot = getRenderRootByModelId(selModel.id);
    if (!selRoot) { clearHoveredPatchEdge(); return; }
    const selMeshes = [];
    selRoot.traverse(child => { if (child.isMesh) selMeshes.push(child); });

    updatePointerFromEvent(event);
    const hits = raycaster.intersectObjects(selMeshes, false);
    // ... rest of existing logic unchanged
}
```

**Impact**: Raycast cost drops from O(76K) to O(20). Zero cost when nothing is selected.

**Throttle safety net**: Additionally, gate all hover checks behind rAF coalescing:

```javascript
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
        if (tab === 'mesh') { _rzHoverCheck(e); updateBlockMeshHover(e); }
        else if (tab === 'postprocess') { _ppAiHoverCheck(e); }
        else { updatePatchEdgeHover(e); }
    });
}
```

### 3. Snap Computation Optimization

#### 3a. Cache avgSize on drag-start

**File**: `web/STL_generate.html`, `computeProximitySnap()` at line 9069
**Problem**: The first loop (lines 9085-9093) iterates ALL renderRoots to compute `avgSize` — but models don't change during a drag, so this is redundant.

**Fix**: Compute and cache `avgSize` once on drag-start:

```javascript
let _snapAvgSize = null;

function initSnapCache() {
    const allSizes = [];
    const scratch = new THREE.Vector3();
    renderRoots.forEach(root => {
        const mid = root.userData && root.userData.modelId;
        if (!mid || !isModelEnabled(mid)) return;
        const wb = getRenderRootWorldBounds(root);
        if (wb) allSizes.push(Math.max(...wb.getSize(scratch).toArray()));
    });
    _snapAvgSize = allSizes.length ? allSizes.reduce((a, b) => a + b, 0) / allSizes.length : 1;
}

function clearSnapCache() { _snapAvgSize = null; }
```

Call `initSnapCache()` in drag-start handlers, `clearSnapCache()` in drag-end. Inside `computeProximitySnap()`, use `_snapAvgSize` directly and skip lines 9084-9095.

#### 3b. Spatial hash grid for nearby-model lookup

**Problem**: The second loop (lines 9100-9134) also iterates ALL renderRoots. With 2000+ models, even with 80ms throttle, each call does ~2000 bbox comparisons.

**Fix**: Build a 2D spatial hash on drag-start, query only nearby cells during snap:

```javascript
let _snapGrid = null;
const SNAP_CELL_SIZE = 2.0;  // meters, tuned to typical threshold range

function buildSnapGrid() {
    _snapGrid = new Map();
    renderRoots.forEach(root => {
        const mid = root.userData && root.userData.modelId;
        if (!mid || !isModelEnabled(mid)) return;
        const wb = getRenderRootWorldBounds(root);
        if (!wb) return;
        const minC = { x: Math.floor(wb.min.x / SNAP_CELL_SIZE), y: Math.floor(wb.min.y / SNAP_CELL_SIZE) };
        const maxC = { x: Math.floor(wb.max.x / SNAP_CELL_SIZE), y: Math.floor(wb.max.y / SNAP_CELL_SIZE) };
        for (let cx = minC.x; cx <= maxC.x; cx++) {
            for (let cy = minC.y; cy <= maxC.y; cy++) {
                const key = cx + ',' + cy;
                if (!_snapGrid.has(key)) _snapGrid.set(key, []);
                _snapGrid.get(key).push({ id: mid, bounds: wb });
            }
        }
    });
}
```

In `computeProximitySnap()`, expand the combined moving bbox by `threshold`, query overlapping cells, and iterate only those entries. Typical query: 5-30 models vs 2000+.

#### 3c. Reuse for wall/floor resize snap

The inline resize-snap code at lines 11237-11280 duplicates the same iterate-all-renderRoots pattern. Replace with the same `_snapAvgSize` cache and `_snapGrid` lookup. Extract a shared `querySnapCandidates(bounds, threshold)` function.

### 4. Minor Quick Wins

#### 4a. Seed surface helper: transform, not rebuild

`updateSeedSurfaceDrag()` (line 12180) calls `updateSeedSurfaceHelper()` on every pointermove, which destroys and recreates the entire arrow+sphere geometry. During drag, update `Group.position`/`quaternion` only; full rebuild on drag-end.

#### 4b. Reuse scratch Vector3 in snap hot path

Lines 9090, 11242 create `new THREE.Vector3()` inside loops over all renderRoots. Use a module-level scratch vector to avoid thousands of micro-allocations per snap call.

### 5. Single-Click Patch Selection (Bug Fix)

**File**: `web/STL_generate.html`, `handleViewportSelection()` at line 11854, `_mergeModelGroup()` at line 6199
**Problem**: Clicking directly on a patch area requires two clicks — first click selects the parent model, second click selects the patch. This happens because merged proxy meshes lose all `userData` except `{ isMergedProxy: true, modelId }` (line 6281). The first click hits the proxy, falls through to `setSelection('model', modelId)` at line 11984. Only after the model is selected (triggering unmerge) can the second click resolve individual patch meshes.

**Root cause**: `_mergeModelGroup()` at line 6281:
```javascript
mergedMesh.userData = { isMergedProxy: true, modelId: modelGroup.userData.modelId };
```

**Fix**: When a merged proxy is clicked, temporarily unmerge that model and re-raycast to resolve the click to the finest-grained element:

```javascript
// In handleViewportSelection(), after pickPreferredHit:
if (mesh.userData.isMergedProxy && modelId) {
    const modelRoot = getRenderRootByModelId(modelId);
    if (modelRoot && modelRoot.userData._origChildren) {
        // Temporarily unmerge to get real mesh hits
        _unmergeModelGroup(modelRoot);
        const detailedHits = raycaster.intersectObjects(renderMeshes, false)
            .filter(h => h.object.userData && h.object.userData.modelId === modelId);
        const detailedHit = pickPreferredHit(detailedHits);
        if (detailedHit) {
            // Re-merge, then process the detailed hit through normal selection logic
            _mergeModelGroup(modelRoot);
            // Use detailedHit instead of original hit for selection dispatch
            mesh = detailedHit.object;
            // ... fall through to existing if/else chain
        } else {
            _mergeModelGroup(modelRoot);
        }
    }
}
```

**Impact**: Patches, rack slots, face regions, and imported regions all become single-click selectable even on merged models. No double-click needed.

### 6. Nearest-to-Camera Object Selection

**File**: `web/STL_generate.html`, `pickPreferredHit()` at line 1692
**Problem**: When objects overlap along the camera direction, the `depthWindow` (0.03m) groups them as co-planar candidates and picks by priority. This causes the selection to "jump through" a nearer object to select a farther one with higher priority — confusing the user.

**Example**: A baffle (priority 0) directly in front of a rack patch (priority 90) within 3cm — clicking selects the rack patch behind the baffle instead of the baffle itself.

**Fix**: For `handleViewportSelection()`, use strict nearest-first selection. Priority should only break ties for truly co-planar surfaces (same model, same face). Replace `pickPreferredHit()` usage in selection with a stricter variant:

```javascript
function pickNearestHit(hits, predicate = null) {
    const filtered = (hits || []).filter(hit => !predicate || predicate(hit));
    if (!filtered.length) return null;
    const nearest = filtered[0];  // hits are sorted by distance
    const nearestDist = nearest.distance || 0;
    // Only group co-planar hits from the SAME model (e.g., patch on wall face)
    const coplanarWindow = Math.max(0.005, nearestDist * 0.001);
    const candidates = filtered.filter(hit =>
        (hit.distance || 0) <= nearestDist + coplanarWindow
        && hit.object.userData.modelId === nearest.object.userData.modelId
    );
    // Among co-planar same-model hits, pick highest priority (patch > face > body)
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

**Key behavior changes**:
- Hits from **different models** are never grouped — nearest model always wins
- Hits from the **same model** at the same depth (e.g., patch mesh on wall face) still use priority to prefer the patch
- `depthWindow` reduced from 0.03m to 0.005m and constrained to same-model only

**Note**: `pickPreferredHit()` is still used for hover detection (`updatePatchEdgeHover`), where the wider window is appropriate. Only `handleViewportSelection()` switches to `pickNearestHit()`.

## Summary Table

| # | Change | Problem Solved | Effort |
|---|--------|---------------|--------|
| 1 | Single pointermove dispatcher, skip hover during drag | 12 handler calls + hover raycasts during drag | Small |
| 2 | Selection-gated edge hover + rAF throttle | 76K-mesh raycast on every pointermove | Small |
| 3a | Cache avgSize on drag-start | Redundant O(N) loop per snap call | Trivial |
| 3b | Spatial hash grid for snap | O(N) snap search, N=2000+ | Medium |
| 3c | Reuse spatial hash for resize snap | Duplicate O(N) inline code | Small |
| 4a | Seed helper: transform update, not rebuild | Geometry destroy+create per pointermove | Small |
| 4b | Scratch Vector3 reuse | GC pressure from micro-allocations | Trivial |
| 5 | Single-click patch selection via unmerge-raycast-remerge | Patch/slot/region requires double-click | Medium |
| 6 | Nearest-to-camera selection with same-model-only priority | Click "jumps through" nearer object to farther one | Small |

## Non-Goals (Phase 2)

- **InstancedMesh** for repeated fanServer/rack geometry — helps draw calls & memory, not drag latency
- **three-mesh-bvh** for accelerated raycasting — overkill once hover is selection-gated
- **Web Worker for snap** — too complex for single HTML file architecture
- **Undo system compression** — memory issue, not latency issue
- **Code splitting** — would break monolithic HTML architecture

## Testing

### Performance Tests
1. Load `CMI-Guichou.cfdproj` (343 models) → select a baffle → enable face snap → resize by dragging edge → should follow mouse smoothly
2. Load `Alibaba-Hangchou.cfdproj` (287 models) → select a wall → resize → smooth
3. Move mouse over viewport with nothing selected → no edge-hover cursors should appear (UX fix verification)
4. Select a wall → move mouse near its edges → resize cursor appears → deselect → cursor gone
5. Drag-move a model with snap enabled → snap should work correctly with spatial grid
6. Rotate viewport on large case → should remain smooth (no hover overhead during orbit)

### Selection Tests
7. Click directly on a patch of a non-selected wall/baffle/floor → patch should be selected immediately (single click, no double-click)
8. Click on a rack slot of a non-selected rack → slot should be selected immediately
9. Click on a baffle that is in front of a rack → baffle should be selected, not the rack behind it
10. Click on overlapping co-planar patches on the same wall face → higher-priority patch should be selected (priority still applies within same model)
11. Ctrl+click multi-select should still work correctly with nearest-first selection
