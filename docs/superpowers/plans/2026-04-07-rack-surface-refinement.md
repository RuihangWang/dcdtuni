# Rack Surface Per-Unit Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the per-U-height refinement system from server-faces-only to all three rack surface types: server faces, blanking plates, and leakage zones — with a unified collapsible "Rack 表面加密" panel.

**Architecture:** Refactor the existing server-only collapsible panel into a 3-section panel. Generalize the scanning/auto-detect/warning functions to handle all three categories. Add new state fields and export keys. Backend dispatches per-unit levels for blanking plate solids and leakage zone solids.

**Tech Stack:** Vanilla JS (STL_generate.html), i18n JSON, Python (stlgen_runner.py)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | Chinese i18n keys |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | English i18n keys |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | All frontend: HTML restructure, new scanning functions, generalized UI/auto-detect/warning |
| `dctwin/dctwin/runner/stlgen_runner.py` | Per-unit dispatch for blanking plate and leakage zone solids |

---

### Task 1: Add i18n Keys

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Add Chinese i18n keys**

In `zh-CN.json`, add these keys near other `label.*` keys (after `label.leakage_zone`):

```json
  "label.rack_surface_refinement": "Rack 表面加密",
  "label.server_face_section": "🖥 服务器面",
  "label.blanking_plate": "🧱 盲板",
  "label.rack_leakage": "💨 漏风区域",
  "label.blanking_per_unit": "{n}U ({count}个)",
  "label.leakage_per_unit": "{n}U ({count}个)",
```

Replace existing `dialog.server_refinement_*` keys with `dialog.rack_refinement_*`:

```json
  "dialog.rack_refinement_title": "Rack 表面网格精度预警",
  "dialog.rack_refinement_desc": "以下 Rack 表面加密层级不足，snappyHexMesh 可能无法捕获这些表面，导致求解失败。",
```

Keep `dialog.server_refinement_item` as-is (it's still used for the per-item template, now shared across all 3 types).

- [ ] **Step 2: Add English i18n keys**

In `en-US.json`, add the same keys with English values:

```json
  "label.rack_surface_refinement": "Rack Surface Refinement",
  "label.server_face_section": "🖥 Server Face",
  "label.blanking_plate": "🧱 Blanking Plate",
  "label.rack_leakage": "💨 Leakage Zone",
  "label.blanking_per_unit": "{n}U ({count} units)",
  "label.leakage_per_unit": "{n}U ({count} units)",
  "dialog.rack_refinement_title": "Rack Surface Mesh Accuracy Warning",
  "dialog.rack_refinement_desc": "The following rack surface refinement levels are insufficient. snappyHexMesh may fail to capture these surfaces, causing solver errors.",
```

- [ ] **Step 3: Commit**

```bash
cd STLGeneratorForOpenFoam && git add web/i18n/zh-CN.json web/i18n/en-US.json
git commit -m "feat: add i18n keys for rack surface refinement (blanking plates + leakage zones)"
```

---

### Task 2: Frontend — HTML Restructure + JS Functions + Export

This is a large task because all parts of STL_generate.html are tightly coupled. The changes are:

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

#### Part A: HTML — Replace server-only panel with 3-section "Rack 表面加密" panel

- [ ] **Step 1: Replace lines 80-87 and remove lines 100-103**

Current lines 80-87 (server face row + collapsible panel inside the grid):
```html
                    <label title="fan-server / non-fan 设备面" ...>服务器面</label>
                    <input ... id="ms-srv-0" ... oninput="window.setUnifiedServerLevel(0,+this.value)">
                    <input ... id="ms-srv-1" ... oninput="window.setUnifiedServerLevel(1,+this.value)">
                    <button id="srv-refine-toggle" ...>▼</button>
                    <div id="srv-refine-panel" ...>
                        <div id="srv-refine-buckets" ...></div>
                        <button ... onclick="window.runAutoDetectServerRefinement()" ...>🔍 自动检测</button>
                    </div>
```

Replace with a single row that spans the full grid + a collapsible panel:
```html
                    <div style="grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;padding:2px 0;border-top:1px solid rgba(90,167,255,0.1);margin-top:2px;">
                        <span style="font-size:0.82rem;" data-i18n="label.rack_surface_refinement">Rack 表面加密</span>
                        <button id="rack-refine-toggle" onclick="window.toggleRackRefinePanel()" style="background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:0.9rem;padding:0 4px;" title="展开/折叠 Rack 表面加密设置">▼</button>
                    </div>
                    <div id="rack-refine-panel" style="display:none;grid-column:1/-1;padding:6px 8px;border:1px solid rgba(90,167,255,0.15);background:rgba(90,167,255,0.03);">
                        <div id="rack-refine-content" style="display:flex;flex-direction:column;gap:8px;font-size:0.8rem;"></div>
                        <button class="btn" onclick="window.runAutoDetectRackRefinement()" style="margin-top:6px;padding:4px 10px;font-size:0.78rem;" data-i18n="btn.auto_detect_refinement">🔍 自动检测</button>
                    </div>
```

Also remove lines 100-103 (the "漏风区域" row — 4 elements: label + 3 inputs):
```html
                    <label ... data-i18n="label.leakage_zone" ...>漏风区域</label>
                    <input ... id="ms-leakage-0" ...>
                    <input ... id="ms-leakage-1" ...>
                    <input ... id="ms-feat-leakage" ...>
```

#### Part B: JS — Add blanking plate and leakage scanning functions

- [ ] **Step 2: Add `scanBlankingPlateUnitBuckets()` and `scanLeakageUnitBuckets()` after `scanServerUnitBuckets()` (line ~20229)**

Insert after `scanServerUnitBuckets`:

```javascript
    function scanBlankingPlateUnitBuckets() {
        var buckets = {};
        (state.models || []).forEach(function(model) {
            if (model.kind !== 'rack' || !model.rack) return;
            var unitH = model.rack.unitHeight || RACK_DEFAULT_UNIT_HEIGHT;
            ['front', 'back'].forEach(function(side) {
                var ranges = getRackMergedEmptySlotRanges(model, side);
                ranges.forEach(function(range) {
                    var span = range.endUnit - range.startUnit + 1;
                    var key = String(span);
                    if (!buckets[key]) buckets[key] = { count: 0, patchHeight: span * unitH };
                    buckets[key].count++;
                });
            });
        });
        return buckets;
    }

    function scanLeakageUnitBuckets() {
        var buckets = {};
        (state.models || []).forEach(function(model) {
            if (model.kind !== 'rack' || !model.rack) return;
            var unitH = model.rack.unitHeight || RACK_DEFAULT_UNIT_HEIGHT;
            var groups = getRackLeakageGroups(model);
            groups.forEach(function(grp) {
                var span = grp.endUnit - grp.startUnit + 1;
                var key = String(span);
                if (!buckets[key]) buckets[key] = { count: 0, patchHeight: span * unitH };
                buckets[key].count++;
            });
        });
        return buckets;
    }
```

#### Part C: JS — Generalize auto-detect and warning collection to 3 categories

- [ ] **Step 3: Replace `autoDetectServerRefinement()` with `autoDetectRackRefinement()` (line ~20240)**

Replace `autoDetectServerRefinement` with a generalized function that handles all 3 categories:

```javascript
    function _autoDetectCategory(scanFn, unifiedKey, perUnitKey, defaultUnified) {
        var buckets = scanFn();
        var baseSize = state.cfdRoom.meshBaseSize || 0.4;
        var perUnit = state.cfdRoom[perUnitKey] || {};
        var unified = state.cfdRoom[unifiedKey] || defaultUnified;
        var changes = [];

        Object.keys(buckets).sort(function(a, b) { return +a - +b; }).forEach(function(key) {
            var bucket = buckets[key];
            var safeLevel = computeMinSafeLevel(baseSize, bucket.patchHeight);
            var current = perUnit[key] || unified.slice();
            if (current[0] < safeLevel[0] || current[1] < safeLevel[1]) {
                var newLevel = [Math.max(current[0], safeLevel[0]), Math.max(current[1], safeLevel[1])];
                changes.push({
                    uHeight: +key, count: bucket.count, patchHeight: bucket.patchHeight,
                    oldLevel: current.slice(), newLevel: newLevel, safeLevel: safeLevel
                });
                perUnit[key] = newLevel;
            } else {
                if (!perUnit[key]) perUnit[key] = current;
            }
        });
        Object.keys(perUnit).forEach(function(key) { if (!buckets[key]) delete perUnit[key]; });
        state.cfdRoom[perUnitKey] = perUnit;
        return changes;
    }

    function autoDetectRackRefinement() {
        var serverChanges = _autoDetectCategory(scanServerUnitBuckets, 'serverFaceRefinementLevel', 'serverFacePerUnitLevel', [1,1]);
        var blankingChanges = _autoDetectCategory(scanBlankingPlateUnitBuckets, 'blankingPlateRefinementLevel', 'blankingPlatePerUnitLevel', [1,1]);
        var leakageChanges = _autoDetectCategory(scanLeakageUnitBuckets, 'rackLeakageRefinementLevel', 'rackLeakagePerUnitLevel', [1,2]);
        return { server: serverChanges, blanking: blankingChanges, leakage: leakageChanges };
    }
```

- [ ] **Step 4: Replace `collectServerRefinementWarnings()` with `collectRackSurfaceRefinementWarnings()` (line ~20275)**

```javascript
    function _collectCategoryWarnings(scanFn, unifiedKey, perUnitKey, defaultUnified, type) {
        var buckets = scanFn();
        var baseSize = state.cfdRoom.meshBaseSize || 0.4;
        var perUnit = state.cfdRoom[perUnitKey] || {};
        var unified = state.cfdRoom[unifiedKey] || defaultUnified;
        var warnings = [];
        Object.keys(buckets).sort(function(a, b) { return +a - +b; }).forEach(function(key) {
            var bucket = buckets[key];
            var safeLevel = computeMinSafeLevel(baseSize, bucket.patchHeight);
            var current = perUnit[key] || unified.slice();
            if (current[0] < safeLevel[0] || current[1] < safeLevel[1]) {
                var cellSize = baseSize / Math.pow(2, current[1]);
                warnings.push({
                    type: type, uHeight: +key, count: bucket.count, patchHeight: bucket.patchHeight,
                    curMin: current[0], curMax: current[1],
                    safeMin: safeLevel[0], safeMax: safeLevel[1], cellSize: cellSize
                });
            }
        });
        return warnings;
    }

    function collectRackSurfaceRefinementWarnings() {
        return [].concat(
            _collectCategoryWarnings(scanServerUnitBuckets, 'serverFaceRefinementLevel', 'serverFacePerUnitLevel', [1,1], 'server'),
            _collectCategoryWarnings(scanBlankingPlateUnitBuckets, 'blankingPlateRefinementLevel', 'blankingPlatePerUnitLevel', [1,1], 'blanking'),
            _collectCategoryWarnings(scanLeakageUnitBuckets, 'rackLeakageRefinementLevel', 'rackLeakagePerUnitLevel', [1,2], 'leakage')
        );
    }
```

#### Part D: JS — Generalize UI toggle, unified setter, per-unit setter, auto-detect button, and panel renderer

- [ ] **Step 5: Replace existing toggle/setter/renderer functions (lines ~20298-20374)**

Replace the 5 existing server-specific functions (`toggleServerRefinePanel`, `setUnifiedServerLevel`, `setPerUnitServerLevel`, `runAutoDetectServerRefinement`, `refreshServerBucketUI`) with generalized versions:

```javascript
    window.toggleRackRefinePanel = function() {
        var panel = document.getElementById('rack-refine-panel');
        var btn = document.getElementById('rack-refine-toggle');
        if (!panel || !btn) return;
        var visible = panel.style.display !== 'none';
        panel.style.display = visible ? 'none' : '';
        btn.textContent = visible ? '\u25BC' : '\u25B2';
        if (!visible) refreshRackRefinePanelUI();
    };

    window.setUnifiedRackCategory = function(unifiedKey, perUnitKey, idx, value) {
        pushUndoCheckpoint('setUnifiedRackCategory');
        if (!Array.isArray(state.cfdRoom[unifiedKey])) state.cfdRoom[unifiedKey] = [1, 1];
        state.cfdRoom[unifiedKey][idx] = value;
        var unified = state.cfdRoom[unifiedKey];
        var perUnit = state.cfdRoom[perUnitKey] || {};
        Object.keys(perUnit).forEach(function(key) { perUnit[key] = unified.slice(); });
        state.cfdRoom[perUnitKey] = perUnit;
        refreshRackRefinePanelUI();
        logOperation('setUnifiedRackCategory', t('log.refinement_strategy', { key: unifiedKey, value: JSON.stringify(unified) }), { unifiedKey, idx, value }, 'modify');
    };

    window.setPerUnitRackCategory = function(perUnitKey, unifiedKey, uKey, idx, value) {
        pushUndoCheckpoint('setPerUnitRackCategory');
        var perUnit = state.cfdRoom[perUnitKey] || {};
        if (!perUnit[uKey]) perUnit[uKey] = (state.cfdRoom[unifiedKey] || [1, 1]).slice();
        perUnit[uKey][idx] = value;
        state.cfdRoom[perUnitKey] = perUnit;
        refreshRackRefinePanelUI();
        logOperation('setPerUnitRackCategory', t('log.refinement_strategy', { key: uKey + 'U', value: JSON.stringify(perUnit[uKey]) }), { perUnitKey, uKey, idx, value }, 'modify');
    };

    window.runAutoDetectRackRefinement = function() {
        pushUndoCheckpoint('autoDetectRackRefinement');
        var result = autoDetectRackRefinement();
        refreshRackRefinePanelUI();
        var allChanges = [].concat(
            result.server.map(function(c) { return '\u{1F5A5}' + c.uHeight + 'U [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; }),
            result.blanking.map(function(c) { return '\u{1F9F1}' + c.uHeight + 'U [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; }),
            result.leakage.map(function(c) { return '\u{1F4A8}' + c.uHeight + 'U [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; })
        );
        if (allChanges.length) {
            logOperation('autoDetectRackRefinement', t('log.auto_adjust_refinement', { changes: allChanges.join(', ') }), { result: result }, 'modify');
        }
    };

    function _renderCategorySection(title, scanFn, unifiedKey, perUnitKey, defaultUnified, labelKey, setUnifiedCall, setPerUnitCall) {
        var buckets = scanFn();
        var baseSize = state.cfdRoom.meshBaseSize || 0.4;
        var perUnit = state.cfdRoom[perUnitKey] || {};
        var unified = state.cfdRoom[unifiedKey] || defaultUnified;
        var keys = Object.keys(buckets).sort(function(a, b) { return +a - +b; });

        var html = '<div style="display:flex;align-items:center;gap:6px;padding:2px 0;">'
            + '<span style="font-weight:600;">' + title + '</span>'
            + '<input type="number" min="0" max="8" value="' + unified[0] + '" class="mesh-lvl-input" style="width:38px;" oninput="' + setUnifiedCall.replace('{IDX}', '0') + '">'
            + '<input type="number" min="0" max="8" value="' + unified[1] + '" class="mesh-lvl-input" style="width:38px;" oninput="' + setUnifiedCall.replace('{IDX}', '1') + '">'
            + '</div>';

        if (!keys.length) {
            html += '<div style="color:var(--text-dim);font-style:italic;padding-left:16px;">—</div>';
            return html;
        }

        html += keys.map(function(key, idx) {
            var bucket = buckets[key];
            var current = perUnit[key] || unified.slice();
            var safeLevel = computeMinSafeLevel(baseSize, bucket.patchHeight);
            var isSafe = current[0] >= safeLevel[0] && current[1] >= safeLevel[1];
            var prefix = idx < keys.length - 1 ? '\u251C' : '\u2514';
            var borderStyle = isSafe ? '1px solid rgba(100,200,100,0.3)' : '1px solid rgba(255,140,0,0.4)';
            var bgStyle = isSafe ? 'rgba(100,200,100,0.04)' : 'rgba(255,140,0,0.06)';
            var inputBorder = isSafe ? '' : 'border-color:rgba(255,140,0,0.6);';
            var icon = isSafe ? '<span style="color:#6c6;">\u2713</span>' : '<span style="color:#ffb347;">\u26A0</span>';
            var label = t(labelKey, { n: key, count: bucket.count });
            var perUnitCall0 = setPerUnitCall.replace('{KEY}', key).replace('{IDX}', '0');
            var perUnitCall1 = setPerUnitCall.replace('{KEY}', key).replace('{IDX}', '1');

            return '<div style="display:flex;align-items:center;gap:6px;padding:3px 4px 3px 16px;border:' + borderStyle + ';background:' + bgStyle + ';">'
                + '<span style="color:var(--text-dim);font-family:monospace;">' + prefix + '</span>'
                + '<span style="min-width:80px;">' + label + '</span>'
                + '<input type="number" min="0" max="8" value="' + current[0] + '" class="mesh-lvl-input" style="width:38px;' + inputBorder + '" oninput="' + perUnitCall0 + '">'
                + '<input type="number" min="0" max="8" value="' + current[1] + '" class="mesh-lvl-input" style="width:38px;' + inputBorder + '" oninput="' + perUnitCall1 + '">'
                + ' ' + icon
                + '</div>';
        }).join('');

        return html;
    }

    function refreshRackRefinePanelUI() {
        var container = document.getElementById('rack-refine-content');
        if (!container) return;
        container.innerHTML =
            _renderCategorySection(
                t('label.server_face_section'), scanServerUnitBuckets,
                'serverFaceRefinementLevel', 'serverFacePerUnitLevel', [1,1],
                'label.per_unit_refinement',
                "window.setUnifiedRackCategory('serverFaceRefinementLevel','serverFacePerUnitLevel',{IDX},+this.value)",
                "window.setPerUnitRackCategory('serverFacePerUnitLevel','serverFaceRefinementLevel','{KEY}',{IDX},+this.value)"
            )
            + _renderCategorySection(
                t('label.blanking_plate'), scanBlankingPlateUnitBuckets,
                'blankingPlateRefinementLevel', 'blankingPlatePerUnitLevel', [1,1],
                'label.blanking_per_unit',
                "window.setUnifiedRackCategory('blankingPlateRefinementLevel','blankingPlatePerUnitLevel',{IDX},+this.value)",
                "window.setPerUnitRackCategory('blankingPlatePerUnitLevel','blankingPlateRefinementLevel','{KEY}',{IDX},+this.value)"
            )
            + _renderCategorySection(
                t('label.rack_leakage'), scanLeakageUnitBuckets,
                'rackLeakageRefinementLevel', 'rackLeakagePerUnitLevel', [1,2],
                'label.leakage_per_unit',
                "window.setUnifiedRackCategory('rackLeakageRefinementLevel','rackLeakagePerUnitLevel',{IDX},+this.value)",
                "window.setPerUnitRackCategory('rackLeakagePerUnitLevel','rackLeakageRefinementLevel','{KEY}',{IDX},+this.value)"
            );
    }
```

#### Part E: JS — Update switchLeftTab, warning dialog, and runMeshGeneration

- [ ] **Step 6: Update `switchLeftTab` (line ~15258)**

Replace the server-only auto-detect call:
```javascript
        if (tab === 'mesh') {
            var changes = autoDetectServerRefinement();
            refreshServerBucketUI();
            if (changes.length) {
                var desc = changes.map(function(c) { return c.uHeight + 'U [' + c.oldLevel.join(',') + ']\u2192[' + c.newLevel.join(',') + ']'; }).join(', ');
                logOperation('autoDetectServerRefinement', t('log.auto_adjust_refinement', { changes: desc }), { changes: changes }, 'modify');
            }
        }
```

With:
```javascript
        if (tab === 'mesh') {
            window.runAutoDetectRackRefinement();
        }
```

(The `runAutoDetectRackRefinement` function already handles all 3 categories, refreshes UI, and logs.)

- [ ] **Step 7: Update warning dialog function `showServerRefinementWarningDialog` → `showRackRefinementWarningDialog` (line ~21970)**

Update the dialog to use `dialog.rack_refinement_title` and `dialog.rack_refinement_desc` instead of `dialog.server_refinement_title`/`desc`. Add a type emoji prefix to each warning item:

In the warnings map function, add a type prefix:
```javascript
var typePrefix = {'server': '\u{1F5A5} ', 'blanking': '\u{1F9F1} ', 'leakage': '\u{1F4A8} '}[w.type] || '';
```

And prepend it to the item header:
```javascript
+ typePrefix + w.uHeight + 'U (' + w.count + ')</div>'
```

Also rename the dialog ID from `server-refinement-warning-dialog` to `rack-refinement-warning-dialog` and button IDs similarly.

- [ ] **Step 8: Update `runMeshGeneration` (line ~22056)**

Replace:
```javascript
            var srvRefineWarnings = collectServerRefinementWarnings();
            if (srvRefineWarnings.length) {
                var shouldContinueSrv = await showServerRefinementWarningDialog(srvRefineWarnings);
```

With:
```javascript
            var rackRefineWarnings = collectRackSurfaceRefinementWarnings();
            if (rackRefineWarnings.length) {
                var shouldContinueSrv = await showRackRefinementWarningDialog(rackRefineWarnings);
```

#### Part F: JS — Update export and state restore

- [ ] **Step 9: Add new fields to `buildCfdExportJson` (line ~22823)**

After `serverFacePerUnitLevel`:
```javascript
                blankingPlateRefinementLevel: state.cfdRoom.blankingPlateRefinementLevel || [1, 1],
                blankingPlatePerUnitLevel: state.cfdRoom.blankingPlatePerUnitLevel || {},
                rackLeakageRefinementLevel: state.cfdRoom.rackLeakageRefinementLevel || [1, 2],
                rackLeakagePerUnitLevel: state.cfdRoom.rackLeakagePerUnitLevel || {},
```

- [ ] **Step 10: Update state restore (line ~20142-20152)**

Remove the server-face-only restore lines:
```javascript
        set('ms-srv-0', ...);
        set('ms-srv-1', ...);
        refreshServerBucketUI();
```

And the leakage restore lines:
```javascript
        set('ms-leakage-0', ...);
        set('ms-leakage-1', ...);
```

Replace with:
```javascript
        refreshRackRefinePanelUI();
```

- [ ] **Step 11: Commit**

```bash
cd STLGeneratorForOpenFoam && git add web/STL_generate.html
git commit -m "feat: 3-section Rack surface refinement panel (server + blanking plate + leakage)"
```

---

### Task 3: Backend Per-Solid Refinement Dispatch

**Files:**
- Modify: `dctwin/dctwin/runner/stlgen_runner.py`

- [ ] **Step 1: Add blanking plate and leakage per-unit level reading (after line ~2068)**

After the existing `per_unit_levels` line, add:
```python
        # Per-unit blanking plate refinement levels
        per_unit_blanking = mc.get("blankingPlatePerUnitLevel", {})
        rl_blanking_unified = mc.get("blankingPlateRefinementLevel", rl_rack)

        # Per-unit rack leakage zone refinement levels
        per_unit_leakage = mc.get("rackLeakagePerUnitLevel", {})
        rl_leakage_unified = mc.get("rackLeakageRefinementLevel", rl_leakage)
```

- [ ] **Step 2: Build solid → span mapping for blanking plates (after server solid collection, ~line 2114)**

After the `rack_io_solids` collection loop, add:
```python
        # Build mapping: blanking plate solid name → spanUnits (for per-unit refinement)
        solid_to_blanking_span: Dict[str, int] = {}
        _u_range_pat = re.compile(r'_u(\d+)_u(\d+)')
        for solid_name in (rack_io_solids | rack_wall_baffle_solids):
            m = _u_range_pat.search(solid_name)
            if m:
                solid_to_blanking_span[solid_name] = int(m.group(2)) - int(m.group(1)) + 1

        # Build mapping: leakage zone solid name → spanUnits
        solid_to_leakage_span: Dict[str, int] = {}
        for rack in data.get("racks", {}).values():
            for grp in rack.get("leakage", {}).get("groups", []):
                san = _sanitize(grp["stlSolidName"])
                solid_to_leakage_span[san] = grp.get("endUnit", 0) - grp.get("startUnit", 0) + 1
```

- [ ] **Step 3: Update rack_io_solids refinement dispatch (line ~2323)**

Replace:
```python
                elif sanitized in rack_io_solids:
                    # Structural inlet/outlet (safe margins, frames, U-ranges)
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_rack[0]} {rl_rack[1]});
                patchInfo {{ type patch; }}
            }}""")
```

With:
```python
                elif sanitized in rack_io_solids:
                    # Blanking plate (wall patch): per-unit refinement
                    _span = solid_to_blanking_span.get(sanitized)
                    _rl = per_unit_blanking.get(str(_span), rl_blanking_unified) if _span else rl_rack
                    if not isinstance(_rl, (list, tuple)) or len(_rl) < 2:
                        _rl = rl_rack
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl[0]} {_rl[1]});
                patchInfo {{ type patch; }}
            }}""")
```

- [ ] **Step 4: Update rack_wall_baffle_solids refinement dispatch (line ~2336)**

Replace:
```python
                elif sanitized in wall_baffle_solids:
                    # Wall baffle: faceType internal + faceZone → createBaffles cyclic → post-process to wall.
                    # Cells kept on both sides. Creates master+slave wall patches.
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_baffle[0]} {rl_baffle[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
```

With:
```python
                elif sanitized in rack_wall_baffle_solids:
                    # Rack wall baffle (blanking plate): per-unit refinement, decoupled from standalone baffles
                    _span = solid_to_blanking_span.get(sanitized)
                    _rl = per_unit_blanking.get(str(_span), rl_blanking_unified) if _span else rl_baffle
                    if not isinstance(_rl, (list, tuple)) or len(_rl) < 2:
                        _rl = rl_baffle
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl[0]} {_rl[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
```

**Important:** Also check that the condition variable name matches — the existing code uses `wall_baffle_solids` which is a **superset** of `rack_wall_baffle_solids` (it may include standalone baffles). Verify the condition at this line checks `rack_wall_baffle_solids` specifically, and that standalone baffles fall through to a separate `elif` that uses `rl_baffle`. Read the actual code to confirm the variable name before editing.

- [ ] **Step 5: Update leakage zone refinement dispatch (line ~2260)**

Replace:
```python
                san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_leakage[0]} {rl_leakage[1]});
        faceType internal;
        cellZone {san};
        cellZoneInside inside;
    }}""")
```

With:
```python
                san = _sanitize(solids[0])
                _span = solid_to_leakage_span.get(san)
                _rl = per_unit_leakage.get(str(_span), rl_leakage_unified) if _span else rl_leakage
                if not isinstance(_rl, (list, tuple)) or len(_rl) < 2:
                    _rl = rl_leakage
                refine_lines.append(f"""    {stem}
    {{
        level ({_rl[0]} {_rl[1]});
        faceType internal;
        cellZone {san};
        cellZoneInside inside;
    }}""")
```

- [ ] **Step 6: Verify import succeeds**

```bash
conda run -n dcdtuni python -c "from dctwin.runner.stlgen_runner import STLGenRunner; print('OK')"
```

- [ ] **Step 7: Commit**

```bash
git add dctwin/dctwin/runner/stlgen_runner.py
git commit -m "feat: per-unit dispatch for blanking plate and leakage zone refinement in snappyHexMeshDict"
```

---

### Task 4: End-to-End Verification

**Files:** None (testing only)

- [ ] **Step 1: Restart server**

```bash
fuser -k 8005/tcp 2>/dev/null; sleep 2
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir dctwin/case --port 8005 &
```

- [ ] **Step 2: Load a project with all 3 surface types and verify UI**

Load CMI-Guichou or similar project with racks that have servers, blanking plates, and leakage zones.

1. Switch to mesh tab → check Operation Log for auto-detection messages covering all 3 categories
2. Click ▼ on "Rack 表面加密" → verify 3 sections appear: 🖥 服务器面, 🧱 盲板, 💨 漏风区域
3. Verify each section shows correct U-height buckets with ⚠/✓ icons
4. Modify a unified value → verify all that category's buckets sync
5. Modify a per-unit value → verify only that bucket changes
6. Click "🔍 自动检测" → verify all 3 categories get auto-filled

- [ ] **Step 3: Trigger mesh generation pre-check**

1. Set a blanking plate bucket to [1,1] manually
2. Click "执行网格生成" → verify warning dialog shows all 3 category types
3. Click "返回修改" → verify cancelled

- [ ] **Step 4: Verify backend dispatch**

After mesh gen, check snappyHexMeshDict:
```bash
grep -B1 "level (" dctwin/case/CMI-Guichou/Mesh/system/snappyHexMeshDict | head -30
```

Verify blanking plate solids and leakage zone solids have different refinement levels based on their U-height.
