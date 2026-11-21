# Headless STL Export & Agent-Driven Debugging Pipeline

## Problem

The STLGenerator browser app (`STL_generate.html`) relies on WebGL for geometry construction. When run in a headless browser (Playwright with `--disable-gpu`), `rebuildGeometry()` fails because `THREE.WebGLRenderer` cannot create a GL context. This means:

- `renderMeshes[]` is never populated
- `buildGroupedStlMap()` returns empty results
- LLM agents cannot trigger STL export → mesh generation → verification without a human operating the browser

## Goals

1. LLM agents can load a `.cfdproj` file and trigger full mesh generation via REST API
2. CI/CD pipelines can run regression tests (load case → generate mesh → validate patches/faceZones)
3. Human workflow remains unchanged — browser renders as before
4. Single source of truth for geometry — no duplicate STL generation logic

## Design

### Layer 1: JS Refactor — Separate Geometry from Rendering

Split `rebuildGeometry()` into two functions:

**`buildGeometryData()`** — Pure geometry construction (no WebGL):
- Creates `THREE.BufferGeometry` objects from model state (BoxGeometry, custom STL parsing, rack U-slot geometry, CSG booleans)
- Computes `matrixWorld` transforms from model anchor/position/rotation
- Populates `renderMeshes[]` with `THREE.Mesh(geom, dummyMaterial)` objects
- Sets `mesh.userData.modelId`, `mesh.userData.exportName`, etc.
- Uses `THREE.MeshBasicMaterial()` as dummy (no WebGL needed to instantiate)
- Returns the populated `renderMeshes[]`

**`renderScene(geometryData)`** — Visual rendering (WebGL required):
- Takes geometry data from `buildGeometryData()`
- Applies visual materials (colors, opacity, selection highlights)
- Manages scene graph (`globalGroup`, `wrapperGroup`)
- Calls `renderer.render(scene, camera)`
- Only called when WebGL is available

**Migration strategy for existing render functions** (e.g., `renderBoxModel`, `renderRackModel`, `renderCustomModel`):

Each existing `renderXxxModel(model, modelGroup, layout)` currently does both geometry + visual setup. Refactor each into:
- Extract geometry construction (BufferGeometry creation, position computation) into the function body that runs unconditionally
- Extract visual setup (material assignment, color, opacity, highlight) into a section guarded by `if (webglAvailable)`
- `spawnRenderable()` gets a `headless` parameter: if true, uses dummy material and skips scene graph attachment but still pushes to `renderMeshes[]`

**WebGL detection** (at startup):
```javascript
let webglAvailable = false;
try {
    const canvas = document.createElement('canvas');
    webglAvailable = !!(canvas.getContext('webgl2') || canvas.getContext('webgl'));
} catch (e) {}
```

**Startup flow change**:
```
// Before:
rebuildGeometry();  // fails in headless

// After:
buildGeometryData();           // always succeeds
if (webglAvailable) {
    renderScene();             // only in browser
}
```

### Layer 2: Server API — New Endpoints

**`POST /api/browser/export-stl`**
- Calls `buildGroupedStlMap()` in headless browser (now works because `buildGeometryData()` populated `renderMeshes[]`)
- Returns `{stlFiles: {filename: base64Content, ...}}`
- Also writes files directly to `triSurface/` directory

**`POST /api/browser/full-pipeline`**
- Request body: `{projPath: string, meshOnly?: bool, solveOnly?: bool}`
- Orchestrates the full flow:
  1. Load cfdproj via existing `/api/browser/load-proj`
  2. Call `buildGeometryData()` in headless browser
  3. Export STL files via `buildGroupedStlMap()`
  4. Build `cfd_export.json` via `buildCfdExportJson()`
  5. Write STL + JSON to case directory
  6. Submit mesh job (same as `/api/run-mesh`)
  7. Return `{jobId: string}`
- Single curl call from agent to trigger everything

**`GET /api/browser/geometry-status`**
- Returns: `{webglAvailable: bool, renderMeshesCount: int, modelsLoaded: int}`
- Diagnostic endpoint for agent to verify headless geometry is working

### Layer 3: Project Load Enhancement

When `/api/browser/load-proj` loads a cfdproj file, the current code does:
```javascript
try { rebuildGeometry(); } catch(e) {}
```

Change to:
```javascript
buildGeometryData();  // always works
if (webglAvailable) { renderScene(); }
```

This ensures `renderMeshes[]` is populated immediately after project load, even in headless mode.

### What Does NOT Change

- Browser UI — users see no difference
- STL export format — same multi-solid ASCII STL output
- `buildCfdExportJson()` — already works in headless (pure JSON)
- `stlgen_runner.py` — backend mesh pipeline unchanged
- All existing API endpoints — backward compatible

## Key Technical Details

### Three.js APIs That DO NOT Require WebGL

All of these work in headless (verified):
- `new THREE.BoxGeometry(w, h, d)` — creates BufferGeometry with vertex data
- `new THREE.Mesh(geom, material)` — just a JS object container
- `new THREE.MeshBasicMaterial()` — no GPU allocation needed
- `mesh.position.set(x, y, z)` — pure math
- `mesh.updateMatrixWorld(true)` — pure matrix math
- `BufferGeometry.attributes.position` — typed array access
- `Vector3.applyMatrix4()` — pure math
- `appendMeshFacets()` — reads BufferGeometry data, writes ASCII STL

### Three.js APIs That REQUIRE WebGL

- `THREE.WebGLRenderer` — needs GL context
- `renderer.render(scene, camera)` — needs GL context
- Material texture loading — needs GL context
- Post-processing effects — needs GL context

### CSG Boolean Operations

`three-csg-ts` library (used for custom STL patch extraction) operates on `THREE.Mesh` objects using pure geometry math. It does NOT require WebGL. This means custom model patches (water transfer printing) will work in headless.

## Agent Workflow Example

```bash
# 1. Start server
./start_server.sh --case-dir dctwin/case/GTC

# 2. One-shot: load project + generate mesh
curl -X POST http://localhost:8000/api/browser/full-pipeline \
  -H "Content-Type: application/json" \
  -d '{"projPath": "/path/to/GTC.cfdproj", "meshOnly": true}'
# Returns: {"jobId": "abc123"}

# 3. Poll until done
curl http://localhost:8000/api/job-status?job_id=abc123

# 4. Verify mesh
python -c "import pyvista as pv; ..."
```

## Testing Strategy

1. **Unit test**: `buildGeometryData()` produces same `renderMeshes[]` count as browser's `rebuildGeometry()`
2. **Integration test**: `/api/browser/export-stl` returns non-empty STL files for test cfdproj
3. **Regression test**: Load canonical case → generate mesh → verify patch count, faceZone face counts, cell count
4. **Equivalence test**: Compare STL output from headless vs browser for same cfdproj (binary diff of sorted facets)

## Files to Modify

| File | Changes |
|------|---------|
| `STL_generate.html` | Refactor `rebuildGeometry()` → `buildGeometryData()` + `renderScene()`, add WebGL detection, update all render functions |
| `server.py` | Add `/api/browser/export-stl`, `/api/browser/full-pipeline`, `/api/browser/geometry-status` endpoints |
| `CLAUDE.md` | Update debugging instructions with new API endpoints |

## Risks

- **CSG operations may have edge cases** in headless (e.g., `three-csg-ts` using `Mesh.clone()` which internally may reference renderer). Mitigation: test with custom models early.
- **Geometry parity** — headless output must match browser output exactly. Mitigation: equivalence tests comparing STL facet data.
- **HTML file size** — already 7300+ lines. The refactor adds ~200 lines but removes ~100 lines of duplicated logic. Net increase is manageable.
