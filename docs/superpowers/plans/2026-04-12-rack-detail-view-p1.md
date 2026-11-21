# Rack Detail View (P1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Rack Detail" view mode to STL_generate.html where a single Rack is rendered in isolation for fine-grained U-slot editing.

**Architecture:** New state variables (`rackDetailTarget`, `rackDetailReturnMode`, `rackDetailShowContext`) drive an isolated rendering path (`doRebuildRackDetail`) alongside the existing 3D/2D/STL-preview modes. Entry via Inspector button, exit via toolbar return button.

**Tech Stack:** Three.js r128, vanilla JS, single file (STL_generate.html).

---

### Task 1: Add state variables and mode detection helper

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:2631-2640` (inside `getInitialState()`, after `dctwinConfig`)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:3004-3006` (after `isStlPreviewMode`)

- [ ] **Step 1: Add state variables to `getInitialState()`**

Find the `dctwinConfig` block (ends at line ~2640). After it, add:

```javascript
            rackDetailTarget: null,
            rackDetailReturnMode: '3d',
            rackDetailShowContext: false,
```

Insert these three lines right after `dctwinConfig: { ... },` block (line 2640) and before `liquidCooling: {` (line 2641).

- [ ] **Step 2: Add mode detection helper**

After `isStlPreviewMode()` at line 3006, add:

```javascript

    function isRackDetailMode() {
        return state.rackDetailTarget !== null;
    }
```

- [ ] **Step 3: Verify page loads without errors**

Open `http://localhost:8005/STL_generate.html`, login, open browser console — no JS errors.

- [ ] **Step 4: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail): add state variables and isRackDetailMode helper"
```

---

### Task 2: Add `doRebuildRackDetail()` rendering function and wire into dispatcher

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:7073` (near `doRebuildStlPreview`)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:7424-7426` (`rebuildGeometry` dispatcher)

- [ ] **Step 1: Add `doRebuildRackDetail()` function**

Insert this function right before `doRebuildStlPreview()` (before line 7073):

```javascript
    function doRebuildRackDetail() {
        clearRender();
        var targetId = state.rackDetailTarget;
        var model = getModelById(targetId);
        if (!model || model.kind !== 'rack') {
            // Target deleted or invalid — exit detail mode
            state.rackDetailTarget = null;
            doRebuildGeometry();
            return;
        }

        // Render the target rack centered at origin
        var modelGroup = new THREE.Group();
        // Position at origin (subtract anchor to center the rack)
        modelGroup.position.set(0, 0, 0);
        modelGroup.userData = { modelId: model.id };
        // Apply rotation but NOT translation (we center it)
        applyModelTransformToObject(modelGroup, model);
        // Override position back to 0 (applyModelTransformToObject may set it)
        modelGroup.position.set(0, 0, 0);
        globalGroup.add(modelGroup);
        renderRoots.push(modelGroup);
        _renderRootMap.set(model.id, modelGroup);

        // Anchor marker
        if (webglAvailable) {
            var anchorMarker = createAnchorMarkerForModel(model);
            modelGroup.add(anchorMarker);
            renderHelpers.push(anchorMarker);
        }

        // Render rack geometry using existing pipeline
        var faceLayouts = getFaceLayouts(model);
        renderRackModel(model, modelGroup, faceLayouts);

        // Context models (if toggle is on)
        if (state.rackDetailShowContext) {
            state.models.forEach(function(otherModel) {
                if (otherModel.id === targetId || !isModelEnabled(otherModel)) return;
                var ctxGroup = new THREE.Group();
                ctxGroup.position.set(
                    otherModel.anchor.x - model.anchor.x,
                    otherModel.anchor.y - model.anchor.y,
                    otherModel.anchor.z - model.anchor.z
                );
                ctxGroup.userData = { modelId: otherModel.id, isRackDetailContext: true };
                applyModelTransformToObject(ctxGroup, otherModel);
                // Override position to be relative to target rack
                ctxGroup.position.set(
                    otherModel.anchor.x - model.anchor.x,
                    otherModel.anchor.y - model.anchor.y,
                    otherModel.anchor.z - model.anchor.z
                );
                globalGroup.add(ctxGroup);

                var ctxFaceLayouts = getFaceLayouts(otherModel);
                if (otherModel.kind === 'rack') renderRackModel(otherModel, ctxGroup, ctxFaceLayouts);
                else if (otherModel.kind === 'wall') renderWallModel(otherModel, ctxGroup, ctxFaceLayouts);
                else if (otherModel.kind === 'baffle') renderBaffleModel(otherModel, ctxGroup, ctxFaceLayouts);
                else if (otherModel.kind === 'floor') renderFloorModel(otherModel, ctxGroup, ctxFaceLayouts);
                else if (otherModel.kind === 'custom' && geometryCache.has(otherModel.id)) renderCustomModel(otherModel, ctxGroup, ctxFaceLayouts);
                else renderBoxModel(otherModel, ctxGroup, ctxFaceLayouts, buildFaceIslands(otherModel));

                // Make context meshes ghost-like: low opacity, not pickable
                ctxGroup.traverse(function(child) {
                    if (child.isMesh || child.isLineSegments || child.isLine) {
                        if (child.material) {
                            child.material = child.material.clone();
                            child.material.opacity = 0.08;
                            child.material.transparent = true;
                            child.material.depthWrite = false;
                        }
                        child.layers.set(2); // raycaster only checks layer 0
                        child.renderOrder = -1;
                    }
                });
            });
        }

        updateHighlights();
    }
```

- [ ] **Step 2: Wire into `rebuildGeometry()` dispatcher**

Change lines 7424-7426 from:

```javascript
    function rebuildGeometry() {
        if (isStlPreviewMode()) doRebuildStlPreview();
        else doRebuildGeometry();
```

to:

```javascript
    function rebuildGeometry() {
        if (isRackDetailMode()) doRebuildRackDetail();
        else if (isStlPreviewMode()) doRebuildStlPreview();
        else doRebuildGeometry();
```

- [ ] **Step 3: Verify page loads, no errors when not in rack detail mode**

Refresh page, create models, verify normal 3D mode still works.

- [ ] **Step 4: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail): add doRebuildRackDetail renderer and dispatcher branch"
```

---

### Task 3: Add `enterRackDetail()` and `exitRackDetail()` functions

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (near `toggleStlPreviewMode`, ~line 9827)

- [ ] **Step 1: Add `enterRackDetail()` and `exitRackDetail()`**

Insert after `toggleStlPreviewMode` (after line ~9854):

```javascript
    // --- Rack Detail View Mode ---
    var _rackDetailSavedCamera = null;
    var _rackDetailSavedBtnHTML = '';
    var _rackDetailSavedBtnOnclick = null;

    window.enterRackDetail = function(modelId) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;

        // Save camera
        _rackDetailSavedCamera = captureCameraViewState();

        // Record return mode
        state.rackDetailReturnMode = (typeof room2d !== 'undefined' && room2d._active) ? '2d' : '3d';

        // If in 2D, exit first
        if (typeof room2d !== 'undefined' && room2d._active) {
            room2d.exitEditor(false);
        }

        // Enter rack detail mode
        state.rackDetailTarget = modelId;
        state.rackDetailShowContext = false;

        // Rebuild scene with only the target rack
        rebuildGeometry();

        // Fit camera to rack
        _fitCameraToRack(model);

        // Swap toolbar button
        var btn2d = document.querySelector('[data-i18n="btn.room_2d_editor"]');
        if (btn2d) {
            _rackDetailSavedBtnHTML = btn2d.innerHTML;
            _rackDetailSavedBtnOnclick = btn2d.onclick;
            var returnLabel = state.rackDetailReturnMode === '2d' ? '↩ 返回 2D 界面' : '↩ 返回 3D 界面';
            btn2d.innerHTML = returnLabel;
            btn2d.onclick = function() { window.exitRackDetail(); };
        }

        logOperation('enterRackDetail', 'Enter Rack Detail: ' + model.name, { modelId: modelId }, 'view');
    };

    window.exitRackDetail = function() {
        if (!isRackDetailMode()) return;

        state.rackDetailTarget = null;

        // Restore view
        if (state.rackDetailReturnMode === '2d' && typeof room2d !== 'undefined') {
            room2d.enterEditor();
        } else {
            rebuildGeometry();
        }

        // Restore camera
        if (_rackDetailSavedCamera) {
            restoreCameraViewState(_rackDetailSavedCamera);
            _rackDetailSavedCamera = null;
        }

        // Restore toolbar button
        var btn2d = document.querySelector('[data-i18n="btn.room_2d_editor"]');
        if (btn2d) {
            btn2d.innerHTML = _rackDetailSavedBtnHTML;
            btn2d.onclick = _rackDetailSavedBtnOnclick;
        }

        logOperation('exitRackDetail', 'Exit Rack Detail', {}, 'view');
    };

    function _fitCameraToRack(model) {
        // After rebuild, rack is centered at origin. Compute bounds from render root.
        var root = getRenderRootByModelId(model.id);
        if (!root) return;
        var box = new THREE.Box3().setFromObject(root);
        var center = new THREE.Vector3();
        box.getCenter(center);
        var size = new THREE.Vector3();
        box.getSize(size);

        // Inlet face normal based on rack orientation
        var geom = model.geometry || {};
        var ori = geom.orientation || 0;
        var rad = ori * Math.PI / 180;
        var inletNormal = new THREE.Vector3(Math.sin(rad), -Math.cos(rad), 0);

        // Camera at inlet face, distance = 1.5x max dimension
        var maxDim = Math.max(size.x, size.y, size.z);
        var fitDist = maxDim * 1.5;
        var camPos = center.clone().add(inletNormal.clone().multiplyScalar(fitDist));
        camPos.z = center.z + size.z * 0.3;

        camera.position.copy(camPos);
        controls.target.copy(center);
        controls.update();
        if (window.requestRender) window.requestRender();
    }
```

- [ ] **Step 2: Verify no errors on page load**

Refresh page, confirm console is clean. Functions exist but are not yet callable from UI.

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail): add enterRackDetail/exitRackDetail with camera fit"
```

---

### Task 4: Add "Rack 细节设计" button to Inspector

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:8169` (inside `renderRackModelInspector`)

- [ ] **Step 1: Add the detail editor button at the top of the rack inspector**

In `renderRackModelInspector(prop, model)` at line 8169, the function starts with `prop.innerHTML = \`...`. Insert a new button group at the very beginning of the template literal, right after the backtick opening:

Find line 8172 (`prop.innerHTML = \``), and the first content line 8173 (`${anchorControlsForModel(model)}`). Insert before `${anchorControlsForModel(model)}`:

```javascript
            <div class="prop-group" style="border-color:#ff8c00;">
                <button class="btn" style="width:100%; padding:8px; font-size:13px; background:linear-gradient(180deg,#3a2a10,#2a1a08); border:1px solid #ff8c00; color:#ffaa33; font-weight:bold;" onclick="window.enterRackDetail('${model.id}')">🔧 Rack 细节设计 / Rack Detail Editor</button>
            </div>
```

So line 8172-8174 becomes:

```javascript
        prop.innerHTML = `
            <div class="prop-group" style="border-color:#ff8c00;">
                <button class="btn" style="width:100%; padding:8px; font-size:13px; background:linear-gradient(180deg,#3a2a10,#2a1a08); border:1px solid #ff8c00; color:#ffaa33; font-weight:bold;" onclick="window.enterRackDetail('${model.id}')">🔧 Rack 细节设计 / Rack Detail Editor</button>
            </div>
            ${anchorControlsForModel(model)}
```

- [ ] **Step 2: Test in browser**

1. Create a room + rack
2. Select the rack
3. Right panel should show the orange "Rack 细节设计" button at the top
4. Click it — should enter detail view (only rack visible, camera fits to inlet face)

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail): add detail editor button to rack inspector"
```

---

### Task 5: Add Rack Detail right panel content (Inspector override)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (inside `updateUI` or the inspector rendering path)

- [ ] **Step 1: Add rack detail inspector panel rendering**

Find `renderRackModelInspector(prop, model)` at line 8169. Add a guard at the very top to render the Detail panel instead when in rack detail mode:

```javascript
    function renderRackModelInspector(prop, model) {
        // If in Rack Detail mode, render the detail panel instead of normal inspector
        if (isRackDetailMode() && state.rackDetailTarget === model.id) {
            _renderRackDetailPanel(prop, model);
            return;
        }
        const rack = model.rack;
        // ... rest of existing function unchanged
```

Then add the detail panel renderer nearby (before `renderRackModelInspector`):

```javascript
    function _renderRackDetailPanel(prop, model) {
        var rack = model.rack;
        var eqCount = (rack.equipments || []).length;
        var unitH_mm = (rack.unitHeight * 1000).toFixed(1);
        prop.innerHTML = `
            <div class="prop-group" style="border-color:#ff8c00;">
                <div class="prop-title" style="color:#ff8c00;">🔧 Rack Detail: ${model.name}</div>
                <div style="font-size:11px; color:#aaa; padding:4px 8px; line-height:1.6;">
                    📦 ${t('label.width_w')}: ${formatLength(model.base.W)} ×
                    ${t('label.depth_d')}: ${formatLength(model.base.D)} ×
                    H: ${formatLength(model.base.H)}<br>
                    U-slots: ${rack.unitCount} (${unitH_mm}mm/U)<br>
                    ${t('inspector.rack_equipment')}: ${eqCount}
                </div>
            </div>
            <div class="prop-group" style="border-color:#4a9eff;">
                <div class="prop-title" style="color:#4a9eff;">👁️ 显示选项 / Display</div>
                <div class="prop-row">
                    <label style="font-size:11px;">显示周围环境 / Show Context</label>
                    <input type="checkbox" ${state.rackDetailShowContext ? 'checked' : ''}
                           onchange="window.rackDetailToggleContext(this.checked)">
                </div>
            </div>
            <div class="prop-group" style="border-color:#555;">
                <div class="prop-title" style="color:#888;">─── 内径宽度 / Inner Width ───</div>
                <div style="padding:8px; color:#666; font-size:11px; text-align:center;">Phase 2 — 即将推出</div>
            </div>
            <div class="prop-group" style="border-color:#555;">
                <div class="prop-title" style="color:#888;">─── U-slot 进深 / Slot Depth ───</div>
                <div style="padding:8px; color:#666; font-size:11px; text-align:center;" id="rack-detail-slot-depth">Phase 2 — 即将推出</div>
            </div>
            <div class="prop-group" style="border-color:#555;">
                <div class="prop-title" style="color:#888;">─── 穿孔门 / Perforated Door ───</div>
                <div style="padding:8px; color:#666; font-size:11px; text-align:center;" id="rack-detail-door">Phase 3 — 即将推出</div>
            </div>
            <div class="prop-group" style="border-color:#5a3a3a;">
                <button class="btn" style="width:100%; padding:6px; font-size:11px; background:linear-gradient(180deg,#4a1e1e,#3a1616); border:1px solid #5a2a2a; color:#f66;" onclick="window.exitRackDetail()">↩ 退出细节设计 / Exit Detail Editor</button>
            </div>
        `;
    }
```

- [ ] **Step 2: Add `rackDetailToggleContext` function**

Insert near `enterRackDetail` / `exitRackDetail`:

```javascript
    window.rackDetailToggleContext = function(checked) {
        state.rackDetailShowContext = checked;
        rebuildGeometry();
        logOperation('rackDetailToggleContext', 'Toggle context: ' + checked, { show: checked }, 'view');
    };
```

- [ ] **Step 3: Test in browser**

1. Select rack → click "Rack 细节设计"
2. Right panel shows: Rack summary, context toggle, P2/P3 placeholders, exit button
3. Toggle "显示周围环境" ON → other models appear as ghosts
4. Toggle OFF → other models disappear
5. Click "退出细节设计" → returns to normal view

- [ ] **Step 4: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail): add detail panel with context toggle and P2/P3 placeholders"
```

---

### Task 6: Guard tab switching and model deletion

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:16171` (`switchLeftTab`)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (model deletion handler)

- [ ] **Step 1: Exit rack detail on tab switch**

In `switchLeftTab()` at line 16171, add a guard at the top:

```javascript
    window.switchLeftTab = function(tab) {
        // Exit rack detail if switching away from geometry tab
        if (isRackDetailMode()) {
            window.exitRackDetail();
        }
        // view-only — no undo checkpoint
        state.dctwinConfig.activeTab = tab;
        _applyLeftTabUI();
        if (tab === 'mesh') {
            window.runAutoDetectRackRefinement();
        }
    };
```

- [ ] **Step 2: Auto-exit when target rack is deleted**

Find the model deletion function. Search for `window.deleteModel` or `deleteSelectedModels`. Add a guard at the start:

```javascript
    // Inside the delete function, after the model is removed from state.models:
    // Check if the deleted model was the rack detail target
    if (isRackDetailMode() && !getModelById(state.rackDetailTarget)) {
        state.rackDetailTarget = null;
        // Restore toolbar button
        var btn2d = document.querySelector('[data-i18n="btn.room_2d_editor"]');
        if (btn2d) {
            btn2d.innerHTML = _rackDetailSavedBtnHTML;
            btn2d.onclick = _rackDetailSavedBtnOnclick;
        }
    }
```

Find the exact location of the delete handler by searching for `deleteModel` or `removeModel`. Add the guard after the model is spliced from `state.models` but before `rebuildGeometry()` is called. The `rebuildGeometry()` call will then dispatch to `doRebuildRackDetail()` which checks for invalid target and falls back to `doRebuildGeometry()`.

- [ ] **Step 3: Test**

1. Enter rack detail → switch to mesh tab → should exit detail first
2. Enter rack detail → delete the rack from model tree → should auto-exit to 3D

- [ ] **Step 4: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail): guard tab switch and model deletion"
```

---

### Task 7: Disable model drag in rack detail mode

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (drag handler, near `beginModelMoveDrag`)

- [ ] **Step 1: Block drag initiation in detail mode**

Find `beginModelMoveDrag` (search for the function name). Add a guard at the top:

```javascript
    // At the beginning of beginModelMoveDrag or the drag start handler:
    if (isRackDetailMode()) return; // Rack position is fixed in detail view
```

This prevents the user from accidentally moving the rack while in the detail view.

- [ ] **Step 2: Test**

1. Enter rack detail → try to drag the rack → nothing happens
2. Exit detail → drag works normally

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail): disable model drag in detail view"
```

---

### Task 8: End-to-end integration test

- [ ] **Step 1: Full flow test from 3D**

1. Open app, create room + 2 racks + 1 ACU
2. Select Rack 1 → click "Rack 细节设计"
3. Verify: only Rack 1 visible, camera on inlet face
4. Verify: toolbar shows "↩ 返回 3D 界面"
5. Verify: right panel shows detail panel with summary + context toggle
6. Toggle context ON → other models appear as ghosts
7. Click a U-slot to delete → works, rack re-renders in isolation
8. Add a fan-server → works
9. Ctrl+Z → undo works
10. Rotate/zoom camera → OrbitControls work
11. Click "返回 3D 界面" → full 3D view restored, camera restored

- [ ] **Step 2: Full flow test from 2D**

1. Enter 2D floor plan editor
2. Click on a rack in the model tree (or select in 2D)
3. Click "Rack 细节设计" in right panel
4. Verify: toolbar shows "↩ 返回 2D 界面"
5. Click "返回 2D 界面" → 2D editor restored

- [ ] **Step 3: Edge cases**

1. Delete target rack while in detail → auto-exits to 3D
2. Switch to mesh tab while in detail → exits detail first
3. Enter detail → toggle context → exit → re-enter → context is OFF (reset)
4. Try dragging rack in detail → blocked

- [ ] **Step 4: Final commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail): P1 complete — Rack Detail view mode shell"
```

---

## Self-Review

**Spec coverage:**
- §1 State Variables → Task 1 ✓
- §2 Entry Flow → Task 3 ✓
- §3 Exit Flow → Task 3 ✓
- §4 Toolbar Button → Task 3 ✓
- §5 Right Panel → Task 5 ✓
- §6 Scene Rendering → Task 2 ✓
- §7 Camera Fit → Task 3 ✓
- §8 Left Panel → No changes needed ✓
- §9 Interactions (tab switch, model delete, drag disable) → Tasks 6, 7 ✓
- Test Plan → Task 8 ✓

**Placeholder scan:** All code blocks contain complete implementations. No TBD/TODO.

**Type consistency:** `isRackDetailMode()` used consistently across all tasks. State property names (`rackDetailTarget`, `rackDetailReturnMode`, `rackDetailShowContext`) match throughout. `_fitCameraToRack` and `doRebuildRackDetail` signatures consistent.
