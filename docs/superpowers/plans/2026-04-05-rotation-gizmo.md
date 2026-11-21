# Free Rotation System (Phase 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add rotation gizmo with edge-parallel snap for 2D mode, rotation angle display/input in Inspector, and rotation snap toolbar toggle.

**Architecture:** Add `rotateTool` to model (parallel to `moveTool`). Inspector shows rotate button + Yaw/Pitch/Roll controls + angle display. 2D editor draws rotation handle at model centroid, drag rotates around centroid (updating both quaternion and anchor). Rotation snap checks edge parallelism against all scene models.

**Tech Stack:** Three.js r128 (Quaternion, Euler), Canvas 2D API, polygon-clipping

**Spec:** `docs/superpowers/specs/2026-04-05-rotation-gizmo-design.md`

---

### Task 1: Data model — rotateTool + Euler conversion helpers

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add `ensureModelRotateTool` and Euler helpers after `ensureModelMoveTool` (~line 2002)**

```javascript
    function getDefaultModelRotateTool() {
        return { enabled: false, axes: { yaw: true, pitch: false, roll: false } };
    }
    function cloneModelRotateTool(raw = {}) {
        return {
            enabled: !!raw.enabled,
            axes: { yaw: raw.axes?.yaw !== false, pitch: !!raw.axes?.pitch, roll: !!raw.axes?.roll }
        };
    }
    function ensureModelRotateTool(model) {
        if (!model.rotateTool) model.rotateTool = getDefaultModelRotateTool();
        model.rotateTool = cloneModelRotateTool(model.rotateTool);
        return model.rotateTool;
    }

    function quaternionToEulerDeg(q) {
        const euler = new THREE.Euler().setFromQuaternion(
            new THREE.Quaternion(q.x || 0, q.y || 0, q.z || 0, q.w || 1), 'ZXY'
        );
        return {
            yaw: THREE.MathUtils.radToDeg(euler.z),
            pitch: THREE.MathUtils.radToDeg(euler.x),
            roll: THREE.MathUtils.radToDeg(euler.y)
        };
    }

    function eulerDegToQuaternion(yaw, pitch, roll) {
        const euler = new THREE.Euler(
            THREE.MathUtils.degToRad(pitch),
            THREE.MathUtils.degToRad(roll),
            THREE.MathUtils.degToRad(yaw),
            'ZXY'
        );
        const q = new THREE.Quaternion().setFromEuler(euler);
        return { x: q.x, y: q.y, z: q.z, w: q.w };
    }
```

- [ ] **Step 2: Add `rotateTool` preservation in `createModelFromRaw` (~line 2347)**

After the `isRoomBoundary:` line, add:
```javascript
            rotateTool: rawModel.rotateTool ? cloneModelRotateTool(rawModel.rotateTool) : null,
```

- [ ] **Step 3: Add toggle and angle update window functions**

After `window.toggleModelMoveTool`:
```javascript
    window.toggleModelRotateTool = function(modelId) {
        pushUndoCheckpoint('toggleModelRotateTool');
        const model = getModelById(modelId);
        if (!model) return;
        const tool = ensureModelRotateTool(model);
        tool.enabled = !tool.enabled;
        updateUI();
        if (window.requestRender) window.requestRender();
        logOperation('toggleModelRotateTool', 'Toggle model rotate tool', { modelId, enabled: tool.enabled }, 'modify');
    };

    window.setModelRotateAxis = function(modelId, axis, checked) {
        const model = getModelById(modelId);
        if (!model) return;
        ensureModelRotateTool(model).axes[axis] = !!checked;
        updateUI();
    };

    window.updateModelRotation = function(modelId, axis, value) {
        pushUndoCheckpoint('updateModelRotation');
        const model = getModelById(modelId);
        if (!model) return;
        const t = ensureModelTransform(model);
        const euler = quaternionToEulerDeg(t.quaternion);
        euler[axis] = parseFloat(value) || 0;
        model.transform.quaternion = eulerDegToQuaternion(euler.yaw, euler.pitch, euler.roll);
        syncModelRenderTransform(model);
        rebuildSingleModel(model.id);
        updateUI();
        if (window.requestRender) window.requestRender();
        logOperation('updateModelRotation', 'Update rotation ' + axis, { modelId, value }, 'modify');
    };
```

- [ ] **Step 4: Add i18n keys**

In `web/i18n/zh-CN.json`:
```json
  "btn.enable_rotate": "启用旋转",
  "btn.stop_rotate": "停止旋转",
  "label.rotation_angles": "🔄 旋转角度",
  "label.yaw": "偏航 (Z轴)",
  "label.pitch": "俯仰 (X轴)",
  "label.roll": "翻滚 (Y轴)",
  "label.rotation_snap": "旋转吸附",
  "hint.rotation_snap": "旋转时吸附使面/边平行于最近的物体",
```

In `web/i18n/en-US.json`:
```json
  "btn.enable_rotate": "Enable Rotate",
  "btn.stop_rotate": "Stop Rotate",
  "label.rotation_angles": "🔄 Rotation",
  "label.yaw": "Yaw (Z)",
  "label.pitch": "Pitch (X)",
  "label.roll": "Roll (Y)",
  "label.rotation_snap": "Rotation Snap",
  "hint.rotation_snap": "Snap rotation to align edge parallel to nearest object",
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(rotation): data model, Euler helpers, toggle/update functions, i18n"
```

---

### Task 2: Inspector UI — rotate button + axis controls + angle display

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add rotate button next to move button in `anchorControlsForModel` (~line 7229)**

Find the move button prop-row. After it, add the rotate button:

```javascript
            // Rotate tool button (next to move)
            const rotateTool = ensureModelRotateTool(model);
            html += '<div class="prop-row"><label>${t('btn.enable_rotate')}</label><button class="btn ${rotateTool.enabled ? 'primary' : ''}" style="width:160px; margin:0" onclick="toggleModelRotateTool('${model.id}')">${rotateTool.enabled ? t('btn.stop_rotate') : t('btn.enable_rotate')}</button></div>';
```

Note: The actual implementation must use string concatenation (not template literals) matching the existing pattern in `anchorControlsForModel`.

- [ ] **Step 2: Add axis checkboxes when rotate tool is enabled**

After the rotate button, add Yaw/Pitch/Roll checkboxes:

```javascript
            if (rotateTool.enabled) {
                html += '<div class="prop-row"><label>' + t('label.rotation_angles') + '</label>'
                    + '<div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; width:45%; min-width:160px; justify-content:flex-end;">'
                    + '<label style="display:flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" ' + (rotateTool.axes.yaw ? 'checked' : '') + ' onchange="setModelRotateAxis(\'' + model.id + '\', \'yaw\', this.checked)">Yaw</label>'
                    + '<label style="display:flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" ' + (rotateTool.axes.pitch ? 'checked' : '') + ' onchange="setModelRotateAxis(\'' + model.id + '\', \'pitch\', this.checked)">Pitch</label>'
                    + '<label style="display:flex; align-items:center; gap:4px; cursor:pointer;"><input type="checkbox" ' + (rotateTool.axes.roll ? 'checked' : '') + ' onchange="setModelRotateAxis(\'' + model.id + '\', \'roll\', this.checked)">Roll</label>'
                    + '</div></div>';
            }
```

- [ ] **Step 3: Add rotation angle display/input after anchor coordinates (~line 7243)**

After the Z anchor input, add:

```javascript
            // Rotation angle display/input
            const _euler = quaternionToEulerDeg(ensureModelTransform(model).quaternion);
            html += '<div class="prop-group"><div class="prop-title">' + t('label.rotation_angles') + '</div>';
            html += '<div class="prop-row"><label>' + t('label.yaw') + '</label><input type="number" step="1" value="' + _euler.yaw.toFixed(1) + '" onchange="updateModelRotation(\'' + model.id + '\', \'yaw\', this.value)"> °</div>';
            html += '<div class="prop-row"><label>' + t('label.pitch') + '</label><input type="number" step="1" value="' + _euler.pitch.toFixed(1) + '" onchange="updateModelRotation(\'' + model.id + '\', \'pitch\', this.value)"> °</div>';
            html += '<div class="prop-row"><label>' + t('label.roll') + '</label><input type="number" step="1" value="' + _euler.roll.toFixed(1) + '" onchange="updateModelRotation(\'' + model.id + '\', \'roll\', this.value)"> °</div>';
            html += '</div>';
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(rotation): Inspector rotate button, axis controls, angle display"
```

---

### Task 3: 2D rotation handle — visual + drag interaction

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/room_2d_editor.js`

- [ ] **Step 1: Add rotation state variables**

Near other state declarations:
```javascript
    let _rotateHandle = null;  // { id, centroidX, centroidY, startAngle, currentAngle, startQuat, startAnchor }
    let _rotationSnap = false;
```

- [ ] **Step 2: Add rotation handle rendering in `_drawModelProjections`**

After the existing highlight/label code for each model, add:
```javascript
            // Rotation handle (when rotateTool enabled)
            const rt = m.rotateTool;
            if (rt && rt.enabled && rt.axes.yaw) {
                const centroid = { x: (visTL.x + visBR.x) / 2, y: (visTL.y + visBR.y) / 2 };
                const diagPx = Math.hypot(visBR.x - visTL.x, visBR.y - visTL.y);
                const radius = Math.max(25, diagPx * 0.6);
                
                // Current angle from quaternion
                const q = m.transform ? m.transform.quaternion : {x:0,y:0,z:0,w:1};
                const angle = 2 * Math.atan2(q.z || 0, q.w || 1);
                
                // Circle
                const isRotating = _rotateHandle && _rotateHandle.id === m.id;
                ctx.beginPath();
                ctx.arc(centroid.x, centroid.y, radius, 0, Math.PI * 2);
                ctx.strokeStyle = isRotating ? '#ffcc00' : 'rgba(0,212,255,0.4)';
                ctx.lineWidth = isRotating ? 2 : 1.5;
                ctx.setLineDash([4, 4]);
                ctx.stroke();
                ctx.setLineDash([]);
                
                // Drag handle point on ring
                const hx = centroid.x + radius * Math.cos(-angle);
                const hy = centroid.y + radius * Math.sin(-angle);
                ctx.beginPath();
                ctx.arc(hx, hy, 6, 0, Math.PI * 2);
                ctx.fillStyle = isRotating ? '#ffcc00' : '#00d4ff';
                ctx.fill();
                ctx.strokeStyle = '#1a1e26';
                ctx.lineWidth = 1.5;
                ctx.stroke();
                
                // Guide line from centroid to handle
                ctx.beginPath();
                ctx.moveTo(centroid.x, centroid.y);
                ctx.lineTo(hx, hy);
                ctx.strokeStyle = 'rgba(0,212,255,0.3)';
                ctx.lineWidth = 1;
                ctx.stroke();
                
                // Angle label during rotation
                if (isRotating) {
                    const degLabel = (THREE.MathUtils.radToDeg(_rotateHandle.currentAngle) % 360).toFixed(1) + '°';
                    ctx.font = '13px Share Tech Mono, monospace';
                    ctx.fillStyle = '#ffcc00';
                    ctx.textAlign = 'center';
                    ctx.fillText(degLabel, centroid.x, centroid.y - radius - 10);
                    ctx.textAlign = 'start';
                }
            }
```

- [ ] **Step 3: Add rotation handle hit test + drag in `_onMouseDown`**

In the model hit test section (inside `if (_tool === 'select')`), BEFORE the existing model hit test, add rotation handle check:

```javascript
        // Rotation handle hit test
        if (_tool === 'select') {
            for (let mi = 0; mi < state.models.length; mi++) {
                const m = state.models[mi];
                if (m.enabled === false || !m.rotateTool || !m.rotateTool.enabled || !m.rotateTool.axes.yaw) continue;
                // Compute handle position
                const obb = window.getModelOBB ? getModelOBB(m) : null;
                if (!obb) continue;
                const centroid = worldToCanvas(obb.center.x, obb.center.y);
                const visTL = worldToCanvas(obb.center.x - obb.half[0], obb.center.y + obb.half[1]);
                const visBR = worldToCanvas(obb.center.x + obb.half[0], obb.center.y - obb.half[1]);
                const diagPx = Math.hypot(visBR.x - visTL.x, visBR.y - visTL.y);
                const radius = Math.max(25, diagPx * 0.6);
                const q = m.transform ? m.transform.quaternion : {x:0,y:0,z:0,w:1};
                const angle = 2 * Math.atan2(q.z || 0, q.w || 1);
                const hx = centroid.x + radius * Math.cos(-angle);
                const hy = centroid.y + radius * Math.sin(-angle);
                
                if (Math.hypot(cx - hx, cy - hy) < 12) {
                    if (window.pushUndoCheckpoint) pushUndoCheckpoint('2dRotate');
                    _rotateHandle = {
                        id: m.id,
                        centroidX: obb.center.x,
                        centroidY: obb.center.y,
                        startAngle: angle,
                        currentAngle: angle,
                        startQuat: { ...m.transform.quaternion },
                        startAnchorX: m.anchor.x,
                        startAnchorY: m.anchor.y,
                        dragStartAngle: Math.atan2(-(cy - centroid.y), cx - centroid.x)
                    };
                    _scheduleRender();
                    return;
                }
            }
        }
```

- [ ] **Step 4: Add rotation drag update in `_onMouseMove`**

After the existing `_dragModel` handler, add:

```javascript
        if (_rotateHandle) {
            const centroidCanvas = worldToCanvas(_rotateHandle.centroidX, _rotateHandle.centroidY);
            const mouseAngle = Math.atan2(-(cy - centroidCanvas.y), cx - centroidCanvas.x);
            const dAngle = mouseAngle - _rotateHandle.dragStartAngle;
            const newAngle = _rotateHandle.startAngle + dAngle;
            
            // Apply rotation snap if enabled
            let finalAngle = newAngle;
            if (_shiftHeld) {
                finalAngle = Math.round(newAngle / (Math.PI / 12)) * (Math.PI / 12); // 15° snap
            }
            if (_rotationSnap) {
                const snapResult = _findRotationSnapAngle(finalAngle);
                if (snapResult !== null) finalAngle += snapResult;
            }
            
            _rotateHandle.currentAngle = finalAngle;
            
            // Update model: rotate around centroid
            const m = window.getModelById ? getModelById(_rotateHandle.id) : null;
            if (m) {
                const totalDAngle = finalAngle - _rotateHandle.startAngle;
                // Update quaternion: start quaternion + Z rotation by totalDAngle
                const dq = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), totalDAngle);
                const startQ = new THREE.Quaternion(_rotateHandle.startQuat.x, _rotateHandle.startQuat.y, _rotateHandle.startQuat.z, _rotateHandle.startQuat.w);
                const newQ = dq.clone().multiply(startQ);
                m.transform.quaternion = { x: newQ.x, y: newQ.y, z: newQ.z, w: newQ.w };
                
                // Rotate anchor around centroid
                const cos = Math.cos(totalDAngle), sin = Math.sin(totalDAngle);
                const dx = _rotateHandle.startAnchorX - _rotateHandle.centroidX;
                const dy = _rotateHandle.startAnchorY - _rotateHandle.centroidY;
                m.anchor.x = _rotateHandle.centroidX + dx * cos - dy * sin;
                m.anchor.y = _rotateHandle.centroidY + dx * sin + dy * cos;
                
                if (window.syncModelRenderTransform) syncModelRenderTransform(m);
                if (window.invalidateBBoxCache) invalidateBBoxCache(m.id);
            }
            _scheduleRender();
            return;
        }
```

- [ ] **Step 5: Add rotation drag end in `_onMouseUp`**

After existing `_dragModel` cleanup:
```javascript
        if (_rotateHandle) {
            if (window.requestRender) requestRender();
            const m = window.getModelById ? getModelById(_rotateHandle.id) : null;
            if (m) { rebuildSingleModel(m.id); }
            _rotateHandle = null;
        }
```

- [ ] **Step 6: Add rotation snap function**

After the existing `_applySnap` function:
```javascript
    function _findRotationSnapAngle(currentAngle) {
        if (!_rotationSnap || !state || !state.models) return null;
        const targetAngles = [];
        state.models.forEach(function(m) {
            if (m.enabled === false) return;
            const q = m.transform ? m.transform.quaternion : {x:0,y:0,z:0,w:1};
            const mAngle = 2 * Math.atan2(q.z || 0, q.w || 1);
            // Each model contributes 2 unique edge directions (0° and 90° from rotation)
            targetAngles.push(mAngle);
            targetAngles.push(mAngle + Math.PI / 2);
        });
        // Also add polygon edge angles
        if (_vertices.length >= 2) {
            for (var i = 0; i < _vertices.length - (_closed ? 0 : 1); i++) {
                var a = _vertices[i], b = _vertices[(i + 1) % _vertices.length];
                targetAngles.push(Math.atan2(b[1] - a[1], b[0] - a[0]));
            }
        }
        
        var modelEdges = [currentAngle, currentAngle + Math.PI / 2, currentAngle + Math.PI, currentAngle + Math.PI * 1.5];
        var bestSnap = null, bestDist = Math.PI / 60; // 3° threshold
        for (var ti = 0; ti < targetAngles.length; ti++) {
            for (var mi = 0; mi < modelEdges.length; mi++) {
                var diff = ((targetAngles[ti] - modelEdges[mi]) % Math.PI + Math.PI * 2) % Math.PI;
                if (diff > Math.PI / 2) diff = Math.PI - diff;
                if (diff < bestDist) {
                    bestDist = diff;
                    bestSnap = targetAngles[ti] - modelEdges[mi];
                    while (bestSnap > Math.PI / 2) bestSnap -= Math.PI;
                    while (bestSnap < -Math.PI / 2) bestSnap += Math.PI;
                }
            }
        }
        return bestSnap;
    }
```

- [ ] **Step 7: Add rotation snap toggle to 2D toolbar**

In the toolbar HTML, add button and toggle function:
```javascript
    room2d.toggleRotationSnap = function() {
        _rotationSnap = !_rotationSnap;
        _updateToolButtons();
    };
```

In `_updateToolButtons`, add:
```javascript
        setActive('room2d-btn-rotsnap', _rotationSnap);
```

- [ ] **Step 8: Add toolbar button HTML in STL_generate.html (~line 615)**

After the edge align button:
```html
<button class="tool-btn" id="room2d-btn-rotsnap" onclick="room2d.toggleRotationSnap()">🔄 <span data-i18n="label.rotation_snap">旋转吸附</span></button>
```

- [ ] **Step 9: Commit**

```bash
git commit -m "feat(rotation): 2D rotation handle, centroid rotation, edge-parallel snap"
```

---

### Task 4: 2D auto-enable rotate tool + 3D toolbar snap toggle

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Auto-enable rotate tool in 2D mode**

In the existing `setSelection` 2D auto-enable block (where `moveTool.enabled = true`), also enable rotateTool:

```javascript
        if (window.room2d && room2d._scheduleRender && type === 'model' && modelId) {
            const _m2d = getModelById(modelId);
            if (_m2d && (new Set(['rack', 'acu', 'box'])).has(_m2d.kind)) {
                const _mt = ensureModelMoveTool(_m2d);
                _mt.enabled = true;
                _mt.axes = { x: true, y: true, z: false };
                const _rt = ensureModelRotateTool(_m2d);
                _rt.enabled = true;
                _rt.axes = { yaw: true, pitch: false, roll: false };
            }
        }
```

- [ ] **Step 2: Add rotation snap toggle to 3D toolbar (~line 572)**

After the existing mirror buttons, add:
```html
<button class="tool-btn" id="rot-snap-toggle" onclick="window.toggleRotationSnap3D()" data-i18n="label.rotation_snap">🔄 旋转吸附</button>
```

Add the toggle function:
```javascript
    let _rotationSnap3D = false;
    window.toggleRotationSnap3D = function() {
        _rotationSnap3D = !_rotationSnap3D;
        const btn = document.getElementById('rot-snap-toggle');
        if (btn) btn.classList.toggle('active', _rotationSnap3D);
    };
```

Phase 2 will use `_rotationSnap3D` for 3D gizmo snap — for now the button is placed and functional as a toggle.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(rotation): auto-enable in 2D, 3D toolbar snap toggle"
```

---

### Task 5: Manual testing

- [ ] **Step 1: Test Inspector rotate controls**

1. Select rack → "Enable Rotate" button appears next to "Enable Move"
2. Click → Yaw/Pitch/Roll checkboxes appear
3. Rotation angle section shows current degrees
4. Edit Yaw to 45 → model rotates 45° in 3D

- [ ] **Step 2: Test 2D rotation handle**

1. Enter 2D → select rack → rotation circle appears at centroid
2. Drag handle → model rotates smoothly
3. Anchor position changes (orbits centroid)
4. Shift+drag → snaps to 15° increments

- [ ] **Step 3: Test rotation snap**

1. Enable rotation snap in 2D toolbar
2. Rotate rack near a wall → snaps to parallel
3. Green indicator when snapped

- [ ] **Step 4: Test toolbar rotation still works**

1. Use toolbar 90° Z rotation → rotates around anchor (not centroid)
2. Verify angle display updates
3. Undo/redo works
