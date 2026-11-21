# Rack Detail P2: Inner Width & U-slot Depth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Rack's inner channel width and U-slot depth user-configurable, with real-time 3D preview and correct STL export.

**Architecture:** Two new `model.rack` properties (`inletOffset`, `outletOffset`) plus the existing `slotWidthRatio` are exposed through the Rack Detail panel with input+slider controls. `renderRackModel` is modified to use these offset values when positioning inner walls, equipment plates, and cavity walls.

**Tech Stack:** Three.js r128, vanilla JS, single file (STL_generate.html).

---

### Task 1: Add state properties and validation

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `getDefaultRackState()` (~line 1952)
  - `cloneRackState()` (~line 1972)
  - `syncRackModelState()` (~line 2022)

- [ ] **Step 1: Add defaults to `getDefaultRackState()`**

Find `getDefaultRackState()` at line 1952. Add after `slotWidthRatio: 0.8,` (line 1957):

```javascript
            inletOffset: 0,
            outletOffset: 0,
```

- [ ] **Step 2: Add to `cloneRackState()`**

Find `cloneRackState()` at line 1972. In the returned object, after the `slotWidthRatio` line (line 1979), add:

```javascript
            inletOffset: Number.isFinite(rawRack.inletOffset) ? rawRack.inletOffset : fallback.inletOffset,
            outletOffset: Number.isFinite(rawRack.outletOffset) ? rawRack.outletOffset : fallback.outletOffset,
```

- [ ] **Step 3: Add validation to `syncRackModelState()`**

Find `syncRackModelState()` at line 2022. After `rack.slotWidthRatio = ...` (line 2029), add:

```javascript
        // U-slot depth offsets: each ≤ 50% of depth, sum < total depth
        rack.inletOffset = Math.max(0, Number(rack.inletOffset) || 0);
        rack.outletOffset = Math.max(0, Number(rack.outletOffset) || 0);
        var halfD = model.base.D * 0.5;
        rack.inletOffset = Math.min(rack.inletOffset, halfD);
        rack.outletOffset = Math.min(rack.outletOffset, halfD);
        if (rack.inletOffset + rack.outletOffset >= model.base.D) {
            // Scale both down proportionally to leave at least 10% depth for the slot
            var scale = (model.base.D * 0.9) / (rack.inletOffset + rack.outletOffset);
            rack.inletOffset *= scale;
            rack.outletOffset *= scale;
        }
```

- [ ] **Step 4: Verify page loads without errors**

Refresh browser, create a rack, verify console is clean.

- [ ] **Step 5: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p2): add inletOffset/outletOffset state and validation"
```

---

### Task 2: Replace P1 placeholder UI with inner width controls

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `_renderRackDetailPanel()` (~line 8254)

- [ ] **Step 1: Replace the inner width placeholder**

In `_renderRackDetailPanel()` at line 8254, find the placeholder block (lines 8277-8279):

```html
<div class="prop-group" style="border-color:#555;">
    <div class="prop-title" style="color:#888;">─── 内径宽度 / Inner Width ───</div>
    <div style="padding:8px; color:#666; font-size:11px; text-align:center;" id="rack-detail-inner-width">Phase 2 — 即将推出</div>
</div>
```

Replace with:

```javascript
            <div class="prop-group" style="border-color:#2a7a5a;">
                <div class="prop-title" style="color:#2a7a5a;">📐 内径宽度 / Inner Width</div>
                <div style="padding:4px 8px;">
                    <div style="display:flex; align-items:center; gap:6px; margin-bottom:6px;">
                        <label style="font-size:11px; color:#aaa; white-space:nowrap;">宽度(m)</label>
                        <input type="number" step="${getLengthStep()}" min="0.01"
                               value="${formatLength(model.base.W * rack.slotWidthRatio)}"
                               onchange="window._rdSetInnerWidth('${model.id}', parseFloat(this.value))"
                               style="width:70px; font-size:11px;">
                        <input type="range" min="20" max="95" step="1"
                               value="${Math.round(rack.slotWidthRatio * 100)}"
                               oninput="window._rdSetInnerWidthPct('${model.id}', parseFloat(this.value))"
                               style="flex:1;">
                        <span style="font-size:10px; color:#666; font-family:monospace; min-width:30px;">${Math.round(rack.slotWidthRatio * 100)}%</span>
                    </div>
                </div>
            </div>
```

- [ ] **Step 2: Add handler functions**

Near `window.rackDetailToggleContext` (~line 10064), add:

```javascript
    var _rdCheckpointPushed = false;

    window._rdSetInnerWidth = function(modelId, meters) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        if (!_rdCheckpointPushed) { pushUndoCheckpoint('rackDetailInnerWidth'); _rdCheckpointPushed = true; }
        var ratio = meters / model.base.W;
        model.rack.slotWidthRatio = Math.min(0.95, Math.max(0.2, ratio));
        syncRackModelState(model);
        rebuildGeometry();
        logOperation('rackDetailInnerWidth', 'Set inner width: ' + meters.toFixed(3) + 'm', { modelId: modelId }, 'modify');
    };

    window._rdSetInnerWidthPct = function(modelId, pct) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        if (!_rdCheckpointPushed) { pushUndoCheckpoint('rackDetailInnerWidth'); _rdCheckpointPushed = true; }
        model.rack.slotWidthRatio = Math.min(0.95, Math.max(0.2, pct / 100));
        syncRackModelState(model);
        rebuildGeometry();
    };
```

- [ ] **Step 3: Reset checkpoint flag on interaction end**

Add an `onchange` to the slider to reset the flag (checkpoint per drag, not per frame):

In the slider's HTML, add `onchange="window._rdCheckpointPushed = false"` alongside the existing `oninput`.

Actually, the simpler pattern is: set `_rdCheckpointPushed = false` at the start of `enterRackDetail` and `exitRackDetail`. Find both functions and add `_rdCheckpointPushed = false;` at the top.

- [ ] **Step 4: Test in browser**

1. Enter rack detail
2. Drag the inner width slider — rack channel should narrow/widen in real-time
3. Type a value in the input box — slider updates
4. Ctrl+Z — reverts

- [ ] **Step 5: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p2): add inner width controls with input+slider"
```

---

### Task 3: Replace P1 placeholder UI with U-slot depth controls

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `_renderRackDetailPanel()` (~line 8254)

- [ ] **Step 1: Replace the slot depth placeholder**

In `_renderRackDetailPanel()`, find the placeholder block (lines 8281-8283):

```html
<div class="prop-group" style="border-color:#555;">
    <div class="prop-title" style="color:#888;">─── U-slot 进深 / Slot Depth ───</div>
    <div style="padding:8px; color:#666; font-size:11px; text-align:center;" id="rack-detail-slot-depth">Phase 2 — 即将推出</div>
</div>
```

Replace with:

```javascript
            <div class="prop-group" style="border-color:#5a6a8a;">
                <div class="prop-title" style="color:#5a6a8a;">📏 U-slot 进深 / Slot Depth</div>
                <div style="padding:4px 8px;">
                    <div style="display:flex; align-items:center; gap:6px; margin-bottom:4px;">
                        <label style="font-size:11px; color:#4af; white-space:nowrap;">距inlet(m)</label>
                        <input type="number" step="${getLengthStep()}" min="0"
                               value="${formatLength(rack.inletOffset)}"
                               onchange="window._rdSetInletOffset('${model.id}', parseFloat(this.value))"
                               style="width:70px; font-size:11px;">
                    </div>
                    <div style="display:flex; align-items:center; gap:6px; margin-bottom:6px;">
                        <label style="font-size:11px; color:#f64; white-space:nowrap;">距outlet(m)</label>
                        <input type="number" step="${getLengthStep()}" min="0"
                               value="${formatLength(rack.outletOffset)}"
                               onchange="window._rdSetOutletOffset('${model.id}', parseFloat(this.value))"
                               style="width:70px; font-size:11px;">
                    </div>
                    <div style="margin-top:4px;">
                        <div style="display:flex; align-items:center; gap:4px; margin-bottom:3px;">
                            <label style="font-size:10px; color:#4af; min-width:52px;">inlet %</label>
                            <input type="range" min="0" max="50" step="1"
                                   value="${Math.round((rack.inletOffset / model.base.D) * 100)}"
                                   oninput="window._rdSetInletOffsetPct('${model.id}', parseFloat(this.value))"
                                   style="flex:1;">
                            <span style="font-size:10px; color:#666; font-family:monospace; min-width:28px;">${Math.round((rack.inletOffset / model.base.D) * 100)}%</span>
                        </div>
                        <div style="display:flex; align-items:center; gap:4px; margin-bottom:3px;">
                            <label style="font-size:10px; color:#f64; min-width:52px;">outlet %</label>
                            <input type="range" min="0" max="50" step="1"
                                   value="${Math.round((rack.outletOffset / model.base.D) * 100)}"
                                   oninput="window._rdSetOutletOffsetPct('${model.id}', parseFloat(this.value))"
                                   style="flex:1;">
                            <span style="font-size:10px; color:#666; font-family:monospace; min-width:28px;">${Math.round((rack.outletOffset / model.base.D) * 100)}%</span>
                        </div>
                        <div style="display:flex; align-items:center; gap:4px; border-top:1px solid #333; padding-top:3px;">
                            <label style="font-size:10px; color:#aaa; min-width:52px;">同时控制</label>
                            <input type="range" min="0" max="50" step="1"
                                   value="${Math.round(Math.max(rack.inletOffset, rack.outletOffset) / model.base.D * 100)}"
                                   oninput="window._rdSetBothOffsetPct('${model.id}', parseFloat(this.value))"
                                   style="flex:1;">
                            <span style="font-size:10px; color:#666; font-family:monospace; min-width:28px;">${Math.round(Math.max(rack.inletOffset, rack.outletOffset) / model.base.D * 100)}%</span>
                        </div>
                    </div>
                </div>
            </div>
```

- [ ] **Step 2: Add handler functions**

Near the inner width handlers added in Task 2, add:

```javascript
    window._rdSetInletOffset = function(modelId, meters) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        if (!_rdCheckpointPushed) { pushUndoCheckpoint('rackDetailSlotDepth'); _rdCheckpointPushed = true; }
        model.rack.inletOffset = Math.max(0, meters);
        syncRackModelState(model);
        rebuildGeometry();
        logOperation('rackDetailSlotDepth', 'Set inlet offset: ' + meters.toFixed(3) + 'm', { modelId: modelId }, 'modify');
    };

    window._rdSetOutletOffset = function(modelId, meters) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        if (!_rdCheckpointPushed) { pushUndoCheckpoint('rackDetailSlotDepth'); _rdCheckpointPushed = true; }
        model.rack.outletOffset = Math.max(0, meters);
        syncRackModelState(model);
        rebuildGeometry();
        logOperation('rackDetailSlotDepth', 'Set outlet offset: ' + meters.toFixed(3) + 'm', { modelId: modelId }, 'modify');
    };

    window._rdSetInletOffsetPct = function(modelId, pct) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        if (!_rdCheckpointPushed) { pushUndoCheckpoint('rackDetailSlotDepth'); _rdCheckpointPushed = true; }
        model.rack.inletOffset = (pct / 100) * model.base.D;
        syncRackModelState(model);
        rebuildGeometry();
    };

    window._rdSetOutletOffsetPct = function(modelId, pct) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        if (!_rdCheckpointPushed) { pushUndoCheckpoint('rackDetailSlotDepth'); _rdCheckpointPushed = true; }
        model.rack.outletOffset = (pct / 100) * model.base.D;
        syncRackModelState(model);
        rebuildGeometry();
    };

    window._rdSetBothOffsetPct = function(modelId, pct) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        if (!_rdCheckpointPushed) { pushUndoCheckpoint('rackDetailSlotDepth'); _rdCheckpointPushed = true; }
        model.rack.inletOffset = (pct / 100) * model.base.D;
        model.rack.outletOffset = (pct / 100) * model.base.D;
        syncRackModelState(model);
        rebuildGeometry();
    };
```

- [ ] **Step 3: Test in browser**

1. Enter rack detail → see depth controls
2. Set inlet offset to 0.05 → nothing visual changes yet (geometry mod is Task 4)
3. Slider values update correctly
4. "同时控制" slider sets both offsets identically

- [ ] **Step 4: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p2): add U-slot depth controls with 3 sliders"
```

---

### Task 4: Modify inner wall and equipment geometry for depth offset

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `renderRackModel()` — inner walls (~line 6410-6446) and equipment plates (~line 6480-6528)

This is the core geometry task. All depth-dependent dimensions in `renderRackModel` must use `inletOffset` / `outletOffset`.

- [ ] **Step 1: Compute effective depth values at the top of `renderRackModel`**

Find `renderRackModel()` — search for `function renderRackModel`. Near the top of the function, after existing variable declarations (like `const slotWidth = ...`, `const rName = ...`), add:

```javascript
        // U-slot depth offsets (P2)
        var inletOff = rack.inletOffset || 0;
        var outletOff = rack.outletOffset || 0;
        var slotDepthStart = inletOff;                    // Y coordinate where slot begins
        var slotDepthEnd = model.base.D - outletOff;      // Y coordinate where slot ends
        var effectiveSlotDepth = slotDepthEnd - slotDepthStart;
```

- [ ] **Step 2: Modify inner left/right walls to use effective depth**

Find the inner wall rendering (~lines 6411-6446). Change the left wall's origin Y and depth:

**Left wall** (line ~6411-6427): Change `model.base.D` to `effectiveSlotDepth` and origin Y from `0` to `slotDepthStart`:

```javascript
            spawnPlanarRect(
                modelGroup,
                new THREE.Vector3(slotX, slotDepthStart, safeBottomHeight),
                new THREE.Vector3(0, 1, 0),
                new THREE.Vector3(0, 0, 1),
                effectiveSlotDepth,
                centralFrameHeight,
                {
                    color: channelWallColor,
                    opacity,
                    objectKind: 'rack-side',
                    userData: {
                        modelId: model.id,
                        isBaseWall: true,
                        exportName: `${rName}_inner_left`
                    }
                }
            );
```

**Right wall** (line ~6429-6446): Same changes — origin Y to `slotDepthStart`, depth dimension to `effectiveSlotDepth`:

```javascript
            spawnPlanarRect(
                modelGroup,
                new THREE.Vector3(slotX + slotWidth, slotDepthStart, safeBottomHeight),
                new THREE.Vector3(0, 0, 1),
                new THREE.Vector3(0, 1, 0),
                centralFrameHeight,
                effectiveSlotDepth,
                {
                    color: channelWallColor,
                    opacity,
                    objectKind: 'rack-side',
                    userData: {
                        modelId: model.id,
                        isBaseWall: true,
                        exportName: `${rName}_inner_right`
                    }
                }
            );
```

- [ ] **Step 3: Modify equipment top/bottom plates to use effective depth**

Find non-fan equipment plates (~lines 6480-6502). Change:
- Bottom plate origin: `new THREE.Vector3(bounds.x, 0, bounds.y)` → `new THREE.Vector3(bounds.x, slotDepthStart, bounds.y)`
- Bottom plate depth: `model.base.D` → `effectiveSlotDepth`
- Top plate origin: `new THREE.Vector3(bounds.x, model.base.D, bounds.y + height)` → `new THREE.Vector3(bounds.x, slotDepthEnd, bounds.y + height)`
- Top plate depth: `model.base.D` → `effectiveSlotDepth` (and flip direction since origin changed)

Do the same for fan-server channel walls (~lines 6519-6528).

- [ ] **Step 4: Test in browser**

1. Enter rack detail
2. Set both offsets to 10% → inner walls should visibly shorten
3. Equipment plates should narrow in depth
4. Offsets at 0% → identical to before (backward compat)

- [ ] **Step 5: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p2): adjust inner wall and equipment depth for offsets"
```

---

### Task 5: Add cavity wall geometry

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `renderRackModel()` — new section after inner walls

- [ ] **Step 1: Add cavity wall rendering after inner walls**

After the inner left/right wall block (after line ~6447), add:

```javascript
        // P2: Cavity walls — seal the offset zone on left/right sides
        if (inletOff > 1e-6) {
            // Inlet cavity left wall
            spawnPlanarRect(
                modelGroup,
                new THREE.Vector3(slotX, 0, safeBottomHeight),
                new THREE.Vector3(0, 1, 0),
                new THREE.Vector3(0, 0, 1),
                inletOff,
                centralFrameHeight,
                {
                    color: channelWallColor, opacity,
                    objectKind: 'rack-side',
                    userData: { modelId: model.id, isBaseWall: true,
                               exportName: `${rName}_inlet_cavity_left` }
                }
            );
            // Inlet cavity right wall
            spawnPlanarRect(
                modelGroup,
                new THREE.Vector3(slotX + slotWidth, 0, safeBottomHeight),
                new THREE.Vector3(0, 0, 1),
                new THREE.Vector3(0, 1, 0),
                centralFrameHeight,
                inletOff,
                {
                    color: channelWallColor, opacity,
                    objectKind: 'rack-side',
                    userData: { modelId: model.id, isBaseWall: true,
                               exportName: `${rName}_inlet_cavity_right` }
                }
            );
        }
        if (outletOff > 1e-6) {
            // Outlet cavity left wall
            spawnPlanarRect(
                modelGroup,
                new THREE.Vector3(slotX, slotDepthEnd, safeBottomHeight),
                new THREE.Vector3(0, 1, 0),
                new THREE.Vector3(0, 0, 1),
                outletOff,
                centralFrameHeight,
                {
                    color: channelWallColor, opacity,
                    objectKind: 'rack-side',
                    userData: { modelId: model.id, isBaseWall: true,
                               exportName: `${rName}_outlet_cavity_left` }
                }
            );
            // Outlet cavity right wall
            spawnPlanarRect(
                modelGroup,
                new THREE.Vector3(slotX + slotWidth, slotDepthEnd, safeBottomHeight),
                new THREE.Vector3(0, 0, 1),
                new THREE.Vector3(0, 1, 0),
                centralFrameHeight,
                outletOff,
                {
                    color: channelWallColor, opacity,
                    objectKind: 'rack-side',
                    userData: { modelId: model.id, isBaseWall: true,
                               exportName: `${rName}_outlet_cavity_right` }
                }
            );
        }
```

- [ ] **Step 2: Test in browser**

1. Set inlet offset to 10% → two short walls appear near the inlet face inside the channel
2. Set outlet offset to 10% → same near outlet
3. Rotate to see the walls from different angles
4. Set both to 0% → no cavity walls (clean)

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p2): render cavity wall geometry for depth offsets"
```

---

### Task 6: Adjust leakage zone depth for offsets

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - Leakage zone rendering (~line 6562) and `buildLeakageZoneStl()` (~line 23790)

- [ ] **Step 1: Modify leakage zone rendering**

Find the leakage zone visualization code (search for `leakageGroupStart` or `isLeakageZone`). The leakage box currently uses `model.base.D` for depth. Change to `effectiveSlotDepth` and offset the Y origin to `slotDepthStart`.

Look for the 6-face leakage box rendering in `renderRackModel`. Each face that uses `model.base.D` for the depth dimension should use `effectiveSlotDepth`, and Y=0 origins should shift to `slotDepthStart`.

- [ ] **Step 2: Modify `buildLeakageZoneStl()`**

Find `buildLeakageZoneStl` (~line 23790). It generates raw triangles for leakage zone STL export. Change depth references from full `D` to `effectiveSlotDepth` with offset.

Read the function first to find where `model.base.D` or the depth dimension is used, then replace with offset-aware values.

- [ ] **Step 3: Test**

1. Create rack with offsets → delete U-slots on both sides to create a leakage zone
2. Leakage zone box should match the shortened slot depth
3. Export STL → leakage solid should have correct geometry

- [ ] **Step 4: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p2): adjust leakage zone depth for slot offsets"
```

---

### Task 7: Export offsets in cfd_export.json

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `buildCfdExportJson()` — rack geometry section

- [ ] **Step 1: Add offsets to the export**

Find `buildCfdExportJson()` — search for the function. Inside the rack geometry export section, find where `slotWidthRatio` is exported and add the new fields:

```javascript
                    slotWidthRatio: rack.slotWidthRatio,
                    inletOffset: rack.inletOffset || 0,
                    outletOffset: rack.outletOffset || 0,
```

- [ ] **Step 2: Verify export**

1. Create rack with offsets
2. Run prepare-case API or export
3. Check the .cfdrun file contains `inletOffset` and `outletOffset`

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p2): export inletOffset/outletOffset in cfd_export.json"
```

---

### Task 8: End-to-end verification

- [ ] **Step 1: Full geometry test**

1. Create room + rack
2. Enter rack detail
3. Set inner width to 60% → channel narrows visually
4. Set inlet offset to 10%, outlet to 15% → cavity walls appear, inner walls shorten
5. Add a fan-server → its plates are at the offset positions
6. Delete U-slots to create leakage → zone matches offset depth
7. Export STL → new solids appear: `_inlet_cavity_left/right`, `_outlet_cavity_left/right`
8. Ctrl+Z → all changes revert

- [ ] **Step 2: Backward compatibility test**

1. Load an existing project with no offsets
2. Rack renders identically to before
3. Export → no offset fields (or 0)

- [ ] **Step 3: Edge case test**

1. Set both offsets to 50% → validation clamps, slot has minimum depth
2. Set inner width to min (20%) → narrow channel
3. Set inner width to max (95%) → wide channel

- [ ] **Step 4: Final commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p2): complete — inner width + slot depth controls"
```

---

## Self-Review

**Spec coverage:**
- §1 State Properties → Task 1 ✓
- §2.1 Inner Width UI → Task 2 ✓
- §2.2 Slot Depth UI → Task 3 ✓
- §3 Geometry Changes (inner walls, equipment, cavity) → Tasks 4, 5 ✓
- §4 STL Solid Names → Task 5 (userData.exportName) ✓
- §5 cfd_export → Task 7 ✓
- §6 Backward Compat → Task 1 (defaults=0) + Task 8 ✓
- Leakage zone adjustment → Task 6 ✓

**Placeholder scan:** All code blocks contain complete implementations. Task 6 (leakage) is less prescriptive because the leakage rendering code is complex and the implementer needs to read it first — but the task describes exactly WHAT to change (depth references).

**Type consistency:** `inletOffset`, `outletOffset`, `slotDepthStart`, `slotDepthEnd`, `effectiveSlotDepth` used consistently.
