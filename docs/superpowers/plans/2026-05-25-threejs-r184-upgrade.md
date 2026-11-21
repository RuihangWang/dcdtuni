# Three.js r128 → r184 Upgrade + Performance Optimization

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Three.js from r128 to r184 via import maps and add InstancedMesh/BVH/LOD/static-merging optimizations for large-scene performance.

**Architecture:** Replace CDN script tags (lines 6-10 of STL_generate.html) with an ES module import map. A bootstrap `<script type="module">` imports THREE + addons, exposes them as `window.*` globals, and loads the CSG library via top-level await. All downstream script tags convert to `type="module"` so they execute in order after the bootstrap. Performance optimizations are implemented as new JS module files integrated at specific points in the existing rendering pipeline.

**Tech Stack:** Three.js r184 (ESM via import map), three-mesh-bvh (BVH raycasting), existing CSG library (three-csg-ts UMD)

**Spec:** `docs/superpowers/specs/2026-05-25-threejs-r184-upgrade-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/frontend/web/STL_generate.html` | Modify | Import map, module conversion, API renames, integration hooks for perf modules |
| `src/frontend/web/js/instance_manager.js` | Create | InstancedMesh batching for repeated equipment |
| `src/frontend/web/js/bvh_setup.js` | Create | BVH tree computation for raycaster acceleration |
| `src/frontend/web/js/lod_manager.js` | Create | LOD level creation and equipment wrapping |
| `src/frontend/web/js/static_merger.js` | Create | Static geometry merging for non-interactive objects |
| `src/frontend/web/vendor/r184/` | Create | Local ESM fallback copies of Three.js r184 |
| `src/frontend/web/vendor/r128-backup/` | Create | Backup of current r128 files for rollback |

---

## Phase A: Three.js Upgrade

### Task 1: Branch Setup + Baseline Screenshots

**Files:**
- No file changes (git + browser-use operations only)

- [ ] **Step 1: Create feature branch**

```bash
git checkout -b feat/threejs-r184-upgrade
```

- [ ] **Step 2: Back up current r128 vendor files**

```bash
mkdir -p src/frontend/web/vendor/r128-backup
cp src/frontend/web/vendor/three.min.js src/frontend/web/vendor/r128-backup/
cp src/frontend/web/vendor/OrbitControls.js src/frontend/web/vendor/r128-backup/
cp src/frontend/web/vendor/STLLoader.js src/frontend/web/vendor/r128-backup/
cp src/frontend/web/vendor/TransformControls.js src/frontend/web/vendor/r128-backup/
cp src/frontend/web/vendor/BufferGeometryUtils.js src/frontend/web/vendor/r128-backup/
```

- [ ] **Step 3: Capture baseline screenshots on r128**

Start the server, open browser-use, load projects at fixed camera angles, and save screenshots for later comparison.

```bash
# Start server
conda run -n dcdtuni python src/server/server.py --case-dir /tmp/threejs-baseline &
sleep 3

# Use browser-use to load a small project and screenshot
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
# Log in, load case_01 (DMAL_Testbed), screenshot
browser-use screenshot /tmp/baseline-r128-case01.png

# Load a medium project (case_09 STDCT), screenshot
browser-use screenshot /tmp/baseline-r128-case09.png
browser-use close
```

- [ ] **Step 4: Commit baseline**

```bash
git add src/frontend/web/vendor/r128-backup/
git commit -m "chore: back up Three.js r128 vendor files before upgrade"
```

---

### Task 2: Download r184 Local Fallback Files

**Files:**
- Create: `src/frontend/web/vendor/r184/three.module.js`
- Create: `src/frontend/web/vendor/r184/OrbitControls.js`
- Create: `src/frontend/web/vendor/r184/STLLoader.js`
- Create: `src/frontend/web/vendor/r184/TransformControls.js`
- Create: `src/frontend/web/vendor/r184/BufferGeometryUtils.js`

- [ ] **Step 1: Download Three.js r184 ESM files from jsdelivr**

```bash
mkdir -p src/frontend/web/vendor/r184

curl -L -o src/frontend/web/vendor/r184/three.module.js \
  "https://cdn.jsdelivr.net/npm/three@0.184.0/build/three.module.js"

curl -L -o src/frontend/web/vendor/r184/OrbitControls.js \
  "https://cdn.jsdelivr.net/npm/three@0.184.0/examples/jsm/controls/OrbitControls.js"

curl -L -o src/frontend/web/vendor/r184/STLLoader.js \
  "https://cdn.jsdelivr.net/npm/three@0.184.0/examples/jsm/loaders/STLLoader.js"

curl -L -o src/frontend/web/vendor/r184/TransformControls.js \
  "https://cdn.jsdelivr.net/npm/three@0.184.0/examples/jsm/controls/TransformControls.js"

curl -L -o src/frontend/web/vendor/r184/BufferGeometryUtils.js \
  "https://cdn.jsdelivr.net/npm/three@0.184.0/examples/jsm/utils/BufferGeometryUtils.js"
```

If r184 is not available on jsdelivr (version may differ), check the latest version:
```bash
curl -s "https://data.jsdelivr.com/v1/packages/npm/three" | python3 -c "import sys,json; print(json.load(sys.stdin)['tags']['latest'])"
```
Then adjust the version number in all URLs.

- [ ] **Step 2: Verify downloads are valid ES modules**

```bash
head -3 src/frontend/web/vendor/r184/three.module.js
# Expected: starts with ES module syntax (export, import, or comment)

head -3 src/frontend/web/vendor/r184/OrbitControls.js
# Expected: starts with "import { ... } from 'three';" or similar
```

- [ ] **Step 3: Commit local fallback files**

```bash
git add src/frontend/web/vendor/r184/
git commit -m "chore: add Three.js r184 ESM files for local fallback"
```

---

### Task 3: Replace Script Loading with Import Map + Bootstrap Module

**Files:**
- Modify: `src/frontend/web/STL_generate.html:6-14`

This is the most critical task. Replace the old blocking script tags with an import map and a bootstrap ES module.

**Key constraint:** `<script type="module">` is deferred — it runs after HTML parsing. Regular `<script>` tags run immediately (blocking). The bootstrap module uses **top-level await** to load the CSG UMD library, which blocks all subsequent modules from executing until CSG is ready. This guarantees `window.THREE` and CSG are available when downstream modules execute.

- [ ] **Step 1: Replace lines 6-14 of STL_generate.html**

Replace the current script loading block:

```html
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/STLLoader.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/utils/BufferGeometryUtils.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/TransformControls.js"></script>
    <script src="https://unpkg.com/polygon-clipping@0.15.3/dist/polygon-clipping.umd.js"></script>
    <script>window.require = function(name){ if (name === 'three') return window.THREE; throw new Error('require: unknown module ' + name); };</script>
    <script src="vendor/three-csg-ts.umd.js"></script>
    <script>try { delete window.require; } catch(_) { window.require = undefined; }</script>
```

With:

```html
    <script src="https://unpkg.com/polygon-clipping@0.15.3/dist/polygon-clipping.umd.js"></script>
    <script type="importmap">
    {
      "imports": {
        "three": "https://cdn.jsdelivr.net/npm/three@0.184.0/build/three.module.js",
        "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.184.0/examples/jsm/"
      }
    }
    </script>
    <script type="module">
      import * as THREE from 'three';
      import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
      import { STLLoader } from 'three/addons/loaders/STLLoader.js';
      import { TransformControls } from 'three/addons/controls/TransformControls.js';
      import * as BufferGeometryUtils from 'three/addons/utils/BufferGeometryUtils.js';

      window.THREE = THREE;
      window.THREE.OrbitControls = OrbitControls;
      window.THREE.STLLoader = STLLoader;
      window.THREE.TransformControls = TransformControls;
      window.THREE.BufferGeometryUtils = BufferGeometryUtils;

      // Load CSG library (needs window.THREE to exist)
      window.require = function(name) {
        if (name === 'three') return window.THREE;
        throw new Error('require: unknown module ' + name);
      };
      await new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'vendor/three-csg-ts.umd.js';
        s.onload = resolve;
        s.onerror = reject;
        document.head.appendChild(s);
      });
      try { delete window.require; } catch(_) { window.require = undefined; }
    </script>
```

Note: `polygon-clipping` stays as a regular `<script>` — it doesn't depend on Three.js and can load immediately.

- [ ] **Step 2: Verify import map syntax is valid JSON**

Open the file and check the import map JSON is correctly formatted (no trailing commas, proper quoting). Import map syntax errors are silent — the browser just ignores the map.

- [ ] **Step 3: Commit the import map change**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat: replace Three.js r128 script tags with r184 import map + bootstrap module"
```

---

### Task 4: Convert Downstream Scripts to ES Modules

**Files:**
- Modify: `src/frontend/web/STL_generate.html:1133-1144`

All `<script src>` tags after the bootstrap must become `<script type="module" src>` so they execute in document order **after** the bootstrap module (which sets `window.THREE`). The main inline `<script>` at line 1144 must also become `<script type="module">`.

**Critical timing fix:** The main script has `window.addEventListener('DOMContentLoaded', ...)` at line 4233. When a `<script type="module">` runs, `DOMContentLoaded` has already fired (modules are deferred). The listener must be replaced with a direct call or a readyState check.

- [ ] **Step 1: Convert external script tags to modules**

Replace lines 1133-1143:

```html
<script src="theme/theme-loader.js"></script>
<script src="room_2d_editor.js"></script>
<script src="js/liquid_cooling.js"></script>
<script src="js/liquid_graphgen.js"></script>
<script src="js/liquid_solve_monitor.js"></script>
<script src="js/liquid_pipe_renderer.js"></script>
<script src="js/liquid_topology_editor.js"></script>
<script src="js/liquid_results_panel.js"></script>
<script src="js/liquid_mode.js"></script>
<script src="js/patch_filter.js"></script>
<script src="js/patch_selection.js"></script>
<script>
```

With:

```html
<script type="module" src="theme/theme-loader.js"></script>
<script type="module" src="room_2d_editor.js"></script>
<script type="module" src="js/liquid_cooling.js"></script>
<script type="module" src="js/liquid_graphgen.js"></script>
<script type="module" src="js/liquid_solve_monitor.js"></script>
<script type="module" src="js/liquid_pipe_renderer.js"></script>
<script type="module" src="js/liquid_topology_editor.js"></script>
<script type="module" src="js/liquid_results_panel.js"></script>
<script type="module" src="js/liquid_mode.js"></script>
<script type="module" src="js/patch_filter.js"></script>
<script type="module" src="js/patch_selection.js"></script>
<script type="module">
```

- [ ] **Step 2: Fix DOMContentLoaded timing**

At line ~4233, the initialization is inside a `DOMContentLoaded` listener. Since the module is deferred, this event has already fired. Replace:

```javascript
window.addEventListener('DOMContentLoaded', async () => {
```

With:

```javascript
(async () => {
```

And verify the matching closing `});` becomes `})();`. The content of the handler stays the same — only the wrapping changes. Since modules are deferred, the DOM is guaranteed to be ready.

Also check the `window.addEventListener('load', ...)` at line ~13986. `load` fires after all resources (images, stylesheets) are loaded and may still fire after a module script. Test whether this listener still works; if not, apply the same readyState-check pattern:

```javascript
if (document.readyState === 'complete') {
  initToolbar();
} else {
  window.addEventListener('load', initToolbar);
}
```

- [ ] **Step 3: Check for bare function declarations used cross-file**

Module scripts scope top-level declarations. Search for functions defined in the main script that are called from the external JS files (liquid_mode.js, etc.):

```bash
# Extract function names from external JS files that might reference main script functions
grep -h 'window\.\|globalThis\.' src/frontend/web/js/*.js src/frontend/web/room_2d_editor.js | head -20
```

If any external JS file calls a function defined in the main script WITHOUT the `window.` prefix, that function must be added to `window.*` in the main script. This is unlikely since the codebase already uses `window.*` extensively, but verify.

- [ ] **Step 4: Commit module conversion**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "feat: convert all script tags to ES modules for Three.js r184 compatibility"
```

---

### Task 5: API Migration — Encoding → ColorSpace

**Files:**
- Modify: `src/frontend/web/STL_generate.html:1414,1851,1905`

Three.js r152 renamed the encoding API. Three exact replacements needed.

- [ ] **Step 1: Replace renderer.outputEncoding**

At line 1414, replace:

```javascript
renderer.outputEncoding = THREE.sRGBEncoding;
```

With:

```javascript
renderer.outputColorSpace = THREE.SRGBColorSpace;
```

- [ ] **Step 2: Replace texture encoding at line 1851**

Replace:

```javascript
texture.encoding = isDataMap ? THREE.LinearEncoding : THREE.sRGBEncoding;
```

With:

```javascript
texture.colorSpace = isDataMap ? THREE.LinearSRGBColorSpace : THREE.SRGBColorSpace;
```

- [ ] **Step 3: Replace texture encoding at line 1905**

Replace:

```javascript
tex.encoding = isNormalMap ? THREE.LinearEncoding : THREE.sRGBEncoding;
```

With:

```javascript
tex.colorSpace = isNormalMap ? THREE.LinearSRGBColorSpace : THREE.SRGBColorSpace;
```

- [ ] **Step 4: Search for any other encoding references**

```bash
grep -n 'Encoding\|\.encoding' src/frontend/web/STL_generate.html | grep -i 'srgb\|linear\|output'
grep -rn 'Encoding\|\.encoding' src/frontend/web/js/*.js | grep -i 'srgb\|linear\|output'
```

If any additional occurrences found, apply the same rename pattern.

- [ ] **Step 5: Commit encoding migration**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "fix: rename Three.js encoding API to colorSpace for r184 compatibility"
```

---

### Task 6: API Migration — BufferGeometryUtils

**Files:**
- Modify: `src/frontend/web/STL_generate.html:9659-9660`

Three.js r151 renamed `mergeBufferGeometries()` to `mergeGeometries()`.

- [ ] **Step 1: Replace mergeBufferGeometries at line 9659**

Replace:

```javascript
const mergedGeo = THREE.BufferGeometryUtils.mergeBufferGeometries(tempGeometries, false);
```

With:

```javascript
const mergedGeo = THREE.BufferGeometryUtils.mergeGeometries(tempGeometries, false);
```

- [ ] **Step 2: Update the error message at line 9660**

Replace:

```javascript
if (!mergedGeo) throw new Error('mergeBufferGeometries returned null');
```

With:

```javascript
if (!mergedGeo) throw new Error('mergeGeometries returned null');
```

- [ ] **Step 3: Search for any other mergeBuffer references**

```bash
grep -n 'mergeBuffer' src/frontend/web/STL_generate.html
grep -rn 'mergeBuffer' src/frontend/web/js/*.js
```

Replace any additional occurrences found.

- [ ] **Step 4: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "fix: rename mergeBufferGeometries to mergeGeometries for r184"
```

---

### Task 7: WebGL Detection Cleanup

**Files:**
- Modify: `src/frontend/web/STL_generate.html:1391-1400`

Three.js r163 dropped WebGL 1 support. Simplify the detection to WebGL 2 only.

- [ ] **Step 1: Simplify WebGL detection**

At lines 1391-1400, find the WebGL detection block. Replace the `getContext('webgl2') || getContext('webgl')` fallback with WebGL 2 only:

```javascript
const gl = testCanvas.getContext('webgl2');
```

Remove any WebGL 1 fallback paths. If the code has a separate error message for "WebGL not supported", keep it — just remove the WebGL 1 branch.

- [ ] **Step 2: Commit**

```bash
git add src/frontend/web/STL_generate.html
git commit -m "fix: remove WebGL 1 fallback, require WebGL 2 for Three.js r184"
```

---

### Task 8: Smoke Test — Verify r184 Loads and Renders

**Files:**
- No file changes (browser-use verification only)

- [ ] **Step 1: Start the server**

```bash
fuser -k 8005/tcp 2>/dev/null
fuser -k 8006/tcp 2>/dev/null
conda run -n dcdtuni python src/server/server.py --case-dir /tmp/threejs-test &
sleep 3
```

- [ ] **Step 2: Open browser and check console**

```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
browser-use screenshot /tmp/r184-smoke-test.png
```

Read the screenshot. The scene should render (grid visible, axes visible). Check browser console for errors:

```bash
browser-use eval "JSON.stringify(window._consoleErrors || 'no error capture')"
```

Common errors to look for and fix:
- `THREE.sRGBEncoding is undefined` → missed an encoding rename
- `THREE.BufferGeometryUtils.mergeBufferGeometries is not a function` → missed a mergeBuffer rename
- `THREE is not defined` → module loading order issue, bootstrap module failed
- CSG errors → three-csg-ts incompatible with r184

- [ ] **Step 3: Load a small project and verify interaction**

Log in (admin / WIN#@desk), set a case directory, load case_01 (DMAL_Testbed). Test:
- Camera orbit (click + drag)
- Zoom (scroll)
- Click to select an object
- Move an object (drag)
- Undo (Ctrl+Z)

```bash
browser-use screenshot /tmp/r184-case01-loaded.png
```

- [ ] **Step 4: Test CSG operations**

Load a project with partition walls (any project with wall cutouts). Verify the partition walls render correctly — CSG boolean operations must produce correct geometry.

- [ ] **Step 5: Test 2D topview editor**

```bash
browser-use eval "window.room2d && window.room2d.enterEditor()"
browser-use screenshot /tmp/r184-room2d.png
```

Verify the 2D editor renders correctly.

- [ ] **Step 6: Fix any issues found, then commit**

If smoke test reveals issues, fix them and commit:

```bash
git add -u
git commit -m "fix: resolve Three.js r184 smoke test issues"
```

---

### Task 9: Visual Regression Check

**Files:**
- No file changes (comparison only)

- [ ] **Step 1: Compare r128 baseline with r184 screenshots**

Read both sets of screenshots (baseline from Task 1, current from Task 8). Compare:
- Material colors and shading
- Transparency / alpha blending
- Light intensity and direction
- Grid and axes rendering
- Text/label rendering

- [ ] **Step 2: Adjust lighting if needed**

Three.js r155 changed to physically correct light falloff. If scenes appear darker or brighter:

Check the LightKit setup (search for `DirectionalLight` creation). Adjust `intensity` values if needed. The LightKit uses 5 directional lights — directional lights are NOT affected by the `useLegacyLights` change, but verify.

If adjustment is needed, commit:

```bash
git add src/frontend/web/STL_generate.html
git commit -m "fix: adjust light intensities for Three.js r184 physically correct model"
```

- [ ] **Step 3: Remove old vendor files**

After confirming r184 works, remove the old r128 vendor addon files (the core three.min.js is in r128-backup):

```bash
rm src/frontend/web/vendor/three.min.js
rm src/frontend/web/vendor/OrbitControls.js
rm src/frontend/web/vendor/STLLoader.js
rm src/frontend/web/vendor/TransformControls.js
rm src/frontend/web/vendor/BufferGeometryUtils.js
git add -u
git commit -m "chore: remove old r128 vendor addon files, replaced by import map"
```

---

## Phase B: Performance Optimizations

### Task 10: BVH-Accelerated Raycasting

**Files:**
- Create: `src/frontend/web/js/bvh_setup.js`
- Modify: `src/frontend/web/STL_generate.html` (import map + script tag + geometry build hook)

BVH acceleration is the simplest performance optimization to integrate and provides immediate raycasting speedup. It requires no changes to the raycaster call sites — the library monkey-patches `THREE.Mesh.prototype.raycast`.

- [ ] **Step 1: Add three-mesh-bvh to the import map**

In the import map (line ~7 after Task 3), add an entry:

```json
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.184.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.184.0/examples/jsm/",
    "three-mesh-bvh": "https://cdn.jsdelivr.net/npm/three-mesh-bvh@latest/build/index.module.js"
  }
}
```

- [ ] **Step 2: Add three-mesh-bvh to the bootstrap module**

In the bootstrap `<script type="module">` block, after the Three.js imports, add:

```javascript
import { computeBoundsTree, disposeBoundsTree, acceleratedRaycast }
  from 'three-mesh-bvh';

THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
THREE.Mesh.prototype.raycast = acceleratedRaycast;
```

This globally patches all meshes to use BVH raycasting.

- [ ] **Step 3: Create bvh_setup.js**

```javascript
// Compute BVH for all mesh geometries in a group.
// Call after buildGeometryData() completes.
window.computeSceneBVH = function(rootGroup) {
  rootGroup.traverse(function(obj) {
    if (obj.isMesh && obj.geometry && !obj.geometry.boundsTree) {
      try {
        obj.geometry.computeBoundsTree();
      } catch (e) {
        console.warn('[BVH] Failed for', obj.name, e.message);
      }
    }
  });
};

window.disposeMeshBVH = function(mesh) {
  if (mesh && mesh.geometry && mesh.geometry.boundsTree) {
    mesh.geometry.disposeBoundsTree();
  }
};
```

- [ ] **Step 4: Add script tag for bvh_setup.js**

In STL_generate.html, after the other JS module script tags (around line 1143), add:

```html
<script type="module" src="js/bvh_setup.js"></script>
```

- [ ] **Step 5: Hook into geometry rebuild**

In STL_generate.html, find the `doRebuildGeometry()` function (line ~9762). At the end of this function, after all geometry is built, add:

```javascript
if (window.computeSceneBVH) window.computeSceneBVH(globalGroup);
```

Also in `_applyProjectData()` (line ~16572), after the geometry is fully loaded, add the same call.

- [ ] **Step 6: Test raycaster performance**

```bash
browser-use open http://localhost:8005/STL_generate.html
# Load a large project, test mouse hover and click responsiveness
browser-use screenshot /tmp/bvh-test.png
```

- [ ] **Step 7: Commit**

```bash
git add src/frontend/web/js/bvh_setup.js src/frontend/web/STL_generate.html
git commit -m "feat: add BVH-accelerated raycasting via three-mesh-bvh"
```

---

### Task 11: InstancedMesh Manager for Repeated Equipment

**Files:**
- Create: `src/frontend/web/js/instance_manager.js`
- Modify: `src/frontend/web/STL_generate.html` (script tag + integration into buildGeometryData and selection system)

This is the highest-impact performance optimization. The InstanceManager groups identical geometries into `THREE.InstancedMesh` objects, reducing draw calls from O(n) to O(unique_types).

- [ ] **Step 1: Create instance_manager.js**

```javascript
(function() {
  'use strict';

  class InstanceManager {
    constructor(parentGroup) {
      this._parentGroup = parentGroup;
      this._batches = new Map();      // geometryKey -> { mesh: InstancedMesh, slots: Map<modelId, slotIndex>, freeSlots: [] }
      this._extracted = new Map();    // modelId -> { batch, slotIndex, standaloneMesh }
    }

    // Generate a key from geometry type + dimensions for grouping identical models
    static geometryKey(model) {
      const d = model.dimensions || {};
      return `${model.type}_${d.width}_${d.height}_${d.depth}`;
    }

    // Register a model for instancing. Returns true if batched, false if not batchable.
    addInstance(modelId, geometryKey, geometry, material, matrix4) {
      let batch = this._batches.get(geometryKey);

      if (!batch) {
        // First instance of this geometry type — create InstancedMesh with initial capacity
        const capacity = 64;
        const instMesh = new THREE.InstancedMesh(geometry, material, capacity);
        instMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        instMesh.count = 0;
        instMesh.frustumCulled = false;
        instMesh.name = 'instanced_' + geometryKey;
        instMesh.userData._isInstanceBatch = true;
        this._parentGroup.add(instMesh);

        batch = {
          mesh: instMesh,
          geometry: geometry,
          material: material,
          capacity: capacity,
          slots: new Map(),
          freeSlots: []
        };
        this._batches.set(geometryKey, batch);
      }

      // Grow capacity if needed
      if (batch.mesh.count >= batch.capacity && batch.freeSlots.length === 0) {
        this._growBatch(batch);
      }

      // Assign slot
      const slotIndex = batch.freeSlots.length > 0
        ? batch.freeSlots.pop()
        : batch.mesh.count++;
      batch.mesh.setMatrixAt(slotIndex, matrix4);
      batch.mesh.instanceMatrix.needsUpdate = true;
      batch.slots.set(modelId, slotIndex);

      return true;
    }

    // Update an instance's transform (e.g., after move/rotate)
    updateInstance(modelId, matrix4) {
      for (const [, batch] of this._batches) {
        if (batch.slots.has(modelId)) {
          const slot = batch.slots.get(modelId);
          batch.mesh.setMatrixAt(slot, matrix4);
          batch.mesh.instanceMatrix.needsUpdate = true;
          return true;
        }
      }
      return false;
    }

    // Extract an instance to a standalone mesh (for TransformControls / selection).
    // The instance slot is hidden (scale 0,0,0) but reserved.
    extractForEditing(modelId) {
      for (const [key, batch] of this._batches) {
        if (batch.slots.has(modelId)) {
          const slot = batch.slots.get(modelId);
          const mat4 = new THREE.Matrix4();
          batch.mesh.getMatrixAt(slot, mat4);

          // Hide the instance slot
          const zeroMat = new THREE.Matrix4().makeScale(0, 0, 0);
          batch.mesh.setMatrixAt(slot, zeroMat);
          batch.mesh.instanceMatrix.needsUpdate = true;

          // Create standalone mesh
          const standalone = new THREE.Mesh(batch.geometry, batch.material.clone());
          standalone.applyMatrix4(mat4);
          standalone.userData._extractedFrom = { batchKey: key, modelId, slot };
          this._parentGroup.add(standalone);

          this._extracted.set(modelId, { batch, slotIndex: slot, standaloneMesh: standalone });
          return standalone;
        }
      }
      return null;
    }

    // Merge a standalone mesh back into its batch after editing
    reinsert(modelId) {
      const ext = this._extracted.get(modelId);
      if (!ext) return false;

      const mat4 = new THREE.Matrix4();
      ext.standaloneMesh.updateMatrixWorld(true);
      mat4.copy(ext.standaloneMesh.matrixWorld);

      ext.batch.mesh.setMatrixAt(ext.slotIndex, mat4);
      ext.batch.mesh.instanceMatrix.needsUpdate = true;

      this._parentGroup.remove(ext.standaloneMesh);
      ext.standaloneMesh.geometry.dispose();
      ext.standaloneMesh.material.dispose();
      this._extracted.delete(modelId);

      return true;
    }

    // Remove an instance permanently (delete equipment)
    removeInstance(modelId) {
      // Check extracted first
      if (this._extracted.has(modelId)) {
        const ext = this._extracted.get(modelId);
        this._parentGroup.remove(ext.standaloneMesh);
        ext.batch.freeSlots.push(ext.slotIndex);
        ext.batch.slots.delete(modelId);
        this._extracted.delete(modelId);
        return true;
      }

      for (const [, batch] of this._batches) {
        if (batch.slots.has(modelId)) {
          const slot = batch.slots.get(modelId);
          const zeroMat = new THREE.Matrix4().makeScale(0, 0, 0);
          batch.mesh.setMatrixAt(slot, zeroMat);
          batch.mesh.instanceMatrix.needsUpdate = true;
          batch.freeSlots.push(slot);
          batch.slots.delete(modelId);
          return true;
        }
      }
      return false;
    }

    // Set per-instance color (for selection highlighting)
    setInstanceColor(modelId, color) {
      for (const [, batch] of this._batches) {
        if (batch.slots.has(modelId)) {
          const slot = batch.slots.get(modelId);
          if (!batch.mesh.instanceColor) {
            batch.mesh.instanceColor = new THREE.InstancedBufferAttribute(
              new Float32Array(batch.capacity * 3).fill(1), 3
            );
          }
          batch.mesh.setColorAt(slot, color);
          batch.mesh.instanceColor.needsUpdate = true;
          return true;
        }
      }
      return false;
    }

    // Clear all batches (for project reload)
    clear() {
      for (const [, ext] of this._extracted) {
        this._parentGroup.remove(ext.standaloneMesh);
      }
      this._extracted.clear();

      for (const [, batch] of this._batches) {
        this._parentGroup.remove(batch.mesh);
        batch.mesh.dispose();
      }
      this._batches.clear();
    }

    // Double the capacity of a batch
    _growBatch(batch) {
      const newCapacity = batch.capacity * 2;
      const newMesh = new THREE.InstancedMesh(batch.geometry, batch.material, newCapacity);
      newMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      newMesh.count = batch.mesh.count;
      newMesh.name = batch.mesh.name;
      newMesh.frustumCulled = false;
      newMesh.userData._isInstanceBatch = true;

      // Copy existing matrices
      for (let i = 0; i < batch.mesh.count; i++) {
        const m = new THREE.Matrix4();
        batch.mesh.getMatrixAt(i, m);
        newMesh.setMatrixAt(i, m);
      }
      newMesh.instanceMatrix.needsUpdate = true;

      this._parentGroup.remove(batch.mesh);
      batch.mesh.dispose();
      this._parentGroup.add(newMesh);
      batch.mesh = newMesh;
      batch.capacity = newCapacity;
    }

    get stats() {
      let totalInstances = 0;
      let totalBatches = 0;
      for (const [, batch] of this._batches) {
        totalBatches++;
        totalInstances += batch.slots.size;
      }
      return { batches: totalBatches, instances: totalInstances, extracted: this._extracted.size };
    }
  }

  window.InstanceManager = InstanceManager;
})();
```

- [ ] **Step 2: Add script tag**

In STL_generate.html, add before the main script block:

```html
<script type="module" src="js/instance_manager.js"></script>
```

- [ ] **Step 3: Initialize InstanceManager in the main script**

In the main script, near where `globalGroup` is created (line ~1807), add:

```javascript
window._instanceManager = new InstanceManager(globalGroup);
```

- [ ] **Step 4: Integrate into buildGeometryData()**

In `buildGeometryData()` (line ~9418), the function iterates over models and creates meshes. For each rack/ACU model, instead of adding the mesh directly to `globalGroup`, check if it can be instanced:

After a mesh group is created for a model, add this logic:

```javascript
const geoKey = InstanceManager.geometryKey(model);
if (model.type === 'rack' || model.type === 'acu') {
  const mat4 = new THREE.Matrix4();
  mat4.compose(
    new THREE.Vector3(model.position.x, model.position.y, model.position.z),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(0, THREE.MathUtils.degToRad(model.rotation || 0), 0)),
    new THREE.Vector3(1, 1, 1)
  );
  if (window._instanceManager.addInstance(model.id, geoKey, meshGroup.children[0].geometry, meshGroup.children[0].material, mat4)) {
    // Instanced — don't add the individual mesh group
    continue;
  }
}
// Not instanced — add normally
globalGroup.add(meshGroup);
```

This integration requires careful study of the actual `buildGeometryData()` code to find the right insertion point. The model iteration pattern and mesh creation differ per model type. Start with racks only (most repeated type), verify it works, then extend to ACUs.

- [ ] **Step 5: Integrate with selection system**

Find the selection handler (mousedown/click event on the canvas). When a user clicks an InstancedMesh, use `intersection.instanceId` to identify which instance was hit:

```javascript
if (intersected.object.userData._isInstanceBatch) {
  const instanceId = intersected.instanceId;
  // Look up which modelId this instance slot belongs to
  // Extract it for editing
  const standalone = window._instanceManager.extractForEditing(modelId);
  // Attach TransformControls to standalone
}
```

When selection is cleared (deselect), reinsert:

```javascript
window._instanceManager.reinsert(modelId);
```

- [ ] **Step 6: Integrate with delete and move operations**

When a model is deleted, call `window._instanceManager.removeInstance(modelId)`.
When a model is moved (drag end), call `window._instanceManager.updateInstance(modelId, newMatrix)` or use the extract/reinsert pattern.

- [ ] **Step 7: Test instancing**

Load a project with many racks (case_07 CMI_China, 20 racks). Verify:
- All racks render at correct positions
- Clicking a rack selects it
- Moving a rack updates its position
- Deleting a rack removes it
- Undo works

```bash
browser-use screenshot /tmp/instancing-test.png
```

- [ ] **Step 8: Commit**

```bash
git add src/frontend/web/js/instance_manager.js src/frontend/web/STL_generate.html
git commit -m "feat: add InstancedMesh batching for repeated equipment (racks, ACUs)"
```

---

### Task 12: LOD (Level of Detail) Manager

**Files:**
- Create: `src/frontend/web/js/lod_manager.js`
- Modify: `src/frontend/web/STL_generate.html` (script tag + integration hook)

LOD reduces triangle count for distant objects. Each equipment type gets 2-3 geometry detail levels. The `THREE.LOD` object automatically swaps geometry based on camera distance.

- [ ] **Step 1: Create lod_manager.js**

```javascript
(function() {
  'use strict';

  const LOD_DISTANCES = {
    high: 0,     // Full detail: 0 - 5m
    medium: 5,   // Simplified: 5 - 15m
    low: 15      // Box only: 15m+
  };

  // Create a simplified box geometry for LOD low level
  function createLowLOD(width, height, depth, material) {
    const geo = new THREE.BoxGeometry(width, height, depth);
    return new THREE.Mesh(geo, material);
  }

  // Create a medium-detail geometry (box with front face differentiated)
  function createMediumLOD(width, height, depth, material) {
    const geo = new THREE.BoxGeometry(width, height, depth, 1, 2, 1);
    return new THREE.Mesh(geo, material);
  }

  // Wrap an existing high-detail mesh group in a THREE.LOD object
  window.wrapInLOD = function(highDetailGroup, model) {
    const d = model.dimensions || {};
    if (!d.width || !d.height || !d.depth) return highDetailGroup;

    const lod = new THREE.LOD();
    lod.name = highDetailGroup.name;
    lod.userData = Object.assign({}, highDetailGroup.userData);

    // Copy position/rotation from the high-detail group
    lod.position.copy(highDetailGroup.position);
    lod.rotation.copy(highDetailGroup.rotation);
    highDetailGroup.position.set(0, 0, 0);
    highDetailGroup.rotation.set(0, 0, 0);

    // Get material from the first mesh child
    let baseMaterial = null;
    highDetailGroup.traverse(function(child) {
      if (!baseMaterial && child.isMesh && child.material) {
        baseMaterial = child.material;
      }
    });

    if (!baseMaterial) return highDetailGroup;

    const medMesh = createMediumLOD(d.width, d.height, d.depth, baseMaterial);
    const lowMesh = createLowLOD(d.width, d.height, d.depth, baseMaterial);

    lod.addLevel(highDetailGroup, LOD_DISTANCES.high);
    lod.addLevel(medMesh, LOD_DISTANCES.medium);
    lod.addLevel(lowMesh, LOD_DISTANCES.low);

    return lod;
  };

  // Force LOD update for the entire scene
  window.updateSceneLOD = function(camera, rootGroup) {
    rootGroup.traverse(function(obj) {
      if (obj.isLOD) {
        obj.update(camera);
      }
    });
  };
})();
```

- [ ] **Step 2: Add script tag**

```html
<script type="module" src="js/lod_manager.js"></script>
```

- [ ] **Step 3: Integrate into geometry building**

In `buildGeometryData()` or the individual render functions (`renderRackModel`, `renderBoxModel`), after creating the high-detail mesh group, wrap it:

```javascript
let meshGroup = renderRackModel(model);
if (window.wrapInLOD) {
  meshGroup = window.wrapInLOD(meshGroup, model);
}
globalGroup.add(meshGroup);
```

- [ ] **Step 4: Add LOD update to render loop**

In `_doRender()` (line ~32138), before the render call, add:

```javascript
if (window.updateSceneLOD) window.updateSceneLOD(camera, globalGroup);
```

- [ ] **Step 5: Test LOD transitions**

Load a project, zoom in and out. Verify:
- Close objects show full detail
- Distant objects show simplified geometry
- No visual popping (transitions should be smooth)

- [ ] **Step 6: Commit**

```bash
git add src/frontend/web/js/lod_manager.js src/frontend/web/STL_generate.html
git commit -m "feat: add LOD system for distant equipment simplification"
```

---

### Task 13: Static Geometry Merging

**Files:**
- Create: `src/frontend/web/js/static_merger.js`
- Modify: `src/frontend/web/STL_generate.html` (script tag + hook after geometry build)

Merge non-interactive objects (room walls, floor, ceiling) into single meshes per material to reduce draw calls.

- [ ] **Step 1: Create static_merger.js**

```javascript
(function() {
  'use strict';

  const STATIC_TYPES = new Set(['room', 'floor', 'ceiling', 'raisedFloor']);

  // Merge all static geometry children in rootGroup by material.
  // Non-static objects are left untouched.
  window.mergeStaticGeometry = function(rootGroup) {
    const byMaterial = new Map(); // material.uuid -> { material, geometries: [], meshes: [] }
    const toRemove = [];

    rootGroup.traverse(function(obj) {
      if (!obj.isMesh) return;
      const model = obj.userData.model || (obj.parent && obj.parent.userData.model);
      if (!model || !STATIC_TYPES.has(model.type)) return;

      const key = obj.material.uuid;
      if (!byMaterial.has(key)) {
        byMaterial.set(key, { material: obj.material, geometries: [], meshes: [] });
      }

      // Clone geometry and apply world transform
      const geo = obj.geometry.clone();
      obj.updateMatrixWorld(true);
      geo.applyMatrix4(obj.matrixWorld);
      byMaterial.get(key).geometries.push(geo);
      byMaterial.get(key).meshes.push(obj);
      toRemove.push(obj);
    });

    for (const [, group] of byMaterial) {
      if (group.geometries.length < 2) continue;

      const merged = THREE.BufferGeometryUtils.mergeGeometries(group.geometries, false);
      if (!merged) continue;

      const mergedMesh = new THREE.Mesh(merged, group.material);
      mergedMesh.name = 'static_merged';
      mergedMesh.userData._isStaticMerged = true;
      rootGroup.add(mergedMesh);

      // Remove individual meshes
      for (const mesh of group.meshes) {
        if (mesh.parent) mesh.parent.remove(mesh);
      }

      // Dispose cloned geometries
      for (const geo of group.geometries) geo.dispose();
    }
  };
})();
```

- [ ] **Step 2: Add script tag**

```html
<script type="module" src="js/static_merger.js"></script>
```

- [ ] **Step 3: Hook into geometry rebuild**

In `doRebuildGeometry()` (line ~9762), after all geometry is built, call:

```javascript
if (window.mergeStaticGeometry) window.mergeStaticGeometry(globalGroup);
```

Call this BEFORE `computeSceneBVH` so the merged meshes also get BVH trees.

- [ ] **Step 4: Test**

Load a project, verify room walls and floor still render correctly. Verify they cannot be individually selected (they are merged).

- [ ] **Step 5: Commit**

```bash
git add src/frontend/web/js/static_merger.js src/frontend/web/STL_generate.html
git commit -m "feat: merge static geometry (walls, floor, ceiling) to reduce draw calls"
```

---

### Task 14: Performance Benchmark

**Files:**
- No file changes (measurement only)

- [ ] **Step 1: Measure FPS on large scene**

```bash
browser-use open http://localhost:8005/STL_generate.html
# Load case_10 (Base604, 142 racks) or case_15 (CMI-Guichou, 238 racks)
```

Add a temporary FPS counter via browser-use:

```bash
browser-use eval "
  let _fpsFrames = 0, _fpsLast = performance.now();
  const _origRender = window.requestRender;
  window.requestRender = function() {
    _fpsFrames++;
    const now = performance.now();
    if (now - _fpsLast >= 1000) {
      console.log('FPS:', _fpsFrames);
      document.title = 'FPS: ' + _fpsFrames;
      _fpsFrames = 0;
      _fpsLast = now;
    }
    return _origRender.apply(this, arguments);
  };
"
```

- [ ] **Step 2: Test interaction performance**

Orbit the camera with mouse drag. Measure:
- FPS during orbit (should be 30+ on a 200-rack scene)
- Time to load the project (should be < 5s for 142 racks)
- Time to select a rack (should be < 200ms)

- [ ] **Step 3: Log InstanceManager stats**

```bash
browser-use eval "JSON.stringify(window._instanceManager && window._instanceManager.stats)"
```

Expected output: `{ batches: 5-10, instances: 142+, extracted: 0 }` for a 142-rack scene.

- [ ] **Step 4: Compare with baseline**

Compare FPS numbers against the r128 baseline. Document the improvement:
- Before: X FPS during orbit on case_10
- After: Y FPS during orbit on case_10
- Improvement: Y/X ratio

- [ ] **Step 5: Final commit and summary**

```bash
git add -A
git status
# If any uncommitted changes, commit them
git commit -m "docs: add Three.js r184 upgrade performance benchmark results"
```
