# Three.js r128 → r184 Upgrade + Large-Scene Performance Optimization

**Date**: 2026-05-25
**Status**: Design approved, pending implementation plan

## Problem

The frontend (`STL_generate.html`, ~32,000 lines) uses Three.js r128 (Feb 2021), which is 56 versions behind the current release (r184). Large scenes with 100+ racks suffer from poor frame rates across all operations: project loading, camera orbit/zoom, and editing (move/add/delete).

The performance bottleneck is primarily **draw call count** — each equipment model is a separate `THREE.Mesh`, causing hundreds of GPU state switches per frame — combined with **O(n) raycasting** over all scene triangles.

## Decision

**Approach C**: Full upgrade to Three.js r184 using import maps (no build step), plus four application-level performance optimizations.

Alternatives considered:
- **Approach A** (perf-only on r128): Rejected — stays on unsupported version.
- **Approach B** (upgrade to r149): Rejected — user prefers fully modern codebase.

Module loading via **import maps** (not Vite) because the project uses a Python server, a monolithic HTML file, and has no existing npm/Node toolchain.

## Architecture

### 1. Import Map Loading

Replace global script tags with an ES module import map. Expose THREE and addons as `window.*` globals so the existing 32k lines of inline code and JS modules continue working without refactoring.

```html
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
  window.OrbitControls = OrbitControls;
  window.STLLoader = STLLoader;
  window.TransformControls = TransformControls;
  window.BufferGeometryUtils = BufferGeometryUtils;
</script>
```

**Local fallback strategy**: Download r184 ESM files (`three.module.js` + addon ESM files) into `vendor/r184/` and serve them from the Python server. The import map can be switched to point to local paths (`/vendor/r184/...`) instead of CDN if offline use is needed. The old UMD-style vendor addon files (`vendor/OrbitControls.js`, etc.) are removed since r184 addons are ESM-only.

### 2. API Migration (r128 → r184)

#### High impact (code changes required)

| Old (r128) | New (r184) | Occurrences |
|---|---|---|
| `THREE.sRGBEncoding` | `THREE.SRGBColorSpace` | ~3 |
| `THREE.LinearEncoding` | `THREE.LinearSRGBColorSpace` | ~2 |
| `renderer.outputEncoding` | `renderer.outputColorSpace` | 1 |
| `texture.encoding` | `texture.colorSpace` | any occurrences |
| `mergeBufferGeometries()` | `mergeGeometries()` | all occurrences |
| `mergeBufferAttributes()` | `mergeAttributes()` | if used |

#### Medium impact (behavior changes, verify after upgrade)

- **Lighting model (r155)**: `useLegacyLights` defaults to `false`. Physically correct light falloff. Directional lights (used in LightKit) are unaffected. Verify visual appearance; adjust intensities if point/spot lights exist.
- **WebGL 2 only (r163)**: Remove WebGL1 fallback path. All modern browsers support WebGL2.
- **Stencil disabled by default (r162)**: Check if stencil operations are used. Add `{ stencil: true }` to renderer constructor if needed.
- **Raycaster respects visibility (r113)**: Now skips `visible: false` objects. This is desirable behavior; verify selection still works.
- **Material.type read-only (r169)**: Cannot set `material.type` manually. Unlikely to affect this codebase.

#### No change needed (already compatible)

- `BufferGeometry` used exclusively (no legacy `Geometry`)
- `THREE.MathUtils` used correctly (not removed `THREE.Math`)
- `computeVertexNormals()` unchanged
- `Face3` not used

### 3. CSG Library

Current: `three-csg-ts` UMD bundle with `window.require('three')` shim.

Plan: Keep the existing shim (returns `window.THREE` which now points to r184). Test CSG operations (partition wall cutouts, patch extraction) after upgrade. If incompatible, migrate to `three-bvh-csg` (actively maintained, designed for modern Three.js, better performance for complex boolean operations).

### 4. Performance Optimizations

#### 4a. InstancedMesh for Repeated Equipment (highest impact)

Group models by geometry hash (type + dimensions) during `_applyProjectData()`. Replace groups of identical meshes with a single `THREE.InstancedMesh`. Per-instance transforms via `instanceMatrix`, per-instance colors via `instanceColor` for selection highlighting.

Interactive objects (selected, being dragged) are temporarily extracted into standalone meshes for TransformControls compatibility. On deselect, they merge back into the instanced batch.

Expected: 200 rack draw calls → ~5-10 draw calls.

#### 4b. BVH-Accelerated Raycasting

Add `three-mesh-bvh` via import map. After geometry creation, call `geometry.computeBoundsTree()`. The library monkey-patches `Raycaster` to use BVH automatically.

Expected: Raycasting 10-50x faster. Mouse hover/click responsiveness dramatically improved.

#### 4c. LOD (Level of Detail)

Wrap equipment groups in `THREE.LOD` with 2-3 detail levels:
- High detail (< 5m): Full geometry (U-slots, handles, fan grills)
- Medium detail (5-15m): Simplified box with front-face texture
- Low detail (> 15m): Single colored box, minimal triangles

LOD system auto-swaps based on camera distance during render.

Expected: 60-80% triangle count reduction when zoomed out.

#### 4d. Static Geometry Merging

Identify never-selected objects (walls, floor, ceiling, raised floor panels) and merge into single meshes per material. Extends existing `mergeBufferGeometries` usage more systematically.

Expected: Additional 5-10% draw call reduction.

### Combined Performance Projection

On a 200-rack scene (e.g., case_10 Base604, case_15 CMI-Guichou):
- Draw calls: 200+ → ~10-15
- Triangle count at typical zoom: full → ~20-40% of full
- Frame rate improvement: 3-5x during camera interaction, 2-3x during editing

## Testing Strategy

### Phase 1: Smoke Test (after API migration, before perf optimizations)

1. Start server, open STL_generate.html
2. Verify Three.js initializes without console errors
3. Load small project (case_01 DMAL_Testbed): verify rendering
4. Test: orbit, zoom, pan, select, move, undo/redo
5. Test: STL import, export cfdrun, 2D topview editor
6. Test: CSG operations (partition walls)

### Phase 2: Visual Regression

1. Capture reference screenshots on r128 at fixed camera angles
2. Capture same scenes on r184
3. Compare lighting, colors, transparency, material appearance
4. Adjust light/material properties if visual regression detected

### Phase 3: Large Scene Performance (after perf optimizations)

1. Load case_10 (142 racks): measure load time, FPS during orbit
2. Load case_15 (238 racks, 46 ACUs): same measurements
3. Test: select/move/delete rack in dense scene
4. Test: add new equipment to dense scene
5. Compare vs r128 baseline

All testing via `browser-use` (headless Chromium) per CLAUDE.md debugging rules.

## Rollback Plan

- Feature branch: `main` stays on r128 until verified
- Vendor backup: r128 files preserved in `vendor/r128-backup/` during development
- Import map swap: reverting is one commit (swap import map back to r128 script tags)

## Scope Boundaries

### In scope

- Three.js r128 → r184 via import maps
- All API migration changes
- InstancedMesh, BVH, LOD, static merging optimizations
- Visual verification and performance benchmarking
- CSG library compatibility (with fallback to three-bvh-csg)
- three-mesh-bvh integration

### Out of scope

- Splitting the 32k-line HTML file into modules
- Introducing a bundler (Vite/webpack)
- WebGPU renderer (experimental)
- Upgrading other vendor libraries (polygon-clipping, jszip, xterm)
- Changing server architecture

## Files Affected

| File | Changes |
|---|---|
| `src/frontend/web/STL_generate.html` | Import map, API renames, InstancedMesh/LOD integration, BVH setup |
| `src/frontend/web/js/liquid_mode.js` | API renames if encoding/mergeBuffer used |
| `src/frontend/web/js/liquid_pipe_renderer.js` | API renames if encoding/mergeBuffer used |
| `src/frontend/web/js/liquid_solve_monitor.js` | API renames if encoding/mergeBuffer used |
| `src/frontend/web/vendor/three.min.js` | Remove (replaced by import map to CDN) |
| `src/frontend/web/vendor/OrbitControls.js` | Remove (replaced by ESM import) |
| `src/frontend/web/vendor/STLLoader.js` | Remove (replaced by ESM import) |
| `src/frontend/web/vendor/TransformControls.js` | Remove (replaced by ESM import) |
| `src/frontend/web/vendor/BufferGeometryUtils.js` | Remove (replaced by ESM import) |
| `src/frontend/web/vendor/r184/` (new) | Local ESM copies of Three.js r184 core + addons for offline fallback |
| `src/frontend/web/vendor/three-csg-ts.umd.js` | Verify compat or replace with three-bvh-csg |
