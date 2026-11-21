# Resize Handles & Snap System Fix — Design Spec

**Goal:** Replace invisible cursor-threshold edge resizing with explicit green cube handles, add yellow selection outlines, and fix/extend the snap-and-align system for patches across model types.

**Architecture:** Two independent subsystems modified in the same ~32k-line `STL_generate.html` frontend:
1. A new visual handle system (green cubes + yellow outline) that replaces cursor-based edge detection
2. An extended snap system that adds same-kind patch-to-patch snapping and broader rack-level server patch snapping

**Tech Stack:** Three.js r184 (IIFE bundle), vanilla JS within `STL_generate.html`

---

## 1. Green Cube Handle System

### 1.1 Visual Elements

When a quadrilateral object is **selected**, render:

- **Yellow outline:** `THREE.LineLoop` using the 4 corners of the selected face/patch. Color `#ffdd00`, `linewidth: 2`, `depthTest: false`, `renderOrder: 10`.
- **4 green cubes:** `THREE.Mesh` with `THREE.BoxGeometry` positioned at each edge center (top, bottom, left, right). Color `#00cc44`, `depthTest: false`, `renderOrder: 11`. Material: `MeshBasicMaterial` (no lighting dependency).

Cube size: `Math.max(0.01, Math.min(0.05, Math.min(faceW, faceH) * 0.03))` — 3% of the shorter face dimension, clamped between 1cm and 5cm.

For server patches (normalized 0–1 coordinates), cube size is computed in face-local space and transformed to world space via the equipment's matrix.

### 1.2 Object Types That Receive Handles

| Object Type | Selection Type | Outline Scope |
|---|---|---|
| Wall | `selectedFace` (type='face') | Selected face rectangle |
| Baffle | `selectedFace` (type='face') | Front face rectangle |
| Floor | `selectedFace` (type='face') | Top face rectangle |
| Box | `selectedFace` (type='face') | Selected face rectangle |
| ACU | `selectedFace` (type='face') | Selected face rectangle |
| Rect patch on wall/baffle/floor/box/ACU | `selectedPatch` (type='patch') | Patch rectangle within face |
| Server rect patch (inlet/outlet) | `selectedServerPatch` (type='serverPatch') | Patch rectangle on equipment face |

**Excluded:** Circle patches (keep existing radius-drag), custom vector patches, imported regions, rack slots, rack equipment bodies.

### 1.3 Handle Lifecycle

- **Create:** In `updateHighlights()`, after determining the selection type, if the selected object is a qualifying quadrilateral, call `_createResizeHandles(modelGroup, faceId, patch, layout)`.
- **Store:** On `modelGroup.userData._resizeHandles = { outline, cubes: [top, bottom, left, right], overlayGroup }`.
- **Destroy:** At the start of `updateHighlights()`, dispose any existing handles before recomputing. Also dispose in `clearRender()` and `_applyProjectData()`.
- **Overlay group:** Handles live in a dedicated `THREE.Group` added to `scene` (not to `modelGroup`) so they don't pollute bounding box calculations, frustum culling, STL export, or merge optimization.

### 1.4 Interaction Flow

1. **Hover over green cube:** Raycast hits cube mesh (cubes have `userData.isResizeHandle: true, edgeKey: 'left'|'right'|'top'|'bottom'`). Cursor changes to `grab`.
2. **Click and hold:** Cube material color changes to `#ffee00` (highlighted yellow). Cursor becomes `grabbing`. `dragState` is set with `resizeEdge: edgeKey` and mode determined by context:
   - **Model-level face** (wall/baffle/floor selected face): `mode: 'wallResize'` — changes `model.base.W/H/D` and shifts anchor to keep opposite edge fixed
   - **Rect patch on face**: `mode: 'boxRectResize'` — changes `patch.x/y/w/h` within the face
   - **Server rect patch**: `mode: 'boxRectResize'` — changes `patch.x/y/w/h` in normalized 0–1 space
3. **Drag:** Existing drag update logic in `updatePatchDrag()` handles the resize. Cube positions update each frame to follow the moving edge.
4. **Release:** Cube returns to `#00cc44`. Cursor resets. Final geometry rebuild.

### 1.5 What Gets Removed

- **`updatePatchEdgeHover()`:** Remove the `ns-resize`/`ew-resize` cursor logic for edge proximity detection. Keep the function but simplify — it only needs to detect green cube hover via raycast.
- **`getBoxPatchDragMode()` edge proximity:** Remove the threshold-based edge detection for rect patches. The drag mode is determined by whether the raycast hit a green cube (`boxRectResize`) or the face body (`boxFace`).
- **`getBoxPatchEdgeThreshold()`:** Delete entirely — no longer needed.
- **`hoverPatchEdge` state:** Simplify — only tracks which green cube is hovered, not invisible edge zones.
- **Edge helper lines** (`createBoxPatchEdgeHelpers`, `createWallBaffleEdgeHelpers`): Delete — replaced by the yellow outline and green cubes.
- **Cursor resize styles** (`ns-resize`, `ew-resize`, `nwse-resize` for rect edges): Remove — replaced by `grab`/`grabbing` on cube handles.

### 1.6 Handle Position Computation

For **model-level faces** (wall/baffle/floor/box/ACU):
- Get face layout via `getFaceLayouts(model)[faceId]`
- Corners: `origin`, `origin + right*w`, `origin + right*w + up*h`, `origin + up*h`
- Edge centers: midpoints of each edge
- Transform to world space via `modelGroup.matrixWorld`

For **rect patches** on model faces:
- Patch corners in face-local: `(patch.x, patch.y)` to `(patch.x+patch.w, patch.y+patch.h)`
- Map to 3D via face layout: `origin + right*x + up*y`
- Transform to world space via `modelGroup.matrixWorld`

For **server rect patches**:
- Patch coordinates are normalized 0–1 within equipment face
- Map to equipment face geometry using equipment slot bounds and face layout
- Transform to world space via rack model's `matrixWorld`

---

## 2. Snap System Fix

### 2.1 Layer A — Box Patch Snap to Model Edges (verify existing)

Code location: lines 18318–18420.

This should already work for baffle/floor patches. Verify by:
1. Loading a project with a baffle + nearby rack
2. Adding a patch to the baffle
3. Dragging the patch toward the rack edge
4. Confirming snap activates

If broken, likely causes:
- Face layout returns undefined for baffle/floor canonical face
- World-space projection doesn't account for model rotation
- `getRenderRootWorldBounds()` returns null for some models

Fix any issues found during verification.

### 2.2 Layer B — Same-Kind Patch-to-Patch Snap (NEW)

When dragging a patch, in addition to snapping to other models' bounding boxes, also snap to patches on other models of the **same kind**.

Implementation in the snap loop at line 18364 (`renderRoots.forEach`):

```
For each other renderRoot where otherModel.kind === model.kind:
  For each face on otherModel:
    For each rect patch on that face:
      Project patch edges (x, x+w, y, y+h) into world-space using otherModel's face layout + matrixWorld
      Compare projected edges against dragged patch's world-space edges
      Track best snap candidate (same logic as existing AABB edge comparison)
```

**Threshold:** Same as model-edge snap (6% of avg scene size).

**Performance:** Only iterate same-kind models. In typical scenes, there are few baffles/floors (< 50), so iterating their patches is cheap. For rack-heavy scenes with 300+ racks, skip patch iteration on racks (rack patches are handled by Layer C).

### 2.3 Layer C — Server Patch Snap Within Same Rack (extend existing)

Code location: lines 18424–18486.

Current behavior: snaps server patches against other equipment's patches on the **same face type** (e.g., inlet patches only compare to other inlet patches).

**Extension:** Snap against ALL rect patches on ALL equipment within the same rack, across all face types. When dragging an inlet patch:
- Compare edges to inlet patches on other servers (existing)
- Also compare to outlet patches on other servers (NEW)
- Also compare to top/bottom patches on other servers (NEW)

This requires normalizing coordinates across face types since inlet/outlet/top/bottom faces have different orientations. Project all patch edges into the rack's local coordinate system for comparison.

**Same-rack constraint:** Only compare equipment within `model.rack.equipments` — the rack that owns the selected server.

### 2.4 Snap Visual Feedback (NEW)

When a snap locks onto a target edge, render a thin dashed guide line:
- `THREE.Line` with `LineDashedMaterial`, color `#00ffaa`, `dashSize: 0.05`, `gapSize: 0.03`
- Extends from the snapped edge to the target edge
- Added to the overlay group (same as handles)
- Removed on next drag frame or on drag end

---

## 3. Integration Notes

### Undo Integration
- Green cube drag must call `pushUndoCheckpoint()` at drag start (first frame only), same as existing edge drag
- Snap adjustments are part of the same drag operation — no separate checkpoint

### Performance
- Handle creation is O(1) per selection change — negligible
- Same-kind patch snap adds iteration over same-kind models' patches — bounded by model count
- Server rack snap iterates equipment within one rack — bounded by rack size (typically < 50 servers)

### Tab Visibility
- Resize handles are viewport helpers — visible on the **Model** tab only
- Hide handles when switching to Mesh/Solver/Result tabs (add to `_applyLeftTabUI()`)

### Coordinate Systems
- Model-level faces: meters, face-local coordinates
- Box patches: meters, face-local (x, y, w, h relative to face origin)
- Server patches: normalized 0–1 within equipment face
- Snap comparisons: all projected to world space for consistency
