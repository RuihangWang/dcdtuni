# Rotation Gizmo Phase 2 — 3D Ring Gizmo Design Spec

**Date**: 2026-04-05
**Goal**: Add a 3D visual rotation ring gizmo to the viewport for interactive model rotation via mouse drag.

**Phase 1** (done): Inspector UI, 2D rotation handle in room_2d_editor, rotation snap
**Phase 2** (this spec): 3D ring gizmo in the Three.js viewport

---

## 1. Overview

When `rotateTool.enabled = true` and a model is selected, render a visible ring gizmo around the model in the 3D viewport. The user drags the ring to rotate the model around its geometric centroid, with real-time visual feedback.

### What it is NOT

- NOT Three.js TransformControls (too heavy, wrong UX — we want a custom ring)
- NOT full 3-axis gizmo (Phase 2 is yaw-only / single-axis at a time based on `rotateTool.axes`)
- NOT multi-model rotation (single selected model only)

---

## 2. Visual Design

### Ring Geometry

A `THREE.TorusGeometry` centered at the model's OBB center, lying in the plane perpendicular to the active rotation axis:

| Axis | Ring Plane | Color |
|------|-----------|-------|
| Yaw (Z) | XY plane | Cyan `#00d4ff` |
| Pitch (X) | YZ plane | Red `#ff4444` |
| Roll (Y) | XZ plane | Green `#44ff44` |

**Ring sizing:**
- Major radius = max(OBB diagonal * 0.6, 0.3m) — enough to surround the model
- Tube radius = 0.02m (thin ring, thick enough to click)
- Segments: 48 radial, 12 tubular

**Ring material:**
- Default: `MeshBasicMaterial({ color, transparent: true, opacity: 0.5, depthTest: false })`
- Hover: opacity 0.8, color brightens
- Dragging: opacity 1.0, full bright color

### Handle indicator

A small sphere (radius 0.04m) on the ring at the current rotation angle, showing where to grab:
- Same color as ring but fully opaque
- Shows the current angle position on the ring

### Angle label

During drag, show the rotation angle as a CSS overlay near the cursor:
- Format: `"45.0°"` or `"45.0° ∥ Wall_1"` when snapped
- Yellow text on semi-transparent dark background
- Positioned 20px above cursor

---

## 3. Gizmo Lifecycle

```
Model selected + rotateTool.enabled → createRotationGizmo(model)
Model deselected OR rotateTool disabled → destroyRotationGizmo()
Model anchor/size changes → updateRotationGizmoPosition()
```

### State

```javascript
let _rotateGizmo = null;  // { ring, handle, modelId, axis, center }
```

### Create

```javascript
function createRotationGizmo(model) {
    destroyRotationGizmo();  // clean previous
    const rt = ensureModelRotateTool(model);
    if (!rt.enabled) return;
    
    // Determine active axis (first enabled: yaw > pitch > roll)
    const axis = rt.axes.yaw ? 'z' : rt.axes.pitch ? 'x' : rt.axes.roll ? 'y' : null;
    if (!axis) return;
    
    const obb = getModelOBB(model);
    const diag = Math.hypot(obb.half[0], obb.half[1], obb.half[2]) * 2;
    const radius = Math.max(diag * 0.6, 0.3);
    
    // Ring torus
    const geometry = new THREE.TorusGeometry(radius, 0.02, 12, 48);
    const color = axis === 'z' ? 0x00d4ff : axis === 'x' ? 0xff4444 : 0x44ff44;
    const material = new THREE.MeshBasicMaterial({
        color, transparent: true, opacity: 0.5, depthTest: false, side: THREE.DoubleSide
    });
    const ring = new THREE.Mesh(geometry, material);
    ring.position.copy(obb.center);
    
    // Orient ring perpendicular to axis
    if (axis === 'z') { /* default orientation, XY plane */ }
    else if (axis === 'x') ring.rotation.set(0, Math.PI/2, 0);
    else if (axis === 'y') ring.rotation.set(Math.PI/2, 0, 0);
    
    ring.renderOrder = 999;  // Always on top
    ring.userData = { isRotateGizmo: true };
    scene.add(ring);
    
    // Handle sphere at current angle
    const q = ensureModelTransform(model).quaternion;
    const currentAngle = getCurrentAxisAngle(q, axis);
    const handle = createHandleSphere(obb.center, radius, currentAngle, axis, color);
    scene.add(handle);
    
    _rotateGizmo = { ring, handle, modelId: model.id, axis, center: obb.center.clone(), radius };
    requestRender();
}
```

### Destroy

```javascript
function destroyRotationGizmo() {
    if (!_rotateGizmo) return;
    scene.remove(_rotateGizmo.ring);
    _rotateGizmo.ring.geometry.dispose();
    _rotateGizmo.ring.material.dispose();
    scene.remove(_rotateGizmo.handle);
    _rotateGizmo.handle.geometry.dispose();
    _rotateGizmo.handle.material.dispose();
    _rotateGizmo = null;
    // Remove angle label if exists
    removeAngleLabel();
    requestRender();
}
```

---

## 4. Drag Interaction

### Hit detection (pointerdown)

On left-click, raycast against the ring torus. If hit:

```javascript
function beginRotationGizmoDrag(event) {
    if (!_rotateGizmo || event.button !== 0) return false;
    
    updatePointerFromEvent(event);
    const hits = raycaster.intersectObject(_rotateGizmo.ring, false);
    // Also check handle sphere for easier grabbing
    const handleHits = raycaster.intersectObject(_rotateGizmo.handle, false);
    if (!hits.length && !handleHits.length) return false;
    
    const model = getModelById(_rotateGizmo.modelId);
    if (!model) return false;
    
    pushUndoCheckpoint('modelRotate3D');
    
    // Compute initial angle from center to click point projected onto rotation plane
    const hitPoint = (handleHits.length ? handleHits[0] : hits[0]).point;
    const startAngle = computeAngleOnPlane(hitPoint, _rotateGizmo.center, _rotateGizmo.axis);
    
    dragState = {
        mode: 'modelRotate3D',
        pointerId: event.pointerId,
        modelId: model.id,
        axis: _rotateGizmo.axis,
        center: _rotateGizmo.center.clone(),
        startAngle: startAngle,
        startQuaternion: { ...ensureModelTransform(model).quaternion },
        startAnchor: { x: model.anchor.x, y: model.anchor.y, z: model.anchor.z },
        radius: _rotateGizmo.radius,
        accumulatedAngle: 0,
    };
    
    if (controls) controls.enabled = false;
    try { renderer.domElement.setPointerCapture(event.pointerId); } catch(e) {}
    
    // Visual feedback
    _rotateGizmo.ring.material.opacity = 1.0;
    requestRender();
    return true;
}
```

### Drag update (pointermove)

Project mouse onto the rotation plane, compute angle delta:

```javascript
// In onPointerMove, when dragState.mode === 'modelRotate3D':
function updateRotationDrag(event) {
    const model = getModelById(dragState.modelId);
    if (!model) return;
    
    updatePointerFromEvent(event);
    
    // Project ray onto rotation plane
    const planeNormal = getAxisVector(dragState.axis);
    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(planeNormal, dragState.center);
    const intersection = new THREE.Vector3();
    if (!raycaster.ray.intersectPlane(plane, intersection)) return;
    
    // Compute current angle
    const currentAngle = computeAngleOnPlane(intersection, dragState.center, dragState.axis);
    let deltaAngle = currentAngle - dragState.startAngle;
    
    // Normalize to [-PI, PI]
    while (deltaAngle > Math.PI) deltaAngle -= 2 * Math.PI;
    while (deltaAngle < -Math.PI) deltaAngle += 2 * Math.PI;
    
    // Shift: snap to 15-degree increments
    if (event.shiftKey) {
        const step = Math.PI / 12;  // 15 degrees
        deltaAngle = Math.round(deltaAngle / step) * step;
    }
    
    // Apply rotation: premultiply delta quaternion onto start quaternion
    const axisVec = getAxisVector(dragState.axis);
    const dq = new THREE.Quaternion().setFromAxisAngle(axisVec, deltaAngle);
    const startQ = new THREE.Quaternion(
        dragState.startQuaternion.x, dragState.startQuaternion.y,
        dragState.startQuaternion.z, dragState.startQuaternion.w
    );
    const newQ = dq.multiply(startQ);
    
    model.transform.quaternion = { x: newQ.x, y: newQ.y, z: newQ.z, w: newQ.w };
    
    // Orbit anchor around centroid
    const cos = Math.cos(deltaAngle), sin = Math.sin(deltaAngle);
    // Project anchor offset onto rotation plane, rotate, restore
    rotateAnchorAroundCenter(model, dragState, deltaAngle);
    
    syncModelRenderTransform(model);
    updateRotationGizmoPosition();
    updateAngleLabel(deltaAngle, event);
    requestRender();
}
```

### Drag end (pointerup)

```javascript
// In endPatchDrag or new endRotationGizmoDrag:
if (dragState.mode === 'modelRotate3D') {
    if (controls) controls.enabled = true;
    try { renderer.domElement.releasePointerCapture(dragState.pointerId); } catch(e) {}
    
    // Reset ring visual
    if (_rotateGizmo) _rotateGizmo.ring.material.opacity = 0.5;
    
    removeAngleLabel();
    updateUI();  // Refresh inspector angle display
    dragState = null;
    requestRender();
}
```

---

## 5. Integration Points

### Mouse event priority

In the `pointerdown` handler (~line 13421), insert rotation gizmo check **before** `beginModelMoveDrag`:

```javascript
if (beginRotationGizmoDrag(event)) return;  // NEW — before modelMove
if (beginModelMoveDrag(event)) return;
```

### Gizmo lifecycle triggers

Call `createRotationGizmo` / `destroyRotationGizmo` from:
1. `toggleModelRotateTool()` — after toggling enabled state
2. `updateUI()` — when selection changes
3. `setSelection()` — when model selection changes
4. `setModelRotateAxis()` — when axis changes

### updateUI integration

At the end of `updateUI()` or in a dedicated `refreshRotationGizmo()`:
```javascript
function refreshRotationGizmo() {
    const model = getSelectedModel();
    if (model && ensureModelRotateTool(model).enabled) {
        createRotationGizmo(model);
    } else {
        destroyRotationGizmo();
    }
}
```

---

## 6. Helper Functions

```javascript
function getAxisVector(axis) {
    return axis === 'x' ? new THREE.Vector3(1,0,0)
         : axis === 'y' ? new THREE.Vector3(0,1,0)
         : new THREE.Vector3(0,0,1);
}

function computeAngleOnPlane(point, center, axis) {
    const d = point.clone().sub(center);
    if (axis === 'z') return Math.atan2(d.y, d.x);
    if (axis === 'x') return Math.atan2(d.z, d.y);
    if (axis === 'y') return Math.atan2(d.x, d.z);
}

function getCurrentAxisAngle(q, axis) {
    const euler = quaternionToEulerDeg(q);
    if (axis === 'z') return THREE.MathUtils.degToRad(euler.yaw);
    if (axis === 'x') return THREE.MathUtils.degToRad(euler.pitch);
    if (axis === 'y') return THREE.MathUtils.degToRad(euler.roll);
}

function rotateAnchorAroundCenter(model, dragState, deltaAngle) {
    const axis = dragState.axis;
    const cx = dragState.center.x, cy = dragState.center.y, cz = dragState.center.z;
    const ax = dragState.startAnchor.x, ay = dragState.startAnchor.y, az = dragState.startAnchor.z;
    const cos = Math.cos(deltaAngle), sin = Math.sin(deltaAngle);
    
    if (axis === 'z') {
        model.anchor.x = cx + (ax - cx) * cos - (ay - cy) * sin;
        model.anchor.y = cy + (ax - cx) * sin + (ay - cy) * cos;
        model.anchor.z = az;
    } else if (axis === 'x') {
        model.anchor.x = ax;
        model.anchor.y = cy + (ay - cy) * cos - (az - cz) * sin;
        model.anchor.z = cz + (ay - cy) * sin + (az - cz) * cos;
    } else {
        model.anchor.x = cx + (ax - cx) * cos + (az - cz) * sin;
        model.anchor.y = ay;
        model.anchor.z = cz - (ax - cx) * sin + (az - cz) * cos;
    }
}
```

---

## 7. Hover Feedback

On `pointermove` when NOT dragging, check if mouse hovers over the ring:

```javascript
// In onPointerMove, after regular hover checks:
if (_rotateGizmo && !dragState) {
    const hits = raycaster.intersectObject(_rotateGizmo.ring, false);
    const handleHits = raycaster.intersectObject(_rotateGizmo.handle, false);
    if (hits.length || handleHits.length) {
        _rotateGizmo.ring.material.opacity = 0.8;
        setViewerCursor('grab');
    } else {
        _rotateGizmo.ring.material.opacity = 0.5;
    }
    requestRender();
}
```

---

## 8. Angle Label (CSS Overlay)

```javascript
function updateAngleLabel(deltaAngle, event) {
    let label = document.getElementById('rotate-angle-label');
    if (!label) {
        label = document.createElement('div');
        label.id = 'rotate-angle-label';
        label.style.cssText = 'position:fixed;pointer-events:none;font:13px "Share Tech Mono",monospace;color:#ffcc00;background:rgba(0,0,0,0.7);padding:2px 8px;border-radius:4px;z-index:9999;';
        document.body.appendChild(label);
    }
    const deg = THREE.MathUtils.radToDeg(deltaAngle);
    label.textContent = deg.toFixed(1) + '°';
    label.style.left = (event.clientX + 15) + 'px';
    label.style.top = (event.clientY - 25) + 'px';
}

function removeAngleLabel() {
    const label = document.getElementById('rotate-angle-label');
    if (label) label.remove();
}
```

---

## 9. Non-Goals

- Multi-axis simultaneous display (only show ring for the first enabled axis)
- Multi-model rotation (single selected model)
- 3D face-normal snap (keep for future Phase 3)
- Undo during drag (one checkpoint per drag, same as moveTool)

---

## 10. Testing

1. Select rack model → enable rotate → cyan ring appears around model
2. Drag ring → model rotates smoothly, angle label shows degrees
3. Release → rotation persists, inspector shows updated angles
4. Shift+drag → snaps to 15-degree increments
5. Disable rotate tool → ring disappears
6. Switch axis (enable pitch) → ring changes to red, lies in YZ plane
7. Deselect model → ring disappears
8. Undo (Ctrl+Z) → rotation reverts
9. Save/load project → rotation preserved
10. OrbitControls work normally when not dragging the ring
