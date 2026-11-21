# Rack Detail View (P1) — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a third view mode ("Rack Detail") parallel to the existing 3D and 2D modes, where a single selected Rack is rendered in isolation for fine-grained U-slot editing.

**Architecture:** Extends the existing `state.displayMode` / `room2d._active` view-mode system with a new `rackDetail` mode. The mode is activated from the Inspector panel when a Rack is selected, renders only the target Rack in a dedicated scene group, and provides a "return" button to go back to the originating view (3D or 2D).

**Tech Stack:** Three.js r128 (existing), vanilla JS (existing STL_generate.html patterns).

---

## Scope

This is **Phase 1** of a 4-phase Rack Detail Editor feature:

| Phase | Feature | Status |
|-------|---------|--------|
| **P1** | **View mode shell** — enter/exit, isolated rendering, right panel, camera | **This spec** |
| P2 | Inner width ratio + U-slot depth controls + new wall solids | Future spec |
| P3 | Perforated door (hexagonal texture, click-through, porousBafflePressure BC) | Future spec |
| P4 | Leakage zone interaction polish (100% porosity = no source term) | Future spec |

P1 delivers a usable view mode with no new geometry parameters. Existing Rack inspector controls (equipment placement, slot deletion, leakage) work unchanged inside the Detail view.

## Non-goals

- New geometry parameters (inner width, depth offset, perforated door) — P2/P3.
- Changes to STL export, cfd_export.json, or bridge_bc — P2/P3.
- Changes to Rack rendering logic (`renderRackModel`) — unchanged.
- Support for non-Rack model types in Detail view — future, if ever.

---

## 1. State Variables

Add to `state` (alongside existing `displayMode`, `selection`, etc.):

```javascript
state.rackDetailTarget = null;       // model ID of the Rack being edited, or null
state.rackDetailReturnMode = '3d';   // '3d' or '2d' — which view to return to
state.rackDetailShowContext = false;  // toggle: show surrounding models at low opacity
```

**Mode detection helper:**

```javascript
function isRackDetailMode() {
    return state.rackDetailTarget !== null;
}
```

This is intentionally separate from `displayMode` — the Rack Detail mode overlays on top of the current display mode rather than replacing it. This avoids touching the existing `displayMode` state machine (which drives air/liquid/stlPreview branching).

## 2. Entry Flow

### 2.1 Trigger: Inspector Button

When a Rack model is selected in 3D or 2D mode, the right-panel Inspector (`updateUI` / `rebuildInspectorForModel`) appends a button:

```
🔧 Rack 细节设计 / Rack Detail Editor
```

Clicking it calls `window.enterRackDetail(modelId)`.

### 2.2 `enterRackDetail(modelId)`

1. Save current camera state: `captureCameraViewState()`.
2. Record return mode: `state.rackDetailReturnMode = room2d._active ? '2d' : '3d'`.
3. If in 2D mode, exit it: `room2d.exitEditor(false)` (don't apply changes).
4. Set `state.rackDetailTarget = modelId`.
5. Set `state.rackDetailShowContext = false`.
6. Call `rebuildGeometry()` — the dispatcher sees `isRackDetailMode()` and calls `doRebuildRackDetail()`.
7. Fly camera to Rack front (inlet face): compute Rack world-space bounding box, position camera at `center + normal * fitDistance`, OrbitControls target = center.
8. Update toolbar button (see §4).
9. Update right panel Inspector to Rack Detail layout (see §5).
10. `logOperation('enterRackDetail', ...)`.

No undo checkpoint — this is a pure view switch, no state mutation.

## 3. Exit Flow

### 3.1 `exitRackDetail()`

1. Save camera state (optional — user may want to return to previous view angle).
2. Clear `state.rackDetailTarget = null`.
3. If `rackDetailReturnMode === '2d'`: call `room2d.enterEditor()`.
4. Else: call `rebuildGeometry()` (normal 3D rebuild).
5. Restore camera to the state saved in `enterRackDetail`.
6. Restore toolbar button to original state.
7. Restore Inspector to normal selection-based panel.
8. `logOperation('exitRackDetail', ...)`.

## 4. Toolbar Button

Reuse the 2D toggle button slot (`btn.room_2d_editor` button in `#toolbar`):

| Mode | Button Text | onclick |
|------|------------|---------|
| Normal 3D | 🏠 房间 2D 平面设计 | `room2d.enterEditor()` |
| Normal 2D | ↩ 返回 3D 视图 | `room2d.exitEditor(false)` |
| **Rack Detail (from 3D)** | **↩ 返回 3D 界面** | **`exitRackDetail()`** |
| **Rack Detail (from 2D)** | **↩ 返回 2D 界面** | **`exitRackDetail()`** |

Implementation: `enterRackDetail` saves the button's current innerHTML and onclick, replaces them, and `exitRackDetail` restores.

## 5. Right Panel (Inspector Reuse)

When `isRackDetailMode()`, the Inspector area (`prop-content`) renders a dedicated Rack Detail panel instead of the normal model inspector. Content:

### 5.1 Rack Summary (read-only)

```
📦 Rack: {model.name}
尺寸: {W} × {D} × {H} m
U-slots: {unitCount} ({unitHeight*1000}mm/U)
设备: {equipments.length} 台
```

### 5.2 Display Options

```
☐ 显示周围环境 / Show Context
```

Toggle calls `window.rackDetailToggleContext(checked)`:
- `true`: other models rendered at 8% opacity, `mesh.layers.set(2)` (raycaster only checks layer 0, so these are not pickable).
- `false`: other models hidden (`visible = false`).

### 5.3 Phase 2/3 Placeholder Areas

```
─── 内径宽度 / Inner Width ───
Phase 2 — 即将推出

─── U-slot 进深 / Slot Depth ───
Phase 2 — 即将推出

─── 穿孔门 / Perforated Door ───
Phase 3 — 即将推出
```

These are static divs with IDs (`#rack-detail-inner-width`, `#rack-detail-slot-depth`, `#rack-detail-door`) that P2/P3 will populate with real controls.

## 6. Scene Rendering: `doRebuildRackDetail()`

New rendering function, called by `rebuildGeometry()` when `isRackDetailMode()`:

1. `clearRender()` — remove all existing renderRoots (same as normal rebuild).
2. Find target model: `getModelById(state.rackDetailTarget)`.
3. If model not found or not a Rack, `exitRackDetail()` and return.
4. Create a `rackDetailGroup = new THREE.Group()`.
5. Render the target Rack into `rackDetailGroup` using the existing `renderRackModel(model, rackDetailGroup, faceLayouts)`.
6. Position the group at world origin (subtract model.anchor so the Rack is centered).
7. Add `rackDetailGroup` to `globalGroup`.
8. If `state.rackDetailShowContext`:
   - For each other enabled model, render normally but set all meshes to 8% opacity, `transparent: true`, `depthWrite: false`, and `mesh.layers.set(2)`.
9. Set renderOrder on rack detail meshes = 0 (normal), context meshes = -1 (render behind).

## 7. Camera Fit

```javascript
function fitCameraToRack(model) {
    const box = getRenderRootWorldBounds(getRenderRootByModelId(model.id));
    if (!box) return;
    const center = new THREE.Vector3();
    box.getCenter(center);
    const size = new THREE.Vector3();
    box.getSize(size);

    // Compute inlet face normal based on rack orientation
    const ori = (model.rack && model.rack.orientation) || model.geometry?.orientation || 0;
    const rad = ori * Math.PI / 180;
    const inletNormal = new THREE.Vector3(Math.sin(rad), -Math.cos(rad), 0);

    // Position camera at inlet face, distance = 1.5x max dimension
    const maxDim = Math.max(size.x, size.y, size.z);
    const fitDist = maxDim * 1.5;
    const camPos = center.clone().add(inletNormal.multiplyScalar(fitDist));
    camPos.z = center.z + size.z * 0.3; // slight upward offset

    camera.position.copy(camPos);
    controls.target.copy(center);
    controls.update();
}
```

OrbitControls remain fully functional — user can freely rotate/zoom/pan after the initial fit.

## 8. Left Panel (Model Tree)

**No changes.** The model tree stays visible and functional. Clicking another model in the tree while in Detail mode does NOT switch the detail target — it just changes selection highlight. To edit a different Rack's detail, the user must exit first, select the other Rack, and re-enter.

## 9. Interaction with Existing Features

| Feature | Behavior in Rack Detail |
|---------|------------------------|
| U-slot selection/deletion | Works normally (same renderRackModel) |
| Equipment add/remove | Works normally |
| Leakage zone auto-detection | Works normally |
| Partition add/remove | Works normally |
| Model drag/move | Disabled (Rack position is fixed in Detail view) |
| Undo/Redo | Works normally (geometry changes undoable, view switch is not) |
| STL Export | Works normally (uses state, not view) |
| Tab switching (mesh/solver/post) | Exits Rack Detail first, then switches tab |

## 10. File Changes

| File | Change |
|------|--------|
| `STL_generate.html` | Add state vars, `enterRackDetail()`, `exitRackDetail()`, `doRebuildRackDetail()`, `fitCameraToRack()`, `rackDetailToggleContext()`, Inspector panel, toolbar button swap |

Single-file change. No backend/runner/template changes needed for P1.

## Test Plan

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Select Rack in 3D → click "Rack 细节设计" | Enter Detail view, only Rack visible, camera on inlet face |
| 2 | Click "返回 3D 界面" | Return to 3D with previous camera restored |
| 3 | Select Rack in 2D → click "Rack 细节设计" | Enter Detail from 2D, button says "返回 2D 界面" |
| 4 | Click "返回 2D 界面" | Return to 2D editor |
| 5 | Toggle "显示周围环境" ON | Other models appear at low opacity, not pickable |
| 6 | Toggle "显示周围环境" OFF | Other models disappear |
| 7 | In Detail view, click U-slot to delete | Works, Rack re-renders in Detail view |
| 8 | In Detail view, add fan-server | Works, renders correctly |
| 9 | Ctrl+Z in Detail view | Undo works on geometry changes |
| 10 | Switch to mesh tab while in Detail | Exits Detail first, then shows mesh tab |
| 11 | Camera rotate/zoom/pan in Detail | OrbitControls fully functional |
| 12 | Delete the target Rack while in Detail (via model tree) | Auto-exit Detail, return to 3D/2D |

## Risks

- **Model tree click behavior**: Clicking another model in the tree while in Detail mode could confuse users. Mitigated by keeping the Detail view focused and requiring explicit exit.
- **Performance with context toggle**: Rendering all models at low opacity could be slow on complex scenes. Mitigated by using merged geometry and the existing `_dimMultC` opacity dimming pattern.
