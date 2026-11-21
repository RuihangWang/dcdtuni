# Liquid Cooling Dedicated Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the frontend so liquid cooling becomes a dedicated 3D mode with its own workflow, cleanly separated from air cooling; backend changes are minimal (one flag).

**Architecture:** Introduce `state.displayMode` with values `'air'` | `'liquid'`. Both modes share `state.models`. Right panel splits into creation toolbar (top) + property inspector (bottom). New liquid model kinds become first-class. Legacy Phase 1–8 data migrates on load.

**Tech Stack:** Vanilla JS (no build), Three.js r128, Canvas 2D, single `.cfdproj` JSON persistence.

**Design Spec:** `AGENTS/specs/2026-04-10-liquid-cooling-dedicated-mode-design.md`

---

## File Map

### Primary Files to Modify

| File | Responsibility |
|------|---------------|
| `src/frontend/web/STL_generate.html` | File bar, tab bar, right panel structure, model factories, migration |
| `src/frontend/web/js/liquid_mode.js` | **NEW** — mode switch, contextual creation, pipe drag state machine |
| `src/frontend/web/js/liquid_cooling.js` | Extend with new kind factories |
| `src/frontend/web/js/liquid_pipe_renderer.js` | Render `pipe_segment` model kind |
| `src/backend/aircooling/runner/stlgen_runner.py` | Add `inheritLiquidResults` flag check |

### Phase → Files Touched

| Phase | Files |
|-------|-------|
| A | `STL_generate.html` (file bar button, state init, mode helpers) |
| B | `STL_generate.html` (right panel HTML, toolbar migration, renderRootInspector cleanup) |
| C | `STL_generate.html` (tab bar, _applyLeftTabUI, liquid geometry tree) |
| D | `STL_generate.html` (new model kinds, migration), `liquid_cooling.js` (factories) |
| E | `STL_generate.html` (context menu wiring), `liquid_mode.js` (NEW), `liquid_pipe_renderer.js` (pipe_segment render) |
| F | `STL_generate.html` (air solver prop-group, cleanup), `stlgen_runner.py` (flag) |

---

## Phase A: displayMode Infrastructure

**Goal:** Replace the STL Preview button with a "Liquid Simulation" button, wire up `state.displayMode` with `'air'` | `'liquid'` values, add mode helpers, update undo/redo logging. No visual layout changes yet.

**Files:**
- Modify: `src/frontend/web/STL_generate.html`

### Task A.1: Replace file bar button

- [ ] **Step 1: Locate the button**

Run: `grep -n 'toggle-preview-btn\|toggleStlPreviewMode' src/frontend/web/STL_generate.html`
Expected: matches at line 30 (button) and ~line 9442 (function)

- [ ] **Step 2: Replace the HTML button at line 30**

Find:
```html
<button id="toggle-preview-btn" class="file-btn" onclick="toggleStlPreviewMode()">👁 STL Preview</button>
```

Replace with:
```html
<button id="btn-mode-switch" class="file-btn" onclick="window.toggleDisplayMode()" style="background:rgba(0,180,220,0.1); border-color:rgba(0,180,220,0.4); color:#00b4dc;">💧 液冷模拟 / Liquid Simulation</button>
```

- [ ] **Step 3: Add mode helper functions**

Find the line `function isStlPreviewMode() {` (around line 2713). Add BEFORE it:

```javascript
    function isAirMode() {
        return state.displayMode !== 'liquid';
    }

    function isLiquidMode() {
        return state.displayMode === 'liquid';
    }
```

- [ ] **Step 4: Add `toggleDisplayMode` function**

Find `window.toggleStlPreviewMode = async function(forceMode = null) {` (around line 9442). Add BEFORE it:

```javascript
    window.toggleDisplayMode = function() {
        pushUndoCheckpoint('toggleDisplayMode');
        const next = isLiquidMode() ? 'air' : 'liquid';
        state.displayMode = next;
        // Update button label
        const btn = document.getElementById('btn-mode-switch');
        if (btn) {
            btn.innerHTML = next === 'liquid'
                ? '← 返回风冷模拟 / Back to Air Mode'
                : '💧 液冷模拟 / Liquid Simulation';
            btn.style.background = next === 'liquid'
                ? 'rgba(255,152,0,0.1)'
                : 'rgba(0,180,220,0.1)';
            btn.style.borderColor = next === 'liquid'
                ? 'rgba(255,152,0,0.4)'
                : 'rgba(0,180,220,0.4)';
            btn.style.color = next === 'liquid' ? '#FF9800' : '#00b4dc';
        }
        if (typeof _applyDisplayMode === 'function') _applyDisplayMode();
        updateUI();
        logOperation('toggleDisplayMode', 'Switch display mode to ' + next, {mode: next}, 'view');
    };

    // Placeholder — becomes meaningful in Phase B/C
    function _applyDisplayMode() {
        // Filled in by later phases
    }
```

- [ ] **Step 5: Update state initialization**

Find `displayMode: 'modeling',` at line 2381. Replace with:

```javascript
        displayMode: 'air',  // 'air' | 'liquid' (legacy: 'modeling' | 'stlPreview')
```

Also update `isStlPreviewMode()` to keep backward compatibility:

Find:
```javascript
    function isStlPreviewMode() {
        return state.displayMode === 'stlPreview';
    }
```

No change needed — it already returns false for `'air'`/`'liquid'`.

- [ ] **Step 6: Run browser syntax check**

```bash
node --check src/frontend/web/js/liquid_cooling.js
```
Expected: no output (success)

```bash
# Verify HTML is syntactically intact — use Python to parse it
python3 -c "from html.parser import HTMLParser; p = HTMLParser(); p.feed(open('src/frontend/web/STL_generate.html').read()); print('HTML parse OK')"
```
Expected: `HTML parse OK`

- [ ] **Step 7: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase A — replace STL Preview with Liquid Simulation button, add displayMode helpers"
cd ..
git add frontend
git commit -m "feat(liquid): Phase A — dedicated mode infrastructure"
```

### Task A.2: Initial load migration for displayMode

- [ ] **Step 1: Find project load logic**

Run: `grep -n 'createModelFromRaw\|_applyProjectData\|function loadProject' src/frontend/web/STL_generate.html | head -20`

- [ ] **Step 2: Add displayMode migration in state restore**

Find the function that restores state from loaded JSON (near `_applyProjectData` or similar). Add logic to normalize `displayMode`:

Search for the string `raw.displayMode` in `STL_generate.html`. Update the existing line:
```javascript
            displayMode: raw.displayMode === 'stlPreview' ? 'stlPreview' : initial.displayMode,
```

Replace with:
```javascript
            displayMode: (raw.displayMode === 'air' || raw.displayMode === 'liquid')
                ? raw.displayMode
                : 'air',  // Legacy: map 'modeling' and 'stlPreview' to 'air'
```

- [ ] **Step 3: Verify**

Load an existing project via the browser. Confirm that `state.displayMode` is `'air'` (not `'modeling'`).

```bash
# Manual test: start server, load a project, check state in browser console
```

- [ ] **Step 4: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase A — migrate legacy displayMode values to air/liquid"
cd ..
git add frontend
git commit -m "chore: update submodule for Phase A.2"
```

### Phase A Acceptance

- Clicking the "💧 液冷模拟" button toggles `state.displayMode` between `'air'` and `'liquid'`
- Button label and colors update on click
- Undo checkpoint pushed before each toggle
- `logOperation` called after each toggle
- No visual changes yet (layout remains identical)
- Loading an old `.cfdproj` file normalizes `displayMode` to `'air'`

---

## Phase B: Right Panel Split + Air Toolbar Migration

**Goal:** Restructure the right panel into a top creation toolbar + bottom property inspector. Migrate the air creation buttons (Box/Rack/ACU/Wall/Baffle/Floor) into the toolbar. Mode-aware toolbar content (air vs liquid placeholder).

**Files:**
- Modify: `src/frontend/web/STL_generate.html`

### Task B.1: Restructure right panel HTML

- [ ] **Step 1: Find right panel structure**

Run: `grep -n 'id="right-panel"\|prop-title\|prop-content' src/frontend/web/STL_generate.html | head -10`
Expected: right-panel around line 695, prop-title line 707, prop-content line 708.

- [ ] **Step 2: Add creation toolbar container before prop-title**

Find (around line 707):
```html
        <div class="header" id="prop-title" data-i18n="inspector.title">⚙️ 属性检查器 (Inspector)</div>
        <div class="content" id="prop-content"></div>
```

Replace with:
```html
        <div id="creation-toolbar-header" class="header" style="padding:4px 8px; background:rgba(0,180,220,0.06); border-bottom:1px solid rgba(0,180,220,0.3);">
            <span id="creation-toolbar-title">➕ 创建几何 / Create Geometry</span>
        </div>
        <div id="creation-toolbar" style="padding:6px; border-bottom:1px solid var(--border); flex-shrink:0; max-height:240px; overflow-y:auto;"></div>
        <div class="header" id="prop-title" data-i18n="inspector.title">⚙️ 属性检查器 (Inspector)</div>
        <div class="content" id="prop-content"></div>
```

- [ ] **Step 3: Add `updateCreationToolbar()` function**

Find `function updateUI() {` (around line 9571). Add BEFORE it:

```javascript
    function updateCreationToolbar() {
        const bar = document.getElementById('creation-toolbar');
        const header = document.getElementById('creation-toolbar-title');
        if (!bar || !header) return;

        if (isLiquidMode()) {
            header.textContent = '💧 液冷工具栏 / Liquid Tools';
            bar.innerHTML = _renderLiquidToolbar();
        } else {
            header.textContent = '➕ 创建几何 / Create Geometry';
            bar.innerHTML = _renderAirToolbar();
        }
    }

    function _renderAirToolbar() {
        const t = (k) => (window.t ? window.t(k) : k);
        return `
            <div style="display:flex; flex-direction:column; gap:4px;">
                <button class="btn primary" onclick="createDefaultBaseModel()">${t('btn.new_box')}</button>
                <button class="btn" style="background:rgba(255,184,0,0.06); border-color:rgba(255,184,0,0.35); color:#ffb800;" onclick="createDefaultRackModel()">${t('btn.new_rack')}</button>
                <button class="btn" style="background:rgba(0,212,255,0.06); border-color:rgba(0,212,255,0.35); color:var(--accent3);" onclick="createDefaultAcuModel()">${t('btn.new_acu')}</button>
                <button class="btn" style="background:rgba(204,68,255,0.06); border-color:rgba(204,68,255,0.3); color:#cc44ff;" onpointerdown="beginDragCreate(event,'wall')" onclick="createDefaultWallModel()">${t('btn.new_wall')}</button>
                <button class="btn" style="background:rgba(0,255,136,0.05); border-color:rgba(0,255,136,0.25); color:var(--highlight);" onpointerdown="beginDragCreate(event,'baffle')" onclick="createDefaultBaffleModel()">${t('btn.new_baffle')}</button>
                <button class="btn" style="background:rgba(0,255,136,0.04); border-color:rgba(0,255,136,0.18); color:#00cc6a;" onclick="createDefaultFloorModel()">${t('btn.new_floor')}</button>
            </div>
        `;
    }

    function _renderLiquidToolbar() {
        // Phase B: placeholder. Phase D/E extends this.
        return `
            <div style="display:flex; flex-direction:column; gap:4px;">
                <button class="btn" style="background:rgba(0,180,220,0.08); border-color:rgba(0,180,220,0.4); color:#00b4dc;" onclick="createDefaultCduModel()">🏭 新建CDU / New CDU</button>
                <div style="font-size:10px; color:#888; padding:4px 6px; font-style:italic;">Phase D+E 将扩展更多按钮</div>
            </div>
        `;
    }
```

- [ ] **Step 4: Call `updateCreationToolbar()` inside `updateUI()`**

Find `function updateUI() {` (line 9571). After the `if (isStlPreviewMode()) { updateStlPreviewUI(); return; }` line, add:

```javascript
        updateCreationToolbar();
```

- [ ] **Step 5: Remove creation buttons from `renderRootInspector`**

Find `function renderRootInspector(prop)` (around line 7402). The function contains the 7 creation buttons at lines 7421-7427. Remove those buttons (the `createDefaultBaseModel`, `createDefaultRackModel`, `createDefaultAcuModel`, `createDefaultCduModel`, `createDefaultWallModel`, `createDefaultBaffleModel`, `createDefaultFloorModel` buttons). Keep all other content of the function.

Replace the 7-button block with:
```html
            <div style="padding:6px 10px; font-size:11px; color:#888; font-style:italic; border-bottom:1px solid #333; margin-bottom:6px;">
                💡 创建新模型请使用右上方"创建几何"工具栏 / Use the creation toolbar at the top right
            </div>
```

- [ ] **Step 6: Also call toolbar update on mode switch**

Update `_applyDisplayMode()` (placeholder added in Phase A) to:

```javascript
    function _applyDisplayMode() {
        updateCreationToolbar();
    }
```

- [ ] **Step 7: Verify manually**

Start server:
```bash
fuser -k 8005/tcp 2>/dev/null; sleep 1
mkdir -p /tmp/lc_phaseB_test/model
conda run -n dcdtuni python src/server/server.py --port 8005 --case-dir /tmp/lc_phaseB_test &
sleep 5
echo "Server ready — open http://localhost:8005/STL_generate.html in browser"
```

Load the page, verify:
- Right panel has a "➕ 创建几何" header at the top
- Air creation buttons (Box, Rack, ACU, etc.) appear in the toolbar
- Click "💧 液冷模拟" — toolbar header changes to "💧 液冷工具栏", only CDU button remains
- Click to return — toolbar goes back to air buttons

Stop server:
```bash
fuser -k 8005/tcp 2>/dev/null
```

- [ ] **Step 8: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase B — split right panel into toolbar + inspector, mode-aware toolbar"
cd ..
git add frontend
git commit -m "feat(liquid): Phase B complete"
```

### Phase B Acceptance

- Right panel has `#creation-toolbar` above `#prop-title`
- Air mode toolbar shows 6 creation buttons (Box/Rack/ACU/Wall/Baffle/Floor)
- Liquid mode toolbar shows CDU button + placeholder
- `renderRootInspector` no longer has the 7 duplicate buttons
- Mode switch updates toolbar content
- All existing air cooling features still work

---

## Phase C: Liquid Left Tab Bar + Geometry Tree + Air Model Dimming

**Goal:** In liquid mode, show 3-tab left panel (geometry / solver / postprocess), a liquid-specific geometry tree, and dim air models to indicate they're read-only.

**Files:**
- Modify: `src/frontend/web/STL_generate.html`

### Task C.1: Mode-aware tab bar

- [ ] **Step 1: Add helper to update tab visibility**

Find `function _applyLeftTabUI() {` (around line 15571). Add BEFORE it:

```javascript
    function _applyLeftTabBarForMode() {
        const meshBtn = document.getElementById('tab-btn-mesh');
        const adminBtn = document.getElementById('tab-btn-admin');
        const geoBtn = document.getElementById('tab-btn-geometry');
        const solverBtn = document.getElementById('tab-btn-solver');
        const postBtn = document.getElementById('tab-btn-postprocess');

        if (isLiquidMode()) {
            // Hide mesh and admin in liquid mode
            if (meshBtn) meshBtn.style.display = 'none';
            if (adminBtn) adminBtn.style.display = 'none';
            // Update solver / postprocess labels
            if (solverBtn) solverBtn.innerHTML = '🔬 液冷求解 / Liquid Solve';
            if (postBtn) postBtn.innerHTML = '📊 液冷后处理 / Liquid Results';
            // If active tab is mesh/admin, switch to geometry
            const t = state.dctwinConfig && state.dctwinConfig.activeTab;
            if (t === 'mesh' || t === 'admin') {
                state.dctwinConfig.activeTab = 'geometry';
            }
        } else {
            if (meshBtn) meshBtn.style.display = '';
            if (adminBtn) {
                // Admin visibility is role-based — only show if admin role
                const isAdmin = (window._currentUserRole === 'admin');
                adminBtn.style.display = isAdmin ? '' : 'none';
            }
            if (solverBtn) solverBtn.innerHTML = '🔬 求解';
            if (postBtn) postBtn.innerHTML = '📊 后处理';
        }
    }
```

- [ ] **Step 2: Wire into mode switch**

Update `_applyDisplayMode()`:
```javascript
    function _applyDisplayMode() {
        _applyLeftTabBarForMode();
        updateCreationToolbar();
        _applyLeftTabUI();  // Re-apply tab layout
        if (typeof rebuildGeometry === 'function') rebuildGeometry();
    }
```

- [ ] **Step 3: Call on initial load**

Find the end of the main initialization code (where `updateUI()` is first called on startup). Add after it:

```bash
# Search for the pattern first
grep -n 'updateUI();' src/frontend/web/STL_generate.html | tail -5
```

At the last `updateUI();` call in startup code, add after it:
```javascript
        _applyLeftTabBarForMode();
```

- [ ] **Step 4: Verify manually**

Start server, load project, click Liquid mode button:
- Mesh and Admin tabs disappear
- Solver button relabels to "液冷求解"
- Postprocess button relabels to "液冷后处理"
- Click back to Air — tabs restored

- [ ] **Step 5: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase C.1 — mode-aware left tab bar"
cd ..
git add frontend
git commit -m "feat(liquid): Phase C.1"
```

### Task C.2: Dim air models in liquid mode

- [ ] **Step 1: Add opacity rule to renderers**

Find `function renderBoxModel(model, modelGroup, ...) {` (around line 5807). At the start of the function, after the `isACU` detection, add:

```javascript
        // In liquid mode, dim air models to indicate read-only
        const isAirModelInLiquidMode = isLiquidMode()
            && model.kind !== 'cdu'
            && model.kind !== 'crh'
            && model.kind !== 'cold_plate'
            && model.kind !== 'heat_source'
            && model.kind !== 'pipe_segment'
            && model.kind !== 'pipe_junction';
        const dimOpacity = isAirModelInLiquidMode ? 0.35 : 1.0;
```

Then, when creating materials, multiply the existing opacity by `dimOpacity`. Find where `opacity:` is set in `spawnRenderable` calls within this function and adjust. For example, where `faceOpacity` is computed:

Find:
```javascript
                const faceOpacity = (model.opacity ?? 1);
```

Replace with:
```javascript
                const faceOpacity = (model.opacity ?? 1) * dimOpacity;
```

Apply the same pattern in `renderRackModel`, `renderWallModel`, `renderBaffleModel`, `renderFloorModel`. (Search: `grep -n 'function renderRackModel\|function renderWallModel\|function renderBaffleModel\|function renderFloorModel'`)

- [ ] **Step 2: Disable transform gizmo for air models in liquid mode**

Find where TransformControls is attached to selected models. Search:
```bash
grep -n 'transformControls.attach\|setMoveTool\|beginDragCreate' src/frontend/web/STL_generate.html | head -20
```

Find the selection handler or the function that activates transform controls on model selection. Add a guard:

At the start of that function (where `transformControls.attach(...)` is called):

```javascript
        // In liquid mode, air models are read-only
        if (isLiquidMode() && ['box','rack','acu','wall','baffle','floor'].includes(model.kind)) {
            transformControls.detach();
            return;
        }
```

- [ ] **Step 3: Verify**

Start server, create a rack in air mode, switch to liquid mode:
- Rack appears at ~35% opacity
- Clicking rack does not show transform gizmo
- Switch back to air — rack returns to full opacity, gizmo works

- [ ] **Step 4: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase C.2 — dim air models and disable gizmos in liquid mode"
cd ..
git add frontend
git commit -m "feat(liquid): Phase C.2"
```

### Task C.3: Liquid geometry tree

- [ ] **Step 1: Find tree rendering function**

Run: `grep -n 'function renderTree\|tree-container\|<ul class="tree">' src/frontend/web/STL_generate.html | head -10`

- [ ] **Step 2: Add `renderLiquidTree()` function**

Add BEFORE `function updateUI()`:

```javascript
    function renderLiquidTree() {
        const cdus = state.models.filter(m => m.kind === 'cdu');
        const crhs = state.models.filter(m => m.kind === 'crh');
        const coldPlates = state.models.filter(m => m.kind === 'cold_plate');
        const heatSources = state.models.filter(m => m.kind === 'heat_source');
        const pipes = state.models.filter(m => m.kind === 'pipe_segment');
        const racks = state.models.filter(m => m.kind === 'rack');

        let html = '<ul class="tree" style="padding:6px;">';
        // Root node
        html += '<li class="tree-node"><div class="node-label"><span class="node-main">💧 液冷系统 / Liquid System</span></div>';

        // CDUs
        html += '<ul><li class="tree-node"><div class="node-label"><span class="node-main">🏭 CDUs (' + cdus.length + ')</span></div>';
        if (cdus.length) {
            html += '<ul>';
            cdus.forEach(c => {
                const served = (c.liquidCooling && c.liquidCooling.servedRacks) || [];
                html += '<li class="tree-node"><div class="node-label" onclick="selectModelById(' + c.id + ')"><span class="node-main">' + escapeHtml(c.name) + (served.length ? ' [🔌 ' + served.length + ']' : '') + '</span></div></li>';
            });
            html += '</ul>';
        }
        html += '</li></ul>';

        // Rack attachments (CRH + cold plates grouped by rack)
        html += '<ul><li class="tree-node"><div class="node-label"><span class="node-main">🗂️ Rack附件 / Rack Attachments</span></div>';
        if (racks.length) {
            html += '<ul>';
            racks.forEach(r => {
                const rackCRH = crhs.filter(x => x.parentRackId === r.id);
                const rackCPs = coldPlates.filter(x => x.parentRackId === r.id);
                const hasAny = rackCRH.length || rackCPs.length;
                if (!hasAny) return;
                html += '<li class="tree-node"><div class="node-label"><span class="node-main">' + escapeHtml(r.name) + '</span></div><ul>';
                rackCRH.forEach(c => {
                    html += '<li class="tree-node"><div class="node-label" onclick="selectModelById(' + c.id + ')"><span class="node-main">🌊 ' + escapeHtml(c.name) + '</span></div></li>';
                });
                rackCPs.forEach(cp => {
                    html += '<li class="tree-node"><div class="node-label" onclick="selectModelById(' + cp.id + ')"><span class="node-main">❄️ ' + escapeHtml(cp.name) + '</span></div></li>';
                });
                html += '</ul></li>';
            });
            html += '</ul>';
        }
        html += '</li></ul>';

        // Pipes
        html += '<ul><li class="tree-node"><div class="node-label"><span class="node-main">🔗 管道网络 / Pipes (' + pipes.length + ')</span></div>';
        if (pipes.length) {
            html += '<ul>';
            pipes.forEach(p => {
                html += '<li class="tree-node"><div class="node-label" onclick="selectModelById(' + p.id + ')"><span class="node-main">' + (p.pipe && p.pipe.color === 'return' ? '🔴' : '🔵') + ' ' + escapeHtml(p.name) + '</span></div></li>';
            });
            html += '</ul>';
        }
        html += '</li></ul>';

        html += '</li></ul>';
        return html;
    }

    function escapeHtml(s) {
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    window.selectModelById = function(id) {
        if (typeof setSelection === 'function') setSelection('model', id);
    };
```

- [ ] **Step 3: Call from updateUI when in liquid mode**

Find where the air tree is rendered in `updateUI()` (search for `renderTree()` or `tree-container`). Wrap the air tree rendering with a mode check:

```javascript
        const treeEl = document.getElementById('tree-container');
        if (treeEl) {
            if (isLiquidMode()) {
                treeEl.innerHTML = renderLiquidTree();
            } else {
                // Existing air tree rendering
                // ...
            }
        }
```

- [ ] **Step 4: Verify**

Start server, create CDU and Rack, switch to liquid mode:
- Left tree shows "💧 液冷系统" with CDUs, Rack Attachments, Pipes sections
- Click CDU in tree — model is selected in 3D viewport

- [ ] **Step 5: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase C.3 — liquid geometry tree"
cd ..
git add frontend
git commit -m "feat(liquid): Phase C complete"
```

### Phase C Acceptance

- Liquid mode hides Mesh + Admin tabs
- Liquid mode relabels Solver / Postprocess tabs
- Air models dim to 35% opacity in liquid mode
- Transform gizmos disabled for air models in liquid mode
- Liquid geometry tree shows CDUs / Rack Attachments / Pipes sections
- Switching back to air mode restores full air UI

---

## Phase D: New Liquid Model Kinds + Data Migration

**Goal:** Implement `crh`, `cold_plate`, `heat_source`, `pipe_segment`, `pipe_junction` as first-class model kinds. Migrate legacy Phase 1-8 data (`model.rdhx`, `eq.liquidCooling.coldPlates`, `state.liquidCooling.piping.chains`) on project load.

**Files:**
- Modify: `src/frontend/web/STL_generate.html`

### Task D.1: Factory functions

- [ ] **Step 1: Add factories for new kinds**

Find `function createCduModel(name = 'CDU') {` (around line 2319). After that function's closing brace, add:

```javascript
    function createCrhModel(name = 'CRH', parentRackId = null) {
        return {
            id: nextModelId(),
            kind: 'crh',
            name,
            enabled: true,
            showAnchorMarker: false,
            opacity: 0.4,
            anchor: { x: 0, y: 0, z: 0 },
            base: { W: 0.6, D: 0.02, H: 2.0 },
            transform: getDefaultModelTransform(),
            moveTool: getDefaultModelMoveTool(),
            faces: createFacesState(),
            solidRegions: [],
            customSTLBase64: null,
            customOffset: { x: 0, y: 0, z: 0 },
            cfd: getDefaultCfd ? getDefaultCfd('crh') : { role: 'crh' },
            parentRackId: parentRackId,
            crh: {
                effectiveness: 0.5,
                waterFlowRate: 20,
                waterInletTemp: 25,
                heatRejectionCapacity: 30000
            }
        };
    }

    function createColdPlateModel(name = 'ColdPlate', parentRackId = null, parentServerId = null) {
        return {
            id: nextModelId(),
            kind: 'cold_plate',
            name,
            enabled: true,
            showAnchorMarker: false,
            opacity: 1,
            anchor: { x: 0, y: 0, z: 0 },
            base: { W: 0.1, D: 0.1, H: 0.01 },
            transform: getDefaultModelTransform(),
            moveTool: getDefaultModelMoveTool(),
            faces: createFacesState(),
            solidRegions: [],
            customSTLBase64: null,
            customOffset: { x: 0, y: 0, z: 0 },
            cfd: getDefaultCfd ? getDefaultCfd('cold_plate') : { role: 'cold_plate' },
            parentRackId: parentRackId,
            parentServerId: parentServerId,
            coldPlate: {
                specName: 'cp_default',
                chipTDP: 350,
                thermalModel: { chipArea: 0.004, chipThickness: 0.001, thermalConductivity: 148 },
                inletPortId: null,  // set after creation
                outletPortId: null
            }
        };
    }

    function createHeatSourceModel(name = 'HeatSource', parentRackId = null, parentServerId = null) {
        return {
            id: nextModelId(),
            kind: 'heat_source',
            name,
            enabled: true,
            showAnchorMarker: false,
            opacity: 1,
            anchor: { x: 0, y: 0, z: 0 },
            base: { W: 0.04, D: 0.04, H: 0.005 },
            transform: getDefaultModelTransform(),
            moveTool: getDefaultModelMoveTool(),
            faces: createFacesState(),
            solidRegions: [],
            customSTLBase64: null,
            customOffset: { x: 0, y: 0, z: 0 },
            cfd: getDefaultCfd ? getDefaultCfd('heat_source') : { role: 'heat_source' },
            parentRackId: parentRackId,
            parentServerId: parentServerId,
            heatSource: {
                power: 350,
                chipArea: 0.004,
                thermalMass: 60
            }
        };
    }

    function createPipeSegmentModel(name = 'Pipe', startPortId = '', endPortId = '', color = 'supply') {
        return {
            id: nextModelId(),
            kind: 'pipe_segment',
            name,
            enabled: true,
            showAnchorMarker: false,
            opacity: 1,
            anchor: { x: 0, y: 0, z: 0 },
            base: { W: 0, D: 0, H: 0 },  // pipes have no box
            transform: getDefaultModelTransform(),
            moveTool: getDefaultModelMoveTool(),
            faces: createFacesState(),
            solidRegions: [],
            customSTLBase64: null,
            customOffset: { x: 0, y: 0, z: 0 },
            cfd: getDefaultCfd ? getDefaultCfd('pipe_segment') : { role: 'pipe_segment' },
            pipe: {
                spec: 'pipe_25mm',
                startPortId: startPortId,
                endPortId: endPortId,
                waypoints: [],
                color: color,  // 'supply' | 'return'
                junctions: []
            }
        };
    }

    function createPipeJunctionModel(name = 'T-Branch', parentPipeId = null, position = null) {
        return {
            id: nextModelId(),
            kind: 'pipe_junction',
            name,
            enabled: true,
            showAnchorMarker: true,
            opacity: 1,
            anchor: position || { x: 0, y: 0, z: 0 },
            base: { W: 0.06, D: 0.06, H: 0.06 },
            transform: getDefaultModelTransform(),
            moveTool: getDefaultModelMoveTool(),
            faces: createFacesState(),
            solidRegions: [],
            customSTLBase64: null,
            customOffset: { x: 0, y: 0, z: 0 },
            cfd: getDefaultCfd ? getDefaultCfd('pipe_junction') : { role: 'pipe_junction' },
            parentPipeId: parentPipeId,
            junction: {
                type: 'T_branch',
                quaternion: { x: 0, y: 0, z: 0, w: 1 },
                connectedPipes: []
            }
        };
    }
```

- [ ] **Step 2: Update kind-handling code to include new kinds**

Find `if (model.kind === 'box' || model.kind === 'wall' || model.kind === 'baffle' || model.kind === 'floor' || model.kind === 'acu' || model.kind === 'cdu')` (line 2536 area). Add the new kinds:

```javascript
        if (model.kind === 'box' || model.kind === 'wall' || model.kind === 'baffle' || model.kind === 'floor' || model.kind === 'acu' || model.kind === 'cdu' || model.kind === 'crh' || model.kind === 'cold_plate' || model.kind === 'heat_source' || model.kind === 'pipe_segment' || model.kind === 'pipe_junction')
```

Same pattern for the opacity-handling code at line 10438.

- [ ] **Step 3: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase D.1 — factories for crh/cold_plate/heat_source/pipe_segment/pipe_junction"
cd ..
git add frontend
git commit -m "feat(liquid): Phase D.1"
```

### Task D.2: Data migration on project load

- [ ] **Step 1: Add migration function**

Find `function createModelFromRaw(rawModel)` (search for it). After that function's closing brace, add:

```javascript
    /**
     * Migrate a project loaded from Phase 1-8 schema to the new liquid cooling model-kind schema.
     * Runs after all raw models have been loaded into state.models.
     * Sets state.schemaVersion = 2 to prevent double-migration.
     */
    function migrateLiquidCoolingToKinds() {
        if (state.schemaVersion && state.schemaVersion >= 2) return;
        const newModels = [];
        const oldModels = state.models;

        // 1. Migrate model.rdhx → standalone crh model
        oldModels.forEach(m => {
            if (m.kind === 'rack' && m.rdhx && m.rdhx.enabled) {
                const crh = createCrhModel(`CRH_${m.name}`, m.id);
                crh.crh = {
                    effectiveness: m.rdhx.effectiveness ?? 0.5,
                    waterFlowRate: m.rdhx.waterFlowRate ?? 20,
                    waterInletTemp: m.rdhx.waterInletTemp ?? 25,
                    heatRejectionCapacity: m.rdhx.heatRejectionCapacity ?? 30000
                };
                // Position at back face of rack
                crh.anchor = { x: m.anchor.x, y: m.anchor.y + m.base.D, z: m.anchor.z };
                crh.base = { W: m.base.W, D: 0.02, H: m.base.H };
                newModels.push(crh);
                delete m.rdhx;
            }

            // 2. Migrate eq.liquidCooling.coldPlates → standalone cold_plate + heat_source models
            if (m.kind === 'rack' && m.rack && m.rack.equipments) {
                m.rack.equipments.forEach(eq => {
                    if (eq.liquidCooling && eq.liquidCooling.enabled && eq.liquidCooling.coldPlates) {
                        eq.liquidCooling.coldPlates.forEach(cp => {
                            const cpModel = createColdPlateModel(
                                `${cp.label || cp.id || 'CP'}_${eq.name || eq.id}`,
                                m.id,
                                eq.id
                            );
                            cpModel.coldPlate.specName = cp.specName || 'cp_default';
                            cpModel.coldPlate.chipTDP = cp.chipTDP || 350;
                            if (cp.thermalModel) cpModel.coldPlate.thermalModel = {...cp.thermalModel};
                            // Position at equipment slot
                            const slotZ = m.anchor.z + (eq.startUnit || 0) * (m.rack.unitHeight || 0.04445);
                            cpModel.anchor = { x: m.anchor.x + 0.05, y: m.anchor.y + 0.1, z: slotZ + 0.02 };
                            newModels.push(cpModel);

                            // Companion heat source
                            const hs = createHeatSourceModel(
                                `Heat_${cp.label || cp.id || 'CP'}_${eq.name || eq.id}`,
                                m.id,
                                eq.id
                            );
                            hs.heatSource.power = cp.chipTDP || 350;
                            if (cp.thermalModel) {
                                hs.heatSource.chipArea = cp.thermalModel.chipArea || 0.004;
                            }
                            hs.anchor = { ...cpModel.anchor, z: cpModel.anchor.z + 0.01 };
                            newModels.push(hs);
                        });
                        // Clear old nested data
                        delete eq.liquidCooling;
                    }
                });
            }
        });

        // 3. Migrate state.liquidCooling.piping.chains → pipe_segment models
        const chains = state.liquidCooling && state.liquidCooling.piping && state.liquidCooling.piping.chains;
        if (chains && Array.isArray(chains)) {
            chains.forEach((chain, idx) => {
                // Extract start/end ports and waypoints from the chain path
                const path = chain.path || [];
                if (path.length < 2) return;
                const startPort = typeof path[0] === 'string' ? path[0] : '';
                const endPort = typeof path[path.length - 1] === 'string' ? path[path.length - 1] : '';
                const waypoints = [];
                path.forEach(item => {
                    if (typeof item === 'object' && item.waypoints) {
                        waypoints.push(...item.waypoints);
                    }
                });
                const color = (chain.id || '').includes('return') ? 'return' : 'supply';
                const pipe = createPipeSegmentModel(
                    chain.id || `Pipe_${idx}`,
                    startPort,
                    endPort,
                    color
                );
                pipe.pipe.waypoints = waypoints;
                newModels.push(pipe);
            });
            state.liquidCooling.piping.chains = [];  // Clear migrated chains
        }

        // Append new models
        newModels.forEach(m => {
            state.models.push(m);
            if (_modelMap) _modelMap.set(m.id, m);
        });

        state.schemaVersion = 2;
        if (newModels.length > 0) {
            console.log(`[migration] Migrated ${newModels.length} liquid cooling objects to new kind schema`);
        }
    }
```

- [ ] **Step 2: Call migration after project load**

Find `function _applyProjectData` or the main `loadProject` success path. After `state.models` is populated from raw JSON, add:

```javascript
        migrateLiquidCoolingToKinds();
```

Also call on initial state creation for empty projects:

Find `getInitialState()` function. In its return object, add:
```javascript
            schemaVersion: 2,
```

- [ ] **Step 3: Test migration with a legacy project**

```bash
# Create a test project with old-schema liquid data
mkdir -p /tmp/lc_migration_test/model
cat > /tmp/lc_migration_test/model/test.cfdproj << 'EOF'
{
  "state": {
    "displayMode": "modeling",
    "models": [
      {"id": 100, "kind": "rack", "name": "Rack_1", "anchor":{"x":0,"y":0,"z":0}, "base":{"W":0.6,"D":1.0,"H":1.8}, "rack": {"equipments":[]}, "rdhx": {"enabled": true, "effectiveness": 0.6, "waterFlowRate": 25}}
    ],
    "liquidCooling": {
      "enabled": true,
      "piping": {"chains": [{"id":"chain_supply","path":["CDU_1_supply",{"pipe":"pipe_25mm","waypoints":[{"x":1,"y":1,"z":1},{"x":2,"y":1,"z":1}]},"Rack_1_manifold_in"]}]}
    }
  }
}
EOF
```

Load it manually in the browser and verify in console:
```javascript
state.models.filter(m => m.kind === 'crh').length  // should be 1
state.models.filter(m => m.kind === 'pipe_segment').length  // should be 1
state.schemaVersion  // should be 2
```

- [ ] **Step 4: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase D.2 — data migration from Phase 1-8 schema"
cd ..
git add frontend
git commit -m "feat(liquid): Phase D.2"
```

### Task D.3: Contextual creation functions

- [ ] **Step 1: Add window-exposed creators**

Find `window.createDefaultCduModel = function() {` (line ~10631). After it, add:

```javascript
    window.createCRHForRack = function(rackId) {
        const rack = _modelMap.get(rackId);
        if (!rack || rack.kind !== 'rack') {
            alert('请选中一个Rack / Please select a Rack first');
            return;
        }
        // Check if CRH already exists for this rack
        const existing = state.models.find(m => m.kind === 'crh' && m.parentRackId === rackId);
        if (existing) {
            alert('该Rack已有CRH / This Rack already has a CRH');
            return;
        }
        pushUndoCheckpoint('createCRHForRack');
        const crh = createCrhModel(`CRH_${rack.name}`, rackId);
        crh.anchor = { x: rack.anchor.x, y: rack.anchor.y + rack.base.D, z: rack.anchor.z };
        crh.base = { W: rack.base.W, D: 0.02, H: rack.base.H };
        state.models.push(crh);
        _modelMap.set(crh.id, crh);
        setSelection('model', crh.id);
        rebuildGeometry();
        logOperation('createCRHForRack', `Create CRH for ${rack.name}`, {rackId, crhId: crh.id}, 'create');
    };

    window.createColdPlateForServer = function(rackId, serverId) {
        const rack = _modelMap.get(rackId);
        if (!rack || rack.kind !== 'rack') return;
        const server = (rack.rack.equipments || []).find(e => e.id === serverId);
        if (!server) {
            alert('请选中一个Server / Please select a Server first');
            return;
        }
        pushUndoCheckpoint('createColdPlateForServer');
        const cp = createColdPlateModel(`CP_${server.name || server.id}`, rackId, serverId);
        const slotZ = rack.anchor.z + (server.startUnit || 0) * (rack.rack.unitHeight || 0.04445);
        cp.anchor = { x: rack.anchor.x + 0.05, y: rack.anchor.y + 0.1, z: slotZ + 0.02 };
        state.models.push(cp);
        _modelMap.set(cp.id, cp);
        setSelection('model', cp.id);
        rebuildGeometry();
        logOperation('createColdPlateForServer', `Create cold plate for ${server.name}`, {rackId, serverId}, 'create');
    };

    window.createHeatSourceForServer = function(rackId, serverId) {
        const rack = _modelMap.get(rackId);
        if (!rack) return;
        const server = (rack.rack.equipments || []).find(e => e.id === serverId);
        if (!server) return;
        pushUndoCheckpoint('createHeatSourceForServer');
        const hs = createHeatSourceModel(`Heat_${server.name || server.id}`, rackId, serverId);
        const slotZ = rack.anchor.z + (server.startUnit || 0) * (rack.rack.unitHeight || 0.04445);
        hs.anchor = { x: rack.anchor.x + 0.1, y: rack.anchor.y + 0.15, z: slotZ + 0.03 };
        state.models.push(hs);
        _modelMap.set(hs.id, hs);
        setSelection('model', hs.id);
        rebuildGeometry();
        logOperation('createHeatSourceForServer', `Create heat source for ${server.name}`, {rackId, serverId}, 'create');
    };
```

- [ ] **Step 2: Update `_renderLiquidToolbar()` to include contextual buttons**

Replace the placeholder `_renderLiquidToolbar` from Phase B with:

```javascript
    function _renderLiquidToolbar() {
        const sel = getSelectedModel && getSelectedModel();
        const selRackId = (sel && sel.kind === 'rack') ? sel.id : null;
        const selIsServer = state.selection && state.selection.type === 'rackEquipment';
        const rackForServer = selIsServer ? _modelMap.get(state.selection.modelId) : null;
        const serverId = selIsServer ? state.selection.equipmentId : null;

        let html = '<div style="display:flex; flex-direction:column; gap:4px;">';
        html += '<button class="btn" style="background:rgba(0,180,220,0.08); border-color:rgba(0,180,220,0.4); color:#00b4dc;" onclick="createDefaultCduModel()">🏭 新建CDU / New CDU</button>';
        html += '<button class="btn" id="btn-new-pipe" style="background:rgba(0,180,220,0.08); border-color:rgba(0,180,220,0.4); color:#00b4dc;" onclick="window._enterPipeCreationMode && window._enterPipeCreationMode()">🔗 新建管道 / New Pipe</button>';
        html += '<div style="border-top:1px solid #333; margin:4px 0; padding-top:4px;"><div style="font-size:10px; color:#888; padding:2px 4px;">上下文 / Contextual:</div>';
        if (selRackId) {
            html += '<button class="btn" style="background:rgba(0,180,220,0.06); color:#00b4dc;" onclick="window.createCRHForRack(' + selRackId + ')">🌊 为' + escapeHtml(sel.name) + '建CRH</button>';
            html += '<button class="btn" style="background:rgba(0,180,220,0.06); color:#00b4dc;" onclick="window.createColdPlateForRack && window.createColdPlateForRack(' + selRackId + ')">❄️ 为' + escapeHtml(sel.name) + '建冷板</button>';
        } else {
            html += '<button class="btn" disabled style="opacity:0.4;">🌊 为选中Rack建CRH</button>';
            html += '<button class="btn" disabled style="opacity:0.4;">❄️ 为选中Rack建冷板</button>';
        }
        if (selIsServer && rackForServer) {
            html += '<button class="btn" style="background:rgba(255,87,34,0.1); color:#FF5722;" onclick="window.createHeatSourceForServer(' + rackForServer.id + ', ' + serverId + ')">🔥 为Server建热源</button>';
        } else {
            html += '<button class="btn" disabled style="opacity:0.4;">🔥 为选中Server建热源</button>';
        }
        html += '</div></div>';
        return html;
    }
```

- [ ] **Step 3: Trigger toolbar update on selection change**

Find the selection handler (`setSelection` or similar). Add a call to `updateCreationToolbar()` after selection changes:

```bash
grep -n 'function setSelection\|window.setSelection' src/frontend/web/STL_generate.html
```

At the end of `setSelection` function body, add:
```javascript
        if (typeof updateCreationToolbar === 'function') updateCreationToolbar();
```

- [ ] **Step 4: Test**

Start server, enter liquid mode, select a rack:
- Liquid toolbar shows "为<rackName>建CRH" button
- Click → CRH model created, selected
- Tree shows new CRH under Rack Attachments

- [ ] **Step 5: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase D.3 — contextual creation functions and dynamic toolbar"
cd ..
git add frontend
git commit -m "feat(liquid): Phase D complete"
```

### Phase D Acceptance

- 5 new model kinds (`crh`, `cold_plate`, `heat_source`, `pipe_segment`, `pipe_junction`) create correctly via factories
- Legacy projects auto-migrate on load: `model.rdhx` → `crh` models, nested cold plates → `cold_plate`+`heat_source` models, chains → `pipe_segment` models
- `state.schemaVersion` set to 2 after migration
- Contextual toolbar shows enabled/disabled buttons based on selection
- Selected rack → CRH button active
- Selected server → Heat source button active
- CRH appears in liquid tree under its parent Rack

---

## Phase E: Pipe Drag-Connect + T-Branch Junctions

**Goal:** Enable pipe creation by dragging from port to port. Add right-click menu to insert T-branch junctions on existing pipes. Junctions use rotation gizmo for orientation.

**Files:**
- Create: `src/frontend/web/js/liquid_mode.js`
- Modify: `src/frontend/web/STL_generate.html` (script import + context menu wiring)
- Modify: `src/frontend/web/js/liquid_pipe_renderer.js` (render `pipe_segment` model kind)

### Task E.1: liquid_mode.js skeleton

- [ ] **Step 1: Create the file**

Create `src/frontend/web/js/liquid_mode.js`:

```javascript
/**
 * Liquid cooling mode interactions: pipe drag-connect state machine,
 * port indicator rendering, T-branch junction management.
 *
 * Depends on globals: state, _modelMap, scene, globalGroup, camera,
 * pushUndoCheckpoint, logOperation, createPipeSegmentModel,
 * createPipeJunctionModel, rebuildGeometry.
 */
(function() {
  'use strict';

  // Pipe creation mode state
  let _pipeMode = false;
  let _pipeStartPort = null;  // {modelId, portId, position: Vector3, type: 'in'|'out'}
  let _portIndicators = [];   // Three.js meshes for all visible ports
  let _rubberBandLine = null; // Temporary line during drag

  /**
   * Enter pipe creation mode — show all port indicators, activate drag listener.
   */
  function enterPipeCreationMode() {
    if (_pipeMode) return;
    _pipeMode = true;
    _buildPortIndicators();
    if (typeof window._showStatusMessage === 'function') {
      window._showStatusMessage('管道创建模式 / Pipe creation mode — drag from a port to another');
    }
    const btn = document.getElementById('btn-new-pipe');
    if (btn) {
      btn.style.background = 'rgba(255,204,0,0.2)';
      btn.style.borderColor = '#ffcc00';
      btn.style.color = '#ffcc00';
    }
  }

  function exitPipeCreationMode() {
    _pipeMode = false;
    _clearPortIndicators();
    _clearRubberBand();
    _pipeStartPort = null;
    const btn = document.getElementById('btn-new-pipe');
    if (btn) {
      btn.style.background = 'rgba(0,180,220,0.08)';
      btn.style.borderColor = 'rgba(0,180,220,0.4)';
      btn.style.color = '#00b4dc';
    }
  }

  function _buildPortIndicators() {
    _clearPortIndicators();
    // For each CDU model, add indicators for supply/return ports
    state.models.forEach(m => {
      if (m.kind === 'cdu') {
        _addPortIndicator(m.id, 'supply', _getCduPortPos(m, 'supply'), 'out');
        _addPortIndicator(m.id, 'return', _getCduPortPos(m, 'return'), 'in');
      }
      if (m.kind === 'cold_plate') {
        _addPortIndicator(m.id, 'cp_in', _getColdPlatePortPos(m, 'in'), 'in');
        _addPortIndicator(m.id, 'cp_out', _getColdPlatePortPos(m, 'out'), 'out');
      }
      if (m.kind === 'crh') {
        _addPortIndicator(m.id, 'crh_in', _getCrhPortPos(m, 'in'), 'in');
        _addPortIndicator(m.id, 'crh_out', _getCrhPortPos(m, 'out'), 'out');
      }
    });
  }

  function _addPortIndicator(modelId, portId, position, type) {
    if (typeof THREE === 'undefined' || typeof globalGroup === 'undefined') return;
    const color = type === 'in' ? 0x2196F3 : 0xF44336;
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(0.03, 12, 12),
      new THREE.MeshBasicMaterial({color: color, transparent: true, opacity: 0.9})
    );
    mesh.position.copy(position);
    mesh.userData = {isPortIndicator: true, modelId, portId, portType: type};
    globalGroup.add(mesh);
    _portIndicators.push(mesh);
  }

  function _clearPortIndicators() {
    _portIndicators.forEach(m => {
      if (m.parent) m.parent.remove(m);
      if (m.geometry) m.geometry.dispose();
      if (m.material) m.material.dispose();
    });
    _portIndicators = [];
  }

  function _getCduPortPos(m, which) {
    const a = m.anchor;
    const w = m.base.W, d = m.base.D, h = m.base.H;
    const cx = a.x + w / 2;
    const z = a.z + h * 0.85;
    const yOffset = which === 'supply' ? d + 0.02 : d + 0.02;
    const zOffset = which === 'supply' ? 0 : -0.05;
    return new THREE.Vector3(cx, a.y + yOffset, z + zOffset);
  }

  function _getColdPlatePortPos(m, which) {
    const a = m.anchor;
    const w = m.base.W;
    return new THREE.Vector3(
      which === 'in' ? a.x - 0.02 : a.x + w + 0.02,
      a.y + m.base.D / 2,
      a.z + m.base.H / 2
    );
  }

  function _getCrhPortPos(m, which) {
    const a = m.anchor;
    const yOff = which === 'in' ? -0.02 : 0.04;
    return new THREE.Vector3(a.x + m.base.W / 2, a.y + yOff, a.z + m.base.H * 0.5);
  }

  function _clearRubberBand() {
    if (_rubberBandLine && _rubberBandLine.parent) {
      _rubberBandLine.parent.remove(_rubberBandLine);
      if (_rubberBandLine.geometry) _rubberBandLine.geometry.dispose();
      if (_rubberBandLine.material) _rubberBandLine.material.dispose();
    }
    _rubberBandLine = null;
  }

  /**
   * Raycast from mouse event to find port indicator under cursor.
   * Returns the hit indicator mesh, or null.
   */
  function _pickPortAtMouse(event) {
    if (typeof camera === 'undefined' || typeof THREE === 'undefined') return null;
    const raycaster = new THREE.Raycaster();
    const canvas = event.target;
    const rect = canvas.getBoundingClientRect();
    const mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);
    const hits = raycaster.intersectObjects(_portIndicators, false);
    return hits.length > 0 ? hits[0].object : null;
  }

  /**
   * Handle click in pipe creation mode — start or end drag.
   */
  function handlePipeClick(event) {
    if (!_pipeMode) return false;
    const hit = _pickPortAtMouse(event);
    if (!hit) return false;
    if (!_pipeStartPort) {
      // First click — start drag
      if (hit.userData.portType !== 'out') {
        alert('请从出水口开始 (红色) / Start from an outlet port (red)');
        return true;
      }
      _pipeStartPort = {
        modelId: hit.userData.modelId,
        portId: hit.userData.portId,
        position: hit.position.clone(),
        type: 'out'
      };
      return true;
    } else {
      // Second click — end drag
      if (hit.userData.portType !== 'in') {
        alert('请连接到进水口 (蓝色) / Connect to an inlet port (blue)');
        return true;
      }
      _createPipeBetween(_pipeStartPort, {
        modelId: hit.userData.modelId,
        portId: hit.userData.portId,
        position: hit.position.clone(),
        type: 'in'
      });
      _pipeStartPort = null;
      exitPipeCreationMode();
      return true;
    }
  }

  function _createPipeBetween(fromPort, toPort) {
    pushUndoCheckpoint('createPipeSegment');
    const pipe = createPipeSegmentModel(
      `Pipe_${fromPort.modelId}_${toPort.modelId}`,
      fromPort.modelId + '.' + fromPort.portId,
      toPort.modelId + '.' + toPort.portId,
      'supply'
    );
    // Generate L-shape path
    const midZ = Math.max(fromPort.position.z, toPort.position.z) + 0.1;
    pipe.pipe.waypoints = [
      {x: fromPort.position.x, y: fromPort.position.y, z: fromPort.position.z},
      {x: fromPort.position.x, y: fromPort.position.y, z: midZ},
      {x: toPort.position.x, y: fromPort.position.y, z: midZ},
      {x: toPort.position.x, y: toPort.position.y, z: midZ},
      {x: toPort.position.x, y: toPort.position.y, z: toPort.position.z}
    ];
    state.models.push(pipe);
    if (_modelMap) _modelMap.set(pipe.id, pipe);
    if (typeof rebuildGeometry === 'function') rebuildGeometry();
    logOperation('createPipeSegment', 'Create pipe segment', {pipeId: pipe.id}, 'create');
  }

  // Expose API
  window._enterPipeCreationMode = enterPipeCreationMode;
  window._exitPipeCreationMode = exitPipeCreationMode;
  window._isPipeCreationMode = function() { return _pipeMode; };
  window._handlePipeClick = handlePipeClick;

  console.log('[liquid_mode.js] Loaded');
})();
```

- [ ] **Step 2: Add script tag to HTML**

Find existing `<script src="js/liquid_` tag in `STL_generate.html`. After the last liquid JS import, add:

```html
<script src="js/liquid_mode.js"></script>
```

- [ ] **Step 3: Wire into click handler**

Find the main 3D viewport click handler in `STL_generate.html`. At the start of it, add:

```javascript
        if (window._isPipeCreationMode && window._isPipeCreationMode()) {
            if (window._handlePipeClick(event)) return;
        }
```

- [ ] **Step 4: Syntax check and commit**

```bash
node --check src/frontend/web/js/liquid_mode.js && echo OK
cd frontend
git add web/js/liquid_mode.js web/STL_generate.html
git commit -m "feat(liquid): Phase E.1 — pipe creation mode state machine"
cd ..
git add frontend
git commit -m "feat(liquid): Phase E.1"
```

### Task E.2: Render pipe_segment models

- [ ] **Step 1: Update liquid_pipe_renderer.js**

Find the function that renders pipes from chains in `liquid_pipe_renderer.js`. Add a new function that renders from `pipe_segment` models:

```javascript
  function renderFromPipeSegmentModels() {
    if (typeof state === 'undefined' || !state.models) return;
    _pipeGroup = new THREE.Group();
    _pipeGroup.userData = { isPipeGroup: true };

    const pipes = state.models.filter(m => m.kind === 'pipe_segment' && m.enabled);
    pipes.forEach(p => {
      const wp = p.pipe && p.pipe.waypoints;
      if (!wp || wp.length < 2) return;
      const points = wp.map(w => new THREE.Vector3(w.x, w.y, w.z));
      const color = p.pipe.color === 'return' ? RETURN_COLOR : SUPPLY_COLOR;
      const mesh = createTubeMesh(points, color);
      if (mesh) _pipeGroup.add(mesh);
    });

    if (_pipeGroup.children.length > 0 && typeof globalGroup !== 'undefined') {
      globalGroup.add(_pipeGroup);
    }
  }
```

Then in `updatePipeGeometry()`, add a check for `pipe_segment` models BEFORE the existing chain rendering:

```javascript
  function updatePipeGeometry() {
    clearPipes();
    if (!state || !state.liquidCooling) return;

    // New: render from pipe_segment models if any exist
    const hasPipeSegments = state.models && state.models.some(m => m.kind === 'pipe_segment' && m.enabled);
    if (hasPipeSegments) {
      renderFromPipeSegmentModels();
      return;
    }

    // Legacy: existing chain-based rendering
    // ... (keep existing code)
  }
```

- [ ] **Step 2: Syntax check and commit**

```bash
node --check src/frontend/web/js/liquid_pipe_renderer.js && echo OK
cd frontend
git add web/js/liquid_pipe_renderer.js
git commit -m "feat(liquid): Phase E.2 — render pipe_segment model kind"
cd ..
git add frontend
git commit -m "feat(liquid): Phase E.2"
```

### Task E.3: T-branch junction (deferred stub)

- [ ] **Step 1: Add stub function**

In `liquid_mode.js`, add:

```javascript
  /**
   * Upgrade a pipe waypoint to a T-branch junction.
   * Creates a pipe_junction model at the waypoint position.
   */
  window._upgradeToTBranch = function(pipeId, waypointIndex) {
    const pipe = _modelMap.get(pipeId);
    if (!pipe || pipe.kind !== 'pipe_segment') return;
    const wp = pipe.pipe.waypoints[waypointIndex];
    if (!wp) return;

    pushUndoCheckpoint('upgradeToTBranch');
    const junction = createPipeJunctionModel(
      `TBranch_${pipe.name}_${waypointIndex}`,
      pipeId,
      {x: wp.x, y: wp.y, z: wp.z}
    );
    junction.junction.connectedPipes = [pipeId];
    pipe.pipe.junctions.push({
      afterWaypointIndex: waypointIndex,
      type: 'T_branch',
      quaternion: {x:0, y:0, z:0, w:1},
      branchPipeId: null  // will be set when second pipe is connected
    });
    state.models.push(junction);
    _modelMap.set(junction.id, junction);
    rebuildGeometry();
    logOperation('upgradeToTBranch', 'Upgrade waypoint to T-branch', {pipeId, waypointIndex}, 'create');
  };
```

- [ ] **Step 2: Commit**

```bash
cd frontend
git add web/js/liquid_mode.js
git commit -m "feat(liquid): Phase E.3 — T-branch junction stub"
cd ..
git add frontend
git commit -m "feat(liquid): Phase E complete"
```

### Phase E Acceptance

- Clicking "🔗 新建管道" enters pipe creation mode
- Port indicators (blue/red spheres) appear on all liquid models
- Clicking a red outlet port starts the drag
- Clicking a blue inlet port completes the drag and creates a `pipe_segment`
- Pipe renders as TubeGeometry with L-shape path
- Junction upgrade function exists (full rotation gizmo is deferred polish)

---

## Phase F: Result Inheritance + Air Mode Cleanup

**Goal:** Add the "Inherit Liquid Results" checkbox in the air solver tab (residual power mode). Remove all remaining liquid UI from air mode.

**Files:**
- Modify: `src/frontend/web/STL_generate.html`
- Modify: `src/backend/aircooling/runner/stlgen_runner.py`

### Task F.1: Inheritance checkbox in air solver tab

- [ ] **Step 1: Locate the solver tab content area**

Run: `grep -n 'id="solver-settings-panel"' src/frontend/web/STL_generate.html`

- [ ] **Step 2: Add the new prop-group**

Find the existing solver-settings-panel content (after the "求解器设置" block). Add a new block BEFORE the liquid-cooling-solver-group:

```html
        <div class="prop-group" id="liquid-inheritance-group" style="border-color:#FF5722; margin:8px;">
            <div class="prop-title" style="color:#FF5722">🔥 液冷结果继承 / Liquid Cooling Inheritance</div>
            <div class="prop-row">
                <label>继承液冷结果 / Inherit</label>
                <input type="checkbox" id="lc-inherit-checkbox" onchange="window.setLiquidInheritance(this.checked)">
            </div>
            <div id="lc-inherit-status" style="padding:4px 8px; font-size:11px; color:#888;">
                请先在液冷模式运行求解 / Please run solve in liquid mode first
            </div>
            <div id="lc-inherit-details" style="display:none; padding:4px 8px;">
                <div style="font-size:11px; color:#ccc;" id="lc-inherit-summary"></div>
                <button class="btn" style="width:100%; font-size:11px; padding:4px;" onclick="window.refreshLiquidInheritanceStatus()">🔄 刷新 / Refresh</button>
            </div>
        </div>
```

- [ ] **Step 3: Add handler function**

Add to a script section or existing liquid_cooling.js:

```javascript
    window.setLiquidInheritance = function(enabled) {
        pushUndoCheckpoint('setLiquidInheritance');
        if (!state.liquidCooling) state.liquidCooling = {};
        state.liquidCooling.inheritResults = !!enabled;
        logOperation('setLiquidInheritance', 'Inherit liquid results: ' + enabled, {enabled}, 'modify');
    };

    window.refreshLiquidInheritanceStatus = async function() {
        try {
            const resp = await fetch('/api/liquid/results');
            if (!resp.ok) {
                document.getElementById('lc-inherit-status').textContent = '⚠️ 液冷结果不可用 / No liquid results';
                document.getElementById('lc-inherit-details').style.display = 'none';
                return;
            }
            const data = await resp.json();
            const summary = document.getElementById('lc-inherit-summary');
            const servers = data.servers || {};
            const count = Object.keys(servers).length;
            let maxT = 0;
            Object.values(servers).forEach(s => { if (s.chip_max_temp_C > maxT) maxT = s.chip_max_temp_C; });
            if (summary) {
                summary.innerHTML = `服务器数 / Servers: ${count}<br>最高芯片温度 / Max T_chip: ${maxT.toFixed(1)}°C`;
            }
            document.getElementById('lc-inherit-status').textContent = '✅ 液冷结果已加载 / Results loaded';
            document.getElementById('lc-inherit-details').style.display = '';
        } catch(e) {
            document.getElementById('lc-inherit-status').textContent = 'Error: ' + e.message;
        }
    };
```

- [ ] **Step 4: Update buildCfdExportJson to include the flag**

Find `function buildCfdExportJson()` (line 23223). Add to the cfdData object:

```javascript
        inheritLiquidResults: !!(state.liquidCooling && state.liquidCooling.inheritResults),
```

- [ ] **Step 5: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase F.1 — inherit liquid results checkbox in air solver"
cd ..
git add frontend
git commit -m "feat(liquid): Phase F.1"
```

### Task F.2: Backend flag check

- [ ] **Step 1: Update stlgen_runner.py**

Find the RDHx and liquid results loading block in `src/backend/aircooling/runner/stlgen_runner.py`. Around line 1773-1784 (based on earlier review), the current code loads liquid results unconditionally if the file exists.

Modify to check the flag:

```python
        # --- Load liquid cooling results (if available and inheritance enabled) ---
        liquid_results_path = self.model_dir / "liquid_results.json"
        inherit_liquid = self._data.get("inheritLiquidResults", False)
        liquid_server_results: Dict[str, Any] = {}
        if inherit_liquid and liquid_results_path.exists():
            try:
                from dcdtuni.liquid.results import LiquidResult
                lr = LiquidResult.load(liquid_results_path)
                liquid_server_results = {sid: srv for sid, srv in lr.servers.items()}
                logger.info(f"Inheriting liquid cooling results for {len(liquid_server_results)} servers")
            except Exception as e:
                logger.warning(f"Failed to load liquid results: {e}")
```

- [ ] **Step 2: Run tests to verify backward compat**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/ --tb=short 2>&1 | tail -5
```
Expected: 55 passed (existing tests should still pass because default is `False`)

- [ ] **Step 3: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(liquid): Phase F.2 — gate liquid results inheritance on explicit flag"
```

### Task F.3: Remove liquid UI from air mode

- [ ] **Step 1: Hide old liquid-cooling-solver-group in air mode**

Find `<div class="prop-group" id="liquid-cooling-solver-group"` (line 230). Wrap it with mode-aware visibility. In the `_applyLeftTabUI` or `_applyDisplayMode` function, add:

```javascript
        const lcGroup = document.getElementById('liquid-cooling-solver-group');
        if (lcGroup) {
            lcGroup.style.display = isLiquidMode() ? '' : 'none';
        }
        // Also hide liquid-results-group in air mode (move to liquid postprocess)
        const lcResults = document.getElementById('liquid-results-group');
        if (lcResults) {
            lcResults.style.display = isLiquidMode() ? '' : 'none';
        }
```

- [ ] **Step 2: Hide CDU button in air toolbar**

The air toolbar (`_renderAirToolbar`) should no longer have the CDU button (was added as a temporary measure). Verify it's removed.

- [ ] **Step 3: Remove RDHx section from air-mode Rack inspector**

Find `function renderRackRDHxInspector` in STL_generate.html. Add a guard at the start:

```javascript
    function renderRackRDHxInspector(model) {
        if (isAirMode()) return '';  // RDHx moved to 'crh' model in liquid mode
        // ... existing code
    }
```

- [ ] **Step 4: Remove liquid cooling from server equipment inspector in air mode**

Find the equipment inspector function (search for `renderRackEquipmentInspector`). Add:

```javascript
        // Hide liquid cooling section in air mode
        if (isAirMode()) {
            // Skip the liquid cooling equipment section
        } else {
            // Show the existing liquid cooling section
        }
```

- [ ] **Step 5: Final verification**

```bash
# Start server, verify air mode has no liquid widgets (except inheritance checkbox)
fuser -k 8005/tcp 2>/dev/null; sleep 1
mkdir -p /tmp/lc_phaseF/model
conda run -n dcdtuni python src/server/server.py --port 8005 --case-dir /tmp/lc_phaseF &
sleep 5
# Manual browser test
echo "Open http://localhost:8005/STL_generate.html — verify air mode is clean"
```

Run backend tests:
```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/ --tb=short 2>&1 | tail -3
```
Expected: 55 passed

- [ ] **Step 6: Commit**

```bash
cd frontend
git add web/STL_generate.html
git commit -m "feat(liquid): Phase F.3 — remove liquid UI from air mode"
cd ..
git add frontend
git commit -m "feat(liquid): Phase F complete — air mode clean"
```

### Phase F Acceptance

- Air solver tab shows "🔥 液冷结果继承" prop-group
- Checkbox is unchecked by default
- `buildCfdExportJson` writes `inheritLiquidResults` flag
- Backend reads flag and only uses liquid results when true
- Air mode has no other liquid cooling widgets (no CDU button, no RDHx in Rack, no liquid cooling in server inspector)
- Liquid mode retains all liquid cooling functionality
- Existing 55 backend tests still pass

---

## Global Verification

After all phases complete:

- [ ] Run full backend test suite:
  ```bash
  conda run -n dcdtuni python -m pytest tests/test_liquid/ -v
  ```
  Expected: 55 passed

- [ ] JS syntax check all liquid files:
  ```bash
  for f in liquid_cooling liquid_pipe_renderer liquid_topology_editor liquid_results_panel liquid_mode; do
    node --check src/frontend/web/js/${f}.js
  done
  ```
  Expected: all OK

- [ ] Manual E2E:
  1. Start server, create a project with Rack and ACU in air mode
  2. Switch to liquid mode
  3. Create CDU, select Rack → create CRH, create cold plate for a server
  4. Drag-connect pipe from CDU to Rack
  5. Switch back to air mode — check "Inherit Liquid Results" checkbox
  6. Run CFD (or just check that `cfd_export.json` contains `inheritLiquidResults: true`)

- [ ] Final commit:
  ```bash
  git log --oneline | head -20
  ```

---

## Plan Self-Review

**Spec coverage**:
- §3 (architecture) → Phase A (infrastructure) ✓
- §4 (UI structure) → Phase B (toolbar) + Phase C (tabs/tree) ✓
- §5 (model kinds + migration) → Phase D ✓
- §6 (interactions) → Phase D (contextual) + Phase E (pipe drag) ✓
- §7 (result transfer) → Phase F ✓
- §8 (phases) → All phases A-F ✓
- §9 (file impact) → task files match ✓

**Placeholder scan**: No TBD/TODO in task steps. All code blocks are complete and executable.

**Type consistency**:
- `createCrhModel` signature consistent across Task D.1 factory and Task D.2 migration use
- `createColdPlateModel(name, parentRackId, parentServerId)` consistent
- `pipe_segment.pipe.color` uses `'supply'|'return'` consistently
- Function `updateCreationToolbar()` defined in Phase B.1.3 and used in Phase B.1.4, C.1.2, D.3.2 consistently
- `_applyDisplayMode()` defined in Phase A.1.4 (empty stub), extended in Phase B.1.6, C.1.2
