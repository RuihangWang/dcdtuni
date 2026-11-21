# Model Group (编组) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add generic model grouping — users can group same-kind models (racks, ACUs, etc.) for batch operations and organized tree display.

**Architecture:** All changes in `STLGeneratorForOpenFoam/web/STL_generate.html`. Add `state.groups[]` array, `_modelGroupMap` for O(1) reverse lookup, group nodes in the model tree, group CRUD operations in the Inspector, Shift+click viewport multi-select, and batch rotate/mirror/copy operations.

**Tech Stack:** Three.js r128 (vanilla JS, single HTML file, no build system)

**Spec:** `docs/superpowers/specs/2026-04-03-model-group-design.md`

---

### Task 1: Data Model — state.groups, helpers, normalization

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add `_modelGroupMap` declaration near other Maps (~line 1658)**

After the `_renderRootMap` declaration, add:

```javascript
    const _modelGroupMap = new Map();  // modelId → groupId (reverse lookup for groups)
```

- [ ] **Step 2: Add group helper functions after `_rebuildModelMap` (~line 2937)**

Insert after `_rebuildModelMap()`:

```javascript
    function _rebuildModelGroupMap() {
        _modelGroupMap.clear();
        (state.groups || []).forEach(g => {
            g.memberIds.forEach(id => _modelGroupMap.set(id, g.id));
        });
    }

    function getModelGroup(modelId) {
        const gid = _modelGroupMap.get(modelId);
        return gid ? (state.groups || []).find(g => g.id === gid) || null : null;
    }

    function getGroupById(groupId) {
        return (state.groups || []).find(g => g.id === groupId) || null;
    }

    function _autoDissolveGroups() {
        state.groups = (state.groups || []).filter(g => g.memberIds.length >= 2);
    }

    function _nextGroupName(kind) {
        const prefix = (kind.charAt(0).toUpperCase() + kind.slice(1)) + ' Group ';
        const existing = new Set((state.groups || []).map(g => g.name));
        for (let n = 1; ; n++) {
            const name = prefix + n;
            if (!existing.has(name)) return name;
        }
    }

    window.createModelGroup = function(name, memberIds) {
        pushUndoCheckpoint('createGroup');
        const group = {
            id: 'grp_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
            name: name,
            memberIds: [...memberIds],
            collapsed: false
        };
        // Remove members from any existing groups
        memberIds.forEach(id => {
            const old = getModelGroup(id);
            if (old) old.memberIds = old.memberIds.filter(mid => mid !== id);
        });
        _autoDissolveGroups();
        state.groups.push(group);
        _rebuildModelGroupMap();
        updateUI();
        if (window.requestRender) window.requestRender();
        logOperation('createGroup', `Created group: ${name}`, { groupId: group.id, count: memberIds.length }, 'modify');
    };

    window.dissolveModelGroup = function(groupId) {
        pushUndoCheckpoint('dissolveGroup');
        const group = getGroupById(groupId);
        const name = group ? group.name : groupId;
        state.groups = state.groups.filter(g => g.id !== groupId);
        _rebuildModelGroupMap();
        updateUI();
        if (window.requestRender) window.requestRender();
        logOperation('dissolveGroup', `Dissolved group: ${name}`, { groupId }, 'modify');
    };

    window.removeModelFromGroup = function(modelId) {
        pushUndoCheckpoint('removeFromGroup');
        const group = getModelGroup(modelId);
        if (!group) return;
        group.memberIds = group.memberIds.filter(id => id !== modelId);
        _autoDissolveGroups();
        _rebuildModelGroupMap();
        updateUI();
        if (window.requestRender) window.requestRender();
        logOperation('removeFromGroup', `Removed model from group`, { modelId, groupId: group.id }, 'modify');
    };

    window.addModelsToGroup = function(groupId, modelIds) {
        pushUndoCheckpoint('addToGroup');
        const group = getGroupById(groupId);
        if (!group) return;
        modelIds.forEach(id => {
            const old = getModelGroup(id);
            if (old && old.id !== groupId) old.memberIds = old.memberIds.filter(mid => mid !== id);
            if (!group.memberIds.includes(id)) group.memberIds.push(id);
        });
        _autoDissolveGroups();
        _rebuildModelGroupMap();
        updateUI();
        if (window.requestRender) window.requestRender();
        logOperation('addToGroup', `Added models to group: ${group.name}`, { groupId, count: modelIds.length }, 'modify');
    };

    window.renameModelGroup = function(groupId, newName) {
        pushUndoCheckpoint('renameGroup');
        const group = getGroupById(groupId);
        if (!group) return;
        group.name = newName;
        updateUI();
        logOperation('renameGroup', `Renamed group to: ${newName}`, { groupId }, 'modify');
    };
```

- [ ] **Step 3: Add groups normalization in `normalizeState` (~line 2401)**

Find `normalizeState` function. Before the `return next;` line, add:

```javascript
        // Groups normalization
        if (!next.groups) next.groups = [];
        next.groups = (next.groups || []).filter(g => g && g.id && Array.isArray(g.memberIds));
        const validModelIds = new Set(next.models.map(m => m.id));
        next.groups.forEach(g => {
            g.memberIds = g.memberIds.filter(id => validModelIds.has(id));
            if (typeof g.collapsed !== 'boolean') g.collapsed = false;
            if (typeof g.name !== 'string') g.name = 'Group';
        });
        next.groups = next.groups.filter(g => g.memberIds.length >= 2);
```

- [ ] **Step 4: Call `_rebuildModelGroupMap()` after state replacement**

Find both calls to `_rebuildModelMap()` (in `restoreStateFromSnapshot` and `_applyProjectData`). Add `_rebuildModelGroupMap();` right after each `_rebuildModelMap();` call.

Also add `_rebuildModelGroupMap();` at the end of each group mutation function that calls `_rebuildModelGroupMap` (already done in Step 2).

- [ ] **Step 5: Clean up groups when models are deleted in `removeModel` (~line 9826)**

Find `removeModel`. After `idSet.forEach(id => _modelMap.delete(id));` add:

```javascript
        // Clean up groups — remove deleted models and auto-dissolve
        (state.groups || []).forEach(g => {
            g.memberIds = g.memberIds.filter(id => !idSet.has(id));
        });
        _autoDissolveGroups();
        _rebuildModelGroupMap();
```

- [ ] **Step 6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(group): add data model, helpers, and state normalization

state.groups[] stores model groups with id, name, memberIds, collapsed.
_modelGroupMap provides O(1) reverse lookup. Group CRUD functions:
createModelGroup, dissolveModelGroup, removeModelFromGroup,
addModelsToGroup, renameModelGroup. Groups auto-dissolve when < 2 members.
normalizeState cleans up invalid groups. removeModel cleans up groups."
```

---

### Task 2: Model Tree — render group nodes

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add `selectGroupNode` and `toggleGroupCollapse` window functions**

Insert after the group helper functions from Task 1:

```javascript
    window.selectGroupNode = function(event, groupId) {
        const group = getGroupById(groupId);
        if (!group || !group.memberIds.length) return;

        if (event && event.shiftKey && (state._anchorCategory || state._lastClickedCategory)) {
            // Shift+click: treat group as a category-level item for range select
            // Select all members of this group
            const ids = group.memberIds.filter(id => !!getModelById(id));
            if (ids.length) {
                setSelection('model', ids[0], null, null, null, null, null, null, null, null, null, null, ids, ids[0]);
            }
            return;
        }

        // Normal click: select all members
        const ids = group.memberIds.filter(id => !!getModelById(id));
        if (!ids.length) return;
        setSelection('model', ids[0], null, null, null, null, null, null, null, null, null, null, ids, ids[0]);
        logOperation('selectGroup', `Selected group: ${group.name}`, { groupId, count: ids.length }, 'select');
    };

    window.toggleGroupCollapse = function(event, groupId) {
        event.stopPropagation();
        const group = getGroupById(groupId);
        if (!group) return;
        group.collapsed = !group.collapsed;
        updateUI();
    };
```

- [ ] **Step 2: Modify the category rendering loop in `updateUI` (~line 8835-8848)**

Find the category loop that currently does:
```javascript
catModels.forEach(model => { html += getTreeHtmlForModel(model); });
```

Replace it with a version that renders groups first, then ungrouped models:

```javascript
                        // Render grouped models under group nodes
                        const catGroups = (state.groups || []).filter(g => {
                            const firstMember = getModelById(g.memberIds[0]);
                            return firstMember && firstMember.kind === cat.kind;
                        });
                        catGroups.forEach(group => {
                            const groupMembers = group.memberIds.map(id => getModelById(id)).filter(Boolean);
                            if (!groupMembers.length) return;
                            const anyMemberSelected = groupMembers.some(m => isSelectionWithinModel(m.id));
                            const allMembersSelected = state.selection.type === 'model' && groupMembers.every(m => getSelectedModelIds().includes(m.id));
                            const groupKey = `group:${group.id}`;
                            html += `<li class="tree-node"><div class="node-label${anyMemberSelected ? ' selected' : ''}" onclick="selectGroupNode(event, '${group.id}')">`;
                            html += `<span class="node-main">${getTreeExpander(groupKey, true, () => toggleGroupCollapse(event, '${group.id}'))} 📁 <span contenteditable="false" class="group-name-label" ondblclick="this.contentEditable=true;this.focus();" onblur="renameModelGroup('${group.id}', this.textContent.trim()); this.contentEditable=false;" onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}">${group.name}</span></span>`;
                            html += `<span class="badge">${groupMembers.length}</span></div>`;
                            if (!isTreeCollapsed(groupKey)) {
                                html += `<ul>`;
                                groupMembers.forEach(model => { html += getTreeHtmlForModel(model); });
                                html += `</ul>`;
                            }
                            html += `</li>`;
                        });
                        // Render ungrouped models
                        const groupedIds = new Set(catGroups.flatMap(g => g.memberIds));
                        catModels.filter(m => !groupedIds.has(m.id)).forEach(model => {
                            html += getTreeHtmlForModel(model);
                        });
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(group): render group nodes in model tree

Groups appear between category and model level. Click group node
to multi-select all members. Groups are collapsible. Group name
is inline-editable via double-click."
```

---

### Task 3: Inspector UI — create/dissolve/remove/add-to-group buttons

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add "Create Group" button to `renderMultiSelectInspector` (~line 8075)**

Find `renderMultiSelectInspector`. After the opening HTML (around the title/hint area), add a "Create Group" button that only shows when all selected models are the same kind:

```javascript
        // Group creation button — only for same-kind multi-select
        const selectedKinds = new Set(models.map(m => m.kind));
        if (selectedKinds.size === 1 && ids.length >= 2) {
            const kind = models[0].kind;
            const defaultName = _nextGroupName(kind);
            html += `<div class="prop-group" style="border-color:rgba(0,212,255,0.4)"><div class="prop-title" style="color:var(--accent3)">Group</div>`;
            // Check if all selected are already in the same group
            const existingGroup = getModelGroup(ids[0]);
            const allInSameGroup = existingGroup && ids.every(id => {
                const g = getModelGroup(id);
                return g && g.id === existingGroup.id;
            });
            if (allInSameGroup && existingGroup) {
                html += `<div class="prop-row"><label>Group: ${existingGroup.name}</label></div>`;
                html += `<button class="btn" onclick="dissolveModelGroup('${existingGroup.id}')" style="margin-bottom:8px">Dissolve Group</button>`;
            } else {
                html += `<button class="btn primary" onclick="createModelGroup('${defaultName}', ${JSON.stringify(ids)})" style="margin-bottom:8px">📁 Create Group</button>`;
            }
            // Add to existing group dropdown
            const compatibleGroups = (state.groups || []).filter(g => {
                const firstMember = getModelById(g.memberIds[0]);
                return firstMember && firstMember.kind === kind && !ids.every(id => g.memberIds.includes(id));
            });
            if (compatibleGroups.length > 0) {
                html += `<div class="prop-row"><label>Add to group</label><select onchange="if(this.value) addModelsToGroup(this.value, ${JSON.stringify(ids)}); this.value='';">`;
                html += `<option value="">-- Select --</option>`;
                compatibleGroups.forEach(g => {
                    html += `<option value="${g.id}">${g.name} (${g.memberIds.length})</option>`;
                });
                html += `</select></div>`;
            }
            html += `</div>`;
        }
```

- [ ] **Step 2: Add "Remove from Group" button for single grouped model in model inspector**

Find `renderModelInspector` (~line 8075). Near the top of the function, after the opening HTML, add:

```javascript
        // "Remove from Group" button for grouped models
        const _modelGroup = getModelGroup(model.id);
        if (_modelGroup) {
            html += `<div class="prop-group" style="border-color:rgba(0,212,255,0.4)"><div class="prop-title" style="color:var(--accent3)">Group: ${_modelGroup.name}</div>`;
            html += `<button class="btn" onclick="removeModelFromGroup('${model.id}')" style="margin-bottom:4px">Remove from Group</button>`;
            html += `</div>`;
        }
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(group): add group CRUD buttons to Inspector

Multi-select Inspector shows Create Group / Dissolve Group / Add to
Group controls. Single model Inspector shows Remove from Group when
the model belongs to a group."
```

---

### Task 4: Shift+Click viewport multi-select

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add Shift+click handling in `handleViewportSelection` (~line 12307)**

Find the existing Ctrl+click block:
```javascript
        if (!isStlPreviewMode() && (event.ctrlKey || event.metaKey) && modelId) {
```

Change the condition to also include `event.shiftKey`:

```javascript
        if (!isStlPreviewMode() && (event.ctrlKey || event.metaKey || event.shiftKey) && modelId) {
```

This makes Shift+Click behave identically to Ctrl+Click — model-level toggle multi-select, skipping face/slot/patch selection.

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(group): Shift+Click viewport multi-select

Shift+Click in viewport now triggers model-level toggle multi-select,
same as Ctrl+Click. Skips face/slot/patch selection — always promotes
to model-level."
```

---

### Task 5: Batch copy for multi-selected models

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add `window.duplicateSelectedModels` function**

Insert after the existing `duplicateModel` function (~line 9856):

```javascript
    window.duplicateSelectedModels = function(event) {
        if (event) event.stopPropagation();
        const ids = getSelectedModelIds();
        if (ids.length < 2) {
            // Single model: use existing duplicateModel
            if (ids.length === 1) duplicateModel(event, ids[0]);
            return;
        }
        pushUndoCheckpoint('duplicateMulti');
        const newIds = [];
        const origGroup = getModelGroup(ids[0]);
        const allInSameGroup = origGroup && ids.every(id => {
            const g = getModelGroup(id);
            return g && g.id === origGroup.id;
        });
        ids.forEach(origId => {
            const orig = getModelById(origId);
            if (!orig) return;
            const dup = cloneModelForDuplication(orig);
            state.models.push(dup);
            _modelMap.set(dup.id, dup);
            placeDuplicatedModelWithoutOverlap(dup, orig.id);
            newIds.push(dup.id);
        });
        // Auto-group copies if originals were grouped
        if (allInSameGroup && origGroup && newIds.length >= 2) {
            const newGroupName = origGroup.name + ' Copy';
            const group = {
                id: 'grp_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
                name: newGroupName,
                memberIds: [...newIds],
                collapsed: false
            };
            state.groups.push(group);
            _rebuildModelGroupMap();
        }
        rebuildGeometry();
        if (newIds.length) {
            setSelection('model', newIds[0], null, null, null, null, null, null, null, null, null, null, newIds, newIds[0]);
        }
        logOperation('duplicateMulti', `Duplicated ${ids.length} models`, { count: ids.length }, 'modify');
    };
```

- [ ] **Step 2: Wire up the ⧉ button in multi-select context**

Find the duplicate button in `getTreeHtmlForModel` (~line 6543). The button currently calls `duplicateModel(event, '${model.id}')`. When multi-selected, it should duplicate all selected models.

In `renderMultiSelectInspector`, add a batch duplicate button:

```javascript
        html += `<div style="display:flex; gap:8px; margin-bottom:12px;">`;
        html += `<button class="btn" onclick="duplicateSelectedModels(event)" title="Duplicate all selected">⧉ Duplicate All (${ids.length})</button>`;
        html += `</div>`;
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(group): batch duplicate for multi-selected models

duplicateSelectedModels copies all selected models. If originals are
in the same group, copies are auto-grouped with name '{Group} Copy'.
Batch duplicate button added to multi-select Inspector."
```

---

### Task 6: Batch rotate/mirror for multi-selected models

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add `window.rotateSelectedModels` and `window.mirrorSelectedModels` functions**

Insert after `duplicateSelectedModels`:

```javascript
    window.rotateSelectedModels = function(axis, angleDeg) {
        const ids = getSelectedModelIds();
        if (!ids.length) return;
        pushUndoCheckpoint('rotateMulti');
        const angleRad = angleDeg * Math.PI / 180;
        const cosA = Math.cos(angleRad);
        const sinA = Math.sin(angleRad);
        ids.forEach(id => {
            const model = getModelById(id);
            if (!model) return;
            // Rotate base dimensions and anchor around model's own anchor
            if (axis === 'z') {
                // Swap W and D for 90° rotation around Z
                if (Math.abs(angleDeg) === 90 || Math.abs(angleDeg) === 270) {
                    const tmp = model.base.W;
                    model.base.W = model.base.D;
                    model.base.D = tmp;
                }
            }
            // Update orientation if model has one
            if (model.orientation != null) {
                model.orientation = ((model.orientation || 0) + angleDeg + 360) % 360;
            }
            syncModelRenderTransform(model);
        });
        ids.forEach(id => rebuildSingleModel(id));
        updateUI();
        if (window.requestRender) window.requestRender();
        logOperation('rotateMulti', `Rotated ${ids.length} models ${angleDeg}° around ${axis}`, { count: ids.length, axis, angleDeg }, 'modify');
    };

    window.mirrorSelectedModels = function(axis) {
        const ids = getSelectedModelIds();
        if (!ids.length) return;
        pushUndoCheckpoint('mirrorMulti');
        // Mirror each model around its own anchor by negating the relevant dimension offset
        // For axis X: negate anchor.x relative to group centroid
        // Simple approach: swap face assignments for the mirrored axis
        ids.forEach(id => {
            const model = getModelById(id);
            if (!model) return;
            // Mirror orientation
            if (axis === 'x') {
                if (model.orientation === 0) model.orientation = 180;
                else if (model.orientation === 180) model.orientation = 0;
                else if (model.orientation === 90) model.orientation = 270;
                else if (model.orientation === 270) model.orientation = 90;
            } else if (axis === 'y') {
                if (model.orientation === 0) model.orientation = 180;
                else if (model.orientation === 180) model.orientation = 0;
                else if (model.orientation === 90) model.orientation = 270;
                else if (model.orientation === 270) model.orientation = 90;
            }
            syncModelRenderTransform(model);
        });
        ids.forEach(id => rebuildSingleModel(id));
        updateUI();
        if (window.requestRender) window.requestRender();
        logOperation('mirrorMulti', `Mirrored ${ids.length} models on ${axis} axis`, { count: ids.length, axis }, 'modify');
    };
```

- [ ] **Step 2: Add rotate/mirror buttons to `renderMultiSelectInspector`**

Add after the batch duplicate button:

```javascript
        html += `<div class="prop-group" style="border-color:rgba(229,192,123,0.4)"><div class="prop-title" style="color:var(--hint-color)">Transform</div>`;
        html += `<div style="display:flex; gap:6px; margin-bottom:8px; flex-wrap:wrap;">`;
        html += `<button class="btn" onclick="rotateSelectedModels('z', 90)" title="Rotate 90° CW">↻ 90°</button>`;
        html += `<button class="btn" onclick="rotateSelectedModels('z', -90)" title="Rotate 90° CCW">↺ 90°</button>`;
        html += `<button class="btn" onclick="rotateSelectedModels('z', 180)" title="Rotate 180°">↻ 180°</button>`;
        html += `</div>`;
        html += `<div style="display:flex; gap:6px; flex-wrap:wrap;">`;
        html += `<button class="btn" onclick="mirrorSelectedModels('x')" title="Mirror X axis">⇔ Mirror X</button>`;
        html += `<button class="btn" onclick="mirrorSelectedModels('y')" title="Mirror Y axis">⇕ Mirror Y</button>`;
        html += `</div></div>`;
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat(group): batch rotate and mirror for multi-selected models

rotateSelectedModels rotates each model around its own anchor.
mirrorSelectedModels mirrors orientation. Buttons added to
multi-select Inspector: 90°/180° rotate and X/Y mirror."
```

---

### Task 7: Manual testing

- [ ] **Step 1: Start server and load a large case**

```bash
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir dctwin/case &
```

- [ ] **Step 2: Test group creation**

1. Load alibaba-hangchou or CMI-Guichou
2. Ctrl+click to select 3 racks → Inspector should show "Create Group" button
3. Click "Create Group" → tree should show group node with 3 indented members
4. Double-click group name → rename → verify name updates

- [ ] **Step 3: Test group selection**

1. Click group node in tree → all 3 racks should be multi-selected + highlighted in 3D
2. Shift+click a rack in the viewport → should add to multi-select (model-level)

- [ ] **Step 4: Test group operations**

1. Click "Dissolve Group" → members return to flat level
2. Create group again → select one member → "Remove from Group" → verify removal
3. If only 1 member left, group should auto-dissolve

- [ ] **Step 5: Test batch operations**

1. Select group → "Duplicate All" → verify copies are in a new group
2. Select group → rotate 90° → verify all racks rotated
3. Select group → delete → verify all removed and group dissolved

- [ ] **Step 6: Test persistence**

1. Create groups → save project → reload → verify groups persist
2. Ctrl+Z → verify group creation is undone
3. Ctrl+Y → verify redo works
