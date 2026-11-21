# Free Rotation System — Design Spec

**Date**: 2026-04-05
**Goal**: Add visual rotation gizmo with face-parallel snap for any model, in both 2D and 3D modes.

**Phase 1** (this spec): Right-panel rotate button + angle display/input + 2D rotation handle (Z-axis) + 2D rotation snap
**Phase 2** (future): 3D ring gizmo (Three.js) + 3D face-normal parallel snap

---

## 1. Right Panel: Rotate Button + Axis Control

Add "Rotate" button next to existing "Move" button in model Inspector:

```
[启用移动] [启用旋转]
☑ X  ☑ Y  ☑ Z          ← move axis constraints (existing)
☐ Yaw ☐ Pitch ☐ Roll   ← rotate axis constraints (new)
```

**Data model** — add `rotateTool` to model (parallel to existing `moveTool`):
```javascript
model.rotateTool = {
    enabled: false,
    axes: { yaw: true, pitch: false, roll: false }  // Yaw=Z, Pitch=X, Roll=Y
};
```

**Behavior**:
- Click "Enable Rotate" → `rotateTool.enabled = true`
- In 2D mode: Pitch/Roll disabled (grayed out), only Yaw selectable
- Move and rotate can be enabled simultaneously (not mutually exclusive)
- `rotateTool` stored on model, persisted in `.cfdproj`, included in undo snapshots
- `ensureModelRotateTool(model)` helper creates default if missing

**Angle display** below anchor coordinates in Inspector:
```
⚓ Anchor Position
X: [1.990]  Y: [1.041]  Z: [0.300]

🔄 Rotation (degrees)
Yaw:   [45.0]°   (Z axis)
Pitch: [0.0]°    (X axis)
Roll:  [0.0]°    (Y axis)
```

- Display: quaternion → Euler angles (ZXY order, degrees)
- Input: Euler → quaternion → `syncModelRenderTransform`
- Linked with toolbar rotation buttons (same `model.transform.quaternion`)

---

## 2. Two Rotation Centers

| Mode | Rotation Center | Anchor Behavior |
|------|----------------|-----------------|
| **Gizmo drag** (ring/handle) | Geometric centroid (3D center / 2D centroid) | Anchor orbits around centroid |
| **Toolbar buttons** (90° etc.) | Model anchor | Anchor stays fixed |

**Centroid calculation**:
```javascript
function getModel2DCentroid(model) {
    const obb = getModelOBB(model);
    return { x: obb.center.x, y: obb.center.y };
}
function getModel3DCentroid(model) {
    const obb = getModelOBB(model);
    return { x: obb.center.x, y: obb.center.y, z: obb.center.z };
}
```

**Anchor update during gizmo rotation** (dAngle around Z axis):
```javascript
const cos = Math.cos(dAngle), sin = Math.sin(dAngle);
const dx = model.anchor.x - centroid.x;
const dy = model.anchor.y - centroid.y;
model.anchor.x = centroid.x + dx * cos - dy * sin;
model.anchor.y = centroid.y + dx * sin + dy * cos;
// Update quaternion: append Z-rotation by dAngle
const dq = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), dAngle);
const current = new THREE.Quaternion(q.x, q.y, q.z, q.w);
current.premultiply(dq);
model.transform.quaternion = { x: current.x, y: current.y, z: current.z, w: current.w };
```

---

## 3. 2D Rotation Handle

**Visual** (drawn in `_drawModelProjections` when rotateTool.enabled):
- Dashed circle at model centroid, radius = 60% of projection diagonal
- Drag handle (filled circle, 8px) at current angle direction on the ring
- Guide line from centroid to drag handle
- Colors: blue (#00d4ff) default, yellow (#ffcc00) during drag, green (#00ff88) when snapped

**Interaction**:
- Mouse near drag handle (12px) → cursor = `grab`
- Mousedown → start rotation drag
  - Track angle: `atan2(mouse.y - centroid.y, mouse.x - centroid.x)`
  - Compute delta angle from drag start
  - Update model rotation + anchor in real-time
  - Shift held → snap to 15° increments
  - Rotation snap enabled → snap to edge-parallel angle
- Mouseup → finalize, pushUndoCheckpoint (one per drag)

**Angle display during drag**: Show current angle near the handle: `"45.0°"`

---

## 4. 2D Rotation Snap (Edge-Parallel)

**Toolbar button**: "🔄 旋转吸附" toggle in 2D toolbar (between "边对齐" and grid spacing).

**Algorithm**:
1. Collect edge angles from ALL other models in the scene:
   - Wall/Baffle (D=0): edge angle = model's Z-rotation angle
   - Floor polygon: each polygon edge's angle
   - Rack/ACU/Box: 4 bottom edges (rotation + 0°, 90°, 180°, 270°)
2. Current model's 4 bottom edge angles = current rotation + [0°, 90°, 180°, 270°]
3. For each (model_edge, target_edge) pair:
   - Compute angular difference modulo 180° (parallel = 0° or 180°)
   - If diff < 3°: snap to exact parallel angle
4. Return the snap angle adjustment, or null if no snap

```javascript
function findRotationSnapAngle(model, currentAngle) {
    const targetAngles = _collectSceneEdgeAngles(model.id);
    const modelEdges = [currentAngle, currentAngle + 90, currentAngle + 180, currentAngle + 270];
    
    let bestSnap = null, bestDist = 3; // 3° threshold
    for (const ta of targetAngles) {
        for (const me of modelEdges) {
            let diff = ((ta - me) % 180 + 360) % 180;
            if (diff > 90) diff = 180 - diff;
            if (diff < bestDist) {
                bestDist = diff;
                bestSnap = ta - me; // adjustment needed
                // Normalize to smallest rotation
                while (bestSnap > 90) bestSnap -= 180;
                while (bestSnap < -90) bestSnap += 180;
            }
        }
    }
    return bestSnap;
}
```

**Visual feedback on snap**: Ring turns green + label shows "∥ Wall_1"

---

## 5. Top Toolbar: Rotation Snap Toggle (3D)

Add toggle button to 3D toolbar (near existing "面吸附" / "边对齐"):
```
[面吸附] [边对齐] [🔄 旋转吸附] | [网格间距 ...]
```

In 3D mode, rotation snap uses the same algorithm but in 3D:
- Collect face normals from walls/floors/baffles
- Find angle that makes model face parallel to nearest target face
- Phase 2 will implement the full 3D version; Phase 1 button is placed but only works in 2D

---

## 6. Euler ↔ Quaternion Conversion

```javascript
function quaternionToEulerDeg(q) {
    const euler = new THREE.Euler().setFromQuaternion(
        new THREE.Quaternion(q.x, q.y, q.z, q.w), 'ZXY'
    );
    return {
        yaw: THREE.MathUtils.radToDeg(euler.z),    // Z axis
        pitch: THREE.MathUtils.radToDeg(euler.x),  // X axis
        roll: THREE.MathUtils.radToDeg(euler.y)     // Y axis
    };
}

function eulerDegToQuaternion(yaw, pitch, roll) {
    const euler = new THREE.Euler(
        THREE.MathUtils.degToRad(pitch),  // X
        THREE.MathUtils.degToRad(roll),   // Y
        THREE.MathUtils.degToRad(yaw),    // Z
        'ZXY'
    );
    const q = new THREE.Quaternion().setFromEuler(euler);
    return { x: q.x, y: q.y, z: q.z, w: q.w };
}
```

Euler order `ZXY` matches data center convention: Yaw (Z) is primary rotation.

---

## 7. State Normalization

In `normalizeState`, add `rotateTool` handling:
```javascript
// In createModelFromRaw:
rotateTool: rawModel.rotateTool ? {
    enabled: !!rawModel.rotateTool.enabled,
    axes: { yaw: true, pitch: false, roll: false, ...(rawModel.rotateTool.axes || {}) }
} : null,
```

`rotateTool = null` means rotate button not yet clicked (hide axis controls).

---

## 8. i18n Keys

```
"btn.enable_rotate": "启用旋转" / "Enable Rotate"
"btn.stop_rotate": "停止旋转" / "Stop Rotate"
"label.rotation_angles": "旋转角度" / "Rotation"
"label.yaw": "偏航 (Z轴)" / "Yaw (Z)"
"label.pitch": "俯仰 (X轴)" / "Pitch (X)"
"label.roll": "翻滚 (Y轴)" / "Roll (Y)"
"label.rotation_snap": "旋转吸附" / "Rotation Snap"
"hint.rotation_snap": "旋转时吸附使面/边平行于最近的物体" / "Snap rotation to align face/edge parallel to nearest object"
```

---

## Non-Goals (Phase 2)

- 3D ring gizmo rendering (Three.js TorusGeometry + raycasting)
- 3D face-normal parallel snap (full 3D vector matching)
- Multi-model simultaneous rotation with shared pivot

---

## Testing

1. Select rack → click "Enable Rotate" → Yaw/Pitch/Roll checkboxes appear
2. In 2D: rotation circle appears at model centroid, drag handle rotates model
3. Rotation snap on → rotate near wall → snaps to parallel
4. Inspector shows Yaw/Pitch/Roll degrees → edit value → model rotates
5. Toolbar 90° rotation still works (anchor-centered, not centroid)
6. Save/load project → rotateTool state preserved
7. Undo/redo rotation → correct state restored
8. 2D mode: only Yaw enabled, Pitch/Roll grayed out
