# Patch List Filter + Cross-Surface Selection Design

**Date:** 2026-04-18
**Scope:** `STLGeneratorForOpenFoam/web/STL_generate.html` — mesh-preview right panel + post-processing right panel
**Goal:** Add a filter bar and a selection-with-3D-highlight system to both patch-list surfaces. Lets users quickly locate one patch among many and batch-operate on selection.

---

## 1. Problem & Motivation

Two panels in the single-file frontend render a tree of boundary patch names, each row gated by a checkbox that controls 3D visibility:

| Surface | Container | Render fn | Mesh dict entry |
|---|---|---|---|
| Mesh preview (mesh tab → "显示网格") | `#mp-patch-list` | `mpBuildPatchList()` L23675 | `state.patchMeshes[name] = {surface, edges}` |
| Post-processing right panel | `#pp-tree` | `_ppBuildTree()` L21060 | `state.patchMeshes[name] = mesh` (with `mesh._ppPatchName` set) |

Real cases ship 50–200 patches. Finding one by eye is slow, multi-select is impossible, and there is no cross-link between the list row and the 3D geometry. Users asked for:

- **Substring/regex filtering** with case and whole-word toggles (VSCode-style).
- **Single-select highlight** (purple emissive in 3D + CSS class on the row).
- **Shift/Ctrl extension** for range and additive multi-select.
- **Checkbox batch operation** — with a multi-selection active, Shift/Ctrl + checkbox click applies the toggled visibility to every selected patch.
- **Bidirectional coupling** — clicking a patch face in the 3D viewport selects the corresponding list row (and vice versa, which the list click already implies).

Both panels share the grouped-tree helper `_buildGroupedPatchTree()` and the `state.patchMeshes` dict, so most of the new logic can be factored once and reused twice.

---

## 2. Architecture

Two small, pure modules live under `STLGeneratorForOpenFoam/web/js/`. Each site (mesh-preview / post-processing) keeps a ~60–100 line glue block inside its existing build function.

```
web/js/
  patch_filter.js       createFilterBar({container, onChange}) → {predicate, clear, getState, setState}
  patch_selection.js    createSelectionModel({onChange})       → {handleRowClick, handleCheckboxClick,
                                                                  setSelection, clear, has, getAll, getLastAnchor}
```

Dependency graph:

```
patch_filter.js    (pure JS, zero deps)   ──┐
                                            ├─→ site glue ─→ existing tree render + Three.js scene
patch_selection.js (pure JS, zero deps)   ──┘
```

The two modules know nothing about Three.js or the DOM; they compute predicates and selection-set transitions. Glue layers translate those into DOM class changes and Three.js material tweaks. The glue is site-specific because the two `patchMeshes` payload shapes differ.

No new global state: the selection `Set<string>` lives inside each model's closure; there's one model per site.

---

## 3. Filter Module: `createFilterBar`

### 3.1 DOM

Inserted at the top of each tree container before its existing content:

```html
<div class="patch-filter-bar">
  <button class="btn-toggle" data-mode="case"  title="区分大小写 (Aa)">Aa</button>
  <button class="btn-toggle" data-mode="whole" title="全字匹配 (ab|)">ab|</button>
  <button class="btn-toggle" data-mode="regex" title="正则 (.*)">.*</button>
  <input type="text" class="filter-input" placeholder="过滤 patch..." />
  <span class="filter-count">N / M</span>
</div>
```

Three toggle buttons are independent; any subset may be active. A small `filter-count` label shows `<matched> / <total>` in real time.

### 3.2 Predicate

| State | Predicate |
|---|---|
| input empty | `() => true` |
| regex on, valid | `(name) => pattern.test(name)` where `pattern = new RegExp(input, case ? '' : 'i')` |
| regex on, invalid | `() => false`; input gets red border + `title="invalid regex"` |
| regex off + whole on | `(name) => case ? name === input : name.toLowerCase() === input.toLowerCase()` |
| regex off + whole off | substring `includes()` with same case rule |

### 3.3 Timing and DOM application

Filter fires on every `input` event (no debounce; DOM size is ≤ ~200 rows). Glue layer passes the predicate to a helper `applyFilterToDOM(container, predicate)` which:

1. Walks every `[data-patch]` row, toggles `.filter-hidden` (CSS `display: none`).
2. After each group block, checks whether any child row is visible; if not, toggles `.filter-hidden-group` on the group header/wrapper so empty groups collapse.
3. Updates `filter-count` label.

### 3.4 State boundaries

- Filter **never** touches checkbox state, selection set, or `mesh.visible`. Clearing the input restores everything.
- On list rebuild (e.g. `mpLoadMesh()` re-fires, `ppInitPostProcess()` re-fires), the glue reads `bar.getState()` before rebuild and calls `bar.setState()` after so filter text + toggle positions survive.
- Invalid-regex state renders zero matches without throwing, and updates the red border.

---

## 4. Selection Model: `createSelectionModel`

### 4.1 State (internal to closure)

```js
selected:    Set<string>     // patch names currently selected
lastAnchor:  string | null   // anchor used by shift range
```

`orderedNames: string[]` — the list of patch names in current DOM order, **with filter-hidden rows excluded** — is **not** stored on the model. Glue passes it into every call, because it depends on DOM state the model shouldn't own.

### 4.2 Row-body click

```ts
handleRowClick(name, { shift, ctrl, orderedNames }) → void
```

| Modifier | Behavior |
|---|---|
| none | `selected = {name}`; `lastAnchor = name` |
| ctrl (or meta) | toggle `name` in `selected`; if added, `lastAnchor = name`; if removed, `lastAnchor` unchanged |
| shift | compute `[lastAnchor .. name]` slice of `orderedNames` (inclusive, either direction); `selected = slice`; `lastAnchor` unchanged |
| shift+ctrl | same slice, but **merged** into existing selection (additive range) |

Edge: shift with `lastAnchor === null` → degrade to single-select (same as no modifier).
Edge: `name` not in `orderedNames` (e.g. newly filter-hidden) → shift falls back to single-select on `name`.

### 4.3 Checkbox click

```ts
handleCheckboxClick(name, { shift, ctrl, nextVisible }) → { targets: string[], visible: boolean }
```

| Condition | Result |
|---|---|
| no modifier | `{ targets: [name], visible: nextVisible }` |
| shift or ctrl, `selected` non-empty | `{ targets: [...selected], visible: nextVisible }` (batch) |
| shift or ctrl, `selected` empty | degrade to single `{ targets: [name], visible: nextVisible }` (no silent no-op) |

The model **does not** mutate the DOM or call `mpUpdateVisibility` / `ppUpdatePatchVisibility`. It returns targets; glue applies them.

### 4.4 `setSelection(names)` + `clear()`

Both trigger `onChange(prevSet, currSet)`. Used by 3D raycaster (§6) and lifecycle resets (mesh reload, pp re-init).

### 4.5 `onChange(prev, curr)` event

The glue subscribes once. On each change it diffs the two sets and, for each added/removed name:

- Row DOM: add/remove `.row-selected` class (CSS: purple left border + faint purple background).
- 3D mesh: add/remove purple emissive highlight (§5).

---

## 5. 3D Highlight Rendering

### 5.1 Visual

Purple emissive tint overlaid on the patch's existing material. Original color remains visible (important for post-processing, where the surface may carry field colors).

Constants: `PURPLE = 0x9b59d0`, `emissiveIntensity = 0.6`.

### 5.2 Core helper (shared, inlined per site)

```js
function setPatchHighlight(mesh, on) {
  if (!mesh || !mesh.material) return;
  const m = mesh.material;
  if (on) {
    if (mesh.userData._origEmissive === undefined) {
      mesh.userData._origEmissive = m.emissive ? m.emissive.getHex() : 0x000000;
      mesh.userData._origEmissiveIntensity = m.emissiveIntensity ?? 1;
      if (!m.emissive) {
        // MeshBasicMaterial fallback: tint base color instead
        mesh.userData._origColor = m.color.getHex();
        mesh.userData._fallbackMode = true;
      }
    }
    if (mesh.userData._fallbackMode) {
      m.color.setHex(PURPLE);
    } else {
      m.emissive.setHex(PURPLE);
      m.emissiveIntensity = 0.6;
    }
  } else {
    if (mesh.userData._origEmissive !== undefined) {
      if (mesh.userData._fallbackMode) {
        m.color.setHex(mesh.userData._origColor);
      } else {
        m.emissive.setHex(mesh.userData._origEmissive);
        m.emissiveIntensity = mesh.userData._origEmissiveIntensity;
      }
    }
  }
  m.needsUpdate = true;
}
```

### 5.3 Site wrappers

```js
function mpHighlightPatch(name, on) {
  const entry = state.patchMeshes[name];
  if (!entry) return;
  setPatchHighlight(entry.surface, on);
  if (entry.edges && entry.edges.material) {
    entry.edges.material.color.setHex(on ? PURPLE : 0x000000);
    entry.edges.material.needsUpdate = true;
  }
}

function ppHighlightPatch(name, on) {
  setPatchHighlight(state.patchMeshes[name], on);
}
```

### 5.4 Why not outline/post-pass

The current renderer does not run an `EffectComposer`; adding one for outline would introduce cross-cutting pipeline changes. Emissive (with a MeshBasicMaterial color fallback) is the minimum-footprint option that visibly works across both surfaces.

---

## 6. 3D → List Reverse Coupling

### 6.1 Viewport click handler

Each viewport (mesh preview, post-processing) gets one mousedown/mouseup pair. `mousedown` records `(x, y)`; on `mouseup`, if the pointer moved < 3 px we treat it as a click and run raycasting. This keeps orbit-controls drag from firing spurious selections.

```js
function onViewportClick(e, { containerEl, model, resolveMeshToName, renderer, camera, raycaster }) {
  const rect = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(
    ((e.clientX - rect.left) / rect.width) * 2 - 1,
    -((e.clientY - rect.top) / rect.height) * 2 + 1,
  );
  raycaster.setFromCamera(ndc, camera);

  const meshes = collectSelectableMeshes();  // surface-only; skip edges lines
  const hit = raycaster.intersectObjects(meshes, false)[0];
  if (!hit) {
    if (!e.shiftKey && !(e.ctrlKey || e.metaKey)) model.clear();
    return;
  }
  const name = resolveMeshToName(hit.object);
  if (!name) return;

  const orderedNames = getVisibleOrderedNames(containerEl);
  model.handleRowClick(name, {
    shift: e.shiftKey,
    ctrl: e.ctrlKey || e.metaKey,
    orderedNames,
  });

  const row = containerEl.querySelector(`[data-patch="${CSS.escape(name)}"]`);
  if (row && !row.classList.contains('filter-hidden')) {
    row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}
```

### 6.2 `resolveMeshToName`

| Site | Implementation |
|---|---|
| Mesh preview | iterate `state.patchMeshes` entries, return `name` where `entry.surface === hit.object` |
| Post-processing | read `hit.object._ppPatchName` (already written by `_ppBuildTree` at L21377) |

### 6.3 Filter interaction

Clicking a face whose row is currently filter-hidden **still updates the selection**. The row stays hidden (the class is additive with `.row-selected`); when the user later clears the filter, the row appears already highlighted. `scrollIntoView` is skipped for hidden rows. This is documented as the intended silent behavior.

### 6.4 Integration with existing handlers

Before shipping, check each viewport's existing mousedown/mouseup for conflicts. Two cases:

- **No existing click listener** (likely for mesh preview): add ours directly on `renderer.domElement`.
- **Existing listener for slice / streamline picking** (possible in pp): add ours in the capture phase, gate it on "no active picking tool"; if a picking tool is active, defer to the tool.

OrbitControls uses its own pointer events on the canvas; our handler runs alongside since we gate on movement threshold.

---

## 7. Site Glue (Pattern, Repeated Twice)

Each site declares two module-scoped variables at the top of its JS section, persisting across rebuilds of the panel:

```js
// mesh-preview site:
let mpFilterBar = null;
let mpSelectionModel = null;
// post-processing site:
let ppFilterBar = null;
let ppSelectionModel = null;
```

The build function then does (using mp-prefixed names as illustration; pp is symmetric):

```js
// --- inside mpBuildPatchList() / _ppBuildTree() ---

// 1. Preserve filter + selection before rebuild (no-op on first run)
const prevFilter = mpFilterBar ? mpFilterBar.getState() : null;
const prevSelection = mpSelectionModel ? new Set(mpSelectionModel.getAll()) : new Set();

// 2. Let existing tree render run untouched
// ... existing rendering code ...

// 3. Insert filter bar at container top (if not already present)
if (!mpFilterBar) {
  mpFilterBar = createFilterBar({
    container: containerEl,
    onChange: (predicate) => {
      applyFilterToDOM(containerEl, predicate);
    },
  });
  if (prevFilter) mpFilterBar.setState(prevFilter);
}

// 4. Create selection model once
if (!mpSelectionModel) {
  mpSelectionModel = createSelectionModel({
    onChange: (prev, curr) => {
      diffSets(prev, curr, {
        added: (name) => {
          const row = containerEl.querySelector(`[data-patch="${CSS.escape(name)}"]`);
          row?.classList.add('row-selected');
          mpHighlightPatch(name, true);
        },
        removed: (name) => {
          const row = containerEl.querySelector(`[data-patch="${CSS.escape(name)}"]`);
          row?.classList.remove('row-selected');
          mpHighlightPatch(name, false);
        },
      });
    },
  });
}

// 5. Wire row-level click listener (capture checkbox vs body)
containerEl.querySelectorAll('[data-patch]').forEach((row) => {
  row.addEventListener('click', (e) => {
    const name = row.dataset.patch;
    const isCheckbox = e.target.matches('input[type=checkbox]');
    const shift = e.shiftKey;
    const ctrl = e.ctrlKey || e.metaKey;

    if (isCheckbox) {
      const cb = e.target;
      const nextVisible = cb.checked;          // browser already toggled before click
      const { targets, visible } = mpSelectionModel.handleCheckboxClick(name, {
        shift, ctrl, nextVisible,
      });
      targets.forEach((n) => {
        const otherRow = containerEl.querySelector(`[data-patch="${CSS.escape(n)}"]`);
        const otherCb  = otherRow?.querySelector('input[type=checkbox]');
        if (otherCb) otherCb.checked = visible;
        mpUpdateVisibility(n, visible);
      });
    } else {
      const orderedNames = getVisibleOrderedNames(containerEl);
      mpSelectionModel.handleRowClick(name, { shift, ctrl, orderedNames });
    }
  });
});

// 6. Restore prior selection (names that still exist in the new tree).
//    On a full lifecycle reset (mesh reload / pp re-init) the caller has
//    already invoked mpSelectionModel.clear() so prevSelection is empty and
//    this is a no-op; on incremental rebuilds it carries selection across.
const currentNames = new Set(Array.from(containerEl.querySelectorAll('[data-patch]'))
                                  .map(r => r.dataset.patch));
const restored = [...prevSelection].filter(n => currentNames.has(n));
if (restored.length) mpSelectionModel.setSelection(restored);

// 7. Wire viewport click (once per site)
ensureMpViewportClickHandler(containerEl, mpSelectionModel);

// 8. Re-apply filter if it was active
if (mpFilterBar) applyFilterToDOM(containerEl, mpFilterBar.predicate);
```

Lifecycle reset — `mpLoadMesh()` start / `ppInitPostProcess()` start — calls the corresponding `*SelectionModel.clear()` **before** the build function runs, so step 6's restore block sees an empty `prevSelection` and leaves the rebuilt list unselected. This keeps the reset path explicit and separate from the "partial rebuild" path (currently unused but cheap to keep working).

Per-site bindings:

| Binding | Mesh preview | Post-processing |
|---|---|---|
| `containerEl` | `#mp-patch-list` | `#pp-tree` |
| `siteUpdateVisibility` | `mpUpdateVisibility` | `ppUpdatePatchVisibility` |
| `siteHighlightPatch` | `mpHighlightPatch` | `ppHighlightPatch` |
| `resolveMeshToName` | surface-dict reverse lookup | `obj._ppPatchName` |
| Lifecycle reset | `mpLoadMesh()` start | `ppInitPostProcess()` start |

---

## 8. CSS

Added to the existing `<style>` block:

```css
.patch-filter-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--panel-border, #333);
  background: var(--panel-bg-2, #1e1e1e);
  position: sticky;
  top: 0;
  z-index: 2;
}
.patch-filter-bar .btn-toggle {
  padding: 2px 6px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted, #888);
  cursor: pointer;
  font: 11px monospace;
}
.patch-filter-bar .btn-toggle.active {
  border-color: var(--accent, #569cd6);
  color: var(--accent, #569cd6);
  background: rgba(86, 156, 214, 0.08);
}
.patch-filter-bar .filter-input {
  flex: 1;
  min-width: 0;
  padding: 2px 6px;
  font: 12px monospace;
}
.patch-filter-bar .filter-input.invalid {
  border-color: #e06c75;
  background: rgba(224, 108, 117, 0.06);
}
.patch-filter-bar .filter-count {
  font: 10px monospace;
  color: var(--text-muted, #666);
}

.filter-hidden { display: none !important; }
.filter-hidden-group { display: none !important; }

[data-patch].row-selected {
  border-left: 3px solid #9b59d0;
  background: rgba(155, 89, 208, 0.08);
}
```

---

## 9. Testing Strategy

### 9.1 Pure-JS unit tests (Node `node:test`, no DOM)

`STLGeneratorForOpenFoam/scripts/test_patch_filter.mjs` — covers:
- empty input → matches all
- substring case-sensitive / case-insensitive
- whole-word both cases
- regex valid / invalid / combined with case flag
- filter-bar state `getState` / `setState` round-trip

`STLGeneratorForOpenFoam/scripts/test_patch_selection.mjs` — covers:
- plain / shift / ctrl / shift+ctrl row click
- shift with null anchor → single-select
- shift with name outside `orderedNames` → single-select
- checkbox click: empty vs non-empty selection × with/without modifier
- `setSelection` + `clear` fire `onChange` with correct diffs

### 9.2 Structural front-end tests (Python regex scan, same pattern as `test_refinement_redesign_frontend.py`)

Added to `STLGeneratorForOpenFoam/scripts/test_patch_list_frontend.py`:
- `createFilterBar`, `createSelectionModel` are defined in `STL_generate.html` (or imported from js/ bundle).
- `mpBuildPatchList` body calls `createFilterBar` and `createSelectionModel`.
- `_ppBuildTree` body calls both.
- `mpHighlightPatch` and `ppHighlightPatch` are defined.
- An `addEventListener('click'` wiring on `[data-patch]` rows exists in each build function.
- CSS defines `.patch-filter-bar`, `.row-selected`, `.filter-hidden`.

### 9.3 Manual + browser-use smoke test

Scripted smoke sequence for the headless Chromium harness:
1. Open mesh-preview panel → type `wall` → verify count decrements, non-wall rows hidden.
2. Toggle `.*` → type `^fan` → verify only fan_* rows survive.
3. Click row body → purple class + purple emissive visible in viewport.
4. Shift + click a later row → range selected; ctrl + click a third → added.
5. Shift + click any selected checkbox → all selection toggles visibility.
6. Click a patch face in the 3D viewport → corresponding row highlights + scrolls into view.
7. Click empty space → selection clears.
8. Reload mesh → selection cleared, filter text preserved.
9. Repeat full flow inside the post-processing panel.

---

## 10. Out of Scope

Explicitly **not** in this change:

- Group-header interactions (select all in group, collapse/expand on click).
- Filter/toggle persistence across reloads (no `localStorage`).
- Right-click context menu on rows.
- Keyboard navigation beyond shift/ctrl modifiers (no arrow-key focus, no Esc-to-clear).
- Fuzzy matching or pinyin initials.
- Virtualized list rendering (patch counts stay well under DOM's comfort zone).
- Undo/redo of selection changes (selection is transient UI state, not part of the undo checkpoint system).

**Not touched:**

- `_buildGroupedPatchTree()` internal grouping/collapse logic — only its output is wrapped.
- `mpUpdateVisibility` / `ppUpdatePatchVisibility` internals — glue calls them, never reimplements.
- `pushUndoCheckpoint()` / `logOperation()` contract — both new modules are transient UI controls and do not enter the undo stack or the operation log.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| MeshBasicMaterial in pp lacks `emissive` | Fallback path in `setPatchHighlight` that tints `color` and caches original (§5.2). |
| 3D click interferes with OrbitControls drag | 3-pixel movement gate between mousedown and mouseup before treating as click (§6.1). |
| 3D click interferes with an existing pp picking tool (slice, streamline placement) | Capture-phase handler, gate on "no active picking tool"; if a tool is active, defer (§6.4). |
| Rebuild of list loses selection context | Glue step 6 saves + restores selection against new `currentNames` set (§7). |
| Regex input typed mid-entry becomes invalid (e.g. user typing `[`) | Input marked `.invalid`, predicate returns `false` → zero matches, no throw. |
| Large patch counts (200+) | Current DOM handles; virtualized list is out-of-scope but not architecturally blocked — `applyFilterToDOM` is O(N) and can later be replaced by a virtual-list renderer. |
