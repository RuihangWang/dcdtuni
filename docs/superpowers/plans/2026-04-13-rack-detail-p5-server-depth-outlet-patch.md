# Rack Detail P5: Per-server Depth + Outlet Patch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-equipment depth offsets stacked on rack-level offsets, plus rectangular/circular outlet patches that split the server outlet face into outlet + wall solids.

**Architecture:** Equipment state extended with `extraInletOffset`, `extraOutletOffset`, `outletPatch`. Geometry rendering reads per-server depths. STL export splits outlet face when patch is set. Backend registers patch solid as `fan_outlet`, wall remainder as `wall`.

**Tech Stack:** Three.js r128, vanilla JS (STL_generate.html), Python (stlgen_runner.py).

---

### Task 1: Add per-server depth state + validation

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `cloneRackState()` equipment map (~line 2007)
  - `syncRackModelState()` (~line 2022)

- [ ] **Step 1: Add properties to equipment clone**

In `cloneRackState()`, find the equipment mapping (~line 2010-2017). After `spanUnits`, add:

```javascript
                        extraInletOffset: Number.isFinite(equipment.extraInletOffset) ? equipment.extraInletOffset : 0,
                        extraOutletOffset: Number.isFinite(equipment.extraOutletOffset) ? equipment.extraOutletOffset : 0,
                        outletPatch: equipment.outletPatch ? {
                            type: equipment.outletPatch.type === 'circle' ? 'circle' : 'rect',
                            cx: Number.isFinite(equipment.outletPatch.cx) ? equipment.outletPatch.cx : 0.5,
                            cy: Number.isFinite(equipment.outletPatch.cy) ? equipment.outletPatch.cy : 0.5,
                            width: Number.isFinite(equipment.outletPatch.width) ? equipment.outletPatch.width : 0.6,
                            height: Number.isFinite(equipment.outletPatch.height) ? equipment.outletPatch.height : 0.8,
                            radius: Number.isFinite(equipment.outletPatch.radius) ? equipment.outletPatch.radius : 0.3,
                        } : null,
```

- [ ] **Step 2: Add validation in syncRackModelState**

In `syncRackModelState`, find the equipment normalization loop (where `normalizedEquipments` is built). After each equipment is validated, add:

```javascript
            // Per-server depth offset validation
            eq.extraInletOffset = Math.max(0, Number(eq.extraInletOffset) || 0);
            eq.extraOutletOffset = Math.max(0, Number(eq.extraOutletOffset) || 0);
            var totalIn = rack.inletOffset + eq.extraInletOffset;
            var totalOut = rack.outletOffset + eq.extraOutletOffset;
            if (totalIn + totalOut >= model.base.D) {
                var scale = (model.base.D * 0.9) / (totalIn + totalOut);
                eq.extraInletOffset = Math.max(0, eq.extraInletOffset * scale);
                eq.extraOutletOffset = Math.max(0, eq.extraOutletOffset * scale);
            }
            // Outlet patch validation
            if (eq.outletPatch) {
                var p = eq.outletPatch;
                p.cx = Math.min(1, Math.max(0, Number(p.cx) || 0.5));
                p.cy = Math.min(1, Math.max(0, Number(p.cy) || 0.5));
                p.width = Math.min(1, Math.max(0.05, Number(p.width) || 0.6));
                p.height = Math.min(1, Math.max(0.05, Number(p.height) || 0.8));
                p.radius = Math.min(0.5, Math.max(0.05, Number(p.radius) || 0.3));
            }
```

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(rack-detail-p5): per-server depth + outlet patch state and validation"
```

---

### Task 2: Per-server depth geometry in renderRackModel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `renderRackModel()` equipment rendering section

- [ ] **Step 1: Compute per-server depth in the equipment loop**

In `renderRackModel`, find the `rack.equipments.forEach(equipment => {` loop. At the top of the loop (after `const bounds = ...` and `const height = ...`), add:

```javascript
            var eqExtraIn = equipment.extraInletOffset || 0;
            var eqExtraOut = equipment.extraOutletOffset || 0;
            var eqSlotStart = slotDepthStart + eqExtraIn;
            var eqSlotEnd = slotDepthEnd - eqExtraOut;
            var eqSlotDepth = eqSlotEnd - eqSlotStart;
```

- [ ] **Step 2: Use per-server depth for equipment plates**

Find the equipment bottom/top plate `spawnPlanarRect` calls. These currently use `slotDepthStart` / `slotDepthEnd` / `effectiveSlotDepth`. Replace with `eqSlotStart` / `eqSlotEnd` / `eqSlotDepth`:

For ALL bottom plates (nonFan + fanServer): origin Y from `slotDepthStart` → `eqSlotStart`, depth from `effectiveSlotDepth` → `eqSlotDepth`.

For ALL top plates: origin Y from `slotDepthEnd` → `eqSlotEnd`, depth from `effectiveSlotDepth` → `eqSlotDepth`.

- [ ] **Step 3: Use per-server depth for equipment face positions**

Find the equipment inlet/outlet face rendering. The Codex P2 fix made these use `spawnPlanarRect` at `slotDepthStart`/`slotDepthEnd` when offset > 0. Change to use `eqSlotStart`/`eqSlotEnd`:

For inlet face (with offset): origin Y from `slotDepthStart` → `eqSlotStart`
For outlet face (with offset): origin Y from `slotDepthEnd` → `eqSlotEnd`

Also update the condition: render with `spawnPlanarRect` when `eqExtraIn > 1e-6 || inletOff > 1e-6` (for inlet), and `eqExtraOut > 1e-6 || outletOff > 1e-6` (for outlet).

- [ ] **Step 4: Test in browser**

1. Enter rack detail → add server → set extraInletOffset to 0.05
2. Server plates should visually sit deeper than the rack-level offset

- [ ] **Step 5: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p5): per-server depth geometry in renderRackModel"
```

---

### Task 3: Per-server depth UI controls

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - Inspector panel for equipment in detail mode
  - Handler functions

- [ ] **Step 1: Add per-server depth section to equipment inspector**

When the user selects a server in rack detail mode, the right panel should show depth controls. Find the rack detail inspector rendering — when `isRackDetailMode()`, the panel is rendered by `_renderRackDetailPanel`. But when a server WITHIN the rack is selected, the normal equipment inspector shows.

The approach: modify the equipment rendering in the inspector to detect rack detail mode and add extra controls. Find `renderRackModelInspector` or the equipment inspector section. When `isRackDetailMode()` and the selected entity is an equipment, append:

```javascript
// After existing equipment inspector content, add:
if (isRackDetailMode()) {
    html += `
    <div class="prop-group" style="border-color:#5a6a8a;">
        <div class="prop-title" style="color:#5a6a8a;">📏 Server 进深 / Server Depth</div>
        <div style="padding:4px 8px;">
            <div style="display:flex; align-items:center; gap:6px; margin-bottom:4px;">
                <label style="font-size:11px; color:#4af; min-width:72px;">额外inlet</label>
                <input type="range" min="0" max="40" step="1"
                       value="${Math.round((equipment.extraInletOffset / model.base.D) * 100)}"
                       oninput="window._rdSetServerDepth('${model.id}', '${equipment.id}', 'inlet', parseFloat(this.value))"
                       onchange="window._rdCheckpointPushed = false"
                       style="flex:1;">
                <span style="font-size:10px; color:#666; min-width:28px;">${Math.round((equipment.extraInletOffset / model.base.D) * 100)}%</span>
            </div>
            <div style="display:flex; align-items:center; gap:6px;">
                <label style="font-size:11px; color:#f64; min-width:72px;">额外outlet</label>
                <input type="range" min="0" max="40" step="1"
                       value="${Math.round((equipment.extraOutletOffset / model.base.D) * 100)}"
                       oninput="window._rdSetServerDepth('${model.id}', '${equipment.id}', 'outlet', parseFloat(this.value))"
                       onchange="window._rdCheckpointPushed = false"
                       style="flex:1;">
                <span style="font-size:10px; color:#666; min-width:28px;">${Math.round((equipment.extraOutletOffset / model.base.D) * 100)}%</span>
            </div>
            <div style="font-size:10px; color:#666; padding-top:4px;">
                有效进深: ${formatLength(model.base.D - (rack.inletOffset + equipment.extraInletOffset) - (rack.outletOffset + equipment.extraOutletOffset))}
            </div>
        </div>
    </div>`;
}
```

The exact insertion point depends on how the equipment inspector is structured. The implementer should find where equipment properties are rendered in the inspector and append this block when `isRackDetailMode()`.

- [ ] **Step 2: Add handler function**

Near the other rack detail handlers:

```javascript
    window._rdSetServerDepth = function(modelId, equipId, side, pct) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        if (!window._rdCheckpointPushed) { pushUndoCheckpoint('rackDetailServerDepth'); window._rdCheckpointPushed = true; }
        var eq = model.rack.equipments.find(function(e) { return e.id === equipId; });
        if (!eq) return;
        var key = side === 'inlet' ? 'extraInletOffset' : 'extraOutletOffset';
        eq[key] = (pct / 100) * model.base.D;
        syncRackModelState(model);
        doRebuildRackDetail();
        if (window.requestRender) window.requestRender();
    };
```

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p5): per-server depth UI controls"
```

---

### Task 4: Outlet patch state + UI controls

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add outlet patch section to equipment inspector**

In the same equipment inspector section (when `isRackDetailMode()`), after the server depth controls, add:

```javascript
if (isRackDetailMode() && equipment.kind === 'fanServer') {
    var op = equipment.outletPatch;
    html += `
    <div class="prop-group" style="border-color:#00aa44;">
        <div class="prop-title" style="color:#00aa44;">🎯 Outlet Patch</div>
        <div style="padding:4px 8px;">
            <div style="display:flex; align-items:center; gap:6px; margin-bottom:6px;">
                <label style="font-size:11px;">添加 Patch</label>
                <input type="checkbox" ${op ? 'checked' : ''}
                       onchange="window._rdToggleOutletPatch('${model.id}', '${equipment.id}', this.checked)">
            </div>
            ${op ? `
            <div style="margin-bottom:4px;">
                <label style="font-size:10px; color:#aaa;">类型</label>
                <select style="font-size:10px; margin-left:4px;"
                        onchange="window._rdSetOutletPatchType('${model.id}', '${equipment.id}', this.value)">
                    <option value="rect" ${op.type === 'rect' ? 'selected' : ''}>矩形 Rect</option>
                    <option value="circle" ${op.type === 'circle' ? 'selected' : ''}>圆形 Circle</option>
                </select>
            </div>
            ${op.type === 'rect' ? `
            <div style="display:flex; align-items:center; gap:4px; margin-bottom:3px;">
                <label style="font-size:10px; color:#aaa; min-width:32px;">宽</label>
                <input type="range" min="5" max="100" step="1" value="${Math.round(op.width * 100)}"
                       oninput="window._rdSetOutletPatchProp('${model.id}', '${equipment.id}', 'width', parseFloat(this.value)/100)"
                       onchange="window._rdCheckpointPushed = false" style="flex:1;">
                <span style="font-size:10px; color:#666; min-width:28px;">${Math.round(op.width * 100)}%</span>
            </div>
            <div style="display:flex; align-items:center; gap:4px; margin-bottom:3px;">
                <label style="font-size:10px; color:#aaa; min-width:32px;">高</label>
                <input type="range" min="5" max="100" step="1" value="${Math.round(op.height * 100)}"
                       oninput="window._rdSetOutletPatchProp('${model.id}', '${equipment.id}', 'height', parseFloat(this.value)/100)"
                       onchange="window._rdCheckpointPushed = false" style="flex:1;">
                <span style="font-size:10px; color:#666; min-width:28px;">${Math.round(op.height * 100)}%</span>
            </div>` : `
            <div style="display:flex; align-items:center; gap:4px; margin-bottom:3px;">
                <label style="font-size:10px; color:#aaa; min-width:32px;">半径</label>
                <input type="range" min="5" max="50" step="1" value="${Math.round(op.radius * 100)}"
                       oninput="window._rdSetOutletPatchProp('${model.id}', '${equipment.id}', 'radius', parseFloat(this.value)/100)"
                       onchange="window._rdCheckpointPushed = false" style="flex:1;">
                <span style="font-size:10px; color:#666; min-width:28px;">${Math.round(op.radius * 100)}%</span>
            </div>`}
            ` : ''}
        </div>
    </div>`;
}
```

- [ ] **Step 2: Add handler functions**

```javascript
    window._rdToggleOutletPatch = function(modelId, equipId, enabled) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        pushUndoCheckpoint('rackDetailOutletPatch');
        var eq = model.rack.equipments.find(function(e) { return e.id === equipId; });
        if (!eq) return;
        eq.outletPatch = enabled ? { type: 'rect', cx: 0.5, cy: 0.5, width: 0.6, height: 0.8, radius: 0.3 } : null;
        syncRackModelState(model);
        rebuildGeometry();
        logOperation('rackDetailOutletPatch', (enabled ? 'Add' : 'Remove') + ' outlet patch', { modelId: modelId, equipId: equipId }, 'modify');
    };

    window._rdSetOutletPatchType = function(modelId, equipId, type) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        pushUndoCheckpoint('rackDetailOutletPatch');
        var eq = model.rack.equipments.find(function(e) { return e.id === equipId; });
        if (eq && eq.outletPatch) { eq.outletPatch.type = type; }
        syncRackModelState(model);
        rebuildGeometry();
    };

    window._rdSetOutletPatchProp = function(modelId, equipId, prop, value) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        if (!window._rdCheckpointPushed) { pushUndoCheckpoint('rackDetailOutletPatch'); window._rdCheckpointPushed = true; }
        var eq = model.rack.equipments.find(function(e) { return e.id === equipId; });
        if (eq && eq.outletPatch) { eq.outletPatch[prop] = value; }
        syncRackModelState(model);
        doRebuildRackDetail();
        if (window.requestRender) window.requestRender();
    };
```

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p5): outlet patch UI controls"
```

---

### Task 5: Outlet patch rendering in renderRackModel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Render outlet patch + wall regions**

In `renderRackModel`, inside the equipment loop, find where the outlet face is rendered (both the `spawnFaceRect('back', ...)` and `spawnPlanarRect` paths for the outlet face).

WRAP the existing outlet face rendering in a conditional: if `equipment.outletPatch` is set, render 2 regions instead of 1:

```javascript
            if (equipment.outletPatch && equipment.kind === 'fanServer' && !backDeleted) {
                var op = equipment.outletPatch;
                var faceW = bounds.w;
                var faceH = height;
                // Patch region (green — the actual outlet)
                if (op.type === 'rect') {
                    var pw = faceW * op.width, ph = faceH * op.height;
                    var px = bounds.x + faceW * (op.cx - op.width / 2);
                    var pz = bounds.y + faceH * (op.cy - op.height / 2);
                    spawnPlanarRect(modelGroup,
                        new THREE.Vector3(px, eqSlotEnd, pz),
                        new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 1),
                        pw, ph,
                        { color: '#00cc66', opacity: 0.85, objectKind: 'server-front',
                          userData: { modelId: model.id, ...baseEquipData, side: 'back',
                                     exportName: exportStem + '_outlet_patch', isOutletPatch: true } }
                    );
                }
                // Wall region (dark — remaining area rendered as a full face with cutout color)
                spawnPlanarRect(modelGroup,
                    new THREE.Vector3(bounds.x, eqSlotEnd, bounds.y),
                    new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 1),
                    faceW, faceH,
                    { color: '#555555', opacity: 0.6, objectKind: 'server-front',
                      userData: { modelId: model.id, ...baseEquipData, side: 'back',
                                 exportName: exportStem + '_outlet_wall' } }
                );
            } else {
                // Original outlet face rendering (no patch)
                // ... keep existing spawnFaceRect/spawnPlanarRect code here
            }
```

The implementer needs to carefully restructure the existing outlet face code into the `else` branch.

- [ ] **Step 2: Test visually**

1. Add server → enable outlet patch → green rectangle appears on outlet face
2. Drag width/height sliders → patch resizes

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p5): render outlet patch + wall regions"
```

---

### Task 6: STL export for outlet patch

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `exportBoxModel()` — split outlet face when patch is set

- [ ] **Step 1: Handle outlet patch in STL export**

In `exportBoxModel`, meshes are grouped by `exportName`. The rendering in Task 5 already assigns separate `exportName` values (`_outlet_patch` and `_outlet_wall`). So the STL export should automatically produce 2 separate solids.

However, the `_outlet_wall` region is rendered as a FULL face (not a cutout) for visual simplicity. In the STL export, we need the ACTUAL wall region (face minus patch). For the initial implementation, export the wall as the full face — this is geometrically incorrect but will be fixed in a refinement pass. Mark with a TODO comment.

Alternatively, for rect patches: export the wall as 4 quads (8 triangles) forming the L-shaped border around the patch cutout. This is geometrically correct.

The implementer should read the existing `appendMeshFacets` function and understand how meshes are exported, then decide whether to generate the cutout geometry in the rendering (Task 5) or in the export.

**Recommended approach**: In the rendering (Task 5), render the wall region as 4 separate rects (top strip, bottom strip, left strip, right strip) around the patch, all with the same `exportName = '_outlet_wall'`. This way both the visual AND the STL export are correct.

- [ ] **Step 2: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p5): correct STL export for outlet patch cutout"
```

---

### Task 7: cfd_export + backend BC for outlet patch

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `buildCfdExportJson()`
- Modify: `src/backend/aircooling/runner/stlgen_runner.py` — `_build_bridge_bc()`

- [ ] **Step 1: Export per-server fields in cfd_export**

In `buildCfdExportJson`, find the server export section. Add:

```javascript
                    extraInletOffset: equipment.extraInletOffset || 0,
                    extraOutletOffset: equipment.extraOutletOffset || 0,
                    outletPatch: equipment.outletPatch || null,
                    outletWallSolidName: equipment.outletPatch
                        ? sanitizeName(getRackEquipmentExportStem(model, equipment)) + '_outlet_wall'
                        : null,
```

- [ ] **Step 2: Register outlet patch + wall BCs in backend**

In `_build_bridge_bc()`, find the fan-server BC registration (~line 1906-1912). After the existing `registry.register(outlet, ...)`, add:

```python
                    # If server has outlet patch, register wall for remaining area
                    outlet_patch_data = srv.get("outletPatch")
                    if outlet_patch_data:
                        outlet_wall = _sanitize(srv.get("outletWallSolidName", ""))
                        if outlet_wall:
                            registry.register(outlet_wall, BCSpec(bc_type="wall"))
                            logger.info(f"  {server_id}: outlet patch → wall remainder {outlet_wall}")
```

The `outletSolidName` (the patch) keeps its existing `fan_outlet` BC. The new `outletWallSolidName` gets `wall` BC.

- [ ] **Step 3: Run tests**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/ tests/test_liquid/ -x -q
```

- [ ] **Step 4: Commit**

```bash
cd STLGeneratorForOpenFoam && git add web/STL_generate.html
cd /mnt/lv/litianyu/mycode/dcdtuni && git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(rack-detail-p5): cfd_export + backend BC for outlet patch"
```

---

### Task 8: End-to-end verification

- [ ] **Step 1: Per-server depth test**

1. Create rack → add 2 servers → enter detail
2. Set server 1 extra inlet offset to 10% → server 1 inlet face moves deeper
3. Server 2 stays at rack-level offset → different depths visible
4. Export STL → verify vertex positions of both server plates

- [ ] **Step 2: Outlet patch test**

1. Select fan-server → enable outlet patch
2. Green rect patch visible on outlet face
3. Change to circle → visual updates
4. Resize → sliders work smoothly
5. Export STL → 2 solids: `_outlet_patch` + `_outlet_wall`
6. cfd_export → `outletPatch` and `outletWallSolidName` present

- [ ] **Step 3: Backward compat**

1. Load old project → no errors, no extra offsets, no patches
2. Undo/redo → all new properties revert correctly

- [ ] **Step 4: Final commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(rack-detail-p5): complete — per-server depth + outlet patch"
```

---

## Self-Review

**Spec coverage:**
- §1 Per-server depth state → Task 1 ✓
- §1.4 UI → Task 3 ✓
- §1.5 Geometry → Task 2 ✓
- §1.6 cfd_export → Task 7 ✓
- §2 Outlet patch state → Task 1 ✓
- §2.4 UI → Task 4 ✓
- §2.5 Rendering → Task 5 ✓
- §2.6 STL export → Task 6 ✓
- §2.7 cfd_export → Task 7 ✓
- §2.8 Backend BC → Task 7 ✓

**Placeholder scan:** Task 6 notes the wall cutout geometry challenge — the recommended approach (4 border rects) is described. No TBD.

**Type consistency:** `extraInletOffset`, `extraOutletOffset`, `outletPatch`, `eqSlotStart`, `eqSlotEnd` — consistent across all tasks.
