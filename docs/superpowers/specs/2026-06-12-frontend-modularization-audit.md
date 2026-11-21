# Frontend Modularization — Audit Baseline & Long-Term Roadmap

| Field | Value |
|-------|-------|
| **Date** | 2026-06-12 |
| **Status** | Audit baseline / roadmap — requirements document only; **no code splitting is implemented by this spec** |
| **Related** | `2026-06-12-cfdproj-v4-cfdrun-v2-design.md` (file-format refactor; its new `js/` code seeds the project-IO module, see §6 Wave A2) |
| **Scope** | `src/frontend/web/STL_generate.html`, `src/frontend/web/js/`, `src/frontend/web/room_2d_editor.js` |

This document records the verified current state of the dcdtuni frontend, the coupling
mechanisms that any refactor must respect, the hard constraints (treated as requirements),
a target architecture, and a phased extraction roadmap with acceptance gates. It is the
long-term reference for incremental modularization; individual extraction waves get their
own plan documents when executed.

---

## 1. Current-State Inventory

### 1.1 Line counts (verified via `wc -l`, 2026-06-12)

| File | Lines | Notes |
|------|------:|-------|
| `STL_generate.html` | 32,995 | 1.8 MB; single inline classic `<script>` from line 1160 to 32984, global scope |
| `room_2d_editor.js` | 2,250 | Outside `js/`; loaded at `STL_generate.html:1146` |
| `benchmark.js` | 141 | **Not referenced by any script tag** (see §7) |
| `js/liquid_mode.js` | 2,505 | `window._liquidMode` namespace |
| `js/liquid_solve_monitor.js` | 1,274 | `_lsm*`; mirrors air-mode `_sm*` architecture (header, lines 1–5) |
| `js/liquid_pipe_renderer.js` | 1,046 | `window._liquidPipeRenderer` |
| `js/liquid_graphgen.js` | 753 | `window.buildLiquidrunJson` |
| `js/liquid_topology_editor.js` | 748 | |
| `js/first_person_mode.js` | 546 | `window.fpsMode`; model module header (lines 1–4) |
| `js/liquid_cooling.js` | 327 | **Not IIFE-wrapped** (see §7) |
| `js/liquid_results_panel.js` | 325 | |
| `js/patch_filter.js` | 113 | `window.createFilterBar` factory |
| `js/patch_selection.js` | 102 | `window.createSelectionModel` factory |
| `js/cfdproj_migrate.js` | 92 | dual `window` + `module.exports` export for node tests |
| `js/model_library.js` | 30 | |
| `js/bvh_setup.js` | 19 | |
| **js/ total** | **7,880** | |

15 external script tags load at `STL_generate.html:1145-1159` (theme-loader,
room_2d_editor, the 7 liquid modules, patch_filter, patch_selection, bvh_setup,
cfdproj_migrate, model_library, first_person_mode) — all **before** the inline script.

### 1.2 Section map of STL_generate.html

Anchor lines below are verified; range boundaries are approximate (±10 lines) and follow
the file's own `==== Section ====` banner comments.

| Lines (approx.) | Area | Verified anchors |
|------|------|------------------|
| 1–33 | Head + vendor scripts | `window.require` shim hack :23–25 (set, load three-csg-ts UMD, delete) |
| 34–89 | Small inline `<style>` (main CSS in `theme/*.css`) | |
| 91–1144 | HTML body: auth overlay, left tab panels (geometry/mesh/solver/transient/postprocess/system), `#center-view`, right panel, log panel | 98 static `onclick=` attributes live here |
| 1145–1159 | External script tags | |
| 1160–32984 | **Single inline classic script, global scope** | |
| 1162 | i18n shorthand `const t` (lexical only — never reaches `window.t`) | :1162 |
| 1163–1360 | Auth overlay, I18n object (`window.I18n` :1308), constants | |
| 1363–1419 | `let state = getInitialState()` :1363; `Object.defineProperty(window,'state',{get,set})` bridge :1370–1377; undo/redo stacks ~:1381 | |
| 1421–2452 | 3D viewport bootstrap (`globalGroup` const :1828), PBR materials, resize handles, hit-picking | |
| 2453–4105 | Model factories + `normalizeState` :3770 | |
| 4106–4329 | **Undo/Redo Core** banner :4106; `captureStateSnapshot` :4108; `pushUndoCheckpoint` :4206; `logOperation` :4262 | |
| 4330–7320 | Terminal/init, STL parse cache, model registry, render lifecycle, collision, patch store, `setSelection` ~:6687 | |
| 7320–9917 | Per-kind model render functions; `renderScene` ~:9847 | |
| 9918–13990 | Tree HTML + right-panel inspector renderers (`renderModelInspector` ~:10950) | |
| 13993–16350 | UI update/toolbar (`updateUI` ~:14375), model creation `window.*` fns (`createDefaultBaseModel` ~:15547) | |
| 16352–17206 | Patch CRUD; **project IO**: `serializeProjectState` :16752, `window.saveProject` :16798, `window._applyProjectData` :16802, `loadProject` ~:16846 | |
| 17207–20057 | Transform tools, gizmo, patch drag, viewport selection/drag-create | |
| ~20058–21095 | **STL export engine**: `exportToStlString` :20106, `buildGroupedStlMap` :20782, `downloadStlText` ~:21087 | |
| 21096 | `==== CFD Project Export ====` banner (End marker ~:32367) | :21096 |
| 21109–21664 | **DCTwin server integration** banner :21109 (server info, case dir, folder browser, case status scan) | |
| 21665–22017 | Tab switching: `window.switchLeftTab` :21665; `_applyLeftTabUI` :21709 (tab-scoped 3D helper chokepoint) | |
| 22018–25493 | **Postprocess `_pp*`**: colormaps :22018, shaders, volume rendering, `window.ppInitPostProcess` :23684, filter/selection glue block :23481, streamlines, slices | |
| 25494–26640 | Sensor Points banner :25494; slice edit; blocking-task dialog | |
| 26641–27283 | **Mesh preview `_mp*`**: `window.mpLoadMesh` :26691; mesh-preview glue block :27185 | |
| 27284–27990 | **Air solver monitor `_sm*`** (`_smCreateMonitorWindow` ~:27432) | |
| 27992–28979 | checkMesh readiness, panel input sync, time-varying editor, transient tab | |
| 28985–30264 | **BlockMesh editor `_bm*`**, **refinement zones `_rz*`**, locationInMesh seeds | |
| 30265–31140 | **Mesh/solve job execution**: `window.runMeshGeneration` :30610, `window.runSolveOnly` :30896, abort, SSE log streaming (`_streamDctwinLog` call sites :30808, :30980) | |
| 31141–32367 | CFD property editors; `buildCfdrunJson` :31749; `window.exportCFDProject` :32308 | |
| 32400–32984 | Render loop, view presets, `window.__three = {camera, controls, renderer, scene, globalGroup}` :32667 (+ `__three.edit` ~:32669–32679), log resize, keyboard undo/redo, export dialog, upgrade panel | |

### 1.3 Coupling metrics (measured 2026-06-12)

| Metric | Value | Method |
|--------|------:|--------|
| Top-level `let`/`const` in inline script | ~180–200 | indentation heuristic: 176 at indent ≤4, 190 at ≤6 (prior audit reported ~201) |
| `window.X = function` assignment sites in main file | 379 (all unique names) | grep |
| Total `window.X =` assignment sites (incl. non-function) | 528 (435 unique properties) | grep |
| Implicit globals | hundreds — every top-level `function foo()` becomes a `window` property | classic-script semantics |
| `onclick=` in static body markup (lines 1–1159) | 98 | grep |
| `onclick=` inside JS-generated `innerHTML` strings | 186 | grep (284 total − 98 static) |
| `CustomEvent` dispatches anywhere in `web/` | **0** | grep |
| ES modules / bundler / package scripts | **none** | `src/frontend/package.json` has no test/build script; all scripts classic |

---

## 2. Coupling Analysis

Three communication patterns connect `js/` modules and the inline script. All are
direct-call, guard-protected, and untyped — there is no event bus and no dependency graph.

1. **`window.*` calls into main-file functions.** Modules call `window.state` (≈99 refs
   across `js/` + room_2d_editor), `window.requestRender` (≈25), `window._modelMap` (≈12),
   `window.logOperation` (≈10), `window.pushUndoCheckpoint` (≈6), `window.setSelection` /
   `getModelById` / `getActiveUnitConfig` (≈5 each). These resolve only because the inline
   script's global function declarations implicitly become `window` properties.
2. **Bare lexical globals.** `first_person_mode.js`, `liquid_mode.js` (:48), and
   `liquid_pipe_renderer.js` reference the `const globalGroup` binding
   (`STL_generate.html:1828`) directly, guarded by `typeof globalGroup === 'undefined'`.
   Bare bindings do not survive any module wrapper — they are the most fragile coupling.
3. **The `window.__three` handle bag** (`STL_generate.html:32667`): a curated
   `{camera, controls, renderer, scene, globalGroup}` plus `__three.edit` handoff used by
   `first_person_mode.js` ("Reads shared Three.js handles from window.__three", header
   lines 1–4). This is the cleanest of the three patterns.

**Load order:** the 15 `js/` script tags (:1145–1159) execute **before** the inline script
(:1160), so no main-file global exists at module load time. Every module defensively
resolves dependencies at call time (`window.t || identity` in
`liquid_solve_monitor.js:10`; `typeof globalGroup` guards). Any extracted module inherits
this constraint.

**The `window.state` bridge** (`STL_generate.html:1370–1377`): `state` is a reassignable
`let` (:1363), replaced wholesale on undo/redo (`:4154 state = normalizeState(snapshot)`)
and project load. An `Object.defineProperty(window,'state',{get,set})` keeps external
modules coherent across reassignment. Modules must never cache `state` in a long-lived
local — only read `window.state` (or the bare binding) per call.

**Two-way glue blocks:** the main file also calls module exports — labeled glue blocks
wire `patch_filter.js`/`patch_selection.js` factories into the postprocess tree
(`STL_generate.html:23481`) and the mesh-preview patch list (`:27185`), and call
`room2d`, `fpsMode`, `_liquidPipeRenderer`. Coupling is therefore bidirectional with no
init lifecycle.

---

## 3. Hard Constraints (requirements for any refactor)

These are non-negotiable; a refactor step that violates any of them must be reverted.

1. **CLAUDE.md mandatory coding rules remain in force per extracted module:**
   - Every state-mutating `window.*` function calls `pushUndoCheckpoint()` **first**, then
     `logOperation()` after success (`STL_generate.html:4206`, `:4262`).
   - One checkpoint per drag; first-call-only checkpoint for preview/slider (flag pattern).
   - Selection-only functions push no checkpoint.
   - `state` stays JSON-serializable (snapshot undo at `:4108` and `.cfdproj` save via
     `serializeProjectState` :16752 both serialize it). Extracted modules must not attach
     functions, DOM nodes, or Three.js objects to `state`.
   - `customSTLBase64` strings are immutable after creation.
   - Tab-scoped 3D helpers register visibility in `_applyLeftTabUI()` (`:21709`) and are
     cleaned up by `_applyProjectData` (`:16802`) on project load.
2. **The `window` facade is a public API.** `POST /api/browser/exec-js`
   (`src/server/server.py:2323`) and the skill automation layer call `window.*` functions
   by name — e.g. `.claude/commands/dc-builder.md` invokes `window.createDefaultRackModel`,
   `window.createDefaultFloorModel`, `window.createDefaultWallModel`,
   `window.createRackEquipmentAtSlot`, `window.updateRackServerCfd`, and `room2d.*`.
   Extraction may move implementations but must keep every published `window` name callable
   with identical signature. Renames require a permanent alias.
3. **`onclick` in `innerHTML` is broken under browser-use** (documented CLAUDE.md known
   issue: SyntaxError at 1:1). All *new* interactive elements use `addEventListener`; the
   186 existing dynamic `onclick` strings are a backlog (§7), not a pattern to extend.
4. **browser-use WebGL verification must keep passing** (`/browser-verify`): page load,
   `ANGLE (NVIDIA … Vulkan)` renderer check, project load, screenshot read-back. This is
   part of every phase's acceptance gate (§6.1).

---

## 4. Target Architecture

### Phase A strategy: classic scripts + window facade (no bundler)

The 284 `onclick` call sites, the exec-js/skill API (§3.2), and the proven module pattern
already in the repo make a window facade mandatory for any incremental path. Phase A
therefore keeps classic `<script>` tags and extracts code as **IIFE modules exporting one
window namespace each**, modeled on the two best existing examples:

- `js/first_person_mode.js:1-4` — header documents `Public API: window.fpsMode.{enter,
  exit, toggle, isActive}`, test hooks, and the rule "touches STL_generate.html minimally".
- `js/liquid_solve_monitor.js:1-12` — header states it "mirrors the air-mode _sm*
  architecture", wraps in `(function() { 'use strict'; ... })()`, resolves `window.t` at
  call time.

**Module template requirements (every extracted file):**
1. IIFE with `'use strict'`; zero top-level `var`/bare globals.
2. A header comment block declaring: Public API (exact `window.*` names), inputs read from
   `window` (e.g. `window.state`, `window.__three`), and undo/log contract notes.
3. All main-file dependencies resolved at call time, never at load time.
4. Shared Three.js handles accessed only via `window.__three` — never bare `globalGroup`.
5. New event wiring via `addEventListener`; no new `onclick` strings.
6. Where node-testable (pure logic), dual export: `window.X` + `module.exports` guard, as
   `cfdproj_migrate.js` does.

### Phase C: ES modules / bundler — a deliberate later decision point

Migrating to ES modules (with or without a bundler) is **not assumed**. It would break the
implicit function-declaration→window export and bare lexical sharing in one step, so it is
deferred until Phase A is complete, the window facade is the *only* coupling mechanism,
and the onclick backlog (§7) is substantially burned down. The decision gets its own spec;
this document only requires that Phase A modules avoid patterns that would block it
(no load-time side effects beyond namespace registration, documented APIs).

---

## 5. Phased Extraction Roadmap

### 5.1 Shared acceptance gate (every wave must pass before merge)

| Gate | Command / procedure |
|------|---------------------|
| Node unit tests | `node --test src/frontend/test/*.mjs` (cfdproj_migrate, model_library, patch_filter, patch_selection) |
| Structural tests | `conda run -n dcdtuni python -m pytest src/frontend/test/` (source-grepping frontend contract tests, e.g. `test_patch_list_frontend.py`) |
| E2E eval suite | `bash src/frontend/test/e2e_tests.sh` against a running server (browser-use eval) |
| WebGL load gate | `/browser-verify` flow: `browser-use open`, load `case/Base_leakage` **and** `case/Liquid` projects, screenshot each, read screenshots back multimodally; renderer must report ANGLE/NVIDIA, scene must render, Operation Log must show no errors |
| API surface check | grep proof that every `window.*` name referenced by `onclick=`, `e2e_tests.sh`, and `.claude/commands/*.md` still resolves |

Each wave also adds structural tests asserting its module's undo/log contract
(checkpoint-before-mutation) before extraction begins.

### 5.2 Extraction waves (Phase A), in dependency order

| Wave | Module | Source (approx.) | Rationale |
|------|--------|------------------|-----------|
| **A1** | **Undo/log core** → `js/undo_core.js` | :4106–4329 (`captureStateSnapshot` :4108, `pushUndoCheckpoint` :4206, `logOperation` :4262) + stacks ~:1381 | Most cross-cutting code in the file: called from every feature area and from `js/` modules (≈6 + ≈10 external call sites). Every later wave depends on these names being stable `window` exports, so it must come first. Smallest blast radius per line. |
| **A2** | **Project IO** → extend `js/cfdproj_migrate.js`, new `js/cfdrun_export.js`, then pull `serializeProjectState` :16752 / `_applyProjectData` :16802 / `loadProject` ~:16846 / `normalizeState` :3770 | :16601–17206 + :3770–4105 | **Partially seeded by the cfdproj-v4/cfdrun-v2 format refactor** (see Related spec): that work already lands migrators and export logic in `js/`, making this the natural second module. Depends on A1 (load/save log + checkpoint interplay). |
| **A3** | **STL export engine** → `js/stl_export.js` | ~:20058–21095 (`exportToStlString` :20106, `buildGroupedStlMap` :20782) | Mostly pure geometry→text transformation; reads scene/`globalGroup` via handles, few UI dependencies; consumed by A2's export-cfdrun path. |
| **A4** | **Server integration + job execution** → `js/server_jobs.js` | :21109–21664 (server info, case dir, folder browser, case status) + :30265–31140 (`runMeshGeneration` :30610, `runSolveOnly` :30896, abort, SSE streaming) | Self-contained network layer; both ranges share auth/fetch/SSE concerns and the UI-freeze hook. Depends on A1 (logging) only. |
| **A5** | **Postprocess** → `js/postprocess.js` | :22018–25493 (`_pp*`) | Largest single block (~3,500 lines), already pseudo-namespaced `_pp*`; the patch-list glue block :23481 is its documented seam to `patch_filter`/`patch_selection`. Depends on A4 (results fetching). |
| **A6** | **Air solver monitor** → `js/solve_monitor.js` | :27284–27990 (`_sm*`) | `liquid_solve_monitor.js` already proves this exact architecture externally (`_lsm*` mirrors `_sm*` per its header) — extraction is a port to an existing template. |
| **A7** | **Mesh preview + blockMesh + refinement zones** → `js/mesh_tools.js` (or three files) | `_mp*` :26641–26940 + glue :27185; `_bm*` :28985–29450; `_rz*` :29451–29854; locationInMesh :29855–30264 | Grouped because all are Mesh-tab-scoped 3D helpers whose visibility runs through `_applyLeftTabUI` (:21709); extraction must introduce a visibility-registration hook so the tab chokepoint keeps working (§3.1). |
| **A8** | **Inspectors / UI render layer** → last | tree + inspector renderers :9918–13990, `updateUI` ~:14375 | Deliberately last: densest concentration of the 186 `innerHTML onclick` strings, deepest fan-out into every model kind, highest regression risk. By this point all its callees live behind documented module APIs. |

Banner comments (`==== Section ====`, ~30 of them with several explicit `End` pairs) and
the pseudo-namespace prefixes `_pp*`, `_sm*`, `_lsm*`, `_mp*`, `_rd*`, `_rz*`, `_bm*`,
`_srv*`, `_lc*` are the extraction seams — waves cut along them, never mid-section.

**Phase B (interleaved, opportunistic):** burn down the §7 cleanup register; convert
`innerHTML onclick` strings to `addEventListener` in any code a wave touches.

**Phase C (decision point):** evaluate ES modules/bundler only after A8; see §4.

---

## 6. Known Issues / Cleanup Register

| # | Issue | Evidence | Disposition |
|---|-------|----------|-------------|
| 1 | **`window.t` i18n gap** — `const t` (`STL_generate.html:1162`) is lexical-only; `window.t` is never assigned (grep: 0 sites). Module-side `window.t || identity` fallbacks (`liquid_solve_monitor.js:10`) always take the identity branch, so liquid-module UI strings silently skip i18n. `window.I18n` (:1308) is the partial workaround. | verified | Assign `window.t = t` next to the `window.I18n` export; audit liquid panels for missing `data-i18n`/`t()` coverage. |
| 2 | **`benchmark.js` dead** — 141 lines, no script tag in `STL_generate.html`, no reference in `server.py`; only mentions are in `docs/superpowers/plans/2026-03-29-phase*-performance-optimization.md`. | verified | Confirm with git history that it was ad-hoc benchmarking; then delete or move under `src/frontend/test/`. Do not migrate. |
| 3 | **`liquid_cooling.js` not IIFE-wrapped** — file-level `var _lcAbortController` (:8) and `const _LC_FLUIDS` (:11) leak into global scope. | verified | Wrap in IIFE + explicit `window` exports as the first Phase B item; it is the template-violation precedent new modules must not copy. |
| 4 | **`room_2d_editor.js` outside `js/`** — loaded at `STL_generate.html:1146` from `web/` root. | verified | Move to `js/room_2d_editor.js` when next substantially touched; update the script tag and any test references in the same commit. |
| 5 | **`window.require` shim hack** (`STL_generate.html:23–25`) — fakes CommonJS for the three-csg-ts UMD vendor build, then deletes itself. | verified | Acceptable short-term; long-term replace with a vendor build that attaches to `window` directly. Document in vendor README until then. |
| 6 | **186 `innerHTML onclick` strings** — broken under browser-use (CLAUDE.md known issue), force permanent `window` exposure of their targets. | measured (284 total − 98 static) | Migration backlog: convert to `addEventListener` whenever a wave touches the surrounding renderer; A8 retires the bulk. No big-bang rewrite. |

---

## 7. Risks and Mitigations

| Risk | Mechanism | Mitigation |
|------|-----------|------------|
| **Load-order breakage** | Extracted modules join the loads-before-inline-script set (:1145–1159); any load-time call into a main-file global throws. | Module template forbids load-time side effects; all dependency resolution at call time with guards (existing pattern, e.g. `liquid_mode.js:48`). Init hooks run from the main file's DOMContentLoaded section. |
| **Implicit window-export loss** | Wrapping a function in an IIFE silently removes its implicit `window` property; 284 `onclick` sites, `e2e_tests.sh` evals, and exec-js callers fail only at click/eval time. | Per-wave API surface check (§5.1): enumerate every moved symbol, grep `onclick=`/`window.<name>` across `web/`, `test/`, `.claude/commands/`; add explicit `window.X = X` for each published name. |
| **exec-js / skill API breakage** | `/api/browser/exec-js` (`server.py:2323`) and dc-builder/dc-dataset-reverse call `window.*` by name from outside the codebase's grep horizon. | Treat the names in §3.2 plus everything `e2e_tests.sh` evals as a frozen public API; renames only via permanent aliases; WebGL gate exercises real project load on `case/Base_leakage` + `case/Liquid`. |
| **Undo/log contract regressions** | Moving a mutating function can reorder or drop `pushUndoCheckpoint`/`logOperation`. | A1 extracts the undo core first so the contract has a single owner; each wave adds a structural pytest (source-grep style, as in existing `test_*_frontend.py`) asserting checkpoint-before-mutation for its module. |
| **Bare lexical binding loss** | `globalGroup`, `scene`, `camera` are lexical; an extracted consumer can no longer see them. | Standardize on `window.__three` (:32667) as the only handle bag; extend it rather than exporting new bare globals; A-waves convert bare `globalGroup` refs in the three offending modules. |
| **Stale `state` references** | Modules caching `state` across the `let` reassignment (:1363, :4154) read a dead snapshot. | Rule: read `window.state` per call (the :1370 getter guarantees freshness); structural test greps extracted modules for `const state =` / `let state =` captures at module scope. |

---

## 8. Out of Scope

- **The file-format refactor** (cfdproj v4 / cfdrun v2) — owned by
  `2026-06-12-cfdproj-v4-cfdrun-v2-design.md`. This document only notes that its `js/`
  deliverables (`cfdproj_migrate.js` extension, new `cfdrun_export.js`) seed Wave A2.
- **Any visual or UX redesign** — theme CSS, layout, and panel structure are untouched.
- **`server.py` changes** — the REST/SSE API, auth, and exec-js channel stay as-is; the
  frontend refactor must conform to them, not the reverse.
- **ES-module/bundler migration** — explicitly deferred to the Phase C decision point (§4).
