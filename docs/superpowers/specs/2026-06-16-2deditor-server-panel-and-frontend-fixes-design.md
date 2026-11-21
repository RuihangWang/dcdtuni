# Design — 2D-editor server quick-add panel + rotation perf + mesh-tab selection fix

**Date:** 2026-06-16
**Scope:** `src/frontend/web/STL_generate.html`, `src/frontend/web/room_2d_editor.js`
**Status:** Approved (design), pending implementation plan.

Three independent frontend workstreams, bundled because they were reported together. Verification for all three goes through the GPU-accelerated `browser-use` (`/browser-verify`), never `conda run` end-to-end.

---

## A. Rotation/mirror responsiveness — granular ("scoped") undo + drag-path fix

### Problem
Every mirror/rotate/reset click runs `pushUndoCheckpoint → captureStateSnapshot`, a full
`JSON.parse(JSON.stringify(state))` deep-clone of the entire project (every rack/server/U-slot/patch),
synchronously on the main thread. On large cases (e.g. `case_15_xlarge`, 238 racks) this blocks long
enough to drop the next click ("sometimes no response"). Separately, drag-rotate rebuilds the whole
model tree (`updateUI()` innerHTML) on every `pointermove`.

Key locations: `STL_generate.html:4516` (`captureStateSnapshot`), `:4623` (`pushUndoCheckpoint`),
`:18475` (`globalTransform`), `:18499` (`_rotApply`), `:18428` (`updateModelRotation`),
`:18344-18412` (`updateRotationGizmoDrag`, per-pointermove `updateUI()` at `:18398`),
`:10369` (`rebuildSingleModelForDrag`, the existing rAF-throttled fast path).

### Design — scoped checkpoints (opt-in, zero blast radius elsewhere)
1. Extend `pushUndoCheckpoint(action, opts)`:
   - With `opts.modelIds`: capture **only those models' objects** (deep-clone each model object from its
     `state.constructions.*` bucket) into a scoped stack entry
     `{type:'scoped', action, modelIds, before:{<id>:clone}, timestamp}`.
   - Without opts: unchanged full snapshot, tagged `{type:'full', ...}`.
2. Undo/redo executor branches on entry `type`:
   - `full` → current behaviour (replace `state`, full rebuild). **No change.**
   - `scoped` → capture current state of `modelIds` into the redo counterpart, write the entry's
     `before` clones back into their `state` buckets, then `rebuildSingleModel(id)` per affected model
     + `updateHighlights()`, and a single `updateUI()` if a model was added/removed. Symmetric for redo.
   - The scoped entry stores the **whole model object** (not just transform fields), so mirror
     (negative scale) and any geometry-affecting transform restore exactly.
3. Transform handlers opt in only when models are selected:
   `globalTransform`, `_rotApply`, `updateModelRotation`, gizmo-drag commit pass
   `{modelIds: getSelectedModelIds()}`. The nothing-selected "transform the whole wrapperGroup" path
   keeps the full snapshot (rare, inherently global).
4. **Blast radius:** only the transform handlers + the undo executor's new `scoped` branch. Every other
   `pushUndoCheckpoint(action)` call keeps the untouched full path → zero behaviour change elsewhere.

### Drag-path fix
`updateRotationGizmoDrag` stops calling `updateUI()` on every `pointermove`. It uses the existing
rAF-throttled `rebuildSingleModelForDrag` for the live visual, with one `updateUI()` on drag end
(already present at `:18424`).

### Verification
Click-latency before/after on `case_15_xlarge` in the GPU browser; undo/redo correctness on a rack that
is rotated then mirrored (state must round-trip exactly); confirm non-transform operations' undo is
untouched.

---

## B. Mesh-tab selection death — reproduce first, then fix the confirmed cause

### Problem (as reported)
After clicking the Mesh-tab **显示网格** preview, switching back to the **几何与属性** (Geometry) tab,
the user can no longer click-select any geometry/patch in the viewport.

### Important caveat
A live-browser repro of this exact path on a real 1729-patch mesh did **not** break selection:
`handleViewportSelection` (`:19987`) raycasts only `renderMeshes` (`:19999`); mesh-preview surfaces go
into a separate `_meshPreviewState.group` never added to `renderMeshes`. So no blind fix.

### Plan
**Step 1 — reproduce** the user's flow in the GPU browser (load a real project → Mesh tab → 显示网格 →
back to Geometry → click) and capture the failing state. Only then fix the confirmed cause.

Hypotheses to check, in order:
1. **Most likely:** `switchLeftTab` (`:22312`) does not call `mpClose`, so returning to Geometry leaves
   geometry **dimmed** (`mpSetGeoOpacity`, `:27569`) and the mesh group **still in the scene**. Selection
   may technically fire but the highlight is imperceptible / the mesh occludes — looks dead.
   Fix: on Mesh→Geometry switch, restore geometry opacity and hide the mesh-preview group.
2. The `_doRender` frustum-cull invariant (`:34007-34015`) toggles model `.visible`
   ("raycasting needs all models visible") — left unrestored if the render throws.
   Fix: wrap cull/render/restore in `try/finally`.
3. A stuck flag (`_uiFrozen` / a mesh-mode flag) gating `handleViewportSelection`.
   Fix: clear on tab-switch.

The `try/finally` hardening (cheap, defensive) ships regardless. The **primary** fix ships only once the
repro confirms the cause.

### Verification
Reproduce → fix → re-run the same flow in the GPU browser and confirm a face/patch selects, with a
screenshot read back.

---

## C. 2D-editor rack server quick-add panel

### Goal
In the 2D editor, when exactly **one RACK** is selected, the right sidebar (`#prop-content`) shows a
Servers panel for quickly adding/removing servers by U-slot, kept in sync with the left model tree and
the 3D model.

### Data model (existing — no new structures)
Servers live at `model.constructions.servers` (dict keyed by uid `e_N`):
`{uid, type:'fanServer'|'nonFan'|'liquidServer', geometry:{slotPosition /*0-indexed from bottom*/,
slotOccupation /*span in U*/}, meta:{name,enabled}, faces:{...}}`. v3-shaped views via
`getRackServerViews(model)` (`startUnit↔slotPosition`, `spanUnits↔slotOccupation`, `name↔meta.name`,
`kind↔type`). Display U-label = `U(slotPosition+1)`.

### UI (rendered by extending `room2d._updateRightPanel()`, `room_2d_editor.js:1927`)
```
┌ Servers · Rack-03 ────────────────┐
│ Add server                         │
│  start U [10]  span [4]  [fan ▾]   │
│  [  + Add  ]      ⚠ inline error   │
├────────────────────────────────────┤
│ fan-server_1   U11–U14   fan    ✕  │
│ nonfan_2       U1–U2     non    ✕  │
│ liquid_3       U20–U23   liq    ✕  │
└────────────────────────────────────┘
```
- Inputs: **start-U** (1-indexed, U1 = bottom → `slotPosition = startU-1`), **span** (U →
  `slotOccupation`), **kind** dropdown **fan / non-fan / liquid** (default fan).
- The 2D editor must call `_updateRightPanel()` after model-select in `_onMouseDown`
  (`room_2d_editor.js:1483`, currently only `_scheduleRender()`), and `_updateRightPanel()` renders the
  Servers panel when `uiState.selection.type==='model'` and exactly one selected model is `kind==='rack'`.

### Mutations (new thin wrapper + existing delete)
- **Add:** new `window.addRackServerQuick(modelId, side, startUnit0, spanUnits, kind)` in
  `STL_generate.html`. Reuses the canonical insert path: validate (in-range + slots unoccupied) →
  `pushUndoCheckpoint` (FIRST) → `_rackInsertServer(model, {kind, name:<preset default>,
  faces:<preset>, startUnit:startUnit0, spanUnits})` → clear `deletedFaces` for the span →
  `syncRackModelState` → `setSelection('model', modelId)` → `logOperation` → `rebuildGeometrySingle`.
  Returns `{ok, error, uid}` so the panel shows an **inline error** instead of a blocking `alert()`.
  Always creates a distinct server (no auto-merge); auto-names from the kind preset.
- **Delete:** the `✕` calls the existing `window.deleteRackEquipment(modelId, uid)` (already
  contract-compliant).

### Sync (the "联动")
Both mutations already run `rebuildGeometrySingle → updateUI`, which rebuilds the **left model tree** and
the **3D** model. The 2D add/delete handlers then re-run `room2d._updateRightPanel()` (to re-assert the
Servers panel over the inspector that `updateUI` writes into the shared `#prop-content`) and
`room2d._scheduleRender()` (to redraw the 2D canvas). One live `state` → tree, 3D, and 2D stay consistent.

### Scope / decisions
- Panel only when exactly one rack is selected (acu/box/multi-select → no panel).
- All three kinds exposed (user choice). Liquid pulls its preset defaults via the canonical insert.
- Pre-validate on Add; show inline error; no blocking `alert()`.
- Quick-add auto-names; inline rename is out of scope (rename stays available via the 3D inspector/tree).

---

## Implementation order
1. **B — reproduce** the mesh-selection bug in the GPU browser (cheap; may stand alone).
2. **C — feature** (2D server quick-add panel).
3. **A — scoped-undo rework** (deepest, highest risk; do last with focused before/after profiling).
4. Full `/browser-verify` pass across all three, screenshots read back.

## Coding-rule compliance (STL_generate.html)
- Every new state-mutating `window.*` calls `pushUndoCheckpoint()` FIRST, `logOperation()` after success.
- Existing mutations already push undo internally → the 2D wrappers must **not** double-push.
- State stays JSON-serializable; `customSTLBase64` untouched.
- New 2D interactive elements use `addEventListener`, never `onclick` in `innerHTML`.
