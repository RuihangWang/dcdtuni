# Mesh Preview Enhancement & Solver Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add hierarchical mesh preview patch list, real-time residual convergence charts, and server temperature monitoring to the STLGenerator web app.

**Architecture:** Frontend-only changes for mesh preview (A) and residual parsing (B1). Backend Jinja2 template + CaseRunner changes for temperature monitoring (B2). All charts rendered via Canvas 2D API. Log parsing intercepts SSE events in the existing `_streamDctwinLog` function.

**Tech Stack:** HTML/JS (Three.js, Canvas 2D), Python HTTP server, OpenFOAM Jinja2 templates, SSE log streaming

---

### Task 1: Mesh Preview — Hierarchical Patch Tree & Button Rename

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Rename the mesh preview button**

In the mesh settings panel (around line 586), find the button with text "🔲 预览网格" and change it to "🔲 显示网格":

```html
<!-- Find this (around line 586): -->
<button onclick="window.mpLoadMesh()" style="font-size:0.72rem; padding:6px; ...">
    🔲 预览网格</button>

<!-- Change to: -->
<button onclick="window.mpLoadMesh()" style="font-size:0.72rem; padding:6px; ...">
    🔲 显示网格</button>
```

- [ ] **Step 2: Add Read Zones checkbox to mesh preview right panel HTML**

In the `mesh-preview-right` div (around line 836), add a disabled Read Zones checkbox between the header and opacity slider:

```html
<div id="mesh-preview-right" style="display:none; flex-direction:column; overflow-y:auto; flex:1; padding:0;">
    <div class="header">🔲 网格预览</div>
    <div style="padding:8px; display:flex; gap:4px;">
        <button onclick="window.mpLoadMesh()" style="font-size:11px; padding:3px 8px; background:#1a3a2a; border:1px solid #2a5a3a; color:#6f6; cursor:pointer;">加载网格</button>
        <button onclick="window.mpClose()" style="font-size:11px; padding:3px 8px; background:#3a1a1a; border:1px solid #5a2a2a; color:#f66; cursor:pointer;">关闭预览</button>
    </div>
    <!-- NEW: Read Zones (disabled for mesh preview) -->
    <div style="padding:4px 8px; border-bottom:1px solid #333;">
        <label style="font-size:11px; color:#666; cursor:not-allowed;" title="需要先完成求解才能加载 Zones">
            <input type="checkbox" id="mp-read-zones" disabled> Read Zones
        </label>
    </div>
    <div style="padding:0 8px 8px;">
        <!-- existing opacity slider -->
```

- [ ] **Step 3: Rewrite `mpBuildPatchList` as hierarchical tree**

Replace the existing `mpBuildPatchList` function (around line 12796) with a collapsible tree matching the post-processing style:

```javascript
window.mpBuildPatchList = function(patches) {
    var list = document.getElementById('mp-patch-list');
    list.innerHTML = '';

    // Group: boundary (collapsible)
    var boundaryPatches = [];
    for (var i = 0; i < patches.length; i++) {
        boundaryPatches.push(patches[i]);
    }

    if (boundaryPatches.length === 0) return;

    // Collapsible boundary header
    var header = document.createElement('div');
    header.style.cssText = 'font-size:11px; padding:4px 0; color:#8be9fd; cursor:pointer; user-select:none; display:flex; align-items:center; gap:4px;';
    header.innerHTML = '<span class="mp-tree-arrow" style="display:inline-block; width:10px; font-size:8px; transition:transform 0.15s;">▼</span>' +
        '<span>boundary (' + boundaryPatches.length + ')</span>';
    list.appendChild(header);

    var container = document.createElement('div');
    container.style.cssText = 'padding-left:14px;';
    list.appendChild(container);

    // Toggle collapse
    header.addEventListener('click', function() {
        var arrow = header.querySelector('.mp-tree-arrow');
        if (container.style.display === 'none') {
            container.style.display = '';
            arrow.style.transform = '';
        } else {
            container.style.display = 'none';
            arrow.style.transform = 'rotate(-90deg)';
        }
    });

    // Add each patch as a checkbox row
    for (var i = 0; i < boundaryPatches.length; i++) {
        var p = boundaryPatches[i];
        var row = document.createElement('div');
        row.style.cssText = 'font-size:10px; padding:1px 0; color:#ccc;';
        row.innerHTML = '<label><input type="checkbox" class="mp-cb" data-patch="' + p.name + '"' +
            (p.defaultVisible ? ' checked' : '') + ' onchange="window.mpUpdateVisibility()"> ' +
            p.name + ' <span style="color:#888;">(' + p.nTriangles + ')</span></label>';
        container.appendChild(row);
    }
};
```

- [ ] **Step 4: Verify syntax**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && node -e "
var fs = require('fs');
var html = fs.readFileSync('STLGeneratorForOpenFoam/web/STL_generate.html', 'utf8');
var lines = html.split('\n');
var start = lines.findIndex(function(l){ return l.indexOf('window.mpBuildPatchList = function') !== -1; });
var depth = 0, end = start;
for (var i = start; i < lines.length; i++) {
    for (var j = 0; j < lines[i].length; j++) {
        if (lines[i][j] === '{') depth++;
        if (lines[i][j] === '}') depth--;
    }
    if (depth === 0 && i > start) { end = i; break; }
}
try { new Function(lines.slice(start, end+1).join('\n')); console.log('mpBuildPatchList: OK'); } catch(e) { console.log('ERROR: ' + e.message); process.exit(1); }
"
```

Expected: `mpBuildPatchList: OK`

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(A): mesh preview hierarchical patch tree, Read Zones, button rename"
```

---

### Task 2: Solver Tab Right Panel — Monitoring Configuration UI

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add solver monitoring right panel HTML**

In the right panel HTML (around line 858, after the `mesh-preview-right` div closes), add a new div for solver monitoring:

```html
<!-- Solver monitoring panel (right) -->
<div id="solver-monitoring-right" style="display:none; flex-direction:column; overflow-y:auto; flex:1; padding:0;">
    <div class="header">🔬 求解监控</div>
    <div style="padding:8px; border-bottom:1px solid #333;">
        <label style="font-size:11px; color:#ccc;">
            <input type="checkbox" id="sm-residual-cb" checked> 监控残差收敛曲线
        </label>
    </div>
    <div style="padding:8px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <label style="font-size:11px; color:#aaa;">📊 服务器温度监控</label>
            <div style="display:flex; gap:2px;">
                <button onclick="window.smToggleAllTemp(true)" style="font-size:10px; padding:2px 6px; background:#1a2a3a; border:1px solid #2a4a5a; color:#aaa; cursor:pointer;">全选</button>
                <button onclick="window.smToggleAllTemp(false)" style="font-size:10px; padding:2px 6px; background:#1a2a3a; border:1px solid #2a4a5a; color:#aaa; cursor:pointer;">全不选</button>
            </div>
        </div>
        <div id="sm-server-list" style="max-height:500px; overflow-y:auto;">
            <div style="font-size:10px; color:#666; padding:4px;">加载中...</div>
        </div>
    </div>
</div>
```

- [ ] **Step 2: Update `_applyLeftTabUI` to show solver monitoring panel**

In `_applyLeftTabUI` (around line 11445), add logic for the solver tab to show `solver-monitoring-right` and hide the property inspector:

```javascript
// After the existing postprocess right panel logic, add solver monitoring panel logic:
const smRight = document.getElementById('solver-monitoring-right');
if (isSolver) {
    if (propTitle) propTitle.style.display = 'none';
    if (propContent) propContent.style.display = 'none';
    if (smRight) smRight.style.display = 'flex';
    // Also hide mesh preview if it was open
    const mpRight = document.getElementById('mesh-preview-right');
    if (mpRight) mpRight.style.display = 'none';
} else {
    if (smRight) smRight.style.display = 'none';
}
```

Also ensure the existing `isPostprocess` block hides `smRight` too, and that mesh preview hides `smRight`. The logic should be:
- Solver tab: hide prop-title, prop-content, ppRight, mpRight. Show smRight.
- Postprocess tab: hide prop-title, prop-content, smRight, mpRight. Show ppRight (if initialized).
- Mesh tab: hide smRight, ppRight. Show prop-title, prop-content (and mpRight if active).
- Geometry tab: hide smRight, ppRight, mpRight. Show prop-title, prop-content.

- [ ] **Step 3: Add server patches API endpoint to server.py**

In `STLGeneratorForOpenFoam/server/server.py`, add a new handler function and route:

```python
def handle_server_patches(handler: http.server.BaseHTTPRequestHandler) -> None:
    """GET /api/server-patches — List server inlet/outlet patches from mesh."""
    import re
    with CASE_DIR_LOCK:
        cd = CASE_DIR
    if cd is None:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    boundary_path = cd / "Mesh" / "constant" / "polyMesh" / "boundary"
    if not boundary_path.exists():
        _send_json(handler, 404, {"error": "No mesh found. Run mesh generation first."})
        return
    try:
        text = boundary_path.read_text(encoding="utf-8")
        # Extract patch names (lines with just a name before an opening brace)
        patch_names = re.findall(r'^\s+(\S+)\s*$', text, re.MULTILINE)
        # Filter for inlet/outlet patches
        results = []
        for name in patch_names:
            m = re.search(r'(.+?)_(?:inlet|outlet)(?:_patch|_safe_\w+)?$', name, re.IGNORECASE)
            if not m:
                continue
            is_inlet = '_inlet' in name.lower()
            # Extract a short display ID from the patch name
            # e.g. "A00_PTN3900_inlet_patch" -> "A00_PTN3900"
            parts = name.split('_inlet' if is_inlet else '_outlet')
            short_id = parts[0] if parts else name
            results.append({
                "patch": name,
                "shortId": short_id,
                "type": "inlet" if is_inlet else "outlet",
            })
        _send_json(handler, 200, {"patches": results})
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})
```

Register the route in the `do_GET` handler (find the route dispatch block and add):
```python
elif parsed.path == "/api/server-patches":
    handle_server_patches(self)
```

- [ ] **Step 4: Add frontend JS to populate server patch list**

Add a function `smLoadServerPatches()` that fetches `/api/server-patches` and builds the checkbox list in `sm-server-list`:

```javascript
window.smLoadServerPatches = async function() {
    var listEl = document.getElementById('sm-server-list');
    if (!listEl) return;
    try {
        var resp = await fetch('/api/server-patches');
        var data = await resp.json();
        if (data.error) {
            listEl.innerHTML = '<div style="font-size:10px; color:#f66; padding:4px;">' + data.error + '</div>';
            return;
        }
        var patches = data.patches || [];
        if (patches.length === 0) {
            listEl.innerHTML = '<div style="font-size:10px; color:#666; padding:4px;">未检测到服务器边界</div>';
            return;
        }
        listEl.innerHTML = '';
        for (var i = 0; i < patches.length; i++) {
            var p = patches[i];
            var row = document.createElement('div');
            row.style.cssText = 'font-size:10px; padding:1px 0; color:#ccc;';
            row.innerHTML = '<label><input type="checkbox" class="sm-temp-cb" data-patch="' +
                p.patch + '" data-short="' + p.shortId + '" data-type="' + p.type + '"> ' +
                p.shortId + ' (' + p.type + ')</label>';
            listEl.appendChild(row);
        }
    } catch (e) {
        listEl.innerHTML = '<div style="font-size:10px; color:#f66; padding:4px;">请求失败: ' + e.message + '</div>';
    }
};

window.smToggleAllTemp = function(checked) {
    var cbs = document.querySelectorAll('.sm-temp-cb');
    for (var i = 0; i < cbs.length; i++) cbs[i].checked = checked;
};
```

Call `smLoadServerPatches()` when solver tab is activated — add to `_applyLeftTabUI` in the `isSolver` block:

```javascript
if (isSolver) {
    // ... existing show/hide logic ...
    window.smLoadServerPatches();
}
```

- [ ] **Step 5: Add `smGetMonitoringConfig()` helper**

This function reads the checkbox states and returns a monitoring config object:

```javascript
window.smGetMonitoringConfig = function() {
    var residualCb = document.getElementById('sm-residual-cb');
    var tempCbs = document.querySelectorAll('.sm-temp-cb:checked');
    var tempPatches = [];
    for (var i = 0; i < tempCbs.length; i++) {
        tempPatches.push({
            patch: tempCbs[i].getAttribute('data-patch'),
            shortId: tempCbs[i].getAttribute('data-short'),
            type: tempCbs[i].getAttribute('data-type'),
        });
    }
    return {
        residuals: residualCb ? residualCb.checked : true,
        serverTemperature: tempPatches,
    };
};
```

- [ ] **Step 6: Verify and commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
# Syntax check
node -e "var fs=require('fs'); var h=fs.readFileSync('STLGeneratorForOpenFoam/web/STL_generate.html','utf8'); try{new Function(h.match(/window\.smLoadServerPatches[\s\S]*?(?=\n    window\.\w+ = )/)[0]); console.log('OK')} catch(e){console.log('ERR:'+e.message); process.exit(1)}"
# Commit both files
cd STLGeneratorForOpenFoam
git add web/STL_generate.html ../STLGeneratorForOpenFoam/server/server.py
git commit -m "feat(B1): solver tab right panel with monitoring configuration"
```

---

### Task 3: Floating Chart Window (Shared UI Shell)

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add `_smCreateMonitorWindow()` function**

This creates the floating draggable window with tab buttons and a canvas. Add after the solver monitoring JS functions:

```javascript
window._smMonitorWindow = null;

window._smCreateMonitorWindow = function(hasTemperature) {
    // Remove existing
    if (window._smMonitorWindow) {
        document.body.removeChild(window._smMonitorWindow);
        window._smMonitorWindow = null;
    }

    var win = document.createElement('div');
    win.style.cssText = 'position:fixed; z-index:900; top:60px; right:20px; width:540px; ' +
        'background:#1a1a2e; border:1px solid #555; border-radius:6px; ' +
        'box-shadow:0 4px 20px rgba(0,0,0,0.6); font-family:"Share Tech Mono",monospace; font-size:12px; color:#ccc;';

    // Header (draggable)
    var header = document.createElement('div');
    header.style.cssText = 'display:flex; justify-content:space-between; align-items:center; padding:6px 10px; ' +
        'background:#222238; border-bottom:1px solid #444; border-radius:6px 6px 0 0; cursor:move; user-select:none;';
    header.innerHTML = '<span id="sm-collapse-btn" style="cursor:pointer; color:#888; font-size:14px; margin-right:8px;">▼</span>' +
        '<span style="flex:1; font-weight:bold; color:#fff;">求解监控</span>' +
        '<span id="sm-close-btn" style="cursor:pointer; color:#888; font-size:16px;">&times;</span>';
    win.appendChild(header);

    // Body (collapsible)
    var body = document.createElement('div');
    body.id = 'sm-monitor-body';
    body.style.cssText = 'padding:8px;';

    // Tab bar
    var tabBar = document.createElement('div');
    tabBar.style.cssText = 'display:flex; gap:4px; margin-bottom:6px;';
    tabBar.innerHTML = '<button id="sm-tab-residual" class="sm-tab-btn" style="padding:3px 12px; font-size:11px; ' +
        'background:#2a3a4a; border:1px solid #4a6a8a; color:#8be9fd; cursor:pointer; border-radius:3px;">残差</button>';
    if (hasTemperature) {
        tabBar.innerHTML += '<button id="sm-tab-temp" class="sm-tab-btn" style="padding:3px 12px; font-size:11px; ' +
            'background:#1a2a3a; border:1px solid #333; color:#888; cursor:pointer; border-radius:3px;">温度</button>';
    }
    body.appendChild(tabBar);

    // Canvas
    var canvas = document.createElement('canvas');
    canvas.id = 'sm-chart-canvas';
    canvas.width = 520;
    canvas.height = 260;
    canvas.style.cssText = 'background:#0d0d1a; border:1px solid #333; border-radius:3px; width:100%;';
    body.appendChild(canvas);

    // Legend
    var legend = document.createElement('div');
    legend.id = 'sm-chart-legend';
    legend.style.cssText = 'margin-top:4px; font-size:10px; color:#888; display:flex; flex-wrap:wrap; gap:6px;';
    body.appendChild(legend);

    win.appendChild(body);
    document.body.appendChild(win);
    window._smMonitorWindow = win;

    // Collapse toggle
    document.getElementById('sm-collapse-btn').addEventListener('click', function() {
        var b = document.getElementById('sm-monitor-body');
        if (b.style.display === 'none') {
            b.style.display = '';
            this.textContent = '▼';
        } else {
            b.style.display = 'none';
            this.textContent = '▶';
        }
    });

    // Close
    document.getElementById('sm-close-btn').addEventListener('click', function() {
        if (window._smMonitorWindow) {
            document.body.removeChild(window._smMonitorWindow);
            window._smMonitorWindow = null;
        }
    });

    // Dragging
    var isDragging = false, dragOffX = 0, dragOffY = 0;
    header.addEventListener('mousedown', function(e) {
        if (e.target.id === 'sm-collapse-btn' || e.target.id === 'sm-close-btn') return;
        isDragging = true;
        var rect = win.getBoundingClientRect();
        dragOffX = e.clientX - rect.left;
        dragOffY = e.clientY - rect.top;
        e.preventDefault();
    });
    document.addEventListener('mousemove', function(e) {
        if (!isDragging) return;
        win.style.left = (e.clientX - dragOffX) + 'px';
        win.style.top = (e.clientY - dragOffY) + 'px';
        win.style.right = 'auto';
    });
    document.addEventListener('mouseup', function() { isDragging = false; });

    // Tab switching
    var tabs = win.querySelectorAll('.sm-tab-btn');
    for (var i = 0; i < tabs.length; i++) {
        tabs[i].addEventListener('click', function() {
            for (var j = 0; j < tabs.length; j++) {
                tabs[j].style.background = '#1a2a3a';
                tabs[j].style.borderColor = '#333';
                tabs[j].style.color = '#888';
            }
            this.style.background = '#2a3a4a';
            this.style.borderColor = '#4a6a8a';
            this.style.color = '#8be9fd';
            window._smActiveTab = this.id === 'sm-tab-residual' ? 'residual' : 'temperature';
            window._smDrawChart();
        });
    }
    window._smActiveTab = 'residual';
};
```

- [ ] **Step 2: Verify syntax and commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && node -e "
var fs = require('fs');
var html = fs.readFileSync('STLGeneratorForOpenFoam/web/STL_generate.html', 'utf8');
var lines = html.split('\n');
var start = lines.findIndex(function(l){ return l.indexOf('window._smCreateMonitorWindow = function') !== -1; });
var depth = 0, end = start;
for (var i = start; i < lines.length; i++) {
    for (var j = 0; j < lines[i].length; j++) {
        if (lines[i][j] === '{') depth++;
        if (lines[i][j] === '}') depth--;
    }
    if (depth === 0 && i > start) { end = i; break; }
}
try { new Function(lines.slice(start, end+1).join('\n')); console.log('_smCreateMonitorWindow: OK'); } catch(e) { console.log('ERROR: ' + e.message); process.exit(1); }
"
```

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(B1): floating monitor window with tabs, drag, collapse"
```

---

### Task 4: Canvas 2D Chart Rendering Engine

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add solver monitor state and chart color palette**

```javascript
window._solverMonitor = {
    active: false,
    residuals: {},       // {fieldName: [{step, value}, ...]}  (value = initial residual)
    temperatures: {},    // {foName: [{step, value}, ...]}  (value in Kelvin)
    currentStep: 0,
    seenFieldsThisStep: {},
    foExpectValue: null,  // state machine: name of FO we're expecting value for
};

window._smActiveTab = 'residual';

// 7 colors for residual fields, 10 for temperature series
window._smResidualColors = {
    'Ux': '#e06c75', 'Uy': '#e5c07b', 'Uz': '#61afef',
    'T': '#c678dd', 'p_rgh': '#56b6c2', 'k': '#98c379', 'epsilon': '#d19a66'
};
window._smTempColors = ['#e06c75','#e5c07b','#61afef','#c678dd','#56b6c2','#98c379','#d19a66','#ff6b9d','#4ecdc4','#f7dc6f'];
window._smHiddenSeries = {}; // {seriesName: true} for toggled-off legend items
```

- [ ] **Step 2: Add `_smDrawChart()` function**

This draws the chart on the canvas. It handles both residual (log scale) and temperature (linear scale) modes:

```javascript
window._smDrawChart = function() {
    var canvas = document.getElementById('sm-chart-canvas');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    var mon = window._solverMonitor;
    var isResidual = (window._smActiveTab === 'residual');
    var datasets = isResidual ? mon.residuals : mon.temperatures;

    // Collect all series
    var seriesNames = Object.keys(datasets);
    if (seriesNames.length === 0) {
        ctx.fillStyle = '#666';
        ctx.font = '12px monospace';
        ctx.fillText(isResidual ? '等待残差数据...' : '等待温度数据...', W/2 - 50, H/2);
        window._smUpdateLegend(seriesNames, isResidual);
        return;
    }

    // Chart margins
    var ml = 60, mr = 10, mt = 10, mb = 30;
    var cw = W - ml - mr, ch = H - mt - mb;

    // Determine X range (time steps) — show last 300 steps
    var allSteps = [];
    for (var k = 0; k < seriesNames.length; k++) {
        var arr = datasets[seriesNames[k]];
        for (var j = 0; j < arr.length; j++) allSteps.push(arr[j].step);
    }
    if (allSteps.length === 0) return;
    var maxStep = Math.max.apply(null, allSteps);
    var visibleSteps = 300;
    var xMin = Math.max(1, maxStep - visibleSteps + 1);
    var xMax = maxStep;

    // Determine Y range
    var yMin, yMax;
    if (isResidual) {
        // Log scale: find min/max of log10(value)
        yMin = -8; yMax = 0;
        for (var k = 0; k < seriesNames.length; k++) {
            if (window._smHiddenSeries[seriesNames[k]]) continue;
            var arr = datasets[seriesNames[k]];
            for (var j = 0; j < arr.length; j++) {
                if (arr[j].step < xMin) continue;
                var lv = Math.log10(Math.max(arr[j].value, 1e-15));
                if (lv < yMin) yMin = lv;
                if (lv > yMax) yMax = lv;
            }
        }
        yMin = Math.floor(yMin) - 1;
        yMax = Math.min(0, Math.ceil(yMax) + 1);
    } else {
        // Linear scale: Celsius
        yMin = Infinity; yMax = -Infinity;
        for (var k = 0; k < seriesNames.length; k++) {
            if (window._smHiddenSeries[seriesNames[k]]) continue;
            var arr = datasets[seriesNames[k]];
            for (var j = 0; j < arr.length; j++) {
                if (arr[j].step < xMin) continue;
                var tc = arr[j].value - 273.15;
                if (tc < yMin) yMin = tc;
                if (tc > yMax) yMax = tc;
            }
        }
        if (yMin === Infinity) { yMin = 0; yMax = 100; }
        var padding = Math.max((yMax - yMin) * 0.1, 0.5);
        yMin -= padding; yMax += padding;
    }

    // Coordinate transform
    function xToPixel(step) { return ml + (step - xMin) / (xMax - xMin + 1) * cw; }
    function yToPixel(val) {
        var v = isResidual ? Math.log10(Math.max(val, 1e-15)) : (val - 273.15);
        return mt + ch - (v - yMin) / (yMax - yMin) * ch;
    }

    // Grid lines
    ctx.strokeStyle = '#2a2a3a';
    ctx.lineWidth = 0.5;
    ctx.setLineDash([2, 4]);
    // Y grid
    if (isResidual) {
        for (var gy = Math.ceil(yMin); gy <= Math.floor(yMax); gy++) {
            var py = mt + ch - (gy - yMin) / (yMax - yMin) * ch;
            ctx.beginPath(); ctx.moveTo(ml, py); ctx.lineTo(ml + cw, py); ctx.stroke();
            ctx.fillStyle = '#555'; ctx.font = '9px monospace';
            ctx.fillText('1e' + gy, 2, py + 3);
        }
    } else {
        var yRange = yMax - yMin;
        var yStep = Math.pow(10, Math.floor(Math.log10(yRange / 5)));
        if (yRange / yStep > 10) yStep *= 2;
        for (var gy = Math.ceil(yMin / yStep) * yStep; gy <= yMax; gy += yStep) {
            var py = mt + ch - (gy - yMin) / (yMax - yMin) * ch;
            ctx.beginPath(); ctx.moveTo(ml, py); ctx.lineTo(ml + cw, py); ctx.stroke();
            ctx.fillStyle = '#555'; ctx.font = '9px monospace';
            ctx.fillText(gy.toFixed(1) + '°C', 2, py + 3);
        }
    }
    // X grid
    var xStepSize = Math.max(1, Math.floor((xMax - xMin) / 6));
    for (var gx = Math.ceil(xMin / xStepSize) * xStepSize; gx <= xMax; gx += xStepSize) {
        var px = xToPixel(gx);
        ctx.beginPath(); ctx.moveTo(px, mt); ctx.lineTo(px, mt + ch); ctx.stroke();
        ctx.fillStyle = '#555'; ctx.font = '9px monospace';
        ctx.fillText(gx, px - 10, H - 5);
    }
    ctx.setLineDash([]);

    // Draw axes
    ctx.strokeStyle = '#555';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(ml, mt); ctx.lineTo(ml, mt + ch); ctx.lineTo(ml + cw, mt + ch);
    ctx.stroke();

    // Draw series
    for (var k = 0; k < seriesNames.length; k++) {
        var name = seriesNames[k];
        if (window._smHiddenSeries[name]) continue;
        var arr = datasets[name];
        var color = isResidual ? (window._smResidualColors[name] || '#ccc') : (window._smTempColors[k % window._smTempColors.length]);

        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        var started = false;
        for (var j = 0; j < arr.length; j++) {
            if (arr[j].step < xMin) continue;
            var px = xToPixel(arr[j].step);
            var py = yToPixel(arr[j].value);
            if (!started) { ctx.moveTo(px, py); started = true; }
            else ctx.lineTo(px, py);
        }
        ctx.stroke();
    }

    window._smUpdateLegend(seriesNames, isResidual);
};
```

- [ ] **Step 3: Add `_smUpdateLegend()` function**

```javascript
window._smUpdateLegend = function(seriesNames, isResidual) {
    var legend = document.getElementById('sm-chart-legend');
    if (!legend) return;
    legend.innerHTML = '';
    for (var i = 0; i < seriesNames.length; i++) {
        var name = seriesNames[i];
        var color = isResidual ? (window._smResidualColors[name] || '#ccc') : (window._smTempColors[i % window._smTempColors.length]);
        var hidden = !!window._smHiddenSeries[name];
        var item = document.createElement('span');
        item.style.cssText = 'cursor:pointer; display:flex; align-items:center; gap:2px;' + (hidden ? ' opacity:0.3;' : '');
        item.innerHTML = '<span style="display:inline-block; width:10px; height:3px; background:' + color + ';"></span>' +
            '<span>' + name + '</span>';
        item.setAttribute('data-series', name);
        item.addEventListener('click', function() {
            var s = this.getAttribute('data-series');
            window._smHiddenSeries[s] = !window._smHiddenSeries[s];
            window._smDrawChart();
        });
        legend.appendChild(item);
    }
};
```

- [ ] **Step 4: Verify syntax and commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && node -e "
var fs = require('fs');
var html = fs.readFileSync('STLGeneratorForOpenFoam/web/STL_generate.html', 'utf8');
var lines = html.split('\n');
['window._smDrawChart = function', 'window._smUpdateLegend = function'].forEach(function(fn) {
    var start = lines.findIndex(function(l){ return l.indexOf(fn) !== -1; });
    if (start === -1) { console.log(fn + ': NOT FOUND'); process.exit(1); }
    var depth = 0, end = start;
    for (var i = start; i < lines.length; i++) {
        for (var j = 0; j < lines[i].length; j++) {
            if (lines[i][j] === '{') depth++;
            if (lines[i][j] === '}') depth--;
        }
        if (depth === 0 && i > start) { end = i; break; }
    }
    try { new Function(lines.slice(start, end+1).join('\n')); console.log(fn.split('=')[0].trim() + ': OK'); } catch(e) { console.log('ERROR: ' + e.message); process.exit(1); }
});
"
```

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(B1): Canvas 2D chart engine with log/linear scale, legend"
```

---

### Task 5: Residual Log Parsing & Monitor Activation

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add `_smParseLogLine()` function**

This is called for every SSE log line during solving. It parses residual and temperature data:

```javascript
window._smParseLogLine = function(line) {
    var mon = window._solverMonitor;
    if (!mon.active) return;

    // Time step marker
    var timeMatch = line.match(/^Time = (\d+)/);
    if (timeMatch) {
        // Flush previous step — trigger redraw
        if (mon.currentStep > 0) window._smDrawChart();
        mon.currentStep = parseInt(timeMatch[1]);
        mon.seenFieldsThisStep = {};
        return;
    }

    // Residual line: "Solving for <field>, Initial residual = <val>, Final residual = <val>"
    var resMatch = line.match(/Solving for (\w+), Initial residual = ([\d.eE+-]+), Final residual = ([\d.eE+-]+)/);
    if (resMatch && mon.currentStep > 0) {
        var field = resMatch[1];
        var initial = parseFloat(resMatch[2]);
        // For fields with multiple solves per step (p_rgh), keep first only
        if (!mon.seenFieldsThisStep[field]) {
            mon.seenFieldsThisStep[field] = true;
            if (!mon.residuals[field]) mon.residuals[field] = [];
            mon.residuals[field].push({step: mon.currentStep, value: initial});
        }
        return;
    }

    // surfaceFieldValue output (two-line state machine)
    // Line 1: "<foName> output:"
    var foMatch = line.match(/^(\S+)\s+output:\s*$/);
    if (foMatch) {
        mon.foExpectValue = foMatch[1];
        return;
    }
    // Line 2: "    areaAverage(T) = <value>"
    if (mon.foExpectValue) {
        var valMatch = line.match(/areaAverage\(T\)\s*=\s*([\d.eE+-]+)/);
        if (valMatch) {
            var foName = mon.foExpectValue;
            var tempK = parseFloat(valMatch[1]);
            if (!mon.temperatures[foName]) mon.temperatures[foName] = [];
            mon.temperatures[foName].push({step: mon.currentStep, value: tempK});
        }
        mon.foExpectValue = null;
        return;
    }

    // ExecutionTime line marks end of a time step — trigger redraw
    if (line.indexOf('ExecutionTime') === 0) {
        window._smDrawChart();
    }
};
```

- [ ] **Step 2: Hook into `_streamDctwinLog` and `runSolveOnly`**

Modify `_streamDctwinLog` to call `_smParseLogLine` for each log line. In the `es.onmessage` handler, after `_appendDctwinLogLine(line)`, add:

```javascript
// After: _appendDctwinLogLine(line);
// Add:
if (window._solverMonitor && window._solverMonitor.active) {
    window._smParseLogLine(line);
}
```

Modify `runSolveOnly` to activate monitoring before starting the log stream. Before `_streamDctwinLog(jobId, ...)`, add:

```javascript
// Activate solver monitoring if configured
var monCfg = window.smGetMonitoringConfig();
if (monCfg.residuals || monCfg.serverTemperature.length > 0) {
    window._solverMonitor = {
        active: true,
        residuals: {},
        temperatures: {},
        currentStep: 0,
        seenFieldsThisStep: {},
        foExpectValue: null,
    };
    window._smHiddenSeries = {};
    window._smCreateMonitorWindow(monCfg.serverTemperature.length > 0);
}
```

Also, when the job completes (in `_streamDctwinLog`'s done handler, around the `if (ok && !isMesh)` block), deactivate monitoring and do a final chart draw:

```javascript
// After: if (ok && !isMesh) { ... }
// Add:
if (window._solverMonitor) {
    window._solverMonitor.active = false;
    window._smDrawChart(); // final redraw
}
```

- [ ] **Step 3: Verify syntax and commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && node -e "
var fs = require('fs');
var html = fs.readFileSync('STLGeneratorForOpenFoam/web/STL_generate.html', 'utf8');
var lines = html.split('\n');
var start = lines.findIndex(function(l){ return l.indexOf('window._smParseLogLine = function') !== -1; });
var depth = 0, end = start;
for (var i = start; i < lines.length; i++) {
    for (var j = 0; j < lines[i].length; j++) {
        if (lines[i][j] === '{') depth++;
        if (lines[i][j] === '}') depth--;
    }
    if (depth === 0 && i > start) { end = i; break; }
}
try { new Function(lines.slice(start, end+1).join('\n')); console.log('_smParseLogLine: OK'); } catch(e) { console.log('ERROR: ' + e.message); process.exit(1); }
"
```

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(B1): residual log parsing, monitor activation on solve"
```

---

### Task 6: Backend — surfaceFieldValue in controlDict Template

**Files:**
- Modify: `dctwin/dctwin/third_parties/foam/template/system/steady/controlDict.j2`
- Modify: `dctwin/dctwin/runner/case.py`
- Modify: `dctwin/dctwin/runner/stlgen_runner.py`

- [ ] **Step 1: Update controlDict Jinja2 template**

Modify the template to support both probes and surfaceFieldValue function objects. The `functions {}` block must wrap both. Replace the existing probes block (lines 48-79) with:

```jinja2
{% if probes|length > 0 or surface_field_values|length > 0 %}
functions
{
{% if probes|length > 0 %}
	probes
	{
		// Where to load it from
		libs ( "libsampling.so" );
		type probes;
		enabled true;
		// Name of the directory for probe data
		name probes;
		// Write control
		writeControl timeStep;
		writeInterval 10;
		// Fields to be probed
		fields
		(
			T
			rho
            p
            p_rgh
            U
		);
        probeLocations
        (
            {% for probe in probes %}
                ({{ probe.x }} {{ probe.y }} {{ probe.z }})
            {% endfor %}
        );
   }
{% endif %}
{% for fo in surface_field_values %}
    {{ fo.name }}
    {
        type            surfaceFieldValue;
        libs            ("libfieldFunctionObjects.so");
        enabled         true;
        writeControl    timeStep;
        writeInterval   1;
        log             true;
        writeFields     false;
        regionType      patch;
        name            {{ fo.patch }};
        operation       areaAverage;
        fields          (T);
    }
{% endfor %}
}
{% endif %}
```

- [ ] **Step 2: Update `_generate_control_dict` in case.py**

Add `monitor_patches` parameter (default empty list) and build `surface_field_values` list:

```python
def _generate_control_dict(self, run_dir: Path, monitor_patches: list | None = None) -> None:
    """Generate controlDict with simulation settings, probes, and optional monitoring."""
    system_dir = run_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    sim_cfg = self.config.simulation_config
    end_time = sim_cfg.get("end_time", 500)
    write_interval = sim_cfg.get("write_interval", 100)
    delta_t = 1  # For steady simulation

    # Ensure write_interval doesn't exceed end_time
    if write_interval > end_time:
        logger.warning(
            f"write_interval ({write_interval}) > end_time ({end_time}), "
            f"adjusting write_interval to {end_time}"
        )
        write_interval = end_time

    # Get sensor locations for probes
    sensors = self.config.extract_sensors()
    probes = [{"x": s["x"], "y": s["y"], "z": s["z"]} for s in sensors]

    # Build surfaceFieldValue function objects for temperature monitoring
    surface_field_values = []
    if monitor_patches:
        for patch_name in monitor_patches:
            # Create a safe function object name from the patch name
            fo_name = "avgT_" + patch_name.replace(" ", "_")
            surface_field_values.append({"name": fo_name, "patch": patch_name})
        logger.info(f"Temperature monitoring: {len(surface_field_values)} patches")

    template = template_env.get_template("foam/template/system/steady/controlDict.j2")
    content = template.render(
        delta_t=delta_t,
        write_interval=write_interval,
        end_time=end_time,
        probes=probes,
        surface_field_values=surface_field_values,
    )

    (system_dir / "controlDict").write_text(content, encoding="utf-8")
    logger.info(f"Generated controlDict: endTime={end_time}, writeInterval={write_interval}, probes={len(probes)}")
```

- [ ] **Step 3: Update stlgen_runner to pass monitor patches**

In `stlgen_runner.py`, find where `runner._generate_control_dict(mesh_dir)` is called (around line 197) and modify to pass monitoring config from `cfd_export.json`:

```python
# Before the _generate_control_dict call, extract monitor patches from simulationConfig
sc = self._data.get("simulationConfig", {})
monitoring = sc.get("monitoring", {})
monitor_patches = [p["patch"] for p in monitoring.get("serverTemperature", [])]

runner._generate_control_dict(mesh_dir, monitor_patches=monitor_patches)
```

- [ ] **Step 4: Update `buildCfdExportJson` in frontend to include monitoring config**

In the `simulationConfig` object of `buildCfdExportJson` (around line 14560), add the monitoring config:

```javascript
simulationConfig: {
    endTime: parseInt(state.cfdRoom.endTime) || 500,
    writeInterval: parseInt(state.cfdRoom.writeInterval) || 100,
    solverType: state.cfdRoom.solverType || 'steady',
    roomTemperature: state.cfdRoom.roomTemperature != null ? state.cfdRoom.roomTemperature : 22,
    parallelSolve: state.cfdRoom.parallelSolve || false,
    solveProcess: state.cfdRoom.meshProcess || 1,
    gravity: {
        x: state.cfdRoom.gravityX != null ? state.cfdRoom.gravityX : 0,
        y: state.cfdRoom.gravityY != null ? state.cfdRoom.gravityY : 0,
        z: state.cfdRoom.gravityZ != null ? state.cfdRoom.gravityZ : -9.81
    },
    monitoring: window.smGetMonitoringConfig ? window.smGetMonitoringConfig() : {}
}
```

- [ ] **Step 5: Commit all backend changes**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
# Commit in parent repo (dctwin files)
git add dctwin/dctwin/third_parties/foam/template/system/steady/controlDict.j2
git add dctwin/dctwin/runner/case.py
git add dctwin/dctwin/runner/stlgen_runner.py
git commit -m "feat(B2): surfaceFieldValue FOs for server temperature monitoring"

# Commit frontend changes
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(B2): include monitoring config in cfd_export.json"
```

---

### Task 7: Integration Testing & Cleanup

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (if fixes needed)
- Modify: `STLGeneratorForOpenFoam/server/server.py` (if fixes needed)

- [ ] **Step 1: Restart server and verify all endpoints**

```bash
fuser -k 8000/tcp 2>/dev/null
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --case-dir dctwin/case/CMI-China &
sleep 10

# Test HTML loads
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/STL_generate.html

# Test server-patches endpoint
curl -s http://localhost:8000/api/server-patches | python3 -m json.tool | head -30

# Test existing endpoints still work
curl -s http://localhost:8000/api/scan-case-status | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK: logRuns:', len(d['logRuns']))"
```

- [ ] **Step 2: Verify all JS functions pass syntax check**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && node -e "
var fs = require('fs');
var html = fs.readFileSync('STLGeneratorForOpenFoam/web/STL_generate.html', 'utf8');
var lines = html.split('\n');
var fns = [
    'window.mpBuildPatchList = function',
    'window.smLoadServerPatches = async function',
    'window.smGetMonitoringConfig = function',
    'window._smCreateMonitorWindow = function',
    'window._smDrawChart = function',
    'window._smUpdateLegend = function',
    'window._smParseLogLine = function',
];
var errors = 0;
fns.forEach(function(fn) {
    var start = lines.findIndex(function(l){ return l.indexOf(fn) !== -1; });
    if (start === -1) { console.log(fn.split('=')[0].trim() + ': NOT FOUND'); errors++; return; }
    var depth = 0, end = start;
    for (var i = start; i < lines.length; i++) {
        for (var j = 0; j < lines[i].length; j++) {
            if (lines[i][j] === '{') depth++;
            if (lines[i][j] === '}') depth--;
        }
        if (depth === 0 && i > start) { end = i; break; }
    }
    try {
        if (fn.indexOf('async') !== -1) new (Object.getPrototypeOf(async function(){}).constructor)(lines.slice(start, end+1).join('\n'));
        else new Function(lines.slice(start, end+1).join('\n'));
        console.log(fn.split('=')[0].trim() + ': OK');
    } catch(e) { console.log(fn.split('=')[0].trim() + ': ERROR: ' + e.message); errors++; }
});
console.log(errors === 0 ? 'All OK' : errors + ' errors');
process.exit(errors);
"
```

- [ ] **Step 3: Test the generated controlDict with monitoring patches**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni && python3 -c "
from jinja2 import Environment, FileSystemLoader
import os
env = Environment(loader=FileSystemLoader(os.path.join('dctwin', 'dctwin', 'third_parties', 'foam', 'template')))
template = env.get_template('system/steady/controlDict.j2')

# Test with both probes and surfaceFieldValue
result = template.render(
    delta_t=1, write_interval=100, end_time=500,
    probes=[{'x': 1.0, 'y': 2.0, 'z': 1.5}],
    surface_field_values=[
        {'name': 'avgT_A00_inlet', 'patch': 'A00_PTN3900_inlet_patch'},
        {'name': 'avgT_A00_outlet', 'patch': 'A00_PTN3900_outlet_patch'},
    ],
)
print(result)
assert 'surfaceFieldValue' in result
assert 'avgT_A00_inlet' in result
assert 'probes' in result
print('\n--- Template renders correctly ---')

# Test with no monitoring (backward compat)
result2 = template.render(
    delta_t=1, write_interval=100, end_time=500,
    probes=[], surface_field_values=[],
)
assert 'functions' not in result2
print('--- Empty template works ---')
"
```

Expected: Both tests pass, template renders correctly with surfaceFieldValue blocks.

- [ ] **Step 4: Fix any issues found and commit**

If any issues are found, fix them and commit with an appropriate message.

---

## Self-Review Checklist

### Spec Coverage
| Spec Requirement | Task |
|---|---|
| A: Rename button | Task 1 Step 1 |
| A: Hierarchical patch tree | Task 1 Step 3 |
| A: Read Zones checkbox (disabled) | Task 1 Step 2 |
| B1: Solver tab right panel | Task 2 Steps 1-2 |
| B1: Server patch list API | Task 2 Step 3 |
| B1: Floating chart window | Task 3 |
| B1: Canvas 2D chart | Task 4 |
| B1: Residual log parsing | Task 5 Step 1 |
| B1: Monitor activation on solve | Task 5 Step 2 |
| B2: controlDict Jinja2 template | Task 6 Step 1 |
| B2: CaseRunner changes | Task 6 Step 2 |
| B2: stlgen_runner changes | Task 6 Step 3 |
| B2: Frontend monitoring config in JSON | Task 6 Step 4 |
| B2: Temperature log parsing | Task 5 Step 1 (state machine in _smParseLogLine) |
| Right panel tab switching | Task 2 Step 2 |
| Integration testing | Task 7 |

### Type Consistency
- `_solverMonitor` object shape consistent across Task 4 (definition) and Task 5 (usage)
- `smGetMonitoringConfig()` output shape consistent between Task 2 (definition) and Task 6 (usage in buildCfdExportJson)
- `monitor_patches` parameter consistent between Task 6 Step 2 (case.py) and Step 3 (stlgen_runner.py)
- `surface_field_values` list shape consistent between Task 6 Step 1 (template) and Step 2 (case.py)
