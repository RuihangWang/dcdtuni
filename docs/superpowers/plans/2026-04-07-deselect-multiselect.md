# Deselect on Blank + 2D Multi-Select Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Click blank deselects and disables all move/rotate tools. (2) 2D Ctrl/Shift multi-select with multi-model move and multi-model rotation (each around own Z axis).

**Architecture:** Modify `setSelection` to disable tools on deselect and remove auto-activate. Replace 2D's `_selectedModelId` scalar with reads from `state.selection.modelIds`. Extend 2D drag/rotate to handle multiple models. Extend 3D gizmo to render/drag for all selected models.

**Tech Stack:** Vanilla JS (STL_generate.html + room_2d_editor.js), Three.js r128

---

## File Structure

| File | Responsibility |
|------|---------------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | `setSelection` fix, 3D multi-model gizmo |
| `STLGeneratorForOpenFoam/web/room_2d_editor.js` | 2D multi-select, multi-model highlight/move/rotate |

---

### Task 1: Fix setSelection — Disable Tools on Deselect, Remove Auto-Activate

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Disable all tools when selecting 'project' (deselect)**

In `setSelection()` function (line ~5120), find the block that sets `state.selection = { ... }` (line ~5132-5147). BEFORE that assignment, add:

```javascript
        // Deselect: disable all move and rotate tools
        if (type === 'project') {
            disableAllModelMoveTools();
            state.models.forEach(function(m) { ensureModelRotateTool(m).enabled = false; });
        }
```

- [ ] **Step 2: Remove 2D auto-activate move tool**

Find and DELETE lines 5190-5199 (the block starting with `// In 2D mode: auto-enable move tool`):

```javascript
        // In 2D mode: auto-enable move tool for draggable models (rack/acu/box)
        // Move and rotate are mutually exclusive — only enable move by default
        if (window.room2d && room2d._scheduleRender && type === 'model' && modelId) {
            const _m2d = getModelById(modelId);
            if (_m2d && (new Set(['rack', 'acu', 'box'])).has(_m2d.kind)) {
                const _mt = ensureModelMoveTool(_m2d);
                _mt.enabled = true;
                _mt.axes = { x: true, y: true, z: false };
            }
        }
```

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam && git add web/STL_generate.html
git commit -m "fix: disable all move/rotate tools on blank click deselect, remove 2D auto-activate"
```

---

### Task 2: 2D Multi-Select — Replace _selectedModelId with state.selection.modelIds

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/room_2d_editor.js`

- [ ] **Step 1: Add helper function to check if a model is selected**

Near the top of the file (after line 37 where `_selectedModelId` is declared), add:

```javascript
    function _isModel2dSelected(modelId) {
        return state.selection && state.selection.type === 'model' &&
               Array.isArray(state.selection.modelIds) &&
               state.selection.modelIds.indexOf(modelId) >= 0;
    }
```

- [ ] **Step 2: Replace model click handler with multi-select logic**

Find the model hit handler in `_onMouseDown` (lines ~843-866). The current code:
```javascript
                if (hitModel) {
                    // Check if model is already selected AND move tool enabled → start drag
                    const moveTool = window.ensureModelMoveTool ? ensureModelMoveTool(m) : null;
                    if (_selectedModelId === m.id && moveTool && moveTool.enabled) {
                        _dragModel = { id: m.id, startX: w.x, startY: w.y, anchorStartX: m.anchor.x, anchorStartY: m.anchor.y };
                        _scheduleRender();
                        return;
                    }
                    // First click: select model, show full 3D inspector
                    _selectedModelId = m.id;
                    if (window.setSelection) setSelection('model', m.id);
                    _dragModel = null;
                    _selectedVertex = -1;
                    _selectedEdge = -1;
                    _boxSelectedVertices.clear();
                    _boxSelectedEdges.clear();
                    _scheduleRender();
                    // Auto-enable move tool for 2D: X/Y enabled, Z disabled
                    if (moveTool) {
                        moveTool.enabled = true;
                        moveTool.axes = { x: true, y: true, z: false };
                    }
                    return;
                }
```

Replace with:
```javascript
                if (hitModel) {
                    var moveTool = window.ensureModelMoveTool ? ensureModelMoveTool(m) : null;
                    // Already selected with move enabled → start drag
                    if (_isModel2dSelected(m.id) && moveTool && moveTool.enabled) {
                        // Collect all selected models with move enabled for multi-drag
                        var _selIds = window.getSelectedModelIds ? getSelectedModelIds() : [m.id];
                        _dragModels = _selIds.filter(function(id) {
                            var mm = window.getModelById ? getModelById(id) : null;
                            if (!mm) return false;
                            var mt = window.ensureModelMoveTool ? ensureModelMoveTool(mm) : null;
                            return mt && mt.enabled;
                        }).map(function(id) {
                            var mm = getModelById(id);
                            return { id: id, anchorStartX: mm.anchor.x, anchorStartY: mm.anchor.y };
                        });
                        _dragStart2d = { x: w.x, y: w.y };
                        _dragModel = null;
                        _scheduleRender();
                        return;
                    }
                    // Ctrl/Shift: toggle multi-select
                    if (e.ctrlKey || e.metaKey || e.shiftKey) {
                        var currentIds = window.getSelectedModelIds ? getSelectedModelIds() : [];
                        var idSet = new Set(currentIds);
                        if (idSet.has(m.id)) { idSet.delete(m.id); } else { idSet.add(m.id); }
                        var nextIds = state.models.map(function(x) { return x.id; }).filter(function(id) { return idSet.has(id); });
                        var anchor = (state.selection && state.selection.anchorModelId) || m.id;
                        if (window.setSelection) setSelection('model', nextIds[0] || null, null, null, null, null, null, null, null, null, null, null, nextIds, anchor);
                    } else {
                        // Single select (no modifier)
                        if (window.setSelection) setSelection('model', m.id, null, null, null, null, null, null, null, null, null, null, [m.id], m.id);
                    }
                    _dragModel = null;
                    _dragModels = null;
                    _selectedVertex = -1;
                    _selectedEdge = -1;
                    _boxSelectedVertices.clear();
                    _boxSelectedEdges.clear();
                    _scheduleRender();
                    return;
                }
```

- [ ] **Step 3: Add new variables for multi-drag**

Near `_selectedModelId` declaration (line 37), add:

```javascript
    let _dragModels = null;   // [{id, anchorStartX, anchorStartY}] for multi-model drag
    let _dragStart2d = null;  // {x, y} world coords at drag start
```

- [ ] **Step 4: Replace deselect-on-blank to not use _selectedModelId**

Find the two deselect blocks that reference `_selectedModelId`:

Block 1 (line ~868-874):
```javascript
            if (_selectedModelId) {
                _selectedModelId = null;
                if (window.setSelection) setSelection('project');
                _scheduleRender();
                _updateRightPanel();
            }
```

Replace with:
```javascript
            if (window.getSelectedModelIds && getSelectedModelIds().length) {
                if (window.setSelection) setSelection('project');
                _scheduleRender();
                _updateRightPanel();
            }
```

Block 2 (line ~909-912):
```javascript
            if (_selectedModelId) {
                _selectedModelId = null;
                if (window.setSelection) setSelection('project');
            }
```

Replace with:
```javascript
            if (window.getSelectedModelIds && getSelectedModelIds().length) {
                if (window.setSelection) setSelection('project');
            }
```

- [ ] **Step 5: Update highlight rendering in _draw() to use multi-select**

Find the highlight block (line ~578-593):
```javascript
            if (_selectedModelId === m.id) {
```

Replace with:
```javascript
            if (_isModel2dSelected(m.id)) {
```

Also find the label color (line ~599):
```javascript
                ctx.fillStyle = _selectedModelId === m.id ? '#ffcc00' : border;
```

Replace with:
```javascript
                ctx.fillStyle = _isModel2dSelected(m.id) ? '#ffcc00' : border;
```

- [ ] **Step 6: Update multi-model drag in _onMouseMove**

Find the single-model drag block (line ~992):
```javascript
        if (_dragModel) {
```

BEFORE that block, add the multi-drag handler:
```javascript
        if (_dragModels && _dragModels.length && _dragStart2d) {
            if (!_dragModels._undoPushed) {
                if (window.pushUndoCheckpoint) pushUndoCheckpoint('2dMoveMultiModel');
                _dragModels._undoPushed = true;
            }
            var dx = w.x - _dragStart2d.x, dy = w.y - _dragStart2d.y;
            _dragModels.forEach(function(dm) {
                var mm = window.getModelById ? getModelById(dm.id) : null;
                if (!mm) return;
                var newX = dm.anchorStartX + dx, newY = dm.anchorStartY + dy;
                mm.anchor.x = newX;
                mm.anchor.y = newY;
                if (mm.rack && mm.rack.geometry) { mm.rack.geometry.anchor.x = newX; mm.rack.geometry.anchor.y = newY; }
                if (window.syncModelRenderTransform) syncModelRenderTransform(mm);
                if (window.invalidateBBoxCache) invalidateBBoxCache(mm.id);
            });
            if (window.updateUI) updateUI();
            _scheduleRender();
            return;
        }
```

- [ ] **Step 7: Update _onMouseUp to clear multi-drag**

Find where `_dragModel = null` is set on mouseup. Add `_dragModels = null; _dragStart2d = null;` next to it.

- [ ] **Step 8: Update rotation ring hit test for multi-select**

Find the rotation handle hit test loop (line ~796):
```javascript
            for (let _ri = 0; _ri < state.models.length; _ri++) {
```

This already iterates all models and checks `_getRotateRingParams(m)` which returns null unless rotateTool is enabled. So the ring hit test works for any model with rotate enabled. No change needed here — it already finds rings across all models.

- [ ] **Step 9: Update rotation drag to apply to all selected models**

Find the rotation apply block in `_onMouseMove` (line ~961-989). The current code rotates only `_rotateHandle.id`. After computing `_rfAngle` (the final angle), replace the single-model rotation block:

```javascript
            _rotateHandle.currentAngle = _rfAngle;
            const _rm = window.getModelById ? getModelById(_rotateHandle.id) : null;
            if (_rm) {
                const _totalDA = _rfAngle - _rotateHandle.startAngle;
                const _dq = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), _totalDA);
                const _sq = new THREE.Quaternion(_rotateHandle.startQuat.x, _rotateHandle.startQuat.y, _rotateHandle.startQuat.z, _rotateHandle.startQuat.w);
                const _nq = _dq.clone().multiply(_sq);
                _rm.transform.quaternion = { x: _nq.x, y: _nq.y, z: _nq.z, w: _nq.w };
                const _cos = Math.cos(_totalDA), _sin = Math.sin(_totalDA);
                const _adx = _rotateHandle.startAnchorX - _rotateHandle.centroidX;
                const _ady = _rotateHandle.startAnchorY - _rotateHandle.centroidY;
                _rm.anchor.x = _rotateHandle.centroidX + _adx * _cos - _ady * _sin;
                _rm.anchor.y = _rotateHandle.centroidY + _adx * _sin + _ady * _cos;
                if (window.syncModelRenderTransform) syncModelRenderTransform(_rm);
                if (window.invalidateBBoxCache) invalidateBBoxCache(_rm.id);
                if (window.updateUI) updateUI();
            }
```

With multi-model rotation:

```javascript
            _rotateHandle.currentAngle = _rfAngle;
            var _totalDA = _rfAngle - _rotateHandle.startAngle;
            // Apply rotation to all selected models with rotate enabled
            var _rotateTargets = _rotateHandle._targets || [_rotateHandle];
            _rotateTargets.forEach(function(tgt) {
                var _rm = window.getModelById ? getModelById(tgt.id) : null;
                if (!_rm) return;
                var _dq = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), _totalDA);
                var _sq = new THREE.Quaternion(tgt.startQuat.x, tgt.startQuat.y, tgt.startQuat.z, tgt.startQuat.w);
                var _nq = _dq.clone().multiply(_sq);
                _rm.transform.quaternion = { x: _nq.x, y: _nq.y, z: _nq.z, w: _nq.w };
                var _cos = Math.cos(_totalDA), _sin = Math.sin(_totalDA);
                var _adx = tgt.startAnchorX - tgt.centroidX;
                var _ady = tgt.startAnchorY - tgt.centroidY;
                _rm.anchor.x = tgt.centroidX + _adx * _cos - _ady * _sin;
                _rm.anchor.y = tgt.centroidY + _adx * _sin + _ady * _cos;
                if (window.syncModelRenderTransform) syncModelRenderTransform(_rm);
                if (window.invalidateBBoxCache) invalidateBBoxCache(_rm.id);
            });
            if (window.updateUI) updateUI();
```

- [ ] **Step 10: Build multi-target list when starting rotation drag**

In the rotation handle hit test (line ~803-818), after creating `_rotateHandle`, add target list:

After `_rotateHandle = { id: _rm.id, ... };`, add:
```javascript
                    // Collect all selected models with rotate enabled for multi-rotate
                    var _selRotateIds = (window.getSelectedModelIds ? getSelectedModelIds() : [_rm.id])
                        .filter(function(id) {
                            var mm = window.getModelById ? getModelById(id) : null;
                            if (!mm) return false;
                            var rt = window.ensureModelRotateTool ? ensureModelRotateTool(mm) : null;
                            return rt && rt.enabled && rt.axes && rt.axes.yaw;
                        });
                    _rotateHandle._targets = _selRotateIds.map(function(id) {
                        var mm = getModelById(id);
                        var _robb = window.getModelOBB ? getModelOBB(mm) : null;
                        return {
                            id: id,
                            centroidX: _robb ? _robb.center.x : mm.anchor.x,
                            centroidY: _robb ? _robb.center.y : mm.anchor.y,
                            startQuat: { ...mm.transform.quaternion },
                            startAnchorX: mm.anchor.x,
                            startAnchorY: mm.anchor.y
                        };
                    });
```

- [ ] **Step 11: Remove all remaining references to _selectedModelId**

Search for any remaining `_selectedModelId` usages and replace:
- Declaration `let _selectedModelId = null;` → can keep but mark as deprecated, or remove entirely if no other references remain after steps 2-5.

- [ ] **Step 12: Commit**

```bash
cd STLGeneratorForOpenFoam && git add web/room_2d_editor.js
git commit -m "feat: 2D multi-select with Ctrl/Shift, multi-model move and rotate"
```

---

### Task 3: 3D Multi-Model Rotation Gizmo

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Extend refreshRotationGizmo to handle multiple models**

Find `refreshRotationGizmo()` function. Current code:
```javascript
    function refreshRotationGizmo() {
        const model = getModelById(state.selection.modelId);
        if (model && ensureModelRotateTool(model).enabled) {
            createRotationGizmo(model);
        } else {
            destroyRotationGizmo();
        }
    }
```

Replace with:
```javascript
    function refreshRotationGizmo() {
        destroyRotationGizmo();
        var ids = getSelectedModelIds();
        ids.forEach(function(id) {
            var model = getModelById(id);
            if (model && ensureModelRotateTool(model).enabled) {
                createRotationGizmo(model);
            }
        });
    }
```

This requires `createRotationGizmo` to support being called multiple times (additive, not replacing). Check if `destroyRotationGizmo` cleans up all gizmos, then each `createRotationGizmo` call adds one.

- [ ] **Step 2: Update createRotationGizmo to be additive**

Current `createRotationGizmo` likely destroys old gizmo first. Change it to NOT destroy — just add a new ring mesh group to the scene, tagged with `userData.modelId`. `destroyRotationGizmo` should remove ALL gizmo meshes (find by tag or use an array).

If the current implementation stores gizmo meshes in a single variable (e.g., `_rotationGizmoGroup`), change to an array `_rotationGizmoGroups = []`.

- [ ] **Step 3: Update 3D rotation gizmo drag to apply to all selected models**

In `beginRotationGizmoDrag` and `updateRotationGizmoDrag`, extend to apply angle delta to all selected models with rotate enabled (same logic as 2D Task 2 Step 9).

- [ ] **Step 4: Commit**

```bash
cd STLGeneratorForOpenFoam && git add web/STL_generate.html
git commit -m "feat: 3D multi-model rotation gizmo for all selected models"
```

---

### Task 4: End-to-End Verification

- [ ] **Step 1: Restart server and open browser**

- [ ] **Step 2: Test Bug 1 — Click blank deselects**

1. Select a model in 3D → enable move tool → click blank → verify: model deselected, move button inactive
2. Select a model in 3D → enable rotate tool → click blank → verify: model deselected, gizmo gone, rotate button inactive
3. Select a model in 2D → click blank → verify: highlight gone, no tools active
4. After deselect, click model again → verify: move tool NOT auto-activated (user must click button)

- [ ] **Step 3: Test Feature 2 — 2D Multi-Select**

1. In 2D, click model A → single select highlighted
2. Ctrl+click model B → both A and B highlighted
3. Ctrl+click A again → A deselected, only B remains
4. Shift+click C → B and C highlighted
5. Click blank → all deselected

- [ ] **Step 4: Test Multi-Model Move**

1. Multi-select 2 models (Ctrl+click)
2. Enable move tool from right panel
3. Drag one model → both move together with same delta

- [ ] **Step 5: Test Multi-Model Rotate**

1. Multi-select 2 models
2. Enable rotate tool
3. Verify both models show rotation rings
4. Drag one ring → both models rotate by same angle, each around own Z center
5. Check right panel shows yaw update during drag

- [ ] **Step 6: Test 3D Multi-Select + Rotate**

1. Ctrl+click 2 models in 3D
2. Enable rotate tool
3. Verify both models show gizmo rings
4. Drag one ring → both rotate
