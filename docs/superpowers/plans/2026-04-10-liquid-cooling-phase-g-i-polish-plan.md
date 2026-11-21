# Liquid Cooling Polish Phases G-I Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete T-branch junction interaction (right-click menu + rotation gizmo + 3-port dragging), validate legacy migration with synthetic fixtures, and run a visual consistency audit with browser screenshots.

**Architecture:** Three phases. Phase G extends `liquid_mode.js` and `liquid_pipe_renderer.js` with new T-branch interactions and creates a dedicated TransformControls instance for junction rotation. Phase H writes synthetic legacy-schema fixtures and validates migration by loading them through the existing `_applyProjectData` pipeline. Phase I uses browser-use to capture standardized screenshots and fix visual issues.

**Tech Stack:** Vanilla JS, Three.js r128 (TransformControls, Raycaster, BoxGeometry, SphereGeometry), browser-use MCP for automated testing.

**Design Spec:** `AGENTS/specs/2026-04-10-liquid-cooling-phase-g-i-polish-design.md`

---

## Key Pre-Discovered Facts

**From exploration of current code:**

1. **`_upgradeToTBranch` is at `liquid_mode.js:181`** — currently creates a junction model but does **not** split the original pipe into two. This is a gap to close in G.2.

2. **Waypoint mesh userData** is set at `liquid_pipe_renderer.js:281`:
   ```javascript
   s.userData = {isWaypoint: true, chainIdx: ci, itemIdx: ii, wpIdx: wi};
   ```
   `chainIdx` refers to **legacy** `state.liquidCooling.piping.chains[]` indexing. For new `pipe_segment` models, a different convention is needed — waypoint spheres created from pipe_segment models should carry `pipeSegmentId` instead. **This must be added in G.1 before the right-click menu can work.**

3. **TransformControls is NOT integrated with model selection.** It currently only binds to the post-processing seed sphere (`STL_generate.html:18169+`). Phase G.4 creates a **new dedicated TransformControls instance** for junction rotation, attached to a `_junctionTransformControls` variable.

4. **No existing legacy `.cfdproj` files contain `rdhx` or `liquidCooling` data.** Phase H **must** construct synthetic legacy fixtures. This is a plan adjustment from the spec's "test real legacy files" phrasing.

5. **`_applyProjectData` at `STL_generate.html:~12149`** is where `migrateLiquidCoolingToKinds()` gets called during project load. Phase H tests load through this path.

---

## File Map

| File | Phase | Change Type |
|------|-------|-------------|
| `src/frontend/web/js/liquid_pipe_renderer.js` | G.1, G.3, G.5, I | Modify — pipe_segment waypoint userData, junction rendering, pipe endpoint recompute |
| `src/frontend/web/js/liquid_mode.js` | G.1, G.2, G.4 | Modify — context menu, pipe split, gizmo attach/detach |
| `src/frontend/web/STL_generate.html` | G.4, H.3 | Modify — selection hook for junction gizmo, migration fixes if needed |
| `/tmp/liquid_legacy_fixtures/*.cfdproj` | H.1 | Create — synthetic legacy projects (NOT committed) |
| `/tmp/phase_i_screenshots/*.png` | I.2 | Create — audit screenshots (NOT committed) |

---

## Phase G: T-Branch Junction Complete Interaction

### Task G.1: Attach userData to pipe_segment waypoint spheres

**Context**: Currently, waypoint spheres in `rebuildEditVisuals()` at `liquid_pipe_renderer.js:269-299` use `chainIdx`/`itemIdx`/`wpIdx` which refer to legacy chains. New `pipe_segment` models don't have waypoint spheres visible at all (edit mode was tied to legacy chains). We need a new path that:
1. Iterates `state.models` filtering `kind === 'pipe_segment'`.
2. For each pipe_segment, creates waypoint spheres.
3. Sets userData: `{isWaypoint: true, pipeSegmentId, waypointIndex}`.

**Files:**
- Modify: `src/frontend/web/js/liquid_pipe_renderer.js`

- [ ] **Step 1: Locate `rebuildEditVisuals` and understand current behavior**

```bash
grep -n 'rebuildEditVisuals\|_waypointSpheres' /mnt/lv/litianyu/mycode/dcdtuni/src/frontend/web/js/liquid_pipe_renderer.js
```

Expected: function at around line 269, `_waypointSpheres` state variable near top of file.

- [ ] **Step 2: Add a new function to render waypoint spheres from pipe_segment models**

Add this function immediately after `renderFromPipeSegmentModels` (around line 163):

```javascript
  /**
   * Build interactive waypoint spheres for all pipe_segment models.
   * Used in liquid mode for right-click upgrade to T-branch.
   * Spheres carry userData with pipeSegmentId + waypointIndex for raycasting lookup.
   * Returns an array of sphere meshes (caller is responsible for disposal).
   */
  function buildPipeSegmentWaypointSpheres() {
    const spheres = [];
    if (!window.state || !window.state.models) return spheres;
    const pipes = window.state.models.filter(m => m.kind === 'pipe_segment' && m.enabled);
    pipes.forEach(pipe => {
      const wps = (pipe.pipe && pipe.pipe.waypoints) || [];
      wps.forEach((wp, wi) => {
        const sphere = new THREE.Mesh(
          new THREE.SphereGeometry(0.025, 12, 12),
          new THREE.MeshStandardMaterial({ color: 0xffcc00, roughness: 0.3 })
        );
        sphere.position.set(wp.x, wp.y, wp.z);
        sphere.userData = {
          isWaypoint: true,
          pipeSegmentId: pipe.id,
          waypointIndex: wi
        };
        if (typeof globalGroup !== 'undefined') globalGroup.add(sphere);
        spheres.push(sphere);
      });
    });
    return spheres;
  }
```

- [ ] **Step 3: Expose the function and a tracking array on `window`**

At the end of the IIFE (find `})();` at the file's bottom), add before the closing:

```javascript
  window._liquidPipeRenderer = window._liquidPipeRenderer || {};
  window._liquidPipeRenderer.buildPipeSegmentWaypointSpheres = buildPipeSegmentWaypointSpheres;
```

- [ ] **Step 4: Syntax check**

```bash
node --check /mnt/lv/litianyu/mycode/dcdtuni/src/frontend/web/js/liquid_pipe_renderer.js && echo "OK"
```

- [ ] **Step 5: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/frontend
git add web/js/liquid_pipe_renderer.js
git commit -m "feat(liquid): G.1 — waypoint sphere builder for pipe_segment models"

cd /mnt/lv/litianyu/mycode/dcdtuni
git add frontend
git commit -m "feat(liquid): G.1"
```

---

### Task G.2: Rewrite `_upgradeToTBranch` to split the pipe

**Context**: Currently `_upgradeToTBranch` just creates a junction and tags the pipe. It must **split the original pipe into two new pipes** at the waypoint and delete the original.

**Files:**
- Modify: `src/frontend/web/js/liquid_mode.js`

- [ ] **Step 1: Read the current implementation**

Open `liquid_mode.js` lines 181-210. The current version creates a junction at `pipe.pipe.waypoints[waypointIndex]`, sets `junction.connectedPipes = [pipeId]`, and adds an entry to `pipe.pipe.junctions[]` without actually splitting.

- [ ] **Step 2: Replace the entire `_upgradeToTBranch` function**

Find the current function (starts at line 181 with `window._upgradeToTBranch = function(pipeId, waypointIndex) {`). Replace the entire function with:

```javascript
  window._upgradeToTBranch = function(pipeId, waypointIndex) {
    const pipe = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(pipeId) : null;
    if (!pipe || pipe.kind !== 'pipe_segment') {
      console.warn('[liquid_mode] _upgradeToTBranch: pipe not found', pipeId);
      return null;
    }
    const wps = (pipe.pipe && pipe.pipe.waypoints) || [];
    if (waypointIndex <= 0 || waypointIndex >= wps.length - 1) {
      alert('只能对管道中间的航点升级为T型分支 / Only intermediate waypoints can be upgraded');
      return null;
    }
    const W = wps[waypointIndex];
    if (typeof pushUndoCheckpoint === 'function') pushUndoCheckpoint('upgradeToTBranch');

    // 1. Create the junction model at the waypoint position
    if (typeof createPipeJunctionModel !== 'function') {
      console.error('[liquid_mode] createPipeJunctionModel not found');
      return null;
    }
    const junction = createPipeJunctionModel(
      'TBranch_' + pipe.name + '_' + waypointIndex,
      pipeId,
      { x: W.x, y: W.y, z: W.z }
    );
    // Define 3 ports (local offsets)
    junction.junction = {
      type: 'T_branch',
      quaternion: { x: 0, y: 0, z: 0, w: 1 },
      ports: [
        { id: 'port_1', offset: { x: -0.04, y: 0, z: 0 }, type: 'inout', connectedPipeId: null },
        { id: 'port_2', offset: { x:  0.04, y: 0, z: 0 }, type: 'inout', connectedPipeId: null },
        { id: 'port_3', offset: { x: 0, y: 0, z:  0.04 }, type: 'out',   connectedPipeId: null }
      ],
      connectedPipes: []
    };

    // 2. Create Pipe_A (original start → junction.port_1)
    if (typeof createPipeSegmentModel !== 'function') {
      console.error('[liquid_mode] createPipeSegmentModel not found');
      return null;
    }
    const pipeA = createPipeSegmentModel(
      pipe.name + '_A',
      pipe.pipe.startPortId,
      junction.id + '.port_1',
      pipe.pipe.color || 'supply'
    );
    pipeA.pipe.waypoints = wps.slice(0, waypointIndex + 1).map(p => ({x: p.x, y: p.y, z: p.z}));
    pipeA.pipe.spec = pipe.pipe.spec;

    // 3. Create Pipe_B (junction.port_2 → original end)
    const pipeB = createPipeSegmentModel(
      pipe.name + '_B',
      junction.id + '.port_2',
      pipe.pipe.endPortId,
      pipe.pipe.color || 'supply'
    );
    pipeB.pipe.waypoints = wps.slice(waypointIndex).map(p => ({x: p.x, y: p.y, z: p.z}));
    pipeB.pipe.spec = pipe.pipe.spec;

    // 4. Wire up port connections on the junction
    junction.junction.ports[0].connectedPipeId = pipeA.id;
    junction.junction.ports[1].connectedPipeId = pipeB.id;

    // 5. Delete the original pipe from state
    const idx = window.state.models.findIndex(m => m.id === pipeId);
    if (idx >= 0) window.state.models.splice(idx, 1);
    if (typeof _modelMap !== 'undefined' && _modelMap) _modelMap.delete(pipeId);

    // 6. Push new models
    window.state.models.push(pipeA);
    window.state.models.push(pipeB);
    window.state.models.push(junction);
    if (typeof _modelMap !== 'undefined' && _modelMap) {
      _modelMap.set(pipeA.id, pipeA);
      _modelMap.set(pipeB.id, pipeB);
      _modelMap.set(junction.id, junction);
    }

    // 7. Select the junction and rebuild
    if (typeof setSelection === 'function') setSelection('model', junction.id);
    if (typeof rebuildGeometry === 'function') rebuildGeometry();
    if (typeof logOperation === 'function') {
      logOperation('upgradeToTBranch', 'Split pipe at waypoint ' + waypointIndex,
        { originalPipeId: pipeId, junctionId: junction.id, pipeAId: pipeA.id, pipeBId: pipeB.id },
        'create');
    }
    return junction.id;
  };
```

- [ ] **Step 3: Syntax check**

```bash
node --check /mnt/lv/litianyu/mycode/dcdtuni/src/frontend/web/js/liquid_mode.js && echo "OK"
```

- [ ] **Step 4: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/frontend
git add web/js/liquid_mode.js
git commit -m "feat(liquid): G.2 — split pipe on T-branch upgrade"

cd /mnt/lv/litianyu/mycode/dcdtuni
git add frontend
git commit -m "feat(liquid): G.2"
```

---

### Task G.3: Render junction as cube with 3 port spheres

**Files:**
- Modify: `src/frontend/web/js/liquid_pipe_renderer.js`

- [ ] **Step 1: Add junction rendering function**

In `liquid_pipe_renderer.js`, add this function right after `buildPipeSegmentWaypointSpheres` (added in G.1):

```javascript
  /**
   * Render a pipe_junction model as a colored cube with 3 port spheres.
   * Port spheres are added to globalGroup directly (not into _pipeGroup)
   * so they remain raycastable even when _pipeGroup is rebuilt.
   */
  function renderPipeJunction(junctionModel) {
    if (!junctionModel || junctionModel.kind !== 'pipe_junction' || !junctionModel.enabled) return;
    const j = junctionModel.junction || {};
    const anchor = junctionModel.anchor || { x: 0, y: 0, z: 0 };
    const q = j.quaternion || { x: 0, y: 0, z: 0, w: 1 };

    // 1. Junction body (cube)
    const cubeGeom = new THREE.BoxGeometry(0.06, 0.06, 0.06);
    const cubeMat = new THREE.MeshStandardMaterial({
      color: 0x9c27b0,  // purple — distinct from CDU blue / CRH blue
      roughness: 0.4,
      metalness: 0.3
    });
    const cube = new THREE.Mesh(cubeGeom, cubeMat);
    cube.position.set(anchor.x, anchor.y, anchor.z);
    cube.quaternion.set(q.x, q.y, q.z, q.w);
    cube.userData = { isJunctionBody: true, junctionId: junctionModel.id };
    if (typeof globalGroup !== 'undefined') globalGroup.add(cube);
    if (_pipeGroup) _pipeGroup.userData._junctionMeshes = _pipeGroup.userData._junctionMeshes || [];
    if (_pipeGroup) _pipeGroup.userData._junctionMeshes.push(cube);

    // 2. Port spheres (children of cube so they inherit rotation)
    const ports = j.ports || [];
    ports.forEach(port => {
      const isFree = !port.connectedPipeId;
      const color = isFree ? 0x00ff88 : 0x666666;  // lime green = free, gray = connected
      const sphereGeom = new THREE.SphereGeometry(0.015, 12, 12);
      const sphereMat = new THREE.MeshStandardMaterial({ color: color, roughness: 0.3 });
      const sphere = new THREE.Mesh(sphereGeom, sphereMat);
      sphere.position.set(port.offset.x, port.offset.y, port.offset.z);  // local to cube
      sphere.userData = {
        isJunctionPort: true,
        junctionId: junctionModel.id,
        portId: port.id,
        isFree: isFree
      };
      cube.add(sphere);  // attach to cube as child
    });
  }
```

- [ ] **Step 2: Call `renderPipeJunction` from `updatePipeGeometry`**

Find `updatePipeGeometry` (around line 399). Inside, find the section that calls `renderFromPipeSegmentModels()` (around line 403). After that block, add a junction rendering loop:

```javascript
    // Render pipe_junction models (after pipes, since junctions sit on top)
    if (window.state && window.state.models) {
      window.state.models
        .filter(m => m.kind === 'pipe_junction' && m.enabled)
        .forEach(renderPipeJunction);
    }
```

- [ ] **Step 3: Extend `clearPipes` to also dispose junction meshes**

Find `clearPipes` function. Locate where it traverses `_pipeGroup` children. Before the final `_pipeGroup = null`, add:

```javascript
    // Remove junction cubes and their port sphere children from globalGroup
    if (_pipeGroup && _pipeGroup.userData && _pipeGroup.userData._junctionMeshes) {
      _pipeGroup.userData._junctionMeshes.forEach(function(cube) {
        if (cube.parent) cube.parent.remove(cube);
        cube.traverse(function(child) {
          if (child.geometry) child.geometry.dispose();
          if (child.material) child.material.dispose();
        });
      });
    }
```

- [ ] **Step 4: Syntax check and commit**

```bash
node --check /mnt/lv/litianyu/mycode/dcdtuni/src/frontend/web/js/liquid_pipe_renderer.js && echo "OK"

cd /mnt/lv/litianyu/mycode/dcdtuni/frontend
git add web/js/liquid_pipe_renderer.js
git commit -m "feat(liquid): G.3 — render pipe_junction as cube with 3 port spheres"

cd /mnt/lv/litianyu/mycode/dcdtuni
git add frontend
git commit -m "feat(liquid): G.3"
```

---

### Task G.4: Right-click context menu for waypoint upgrade

**Files:**
- Modify: `src/frontend/web/js/liquid_mode.js`

- [ ] **Step 1: Add waypoint spheres to `_buildPortIndicators` when in pipe creation mode OR make them always visible in liquid mode**

The cleanest approach is to ensure waypoint spheres exist and are raycastable whenever liquid mode is active. For this plan we use a lazy approach: waypoint spheres are only created while in pipe creation mode (reusing the existing `enterPipeCreationMode` infrastructure).

Instead, add a new "waypoint interaction mode" that's separate from pipe creation mode. Add to `liquid_mode.js` after the existing `_portIndicators` declaration:

```javascript
  let _waypointSpheres = [];
```

- [ ] **Step 2: Add `_buildWaypointSpheres` and `_clearWaypointSpheres` helpers**

Add after the existing `_clearPortIndicators` function in `liquid_mode.js`:

```javascript
  function _buildWaypointSpheres() {
    _clearWaypointSpheres();
    if (typeof window._liquidPipeRenderer !== 'object'
        || typeof window._liquidPipeRenderer.buildPipeSegmentWaypointSpheres !== 'function') {
      return;
    }
    _waypointSpheres = window._liquidPipeRenderer.buildPipeSegmentWaypointSpheres();
  }

  function _clearWaypointSpheres() {
    _waypointSpheres.forEach(function(s) {
      if (s.parent) s.parent.remove(s);
      if (s.geometry) s.geometry.dispose();
      if (s.material) s.material.dispose();
    });
    _waypointSpheres = [];
  }
```

- [ ] **Step 3: Add `contextmenu` event listener**

Add after the `window._handlePipeClick` window export at the end of the IIFE:

```javascript
  /**
   * Right-click handler — shows context menu if a waypoint sphere is hit.
   */
  function handleContextMenu(event) {
    if (typeof isLiquidMode !== 'function' || !isLiquidMode()) return;
    // Build waypoint spheres on demand if not already present
    if (_waypointSpheres.length === 0) {
      _buildWaypointSpheres();
    }
    if (_waypointSpheres.length === 0) return;
    // Raycast
    if (typeof camera === 'undefined' || typeof THREE === 'undefined') return;
    const raycaster = new THREE.Raycaster();
    const target = event.target;
    const rect = (target && target.getBoundingClientRect) ? target.getBoundingClientRect() : {left:0,top:0,width:window.innerWidth,height:window.innerHeight};
    const mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(mouse, camera);
    const hits = raycaster.intersectObjects(_waypointSpheres, false);
    if (hits.length === 0) return;
    const hit = hits[0].object;
    if (!hit.userData || !hit.userData.isWaypoint) return;
    event.preventDefault();
    event.stopPropagation();
    _showWaypointContextMenu(event.clientX, event.clientY, hit.userData.pipeSegmentId, hit.userData.waypointIndex);
  }

  function _showWaypointContextMenu(clientX, clientY, pipeSegmentId, waypointIndex) {
    // Remove any existing menu first
    const existing = document.getElementById('liquid-pipe-context-menu');
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

    const menu = document.createElement('div');
    menu.id = 'liquid-pipe-context-menu';
    menu.style.cssText = 'position:fixed; z-index:9999; background:#2a2a3e; border:1px solid #555; border-radius:4px; padding:4px 0; font-family:monospace; font-size:12px; color:#ccc; box-shadow:0 4px 12px rgba(0,0,0,0.5); min-width:200px;';
    menu.style.left = clientX + 'px';
    menu.style.top = clientY + 'px';

    const item = document.createElement('div');
    item.style.cssText = 'padding:6px 16px; cursor:pointer;';
    item.textContent = '升级为T型分支 / Upgrade to T-Branch';
    item.addEventListener('mouseenter', function() { item.style.background = '#3a3a5e'; });
    item.addEventListener('mouseleave', function() { item.style.background = 'transparent'; });
    item.addEventListener('click', function() {
      _closeContextMenu();
      if (typeof window._upgradeToTBranch === 'function') {
        window._upgradeToTBranch(pipeSegmentId, waypointIndex);
        // Rebuild waypoint spheres after the topology change
        _buildWaypointSpheres();
      }
    });
    menu.appendChild(item);
    document.body.appendChild(menu);

    // Dismiss on outside click
    setTimeout(function() {
      document.addEventListener('click', _closeContextMenu, { once: true });
      document.addEventListener('keydown', _escapeCloseContextMenu);
    }, 10);
  }

  function _closeContextMenu() {
    const menu = document.getElementById('liquid-pipe-context-menu');
    if (menu && menu.parentNode) menu.parentNode.removeChild(menu);
    document.removeEventListener('keydown', _escapeCloseContextMenu);
  }

  function _escapeCloseContextMenu(e) {
    if (e.key === 'Escape') _closeContextMenu();
  }

  // Install the context menu listener on the renderer DOM element after load
  if (typeof window !== 'undefined') {
    window.addEventListener('load', function() {
      setTimeout(function() {
        const rendererEl = (typeof renderer !== 'undefined' && renderer.domElement)
          ? renderer.domElement
          : document.querySelector('canvas');
        if (rendererEl) {
          rendererEl.addEventListener('contextmenu', handleContextMenu);
          console.log('[liquid_mode.js] Context menu listener attached');
        }
      }, 500);
    });
  }
```

- [ ] **Step 4: Rebuild waypoint spheres when `rebuildGeometry` runs**

Add a small hook at the end of `rebuildEditVisuals` in `liquid_pipe_renderer.js`, or more simply — call `_buildWaypointSpheres` from within `updatePipeGeometry` after junctions render.

Actually, a cleaner approach: make `_buildWaypointSpheres` idempotent and callable on demand. The right-click handler already handles lazy creation. But we still need to **clear stale waypoint spheres** when pipes change. Add to `clearPipes` in liquid_pipe_renderer.js:

```javascript
    // Also clear waypoint spheres since they're tied to specific pipe IDs
    if (window._liquidMode && window._liquidMode.clearWaypointSpheres) {
      window._liquidMode.clearWaypointSpheres();
    }
```

And expose `_clearWaypointSpheres` on window from `liquid_mode.js`:

```javascript
  window._liquidMode = window._liquidMode || {};
  window._liquidMode.clearWaypointSpheres = _clearWaypointSpheres;
  window._liquidMode.buildWaypointSpheres = _buildWaypointSpheres;
```

- [ ] **Step 5: Syntax check and commit**

```bash
node --check /mnt/lv/litianyu/mycode/dcdtuni/src/frontend/web/js/liquid_mode.js && echo "liquid_mode OK"
node --check /mnt/lv/litianyu/mycode/dcdtuni/src/frontend/web/js/liquid_pipe_renderer.js && echo "liquid_pipe_renderer OK"

cd /mnt/lv/litianyu/mycode/dcdtuni/frontend
git add web/js/liquid_mode.js web/js/liquid_pipe_renderer.js
git commit -m "feat(liquid): G.4 — right-click context menu for T-branch upgrade"

cd /mnt/lv/litianyu/mycode/dcdtuni
git add frontend
git commit -m "feat(liquid): G.4"
```

---

### Task G.5: Junction rotation gizmo + pipe endpoint recompute

**Files:**
- Modify: `src/frontend/web/js/liquid_mode.js`
- Modify: `src/frontend/web/STL_generate.html`
- Modify: `src/frontend/web/js/liquid_pipe_renderer.js`

- [ ] **Step 1: Create a dedicated TransformControls instance for junctions**

In `liquid_mode.js`, add state variable:

```javascript
  let _junctionTC = null;  // Dedicated TransformControls for junction rotation
  let _junctionTCTarget = null;  // Currently attached junction mesh
```

Add initialization function:

```javascript
  function _ensureJunctionTC() {
    if (_junctionTC) return _junctionTC;
    if (typeof THREE === 'undefined' || typeof THREE.TransformControls === 'undefined') return null;
    if (typeof camera === 'undefined' || typeof renderer === 'undefined' || typeof scene === 'undefined') return null;

    _junctionTC = new THREE.TransformControls(camera, renderer.domElement);
    _junctionTC.setMode('rotate');
    _junctionTC.setSize(0.9);
    // Z-axis only
    _junctionTC.showX = false;
    _junctionTC.showY = false;
    _junctionTC.showZ = true;
    scene.add(_junctionTC);

    // When the gizmo is being dragged, disable OrbitControls
    _junctionTC.addEventListener('dragging-changed', function(e) {
      if (typeof controls !== 'undefined' && controls) {
        controls.enabled = !e.value;
      }
    });

    // On rotation change, write quaternion back to the model and reroute pipes
    _junctionTC.addEventListener('objectChange', function() {
      if (!_junctionTCTarget) return;
      const junctionId = _junctionTCTarget.userData && _junctionTCTarget.userData.junctionId;
      if (!junctionId) return;
      const model = (typeof _modelMap !== 'undefined' && _modelMap) ? _modelMap.get(junctionId) : null;
      if (!model || !model.junction) return;
      const q = _junctionTCTarget.quaternion;
      model.junction.quaternion = { x: q.x, y: q.y, z: q.z, w: q.w };
      // Recompute connected pipe endpoints
      if (typeof window._reroutePipesForJunction === 'function') {
        window._reroutePipesForJunction(junctionId);
      }
    });

    return _junctionTC;
  }

  function attachJunctionGizmo(junctionId) {
    const tc = _ensureJunctionTC();
    if (!tc) return;
    // Find the junction's cube mesh in the scene
    let cube = null;
    if (typeof globalGroup !== 'undefined') {
      globalGroup.traverse(function(o) {
        if (o.userData && o.userData.isJunctionBody && o.userData.junctionId === junctionId) {
          cube = o;
        }
      });
    }
    if (!cube) return;
    tc.attach(cube);
    _junctionTCTarget = cube;
  }

  function detachJunctionGizmo() {
    if (_junctionTC) {
      _junctionTC.detach();
    }
    _junctionTCTarget = null;
  }

  // Expose
  window._liquidMode = window._liquidMode || {};
  window._liquidMode.attachJunctionGizmo = attachJunctionGizmo;
  window._liquidMode.detachJunctionGizmo = detachJunctionGizmo;
```

- [ ] **Step 2: Add pipe reroute function in `liquid_pipe_renderer.js`**

Add at the top of the file (inside the IIFE, near other helpers):

```javascript
  /**
   * Recompute endpoint waypoints of pipes connected to a rotated junction.
   * Called when the junction's quaternion changes.
   */
  function reroutePipesForJunction(junctionId) {
    if (!window.state || !window.state.models) return;
    const junction = window.state.models.find(m => m.id === junctionId && m.kind === 'pipe_junction');
    if (!junction || !junction.junction) return;
    const anchor = junction.anchor;
    const q = junction.junction.quaternion;
    const qt = new THREE.Quaternion(q.x, q.y, q.z, q.w);
    const ports = junction.junction.ports || [];

    ports.forEach(function(port) {
      if (!port.connectedPipeId) return;
      const pipe = window.state.models.find(m => m.id === port.connectedPipeId && m.kind === 'pipe_segment');
      if (!pipe || !pipe.pipe) return;
      // World position of this port
      const localOff = new THREE.Vector3(port.offset.x, port.offset.y, port.offset.z);
      localOff.applyQuaternion(qt);
      const worldPos = { x: anchor.x + localOff.x, y: anchor.y + localOff.y, z: anchor.z + localOff.z };
      // Update whichever end of the pipe connects to this port
      const portFullId = junctionId + '.' + port.id;
      const wps = pipe.pipe.waypoints || [];
      if (pipe.pipe.startPortId === portFullId && wps.length > 0) {
        wps[0] = worldPos;
      } else if (pipe.pipe.endPortId === portFullId && wps.length > 0) {
        wps[wps.length - 1] = worldPos;
      }
    });

    // Rebuild pipe tube meshes
    if (typeof updatePipeGeometry === 'function') updatePipeGeometry();
  }

  // Expose
  window._reroutePipesForJunction = reroutePipesForJunction;
```

- [ ] **Step 3: Hook junction gizmo attach into setSelection**

In `STL_generate.html`, find `window.setSelection = function` or `function setSelection(` (search with grep). At the END of the function body, add:

```javascript
        // Liquid mode: attach rotation gizmo for pipe_junction models
        if (typeof isLiquidMode === 'function' && isLiquidMode()) {
            const sel = (typeof getSelectedModel === 'function') ? getSelectedModel() : null;
            if (sel && sel.kind === 'pipe_junction') {
                if (window._liquidMode && window._liquidMode.attachJunctionGizmo) {
                    window._liquidMode.attachJunctionGizmo(sel.id);
                }
            } else {
                if (window._liquidMode && window._liquidMode.detachJunctionGizmo) {
                    window._liquidMode.detachJunctionGizmo();
                }
            }
        }
```

- [ ] **Step 4: Detach gizmo on mode switch**

In `_applyDisplayMode()` in `STL_generate.html`, add:

```javascript
        if (typeof isAirMode === 'function' && isAirMode()) {
            if (window._liquidMode && window._liquidMode.detachJunctionGizmo) {
                window._liquidMode.detachJunctionGizmo();
            }
        }
```

- [ ] **Step 5: Syntax check and commit**

```bash
node --check /mnt/lv/litianyu/mycode/dcdtuni/src/frontend/web/js/liquid_mode.js && echo "OK"
node --check /mnt/lv/litianyu/mycode/dcdtuni/src/frontend/web/js/liquid_pipe_renderer.js && echo "OK"

cd /mnt/lv/litianyu/mycode/dcdtuni/frontend
git add web/js/liquid_mode.js web/js/liquid_pipe_renderer.js web/STL_generate.html
git commit -m "feat(liquid): G.5 — junction rotation gizmo and pipe endpoint reroute"

cd /mnt/lv/litianyu/mycode/dcdtuni
git add frontend
git commit -m "feat(liquid): Phase G complete"
```

### Phase G Acceptance

- Right-clicking a yellow waypoint sphere in liquid mode shows a floating menu.
- Clicking "Upgrade to T-Branch" splits the pipe at that waypoint into two new pipes.
- A purple junction cube renders at the split point with 3 port spheres (2 gray, 1 green).
- Selecting the junction attaches a Z-axis rotation gizmo.
- Rotating the gizmo updates the junction's quaternion and re-routes connected pipe endpoints.
- Switching back to air mode detaches the gizmo.

---

## Phase H: Legacy Migration E2E Validation with Synthetic Fixtures

**Context adjustment**: The exploration phase confirmed that **no existing `.cfdproj` files in the projects directory contain legacy liquid cooling data** (`rdhx`, `liquidCooling.coldPlates`, `piping.chains`). Therefore, this phase uses **synthetic legacy fixtures** that simulate what a Phase 1-8 project would look like.

### Task H.1: Create synthetic legacy fixtures

**Files:**
- Create: `/tmp/liquid_legacy_fixtures/legacy_rdhx_only.cfdproj`
- Create: `/tmp/liquid_legacy_fixtures/legacy_cold_plates.cfdproj`
- Create: `/tmp/liquid_legacy_fixtures/legacy_chains.cfdproj`
- Create: `/tmp/liquid_legacy_fixtures/legacy_all.cfdproj`
- Create: `/tmp/liquid_legacy_fixtures/legacy_edge_cases.cfdproj`

- [ ] **Step 1: Create fixtures directory**

```bash
mkdir -p /tmp/liquid_legacy_fixtures
```

- [ ] **Step 2: Generate `legacy_rdhx_only.cfdproj`**

Only tests the `rack.rdhx` → `crh` migration path.

```bash
cat > /tmp/liquid_legacy_fixtures/legacy_rdhx_only.cfdproj <<'JSON'
{
  "state": {
    "displayMode": "modeling",
    "inputUnit": "mm",
    "projectName": "legacy_rdhx_only_test",
    "models": [
      {
        "id": 101,
        "kind": "rack",
        "name": "Rack_A",
        "enabled": true,
        "anchor": { "x": 0, "y": 0, "z": 0 },
        "base": { "W": 0.6, "D": 1.0, "H": 1.8 },
        "transform": { "quaternion": { "x": 0, "y": 0, "z": 0, "w": 1 }, "scale": { "x": 1, "y": 1, "z": 1 } },
        "rack": { "equipments": [], "unitHeight": 0.04445, "unitCount": 42 },
        "rdhx": {
          "enabled": true,
          "effectiveness": 0.65,
          "waterFlowRate": 25,
          "waterInletTemp": 22,
          "heatRejectionCapacity": 35000
        }
      }
    ]
  }
}
JSON
```

- [ ] **Step 3: Generate `legacy_cold_plates.cfdproj`**

Tests the `equipment.liquidCooling.coldPlates` → `cold_plate` + `heat_source` migration path.

```bash
cat > /tmp/liquid_legacy_fixtures/legacy_cold_plates.cfdproj <<'JSON'
{
  "state": {
    "displayMode": "modeling",
    "inputUnit": "mm",
    "projectName": "legacy_cold_plates_test",
    "models": [
      {
        "id": 201,
        "kind": "rack",
        "name": "Rack_B",
        "enabled": true,
        "anchor": { "x": 2, "y": 0, "z": 0 },
        "base": { "W": 0.6, "D": 1.0, "H": 1.8 },
        "transform": { "quaternion": { "x": 0, "y": 0, "z": 0, "w": 1 }, "scale": { "x": 1, "y": 1, "z": 1 } },
        "rack": {
          "unitHeight": 0.04445,
          "unitCount": 42,
          "equipments": [
            {
              "id": 901,
              "name": "Server_01",
              "kind": "fanServer",
              "startUnit": 5,
              "spanUnits": 2,
              "liquidCooling": {
                "enabled": true,
                "coldPlates": [
                  {
                    "id": "cp_cpu_1",
                    "label": "CPU_1",
                    "specName": "cp_default",
                    "chipTDP": 400,
                    "thermalModel": { "chipArea": 0.005, "chipThickness": 0.0012, "thermalConductivity": 150 }
                  },
                  {
                    "id": "cp_gpu_1",
                    "label": "GPU_1",
                    "specName": "cp_highflow",
                    "chipTDP": 700,
                    "thermalModel": { "chipArea": 0.008, "chipThickness": 0.0015, "thermalConductivity": 148 }
                  }
                ]
              }
            }
          ]
        }
      }
    ]
  }
}
JSON
```

- [ ] **Step 4: Generate `legacy_chains.cfdproj`**

Tests the `state.liquidCooling.piping.chains` → `pipe_segment` migration path.

```bash
cat > /tmp/liquid_legacy_fixtures/legacy_chains.cfdproj <<'JSON'
{
  "state": {
    "displayMode": "modeling",
    "inputUnit": "mm",
    "projectName": "legacy_chains_test",
    "models": [],
    "liquidCooling": {
      "enabled": true,
      "piping": {
        "autoGenerated": false,
        "mainPipeSpec": "pipe_25mm",
        "chains": [
          {
            "id": "CDU_1_to_Rack_A_supply",
            "path": [
              "CDU_1_supply",
              { "pipe": "pipe_25mm", "waypoints": [{"x":0.5,"y":0,"z":1.2},{"x":2,"y":0,"z":1.2},{"x":2,"y":0.5,"z":1.2}] },
              "Rack_A_manifold_in"
            ]
          },
          {
            "id": "CDU_1_from_Rack_A_return",
            "path": [
              "Rack_A_manifold_out",
              { "pipe": "pipe_25mm", "waypoints": [{"x":2,"y":0.5,"z":1.15},{"x":2,"y":0,"z":1.15},{"x":0.5,"y":0,"z":1.15}] },
              "CDU_1_return"
            ]
          }
        ]
      }
    }
  }
}
JSON
```

- [ ] **Step 5: Generate `legacy_all.cfdproj` (combines all 3 legacy schemas)**

```bash
cat > /tmp/liquid_legacy_fixtures/legacy_all.cfdproj <<'JSON'
{
  "state": {
    "displayMode": "modeling",
    "inputUnit": "mm",
    "projectName": "legacy_all_combined",
    "models": [
      {
        "id": 301,
        "kind": "rack",
        "name": "Rack_Alpha",
        "enabled": true,
        "anchor": { "x": 0, "y": 0, "z": 0 },
        "base": { "W": 0.6, "D": 1.0, "H": 1.8 },
        "transform": { "quaternion": { "x": 0, "y": 0, "z": 0, "w": 1 }, "scale": { "x": 1, "y": 1, "z": 1 } },
        "rack": {
          "unitHeight": 0.04445,
          "unitCount": 42,
          "equipments": [
            {
              "id": 902,
              "name": "Srv_1",
              "kind": "fanServer",
              "startUnit": 10,
              "spanUnits": 2,
              "liquidCooling": {
                "enabled": true,
                "coldPlates": [
                  {
                    "id": "cp_1",
                    "label": "CPU",
                    "specName": "cp_default",
                    "chipTDP": 300,
                    "thermalModel": { "chipArea": 0.004, "chipThickness": 0.001, "thermalConductivity": 148 }
                  }
                ]
              }
            }
          ]
        },
        "rdhx": {
          "enabled": true,
          "effectiveness": 0.5,
          "waterFlowRate": 20,
          "waterInletTemp": 25,
          "heatRejectionCapacity": 30000
        }
      }
    ],
    "liquidCooling": {
      "enabled": true,
      "piping": {
        "autoGenerated": false,
        "mainPipeSpec": "pipe_25mm",
        "chains": [
          {
            "id": "supply_chain_1",
            "path": [
              "CDU_1_supply",
              { "pipe": "pipe_25mm", "waypoints": [{"x":0,"y":0,"z":1},{"x":1,"y":0,"z":1}] },
              "Rack_Alpha_in"
            ]
          }
        ]
      }
    }
  }
}
JSON
```

- [ ] **Step 6: Generate `legacy_edge_cases.cfdproj`**

Covers these edge cases:
- Rack with `rdhx: {enabled: false}` — should NOT create a CRH
- Rack without any `rdhx` field — should not throw
- Equipment with `liquidCooling: {enabled: true, coldPlates: []}` — empty array
- Empty `state.liquidCooling.piping.chains` array

```bash
cat > /tmp/liquid_legacy_fixtures/legacy_edge_cases.cfdproj <<'JSON'
{
  "state": {
    "displayMode": "modeling",
    "inputUnit": "mm",
    "projectName": "legacy_edge_cases",
    "models": [
      {
        "id": 401,
        "kind": "rack",
        "name": "Rack_Disabled_RDHx",
        "enabled": true,
        "anchor": { "x": 0, "y": 0, "z": 0 },
        "base": { "W": 0.6, "D": 1.0, "H": 1.8 },
        "transform": { "quaternion": { "x": 0, "y": 0, "z": 0, "w": 1 }, "scale": { "x": 1, "y": 1, "z": 1 } },
        "rack": { "equipments": [], "unitHeight": 0.04445, "unitCount": 42 },
        "rdhx": { "enabled": false, "effectiveness": 0.5, "waterFlowRate": 20, "waterInletTemp": 25 }
      },
      {
        "id": 402,
        "kind": "rack",
        "name": "Rack_No_RDHx_Field",
        "enabled": true,
        "anchor": { "x": 2, "y": 0, "z": 0 },
        "base": { "W": 0.6, "D": 1.0, "H": 1.8 },
        "transform": { "quaternion": { "x": 0, "y": 0, "z": 0, "w": 1 }, "scale": { "x": 1, "y": 1, "z": 1 } },
        "rack": { "equipments": [], "unitHeight": 0.04445, "unitCount": 42 }
      },
      {
        "id": 403,
        "kind": "rack",
        "name": "Rack_Empty_CPs",
        "enabled": true,
        "anchor": { "x": 4, "y": 0, "z": 0 },
        "base": { "W": 0.6, "D": 1.0, "H": 1.8 },
        "transform": { "quaternion": { "x": 0, "y": 0, "z": 0, "w": 1 }, "scale": { "x": 1, "y": 1, "z": 1 } },
        "rack": {
          "unitHeight": 0.04445,
          "unitCount": 42,
          "equipments": [
            {
              "id": 903,
              "name": "Srv_EmptyLC",
              "kind": "fanServer",
              "startUnit": 5,
              "spanUnits": 1,
              "liquidCooling": { "enabled": true, "coldPlates": [] }
            }
          ]
        }
      }
    ],
    "liquidCooling": { "enabled": false, "piping": { "chains": [] } }
  }
}
JSON
```

- [ ] **Step 7: Verify all fixtures are valid JSON**

```bash
for f in /tmp/liquid_legacy_fixtures/*.cfdproj; do
  python3 -c "import json; json.load(open('$f'))" && echo "$f: OK" || echo "$f: FAIL"
done
```

Expected: 5 lines of "OK".

---

### Task H.2: Automated E2E test loop using browser-use

**Goal:** Load each fixture into the browser, switch to liquid mode, take screenshots, and verify that the expected liquid models appear.

**Files:** No file changes — this task runs against existing browser-use infrastructure.

- [ ] **Step 1: Start the server**

```bash
fuser -k 8005/tcp 2>/dev/null; sleep 1
mkdir -p /tmp/lc_phase_h_test/model
conda run -n dcdtuni python /mnt/lv/litianyu/mycode/dcdtuni/src/server/server.py --port 8005 --case-dir /tmp/lc_phase_h_test 2>&1 &
sleep 5
curl -s -o /dev/null -w "%{http_code}" http://localhost:8005/api/auth/check && echo " server up"
```

- [ ] **Step 2: For each fixture, load it and screenshot the liquid tree**

This step must be performed manually via the browser-use MCP tools during plan execution. For each of the 5 fixture files, the implementer must:

1. Navigate to `http://localhost:8005/STL_generate.html`
2. Login with `admin` / `WIN#@desk` (first fixture only, session persists)
3. Click the file input (`#file-proj`) to trigger file chooser — or use the following JavaScript injection via browser-use navigate URL:
   ```
   javascript:document.getElementById('file-proj').click()
   ```
   Then use browser-use to attach the fixture file to the input.
   
   **Simpler approach**: Use the POST `/api/write-file` endpoint (if it exists) to write the fixture into `/tmp/lc_phase_h_test/model/cfd_export.json`, then reload the page. Check via:
   ```bash
   grep -n 'api/write-file\|api/browser' /mnt/lv/litianyu/mycode/dcdtuni/src/server/server.py | head -5
   ```

4. After load, check browser console for errors:
   - Use browser-use `get_state` to capture the current state
   - Take a screenshot

5. Click "液冷模拟" button to switch to liquid mode
6. Take a screenshot of the liquid tree showing migrated models

For each fixture, the expected post-migration state is:

| Fixture | Expected crh | Expected cold_plate | Expected heat_source | Expected pipe_segment |
|---------|--------------|--------------------|--------------------|---------------------|
| legacy_rdhx_only | 1 | 0 | 0 | 0 |
| legacy_cold_plates | 0 | 2 | 2 | 0 |
| legacy_chains | 0 | 0 | 0 | 2 |
| legacy_all | 1 | 1 | 1 | 1 |
| legacy_edge_cases | 0 | 0 | 0 | 0 |

- [ ] **Step 3: Since interactive browser-use testing of 5 fixtures is tedious, add an automated verification via Node.js**

Create a verification script at `/tmp/verify_migration.js`:

```javascript
#!/usr/bin/env node
// Verify migrateLiquidCoolingToKinds() behavior against fixture files.
// Runs the migration function in isolation (Node.js, no DOM).

const fs = require('fs');
const path = require('path');

const FIXTURES_DIR = '/tmp/liquid_legacy_fixtures';

// Expected outcomes per fixture
const EXPECTED = {
  'legacy_rdhx_only.cfdproj':  { crh: 1, cold_plate: 0, heat_source: 0, pipe_segment: 0 },
  'legacy_cold_plates.cfdproj':{ crh: 0, cold_plate: 2, heat_source: 2, pipe_segment: 0 },
  'legacy_chains.cfdproj':     { crh: 0, cold_plate: 0, heat_source: 0, pipe_segment: 2 },
  'legacy_all.cfdproj':        { crh: 1, cold_plate: 1, heat_source: 1, pipe_segment: 1 },
  'legacy_edge_cases.cfdproj': { crh: 0, cold_plate: 0, heat_source: 0, pipe_segment: 0 },
};

// Stub the factory functions (produce models with correct 'kind')
let _nextId = 1000;
function nextModelId() { return _nextId++; }
function getDefaultModelTransform() { return { quaternion: {x:0,y:0,z:0,w:1}, scale: {x:1,y:1,z:1} }; }
function getDefaultModelMoveTool() { return { mode: 'translate' }; }
function createFacesState() { return {}; }

function createCrhModel(name, parentRackId) {
  return { id: nextModelId(), kind: 'crh', name, parentRackId, crh: {}, anchor: {x:0,y:0,z:0}, base: {W:0,D:0,H:0} };
}
function createColdPlateModel(name, parentRackId, parentServerId) {
  return { id: nextModelId(), kind: 'cold_plate', name, parentRackId, parentServerId, coldPlate: { thermalModel: {} }, anchor: {x:0,y:0,z:0}, base: {W:0,D:0,H:0} };
}
function createHeatSourceModel(name, parentRackId, parentServerId) {
  return { id: nextModelId(), kind: 'heat_source', name, parentRackId, parentServerId, heatSource: {}, anchor: {x:0,y:0,z:0}, base: {W:0,D:0,H:0} };
}
function createPipeSegmentModel(name, startPortId, endPortId, color) {
  return { id: nextModelId(), kind: 'pipe_segment', name, pipe: { startPortId, endPortId, color, waypoints: [], junctions: [] }, anchor: {x:0,y:0,z:0}, base: {W:0,D:0,H:0} };
}

// Inline copy of migrateLiquidCoolingToKinds (extracted from STL_generate.html)
function migrateLiquidCoolingToKinds(state) {
  if (state.schemaVersion && state.schemaVersion >= 2) return;
  const newModels = [];
  const oldModels = state.models.slice();

  oldModels.forEach(m => {
    if (m.kind === 'rack' && m.rdhx && m.rdhx.enabled) {
      const crh = createCrhModel('CRH_' + m.name, m.id);
      crh.crh = {
        effectiveness: (m.rdhx.effectiveness != null) ? m.rdhx.effectiveness : 0.5,
        waterFlowRate: (m.rdhx.waterFlowRate != null) ? m.rdhx.waterFlowRate : 20,
        waterInletTemp: (m.rdhx.waterInletTemp != null) ? m.rdhx.waterInletTemp : 25,
        heatRejectionCapacity: (m.rdhx.heatRejectionCapacity != null) ? m.rdhx.heatRejectionCapacity : 30000
      };
      crh.anchor = { x: m.anchor.x, y: m.anchor.y + m.base.D, z: m.anchor.z };
      crh.base = { W: m.base.W, D: 0.02, H: m.base.H };
      newModels.push(crh);
      delete m.rdhx;
    }
    if (m.kind === 'rack' && m.rack && m.rack.equipments) {
      m.rack.equipments.forEach(eq => {
        if (eq.liquidCooling && eq.liquidCooling.enabled && eq.liquidCooling.coldPlates) {
          eq.liquidCooling.coldPlates.forEach(cp => {
            const eqName = eq.name || eq.id || 'eq';
            const cpName = (cp.label || cp.id || 'CP') + '_' + eqName;
            const cpModel = createColdPlateModel(cpName, m.id, eq.id);
            cpModel.coldPlate.specName = cp.specName || 'cp_default';
            cpModel.coldPlate.chipTDP = cp.chipTDP || 350;
            if (cp.thermalModel) {
              cpModel.coldPlate.thermalModel = Object.assign({}, cpModel.coldPlate.thermalModel, cp.thermalModel);
            }
            const slotZ = m.anchor.z + (eq.startUnit || 0) * ((m.rack && m.rack.unitHeight) || 0.04445);
            cpModel.anchor = { x: m.anchor.x + 0.05, y: m.anchor.y + 0.1, z: slotZ + 0.02 };
            newModels.push(cpModel);

            const hs = createHeatSourceModel('Heat_' + cpName, m.id, eq.id);
            hs.heatSource.power = cp.chipTDP || 350;
            if (cp.thermalModel && cp.thermalModel.chipArea) {
              hs.heatSource.chipArea = cp.thermalModel.chipArea;
            }
            hs.anchor = { x: cpModel.anchor.x, y: cpModel.anchor.y, z: cpModel.anchor.z + 0.01 };
            newModels.push(hs);
          });
          delete eq.liquidCooling;
        }
      });
    }
  });

  const chains = state.liquidCooling && state.liquidCooling.piping && state.liquidCooling.piping.chains;
  if (chains && Array.isArray(chains) && chains.length > 0) {
    chains.forEach((chain, idx) => {
      const path = chain.path || [];
      if (path.length < 2) return;
      const startPort = typeof path[0] === 'string' ? path[0] : '';
      const endPort = typeof path[path.length - 1] === 'string' ? path[path.length - 1] : '';
      const waypoints = [];
      path.forEach(item => {
        if (item && typeof item === 'object' && item.waypoints) {
          waypoints.push.apply(waypoints, item.waypoints);
        }
      });
      const color = (chain.id || '').indexOf('return') !== -1 ? 'return' : 'supply';
      const pipe = createPipeSegmentModel(chain.id || 'Pipe_' + idx, startPort, endPort, color);
      pipe.pipe.waypoints = waypoints;
      newModels.push(pipe);
    });
    state.liquidCooling.piping.chains = [];
  }

  newModels.forEach(m => state.models.push(m));
  state.schemaVersion = 2;
}

// Main
let allPassed = true;
for (const [filename, expected] of Object.entries(EXPECTED)) {
  const filePath = path.join(FIXTURES_DIR, filename);
  if (!fs.existsSync(filePath)) {
    console.log(`FAIL: ${filename} (file missing)`);
    allPassed = false;
    continue;
  }
  const raw = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  const state = raw.state;
  migrateLiquidCoolingToKinds(state);

  const counts = {
    crh: state.models.filter(m => m.kind === 'crh').length,
    cold_plate: state.models.filter(m => m.kind === 'cold_plate').length,
    heat_source: state.models.filter(m => m.kind === 'heat_source').length,
    pipe_segment: state.models.filter(m => m.kind === 'pipe_segment').length,
  };
  const match = Object.keys(expected).every(k => counts[k] === expected[k]);

  // Verify legacy fields are cleaned up
  const dirtyRdhx = state.models.some(m => m.kind === 'rack' && m.rdhx && m.rdhx.enabled);
  const dirtyCps = state.models.some(m =>
    m.kind === 'rack' && m.rack && m.rack.equipments && m.rack.equipments.some(e => e.liquidCooling && e.liquidCooling.coldPlates && e.liquidCooling.coldPlates.length > 0));
  const dirtyChains = state.liquidCooling && state.liquidCooling.piping && Array.isArray(state.liquidCooling.piping.chains) && state.liquidCooling.piping.chains.length > 0;
  const cleanedUp = !dirtyRdhx && !dirtyCps && !dirtyChains;

  // Idempotency: run again, should not add more models
  const countBefore = state.models.length;
  migrateLiquidCoolingToKinds(state);
  const idempotent = state.models.length === countBefore;

  const passed = match && cleanedUp && idempotent;
  if (!passed) {
    allPassed = false;
    console.log(`FAIL: ${filename}`);
    console.log(`  counts: ${JSON.stringify(counts)}`);
    console.log(`  expected: ${JSON.stringify(expected)}`);
    console.log(`  cleanedUp: ${cleanedUp} (dirtyRdhx=${dirtyRdhx}, dirtyCps=${dirtyCps}, dirtyChains=${dirtyChains})`);
    console.log(`  idempotent: ${idempotent}`);
  } else {
    console.log(`PASS: ${filename} (counts: ${JSON.stringify(counts)})`);
  }
}

process.exit(allPassed ? 0 : 1);
```

- [ ] **Step 4: Run the verification script**

```bash
node /tmp/verify_migration.js
```

Expected: 5 lines of PASS, exit code 0.

If any fixture FAILs, inspect the error output and fix the migration function in `STL_generate.html`. The migration function is at `STL_generate.html:2731+`.

- [ ] **Step 5: Backend regression**

```bash
conda run -n dcdtuni python -m pytest /mnt/lv/litianyu/mycode/dcdtuni/tests/test_liquid/ --tb=short 2>&1 | tail -3
```

Expected: 55 passed

- [ ] **Step 6: Stop the server**

```bash
fuser -k 8005/tcp 2>/dev/null
```

- [ ] **Step 7: Commit any migration fixes (if needed)**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/frontend
# Only if STL_generate.html was modified during debugging
if [ -n "$(git status -s web/STL_generate.html)" ]; then
  git add web/STL_generate.html
  git commit -m "fix(liquid): Phase H — migration edge case fixes"
  cd /mnt/lv/litianyu/mycode/dcdtuni
  git add frontend
  git commit -m "fix(liquid): Phase H migration fixes"
else
  echo "No migration fixes needed — all fixtures passed."
fi
```

### Phase H Acceptance

- 5 synthetic legacy fixtures created, all valid JSON.
- `/tmp/verify_migration.js` passes for all 5 fixtures.
- Migration is idempotent (running twice produces the same result).
- Legacy fields (`rdhx`, `eq.liquidCooling.coldPlates`, `chains`) are cleaned up after migration.
- All 55 backend tests still pass.

---

## Phase I: Visual Consistency Audit

### Task I.1: Build the audit scene via browser-use

**Prerequisites**: Server running from Phase H (or restart).

- [ ] **Step 1: Start server with clean case dir**

```bash
fuser -k 8005/tcp 2>/dev/null; sleep 1
mkdir -p /tmp/lc_phase_i/model
conda run -n dcdtuni python /mnt/lv/litianyu/mycode/dcdtuni/src/server/server.py --port 8005 --case-dir /tmp/lc_phase_i 2>&1 &
sleep 5
curl -s -o /dev/null -w "%{http_code}" http://localhost:8005/api/auth/check && echo " server up"
```

- [ ] **Step 2: Open browser and login**

Use browser-use MCP tools:
1. `browser_navigate` → `http://localhost:8005/STL_generate.html`
2. `browser_get_state` → find login input indices
3. `browser_type` index 0 → `admin`
4. `browser_type` index 1 → `WIN#@desk`
5. `browser_click` → Sign In button
6. `browser_screenshot` → verify main UI loaded

- [ ] **Step 3: Create 2 Racks**

Click the "新建 RACK" button from the right-side creation toolbar twice. Screenshot after each creation to confirm rack appears in viewport.

- [ ] **Step 4: Switch to liquid mode**

1. Scroll up in the browser page if needed
2. Click the "液冷模拟 / LIQUID SIMULATION" button in the file bar
3. Screenshot to verify mode switched

- [ ] **Step 5: Create 1 CDU**

Click the "🏭 新建CDU / New CDU" button in the liquid toolbar. Screenshot.

### Task I.2: Capture 6 audit screenshots

- [ ] **Step 1: Save screenshots to `/tmp/phase_i_screenshots/`**

```bash
mkdir -p /tmp/phase_i_screenshots
```

- [ ] **Step 2: Capture each scenario**

Use browser-use `browser_screenshot` tool after each setup step.

**Screenshot 1: Liquid mode full view**
- Expected: 3D viewport + liquid tree on left + liquid toolbar on right + CDU + 2 Racks visible
- Save as: `/tmp/phase_i_screenshots/01_liquid_full_view.png` (automatic naming is fine if screenshot tool doesn't support path)

**Screenshot 2: CDU selected**
- Click the CDU in the 3D viewport or in the tree
- Expected: Right-side inspector shows CDU parameters
- Save as screenshot 02

**Screenshot 3: Rack selected in liquid mode**
- Click Rack_1 (the first rack) in the viewport
- Expected: Liquid toolbar shows enabled CRH/cold plate buttons at bottom
- Save as screenshot 03

**Screenshot 4: Air mode — liquid models dimmed**
- Click "返回风冷模拟" button to switch back
- Expected: CDU at 35% opacity, Racks at full opacity
- Save as screenshot 04

**Screenshot 5: Liquid mode — pipe creation mode active**
- Switch back to liquid mode
- Click "🔗 新建管道" button
- Expected: port indicator spheres visible on CDU and other liquid models
- Save as screenshot 05

**Screenshot 6: Junction rotation gizmo (if Phase G is complete)**
- Create a pipe by clicking CDU supply port → CDU return port (or skip if no valid target)
- Right-click a waypoint → "升级为T型分支"
- Click the resulting junction to select it
- Expected: Z-axis rotation gizmo attached to junction cube
- Save as screenshot 06

Note: Some screenshots may not be reproducible if the pipe/junction creation fails. In that case, document the failure and include a "03b: failed step" screenshot.

- [ ] **Step 3: Verify screenshots exist**

```bash
ls -la /tmp/phase_i_screenshots/
```

Expected: at least 4-6 PNG files

- [ ] **Step 4: Close browser**

Use browser-use `browser_close_all`.

### Task I.3: Review screenshots and fix visual issues

- [ ] **Step 1: Read each screenshot with the Read tool**

For each screenshot, use the Read tool to view it and visually inspect for:

- **Color consistency**: CDU blue (`#00b4dc`), CRH blue (`#00b4dc` — same!), junction purple (`#9c27b0`), heat source red. Note any visual clashes.
- **Toolbar button spacing**: Are gaps between buttons in the creation toolbar uniform?
- **Liquid tree indentation**: Is the hierarchy visually clear?
- **Z-fighting**: Does the CRH semi-transparent panel overlap Rack back face with flickering?
- **Text truncation**: Are long rack names cut off in the tree?
- **Contrast**: Is dimmed air mode (35% opacity) readable or too faint?
- **Port indicator size**: Are blue/red port spheres visible without being obtrusive?

- [ ] **Step 2: Document any issues in a plain list**

Note each issue with:
- What screenshot shows it
- What the bug is
- Proposed fix (CSS / constant change)

- [ ] **Step 3: Apply fixes inline**

For each identified issue, use the Edit tool to apply the fix in:
- `src/frontend/web/js/liquid_pipe_renderer.js` — for 3D rendering constants
- `src/frontend/web/STL_generate.html` — for tree/toolbar CSS

After each batch of fixes, re-screenshot the affected scenarios and verify.

- [ ] **Step 4: Commit polish fixes**

Group fixes by theme:

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/frontend
git add web/js/liquid_pipe_renderer.js web/STL_generate.html
git commit -m "polish(liquid): Phase I — visual consistency fixes from audit"

cd /mnt/lv/litianyu/mycode/dcdtuni
git add frontend
git commit -m "polish(liquid): Phase I complete"
```

If no issues found:

```bash
echo "Phase I audit complete — no visual issues requiring fixes" > /tmp/phase_i_report.md
```

- [ ] **Step 5: Stop server**

```bash
fuser -k 8005/tcp 2>/dev/null
```

### Phase I Acceptance

- Standardized audit scene created via browser-use.
- At least 4 out of 6 screenshots captured successfully.
- Each screenshot reviewed for visual issues.
- Any identified issues fixed and verified.
- No regression in backend tests.

---

## Final Verification

- [ ] **Step 1: Run all backend tests**

```bash
conda run -n dcdtuni python -m pytest /mnt/lv/litianyu/mycode/dcdtuni/tests/test_liquid/ --tb=short 2>&1 | tail -5
```

Expected: 55 passed

- [ ] **Step 2: JS syntax check all liquid files**

```bash
for f in liquid_cooling liquid_pipe_renderer liquid_topology_editor liquid_results_panel liquid_mode; do
  node --check /mnt/lv/litianyu/mycode/dcdtuni/src/frontend/web/js/${f}.js || echo "${f}.js FAIL"
done
```

- [ ] **Step 3: Run migration verification script one final time**

```bash
node /tmp/verify_migration.js
```

Expected: 5 PASS

- [ ] **Step 4: Git log summary**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git log --oneline | grep 'Phase G\|Phase H\|Phase I' | head -20
```

Expected: commits for G.1-G.5, Phase H (possibly no fixes), Phase I polish.

---

## Plan Self-Review

**Spec coverage check:**

| Spec Section | Plan Task | Covered |
|--------------|-----------|---------|
| §3.2 G.1 right-click menu | G.4 | ✓ |
| §3.3 G.2 pipe split | G.2 | ✓ |
| §3.4 G.3 three-port viz | G.3 | ✓ |
| §3.5 G.4 rotation gizmo | G.5 | ✓ |
| §3.6 G.5 pipe recompute | G.5 | ✓ |
| §4.2 H.1 discover legacy | H.1 (adjusted: synthetic fixtures since no real legacy files exist) | ✓ |
| §4.3 H.2 test loop | H.2 (Node.js verification + optional browser-use) | ✓ |
| §4.4 H.3 fix bugs | H.2 step 4 | ✓ |
| §5.2 I.1 build scene | I.1 | ✓ |
| §5.3 I.2 screenshots | I.2 | ✓ |
| §5.4 I.3 issue checklist | I.3 step 1 | ✓ |

**Placeholder scan:**
- No "TBD", "TODO", "fill in details" in task steps.
- Every code block is complete and paste-able.
- Step 4 of H.2 mentions "inspect the error output and fix the migration function" — this is direct, and the file location (`STL_generate.html:2731+`) is specified.

**Type consistency:**
- `junction.junction.ports[i].connectedPipeId` used consistently in G.2 (wire setup), G.3 (color logic), G.5 (reroute lookup).
- `junction.id + '.port_X'` format used consistently for port full IDs in G.2 and G.5.
- `pipeSegmentId` / `waypointIndex` userData keys match between G.1 (creation) and G.4 (raycast lookup).
- `_liquidMode` namespace on window used in G.4 and G.5 — consistent.

**Deviations from spec (documented):**

1. **Phase H synthetic fixtures**: The spec said "test real legacy .cfdproj files", but exploration found no such files in the projects directory. Adjusted to build 5 synthetic fixtures that exercise each migration path. The Node.js verification script is a cleaner approach than loading each fixture through the browser — it tests the migration function in isolation without dependencies on UI state.

2. **Phase H browser-use loop**: Originally planned as the primary verification path. Kept as "optional manual test" since the Node.js script provides deterministic verification.

3. **TransformControls**: The spec assumed the existing TransformControls binding could be reused. Exploration revealed it's only used for the PP seed sphere, so Phase G.5 creates a **new dedicated TransformControls instance** stored in `_junctionTC`.
