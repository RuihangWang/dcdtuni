# Design — 2D/3D sidebar parity + rack server panel redesign

**Date:** 2026-06-17
**Scope:** `src/frontend/web/STL_generate.html`, `src/frontend/web/room_2d_editor.js`, `src/frontend/web/i18n/zh-CN.json`, `src/frontend/web/i18n/en.json`
**Status:** Approved (design), pending implementation plan.

Two coupled goals reported together:
1. **Regression fix** — in the 2D editor, selecting an object lost the move/rotate buttons (single) and the 编组 / batch-move / batch-rotate buttons (multi-select). The 2D right sidebar must again equal the 3D right sidebar for any model selection.
2. **Server panel redesign** — the rack server quick-add panel was ugly and hard to use (no visible named server list, over-wide Start-U/Span inputs, English-only labels). Redesign it and place it in the **shared** rack inspector so both 2D and 3D show the same thing.

Verification goes through the GPU-accelerated `browser-use` (`/browser-verify`), never `conda run` end-to-end.

---

## Root cause of the regression

`room_2d_editor.js:_onMouseDown` model-select branch calls `setSelection('model', m.uid, …)` (which already renders the full 3D inspector — move/rotate/group/batch — into the shared `#prop-content` via `setSelection → updateUI`), and then calls `_updateRightPanel()`, which **overwrites** `#prop-content` with the 2D room/vertex/edge panel (no move/rotate/group). The 2D panel also rendered its own ad-hoc server panel.

`_updateInspectorOnly()` (`STL_generate.html:14732`) is the canonical "render the right sidebar for the current selection" dispatch. It already handles project / model / multi-select / face / rackSlot / rackEquipment / serverFace / rackPartition / wallRegion / region and includes every move/rotate/group/batch control.

**Exposure (verified in the live page):** the main app `<script>` runs at global scope, so every top-level `function foo()` is automatically a `window.foo`. `window._updateInspectorOnly`, `window.getSelectedModel`, `window.getSelectedModelIds`, `window.getModelById`, `window.setSelection`, `window.updateUI`, `window.deleteRackEquipment`, `window.addRackServerQuick`, and `window.getRackServerViews` all already resolve to functions. So **no new exposure code is needed** — `room_2d_editor.js` can call `window._updateInspectorOnly()` and `window.getSelectedModel()` directly.

---

## Architecture — Approach A: single source of truth

The 2D sidebar **delegates** to the shared inspector for model selections; the 2D panel renders only for room/vertex/edge/empty selections. The redesigned server panel lives **once**, in `renderRackModelInspector`. 2D and 3D model sidebars become identical by construction, and the server panel appears in both.

Rejected alternatives:
- **Mirror** — copy move/rotate/group/server HTML into the 2D file. Duplicates inspector logic across two files; drifts. This is the bug we are fixing.
- **Minimal regression-only** — drop the clobbering call. Restores buttons but delivers neither the server redesign nor guaranteed multi-select parity.

---

## Components

### 1. 2D delegates (`room_2d_editor.js`, `_updateRightPanel`, ~:1992)
At the **top** of `_updateRightPanel()`:
```js
const _sel = window.uiState && window.uiState.selection;
if (_sel && _sel.type === 'model' && window.getSelectedModel && window.getSelectedModel()
    && window._updateInspectorOnly) {
    window._updateInspectorOnly();
    return;
}
// else: existing room/vertex/edge panel (below)
```
**Only `'model'` is delegated** (not the broader sub-element set). The 2D editor only ever
produces `'model'` (clicking a projection) or `'project'` (deselect). Sub-element types
(`face`/`region`/`rackSlot`/...) only arrive as **stale** state carried in from the 3D
viewport, and `getSelectedModel()` is type-agnostic (truthy for any `modelId`). Delegating
those would hijack the 2D vertex/edge/room panel, because the 2D deselect path is gated on
`getSelectedModelIds()` — empty for non-`'model'` types — so it never clears them. Letting
them fall through renders the correct 2D panel; clicking a projection re-selects as `'model'`.
(Caught in adversarial review of the first cut, which used the 9-type set.)
This fixes **every** `_updateRightPanel()` call site at once (mousedown/mouseup/discrete handlers — never per-frame, so the re-render cost is fine). Remove the now-dead 2D server code: `_getSelectedRack`, `_buildRackServerPanelHtml`, `_SERVER_KIND_LABEL`, `room2d._addServerFromPanel`, `room2d._deleteServerFromPanel`, and the `_buildRackServerPanelHtml(_selRack)` block inside `_updateRightPanel`. (`_esc` stays if still used elsewhere; otherwise remove it too.)

The 2D room-settings panel (height) now shows only when nothing / a vertex / an edge is selected — matching 3D, where selecting a model shows only the model inspector.

### 2. Redesigned server panel (`STL_generate.html`, `renderRackModelInspector`, ~:11892)
Insert a new `prop-group` (placed after the "U-slot layout" group, before `renderRackCfdInspector(model)`), produced by a small helper `_rackServerPanelHtml(model)` so the template stays readable:

```
┌ 服务器 / Servers · SR1  (42U) ───────────────┐
│ 起始U [10]  跨度 [4]  类型 [风冷 ▾]          │   ← inputs width:52px (Start-U, Span); kind select auto
│ [ + 添加 ]            ⚠ <inline error>        │
├───────────────────────────────────────────────┤
│ fan-server_1   U11–U14   风冷   ✕             │
│ nonfan_2       U1–U2     无风   ✕             │   ← list sorted by start-U; name · U-range · kind · delete
│ liquid_3       U20–U23   液冷   ✕             │
└───────────────────────────────────────────────┘   (empty-state line when no servers)
```

- **Labels via i18n** — new `rack.servers.*` keys (title, start_u, span, kind, kind_fan, kind_nonfan, kind_liquid, add, empty, delete_title). Single-language, following the app's language toggle (consistent with every other inspector panel).
- **Adaptive inputs** — Start-U input `width:52px` (`min=1 max=slotCount`), Span input `width:52px` (`min=1`), kind `<select>` natural width. No full-width numeric inputs.
- **Server list** — `getRackServerViews(model)` sorted ascending by `startUnit`; each row shows `meta.name` (ellipsis-truncated, `title=` full name), the U-range `U(start+1)–U(start+span)`, a kind badge, and a delete `✕`. Empty state shows the `rack.servers.empty` line.
- **Add** — `onclick="window.rackServerPanelAdd('<uid>')"` reads the three inputs, calls `window.addRackServerQuick(uid,'front',startU-1,span,kind)`; on `{ok:false}` writes `res.error` into the inline error `<span>` (no `alert`). The canonical add pushes undo + `rebuildGeometrySingle` + `updateUI` internally.
- **Delete** — `onclick="window.deleteRackEquipment('<uid>','<serverUid>')"` (already contract-compliant: undo + rebuild + updateUI).
- `window.rackServerPanelAdd(modelId)` is a thin reader/dispatcher added in `STL_generate.html`; it does **not** push its own undo (the canonical add already does) — it only reads inputs, calls `addRackServerQuick`, and surfaces the inline error.

Both Add and Delete end in `updateUI()`, which re-renders the same `#prop-content` through the shared dispatch in **either** mode — so the panel's list, the 3D model, and the left model tree stay in sync (the "联动"). No 2D-specific re-render hook is needed: a rack's top projection does not change when servers are added/removed.

### 3. 2D whole-object selection (already satisfied)
`room_2d_editor.js:_onMouseDown` already calls `setSelection('model', m.uid, …)` on a projection-area click — the whole model, not a top face. Keep as-is; verify in the browser.

---

## Coding-rule compliance (`STL_generate.html`)
- The new `window.rackServerPanelAdd` is a **reader/dispatcher**, not a direct state mutation — it delegates to `addRackServerQuick`, which already pushes `pushUndoCheckpoint()` first and `logOperation()` after. No double-push.
- State stays JSON-serializable; `customSTLBase64` untouched.
- Buttons use `onclick` in the inspector HTML — consistent with the entire existing 3D inspector (every move/rotate/naming/CFD button uses `onclick`). The browser-use `onclick`-in-`innerHTML` quirk only affects automated clicking; verification drives the `window.*` functions directly via `eval`, and real users get working `onclick`. The "new 2D interactive elements use addEventListener" guidance applied to ad-hoc elements injected by the 2D file; this panel now lives in the shared 3D inspector and follows its pattern.

## Verification (GPU browser, STDCT)
1. **2D single-select parity** — enter 2D editor, click a rack projection → sidebar shows the rack inspector with move/rotate controls (eval the rendered `#prop-content` + screenshot).
2. **2D multi-select parity** — shift-click two models → sidebar shows 编组 / batch-move / batch-rotate (eval + screenshot).
3. **Server panel in 3D** — select a rack in the 3D viewport → server panel renders with the named list; add a server (`rackServerPanelAdd`) → list grows, left tree shows the new server; delete via ✕ → both update.
4. **Server panel in 2D** — same rack in 2D → identical panel; add/delete → list + tree + 3D stay in sync.
5. **Inline error** — add a server overlapping an occupied/ safety-zone slot → inline error text, no `alert`, no state change.

## Implementation order
1. i18n keys (`rack.servers.*`) in both locale files.
2. Server panel in `renderRackModelInspector` + `window.rackServerPanelAdd`.
3. 2D delegation in `_updateRightPanel`; remove dead 2D server code.
4. Full `/browser-verify` pass, screenshots read back.

(`_updateInspectorOnly` is already on `window` — no exposure task.)
