# Per-Unit Server Face Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each U-height category of fan-server its own mesh refinement level, with automatic safety checks before mesh generation.

**Architecture:** Frontend JS functions for bucketing/calculation/UI, collapsible HTML section in mesh tab, warning dialog before mesh gen, backend per-solid refinement dispatch in snappyHexMeshDict.

**Tech Stack:** Vanilla JS (STL_generate.html), Jinja2/Python (stlgen_runner.py), i18n JSON

---

## File Structure

| File | Responsibility |
|------|---------------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | All frontend: bucketing logic, collapsible UI, auto-detect, pre-check dialog, export |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | Chinese i18n keys for dialog/labels |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | English i18n keys for dialog/labels |
| `dctwin/dctwin/runner/stlgen_runner.py` | Per-solid refinement dispatch in `_generate_snappy_hex_mesh_dict()` |

---

### Task 1: Add i18n Keys

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Add Chinese i18n keys**

In `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`, add these keys after the existing `btn.seed_collision_ignore` key (around line 115):

```json
  "btn.auto_detect_refinement": "\ud83d\udd0d \u81ea\u52a8\u68c0\u6d4b",
  "btn.server_refinement_back": "\u8fd4\u56de\u4fee\u6539",
  "btn.server_refinement_ignore": "\u5ffd\u7565\u5e76\u7ee7\u7eed\u751f\u6210",
```

And add these keys after the existing `dialog.seed_collision_item` key (around line 198):

```json
  "dialog.server_refinement_title": "\u670d\u52a1\u5668\u9762\u7f51\u683c\u7cbe\u5ea6\u9884\u8b66",
  "dialog.server_refinement_desc": "\u4ee5\u4e0b U \u4f4d\u9ad8\u5ea6\u7684\u670d\u52a1\u5668\u9762\u52a0\u5bc6\u5c42\u7ea7\u4e0d\u8db3\uff0csnappyHexMesh \u53ef\u80fd\u65e0\u6cd5\u6355\u83b7\u8fd9\u4e9b patch\uff0c\u5bfc\u81f4\u6c42\u89e3\u5931\u8d25\u3002",
  "dialog.server_refinement_item": "{uHeight}U \u670d\u52a1\u5668 ({count}\u53f0): \u5f53\u524d [{curMin},{curMax}], \u5efa\u8bae\u81f3\u5c11 [{safeMin},{safeMax}] \u2014 patch \u9ad8\u5ea6 {patchH}mm, \u5f53\u524d\u6700\u7ec6 cell {cellSize}mm",
```

And add this label key near other `label.*` keys:

```json
  "label.per_unit_refinement": "{n}U ({count}\u53f0)",
  "log.auto_adjust_refinement": "\u81ea\u52a8\u8c03\u6574\u670d\u52a1\u5668\u9762\u52a0\u5bc6: {changes}",
```

- [ ] **Step 2: Add English i18n keys**

In `STLGeneratorForOpenFoam/web/i18n/en-US.json`, add the same keys with English values:

After `btn.seed_collision_ignore`:
```json
  "btn.auto_detect_refinement": "\ud83d\udd0d Auto Detect",
  "btn.server_refinement_back": "Back to Edit",
  "btn.server_refinement_ignore": "Ignore and Continue",
```

After `dialog.seed_collision_item`:
```json
  "dialog.server_refinement_title": "Server Face Mesh Accuracy Warning",
  "dialog.server_refinement_desc": "The following server face refinement levels are insufficient. snappyHexMesh may fail to capture these patches, causing solver errors.",
  "dialog.server_refinement_item": "{uHeight}U servers ({count} units): current [{curMin},{curMax}], recommended [{safeMin},{safeMax}] \u2014 patch height {patchH}mm, finest cell {cellSize}mm",
```

And label/log keys:
```json
  "label.per_unit_refinement": "{n}U ({count} units)",
  "log.auto_adjust_refinement": "Auto-adjusted server face refinement: {changes}",
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/i18n/zh-CN.json STLGeneratorForOpenFoam/web/i18n/en-US.json
git commit -m "feat: add i18n keys for per-unit server refinement UI and dialog"
```

---

### Task 2: Core JS Functions — Bucketing, Calculation, Auto-Detect

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

These functions go right before the existing `window.setMeshRefineLevel` function (line 20200).

- [ ] **Step 1: Add the bucketing function**

Insert before `window.setMeshRefineLevel` (line 20200):

```javascript
    // ── Per-Unit Server Face Refinement ──

    /** Scan all rack fanServer equipment and group by spanUnits.
     *  Returns: { "1": { count: 3, patchHeight: 0.04445 }, "2": { count: 2, patchHeight: 0.0889 }, ... }
     */
    function scanServerUnitBuckets() {
        var buckets = {};
        (state.models || []).forEach(function(model) {
            if (model.kind !== 'rack' || !model.rack || !model.rack.equipments) return;
            var unitH = model.rack.unitHeight || RACK_DEFAULT_UNIT_HEIGHT;
            model.rack.equipments.forEach(function(eq) {
                if (eq.kind !== 'fanServer') return;
                var span = eq.spanUnits || 1;
                var key = String(span);
                if (!buckets[key]) buckets[key] = { count: 0, patchHeight: span * unitH };
                buckets[key].count++;
            });
        });
        return buckets;
    }

    /** Compute minimum safe refinement level for a given patch height.
     *  Returns [minLevel, maxLevel] such that baseSize / 2^maxLevel <= patchHeight / 2.
     */
    function computeMinSafeLevel(baseSize, patchHeight) {
        if (patchHeight <= 0 || baseSize <= 0) return [0, 0];
        var maxCellSize = patchHeight / 2;
        var minMaxLevel = Math.ceil(Math.log2(baseSize / maxCellSize));
        minMaxLevel = Math.max(minMaxLevel, 0);
        var minMinLevel = Math.max(minMaxLevel - 1, 0);
        return [minMinLevel, minMaxLevel];
    }

    /** Run auto-detect: scan buckets, compute safe levels, auto-fill insufficient ones.
     *  Returns array of { uHeight, count, patchHeight, oldLevel, newLevel } for items that changed.
     */
    function autoDetectServerRefinement() {
        var buckets = scanServerUnitBuckets();
        var baseSize = state.cfdRoom.meshBaseSize || 0.4;
        var perUnit = state.cfdRoom.serverFacePerUnitLevel || {};
        var unified = state.cfdRoom.serverFaceRefinementLevel || [1, 1];
        var changes = [];

        Object.keys(buckets).sort(function(a, b) { return +a - +b; }).forEach(function(key) {
            var bucket = buckets[key];
            var safeLevel = computeMinSafeLevel(baseSize, bucket.patchHeight);
            var current = perUnit[key] || unified.slice();
            if (current[0] < safeLevel[0] || current[1] < safeLevel[1]) {
                var newLevel = [Math.max(current[0], safeLevel[0]), Math.max(current[1], safeLevel[1])];
                changes.push({
                    uHeight: +key,
                    count: bucket.count,
                    patchHeight: bucket.patchHeight,
                    oldLevel: current.slice(),
                    newLevel: newLevel,
                    safeLevel: safeLevel
                });
                perUnit[key] = newLevel;
            } else {
                if (!perUnit[key]) perUnit[key] = current;
            }
        });

        // Clean up buckets that no longer exist in scene
        Object.keys(perUnit).forEach(function(key) {
            if (!buckets[key]) delete perUnit[key];
        });

        state.cfdRoom.serverFacePerUnitLevel = perUnit;
        return changes;
    }

    /** Collect server refinement warnings (for pre-mesh dialog).
     *  Returns array of { uHeight, count, patchHeight, curMin, curMax, safeMin, safeMax, cellSize }.
     */
    function collectServerRefinementWarnings() {
        var buckets = scanServerUnitBuckets();
        var baseSize = state.cfdRoom.meshBaseSize || 0.4;
        var perUnit = state.cfdRoom.serverFacePerUnitLevel || {};
        var unified = state.cfdRoom.serverFaceRefinementLevel || [1, 1];
        var warnings = [];

        Object.keys(buckets).sort(function(a, b) { return +a - +b; }).forEach(function(key) {
            var bucket = buckets[key];
            var safeLevel = computeMinSafeLevel(baseSize, bucket.patchHeight);
            var current = perUnit[key] || unified.slice();
            if (current[0] < safeLevel[0] || current[1] < safeLevel[1]) {
                var cellSize = baseSize / Math.pow(2, current[1]);
                warnings.push({
                    uHeight: +key,
                    count: bucket.count,
                    patchHeight: bucket.patchHeight,
                    curMin: current[0],
                    curMax: current[1],
                    safeMin: safeLevel[0],
                    safeMax: safeLevel[1],
                    cellSize: cellSize
                });
            }
        });
        return warnings;
    }
```

- [ ] **Step 2: Verify syntax**

Open browser dev console or run a quick lint check — ensure no syntax errors in the added block.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: core JS functions for server U-bucket scanning, safe level calc, auto-detect"
```

---

### Task 3: Collapsible UI in Mesh Tab

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Replace the "服务器面" HTML row with collapsible section**

Replace lines 80-83 (the server face label, two inputs, and empty span):

```html
                    <label title="fan-server / non-fan 设备面" data-i18n="label.server_face" data-i18n-title="tooltip.refine_server_face">服务器面</label>
                    <input type="number" id="ms-srv-0" min="0" max="6" value="1" class="mesh-lvl-input" oninput="window.setMeshRefineLevel('serverFaceRefinementLevel',0,+this.value)">
                    <input type="number" id="ms-srv-1" min="0" max="6" value="1" class="mesh-lvl-input" oninput="window.setMeshRefineLevel('serverFaceRefinementLevel',1,+this.value)">
                    <span></span>
```

With this collapsible section:

```html
                    <label title="fan-server / non-fan 设备面" data-i18n="label.server_face" data-i18n-title="tooltip.refine_server_face">服务器面</label>
                    <input type="number" id="ms-srv-0" min="0" max="6" value="1" class="mesh-lvl-input" oninput="window.setUnifiedServerLevel(0,+this.value)">
                    <input type="number" id="ms-srv-1" min="0" max="6" value="1" class="mesh-lvl-input" oninput="window.setUnifiedServerLevel(1,+this.value)">
                    <button id="srv-refine-toggle" onclick="window.toggleServerRefinePanel()" style="background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:0.9rem;padding:0 4px;" title="展开/折叠 U 位加密设置">▼</button>
                </div>
                <div id="srv-refine-panel" style="display:none;margin:-4px 0 6px 0;padding:6px 8px;border:1px solid rgba(90,167,255,0.15);background:rgba(90,167,255,0.03);">
                    <div id="srv-refine-buckets" style="display:flex;flex-direction:column;gap:4px;font-size:0.8rem;"></div>
                    <button class="btn" onclick="window.runAutoDetectServerRefinement()" style="margin-top:6px;padding:4px 10px;font-size:0.78rem;" data-i18n="btn.auto_detect_refinement">🔍 自动检测</button>
                </div>
                <div style="display:grid; grid-template-columns:auto 1fr 1fr auto; gap:2px 4px; align-items:center; font-size:0.82rem;">
```

**Note:** This closes the existing grid `</div>`, inserts the collapsible panel, and reopens a new grid `<div>` for the remaining rows (ACU, Box, etc.). The closing `</div>` for the original grid on line 70 must be moved to line 83 (before the collapsible panel).

Actually, the cleaner approach is to insert the collapsible panel **outside** the grid, after the `<span></span>` placeholder. Let me revise:

Replace lines 80-83:

```html
                    <label title="fan-server / non-fan 设备面" data-i18n="label.server_face" data-i18n-title="tooltip.refine_server_face">服务器面</label>
                    <input type="number" id="ms-srv-0" min="0" max="6" value="1" class="mesh-lvl-input" oninput="window.setMeshRefineLevel('serverFaceRefinementLevel',0,+this.value)">
                    <input type="number" id="ms-srv-1" min="0" max="6" value="1" class="mesh-lvl-input" oninput="window.setMeshRefineLevel('serverFaceRefinementLevel',1,+this.value)">
                    <span></span>
```

With:

```html
                    <label title="fan-server / non-fan 设备面" data-i18n="label.server_face" data-i18n-title="tooltip.refine_server_face">服务器面</label>
                    <input type="number" id="ms-srv-0" min="0" max="6" value="1" class="mesh-lvl-input" oninput="window.setUnifiedServerLevel(0,+this.value)">
                    <input type="number" id="ms-srv-1" min="0" max="6" value="1" class="mesh-lvl-input" oninput="window.setUnifiedServerLevel(1,+this.value)">
                    <button id="srv-refine-toggle" onclick="window.toggleServerRefinePanel()" style="background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:0.9rem;padding:0 4px;" title="展开/折叠 U 位加密设置">▼</button>
```

Then insert the collapsible panel div **after the closing `</div>` of the grid** (which is after the last row — 挡板/地板). Find the closing `</div>` of the refinement grid and insert right after it:

```html
                <div id="srv-refine-panel" style="display:none;margin:2px 0 6px 0;padding:6px 8px;border:1px solid rgba(90,167,255,0.15);background:rgba(90,167,255,0.03);">
                    <div id="srv-refine-buckets" style="display:flex;flex-direction:column;gap:4px;font-size:0.8rem;"></div>
                    <button class="btn" onclick="window.runAutoDetectServerRefinement()" style="margin-top:6px;padding:4px 10px;font-size:0.78rem;" data-i18n="btn.auto_detect_refinement">🔍 自动检测</button>
                </div>
```

- [ ] **Step 2: Add the toggle, unified setter, auto-detect UI, and bucket renderer JS functions**

Insert right after the `autoDetectServerRefinement()` function (added in Task 2):

```javascript
    window.toggleServerRefinePanel = function() {
        var panel = document.getElementById('srv-refine-panel');
        var btn = document.getElementById('srv-refine-toggle');
        if (!panel || !btn) return;
        var visible = panel.style.display !== 'none';
        panel.style.display = visible ? 'none' : '';
        btn.textContent = visible ? '▼' : '▲';
        if (!visible) refreshServerBucketUI();
    };

    window.setUnifiedServerLevel = function(idx, value) {
        pushUndoCheckpoint('setUnifiedServerLevel');
        if (!Array.isArray(state.cfdRoom.serverFaceRefinementLevel)) state.cfdRoom.serverFaceRefinementLevel = [1, 1];
        state.cfdRoom.serverFaceRefinementLevel[idx] = value;
        // Overwrite all per-unit buckets with unified value
        var unified = state.cfdRoom.serverFaceRefinementLevel;
        var perUnit = state.cfdRoom.serverFacePerUnitLevel || {};
        Object.keys(perUnit).forEach(function(key) {
            perUnit[key] = unified.slice();
        });
        state.cfdRoom.serverFacePerUnitLevel = perUnit;
        refreshServerBucketUI();
        logOperation('setUnifiedServerLevel', t('log.refinement_strategy', { key: 'serverFaceRefinementLevel', value: JSON.stringify(unified) }), { idx, value }, 'modify');
    };

    window.setPerUnitServerLevel = function(uKey, idx, value) {
        pushUndoCheckpoint('setPerUnitServerLevel');
        var perUnit = state.cfdRoom.serverFacePerUnitLevel || {};
        if (!perUnit[uKey]) perUnit[uKey] = (state.cfdRoom.serverFaceRefinementLevel || [1, 1]).slice();
        perUnit[uKey][idx] = value;
        state.cfdRoom.serverFacePerUnitLevel = perUnit;
        refreshServerBucketUI();
        logOperation('setPerUnitServerLevel', t('log.refinement_strategy', { key: uKey + 'U', value: JSON.stringify(perUnit[uKey]) }), { uKey, idx, value }, 'modify');
    };

    window.runAutoDetectServerRefinement = function() {
        pushUndoCheckpoint('autoDetectServerRefinement');
        var changes = autoDetectServerRefinement();
        refreshServerBucketUI();
        if (changes.length) {
            var desc = changes.map(function(c) { return c.uHeight + 'U [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; }).join(', ');
            logOperation('autoDetectServerRefinement', t('log.auto_adjust_refinement', { changes: desc }), { changes: changes }, 'modify');
        }
    };

    /** Rebuild the per-unit bucket rows in the collapsible panel. */
    function refreshServerBucketUI() {
        var container = document.getElementById('srv-refine-buckets');
        if (!container) return;
        var buckets = scanServerUnitBuckets();
        var baseSize = state.cfdRoom.meshBaseSize || 0.4;
        var perUnit = state.cfdRoom.serverFacePerUnitLevel || {};
        var unified = state.cfdRoom.serverFaceRefinementLevel || [1, 1];
        var keys = Object.keys(buckets).sort(function(a, b) { return +a - +b; });

        if (!keys.length) {
            container.innerHTML = '<div style="color:var(--text-dim);font-style:italic;">No fan-servers in scene</div>';
            return;
        }

        container.innerHTML = keys.map(function(key, idx) {
            var bucket = buckets[key];
            var current = perUnit[key] || unified.slice();
            var safeLevel = computeMinSafeLevel(baseSize, bucket.patchHeight);
            var isSafe = current[0] >= safeLevel[0] && current[1] >= safeLevel[1];
            var prefix = idx < keys.length - 1 ? '├' : '└';
            var borderStyle = isSafe ? '1px solid rgba(100,200,100,0.3)' : '1px solid rgba(255,140,0,0.4)';
            var bgStyle = isSafe ? 'rgba(100,200,100,0.04)' : 'rgba(255,140,0,0.06)';
            var inputBorder = isSafe ? '' : 'border-color:rgba(255,140,0,0.6);';
            var icon = isSafe ? '<span style="color:#6c6;">✓</span>' : '<span style="color:#ffb347;">⚠</span>';
            var label = t('label.per_unit_refinement', { n: key, count: bucket.count });

            return '<div style="display:flex;align-items:center;gap:6px;padding:3px 4px;border:' + borderStyle + ';background:' + bgStyle + ';">'
                + '<span style="color:var(--text-dim);font-family:monospace;">' + prefix + '</span>'
                + '<span style="min-width:80px;">' + label + '</span>'
                + '<input type="number" min="0" max="8" value="' + current[0] + '" class="mesh-lvl-input" style="width:38px;' + inputBorder + '" oninput="window.setPerUnitServerLevel(\'' + key + '\',0,+this.value)">'
                + '<input type="number" min="0" max="8" value="' + current[1] + '" class="mesh-lvl-input" style="width:38px;' + inputBorder + '" oninput="window.setPerUnitServerLevel(\'' + key + '\',1,+this.value)">'
                + ' ' + icon
                + '</div>';
        }).join('');
    }
```

- [ ] **Step 3: Verify the UI renders**

Start the server, open the browser, load a project with racks (e.g., CMI-Guichou), switch to mesh tab. Click ▼ toggle → collapsible panel shows U-bucket rows.

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: collapsible per-unit server refinement UI in mesh tab"
```

---

### Task 4: Auto-Scan on Mesh Tab Switch + Warning Dialog Before Mesh Gen

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Hook auto-scan into switchLeftTab**

Replace the `switchLeftTab` function at line 15254:

```javascript
    window.switchLeftTab = function(tab) {
        // view-only — no undo checkpoint
        state.dctwinConfig.activeTab = tab;
        _applyLeftTabUI();
    };
```

With:

```javascript
    window.switchLeftTab = function(tab) {
        // view-only — no undo checkpoint
        state.dctwinConfig.activeTab = tab;
        _applyLeftTabUI();
        if (tab === 'mesh') {
            // Auto-scan server U-buckets and auto-detect safe levels on mesh tab entry
            var changes = autoDetectServerRefinement();
            refreshServerBucketUI();
            if (changes.length) {
                var desc = changes.map(function(c) { return c.uHeight + 'U [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; }).join(', ');
                logOperation('autoDetectServerRefinement', t('log.auto_adjust_refinement', { changes: desc }), { changes: changes }, 'modify');
            }
        }
    };
```

- [ ] **Step 2: Add the warning dialog function**

Insert after the `showSeedCollisionWarningDialog` function (line 21792):

```javascript
    function showServerRefinementWarningDialog(warnings) {
        return new Promise(function(resolve) {
            var existing = document.getElementById('server-refinement-warning-dialog');
            if (existing) existing.remove();
            var overlay = document.createElement('div');
            overlay.id = 'server-refinement-warning-dialog';
            overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:12000;display:flex;align-items:center;justify-content:center;padding:20px;';
            var dialog = document.createElement('div');
            dialog.style.cssText = 'width:min(760px,92vw);max-height:min(78vh,760px);overflow:hidden;display:flex;flex-direction:column;background:var(--file-browser-bg,#10151c);border:1px solid rgba(90,167,255,0.35);box-shadow:0 24px 60px rgba(0,0,0,0.45);clip-path:polygon(0 0,calc(100% - 8px) 0,100% 8px,100% 100%,8px 100%,0 calc(100% - 8px));';
            dialog.innerHTML = '<div style="padding:14px 18px;border-bottom:1px solid rgba(90,167,255,0.18);font-family:\'Orbitron\',monospace;font-size:1rem;color:#5aa7ff;letter-spacing:0.04em;">'
                + t('dialog.server_refinement_title') + '</div>'
                + '<div style="padding:14px 18px 0;color:var(--text-dim);font-size:0.84rem;line-height:1.7;">'
                + t('dialog.server_refinement_desc') + '</div>'
                + '<div style="padding:14px 18px;overflow:auto;display:flex;flex-direction:column;gap:8px;">'
                + warnings.map(function(w) {
                    return '<div style="padding:10px 12px;border:1px solid rgba(255,140,0,0.28);background:rgba(255,140,0,0.06);font-size:0.82rem;line-height:1.65;">'
                        + '<div style="color:#ffb347;font-family:\'Share Tech Mono\',monospace;margin-bottom:4px;">'
                        + w.uHeight + 'U (' + w.count + ')</div>'
                        + '<div>' + t('dialog.server_refinement_item', {
                            uHeight: w.uHeight,
                            count: w.count,
                            curMin: w.curMin,
                            curMax: w.curMax,
                            safeMin: w.safeMin,
                            safeMax: w.safeMax,
                            patchH: (w.patchHeight * 1000).toFixed(1),
                            cellSize: (w.cellSize * 1000).toFixed(1)
                        }) + '</div></div>';
                }).join('') + '</div>'
                + '<div style="display:flex;justify-content:flex-end;gap:10px;padding:14px 18px;border-top:1px solid rgba(90,167,255,0.18);">'
                + '<button class="btn" id="srv-refine-cancel-btn">' + t('btn.server_refinement_back') + '</button>'
                + '<button class="btn primary" id="srv-refine-ignore-btn">' + t('btn.server_refinement_ignore') + '</button>'
                + '</div>';
            overlay.appendChild(dialog);
            document.body.appendChild(overlay);
            dialog.querySelector('#srv-refine-cancel-btn').onclick = function() {
                overlay.remove();
                resolve(false);
            };
            dialog.querySelector('#srv-refine-ignore-btn').onclick = function() {
                overlay.remove();
                resolve(true);
            };
        });
    }
```

- [ ] **Step 3: Hook the warning check into runMeshGeneration**

In the `runMeshGeneration` function, after the seed collision check block (line 21839), insert:

```javascript
        // Server face refinement pre-check
        if (mode !== 'solve') {
            var srvRefineWarnings = collectServerRefinementWarnings();
            if (srvRefineWarnings.length) {
                var shouldContinueSrv = await showServerRefinementWarningDialog(srvRefineWarnings);
                if (!shouldContinueSrv) {
                    if (statusEl) statusEl.textContent = '';
                    if (runBtn) runBtn.disabled = false;
                    return;
                }
            }
        }
```

- [ ] **Step 4: Verify the full flow**

1. Load CMI-Guichou project → switch to mesh tab → should see auto-detection log
2. Click ▼ → expand panel → see 1U bucket with ⚠
3. Click "执行网格生成" → should see warning dialog

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: auto-detect on mesh tab switch + pre-mesh warning dialog for server refinement"
```

---

### Task 5: Export serverFacePerUnitLevel in cfd_export.json

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add serverFacePerUnitLevel to buildCfdExportJson**

In the `buildCfdExportJson` function, after line 22596 (`serverFaceRefinementLevel`):

```javascript
                serverFaceRefinementLevel: state.cfdRoom.serverFaceRefinementLevel || [1, 1],
```

Add:

```javascript
                serverFacePerUnitLevel: state.cfdRoom.serverFacePerUnitLevel || {},
```

- [ ] **Step 2: Ensure serverFacePerUnitLevel is saved/loaded with project files**

The project save/load serializes `state.cfdRoom` as JSON. Since `serverFacePerUnitLevel` is a plain object of arrays, it will be automatically serialized and restored. Verify by checking `_applyProjectData` loads `cfdRoom` properties.

The state restore in the mesh tab UI (lines 20130-20131) already sets the unified inputs. Add the per-unit restore after line 20131:

After `set('ms-srv-1', ...)`:

```javascript
        // Restore per-unit buckets if panel is visible
        refreshServerBucketUI();
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: export serverFacePerUnitLevel in cfd_export.json + restore on project load"
```

---

### Task 6: Backend Per-Solid Refinement Dispatch

**Files:**
- Modify: `dctwin/dctwin/runner/stlgen_runner.py`

- [ ] **Step 1: Build solid-to-spanUnits mapping**

In `_generate_snappy_hex_mesh_dict()`, after line 2065 (`rl_leakage`), add:

```python
        # Per-unit server face refinement levels
        per_unit_levels = mc.get("serverFacePerUnitLevel", {})

        # Build mapping: sanitized solid name → spanSlots (for per-unit refinement lookup)
        solid_to_span: Dict[str, int] = {}
        for rack in data.get("racks", {}).values():
            for srv in rack.get("servers", []):
                if srv.get("type") == "fanServer":
                    span = srv.get("spanSlots", 1)
                    solid_to_span[_sanitize(srv["inletSolidName"])] = span
                    solid_to_span[_sanitize(srv["outletSolidName"])] = span
```

- [ ] **Step 2: Use per-unit level when generating refinementSurfaces entries**

Replace lines 2298-2303:

```python
                elif sanitized in server_inlet_solids or sanitized in server_outlet_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_server[0]} {rl_server[1]});
                patchInfo {{ type patch; }}
            }}""")
```

With:

```python
                elif sanitized in server_inlet_solids or sanitized in server_outlet_solids:
                    # Per-unit refinement: look up this solid's spanSlots → bucket level
                    _span = solid_to_span.get(sanitized)
                    _rl = per_unit_levels.get(str(_span), rl_server) if _span else rl_server
                    if not isinstance(_rl, (list, tuple)) or len(_rl) < 2:
                        _rl = rl_server
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl[0]} {_rl[1]});
                patchInfo {{ type patch; }}
            }}""")
```

- [ ] **Step 3: Verify backward compatibility**

When `serverFacePerUnitLevel` is absent (old cfd_export.json), `per_unit_levels` is `{}`, `per_unit_levels.get(str(_span), rl_server)` returns `rl_server`. Behavior is identical to before.

- [ ] **Step 4: Commit**

```bash
git add dctwin/dctwin/runner/stlgen_runner.py
git commit -m "feat: per-unit server face refinement dispatch in snappyHexMeshDict"
```

---

### Task 7: End-to-End Verification

**Files:** None (testing only)

- [ ] **Step 1: Restart server**

```bash
fuser -k 8005/tcp 2>/dev/null; sleep 2
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir dctwin/case --port 8005 &
```

- [ ] **Step 2: Load CMI-Guichou and verify UI**

1. Open browser → load CMI-Guichou project
2. Switch to mesh tab → check Operation Log for "自动调整" message
3. Click ▼ on 服务器面 → verify bucket rows appear with correct U-heights and counts
4. Verify ⚠ icons on under-refined buckets, ✓ on sufficient ones
5. Modify a per-unit input → verify only that bucket changes
6. Modify the unified row → verify all buckets sync
7. Click "🔍 自动检测" → verify insufficient buckets get auto-filled

- [ ] **Step 3: Trigger mesh generation pre-check**

1. Set a bucket to [1,1] manually (below safe level)
2. Click "执行网格生成" → verify warning dialog appears
3. Click "返回修改" → verify mesh gen is cancelled
4. Click "执行网格生成" again → click "忽略并继续" → verify mesh gen proceeds

- [ ] **Step 4: Verify backend dispatch**

After mesh generation completes, check the generated snappyHexMeshDict:

```bash
grep -A2 "G8_S4320_56TC_inlet_patch\|level" dctwin/case/CMI-Guichou/Mesh/system/snappyHexMeshDict | head -20
```

Verify that 1U server solids have a higher refinement level than 13U server solids.
