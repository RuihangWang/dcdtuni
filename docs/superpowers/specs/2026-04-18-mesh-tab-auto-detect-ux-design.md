# Mesh Tab Auto-Detect + Postprocess Refresh UX Design

**Date:** 2026-04-18
**Scope:** `STLGeneratorForOpenFoam/web/STL_generate.html` only (frontend-only feature)
**Source:** AGENTS/DctwinBasic.md L20-L24 prompt block — items 1, 2, 3

## Goal

Three tightly-related UX improvements around the mesh/postprocess tab workflow:

1. **Postprocess refresh — two-way button state.** When the user clicks the refresh button, the post-processing tab button should become enabled if results exist AND become disabled if they no longer do. Currently only the "enable" half is wired; stale enabled state persists after data is deleted.

2. **Auto refinement-detect on first mesh-tab entry — gated by a done flag.** Currently `switchLeftTab('mesh')` runs `runAutoDetectRackRefinement()` every time the user enters the mesh tab. Change to run only once per "edit session" — track with a `done` flag that stays `true` until the user edits a refinement level.

3. **Done flag flips to false on any refinement-level edit.** Any user edit to a refinement input (all of which funnel through `setMeshRefineLevel()`) resets `done=false`. Next mesh-tab entry or manual "自动加密检测" click re-runs the detect and flips `done=true` again.

## Architecture

```
window._autoDetectDone : boolean       ← in-memory UX flag; NOT persisted in cfdproj
state.dctwinConfig.solveComplete       ← already exists; tighten scanCaseStatus to set
                                         this deterministically from data.postprocessReady

lifecycle:
  page-load               _autoDetectDone = false
  switchLeftTab('mesh')   if (!_autoDetectDone) { runAutoDetectRackRefinement(); _autoDetectDone = true }
  manual button click     runAutoDetectRefinement(); _autoDetectDone = true  (at end)
                          runAutoDetectRackRefinement(); _autoDetectDone = true (at end, when invoked standalone)
  setMeshRefineLevel      _autoDetectDone = false  (at end, after existing body)
  _applyProjectData       _autoDetectDone = false  (reset on project load)

scanCaseStatus:
  Today: if (data.postprocessReady) state.dctwinConfig.solveComplete = true
  New:   state.dctwinConfig.solveComplete = !!data.postprocessReady
         // and ensure _updatePostprocessTabEnabled() is always called — which it is (L19492)
```

## Feature 1 · Postprocess button two-way sync

### Current behavior

[`window.scanCaseStatus()`](../../../STLGeneratorForOpenFoam/web/STL_generate.html#L19460) (called by the refresh button via
[`window.refreshPostprocessResults()`](../../../STLGeneratorForOpenFoam/web/STL_generate.html#L19499)) sets `state.dctwinConfig.solveComplete = true` only when `data.postprocessReady` is truthy (L19476-L19483). The `else` branch is a no-op, so the flag clings to a stale `true` after data disappears. The follow-up call to `_updatePostprocessTabEnabled()` (L19492) then reads the stale flag and keeps the button enabled.

### Fix

Make the assignment deterministic:

```js
// Replace L19476-L19483 block with:
state.dctwinConfig.solveComplete = !!data.postprocessReady;
const ppBtn = document.getElementById('tab-btn-postprocess');
const initBtn = document.getElementById('btn-init-postprocess');
if (data.postprocessReady) {
    if (ppBtn) { ppBtn.disabled = false; ppBtn.title = ''; }
    if (initBtn) initBtn.disabled = false;
    logOperation('scanCaseStatus', t('log.postprocess_data_ready'), {}, 'file');
}
// No "else" needed — _updatePostprocessTabEnabled() at L19492 reads solveComplete
// and does the symmetric disable; that call already exists and is the single
// source of truth for the button state.
```

### Scope lock

- **Do not touch** `meshReady` or `solveReady` branches. Their current "set-once" semantics match the existing UX intent (mesh/solve are expensive-to-regenerate artifacts; losing them by accident shouldn't silently flip the UI). User's Feature-1 prompt is specifically about the postprocess button.

## Feature 2 · Auto-detect gated by done flag

### Current behavior

[`window.switchLeftTab(tab)`](../../../STLGeneratorForOpenFoam/web/STL_generate.html#L19568) at L19575-L19576:

```js
if (tab === 'mesh') {
    window.runAutoDetectRackRefinement();
}
```

Runs every time, including repeated tab-switches with no changes. Wasteful recompute, and user-visible log spam.

### Fix

Add the gate:

```js
if (tab === 'mesh') {
    if (!window._autoDetectDone) {
        window.runAutoDetectRackRefinement();
        // _autoDetectDone is set inside runAutoDetectRackRefinement's trailing line
    }
}
```

## Feature 3 · Done flag lifecycle

### Where to set `_autoDetectDone = true`

Append as the LAST statement of:
- [`window.runAutoDetectRefinement()`](../../../STLGeneratorForOpenFoam/web/STL_generate.html#L25901) — full auto-detect (manual button)
- [`window.runAutoDetectRackRefinement()`](../../../STLGeneratorForOpenFoam/web/STL_generate.html#L25849) — rack-specific auto-detect (tab-switch)

### Where to set `_autoDetectDone = false`

Append as the LAST statement of:
- [`window.setMeshRefineLevel()`](../../../STLGeneratorForOpenFoam/web/STL_generate.html#L26066) — the single funnel for all surface/feature level mutations (confirmed by context exploration: all input `oninput` handlers — `ms-rl-0`, `ms-rack-0`, `ms-acu-0`, `ms-box-0`, `ms-pw-0`, `ms-bfl-0`, `ms-feat-*`, and the dynamic per-unit category setters — flow through this function)
- Reset inside [`window._applyProjectData()`](../../../STLGeneratorForOpenFoam/web/STL_generate.html#L15134) — so loading a new cfdproj re-enables a fresh first-visit auto-detect.

Initial value at script load: `window._autoDetectDone = false;` declared near other monitor/UX globals (e.g. next to `window._lastSensorJsonPath = null;`).

## Testing

New structural regex-scan tests in `STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py` (follows the pattern of `test_sensor_points_frontend.py`):

1. **`test_scancasestatus_sets_solvecomplete_deterministically`**
   - `extract_fn_body("scanCaseStatus")` must contain `solveComplete = !!data.postprocessReady` (or functionally equivalent explicit deterministic assignment).

2. **`test_switchlefttab_mesh_gated_by_autoDetectDone`**
   - `extract_fn_body("switchLeftTab")` must contain `_autoDetectDone` inside the `'mesh'` branch.

3. **`test_setmeshrefinelevel_clears_autoDetectDone`**
   - `extract_fn_body("setMeshRefineLevel")` must contain `_autoDetectDone = false`.

4. **`test_auto_detect_fns_set_done_true`**
   - Both `extract_fn_body("runAutoDetectRefinement")` and `extract_fn_body("runAutoDetectRackRefinement")` must contain `_autoDetectDone = true`.

5. **`test_applyprojectdata_resets_autoDetectDone`**
   - `extract_fn_body("_applyProjectData")` must contain `_autoDetectDone = false`.

6. **`test_autoDetectDone_global_declaration`**
   - HTML must declare `window._autoDetectDone = false;` (or `window._autoDetectDone = ...` init) at script top-level.

## Out of Scope / YAGNI

- Do NOT persist `_autoDetectDone` to cfdproj — pure in-memory UX state.
- Do NOT change mesh-tab or solve-tab button enable/disable logic (only postprocess).
- Do NOT add toasts / popups for auto-detect completion (UX beyond these 3 items).
- Do NOT refactor `scanCaseStatus` structure beyond the specific deterministic-assignment line.

## Related Files

| File | Role |
|---|---|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | All 3 features land here (single monolith) |
| `STLGeneratorForOpenFoam/scripts/test_mesh_tab_auto_detect_frontend.py` | New structural regex-scan tests |
