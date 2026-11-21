# Deselect on Blank Click + 2D Multi-Select with Group Rotate

> **Goal:** (1) Fix: clicking blank space deselects and deactivates all move/rotate tools in both 2D and 3D. (2) Feature: 2D multi-select with Ctrl/Shift, supporting simultaneous move and multi-model rotation (each model around its own Z axis).

> **Architecture:** Unify 2D selection state with the existing `state.selection.modelIds` array (already used by 3D). Remove auto-activate move tool on select. Extend 2D rotation ring rendering and drag to support multiple models.

---

## Section 1: Bug Fix — Click Blank Deselects + Disables Tools

### Problem

Currently `setSelection('project')` clears the selection and destroys the rotation gizmo, but does NOT disable move tools. Additionally, in 2D mode, selecting a model auto-activates the move tool (`setSelection` lines 5190-5199), so after clicking blank → clicking model, the move tool springs back.

### Fix

1. **In `setSelection()` when `type === 'project'`**: Call `disableAllModelMoveTools()` and disable all rotate tools:
   ```javascript
   if (type === 'project') {
       disableAllModelMoveTools();
       state.models.forEach(m => { ensureModelRotateTool(m).enabled = false; });
   }
   ```

2. **Remove 2D auto-activate move tool**: Delete the block in `setSelection` (lines 5190-5199) that auto-enables move tool when selecting a model in 2D mode. Users must manually click the move/rotate button.

3. **Both 2D and 3D** already call `setSelection('project')` on blank click — no change needed there.

---

## Section 2: 2D Multi-Select

### Data Model Change

**Remove** `_selectedModelId` (single scalar) from `room_2d_editor.js`. Instead, read selected models from `state.selection.modelIds` (the same array 3D uses).

Helper to check if a model is selected in 2D:
```javascript
function _isModelSelected(modelId) {
    return state.selection.type === 'model' &&
           Array.isArray(state.selection.modelIds) &&
           state.selection.modelIds.includes(modelId);
}
```

### Click Behavior

| Action | Behavior |
|--------|----------|
| Click model (no modifier) | Single-select: `setSelection('model', id, ..., [id], id)` |
| Ctrl/Cmd + click model | Toggle model in/out of `modelIds` |
| Shift + click model | Same as Ctrl (toggle) |
| Click blank | `setSelection('project')` → clears all + disables tools |

Implementation: In the 2D mousedown handler (`_tool === 'select'`), when a model is hit:
```javascript
if (event.ctrlKey || event.metaKey || event.shiftKey) {
    // Toggle multi-select (same logic as 3D's Ctrl+click)
    var currentIds = getSelectedModelIds();
    var idSet = new Set(currentIds);
    if (idSet.has(hitModelId)) { idSet.delete(hitModelId); } else { idSet.add(hitModelId); }
    var nextIds = state.models.map(m => m.id).filter(id => idSet.has(id));
    var anchor = state.selection.anchorModelId || hitModelId;
    setSelection('model', nextIds[0] || null, ..., nextIds, anchor);
} else {
    // Single select
    setSelection('model', hitModelId, ..., [hitModelId], hitModelId);
}
```

### 2D Highlight Rendering

In `_draw()`, replace single-model highlight with loop over `getSelectedModelIds()`. Each selected model gets:
- Blue highlight border
- Bottom-face projected polygon (existing OBB rendering)
- Rotation ring (if rotate tool enabled)

---

## Section 3: Multi-Model Move in 2D

### Current State

2D drag logic tracks `_dragModel = { id, startX, startY, anchorStartX, anchorStartY }` for a single model.

### Change

When drag starts on any selected model with move tool enabled:
- Store start positions for ALL selected models with move enabled
- During drag, compute world-space delta from the drag anchor model
- Apply the same delta to all selected models

```javascript
_dragModels = selectedModels.filter(m => ensureModelMoveTool(m).enabled).map(m => ({
    id: m.id,
    anchorStartX: m.rack ? m.rack.geometry.anchor.x : m.base.anchor.x,
    anchorStartY: m.rack ? m.rack.geometry.anchor.y : m.base.anchor.y,
}));
// In mousemove: for each model, newAnchor = startAnchor + worldDelta
```

---

## Section 4: Multi-Model Rotation

### 2D Rotation Rings

**Current**: `_draw()` only renders one ring for `_selectedModelId`.

**New**: Render a ring for every model in `getSelectedModelIds()` that has `rotateTool.enabled && axes.yaw`. Each ring drawn at its own OBB centroid.

### 2D Ring Hit Test

Mousedown iterates over all rendered rings (all selected models with rotate enabled). First hit wins → becomes the drag anchor.

### 2D Rotation Drag

During drag:
1. Compute angle delta from the anchor ring's centroid (same as current single-model logic)
2. Apply the same angle delta to ALL selected models with rotate enabled
3. Each model rotates around its own Z-axis center:
   ```javascript
   selectedRotateModels.forEach(m => {
       // Apply angleDelta as quaternion rotation around Z axis
       // Same math as current single-model rotation
   });
   ```
4. Snap angle applies uniformly to all models

### 3D Gizmo Multi-Model

**Current**: `createRotationGizmo(model)` and `refreshRotationGizmo()` handle one model.

**New**: `refreshRotationGizmo()` iterates `getSelectedModelIds()`, creates a ring for each model with rotate enabled. Drag on any ring rotates all selected models.

The existing 3D gizmo creates `TorusGeometry` meshes — extend to create one set per model, tag each with `userData.modelId`.

### Right Panel Sync

During rotation drag, `updateUI()` already shows multi-select info. Yaw angle display: show the anchor model's angle, or show "multiple" if angles differ.

---

## Section 5: Files to Modify

| File | Change |
|------|--------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | `setSelection`: disable tools on 'project', remove 2D auto-activate; `refreshRotationGizmo`: multi-model gizmo; `beginRotationGizmoDrag`/`updateRotationGizmoDrag`: apply to all selected; `toggleModelMoveTool`/`toggleModelRotateTool`: handle multi-select |
| `STLGeneratorForOpenFoam/web/room_2d_editor.js` | Replace `_selectedModelId` with `state.selection.modelIds`; Ctrl/Shift multi-select; multi-model highlight; multi-model drag move; multi-model rotation ring render + drag |

---

## Out of Scope

- Shift+click range selection (select all models between two clicks) — only toggle behavior
- 2D box-select for models (box select remains for room polygon vertices/edges only)
- Multi-model pitch/roll rotation (only yaw/Z-axis for multi-rotate)
- Custom per-model rotation axis in multi-rotate (all use Z-axis)
