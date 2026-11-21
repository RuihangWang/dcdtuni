# Liquid-Cooled Rack Manifold 3D Rendering — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add liquid cooling rack support: rack `coolingType` switch, vertical manifold pipe rendering, server connection stubs, and manifold layout options (both-left / both-right / split).

**Architecture:** Manifold pipes are Three.js child meshes of the rack geometry group (Approach A). Data stored in `rack.coolingType` + `rack.liquidManifold`. All changes in `STL_generate.html` (single monolithic file). No new JS files.

**Tech Stack:** Three.js r128, vanilla JS, existing `STL_generate.html` structure

---

### Task 1: Data Model — `coolingType` and `liquidManifold` defaults

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `getDefaultRackState()` at line 2224, and `normalizeModel()` at ~line 3200

- [ ] **Step 1: Add `coolingType` and `liquidManifold` to `getDefaultRackState()`**

Find the function `getDefaultRackState()` (line 2224) and add two new fields at the end of the returned object, before the closing `}`:

```js
// In getDefaultRackState(), add after blankingPanelType:
            blankingPanelType: 'typicalSmallGap',
            coolingType: 'air',
            liquidManifold: {
                layout: 'both-left',
                supplyPipe: { diameter: 0.025, offsetFromInlet: 0.10 },
                returnPipe: { diameter: 0.025, offsetFromInlet: 0.10 },
                pipeSpacing: 0.015
            }
```

- [ ] **Step 2: Ensure `normalizeModel()` preserves `coolingType` and `liquidManifold` for loaded projects**

Find the block in `normalizeModel()` where `model.kind === 'rack'` is handled (around line 3246). The `rack` object is already restored from `rawModel.rack`. Since `getDefaultRackState()` provides defaults and the existing code does `Object.assign({}, getDefaultRackState(), rawModel.rack)`, the new fields will automatically get defaults when loading old projects that don't have them. Verify this by searching for the `Object.assign` pattern:

```bash
grep -n "getDefaultRackState" STLGeneratorForOpenFoam/web/STL_generate.html
```

If the `Object.assign` pattern is used, no additional code is needed — the defaults propagate automatically. If raw assignment is used instead, add fallback:

```js
// Only if needed (check existing code pattern):
if (!rack.coolingType) rack.coolingType = 'air';
if (!rack.liquidManifold) rack.liquidManifold = getDefaultRackState().liquidManifold;
```

- [ ] **Step 3: Verify via browser-use**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "var m = window.createDefaultBaseModel ? createRackModel('TestRack') : null; JSON.stringify({ct: m.rack.coolingType, lm: m.rack.liquidManifold})"
browser-use screenshot /tmp/task1_verify.png
```

Expected: `{"ct":"air","lm":{"layout":"both-left","supplyPipe":{"diameter":0.025,"offsetFromInlet":0.1},...}}`

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(liquid): add coolingType and liquidManifold to rack data model"
```

---

### Task 2: UI — Cooling Type dropdown in outer rack inspector

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `renderRackModelInspector()` at line 10483

- [ ] **Step 1: Add cooling type dropdown above the "Rack Detail Editor" button**

In `renderRackModelInspector()` (line 10483), find the first `prop-group` div that contains the "Rack Detail Editor" button (line 10493-10495). Insert a new `prop-group` **before** it:

```js
    function renderRackModelInspector(prop, model) {
        if (isRackDetailMode() && state.rackDetailTarget === model.id) {
            _renderRackDetailPanel(prop, model);
            return;
        }

        const rack = model.rack;
        const coolingType = rack.coolingType || 'air';
        const modelOpacityPercent = opacityToPercent(model.opacity ?? 1);
        prop.innerHTML = `
            <div class="prop-group" style="border-color:#00b4dc;">
                <div class="prop-title" style="color:#00b4dc;">🌊 冷却类型 / Cooling Type</div>
                <div class="prop-row">
                    <label>类型 / Type</label>
                    <select onchange="window.setRackCoolingType('${model.id}', this.value)" style="width:140px;">
                        <option value="air" ${coolingType === 'air' ? 'selected' : ''}>风冷机柜 (Air)</option>
                        <option value="liquid" ${coolingType === 'liquid' ? 'selected' : ''}>液冷机柜 (Liquid)</option>
                    </select>
                </div>
            </div>
            <div class="prop-group" style="border-color:#ff8c00;">
                <button class="btn" style="width:100%; padding:8px; ..." onclick="window.enterRackDetail('${model.id}')">🔧 Rack 细节设计 / Rack Detail Editor</button>
            </div>
            ... rest of existing inspector HTML ...
        `;
    }
```

Keep all existing HTML after the detail editor button unchanged.

- [ ] **Step 2: Implement `window.setRackCoolingType()` function**

Add this function near the other rack mutation functions (around line 10400-10480):

```js
    window.setRackCoolingType = function(modelId, coolingType) {
        pushUndoCheckpoint();
        var model = state.models.find(m => m.id === modelId);
        if (!model || model.kind !== 'rack') return;
        model.rack.coolingType = coolingType;
        if (coolingType === 'liquid' && !model.rack.liquidManifold) {
            model.rack.liquidManifold = getDefaultRackState().liquidManifold;
        }
        rebuildGeometry();
        updateUI();
        logOperation('setRackCoolingType', 'Set rack cooling type to ' + coolingType,
            { modelId, coolingType }, 'modify');
    };
```

- [ ] **Step 3: Verify via browser-use**

```bash
browser-use open http://localhost:8005/STL_generate.html
# Create a rack, select it, screenshot the right panel
browser-use eval "window.createDefaultBaseModel(); setTimeout(function(){}, 500)"
browser-use screenshot /tmp/task2_before.png
# Then check the dropdown exists in the inspector
browser-use state
```

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(liquid): add cooling type dropdown to rack inspector panel"
```

---

### Task 3: 3D Rendering — Manifold pipes as rack child meshes

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — add `_renderLiquidManifold()` function, call it from `renderRackModel()`

- [ ] **Step 1: Add `_renderLiquidManifold()` function**

Add this function right after `renderRackModel()` (after ~line 8500). This function creates two vertical cylinder meshes (supply blue, return red) as children of the rack's Three.js group:

```js
    function _renderLiquidManifold(model, modelGroup) {
        var rack = model.rack;
        if (rack.coolingType !== 'liquid') return;
        var mfld = rack.liquidManifold;
        if (!mfld) return;

        var W = model.base.W;
        var D = model.base.D;
        var rackH = model.base.H;
        var slotH = rack.unitCount * rack.unitHeight;
        var marginBot = rack.safeMarginBottom;
        var pipeH = slotH;
        var supplyR = (mfld.supplyPipe.diameter || 0.025) / 2;
        var returnR = (mfld.returnPipe.diameter || 0.025) / 2;
        var offset = mfld.supplyPipe.offsetFromInlet || 0.10;
        var spacing = mfld.pipeSpacing || 0.015;
        var layout = mfld.layout || 'both-left';

        var supplyMat = new THREE.MeshStandardMaterial({
            color: 0x2196F3, metalness: 0.6, roughness: 0.3,
            transparent: true, opacity: 0.85,
            emissive: 0x2196F3, emissiveIntensity: 0.15
        });
        var returnMat = new THREE.MeshStandardMaterial({
            color: 0xF44336, metalness: 0.6, roughness: 0.3,
            transparent: true, opacity: 0.85,
            emissive: 0xF44336, emissiveIntensity: 0.15
        });

        var supplyGeo = new THREE.CylinderGeometry(supplyR, supplyR, pipeH, 16);
        var returnGeo = new THREE.CylinderGeometry(returnR, returnR, pipeH, 16);
        var supplyMesh = new THREE.Mesh(supplyGeo, supplyMat);
        var returnMesh = new THREE.Mesh(returnGeo, returnMat);

        // Y position: center of the slot area
        var pipeY = marginBot + pipeH / 2;

        // X/Z position depends on layout
        // Rack coordinate system: X = width, Y = height, Z = depth
        // Inlet face is at Z = 0; offset goes into the rack (positive Z)
        var pipeZ = offset;
        var leftX = supplyR + 0.01;          // left edge + small margin
        var rightX = W - returnR - 0.01;     // right edge - small margin

        if (layout === 'both-left') {
            supplyMesh.position.set(leftX, pipeY, pipeZ);
            returnMesh.position.set(leftX + spacing + supplyR + returnR, pipeY, pipeZ);
        } else if (layout === 'both-right') {
            returnMesh.position.set(rightX, pipeY, pipeZ);
            supplyMesh.position.set(rightX - spacing - supplyR - returnR, pipeY, pipeZ);
        } else if (layout === 'split') {
            supplyMesh.position.set(leftX, pipeY, pipeZ);
            returnMesh.position.set(rightX, pipeY, pipeZ);
        }

        supplyMesh.userData = { isLiquidManifold: true, pipeType: 'supply', modelId: model.id };
        returnMesh.userData = { isLiquidManifold: true, pipeType: 'return', modelId: model.id };
        supplyMesh.name = 'liquid_manifold_supply';
        returnMesh.name = 'liquid_manifold_return';

        modelGroup.add(supplyMesh);
        modelGroup.add(returnMesh);
    }
```

- [ ] **Step 2: Call `_renderLiquidManifold()` at the end of `renderRackModel()`**

Find the end of `renderRackModel()` function. Add the call just before the function's closing brace:

```js
        // ... existing renderRackModel code ...

        // Liquid cooling manifold overlay
        _renderLiquidManifold(model, modelGroup);
    }  // end of renderRackModel
```

- [ ] **Step 3: Verify via browser-use — switch to liquid and see manifolds**

```bash
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "window.createDefaultBaseModel()"
# Select the rack, change to liquid cooling
browser-use eval "var m = state.models.find(m => m.kind === 'rack'); m.rack.coolingType = 'liquid'; rebuildGeometry(); updateUI();"
browser-use screenshot /tmp/task3_manifold.png
```

Expected: Two vertical colored pipes visible inside the rack.

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(liquid): render vertical manifold pipes in liquid-cooled racks"
```

---

### Task 4: 3D Rendering — Server connection stubs

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — extend `_renderLiquidManifold()`

- [ ] **Step 1: Add connection stub rendering after manifold pipes**

Extend `_renderLiquidManifold()` — after adding the two manifold meshes, iterate over `rack.equipments` and add horizontal connection stubs:

```js
        // --- Connection stubs from each equipment to manifold ---
        var stubR = 0.004;  // 8mm diameter = 4mm radius
        var stubMat_supply = new THREE.MeshStandardMaterial({
            color: 0x2196F3, metalness: 0.4, roughness: 0.4,
            transparent: true, opacity: 0.75
        });
        var stubMat_return = new THREE.MeshStandardMaterial({
            color: 0xF44336, metalness: 0.4, roughness: 0.4,
            transparent: true, opacity: 0.75
        });

        var slotW = W * rack.slotWidthRatio;
        var slotX = (W - slotW) / 2;

        (rack.equipments || []).forEach(function(eq) {
            var eqSpan = eq.spanUnits || 1;
            var eqBot = marginBot + (eq.startUnit - 1) * rack.unitHeight;
            var eqMidY = eqBot + (eqSpan * rack.unitHeight) / 2;

            // Supply stub: horizontal cylinder from manifold to equipment edge
            var supplyPos = supplyMesh.position;
            var supplyStubLen = Math.abs(slotX - supplyPos.x);
            if (supplyStubLen > 0.001) {
                var sGeo = new THREE.CylinderGeometry(stubR, stubR, supplyStubLen, 8);
                sGeo.rotateZ(Math.PI / 2);  // rotate to horizontal (along X)
                var sMesh = new THREE.Mesh(sGeo, stubMat_supply);
                sMesh.position.set(
                    (supplyPos.x + slotX) / 2,
                    eqMidY,
                    pipeZ
                );
                sMesh.userData = { isLiquidManifold: true, stubType: 'supply', modelId: model.id };
                modelGroup.add(sMesh);
            }

            // Return stub: horizontal cylinder from manifold to equipment edge
            var returnPos = returnMesh.position;
            var returnStubEndX = (layout === 'split') ? (slotX + slotW) : slotX;
            var returnStubLen = Math.abs(returnStubEndX - returnPos.x);
            if (returnStubLen > 0.001) {
                var rGeo = new THREE.CylinderGeometry(stubR, stubR, returnStubLen, 8);
                rGeo.rotateZ(Math.PI / 2);
                var rMesh = new THREE.Mesh(rGeo, stubMat_return);
                rMesh.position.set(
                    (returnPos.x + returnStubEndX) / 2,
                    eqMidY,
                    pipeZ
                );
                rMesh.userData = { isLiquidManifold: true, stubType: 'return', modelId: model.id };
                modelGroup.add(rMesh);
            }
        });
```

- [ ] **Step 2: Verify via browser-use — add equipment and see stubs**

```bash
browser-use eval "var m = state.models.find(m => m.kind === 'rack'); m.rack.coolingType = 'liquid'; window.enterRackDetail(m.id);"
browser-use screenshot /tmp/task4_detail_view.png
# Should see manifolds + stubs connecting to each server
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(liquid): render server connection stubs to manifold pipes"
```

---

### Task 5: UI — Manifold layout dropdown in rack detail panel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `_renderRackDetailPanel()` at line 10338

- [ ] **Step 1: Add manifold layout dropdown to rack detail panel**

In `_renderRackDetailPanel()`, find the display options `prop-group` (around line 10359). Add a new `prop-group` **after** it, that only renders when `rack.coolingType === 'liquid'`:

```js
        // After the display options group, add:
        var _lmHtml = '';
        if (rack.coolingType === 'liquid') {
            var lm = rack.liquidManifold || {};
            var layout = lm.layout || 'both-left';
            _lmHtml = `
            <div class="prop-group" style="border-color:#00b4dc;">
                <div class="prop-title" style="color:#00b4dc;">🌊 歧管布局 / Manifold Layout</div>
                <div class="prop-row">
                    <label>布局 / Layout</label>
                    <select onchange="window.setManifoldLayout('${model.id}', this.value)" style="width:160px;">
                        <option value="both-left" ${layout === 'both-left' ? 'selected' : ''}>左侧并排 (Both Left)</option>
                        <option value="both-right" ${layout === 'both-right' ? 'selected' : ''}>右侧并排 (Both Right)</option>
                        <option value="split" ${layout === 'split' ? 'selected' : ''}>左冷右热 (Split)</option>
                    </select>
                </div>
                <div class="prop-row">
                    <label>管径 / Diameter (mm)</label>
                    <input type="number" value="${((lm.supplyPipe && lm.supplyPipe.diameter) || 0.025) * 1000}" step="1" min="10" max="50" style="width:60px;"
                           onchange="window.setManifoldParam('${model.id}', 'diameter', parseFloat(this.value) / 1000)">
                </div>
                <div class="prop-row">
                    <label>偏移 / Offset (mm)</label>
                    <input type="number" value="${((lm.supplyPipe && lm.supplyPipe.offsetFromInlet) || 0.10) * 1000}" step="5" min="30" max="200" style="width:60px;"
                           onchange="window.setManifoldParam('${model.id}', 'offset', parseFloat(this.value) / 1000)">
                </div>
            </div>`;
        }
```

Then insert `${_lmHtml}` into the `prop.innerHTML` template string after the display options group.

- [ ] **Step 2: Implement `window.setManifoldLayout()` and `window.setManifoldParam()`**

```js
    window.setManifoldLayout = function(modelId, layout) {
        pushUndoCheckpoint();
        var model = state.models.find(m => m.id === modelId);
        if (!model || model.kind !== 'rack') return;
        if (!model.rack.liquidManifold) model.rack.liquidManifold = getDefaultRackState().liquidManifold;
        model.rack.liquidManifold.layout = layout;
        rebuildGeometry();
        updateUI();
        logOperation('setManifoldLayout', 'Set manifold layout to ' + layout,
            { modelId, layout }, 'modify');
    };

    window.setManifoldParam = function(modelId, param, value) {
        pushUndoCheckpoint();
        var model = state.models.find(m => m.id === modelId);
        if (!model || model.kind !== 'rack' || !model.rack.liquidManifold) return;
        var lm = model.rack.liquidManifold;
        if (param === 'diameter') {
            lm.supplyPipe.diameter = value;
            lm.returnPipe.diameter = value;
        } else if (param === 'offset') {
            lm.supplyPipe.offsetFromInlet = value;
            lm.returnPipe.offsetFromInlet = value;
        }
        rebuildGeometry();
        updateUI();
        logOperation('setManifoldParam', 'Set manifold ' + param + ' to ' + value,
            { modelId, param, value }, 'modify');
    };
```

- [ ] **Step 3: Verify via browser-use — change layout and see pipes move**

```bash
# Enter rack detail mode with liquid cooling
browser-use eval "var m = state.models.find(m => m.kind === 'rack'); m.rack.coolingType = 'liquid'; window.enterRackDetail(m.id);"
browser-use screenshot /tmp/task5_both_left.png
# Change layout to split
browser-use eval "window.setManifoldLayout(state.rackDetailTarget, 'split')"
browser-use screenshot /tmp/task5_split.png
# Change layout to both-right
browser-use eval "window.setManifoldLayout(state.rackDetailTarget, 'both-right')"
browser-use screenshot /tmp/task5_both_right.png
```

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(liquid): add manifold layout dropdown with 3 options in rack detail panel"
```

---

### Task 6: STL Export exclusion — skip manifold meshes

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` — `appendMeshFacets()` or rack export logic

- [ ] **Step 1: Ensure manifold meshes are excluded from STL export**

Find the rack STL export function. The manifold meshes have `userData.isLiquidManifold = true`. In the export traversal (`exportMergedSTL` or `appendMeshFacets`), add a guard:

```js
// In the mesh traversal during export, skip liquid manifold meshes:
if (child.userData && child.userData.isLiquidManifold) return;
```

Search for the exact location:
```bash
grep -n "appendMeshFacets\|isRackEquipment\|objectKind.*rack" STLGeneratorForOpenFoam/web/STL_generate.html | head -20
```

The guard should be added where meshes are collected for STL output. Find the `traverse` or `forEach` loop that processes rack meshes and add the early return.

- [ ] **Step 2: Verify — export STL and confirm no manifold geometry**

```bash
browser-use eval "var m = state.models.find(m => m.kind === 'rack'); m.rack.coolingType = 'liquid'; rebuildGeometry();"
# Export and check that manifold names don't appear
browser-use eval "var stl = exportMergedSTL ? exportMergedSTL() : ''; stl.indexOf('liquid_manifold') >= 0 ? 'FAIL: manifold in STL' : 'OK: manifold excluded'"
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "fix(liquid): exclude manifold meshes from STL export"
```

---

### Task 7: Integration test — full workflow verification

- [ ] **Step 1: End-to-end browser-use test**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html

# 1. Create rack
browser-use eval "window.createDefaultBaseModel()"

# 2. Select rack, switch to liquid cooling
browser-use eval "var m = state.models.find(m => m.kind === 'rack'); window.setRackCoolingType(m.id, 'liquid')"
browser-use screenshot /tmp/task7_liquid_rack.png

# 3. Enter rack detail mode
browser-use eval "var m = state.models.find(m => m.kind === 'rack'); window.enterRackDetail(m.id)"
browser-use screenshot /tmp/task7_detail_manifold.png

# 4. Add a server equipment
browser-use eval "window.addEquipmentToRack && window.addEquipmentToRack(state.rackDetailTarget, {type: 'fan', startUnit: 1, spanUnits: 2})"
browser-use screenshot /tmp/task7_with_server.png

# 5. Switch layout to split
browser-use eval "window.setManifoldLayout(state.rackDetailTarget, 'split')"
browser-use screenshot /tmp/task7_split_layout.png

# 6. Switch back to air cooling — manifolds should disappear
browser-use eval "window.setRackCoolingType(state.rackDetailTarget, 'air')"
browser-use screenshot /tmp/task7_back_to_air.png

# 7. Undo — should restore liquid cooling
browser-use eval "window.undo()"
browser-use screenshot /tmp/task7_undo_restore.png

browser-use close
```

- [ ] **Step 2: Review all screenshots**

Read each screenshot file and verify:
- `task7_liquid_rack.png`: Blue/red pipes visible inside rack
- `task7_detail_manifold.png`: Manifolds in detail view + layout dropdown in right panel
- `task7_with_server.png`: Connection stubs from server to manifolds
- `task7_split_layout.png`: Supply on left, return on right
- `task7_back_to_air.png`: No manifold pipes visible
- `task7_undo_restore.png`: Manifold pipes restored after undo

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(liquid): liquid-cooled rack manifold 3D rendering complete"
```
