# Phase 0 — Interaction Foundation

**Date:** 2026-04-15
**Status:** Design — awaiting approval
**Owner:** TBD
**Parent plan:** STLGenerator refactor 4-phase plan (Phase 0 of 4)

## Scope

Phase 0 of the 4-phase refactor. Restores interaction-layer correctness before the later phases (leakage unification → RACK architecture rewrite → partition wall → polish) touch deeper code. **Entirely bug-fix and verification work. No new algorithm design.**

## Problem Statement

The user reports that interaction-layer features he previously relied on have regressed or appear broken after the recent partition-wall branch merge (merge commit `1ae0fc3`). Specifically:

1. **Snap system** — face / edge / rotation snap "completely disappeared" in the current build, especially visible when dragging a rotated baffle in `Tri_angle.cfdproj`.
2. **Undo/redo (Ctrl+Z/Y)** — feels dead in move/rotate modes and in RACK-detail sliders.
3. **Camera controls** — user asked for LEFT=off, MIDDLE=pan, WHEEL=zoom, RIGHT=rotate. Code already matches, so this is a verification task.
4. **Axis widget** — top-right viewCube should be moved to bottom-left above the FPS counter and restyled per the user's reference image.
5. **tri_angle project** — loading `Tri_angle.cfdproj` causes raw i18n keys (e.g. `label.seed_direction_y`) to leak into the UI instead of translated strings.

Known-good baselines exist for 1–4 in git history; the work is locating regressions, not designing new behavior.

## Non-Goals

- Snap algorithm **redesign** — the redesign shipped on 2026-04-14 (see *References*). Phase 0 only restores it.
- Sidebar font/emoji consistency → **deferred to Phase 4**.
- 2D mode RACK projection — **dropped from Phase 0**, verified already-correct in current code at [room_2d_editor.js:554-582](../../STLGeneratorForOpenFoam/web/room_2d_editor.js#L554-L582) (rotation-aware bottom-face projection, old AABB-pulsing bug already fixed).
- Any Phase 1 / 2 / 3 work (leakage, RACK, partition wall).

---

## 1. Architecture

No new modules, no new files. All work lives in:

- [`STLGeneratorForOpenFoam/web/STL_generate.html`](../../STLGeneratorForOpenFoam/web/STL_generate.html)
- [`STLGeneratorForOpenFoam/web/js/snap.js`](../../STLGeneratorForOpenFoam/web/js/snap.js)
- [`STLGeneratorForOpenFoam/web/room_2d_editor.js`](../../STLGeneratorForOpenFoam/web/room_2d_editor.js) (none expected, listed for completeness)

One new tiny helper inside the HTML: `applyI18nToSubtree(rootEl)` to replace repeated full-document `I18n.applyToDOM()` sweeps after property-inspector rerenders.

## 2. Components

### 2.1 Undo-push audit

**Observed symptom:** user reports Ctrl+Z/Y "completely disappeared", especially in move/rotate modes and RACK-detail design.

**Verified:** the keyboard listener at [STL_generate.html:27300-27315](../../STLGeneratorForOpenFoam/web/STL_generate.html#L27300-L27315) is alive and correct. 162 `pushUndoCheckpoint` call sites exist. The likely root cause is **checkpoint push-side regressions**, not a dead listener.

**Primary suspect:** the `_rdCheckpointPushed` "first-call-per-drag" flag pattern at [STL_generate.html:11836-12043](../../STLGeneratorForOpenFoam/web/STL_generate.html#L11836-L12043) uses mixed scoping — some sites reference the lexical `let _rdCheckpointPushed`, others reference `window._rdCheckpointPushed`. Because inline `onchange`/`oninput` handlers execute in global scope, they cannot see the lexical `let`; they only see `window.*`. If a drag-end reset sets only the lexical flag, the `window.*` flag stays `true` forever and **no further RACK-detail change ever pushes an undo checkpoint**.

**Work:**

1. Grep all `_rdCheckpointPushed` reads and writes. Unify to `window._rdCheckpointPushed` everywhere.
2. Introduce one helper `_resetRdCheckpoint()` that sets the flag to `false`. Call it from every drag-end / blur / mouseup that corresponds to a RACK-detail slider.
3. Audit multi-move and multi-rotate drag-end at [STL_generate.html:10885-10986](../../STLGeneratorForOpenFoam/web/STL_generate.html#L10885-L10986) for the same pattern.
4. For any state-mutating `window.*` function discovered to lack a checkpoint entirely (the project rule in CLAUDE.md forbids this), add `pushUndoCheckpoint(actionName)` as the first line.

**Acceptance:**

- 10 sequential slider drags on RACK inner width, each undoable one-by-one (Ctrl+Z pops exactly one drag at a time).
- A full multi-move of 5 models → Ctrl+Z → all 5 return to start positions in one step.
- RACK-detail door porosity slider: 3 sequential drags, 3 undos return to pre-first-drag state.

### 2.2 Snap regression restoration

**Observed symptom:** user reports all three snap modes (face / edge / rotation) "gone" in `Tri_angle.cfdproj`.

**Verified:** the snap system designed in [`2026-04-14-snap-algorithm-design.md`](2026-04-14-snap-algorithm-design.md) shipped in commits `85cd0bc` → `c279f6f`. `snapConfig` defaults to `{face:true, edge:true, vertex:false, rotation:false}` at [STL_generate.html:2736](../../STLGeneratorForOpenFoam/web/STL_generate.html#L2736). Snap module integration in drag path at [STL_generate.html:15732-15821](../../STLGeneratorForOpenFoam/web/STL_generate.html#L15732-L15821) appears intact.

**Hypothesis:** regression introduced by the partition-wall branch (15 commits `0bc07cd` → `108e080`, merged as `1ae0fc3`).

**Work:**

1. Reproduce: open `projects/Tri_angle.cfdproj` → create baffle → drag near a rotated rack → observe no highlight overlay, no snap delta, no cursor-badge.
2. `git bisect` between `c279f6f` (last known-good snap commit) and `1ae0fc3` (merge). Candidate offenders are the 15 partition-wall commits.
3. Identify the regression point and the exact integration it breaks (candidates: drag-path conditional at [STL_generate.html:15993](../../STLGeneratorForOpenFoam/web/STL_generate.html#L15993), `beginDrag` arguments at [STL_generate.html:15732-15735](../../STLGeneratorForOpenFoam/web/STL_generate.html#L15732-L15735), or `__snapModule.invalidate()` miss in a new state-mutation path).
4. Fix the integration without reverting any partition-wall functionality. Preserve the partition-wall feature; restore snap around it.

**Acceptance:** the manual smoke-test checklist in [`2026-04-14-snap-algorithm-design.md` §5.3](2026-04-14-snap-algorithm-design.md) passes end-to-end on `Tri_angle.cfdproj`.

**Conditional redesign gate:** if after the bisect-fix the snap is still janky **specifically for rotated objects** (not on axis-aligned ones), stop and escalate. A mini-design sub-spec will be produced separately; it is **out of scope here**. Do not attempt to redesign inside this implementation.

### 2.3 Camera control verification

**Observed code:** [STL_generate.html:1067-1071](../../STLGeneratorForOpenFoam/web/STL_generate.html#L1067-L1071) already binds `LEFT=null, MIDDLE=PAN, RIGHT=ROTATE`, wheel defaults to zoom. Matches user's stated requirement.

**Work:**

1. Verify in a running server instance (`./STLGeneratorForOpenFoam/server/start_server.sh` → browser, or `browser-use open`).
2. If correct: zero work. Record the verification in the implementation plan outcome and move on.
3. If broken: hunt for a later override of `controls.mouseButtons`, `controls.enableRotate`, `controls.enablePan`, or a custom mousedown handler intercepting LEFT. Likely candidates: room2d enter/exit handlers, rotation-gizmo tool, snap-module drag lifecycle.

**Acceptance:** left-click-drag on empty canvas does nothing to the camera; middle-hold drags the camera parallel to screen; wheel zooms; right-hold orbits the camera around the target.

### 2.4 Axis widget reposition + restyle

**Observed code:** `viewCubeEl` container + `axesGroup` with `AxesHelper(1.5)` at [STL_generate.html:1030-1170](../../STLGeneratorForOpenFoam/web/STL_generate.html#L1030-L1170). Rendered as a small overlay in the top-right.

**Work:**

1. Relocate the `#view-cube` DOM container from top-right to bottom-left of the 3D viewport, positioned just above the FPS counter with a small gap (~8 px).
2. Scale the widget to roughly 50% of its current pixel size.
3. Restyle per user's reference image (requested at implementation time): clearer XYZ colors (red=X, green=Y, blue=Z), bigger axis labels, transparent background, subtle drop shadow.
4. Keep the existing click-to-snap-to-view interaction working (the viewCube click handlers at [STL_generate.html:1160+](../../STLGeneratorForOpenFoam/web/STL_generate.html#L1160) stay wired).

**Acceptance:** widget renders in bottom-left, smaller, matches reference image, still clickable for snap-to-view.

### 2.5 tri_angle i18n "raw keys" fix

**Observed code:** i18n loads once via `I18n.onReady(() => { I18n.applyToDOM(); })` at [STL_generate.html:27320](../../STLGeneratorForOpenFoam/web/STL_generate.html#L27320). Every property-inspector rerender that emits fresh `data-i18n="..."` DOM without re-calling `I18n.applyToDOM(newSubtree)` will leak raw keys.

**Hypothesis:** project-load path for `Tri_angle.cfdproj` triggers DOM updates (model list, property inspector, toolbar badges) that bypass the initial `applyToDOM` sweep, leaving raw keys like `label.seed_direction_y` visible as text.

**Work:**

1. Add a local helper `applyI18nToSubtree(rootEl)` (thin wrapper; if `I18n.applyToDOM` already accepts a root it just forwards, otherwise iterates `rootEl.querySelectorAll('[data-i18n]')`).
2. Audit every function that writes `innerHTML` containing `data-i18n="..."` (property inspector, model tree, RACK detail view, partition-wall inspector, solver panel, mesh panel, etc.). After each `innerHTML` write, call `applyI18nToSubtree(containerEl)`.
3. For dynamic templates built from `innerHTML += ...` (additive), ensure the helper runs after the final concatenation, not after each `+=`.

**Acceptance:** open `Tri_angle.cfdproj`, click every model type, every toolbar tab, every gizmo mode. No DOM text in either `zh-CN` or `en-US` locale starts with `label.`, `btn.`, `tree.`, `title.`, `nav.`, `error.`, `loading.`, or `export.` followed by a lowercase word.

---

## 3. Data / State Changes

None. No schema migration. No new state fields.

## 4. Error Handling

- **Bisect lands on a partition-wall fix commit:** preserve the partition-wall change; integrate snap around it rather than reverting. The partition-wall feature is required by Phase 3 and cannot be rolled back.
- **i18n dict missing a key:** existing behavior presumed to be empty-string fallback + `console.warn`. Verify during implementation; if it throws, add the fallback.
- **viewCube click handler breaks after reposition:** CSS-only reposition must not reparent the DOM or rebind handlers. If handlers are position-dependent (e.g. mouse-coordinate math assuming top-right), update the math, not the handler binding.

## 5. Testing

### 5.1 Automated

- Extend [`web/js/snap.test.js`](../../STLGeneratorForOpenFoam/web/js/snap.test.js) with one new scenario: "snap still produces face candidate when rotated baffle adjacent to rotated rack, quaternion non-identity".
- Add a small Node-runnable assertion under the same harness that exercises 10 simulated slider-drag mutations on a mock state and asserts `undoStack.length === 10` after.

### 5.2 Manual (per PR)

1. Open `Tri_angle.cfdproj`.
2. Snap smoke-test: follow [`2026-04-14-snap-algorithm-design.md` §5.3](2026-04-14-snap-algorithm-design.md) checklist verbatim.
3. Undo smoke-test: 10 RACK inner-width slider drags + 10 undos; 3 door-porosity drags + 3 undos; 1 multi-move of 5 models + 1 undo.
4. Camera: verify mouse-button bindings match spec.
5. Axis widget: screenshot against user's reference image.
6. i18n: switch to `en-US`, reopen project, confirm no raw keys. Repeat for `zh-CN`.

### 5.3 `browser-use` headless pass

- `browser-use open http://localhost:8000/STL_generate.html` → load `Tri_angle.cfdproj` via UI → screenshot → grep rendered DOM text for raw i18n key prefixes. Fail if any found.

---

## 6. Out of Scope (reiterated for clarity)

- Sidebar font/emoji consistency → Phase 4.
- Snap algorithm redesign → already shipped 2026-04-14; not revisited.
- 2D RACK projection → already correct in code, dropped.
- RACK architecture, server geometry, leakage unification, partition wall → Phases 1/2/3.

---

## 7. References

- [`2026-04-14-snap-algorithm-design.md`](2026-04-14-snap-algorithm-design.md) — snap system design (shipped).
- [`2026-04-14-rack-frame-redesign-design.md`](2026-04-14-rack-frame-redesign-design.md) — Phase 2 precursor.
- [`2026-04-14-server-geometry-redesign-design.md`](2026-04-14-server-geometry-redesign-design.md) — Phase 2 precursor.
- [`2026-04-14-partition-wall-design.md`](2026-04-14-partition-wall-design.md) — Phase 3 precursor.
- CLAUDE.md mandatory rule: every state-mutating `window.*` function calls `pushUndoCheckpoint()` first.

## 8. Open Questions

None at spec-approval time. Thresholds and specific regression commits will be pinned during implementation via `git bisect`.

## 9. Implementation Plan Sketch

Full plan to be produced by the `writing-plans` skill after this spec is approved.

1. Reproduce all 5 symptoms on current `main` against `Tri_angle.cfdproj`. Record baseline.
2. `git bisect` snap regression between `c279f6f` and `1ae0fc3`. Fix at the regression point.
3. Undo-flag scoping unification and `_resetRdCheckpoint` wiring.
4. Camera verification. Fix only if broken.
5. Axis widget reposition + restyle (CSS-only where possible).
6. i18n `applyI18nToSubtree` helper + audit of every `innerHTML` with `data-i18n`.
7. Extend snap unit tests; add undo-push smoke tests.
8. Full manual smoke-test pass on `Tri_angle.cfdproj`.
9. `browser-use` headless verification for i18n leakage.
10. PR with before/after screenshots.
