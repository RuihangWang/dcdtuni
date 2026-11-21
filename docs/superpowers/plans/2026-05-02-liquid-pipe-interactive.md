# Liquid Pipe Interactive Creation & Snap System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add free-form water pipe creation with draggable multi-waypoint control points and auto-snap to CDU/RACK manifold ports.

**Architecture:** Extends existing `pipe_segment` model, `liquid_pipe_renderer.js` (pipe rendering), and `liquid_mode.js` (interaction). New gizmo reuses the `TransformControls` pattern from the junction rotation gizmo. Snap detection is a new subsystem in `liquid_mode.js`.

**Tech Stack:** Three.js r128, vanilla JS in STL_generate.html + liquid_*.js modules

---

## File Structure

| File | Changes |
|------|---------|
| `STL_generate.html` (~line 13396) | `_renderLiquidToolbar()` — merge air+liquid buttons |
| `STL_generate.html` (~line 2968) | `createPipeSegmentModel()` — add `diameter` field |
| `STL_generate.html` (~line 2994) | New `window.createFreePipe()` function |
| `web/js/liquid_pipe_renderer.js` | `createTubeMesh()` — accept radius param; color map for `'unconnected'`; new `buildPipeControlPoints()` / `clearPipeControlPoints()` |
| `web/js/liquid_mode.js` | New waypoint gizmo TC; snap target collection; snap preview/lock; raycast for control points; right-click context menu for control points |

---

### Task 1: Toolbar Merge — Air + Liquid Buttons in One Column

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:13396-13422` (`_renderLiquidToolbar`)

**Context:** Currently `_renderLiquidToolbar()` returns ONLY liquid-specific buttons (CDU, Pipe, contextual). The air buttons (Box, Rack, ACU, Wall, etc.) disappear when switching to liquid mode. We need to include all air buttons first, then add liquid buttons after a separator.

- [ ] **Step 1: Modify `_renderLiquidToolbar()` to include air buttons**

Replace the entire `_renderLiquidToolbar()` function (lines 13396-13422) with:

```js
    function _renderLiquidToolbar() {
        const sel = (typeof getSelectedModel === 'function') ? getSelectedModel() : null;
        const selRackId = (sel && sel.kind === 'rack') ? sel.id : null;
        const selIsServer = state.selection && (state.selection.type === 'rackEquipment' || state.selection.type === 'serverFace' || state.selection.type === 'serverPatch');
        const rackForServer = selIsServer ? _modelMap.get(state.selection.modelId) : null;
        const serverId = selIsServer ? state.selection.equipmentId : null;
        const tFn = (k) => (window.t ? window.t(k) : k);

        let html = '<div style="display:flex; flex-direction:column; gap:4px;">';

        // Air-mode geometry buttons (same as _renderAirToolbar content)
        html += `<button class="btn primary" onclick="createDefaultBaseModel()">${tFn('btn.new_box')}</button>`;
        html += `<button class="btn" style="background:rgba(255,184,0,0.06); border-color:rgba(255,184,0,0.35); color:#ffb800;" onclick="createDefaultRackModel()">${tFn('btn.new_rack')}</button>`;
        html += `<button class="btn" style="background:rgba(0,212,255,0.06); border-color:rgba(0,212,255,0.35); color:var(--accent3);" onclick="createDefaultAcuModel()">${tFn('btn.new_acu')}</button>`;
        html += `<button class="btn" style="background:rgba(204,68,255,0.06); border-color:rgba(204,68,255,0.3); color:#cc44ff;" onpointerdown="beginDragCreate(event,'wall')" onclick="createDefaultWallModel()">${tFn('btn.new_wall')}</button>`;
        html += `<button class="btn" style="background:rgba(184,132,63,0.08); border-color:rgba(184,132,63,0.35); color:#b8843f;" onpointerdown="beginDragCreate(event,'partitionWall')" onclick="addPartitionWall()">${tFn('btn.new_partition_wall')}</button>`;
        html += `<button class="btn" style="background:rgba(0,255,136,0.05); border-color:rgba(0,255,136,0.25); color:var(--highlight);" onpointerdown="beginDragCreate(event,'baffle')" onclick="createDefaultBaffleModel()">${tFn('btn.new_baffle')}</button>`;
        html += `<button class="btn" style="background:rgba(0,255,136,0.04); border-color:rgba(0,255,136,0.18); color:#00cc6a;" onclick="createDefaultFloorModel()">${tFn('btn.new_floor')}</button>`;

        // Separator + liquid-specific buttons
        html += '<div style="border-top:1px solid #444; margin:6px 0; padding-top:6px;"><div style="font-size:10px; color:#00b4dc; padding:2px 4px; font-weight:bold;">💧 液冷专属 / Liquid Cooling</div>';
        html += '<button class="btn" style="background:rgba(0,180,220,0.08); border-color:rgba(0,180,220,0.4); color:#00b4dc;" onclick="createDefaultCduModel()">🏭 新建CDU / New CDU</button>';
        html += '<button class="btn" id="btn-new-pipe" style="background:rgba(0,180,220,0.08); border-color:rgba(0,180,220,0.4); color:#00b4dc;" onclick="window.createFreePipe && window.createFreePipe()">🔗 新建管道 / New Pipe</button>';
        html += '</div>';

        // Contextual buttons
        html += '<div style="border-top:1px solid #333; margin:4px 0; padding-top:4px;"><div style="font-size:10px; color:#888; padding:2px 4px;">上下文 / Contextual:</div>';
        if (selRackId) {
            const rackName = (typeof _liquidEscapeHtml === 'function') ? _liquidEscapeHtml(sel.name) : sel.name;
            html += `<button class="btn" style="background:rgba(0,180,220,0.06); color:#00b4dc;" onclick="window.createCRHForRack('${selRackId}')">🌊 为${rackName}建CRH</button>`;
            html += `<button class="btn" style="background:rgba(0,180,220,0.06); color:#00b4dc;" onclick="window.createColdPlateForRack('${selRackId}')">❄️ 为${rackName}建冷板</button>`;
        } else {
            html += '<button class="btn" disabled style="opacity:0.4;">🌊 为选中Rack建CRH</button>';
            html += '<button class="btn" disabled style="opacity:0.4;">❄️ 为选中Rack建冷板</button>';
        }
        if (selIsServer && rackForServer) {
            html += `<button class="btn" style="background:rgba(255,87,34,0.1); color:#FF5722;" onclick="window.createHeatSourceForServer('${rackForServer.id}', '${serverId}')">🔥 为Server建热源</button>`;
        } else {
            html += '<button class="btn" disabled style="opacity:0.4;">🔥 为选中Server建热源</button>';
        }
        html += '</div></div>';
        return html;
    }
```

- [ ] **Step 2: Verify via browser-use**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
browser-use eval "window.toggleDisplayMode(); 'switched to liquid'"
browser-use eval "document.getElementById('creation-toolbar').innerText"
```

Expected: output contains both "新建方形白模" (Box) AND "新建CDU" AND "新建管道".

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: merge air + liquid buttons in liquid toolbar"
```

---

### Task 2: Data Model — `pipe.diameter` Field and `createFreePipe()`

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:2968-2994` (`createPipeSegmentModel`)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (add `createFreePipe` near line 2994)

- [ ] **Step 1: Add `diameter` field to `createPipeSegmentModel`**

At line 2985, inside the `pipe:` object, add `diameter: 0.025,` after `spec:`:

```js
            pipe: {
                spec: 'pipe_25mm',
                diameter: 0.025,
                startPortId: startPortId,
                endPortId: endPortId,
                waypoints: [],
                color: color,
                junctions: []
            }
```

- [ ] **Step 2: Add `createFreePipe()` function after `createPipeSegmentModel`**

Insert after line 2994 (end of `createPipeSegmentModel`):

```js
    window.createFreePipe = function() {
        pushUndoCheckpoint('createFreePipe');
        const pipeCount = state.models.filter(m => m.kind === 'pipe_segment').length;
        const pipe = createPipeSegmentModel('Pipe_' + (pipeCount + 1));
        pipe.pipe.spec = 'pipe_100mm';
        pipe.pipe.diameter = 0.10;
        pipe.pipe.startPortId = '';
        pipe.pipe.endPortId = '';
        pipe.pipe.color = 'unconnected';
        pipe.pipe.waypoints = [
            { x: 0,   y: 0.5, z: 0 },
            { x: 0,   y: 0.5, z: 0.5 },
            { x: 0,   y: 0.5, z: 1.0 }
        ];
        state.models.push(pipe);
        _modelMap.set(pipe.id, pipe);
        rebuildGeometry();
        updateUI();
        logOperation('createFreePipe', 'Create free pipe ' + pipe.name, { pipeId: pipe.id }, 'create');
        return pipe;
    };
```

- [ ] **Step 3: Also add `diameter` field validation in `cloneRackState` or equivalent pipe clone logic**

Search for where pipe_segment models are cloned/validated during project load. In the deep-clone or validation path, ensure `diameter` defaults to `0.025` if missing:

```js
// Inside the model validation/clone path for pipe_segment:
if (model.pipe && typeof model.pipe.diameter !== 'number') {
    model.pipe.diameter = 0.025;
}
if (model.pipe && !model.pipe.color) {
    model.pipe.color = 'supply';
}
```

- [ ] **Step 4: Verify via browser-use**

```bash
browser-use eval "window.toggleDisplayMode(); var p = window.createFreePipe(); p.pipe.diameter + '|' + p.pipe.color + '|' + p.pipe.waypoints.length"
```

Expected: `0.1|unconnected|3`

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add pipe.diameter field and createFreePipe() for free-form pipes"
```

---

### Task 3: Renderer Updates — Diameter-Based Radius and Unconnected Color

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_pipe_renderer.js:17-24` (constants)
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_pipe_renderer.js:121-137` (`createTubeMesh`)
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_pipe_renderer.js:160-177` (`renderFromPipeSegmentModels`)

- [ ] **Step 1: Add `UNCONNECTED_COLOR` constant**

At line 19, after `RETURN_COLOR`, add:

```js
  const UNCONNECTED_COLOR = 0x8B6914;  // Brown (unconnected pipe)
```

- [ ] **Step 2: Modify `createTubeMesh` to accept optional radius**

Change the function signature at line 121 from:

```js
  function createTubeMesh(points, color) {
```

to:

```js
  function createTubeMesh(points, color, radius) {
    if (points.length < 2) return null;
    var r = (typeof radius === 'number' && radius > 0) ? radius : PIPE_RADIUS;
    var curve = new THREE.CatmullRomCurve3(points, false, 'catmullrom', 0.15);
    var geometry = new THREE.TubeGeometry(curve, PIPE_SEGMENTS, r, PIPE_RADIAL, false);
```

(Replace the existing lines 122-124 with the above.)

- [ ] **Step 3: Update `renderFromPipeSegmentModels` to use diameter and unconnected color**

In `renderFromPipeSegmentModels()` at line 174, change the color mapping and pass radius:

```js
      var color;
      if (p.pipe.color === 'return') color = RETURN_COLOR;
      else if (p.pipe.color === 'unconnected') color = UNCONNECTED_COLOR;
      else color = SUPPLY_COLOR;
      var radius = (p.pipe.diameter && p.pipe.diameter > 0) ? p.pipe.diameter / 2 : PIPE_RADIUS;
      var mesh = createTubeMesh(points, color, radius);
```

Replace the existing lines 174-175.

- [ ] **Step 4: Verify via browser-use**

```bash
browser-use eval "window.toggleDisplayMode(); window.createFreePipe(); 'created'"
# Check scene for brown pipe mesh
browser-use eval "var found = false; scene.traverse(function(obj){ if(obj.userData && obj.userData.isPipe && obj.userData.pipeColor === 0x8B6914) found = true; }); 'brown pipe: ' + found"
```

Expected: `brown pipe: true`

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/js/liquid_pipe_renderer.js
git commit -m "feat: support pipe.diameter and unconnected brown color in renderer"
```

---

### Task 4: Control Point Sphere Rendering

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_pipe_renderer.js` (add `buildPipeControlPoints`, `clearPipeControlPoints`)
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_mode.js` (call on selection change)

**Context:** When a `pipe_segment` model is selected, show spheres at each waypoint. Spheres are tagged with `userData.isPipeControlPoint` for raycast and cleanup.

- [ ] **Step 1: Add `clearPipeControlPoints()` and `buildPipeControlPoints()` to liquid_pipe_renderer.js**

Add at the end of the IIFE (before the closing `})();`), near the `window._liquidPipeRenderer` export:

```js
  let _controlPointSpheres = [];

  function clearPipeControlPoints() {
    _controlPointSpheres.forEach(function(s) {
      if (s.parent) s.parent.remove(s);
      if (s.geometry) s.geometry.dispose();
      if (s.material) s.material.dispose();
    });
    _controlPointSpheres = [];
  }

  function buildPipeControlPoints(pipeId) {
    clearPipeControlPoints();
    if (typeof THREE === 'undefined' || typeof globalGroup === 'undefined') return [];
    var pipe = window._modelMap ? window._modelMap.get(pipeId) : null;
    if (!pipe || pipe.kind !== 'pipe_segment' || !pipe.pipe || !pipe.pipe.waypoints) return [];

    var wp = pipe.pipe.waypoints;
    var diameter = (pipe.pipe.diameter && pipe.pipe.diameter > 0) ? pipe.pipe.diameter : 0.025;
    var pipeColor;
    if (pipe.pipe.color === 'return') pipeColor = RETURN_COLOR;
    else if (pipe.pipe.color === 'unconnected') pipeColor = UNCONNECTED_COLOR;
    else pipeColor = SUPPLY_COLOR;

    wp.forEach(function(w, i) {
      var isEndpoint = (i === 0 || i === wp.length - 1);
      var radius = isEndpoint ? diameter * 1.5 : diameter * 1.2;
      var color = isEndpoint ? pipeColor : 0xFFEB3B;

      var geo = new THREE.SphereGeometry(radius, 16, 12);
      var mat = new THREE.MeshBasicMaterial({
        color: color,
        transparent: true,
        opacity: 0.6,
        depthTest: false
      });
      var sphere = new THREE.Mesh(geo, mat);
      sphere.position.set(w.x, w.y, w.z);
      sphere.renderOrder = 998;
      sphere.userData = {
        isPipeControlPoint: true,
        pipeId: pipeId,
        waypointIndex: i,
        isEndpoint: isEndpoint
      };
      globalGroup.add(sphere);
      _controlPointSpheres.push(sphere);
    });

    if (typeof requestRender === 'function') requestRender();
    return _controlPointSpheres;
  }
```

- [ ] **Step 2: Export the new functions**

Find the `window._liquidPipeRenderer` export object at the end of the file and add the new functions:

```js
  // Add to the existing export object:
  window._liquidPipeRenderer.buildPipeControlPoints = buildPipeControlPoints;
  window._liquidPipeRenderer.clearPipeControlPoints = clearPipeControlPoints;
```

- [ ] **Step 3: Call from liquid_mode.js on selection change**

In `liquid_mode.js`, find the `window._liquidMode` export object and add a `onSelectionChanged` handler. Add this function inside the IIFE:

```js
  function onPipeSelectionChanged(newSelection) {
    if (!newSelection || newSelection.type !== 'model') {
      if (window._liquidPipeRenderer && window._liquidPipeRenderer.clearPipeControlPoints) {
        window._liquidPipeRenderer.clearPipeControlPoints();
      }
      _detachPipeWaypointGizmo();
      return;
    }
    var model = window._modelMap ? window._modelMap.get(newSelection.modelId) : null;
    if (!model || model.kind !== 'pipe_segment') {
      if (window._liquidPipeRenderer && window._liquidPipeRenderer.clearPipeControlPoints) {
        window._liquidPipeRenderer.clearPipeControlPoints();
      }
      _detachPipeWaypointGizmo();
      return;
    }
    if (window._liquidPipeRenderer && window._liquidPipeRenderer.buildPipeControlPoints) {
      window._liquidPipeRenderer.buildPipeControlPoints(model.id);
    }
  }
```

Export it:
```js
  window._liquidMode.onPipeSelectionChanged = onPipeSelectionChanged;
```

- [ ] **Step 4: Hook into updateUI in STL_generate.html**

In the `updateUI()` function of STL_generate.html, find where selection changes are handled. Add a call to the liquid mode selection handler. Search for where `updateUI` dispatches to inspectors and add:

```js
    if (window._liquidMode && window._liquidMode.onPipeSelectionChanged) {
        window._liquidMode.onPipeSelectionChanged(state.selection);
    }
```

- [ ] **Step 5: Verify via browser-use**

```bash
browser-use eval "window.toggleDisplayMode(); var p = window.createFreePipe(); state.selection = {type:'model', modelId:p.id, modelIds:[p.id]}; window.updateUI(); 'selected pipe'"
browser-use eval "var count = 0; scene.traverse(function(obj){ if(obj.userData && obj.userData.isPipeControlPoint) count++; }); 'control points: ' + count"
```

Expected: `control points: 3` (start + middle + end)

- [ ] **Step 6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/js/liquid_pipe_renderer.js STLGeneratorForOpenFoam/web/js/liquid_mode.js STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: render control point spheres when pipe_segment is selected"
```

---

### Task 5: Waypoint Gizmo — TransformControls for Dragging

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_mode.js` (add waypoint TC, parallel to junction TC)

**Context:** Reuse the same `TransformControls` pattern as the junction rotation gizmo (lines 206-306 in liquid_mode.js). The waypoint TC uses `translate` mode instead of `rotate`.

- [ ] **Step 1: Add waypoint TransformControls to liquid_mode.js**

Add after the junction TC section (around line 306), inside the IIFE:

```js
  // ─────────────────────────────────────────────────────────────────────────────
  // Pipe waypoint drag gizmo: TransformControls in translate mode
  // ─────────────────────────────────────────────────────────────────────────────

  let _pipeWaypointTC = null;
  let _pipeWaypointTarget = null;

  function _ensurePipeWaypointTC() {
    if (_pipeWaypointTC) return _pipeWaypointTC;
    if (typeof THREE === 'undefined' || typeof THREE.TransformControls === 'undefined') return null;
    if (typeof camera === 'undefined' || typeof renderer === 'undefined' || typeof scene === 'undefined') return null;

    _pipeWaypointTC = new THREE.TransformControls(camera, renderer.domElement);
    _pipeWaypointTC.setMode('translate');
    _pipeWaypointTC.setSize(0.7);
    scene.add(_pipeWaypointTC);

    _pipeWaypointTC.addEventListener('dragging-changed', function(event) {
      controls.enabled = !event.value;
      if (event.value && typeof pushUndoCheckpoint === 'function') {
        pushUndoCheckpoint('dragPipeWaypoint');
      }
      if (!event.value) {
        _onWaypointDragEnd();
      }
    });

    _pipeWaypointTC.addEventListener('change', function() {
      _syncWaypointFromSphere();
    });

    return _pipeWaypointTC;
  }

  function _attachPipeWaypointGizmo(sphere) {
    var tc = _ensurePipeWaypointTC();
    if (!tc) return;
    tc.attach(sphere);
    _pipeWaypointTarget = sphere;
    if (typeof requestRender === 'function') requestRender();
  }

  function _detachPipeWaypointGizmo() {
    if (_pipeWaypointTC) {
      _pipeWaypointTC.detach();
    }
    _pipeWaypointTarget = null;
    if (typeof controls !== 'undefined') controls.enabled = true;
    if (typeof requestRender === 'function') requestRender();
  }

  function _syncWaypointFromSphere() {
    if (!_pipeWaypointTarget) return;
    var ud = _pipeWaypointTarget.userData;
    if (!ud || !ud.isPipeControlPoint) return;
    var pipe = window._modelMap ? window._modelMap.get(ud.pipeId) : null;
    if (!pipe || !pipe.pipe || !pipe.pipe.waypoints) return;
    var wp = pipe.pipe.waypoints[ud.waypointIndex];
    if (!wp) return;
    wp.x = _pipeWaypointTarget.position.x;
    wp.y = _pipeWaypointTarget.position.y;
    wp.z = _pipeWaypointTarget.position.z;
    if (window._liquidPipeRenderer && window._liquidPipeRenderer.updatePipeGeometry) {
      window._liquidPipeRenderer.updatePipeGeometry();
    }
    _updateSnapPreview();
    if (typeof requestRender === 'function') requestRender();
  }

  function _onWaypointDragEnd() {
    _checkSnapOnDragEnd();
    if (typeof logOperation === 'function' && _pipeWaypointTarget) {
      logOperation('dragPipeWaypoint', 'Drag pipe waypoint', {
        pipeId: _pipeWaypointTarget.userData.pipeId,
        waypointIndex: _pipeWaypointTarget.userData.waypointIndex
      }, 'modify');
    }
  }
```

- [ ] **Step 2: Add click handler for control point spheres**

Add a function to handle clicking control point spheres (called from the main click handler):

```js
  function handleControlPointClick(event) {
    if (typeof camera === 'undefined' || typeof THREE === 'undefined') return false;
    var raycaster = new THREE.Raycaster();
    var rect = renderer.domElement.getBoundingClientRect();
    var mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);

    var controlPoints = [];
    globalGroup.traverse(function(obj) {
      if (obj.userData && obj.userData.isPipeControlPoint) controlPoints.push(obj);
    });
    if (controlPoints.length === 0) return false;

    var hits = raycaster.intersectObjects(controlPoints, false);
    if (hits.length === 0) {
      _detachPipeWaypointGizmo();
      return false;
    }

    _attachPipeWaypointGizmo(hits[0].object);
    return true;
  }
```

- [ ] **Step 3: Export new functions**

```js
  window._liquidMode.handleControlPointClick = handleControlPointClick;
  window._liquidMode.detachPipeWaypointGizmo = _detachPipeWaypointGizmo;
```

- [ ] **Step 4: Hook into main click handler in STL_generate.html**

Find the main viewport click handler and add control point click check before other raycast logic. Search for where `handlePipeClick` is called and add before it:

```js
    if (window._liquidMode && window._liquidMode.handleControlPointClick) {
        if (window._liquidMode.handleControlPointClick(event)) return;
    }
```

- [ ] **Step 5: Verify via browser-use**

```bash
browser-use eval "typeof window._liquidMode.handleControlPointClick === 'function' ? 'exists' : 'missing'"
```

Expected: `exists`

- [ ] **Step 6: Commit**

```bash
git add STLGeneratorForOpenFoam/web/js/liquid_mode.js STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add TransformControls gizmo for pipe waypoint dragging"
```

---

### Task 6: Snap System — Target Collection, Detection, Visual Feedback, Lock

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_mode.js` (add snap functions)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (add `_getManifoldEndpoint` helper)

**Context:** When dragging a pipe endpoint, detect proximity to CDU ports and RACK manifold endpoints. Show a pulsing ring highlight when within 0.15m. On release, snap to exact position and set connection + color.

- [ ] **Step 1: Add `_getManifoldEndpoint` helper in STL_generate.html**

Add near `_renderLiquidManifold` (after line ~8590):

```js
    window._getManifoldEndpoint = function(rackModel, pipeType, end) {
        var rack = rackModel.rack;
        if (!rack || rack.coolingType !== 'liquid') return null;
        var manifold = rack.liquidManifold || {};
        var layout = manifold.layout || 'both-left';
        var cfg = pipeType === 'supply' ? (manifold.supplyPipe || {}) : (manifold.returnPipe || {});
        var pipeSpacing = typeof manifold.pipeSpacing === 'number' ? manifold.pipeSpacing : 0.015;
        var diameter = typeof cfg.diameter === 'number' ? cfg.diameter : 0.025;
        var radius = diameter / 2;
        var offsetFromInlet = typeof cfg.offsetFromInlet === 'number' ? cfg.offsetFromInlet : 0.10;

        var W = rackModel.base.W;
        var edgeMargin = 0.01;
        var safeBottom = rack.safeMarginBottom || 0;
        var slotHeight = (rack.unitCount || 0) * (rack.unitHeight || 0);
        if (slotHeight < 1e-6) return null;

        var pipeX;
        if (layout === 'both-left') {
            var sX = edgeMargin + radius;
            pipeX = pipeType === 'supply' ? sX : sX + radius + pipeSpacing + radius;
        } else if (layout === 'both-right') {
            var rX = W - edgeMargin - radius;
            pipeX = pipeType === 'return' ? rX : rX - radius - pipeSpacing - radius;
        } else {
            pipeX = pipeType === 'supply' ? (edgeMargin + radius) : (W - edgeMargin - radius);
        }

        var pipeY = end === 'top' ? (safeBottom + slotHeight) : safeBottom;
        var a = rackModel.anchor;
        return new THREE.Vector3(a.x + pipeX, a.y + pipeY, a.z + offsetFromInlet);
    };
```

- [ ] **Step 2: Add snap system functions in liquid_mode.js**

Add after the waypoint gizmo code:

```js
  // ─────────────────────────────────────────────────────────────────────────────
  // Snap system: auto-connect pipe endpoints to CDU/RACK ports
  // ─────────────────────────────────────────────────────────────────────────────

  const SNAP_PREVIEW_DISTANCE = 0.15;
  const SNAP_DISCONNECT_DISTANCE = 0.20;
  let _snapHighlightMesh = null;
  let _snapAnimFrame = null;

  function _collectSnapTargets() {
    var targets = [];
    if (!window.state || !window.state.models) return targets;

    window.state.models.forEach(function(m) {
      if (!m.enabled) return;

      if (m.kind === 'cdu') {
        var sp = typeof getCduPort === 'function' ? getCduPort(m, 'supply') :
                 (window._liquidPipeRenderer && window._liquidPipeRenderer.getCduPort ? window._liquidPipeRenderer.getCduPort(m, 'supply') : null);
        var rp = typeof getCduPort === 'function' ? getCduPort(m, 'return') :
                 (window._liquidPipeRenderer && window._liquidPipeRenderer.getCduPort ? window._liquidPipeRenderer.getCduPort(m, 'return') : null);
        if (sp) targets.push({ modelId: m.id, portType: 'supply', portId: m.id + '.supply', position: sp, color: 'supply' });
        if (rp) targets.push({ modelId: m.id, portType: 'return', portId: m.id + '.return', position: rp, color: 'return' });
      }

      if (m.kind === 'rack' && m.rack && m.rack.coolingType === 'liquid' && m.rack.liquidManifold) {
        ['supply', 'return'].forEach(function(pt) {
          ['top', 'bottom'].forEach(function(end) {
            var pos = window._getManifoldEndpoint ? window._getManifoldEndpoint(m, pt, end) : null;
            if (pos) targets.push({
              modelId: m.id, portType: pt,
              portId: m.id + '.manifold_' + pt + '_' + end,
              position: pos, color: pt
            });
          });
        });
      }
    });

    return targets;
  }

  function _updateSnapPreview() {
    if (!_pipeWaypointTarget || !_pipeWaypointTarget.userData.isEndpoint) {
      _hideSnapHighlight();
      return;
    }
    var pos = _pipeWaypointTarget.position;
    var targets = _collectSnapTargets();
    var closest = null;
    var closestDist = Infinity;
    targets.forEach(function(t) {
      var d = pos.distanceTo(t.position);
      if (d < SNAP_PREVIEW_DISTANCE && d < closestDist) {
        closest = t;
        closestDist = d;
      }
    });

    if (closest) {
      _showSnapHighlight(closest);
    } else {
      _hideSnapHighlight();
    }
  }

  function _showSnapHighlight(target) {
    if (!_snapHighlightMesh) {
      var geo = new THREE.RingGeometry(0.06, 0.08, 32);
      var mat = new THREE.MeshBasicMaterial({
        color: target.color === 'return' ? 0xF44336 : 0x2196F3,
        transparent: true, opacity: 0.7,
        side: THREE.DoubleSide, depthTest: false
      });
      _snapHighlightMesh = new THREE.Mesh(geo, mat);
      _snapHighlightMesh.renderOrder = 999;
      globalGroup.add(_snapHighlightMesh);
      _startSnapAnimation();
    }
    _snapHighlightMesh.material.color.set(target.color === 'return' ? 0xF44336 : 0x2196F3);
    _snapHighlightMesh.position.copy(target.position);
    _snapHighlightMesh.lookAt(camera.position);
  }

  function _hideSnapHighlight() {
    if (_snapHighlightMesh) {
      if (_snapHighlightMesh.parent) _snapHighlightMesh.parent.remove(_snapHighlightMesh);
      _snapHighlightMesh.geometry.dispose();
      _snapHighlightMesh.material.dispose();
      _snapHighlightMesh = null;
    }
    if (_snapAnimFrame) {
      cancelAnimationFrame(_snapAnimFrame);
      _snapAnimFrame = null;
    }
  }

  function _startSnapAnimation() {
    function animate() {
      if (!_snapHighlightMesh) return;
      _snapHighlightMesh.material.opacity = 0.3 + 0.5 * Math.abs(Math.sin(Date.now() * 0.005));
      if (_snapHighlightMesh.parent) _snapHighlightMesh.lookAt(camera.position);
      if (typeof requestRender === 'function') requestRender();
      _snapAnimFrame = requestAnimationFrame(animate);
    }
    _snapAnimFrame = requestAnimationFrame(animate);
  }

  function _checkSnapOnDragEnd() {
    if (!_pipeWaypointTarget || !_pipeWaypointTarget.userData.isEndpoint) {
      _hideSnapHighlight();
      return;
    }
    var ud = _pipeWaypointTarget.userData;
    var pipe = window._modelMap ? window._modelMap.get(ud.pipeId) : null;
    if (!pipe || !pipe.pipe) { _hideSnapHighlight(); return; }

    var pos = _pipeWaypointTarget.position;
    var targets = _collectSnapTargets();
    var closest = null;
    var closestDist = Infinity;
    targets.forEach(function(t) {
      var d = pos.distanceTo(t.position);
      if (d < SNAP_PREVIEW_DISTANCE && d < closestDist) {
        closest = t;
        closestDist = d;
      }
    });

    var wpIdx = ud.waypointIndex;
    var isStart = (wpIdx === 0);

    if (closest) {
      _pipeWaypointTarget.position.copy(closest.position);
      pipe.pipe.waypoints[wpIdx] = { x: closest.position.x, y: closest.position.y, z: closest.position.z };
      if (isStart) pipe.pipe.startPortId = closest.portId;
      else pipe.pipe.endPortId = closest.portId;
      _updatePipeColor(pipe);
    } else {
      if (isStart && pipe.pipe.startPortId) { pipe.pipe.startPortId = ''; _updatePipeColor(pipe); }
      if (!isStart && pipe.pipe.endPortId) { pipe.pipe.endPortId = ''; _updatePipeColor(pipe); }
    }

    _hideSnapHighlight();
    if (window._liquidPipeRenderer && window._liquidPipeRenderer.updatePipeGeometry) {
      window._liquidPipeRenderer.updatePipeGeometry();
    }
    if (window._liquidPipeRenderer && window._liquidPipeRenderer.buildPipeControlPoints) {
      window._liquidPipeRenderer.buildPipeControlPoints(ud.pipeId);
    }
    if (_pipeWaypointTarget) _attachPipeWaypointGizmo(_pipeWaypointTarget);
  }

  function _updatePipeColor(pipe) {
    var startPort = pipe.pipe.startPortId;
    var endPort = pipe.pipe.endPortId;
    if (!startPort && !endPort) {
      pipe.pipe.color = 'unconnected';
    } else {
      var portId = startPort || endPort;
      var targets = _collectSnapTargets();
      var match = targets.find(function(t) { return t.portId === portId; });
      pipe.pipe.color = match ? match.color : 'unconnected';
    }
  }
```

- [ ] **Step 3: Verify via browser-use**

```bash
browser-use eval "typeof window._liquidMode !== 'undefined' && window._liquidMode._collectSnapTargets ? 'snap system exists' : 'missing'"
```

Note: `_collectSnapTargets` is internal to the IIFE. Verify via the exported functions instead:

```bash
browser-use eval "window.toggleDisplayMode(); window.createFreePipe(); window.createDefaultCduModel(); 'setup done'"
```

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/js/liquid_mode.js STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add snap system for pipe endpoints to CDU/RACK manifold ports"
```

---

### Task 7: Right-Click Context Menu for Control Points

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/js/liquid_mode.js` (extend context menu handler)

**Context:** The existing `handleContextMenu` in liquid_mode.js handles right-click on waypoint spheres for T-branch upgrade. Extend it to also handle pipe control point spheres with "Add Control Point" / "Delete Control Point" options.

- [ ] **Step 1: Add control point context menu handler**

Add inside the IIFE in liquid_mode.js:

```js
  function handleControlPointContextMenu(event) {
    if (typeof camera === 'undefined' || typeof THREE === 'undefined') return false;
    var raycaster = new THREE.Raycaster();
    var rect = renderer.domElement.getBoundingClientRect();
    var mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);

    var controlPoints = [];
    globalGroup.traverse(function(obj) {
      if (obj.userData && obj.userData.isPipeControlPoint) controlPoints.push(obj);
    });
    if (controlPoints.length === 0) return false;

    var hits = raycaster.intersectObjects(controlPoints, false);
    if (hits.length === 0) return false;

    event.preventDefault();
    var sphere = hits[0].object;
    var ud = sphere.userData;
    _showControlPointMenu(event.clientX, event.clientY, ud.pipeId, ud.waypointIndex, ud.isEndpoint);
    return true;
  }

  function _showControlPointMenu(cx, cy, pipeId, wpIdx, isEndpoint) {
    var existing = document.getElementById('pipe-cp-context-menu');
    if (existing) existing.remove();

    var pipe = window._modelMap ? window._modelMap.get(pipeId) : null;
    if (!pipe || !pipe.pipe) return;
    var wp = pipe.pipe.waypoints;

    var menu = document.createElement('div');
    menu.id = 'pipe-cp-context-menu';
    menu.style.cssText = 'position:fixed;left:' + cx + 'px;top:' + cy + 'px;z-index:10000;background:#1e1e2e;border:1px solid #444;border-radius:4px;padding:4px 0;min-width:180px;box-shadow:0 4px 12px rgba(0,0,0,0.5);font-size:12px;color:#ccc;';

    // Add control point after this one (not on last point)
    if (wpIdx < wp.length - 1) {
      var addBtn = document.createElement('div');
      addBtn.textContent = '➕ 添加控制点 / Add Point After';
      addBtn.style.cssText = 'padding:6px 12px;cursor:pointer;';
      addBtn.onmouseenter = function() { this.style.background = '#333'; };
      addBtn.onmouseleave = function() { this.style.background = 'none'; };
      addBtn.onclick = function() {
        menu.remove();
        if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('addPipeControlPoint');
        var curr = wp[wpIdx];
        var next = wp[wpIdx + 1];
        var mid = { x: (curr.x + next.x) / 2, y: (curr.y + next.y) / 2, z: (curr.z + next.z) / 2 };
        wp.splice(wpIdx + 1, 0, mid);
        if (window._liquidPipeRenderer) {
          window._liquidPipeRenderer.updatePipeGeometry();
          window._liquidPipeRenderer.buildPipeControlPoints(pipeId);
        }
        if (typeof logOperation === 'function') logOperation('addPipeControlPoint', 'Add pipe control point', { pipeId: pipeId, index: wpIdx + 1 }, 'modify');
      };
      menu.appendChild(addBtn);
    }

    // Delete control point (only intermediate, not endpoints, and must have >2 points)
    if (!isEndpoint && wp.length > 2) {
      var delBtn = document.createElement('div');
      delBtn.textContent = '🗑️ 删除此控制点 / Delete Point';
      delBtn.style.cssText = 'padding:6px 12px;cursor:pointer;color:#F44336;';
      delBtn.onmouseenter = function() { this.style.background = '#333'; };
      delBtn.onmouseleave = function() { this.style.background = 'none'; };
      delBtn.onclick = function() {
        menu.remove();
        if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('deletePipeControlPoint');
        wp.splice(wpIdx, 1);
        _detachPipeWaypointGizmo();
        if (window._liquidPipeRenderer) {
          window._liquidPipeRenderer.updatePipeGeometry();
          window._liquidPipeRenderer.buildPipeControlPoints(pipeId);
        }
        if (typeof logOperation === 'function') logOperation('deletePipeControlPoint', 'Delete pipe control point', { pipeId: pipeId, index: wpIdx }, 'modify');
      };
      menu.appendChild(delBtn);
    }

    // Disconnect (endpoint with connection)
    if (isEndpoint) {
      var isStart = (wpIdx === 0);
      var hasConn = isStart ? !!pipe.pipe.startPortId : !!pipe.pipe.endPortId;
      if (hasConn) {
        var discBtn = document.createElement('div');
        discBtn.textContent = '🔌 断开连接 / Disconnect';
        discBtn.style.cssText = 'padding:6px 12px;cursor:pointer;color:#FF9800;';
        discBtn.onmouseenter = function() { this.style.background = '#333'; };
        discBtn.onmouseleave = function() { this.style.background = 'none'; };
        discBtn.onclick = function() {
          menu.remove();
          if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('disconnectPipeEndpoint');
          if (isStart) pipe.pipe.startPortId = '';
          else pipe.pipe.endPortId = '';
          _updatePipeColor(pipe);
          if (window._liquidPipeRenderer) {
            window._liquidPipeRenderer.updatePipeGeometry();
            window._liquidPipeRenderer.buildPipeControlPoints(pipeId);
          }
          if (typeof logOperation === 'function') logOperation('disconnectPipeEndpoint', 'Disconnect pipe endpoint', { pipeId: pipeId, end: isStart ? 'start' : 'end' }, 'modify');
        };
        menu.appendChild(discBtn);
      }
    }

    document.body.appendChild(menu);
    setTimeout(function() {
      function removeMenu(e) { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', removeMenu); } }
      document.addEventListener('click', removeMenu);
    }, 50);
  }
```

- [ ] **Step 2: Export and hook into main context menu**

```js
  window._liquidMode.handleControlPointContextMenu = handleControlPointContextMenu;
```

In STL_generate.html, find the viewport `contextmenu` event handler and add:

```js
    if (window._liquidMode && window._liquidMode.handleControlPointContextMenu) {
        if (window._liquidMode.handleControlPointContextMenu(event)) return;
    }
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/js/liquid_mode.js STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add right-click context menu for pipe control points (add/delete/disconnect)"
```

---

### Task 8: Integration Test — Full Workflow Verification

**Files:**
- No code changes — browser-use verification only

- [ ] **Step 1: Start server and open page**

```bash
fuser 8005/tcp 2>/dev/null || conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --port 8005 &
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
```

- [ ] **Step 2: Switch to liquid mode and verify toolbar**

```bash
browser-use eval "window.toggleDisplayMode(); document.getElementById('creation-toolbar').innerText.indexOf('新建方形白模') >= 0 && document.getElementById('creation-toolbar').innerText.indexOf('新建CDU') >= 0 && document.getElementById('creation-toolbar').innerText.indexOf('新建管道') >= 0 ? 'PASS: all buttons present' : 'FAIL'"
```

Expected: `PASS: all buttons present`

- [ ] **Step 3: Create free pipe and verify data model**

```bash
browser-use eval "var p = window.createFreePipe(); JSON.stringify({diameter: p.pipe.diameter, color: p.pipe.color, waypoints: p.pipe.waypoints.length, startPort: p.pipe.startPortId, endPort: p.pipe.endPortId})"
```

Expected: `{"diameter":0.1,"color":"unconnected","waypoints":3,"startPort":"","endPort":""}`

- [ ] **Step 4: Select pipe and verify control points appear**

```bash
browser-use eval "var p = state.models.find(function(m){return m.kind==='pipe_segment'}); state.selection = {type:'model', modelId:p.id, modelIds:[p.id]}; window.updateUI(); var count = 0; scene.traverse(function(obj){ if(obj.userData && obj.userData.isPipeControlPoint) count++; }); 'control points: ' + count"
```

Expected: `control points: 3`

- [ ] **Step 5: Verify pipe rendered with brown color in scene**

```bash
browser-use eval "var found = false; scene.traverse(function(obj){ if(obj.userData && obj.userData.isPipe && obj.material && obj.material.color) { var hex = obj.material.color.getHex(); if (hex === 0x8B6914) found = true; } }); 'brown pipe: ' + found"
```

Expected: `brown pipe: true`

- [ ] **Step 6: Create CDU and verify snap targets exist**

```bash
browser-use eval "window.createDefaultCduModel(); 'CDU created'"
# The snap targets are internal, but we can verify CDU ports are computed
browser-use eval "var cdu = state.models.find(function(m){return m.kind==='cdu'}); cdu ? 'CDU exists: ' + cdu.name : 'no CDU'"
```

Expected: CDU exists

- [ ] **Step 7: Verify undo/redo for pipe creation**

```bash
browser-use eval "var before = state.models.filter(function(m){return m.kind==='pipe_segment'}).length; window.performUndo(); var after = state.models.filter(function(m){return m.kind==='pipe_segment'}).length; 'before: ' + before + ', after undo: ' + after"
```

Expected: `after undo` count is less than `before`

- [ ] **Step 8: Take xvfb screenshot for visual verification**

Use the Playwright xvfb approach (from earlier in this session) to capture a screenshot showing the brown pipe rendered in 3D.

- [ ] **Step 9: Commit integration test results**

No code to commit — verification only. Mark task complete.

---

## Self-Review Checklist

| Spec Section | Task |
|-------------|------|
| §2 Toolbar merge | Task 1 |
| §3 Data model (diameter, createFreePipe) | Task 2 |
| §4 Rendering (diameter radius, unconnected color) | Task 3 |
| §4.2-4.3 Control point spheres | Task 4 |
| §5 Gizmo interaction | Task 5 |
| §6 Snap system (targets, detection, visual, lock, disconnect) | Task 6 |
| §7 Color inheritance | Task 6 (_updatePipeColor) |
| §8 Right-click context menu | Task 7 |
| §10 Testing | Task 8 |
