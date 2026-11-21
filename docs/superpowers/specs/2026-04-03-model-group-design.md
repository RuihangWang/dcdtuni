# Model Group (编组) — Design Spec

**Date**: 2026-04-03
**File**: `STLGeneratorForOpenFoam/web/STL_generate.html`
**Goal**: Allow users to group same-kind models (racks, ACUs, etc.) for batch operations and organized tree display.

---

## 1. Data Model

Add `groups` array to `state`:

```javascript
state.groups = [
    {
        id: "grp_1712345678",       // unique ID (timestamp-based)
        name: "Rack Group A",       // user-editable
        memberIds: ["model_1", "model_2", "model_5"],  // ordered
        collapsed: false            // tree collapse state
    }
];
```

**Rules**:
- Each model belongs to at most ONE group (`memberIds` across all groups are disjoint)
- Group must have >= 2 members; auto-dissolve when members drop below 2
- All members of a group must be the same `kind` (rack, acu, box, etc.)
- Groups are JSON-serializable, saved/loaded with `.cfdproj` project files
- Groups do NOT affect CFD export, boundary conditions, or simulation parameters
- Maintain `_modelGroupMap = new Map(modelId -> groupId)` for O(1) reverse lookup, synced on group mutations

**Undo**: All group operations (create, dissolve, add, remove member) call `pushUndoCheckpoint()` first. Groups are part of `state` so they're included in undo snapshots automatically.

---

## 2. Model Tree Display

Groups appear between Category and Model in the tree hierarchy:

```
📦 RACK (12)
  📁 Rack Group A (3)       ← group node (collapsible)
    🗄️ Rack_1                ← indented member
    🗄️ Rack_2
    🗄️ Rack_5
  📁 Rack Group B (2)
    🗄️ Rack_3
    🗄️ Rack_4
  🗄️ Rack_6                 ← ungrouped rack, same level as before
  🗄️ Rack_7
❄️ ACU (3)
  📁 ACU Group 1 (2)
    ❄️ ACU_1
    ❄️ ACU_2
  ❄️ ACU_3
```

**Group node behavior**:
- Icon: 📁
- Shows: group name (editable) + member count badge
- Collapsible: click expander to show/hide member list (3D view unaffected)
- **Click group node** → `setSelection('model', firstMemberId, ..., allMemberIds)` — multi-select all members
- **Shift+click group node** → range select (integrates with existing category shift-click system)
- **Drag-drop**: group nodes can be reordered within their category; models can be dragged into/out of groups

**Rendering in `getTreeHtmlForModel` / `updateUI`**:
- In the category loop, render grouped models under their group node (indented)
- Render ungrouped models at the same level as before
- Group ordering: groups appear first within a category, then ungrouped models

---

## 3. Group Operations

### 3a. Create Group

**Trigger**: Multi-select >= 2 models of the same `kind`, right Inspector shows `[📁 Create Group]` button.

**Flow**:
1. User multi-selects racks (Ctrl+click, Shift+click, or Shift+click in viewport)
2. Inspector panel shows "Create Group" button (only for same-kind multi-select)
3. Click → create group with default name `{Kind} Group {N}` (e.g., "Rack Group 1", auto-increment skipping existing numbers)
4. Inline name editor appears for immediate rename
5. Selected models move under the new group in the tree
6. If any selected model was in another group, it's removed from the old group (old group auto-dissolves if < 2 members remain)

### 3b. Dissolve Group

**Trigger**: Click group node in tree → Inspector shows `[Dissolve Group]` button.

**Flow**: Members return to category flat level, group removed from `state.groups`.

### 3c. Remove Single Model from Group

**Trigger**: Select a grouped model → Inspector shows `[Remove from Group]` button.

**Flow**: Model moves to category flat level. If group drops below 2 members, auto-dissolve.

### 3d. Add to Existing Group

**Trigger**: Select ungrouped model(s) of the same kind as an existing group → Inspector shows `[Add to: {Group Name} ▾]` dropdown.

**Alternative**: Drag model onto group node in tree.

---

## 4. Batch Operations on Multi-Selected Models

When a group is clicked (selecting all members) or models are multi-selected manually, these operations apply to ALL selected models:

| Operation | Behavior | Status |
|-----------|----------|--------|
| **Copy** (⧉ button) | Duplicate all selected models, copies auto-grouped with name "{Original Group} Copy" | NEW |
| **Delete** (✖ button) | Delete all selected models, group auto-dissolves | EXISTS (removeModel supports batch) |
| **Move** (drag) | All selected models move together | EXISTS (multi-move) |
| **Rotate** | Each model rotates around its own anchor (90° increments) | NEW — add buttons to multi-select Inspector |
| **Mirror** | Each model mirrors around its own anchor (X/Y/Z axis) | NEW — add buttons to multi-select Inspector |
| **Opacity** | Slider applies to all selected models | EXISTS |

### Batch Copy Details
- Each selected model is duplicated with the same offset logic as single-model duplicate
- If the originals were in a group, copies are placed in a new group named `{Original Group Name} Copy`
- If originals were not in a group, copies are not grouped

### Batch Rotate/Mirror
- Add rotate (90° CW/CCW around Z) and mirror (X/Y/Z) buttons to `renderMultiSelectInspector`
- Each model transforms around its own anchor point (not the group centroid)

---

## 5. Shift+Click Viewport Multi-Select

**Current**: Click rack in viewport → selects rack face (face-level)
**New**: Shift+Click any model in viewport → model-level toggle multi-select

**Implementation** in `handleViewportSelection`:

```javascript
if (event.shiftKey && modelId) {
    // Shift+click: model-level multi-select toggle (skip face/slot/patch)
    const currentIds = getSelectedModelIds();
    // If current selection is not model-type, start fresh from current model context
    const baseIds = (state.selection.type === 'model' && currentIds.length)
        ? currentIds
        : (state.selection.modelId ? [state.selection.modelId] : []);
    const idSet = new Set(baseIds);
    if (idSet.has(modelId)) idSet.delete(modelId);
    else idSet.add(modelId);
    const nextIds = state.models.map(m => m.id).filter(id => idSet.has(id));
    if (!nextIds.length) { setSelection('project'); return; }
    setSelection('model', nextIds[0], null, null, null, null, null, null, null, null, null, null, nextIds, nextIds[0]);
    return;
}
```

**Rules**:
- Shift+Click on any model → toggle that model in/out of multi-selection at model-level
- Works regardless of what sub-element (face, slot, patch) the click landed on
- Does NOT enter face/slot/patch selection — always promotes to model-level
- First Shift+Click starts a new multi-select (if nothing was multi-selected before, the previously selected model becomes the first member)
- Both Shift+Click and Ctrl+Click trigger model-level multi-select in viewport (consistent behavior)

---

## 6. State Normalization

In `normalizeState()`, add:

```javascript
if (!state.groups) state.groups = [];
state.groups = state.groups.filter(g => g && g.id && Array.isArray(g.memberIds));
// Remove invalid member references
state.groups.forEach(g => {
    g.memberIds = g.memberIds.filter(id => state.models.some(m => m.id === id));
});
// Auto-dissolve groups with < 2 members
state.groups = state.groups.filter(g => g.memberIds.length >= 2);
```

When a model is deleted (`removeModel`), also clean up `state.groups`:
```javascript
state.groups.forEach(g => {
    g.memberIds = g.memberIds.filter(id => !deletedIds.has(id));
});
state.groups = state.groups.filter(g => g.memberIds.length >= 2);
_rebuildModelGroupMap();
```

---

## 7. Helper Functions

```javascript
// Reverse lookup map
const _modelGroupMap = new Map(); // modelId → groupId

function _rebuildModelGroupMap() {
    _modelGroupMap.clear();
    (state.groups || []).forEach(g => {
        g.memberIds.forEach(id => _modelGroupMap.set(id, g.id));
    });
}

function getModelGroup(modelId) {
    const gid = _modelGroupMap.get(modelId);
    return gid ? state.groups.find(g => g.id === gid) : null;
}

function getGroupById(groupId) {
    return state.groups.find(g => g.id === groupId) || null;
}

function createModelGroup(name, memberIds) {
    pushUndoCheckpoint('createGroup');
    const group = {
        id: 'grp_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
        name,
        memberIds: [...memberIds],
        collapsed: false
    };
    // Remove members from any existing groups
    memberIds.forEach(id => {
        const old = getModelGroup(id);
        if (old) old.memberIds = old.memberIds.filter(mid => mid !== id);
    });
    // Auto-dissolve emptied groups
    state.groups = state.groups.filter(g => g.memberIds.length >= 2);
    state.groups.push(group);
    _rebuildModelGroupMap();
    rebuildGeometry();
    logOperation('createGroup', `Created group: ${name}`, { groupId: group.id, count: memberIds.length }, 'modify');
}

function dissolveModelGroup(groupId) {
    pushUndoCheckpoint('dissolveGroup');
    state.groups = state.groups.filter(g => g.id !== groupId);
    _rebuildModelGroupMap();
    updateUI();
    logOperation('dissolveGroup', `Dissolved group`, { groupId }, 'modify');
}
```

---

## Non-Goals

- Groups do NOT affect CFD export, boundary conditions, or solver parameters
- No nested groups (groups cannot contain other groups)
- No cross-kind groups (all members must be the same model kind)
- No group-level CFD parameter inheritance
- No group-level visibility toggle (use existing per-model toggles)

---

## Testing

1. Multi-select 3 racks → click "Create Group" → verify tree shows group with 3 indented members
2. Click group node → verify all 3 racks are multi-selected + highlighted in 3D
3. Shift+click group node → verify range select works with other groups/categories
4. Click "Dissolve Group" → verify members return to flat category level
5. Select grouped model → "Remove from Group" → verify removal + auto-dissolve if < 2
6. Batch copy grouped racks → verify copies are in a new group
7. Batch rotate/mirror → verify each model transforms around its own anchor
8. Shift+click racks in viewport → verify model-level multi-select (not face-level)
9. Save/load project → verify groups persist in .cfdproj
10. Undo/redo group operations → verify correct state restoration
