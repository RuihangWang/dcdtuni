# Headless STL Export & Agent Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable LLM agents and CI/CD to trigger full STL export → mesh generation → verification via REST API, without WebGL.

**Architecture:** Refactor `rebuildGeometry()` into `buildGeometryData()` (pure geometry, no WebGL) + `renderScene()` (visual, WebGL-only). Add server API endpoints `/api/browser/export-stl` and `/api/browser/full-pipeline`. All Three.js geometry construction (BoxGeometry, BufferAttribute, Matrix4) works without WebGL — only WebGLRenderer needs a GL context.

**Tech Stack:** Three.js r128 (in-browser), Playwright (headless), Python HTTP server, OpenFOAM

**Spec:** `docs/superpowers/specs/2026-03-22-headless-stl-export-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `STLGeneratorForOpenFoam/STL_generate.html` | Browser app — geometry + rendering + export | Modify: add WebGL detection, refactor `spawnRenderable()`, `doRebuildGeometry()`, `rebuildGeometry()` |
| `STLGeneratorForOpenFoam/server.py` | REST API server + Playwright headless browser | Modify: add 3 new endpoints, update project load JS |
| `tests/test_headless_stl_export.py` | Integration tests for headless pipeline | Create |
| `STLGeneratorForOpenFoam/.claude/skills/api-debug-testing.md` | API documentation for agents | Modify: add new endpoint docs |

---

### Task 1: Add WebGL Detection and `webglAvailable` Flag

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html:459-469` (Three.js init section)

- [ ] **Step 1: Add WebGL detection before renderer creation**

Insert immediately after line 458 (`const viewCubeEl = ...`), before the `scene`/`renderer` creation:

```javascript
    // WebGL availability detection (for headless browser support)
    let webglAvailable = false;
    let renderer = null;
    try {
        const testCanvas = document.createElement('canvas');
        const gl = testCanvas.getContext('webgl2') || testCanvas.getContext('webgl');
        webglAvailable = !!gl;
        if (gl) { const ext = gl.getExtension('WEBGL_lose_context'); if (ext) ext.loseContext(); }
    } catch (e) {}
```

- [ ] **Step 2: Guard renderer creation with `webglAvailable`**

Replace lines 459-470 (the current `const scene = ...` through `controls`) with:

```javascript
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, viewer.clientWidth / viewer.clientHeight, 0.1, 100);
    camera.position.set(3.5, -4.5, 3.5);
    camera.up.copy(WORLD_UP);
    if (webglAvailable) {
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setSize(viewer.clientWidth, viewer.clientHeight);
        viewer.appendChild(renderer.domElement);
        renderer.toneMapping = THREE.ACESFilmicToneMapping;
        renderer.toneMappingExposure = 1.1;
        renderer.outputEncoding = THREE.sRGBEncoding;
    }
    const controls = webglAvailable
        ? new THREE.OrbitControls(camera, renderer.domElement)
        : null;
    if (controls) {
        controls.enableDamping = false;
        controls.minPolarAngle = 0.001;
        controls.maxPolarAngle = Math.PI - 0.001;
        controls.addEventListener('start', () => { cameraTransition = null; normalizeMainCameraUp(true); });
        controls.addEventListener('end', () => { normalizeMainCameraUp(true); });
    }
```

- [ ] **Step 3: Guard all `renderer.*` and `controls.*` calls**

Search for all direct references to `renderer.` and `controls.` in the file. Each must be guarded with `if (renderer)` or `if (controls)` respectively. Key locations:
- `renderer.render(scene, camera)` in the render loop
- `renderer.setSize(...)` in resize handler
- `controls.update()` in animation loop
- `controls.enabled = ...` in drag handlers

Use find-and-replace pattern: wrap each call site. Example:
```javascript
// Before:
renderer.render(scene, camera);
// After:
if (renderer) renderer.render(scene, camera);
```

- [ ] **Step 4: Verify browser still works normally**

Open `STL_generate.html` in Chrome. Confirm:
- 3D viewport renders
- Models can be created, moved, rotated
- STL export works
- No console errors

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "feat: add WebGL detection, guard renderer/controls for headless support"
```

---

### Task 2: Refactor `spawnRenderable()` for Headless-Safe Mesh Creation

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html:3525-3577` (`spawnRenderable`)

- [ ] **Step 1: Refactor `spawnRenderable()` to work without WebGL**

Replace the function (lines 3525-3577) with a version that uses dummy materials when WebGL is unavailable:

```javascript
    function spawnRenderable(modelGroup, geom, options) {
        let material;
        if (webglAvailable) {
            // Full PBR material with textures (existing logic)
            const baseOpacity = options.opacity ?? 1;
            const isTransparent = typeof options.transparent === 'boolean' ? options.transparent : baseOpacity < 0.999;
            material = options.objectKind
                ? createMaterialForType(options.objectKind, options)
                : new THREE.MeshStandardMaterial({
                    color: options.color,
                    metalness: 0.1, roughness: 0.8,
                    side: options.doubleSide ? THREE.DoubleSide : THREE.FrontSide,
                    polygonOffset: true, polygonOffsetFactor: 1,
                    transparent: isTransparent, opacity: baseOpacity,
                    depthWrite: options.depthWrite ?? !isTransparent
                });
        } else {
            // Headless: dummy material (no GPU needed)
            material = new THREE.MeshBasicMaterial({ color: options.color || 0xcccccc });
        }
        const mesh = new THREE.Mesh(geom, material);
        // Texture scaling (only when WebGL + textures available)
        if (webglAvailable && material.map && geom.attributes && geom.attributes.uv) {
            const uvAttr = geom.attributes.uv;
            let maxU = 0, maxV = 0;
            for (let i = 0; i < uvAttr.count; i++) {
                maxU = Math.max(maxU, Math.abs(uvAttr.getX(i)));
                maxV = Math.max(maxV, Math.abs(uvAttr.getY(i)));
            }
            const tileSizes = { 'rack-side': 0.3, 'rack-front': 0.3, 'rack-top': 0.3, 'rack-bottom': 0.3,
                'server-front': 0.15, 'blank-panel': 0.3, 'floor': 0.6, 'wall': 0.5, 'baffle': 0.25, 'box': 0.4 };
            const tileSize = tileSizes[options.objectKind] || 0.4;
            material.map.repeat.set(Math.max(1, maxU / tileSize), Math.max(1, maxV / tileSize));
            if (material.normalMap) material.normalMap.repeat.set(Math.max(1, maxU / tileSize), Math.max(1, maxV / tileSize));
            if (material.roughnessMap) material.roughnessMap.repeat.set(Math.max(1, maxU / tileSize), Math.max(1, maxV / tileSize));
            if (material.metalnessMap) material.metalnessMap.repeat.set(Math.max(1, maxU / tileSize), Math.max(1, maxV / tileSize));
        }
        // Edge helpers (visual only, skip in headless)
        if (webglAvailable && !options.skipEdges) {
            mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geom), new THREE.LineBasicMaterial({
                color: 0x000000,
                opacity: Math.min(0.3, Math.max(0.03, (options.opacity ?? 1) * 0.4)),
                transparent: true
            })));
        }
        mesh.userData = {
            ...options.userData,
            origColor: options.color,
            baseOpacity: options.opacity ?? 1,
            _materialPreset: options.objectKind || null
        };
        modelGroup.add(mesh);
        renderMeshes.push(mesh);
        return mesh;
    }
```

- [ ] **Step 2: Verify browser still works**

Open in Chrome, create a box model, verify it renders with correct materials and edge helpers.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "refactor: make spawnRenderable headless-safe with dummy materials"
```

---

### Task 3: Refactor `doRebuildGeometry()` into `buildGeometryData()` + `renderScene()`

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html:4383-4417`

- [ ] **Step 1: Split `doRebuildGeometry()` into two functions**

Replace lines 4383-4417 with:

```javascript
    function buildGeometryData() {
        // Pure geometry construction — works without WebGL.
        // Populates renderMeshes[] with THREE.Mesh objects containing
        // BufferGeometry + matrixWorld transforms for STL export.
        clearRender();
        state.models.forEach(model => {
            if (!isModelEnabled(model)) return;
            const modelGroup = new THREE.Group();
            modelGroup.position.set(model.anchor.x, model.anchor.y, model.anchor.z);
            modelGroup.userData = { modelId: model.id };
            applyModelTransformToObject(modelGroup, model);
            // Attach to wrapperGroup/globalGroup so matrixWorld propagates
            globalGroup.add(modelGroup);
            renderRoots.push(modelGroup);
            if (webglAvailable) {
                const anchorMarker = createAnchorMarkerForModel(model);
                modelGroup.add(anchorMarker);
                renderHelpers.push(anchorMarker);
            }

            const faceLayouts = getFaceLayouts(model);
            const facesIslands = buildFaceIslands(model);
            if (model.kind === 'custom' && geometryCache.has(model.id)) renderCustomModel(model, modelGroup, faceLayouts);
            else if (model.kind === 'rack') renderRackModel(model, modelGroup, faceLayouts);
            else if (model.kind === 'wall') renderWallModel(model, modelGroup, faceLayouts);
            else if (model.kind === 'baffle') renderBaffleModel(model, modelGroup, faceLayouts);
            else if (model.kind === 'floor') renderFloorModel(model, modelGroup, faceLayouts);
            else {
                renderBoxModel(model, modelGroup, faceLayouts, facesIslands);
                if (webglAvailable && model.kind === 'acu') createWallBaffleEdgeHelpers(modelGroup, model, faceLayouts);
            }
        });
        // Ensure matrixWorld is computed for all meshes (needed for STL export)
        wrapperGroup.updateMatrixWorld(true);
    }

    function renderScene() {
        // Visual-only updates — requires WebGL.
        if (!webglAvailable) return;
        updateHighlights();
    }

    function rebuildGeometry() {
        if (isStlPreviewMode()) {
            doRebuildStlPreview();
        } else {
            buildGeometryData();
            renderScene();
        }
        updateUI();
        if (window.requestRender) window.requestRender();
    }
```

- [ ] **Step 2: Expose `buildGeometryData` to window scope for headless access**

Add near other window exports:

```javascript
    window.buildGeometryData = buildGeometryData;
```

- [ ] **Step 3: Verify browser renders correctly**

Open in Chrome, load a project, verify all models render, STL export works.

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "refactor: split doRebuildGeometry into buildGeometryData + renderScene"
```

---

### Task 4: Update Project Load and `withTemporaryRenderedModels` for Headless

**Files:**
- Modify: `STLGeneratorForOpenFoam/server.py:111-126` (load_project JS)
- Modify: `STLGeneratorForOpenFoam/STL_generate.html:9643-9692` (withTemporaryRenderedModels)

- [ ] **Step 1: Update server.py project load JS to call `buildGeometryData()`**

Replace lines 115-126 in server.py:

```python
                result_holder["result"] = page.evaluate(f"""(() => {{
                    try {{
                        const raw = JSON.parse({_json.dumps(proj_data)});
                        state = normalizeState(raw);
                        syncCounters();
                        try {{ restoreGeometryCache(); }} catch(e) {{}}
                        try {{ buildGeometryData(); }} catch(e) {{ console.error('buildGeometryData failed:', e.message); }}
                        if (typeof webglAvailable !== 'undefined' && webglAvailable) {{
                            try {{ renderScene(); }} catch(e) {{}}
                        }}
                        return {{ success: true, modelCount: state.models.length, renderMeshes: (typeof renderMeshes !== 'undefined') ? renderMeshes.length : 0 }};
                    }} catch(e) {{
                        return {{ success: false, error: e.message }};
                    }}
                }})()""")
```

- [ ] **Step 2: Guard `withTemporaryRenderedModels()` visual calls**

In `STL_generate.html`, find `withTemporaryRenderedModels()` (line ~9643). The function already works with geometry (calls renderXxxModel which calls spawnRenderable). Only need to guard the `updateHighlights()` call:

Find and wrap:
```javascript
// Before (inside withTemporaryRenderedModels, after callback):
updateHighlights();
// After:
if (webglAvailable) updateHighlights();
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/server.py
git commit -m "feat: update project load and temp render for headless geometry"
```

---

### Task 5: Add Server API Endpoints

**Files:**
- Modify: `STLGeneratorForOpenFoam/server.py`

- [ ] **Step 1: Add `handle_browser_export_stl()` handler**

Add after `handle_browser_export_cfd()` (line ~326):

```python
def handle_browser_export_stl(handler) -> None:
    """POST /api/browser/export-stl — Generate STL files in headless browser, write to triSurface/."""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set."})
        return
    try:
        # Ensure geometry is built
        _browser_call("exec_js", "buildGeometryData(); renderMeshes.length")
        # Export grouped STL map
        result = _browser_call("exec_js", """(() => {
            const allIds = state.models.map(m => m.id);
            const groupedMap = buildGroupedStlMap(allIds, true);
            const encoded = {};
            for (const [filename, content] of Object.entries(groupedMap)) {
                encoded[filename] = btoa(unescape(encodeURIComponent(content)));
            }
            return { stlFiles: encoded, count: Object.keys(encoded).length };
        })()""")
        # Write STL files to triSurface/
        import base64
        tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
        tri_dir.mkdir(parents=True, exist_ok=True)
        stl_files = result.get("stlFiles", {})
        for filename, b64 in stl_files.items():
            (tri_dir / filename).write_bytes(base64.b64decode(b64))
        _send_json(handler, 200, {
            "success": True,
            "filesWritten": list(stl_files.keys()),
            "count": result.get("count", 0),
            "triSurfaceDir": str(tri_dir),
        })
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})
```

- [ ] **Step 2: Add `handle_browser_geometry_status()` handler**

```python
def handle_browser_geometry_status(handler) -> None:
    """GET /api/browser/geometry-status — Diagnostic: check headless geometry state."""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    try:
        result = _browser_call("exec_js", """(() => {
            return {
                webglAvailable: typeof webglAvailable !== 'undefined' ? webglAvailable : null,
                renderMeshesCount: typeof renderMeshes !== 'undefined' ? renderMeshes.length : -1,
                modelsLoaded: state ? state.models.length : 0,
                rendererExists: typeof renderer !== 'undefined' && renderer !== null,
            };
        })()""")
        _send_json(handler, 200, result)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})
```

- [ ] **Step 3: Add `handle_browser_full_pipeline()` handler**

```python
def handle_browser_full_pipeline(handler) -> None:
    """POST /api/browser/full-pipeline — One-shot: load proj → export STL+JSON → run mesh."""
    if not _ensure_browser(handler):
        _send_json(handler, 500, {"error": "Failed to start headless browser"})
        return
    data = _read_body(handler)
    proj_path = data.get("projPath")
    mesh_only = data.get("meshOnly", True)
    solve_only = data.get("solveOnly", False)
    if not proj_path:
        _send_json(handler, 400, {"error": "projPath is required"})
        return

    with CASE_DIR_LOCK:
        case_dir = CASE_DIR
    if not case_dir:
        _send_json(handler, 400, {"error": "No case directory set."})
        return

    import base64
    try:
        # Step 1: Load project
        load_result = _browser_call("load_project", proj_path)
        if not load_result.get("success"):
            _send_json(handler, 500, {"error": f"Project load failed: {load_result.get('error')}"})
            return

        # Step 2: Export STL files
        stl_result = _browser_call("exec_js", """(() => {
            buildGeometryData();
            const allIds = state.models.map(m => m.id);
            const groupedMap = buildGroupedStlMap(allIds, true);
            const encoded = {};
            for (const [filename, content] of Object.entries(groupedMap)) {
                encoded[filename] = btoa(unescape(encodeURIComponent(content)));
            }
            return encoded;
        })()""")
        tri_dir = case_dir / "Mesh" / "constant" / "triSurface"
        tri_dir.mkdir(parents=True, exist_ok=True)
        for filename, b64 in stl_result.items():
            (tri_dir / filename).write_bytes(base64.b64decode(b64))

        # Step 3: Export cfd_export.json
        cfd_result = _browser_call("exec_js", "JSON.stringify(buildCfdExportJson())")
        model_dir = case_dir / "model"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "cfd_export.json").write_text(cfd_result, encoding="utf-8")

        # Step 4: Run mesh (reuse existing run_mesh logic)
        from dctwin.runner.stlgen_runner import STLGenRunner
        from dctwin.runner.run_cfd import run_from_stlgen
        import threading, uuid
        job_id = uuid.uuid4().hex[:8]
        JOBS[job_id] = {"lines": [], "done": False, "returncode": None, "proc": None}

        def _run():
            try:
                import subprocess, os
                cfd_json_path = model_dir / "cfd_export.json"
                flags = " --mesh-only" if mesh_only else (" --solve-only" if solve_only else "")
                cmd = f"python -m dctwin.runner.run_cfd --from-stlgen {cfd_json_path}{flags}"
                shell_cmd = cmd
                proc = subprocess.Popen(
                    shell_cmd, shell=True, executable="/bin/bash",
                    cwd=str(case_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, preexec_fn=os.setsid
                )
                JOBS[job_id]["proc"] = proc
                for line in proc.stdout:
                    JOBS[job_id]["lines"].append(line.rstrip())
                proc.wait()
                JOBS[job_id]["returncode"] = proc.returncode
                JOBS[job_id]["done"] = True
                if proc.returncode == 0:
                    (case_dir / "Mesh" / "case.foam").write_text("")
            except Exception as ex:
                JOBS[job_id]["lines"].append(f"[ERROR] {ex}")
                JOBS[job_id]["returncode"] = -1
                JOBS[job_id]["done"] = True

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        _send_json(handler, 200, {
            "success": True,
            "jobId": job_id,
            "stlFiles": list(stl_result.keys()),
            "modelsLoaded": load_result.get("modelCount", 0),
        })
    except Exception as e:
        import traceback
        _send_json(handler, 500, {"error": str(e), "traceback": traceback.format_exc()})
```

- [ ] **Step 4: Register new endpoints in the router**

Find the `do_POST` and `do_GET` handler routing (around line 740+). Add:

```python
        elif parsed.path == "/api/browser/export-stl":
            handle_browser_export_stl(handler)
        elif parsed.path == "/api/browser/full-pipeline":
            handle_browser_full_pipeline(handler)
        elif parsed.path == "/api/browser/geometry-status":
            handle_browser_geometry_status(handler)
```

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/server.py
git commit -m "feat: add /api/browser/export-stl, full-pipeline, geometry-status endpoints"
```

---

### Task 6: Integration Test — Headless STL Export

**Files:**
- Create: `tests/test_headless_stl_export.py`

- [ ] **Step 1: Write integration test**

```python
"""Integration tests for headless STL export pipeline.

Requires: server running on localhost:8000 with --case-dir set.
Run: pytest tests/test_headless_stl_export.py -v -s
"""
import pytest
import requests
import time
import json

BASE = "http://localhost:8000"
PROJ_PATH = "/mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam/Saved_proj/GTC_Datacenter_1773993099185.cfdproj"


def _wait_job(job_id, timeout=600):
    """Poll job status until done or timeout."""
    for _ in range(timeout // 5):
        r = requests.get(f"{BASE}/api/job-status", params={"job_id": job_id})
        data = r.json()
        if data.get("done"):
            return data
        time.sleep(5)
    raise TimeoutError(f"Job {job_id} did not complete in {timeout}s")


class TestGeometryStatus:
    def test_geometry_status_reports_no_webgl(self):
        r = requests.get(f"{BASE}/api/browser/geometry-status")
        assert r.status_code == 200
        data = r.json()
        assert data["webglAvailable"] == False
        assert data["modelsLoaded"] >= 0

    def test_after_project_load_meshes_populated(self):
        # Load project
        r = requests.post(f"{BASE}/api/browser/load-proj",
                          json={"path": PROJ_PATH})
        assert r.json()["success"]
        # Check geometry
        r = requests.get(f"{BASE}/api/browser/geometry-status")
        data = r.json()
        assert data["webglAvailable"] == False
        assert data["renderMeshesCount"] > 0, "renderMeshes should be populated in headless"
        assert data["modelsLoaded"] == 75


class TestExportStl:
    def test_export_stl_returns_files(self):
        # Load project first
        requests.post(f"{BASE}/api/browser/load-proj", json={"path": PROJ_PATH})
        # Export STL
        r = requests.post(f"{BASE}/api/browser/export-stl")
        assert r.status_code == 200
        data = r.json()
        assert data["success"]
        assert data["count"] > 0
        assert "merged_rack.stl" in data["filesWritten"]
        assert "merged_baffle.stl" in data["filesWritten"]


class TestFullPipeline:
    def test_full_pipeline_mesh_generation(self):
        r = requests.post(f"{BASE}/api/browser/full-pipeline",
                          json={"projPath": PROJ_PATH, "meshOnly": True})
        assert r.status_code == 200
        data = r.json()
        assert data["success"]
        assert data["jobId"]
        assert len(data["stlFiles"]) > 0
        # Wait for mesh to complete
        result = _wait_job(data["jobId"])
        assert result["returncode"] == 0, f"Mesh failed: rc={result['returncode']}"
```

- [ ] **Step 2: Run test to verify it works end-to-end**

```bash
# Server must be running:
# ./start_server.sh --case-dir dctwin/case/GTC
conda run -n dcdtuni pytest tests/test_headless_stl_export.py -v -s
```

Expected: All tests pass. `TestGeometryStatus.test_after_project_load_meshes_populated` confirms `renderMeshesCount > 0` in headless.

- [ ] **Step 3: Commit**

```bash
git add tests/test_headless_stl_export.py
git commit -m "test: add integration tests for headless STL export pipeline"
```

---

### Task 7: Update CLAUDE.md and API Documentation

**Files:**
- Modify: `STLGeneratorForOpenFoam/.claude/skills/api-debug-testing.md`

- [ ] **Step 1: Add new endpoints to API documentation**

Append to the API endpoints section:

```markdown
### Headless STL Export Endpoints

**`GET /api/browser/geometry-status`** — Check headless geometry state
```bash
curl http://localhost:8000/api/browser/geometry-status
# Returns: {"webglAvailable": false, "renderMeshesCount": 450, "modelsLoaded": 75}
```

**`POST /api/browser/export-stl`** — Generate STL files from headless browser
```bash
curl -X POST http://localhost:8000/api/browser/export-stl
# Returns: {"success": true, "filesWritten": ["merged_rack.stl", ...], "count": 7}
```

**`POST /api/browser/full-pipeline`** — One-shot: load project → export → mesh
```bash
curl -X POST http://localhost:8000/api/browser/full-pipeline \
  -H "Content-Type: application/json" \
  -d '{"projPath": "/path/to/project.cfdproj", "meshOnly": true}'
# Returns: {"success": true, "jobId": "abc123", "stlFiles": [...]}
# Then poll: curl http://localhost:8000/api/job-status?job_id=abc123
```
```

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/.claude/skills/api-debug-testing.md
git commit -m "docs: add headless STL export API endpoints to debug guide"
```

---

## Execution Order

Tasks 1-3 are sequential (each builds on the previous). Task 4-5 depend on Tasks 1-3. Task 6 depends on Task 5. Task 7 can run after Task 5.

```
Task 1 (WebGL detection) → Task 2 (spawnRenderable) → Task 3 (buildGeometryData)
                                                           ↓
                                                      Task 4 (project load + temp render)
                                                           ↓
                                                      Task 5 (server API endpoints)
                                                           ↓
                                                      Task 6 (integration tests)
                                                           ↓
                                                      Task 7 (docs)
```

## Verification Checklist

After all tasks complete, verify:
- [ ] `curl http://localhost:8000/api/browser/geometry-status` shows `webglAvailable: false, renderMeshesCount > 0` after loading project
- [ ] `curl -X POST http://localhost:8000/api/browser/export-stl` returns non-empty STL file list
- [ ] `curl -X POST http://localhost:8000/api/browser/full-pipeline -d '{"projPath": "...", "meshOnly": true}'` returns jobId, mesh completes with rc=0
- [ ] Browser UI still works normally (create models, render, export STL)
- [ ] Mesh output is identical to previous browser-exported mesh (same patch count, same faceZone face counts)
