# Post-Processing Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the slow JSON-based post-processing pipeline with an in-memory binary pipeline, ParaView-style UI, mesh preview, and fix the streamline sphere widget.

**Architecture:** PyVista singleton persists VTM data in memory. Binary REST API serves geometry and field data as ArrayBuffers. Three.js GPU shader handles colormap — field switch only re-uploads a vertex attribute, colorbar changes only update uniforms. Mesh preview is an independent feature reading polyMesh directly.

**Tech Stack:** Python 3.10, PyVista, NumPy, struct (binary packing), Three.js r128, WebGL GLSL shaders, TransformControls

**Spec:** `docs/superpowers/specs/2026-03-26-postprocessing-redesign-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `dctwin/dctwin/runner/postprocess.py` | `CachedPostProcessor` singleton: VTM loading, mesh/field caching, binary serialization. `MeshPreviewLoader`: reads polyMesh for mesh preview. | Modify (add classes at end of file) |
| `STLGeneratorForOpenFoam/server/server.py` | New binary API endpoints (`/api/pp/*`, `/api/mesh-preview/*`). Replace old `_POST_PROCESSOR` global with `CachedPostProcessor`. | Modify (replace post-process handlers) |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | ParaView-style right panel, GPU shader colormap, binary ArrayBuffer parsing, field dropdown, MultiBlock Inspector tree, mesh preview UI, streamline sphere TransformControls. | Modify (replace post-process JS + HTML sections) |
| `tests/test_cached_postprocessor.py` | Unit tests for CachedPostProcessor binary serialization and caching. | Create |

---

### Task 1: CachedPostProcessor — Binary Mesh Extraction

The core backend class that holds VTM data in memory and serves geometry as binary ArrayBuffers with mesh/field separation.

**Files:**
- Modify: `dctwin/dctwin/runner/postprocess.py` (append after existing classes, ~line 1644)
- Create: `tests/test_cached_postprocessor.py`

- [ ] **Step 1: Write the test for binary mesh serialization**

```python
# tests/test_cached_postprocessor.py
import struct
import numpy as np
import pytest

def test_pack_mesh_binary():
    """Binary mesh packing: Float32 positions + Uint32 indices, patches concatenated."""
    from dctwin.runner.postprocess import CachedPostProcessor

    # Simulate 2 patches: patch A (4 points, 2 triangles), patch B (3 points, 1 triangle)
    patches = [
        {
            "name": "wall_front",
            "points": np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0]], dtype=np.float32),
            "indices": np.array([[0,1,2],[0,2,3]], dtype=np.uint32),
        },
        {
            "name": "wall_back",
            "points": np.array([[0,0,1],[1,0,1],[1,1,1]], dtype=np.float32),
            "indices": np.array([[0,1,2]], dtype=np.uint32),
        },
    ]

    binary, meta = CachedPostProcessor.pack_mesh_binary(patches)

    # meta should describe each patch
    assert len(meta) == 2
    assert meta[0]["name"] == "wall_front"
    assert meta[0]["nPoints"] == 4
    assert meta[0]["nTriangles"] == 2
    assert meta[1]["name"] == "wall_back"
    assert meta[1]["nPoints"] == 3
    assert meta[1]["nTriangles"] == 1

    # binary layout: [7 points × 3 floats] + [3 triangles × 3 uint32]
    n_pos_bytes = 7 * 3 * 4  # 84 bytes
    n_idx_bytes = 3 * 3 * 4  # 36 bytes
    assert len(binary) == n_pos_bytes + n_idx_bytes

    # Verify patch B indices are offset by patch A's point count (4)
    idx_offset = n_pos_bytes
    tri_b_start = idx_offset + 2 * 3 * 4  # skip 2 triangles from patch A
    i0, i1, i2 = struct.unpack_from("<III", binary, tri_b_start)
    assert (i0, i1, i2) == (4, 5, 6)  # offset by 4


def test_pack_field_binary_scalar():
    """Binary field packing: 16-byte header + Float32 scalars."""
    from dctwin.runner.postprocess import CachedPostProcessor

    scalars = np.array([20.0, 25.5, 30.0, 22.1], dtype=np.float32)
    binary = CachedPostProcessor.pack_field_binary(scalars, n_components=1)

    # Header: min, max, nComponents, nPoints (4 × 4 bytes = 16)
    fmin, fmax, nc, np_ = struct.unpack_from("<ffII", binary, 0)
    assert abs(fmin - 20.0) < 0.01
    assert abs(fmax - 30.0) < 0.01
    assert nc == 1
    assert np_ == 4

    # Data: 4 floats
    assert len(binary) == 16 + 4 * 4
    v0 = struct.unpack_from("<f", binary, 16)[0]
    assert abs(v0 - 20.0) < 0.01


def test_pack_field_binary_vector():
    """Binary field packing for vector: nComponents=3, interleaved Ux,Uy,Uz."""
    from dctwin.runner.postprocess import CachedPostProcessor

    vectors = np.array([[1,0,0],[0,2,0],[0,0,3]], dtype=np.float32)
    binary = CachedPostProcessor.pack_field_binary(vectors, n_components=3)

    fmin, fmax, nc, np_ = struct.unpack_from("<ffII", binary, 0)
    assert nc == 3
    assert np_ == 3
    assert len(binary) == 16 + 3 * 3 * 4  # header + 9 floats
```

- [ ] **Step 2: Run tests — should fail (class not yet implemented)**

Run: `cd dctwin && conda run -n dcdtuni python -m pytest ../tests/test_cached_postprocessor.py -v`
Expected: ImportError — `CachedPostProcessor` not found

- [ ] **Step 3: Implement CachedPostProcessor class**

Append to the end of `dctwin/dctwin/runner/postprocess.py` (after the existing `PostProcessor` class):

```python
# ---------------------------------------------------------------------------
#  CachedPostProcessor — in-memory singleton for binary post-processing API
# ---------------------------------------------------------------------------

class CachedPostProcessor:
    """Singleton that holds VTM data in memory with mesh/field separation.

    Lifecycle:
      1.  init(run_dir) — runs foamToVTK if needed, loads VTM into PyVista
      2.  get_mesh()    — triangulates boundary patches, caches geometry
      3.  get_field(name) — extracts scalar array, caches per field
      4.  invalidate()  — clears caches (called when switching run dir)
    """

    _instance: Optional["CachedPostProcessor"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.run_dir: Optional[Path] = None
        self.multi_block: Optional[Any] = None  # pyvista.MultiBlockDataSet
        self._internal: Optional[Any] = None     # pyvista.UnstructuredGrid
        self._boundary: Optional[Any] = None     # pyvista.MultiBlockDataSet
        self._tri_cache: Dict[str, dict] = {}    # patch_name → {points, indices}
        self._mesh_binary: Optional[bytes] = None
        self._mesh_meta: Optional[List[dict]] = None
        self._field_cache: Dict[str, bytes] = {} # field_name → packed binary
        self._field_info: Dict[str, dict] = {}   # field_name → {min, max, nComponents}
        self._zones_cache: Optional[bytes] = None
        self._zones_meta: Optional[List[dict]] = None
        self._available_fields: List[dict] = []
        self._vtm_path: Optional[Path] = None

    @classmethod
    def get_instance(cls) -> "CachedPostProcessor":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def init(self, run_dir: Path, force: bool = False) -> dict:
        """Load VTM for the given run directory. Skip if already loaded."""
        run_dir = Path(run_dir).resolve()
        if not force and self.run_dir == run_dir and self.multi_block is not None:
            return {
                "success": True,
                "message": f"Already loaded: {len(self._mesh_meta or [])} patches",
                "cached": True,
            }
        self.invalidate()
        self.run_dir = run_dir

        # Run foamToVTK if needed (reuse existing PostProcessor logic)
        pp = PostProcessor(run_dir)
        pp.convert_to_vtk()
        info = pp.load_vtk()
        self._vtm_path = pp._vtm_path

        # Load multiblock
        pv = _get_pyvista()
        self.multi_block = pv.read(str(self._vtm_path))

        # Locate internal + boundary blocks
        for i in range(self.multi_block.n_blocks):
            key = self.multi_block.keys()[i]
            block = self.multi_block[i]
            if block is None:
                continue
            if key in ("internal", "internalMesh"):
                self._internal = block
            elif key == "boundary":
                self._boundary = block

        # Pre-compute derived fields on internal mesh
        if self._internal is not None and "U" in self._internal.point_data:
            U = self._internal.point_data["U"]
            self._internal.point_data["Umag"] = np.linalg.norm(U, axis=1)
            self._internal.point_data["Ux"] = U[:, 0]
            self._internal.point_data["Uy"] = U[:, 1]
            self._internal.point_data["Uz"] = U[:, 2]

        # Build available fields list
        self._available_fields = self._discover_fields()

        n_patches = self._boundary.n_blocks if self._boundary else 0
        n_fields = len(self._available_fields)
        return {
            "success": True,
            "message": f"VTM loaded, {n_patches} patches, {n_fields} fields",
            "cached": False,
        }

    def _discover_fields(self) -> List[dict]:
        """Scan internal mesh and boundary patches for available fields."""
        fields = []
        seen = set()
        source = self._internal if self._internal is not None else None
        if source is None and self._boundary is not None and self._boundary.n_blocks > 0:
            source = self._boundary[0]
        if source is None:
            return fields

        for name in source.point_data.keys():
            if name in seen:
                continue
            arr = source.point_data[name]
            if arr.ndim == 1:
                fields.append({"name": name, "type": "scalar", "label": name})
                seen.add(name)
            elif arr.ndim == 2 and arr.shape[1] == 3:
                fields.append({
                    "name": name, "type": "vector", "label": name,
                    "components": [f"{name}x", f"{name}y", f"{name}z"],
                })
                seen.add(name)
                # Also add magnitude + components as derived scalars
                for suffix in ("mag", "x", "y", "z"):
                    derived = f"{name}{suffix}"
                    if derived not in seen:
                        seen.add(derived)
        return fields

    def get_mesh(self) -> Tuple[bytes, List[dict]]:
        """Return binary geometry + patch metadata. Cached after first call."""
        if self._mesh_binary is not None and self._mesh_meta is not None:
            return self._mesh_binary, self._mesh_meta

        if self._boundary is None:
            return b"", []

        patches = []
        for i in range(self._boundary.n_blocks):
            name = self._boundary.keys()[i]
            patch = self._boundary[i]
            if patch is None or patch.n_points == 0:
                continue
            tri = patch.triangulate()
            points = np.ascontiguousarray(tri.points, dtype=np.float32)
            # Extract triangle indices
            if hasattr(tri, "regular_faces"):
                indices = np.ascontiguousarray(tri.regular_faces, dtype=np.uint32)
            else:
                faces_arr = tri.faces
                idx, tris = 0, []
                while idx < len(faces_arr):
                    n = faces_arr[idx]
                    tris.append(faces_arr[idx + 1 : idx + 1 + n])
                    idx += 1 + n
                indices = np.array(tris, dtype=np.uint32)

            self._tri_cache[name] = {"points": points, "indices": indices, "tri": tri}
            patches.append({
                "name": name,
                "points": points,
                "indices": indices,
            })

        self._mesh_binary, self._mesh_meta = self.pack_mesh_binary(patches)
        # Add defaultVisible flag
        for m in self._mesh_meta:
            m["defaultVisible"] = (
                not m["name"].endswith("_slave")
                and m["name"] not in ("internalMesh", "internal")
            )
        return self._mesh_binary, self._mesh_meta

    def get_field(self, field_name: str) -> Tuple[bytes, dict]:
        """Return binary scalars + field info. Cached per field."""
        if field_name in self._field_cache:
            return self._field_cache[field_name], self._field_info[field_name]

        if self._boundary is None:
            return b"", {"min": 0, "max": 0, "nComponents": 1, "nPoints": 0}

        # Ensure mesh is triangulated first
        if not self._tri_cache:
            self.get_mesh()

        # Resolve field name (handle vector components + magnitude)
        resolved = field_name
        n_components = 1
        is_component = False
        component_idx = -1

        # Check if it's a component like "Ux", "Uy", "Uz"
        for vec_field in self._available_fields:
            if vec_field["type"] != "vector":
                continue
            base = vec_field["name"]
            if field_name == f"{base}mag":
                resolved = f"{base}mag" if f"{base}mag" in (self._internal.point_data if self._internal else {}) else base
                if resolved == base:
                    # Need to compute magnitude
                    is_component = False
                    resolved = f"{base}_magnitude"
                break
            for ci, suffix in enumerate(("x", "y", "z")):
                if field_name == f"{base}{suffix}":
                    resolved = base
                    is_component = True
                    component_idx = ci
                    break

        # Collect scalars from all cached patches in order
        all_scalars = []
        for name in [m["name"] for m in (self._mesh_meta or [])]:
            cached = self._tri_cache.get(name)
            if cached is None:
                continue
            tri = cached["tri"]
            # Try point_data first, then cell_data
            if resolved in tri.point_data:
                arr = tri.point_data[resolved]
            elif resolved in tri.cell_data:
                tri_pd = tri.cell_data_to_point_data()
                arr = tri_pd.point_data[resolved]
            elif resolved.endswith("_magnitude"):
                base = resolved.replace("_magnitude", "")
                if base in tri.point_data:
                    arr = np.linalg.norm(tri.point_data[base], axis=1)
                else:
                    arr = np.zeros(tri.n_points, dtype=np.float32)
            else:
                arr = np.zeros(tri.n_points, dtype=np.float32)

            if is_component and arr.ndim == 2:
                arr = arr[:, component_idx]
            elif arr.ndim == 2:
                # Vector field requested as-is (shouldn't happen for scalar requests)
                arr = np.linalg.norm(arr, axis=1)

            all_scalars.append(np.asarray(arr, dtype=np.float32))

        if all_scalars:
            combined = np.concatenate(all_scalars)
        else:
            combined = np.array([], dtype=np.float32)

        binary = self.pack_field_binary(combined, n_components=1)
        info = {
            "min": float(combined.min()) if len(combined) > 0 else 0.0,
            "max": float(combined.max()) if len(combined) > 0 else 0.0,
            "nComponents": 1,
            "nPoints": len(combined),
        }
        self._field_cache[field_name] = binary
        self._field_info[field_name] = info
        return binary, info

    def get_available_fields(self) -> List[dict]:
        return self._available_fields

    def get_internal_mesh(self):
        """Return internal mesh for streamline computation."""
        return self._internal

    def invalidate(self) -> None:
        """Clear all caches."""
        self.multi_block = None
        self._internal = None
        self._boundary = None
        self._tri_cache.clear()
        self._mesh_binary = None
        self._mesh_meta = None
        self._field_cache.clear()
        self._field_info.clear()
        self._zones_cache = None
        self._zones_meta = None
        self._available_fields = []
        self._vtm_path = None

    # ── Static binary packing methods ──

    @staticmethod
    def pack_mesh_binary(patches: List[dict]) -> Tuple[bytes, List[dict]]:
        """Pack triangulated patches into a single binary buffer.

        Layout:
          [Float32 × totalPoints × 3]   ← positions (x,y,z interleaved)
          [Uint32 × totalTriangles × 3]  ← indices (offset per patch)

        Returns (binary_bytes, patch_metadata_list).
        """
        all_points = []
        all_indices = []
        meta = []
        point_offset = 0

        for p in patches:
            pts = np.asarray(p["points"], dtype=np.float32)
            idx = np.asarray(p["indices"], dtype=np.uint32)
            n_points = pts.shape[0]
            n_tris = idx.shape[0]

            all_points.append(pts)
            all_indices.append(idx + point_offset)
            meta.append({
                "name": p["name"],
                "nPoints": int(n_points),
                "nTriangles": int(n_tris),
            })
            point_offset += n_points

        if all_points:
            positions = np.concatenate(all_points).ravel()
            indices = np.concatenate(all_indices).ravel()
        else:
            positions = np.array([], dtype=np.float32)
            indices = np.array([], dtype=np.uint32)

        buf = positions.tobytes() + indices.tobytes()
        return buf, meta

    @staticmethod
    def pack_field_binary(values: np.ndarray, n_components: int = 1) -> bytes:
        """Pack scalar/vector field into binary buffer.

        Layout:
          Header (16 bytes): [Float32 min] [Float32 max] [Uint32 nComp] [Uint32 nPts]
          Data: [Float32 × nPoints × nComponents]
        """
        values = np.asarray(values, dtype=np.float32).ravel()
        n_points = len(values) // max(n_components, 1)
        fmin = float(values.min()) if len(values) > 0 else 0.0
        fmax = float(values.max()) if len(values) > 0 else 0.0
        header = struct.pack("<ffII", fmin, fmax, n_components, n_points)
        return header + values.tobytes()
```

Also add `import struct` and `import threading` to the imports at the top of the file if not already present.

- [ ] **Step 4: Run tests — should pass**

Run: `cd dctwin && conda run -n dcdtuni python -m pytest ../tests/test_cached_postprocessor.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add dctwin/dctwin/runner/postprocess.py tests/test_cached_postprocessor.py
git commit -m "feat: add CachedPostProcessor with binary mesh/field serialization"
```

---

### Task 2: Binary API Endpoints on Server

Replace the old JSON-based post-process handlers with new binary endpoints.

**Files:**
- Modify: `STLGeneratorForOpenFoam/server/server.py` (lines 237-265 globals, lines 272-844 handlers)

- [ ] **Step 1: Add new imports and replace global state**

At the top of the post-processing section (around line 237), replace the old `_POST_PROCESSOR` global:

```python
# ── Post-processing: binary pipeline ──
from dctwin.runner.postprocess import CachedPostProcessor

def _get_cached_pp() -> CachedPostProcessor:
    return CachedPostProcessor.get_instance()
```

Keep the old `_POST_PROCESSOR` and `_POST_PROCESSOR_LOCK` until old handlers are fully replaced (they can coexist during migration).

- [ ] **Step 2: Add /api/pp/init handler**

Add a new handler function (place it after the old handlers, or in a clearly marked new section):

```python
def handle_pp_init(handler, case_dir: Path):
    """POST /api/pp/init — Initialize post-processor for a run directory."""
    body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", 0))))
    run_dir_rel = body.get("runDir", "")

    if not run_dir_rel:
        # Auto-detect latest run
        from dctwin.runner.postprocess import PostProcessor
        run_dir = PostProcessor.find_latest_run_dir(case_dir)
        if run_dir is None:
            _send_json(handler, {"success": False, "error": "No run directories found"}, 404)
            return
    else:
        run_dir = (case_dir / run_dir_rel).resolve()
        if not run_dir.exists():
            run_dir = Path(run_dir_rel).resolve()

    cpp = _get_cached_pp()
    result = cpp.init(run_dir)
    _send_json(handler, result)


def handle_pp_mesh(handler):
    """GET /api/pp/mesh — Return patch metadata as JSON."""
    cpp = _get_cached_pp()
    if cpp.multi_block is None:
        _send_json(handler, {"error": "Not initialized. Call /api/pp/init first."}, 400)
        return

    _, meta = cpp.get_mesh()
    fields = cpp.get_available_fields()
    total_points = sum(m["nPoints"] for m in meta)
    total_tris = sum(m["nTriangles"] for m in meta)
    _send_json(handler, {
        "patches": meta,
        "fields": fields,
        "totalPoints": total_points,
        "totalTriangles": total_tris,
    })


def handle_pp_mesh_binary(handler):
    """GET /api/pp/mesh/binary — Return geometry as binary ArrayBuffer."""
    cpp = _get_cached_pp()
    if cpp.multi_block is None:
        _send_json(handler, {"error": "Not initialized"}, 400)
        return

    binary, _ = cpp.get_mesh()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Content-Length", str(len(binary)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(binary)


def handle_pp_field(handler, field_name: str):
    """GET /api/pp/field/{name} — Return field scalars as binary ArrayBuffer."""
    cpp = _get_cached_pp()
    if cpp.multi_block is None:
        _send_json(handler, {"error": "Not initialized"}, 400)
        return

    binary, info = cpp.get_field(field_name)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Content-Length", str(len(binary)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("X-Field-Info", json.dumps(info))
    handler.end_headers()
    handler.wfile.write(binary)
```

- [ ] **Step 3: Add /api/pp/runs handler**

```python
def handle_pp_runs(handler, case_dir: Path):
    """GET /api/pp/runs — List available run directories."""
    log_dir = case_dir / "log"
    runs = []
    if log_dir.exists():
        for d in sorted(log_dir.iterdir(), reverse=True):
            if d.is_dir() and not d.name.startswith("."):
                runs.append({"name": d.name, "path": str(d.relative_to(case_dir))})
    _send_json(handler, {"runs": runs})
```

- [ ] **Step 4: Wire new endpoints into the request router**

In the `STLServerHandler.do_GET` and `do_POST` methods, add route matching for the new endpoints. Find the existing URL dispatch pattern (typically `if path == "/api/..."` or `if path.startswith("/api/post-process")`) and add:

```python
# In do_GET:
elif path == "/api/pp/mesh":
    handle_pp_mesh(self)
elif path == "/api/pp/mesh/binary":
    handle_pp_mesh_binary(self)
elif path.startswith("/api/pp/field/"):
    field_name = path.split("/api/pp/field/")[1]
    handle_pp_field(self, field_name)
elif path == "/api/pp/runs":
    handle_pp_runs(self, cd)

# In do_POST:
elif path == "/api/pp/init":
    handle_pp_init(self, cd)
```

- [ ] **Step 5: Test the new endpoints manually**

Restart the server and test with curl:

```bash
# Restart server
fuser -k 8000/tcp 2>/dev/null; fuser -k 8001/tcp 2>/dev/null; sleep 1
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py \
  --port 8000 --ws-port 8001 --case-dir dctwin/case/CMI-China &
sleep 4

# Test /api/pp/runs
curl -s http://localhost:8000/api/pp/runs | python3 -m json.tool

# Test /api/pp/init (auto-detect latest run)
curl -s -X POST http://localhost:8000/api/pp/init \
  -H "Content-Type: application/json" -d '{"runDir":""}' | python3 -m json.tool

# Test /api/pp/mesh (JSON metadata)
curl -s http://localhost:8000/api/pp/mesh | python3 -m json.tool | head -30

# Test /api/pp/mesh/binary (should return binary data)
curl -s http://localhost:8000/api/pp/mesh/binary -o /tmp/mesh.bin
ls -la /tmp/mesh.bin  # should be > 0 bytes

# Test /api/pp/field/T (binary field data)
curl -s http://localhost:8000/api/pp/field/T -o /tmp/field_T.bin
ls -la /tmp/field_T.bin  # should be > 16 bytes (header + data)
python3 -c "
import struct
with open('/tmp/field_T.bin','rb') as f:
    data = f.read(16)
    fmin,fmax,nc,np = struct.unpack('<ffII', data)
    print(f'T range: [{fmin:.1f}, {fmax:.1f}], nComp={nc}, nPts={np}')
"
```

Expected: All endpoints return valid data. Field T should show a temperature range (e.g., [20.0, 45.0]).

- [ ] **Step 6: Commit**

```bash
git add STLGeneratorForOpenFoam/server/server.py
git commit -m "feat: add binary post-processing API endpoints (/api/pp/*)"
```

---

### Task 3: GPU Shader Colormap Material (Frontend)

Create the Three.js ShaderMaterial with a 1D colormap texture that reads `fieldValue` per vertex.

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (in the `<script>` section, near existing `scalarToColor` function ~line 11510)

- [ ] **Step 1: Add colormap texture generation utility**

Insert after the existing `scalarToColor` function:

```javascript
// ── GPU Colormap Utilities ──

window._ppColormapPresets = {
    'coolwarm': function() {
        // Cool (blue) to Warm (red) via white midpoint, 256 entries
        var data = new Uint8Array(256 * 4);
        for (var i = 0; i < 256; i++) {
            var t = i / 255;
            var r, g, b;
            if (t < 0.5) {
                var s = t * 2;
                r = Math.round(59 + s * (255 - 59));
                g = Math.round(76 + s * (255 - 76));
                b = Math.round(192 + s * (255 - 192));
            } else {
                var s = (t - 0.5) * 2;
                r = Math.round(255 - s * (255 - 180));
                g = Math.round(255 - s * (255 - 4));
                b = Math.round(255 - s * (255 - 38));
            }
            data[i * 4] = r;
            data[i * 4 + 1] = g;
            data[i * 4 + 2] = b;
            data[i * 4 + 3] = 255;
        }
        return data;
    },
    'jet': function() {
        var data = new Uint8Array(256 * 4);
        for (var i = 0; i < 256; i++) {
            var t = i / 255;
            var r = Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 3)));
            var g = Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 2)));
            var b = Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 1)));
            data[i * 4] = Math.round(r * 255);
            data[i * 4 + 1] = Math.round(g * 255);
            data[i * 4 + 2] = Math.round(b * 255);
            data[i * 4 + 3] = 255;
        }
        return data;
    },
    'viridis': function() {
        // Simplified viridis: dark purple → teal → yellow
        var data = new Uint8Array(256 * 4);
        var stops = [
            [0.0, 68, 1, 84],
            [0.25, 59, 82, 139],
            [0.5, 33, 145, 140],
            [0.75, 94, 201, 98],
            [1.0, 253, 231, 37],
        ];
        for (var i = 0; i < 256; i++) {
            var t = i / 255;
            var s0 = stops[0], s1 = stops[1];
            for (var j = 1; j < stops.length; j++) {
                if (t <= stops[j][0]) { s0 = stops[j - 1]; s1 = stops[j]; break; }
            }
            var f = (t - s0[0]) / (s1[0] - s0[0] || 1);
            data[i * 4] = Math.round(s0[1] + f * (s1[1] - s0[1]));
            data[i * 4 + 1] = Math.round(s0[2] + f * (s1[2] - s0[2]));
            data[i * 4 + 2] = Math.round(s0[3] + f * (s1[3] - s0[3]));
            data[i * 4 + 3] = 255;
        }
        return data;
    },
};

window._ppCreateColormapTexture = function(presetName) {
    var gen = window._ppColormapPresets[presetName || 'coolwarm'];
    if (!gen) gen = window._ppColormapPresets['coolwarm'];
    var data = gen();
    var tex = new THREE.DataTexture(data, 256, 1, THREE.RGBAFormat);
    tex.minFilter = THREE.LinearFilter;
    tex.magFilter = THREE.LinearFilter;
    tex.wrapS = THREE.ClampToEdgeWrapping;
    tex.needsUpdate = true;
    return tex;
};

window._ppFieldShaderMaterial = function(colormapTex, minVal, maxVal) {
    return new THREE.ShaderMaterial({
        uniforms: {
            uMinValue: { value: minVal },
            uMaxValue: { value: maxVal },
            uColorMap: { value: colormapTex },
        },
        vertexShader: [
            'attribute float fieldValue;',
            'varying float vFieldValue;',
            'void main() {',
            '    vFieldValue = fieldValue;',
            '    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);',
            '}',
        ].join('\n'),
        fragmentShader: [
            'uniform float uMinValue;',
            'uniform float uMaxValue;',
            'uniform sampler2D uColorMap;',
            'varying float vFieldValue;',
            'void main() {',
            '    float t = clamp((vFieldValue - uMinValue) / (uMaxValue - uMinValue + 1e-10), 0.0, 1.0);',
            '    gl_FragColor = texture2D(uColorMap, vec2(t, 0.5));',
            '}',
        ].join('\n'),
        side: THREE.DoubleSide,
    });
};
```

- [ ] **Step 2: Add binary ArrayBuffer parsing utilities**

```javascript
// ── Binary ArrayBuffer Parsing ──

window._ppParseMeshBinary = function(buffer, patchMeta) {
    // buffer layout: [Float32 × totalPts × 3] [Uint32 × totalTris × 3]
    var totalPts = 0, totalTris = 0;
    for (var i = 0; i < patchMeta.length; i++) {
        totalPts += patchMeta[i].nPoints;
        totalTris += patchMeta[i].nTriangles;
    }
    var posBytes = totalPts * 3 * 4;
    var positions = new Float32Array(buffer, 0, totalPts * 3);
    var indices = new Uint32Array(buffer, posBytes, totalTris * 3);
    return { positions: positions, indices: indices, totalPts: totalPts, totalTris: totalTris };
};

window._ppParseFieldBinary = function(buffer) {
    // Header: [Float32 min] [Float32 max] [Uint32 nComp] [Uint32 nPts]
    var header = new DataView(buffer, 0, 16);
    var fmin = header.getFloat32(0, true);
    var fmax = header.getFloat32(4, true);
    var nComp = header.getUint32(8, true);
    var nPts = header.getUint32(12, true);
    var values = new Float32Array(buffer, 16, nPts * nComp);
    return { min: fmin, max: fmax, nComponents: nComp, nPoints: nPts, values: values };
};
```

- [ ] **Step 3: Verify by opening the browser console**

Open the HTML file in a browser, open DevTools console, and check:

```javascript
// Should return a DataTexture object
var tex = window._ppCreateColormapTexture('coolwarm');
console.log(tex);  // THREE.DataTexture

// Should return a ShaderMaterial
var mat = window._ppFieldShaderMaterial(tex, 20, 40);
console.log(mat);  // THREE.ShaderMaterial
```

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add GPU shader colormap material and binary parsing utils"
```

---

### Task 4: ParaView-Style Right Panel UI (HTML + CSS)

Replace the existing post-processing right panel with the new ParaView-style layout.

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (right panel HTML ~lines 797-812, left panel controls ~lines 652-727)

- [ ] **Step 1: Replace the right panel HTML**

Find the existing `<div id="dctwin-postprocess-right">` section (lines 797-812) and replace it with the new ParaView-style panel:

```html
<div id="dctwin-postprocess-right" style="display:none;">
  <div class="header">📊 后处理控制面板</div>

  <!-- Run Directory Selector -->
  <div style="padding:8px; border-bottom:1px solid #444;">
    <label style="font-size:11px; color:#aaa;">Run 目录:</label>
    <div style="display:flex; gap:4px; margin-top:4px;">
      <select id="pp-run-select" style="flex:1; font-size:11px;"></select>
      <button onclick="window.ppLoadRun()" style="font-size:11px;">加载</button>
    </div>
  </div>

  <!-- Field Selector (instant switch) -->
  <div style="padding:8px; border-bottom:1px solid #444;">
    <label style="font-size:11px; color:#aaa;">渲染变量:</label>
    <select id="pp-field-select" onchange="window.ppSwitchField(this.value)"
            style="width:100%; margin-top:4px; font-size:12px;" disabled>
      <option value="">-- 请先加载 --</option>
    </select>
  </div>

  <!-- Color Bar Controls -->
  <div style="padding:8px; border-bottom:1px solid #444;">
    <label style="font-size:11px; color:#aaa;">Color Bar</label>
    <div style="display:flex; gap:4px; margin-top:4px; align-items:center;">
      <label style="font-size:11px; width:30px;">Min:</label>
      <input type="number" id="pp-clim-min" step="0.1" style="flex:1; font-size:11px;">
    </div>
    <div style="display:flex; gap:4px; margin-top:2px; align-items:center;">
      <label style="font-size:11px; width:30px;">Max:</label>
      <input type="number" id="pp-clim-max" step="0.1" style="flex:1; font-size:11px;">
    </div>
    <div style="display:flex; gap:4px; margin-top:4px; align-items:center;">
      <label style="font-size:11px; width:30px;">预设:</label>
      <select id="pp-cmap-preset" style="flex:1; font-size:11px;">
        <option value="coolwarm">Cool to Warm</option>
        <option value="jet">Jet</option>
        <option value="viridis">Viridis</option>
      </select>
    </div>
    <div style="text-align:right; margin-top:4px;">
      <button onclick="window.ppApplyClim()" style="font-size:11px;">应用</button>
    </div>
  </div>

  <!-- Read Zones Toggle -->
  <div style="padding:8px; border-bottom:1px solid #444;">
    <label style="font-size:11px;">
      <input type="checkbox" id="pp-read-zones" onchange="window.ppToggleZones(this.checked)">
      Read Zones
    </label>
  </div>

  <!-- MultiBlock Inspector Tree -->
  <div style="padding:8px; border-bottom:1px solid #444;">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <label style="font-size:11px; color:#aaa;">MultiBlock Inspector</label>
      <div style="display:flex; gap:2px;">
        <button onclick="window.ppToggleAllPatches(true)" style="font-size:10px;">全选</button>
        <button onclick="window.ppToggleAllPatches(false)" style="font-size:10px;">全不选</button>
      </div>
    </div>
    <div id="pp-tree" style="max-height:300px; overflow-y:auto; margin-top:4px;"></div>
  </div>

  <!-- Streamline Controls -->
  <div style="padding:8px;">
    <label style="font-size:11px; color:#aaa;">流线设置</label>
    <div style="margin-top:4px;">
      <label style="font-size:11px;">
        <input type="checkbox" id="pp-sl-show" onchange="window.ppToggleStreamlines(this.checked)">
        显示流线
      </label>
    </div>
    <div id="pp-sl-config" style="display:none; margin-top:4px;">
      <div style="display:flex; gap:2px; flex-wrap:wrap;">
        <label style="font-size:10px; width:45%;">X: <input type="number" id="pp-sl-cx" step="0.1" style="width:60px; font-size:10px;"></label>
        <label style="font-size:10px; width:45%;">Y: <input type="number" id="pp-sl-cy" step="0.1" style="width:60px; font-size:10px;"></label>
        <label style="font-size:10px; width:45%;">Z: <input type="number" id="pp-sl-cz" step="0.1" style="width:60px; font-size:10px;"></label>
        <label style="font-size:10px; width:45%;">R: <input type="number" id="pp-sl-r" step="0.1" style="width:60px; font-size:10px;"></label>
      </div>
      <div style="display:flex; gap:4px; margin-top:4px; align-items:center;">
        <label style="font-size:10px;">数量:</label>
        <input type="number" id="pp-sl-n" value="27" min="1" max="200" style="width:50px; font-size:10px;">
        <label style="font-size:10px;">管径:</label>
        <input type="number" id="pp-sl-tube" value="0.02" step="0.005" style="width:50px; font-size:10px;">
      </div>
      <div style="text-align:right; margin-top:4px;">
        <button onclick="window.ppGenerateStreamlines()" style="font-size:11px;">生成流线</button>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add MultiBlock Inspector tree builder function**

```javascript
window._ppBuildTree = function(patchMeta) {
    var tree = document.getElementById('pp-tree');
    tree.innerHTML = '';

    // Group: boundary (collapsible)
    var boundaryGroup = document.createElement('div');
    var boundaryHeader = document.createElement('div');
    boundaryHeader.style.cssText = 'cursor:pointer; font-size:11px; padding:2px 0; display:flex; align-items:center; gap:4px;';
    boundaryHeader.innerHTML = '<span class="pp-tree-arrow">▼</span>' +
        '<input type="checkbox" checked class="pp-group-cb" data-group="boundary">' +
        '<b>boundary</b>';
    var boundaryList = document.createElement('div');
    boundaryList.style.cssText = 'padding-left:16px;';
    boundaryList.id = 'pp-tree-boundary';

    boundaryHeader.querySelector('.pp-tree-arrow').onclick = function() {
        var collapsed = boundaryList.style.display === 'none';
        boundaryList.style.display = collapsed ? 'block' : 'none';
        this.textContent = collapsed ? '▼' : '▶';
    };
    boundaryHeader.querySelector('.pp-group-cb').onchange = function() {
        var checks = boundaryList.querySelectorAll('input[type=checkbox]');
        for (var i = 0; i < checks.length; i++) checks[i].checked = this.checked;
        window.ppUpdatePatchVisibility();
    };

    for (var i = 0; i < patchMeta.length; i++) {
        var p = patchMeta[i];
        var row = document.createElement('div');
        row.style.cssText = 'font-size:10px; padding:1px 0; display:flex; align-items:center; gap:4px;';
        row.innerHTML = '<input type="checkbox" class="pp-patch-cb" data-patch="' + p.name + '"' +
            (p.defaultVisible ? ' checked' : '') + '>' +
            '<span>' + p.name + ' <span style="color:#888;">(' + p.nTriangles + ')</span></span>';
        row.querySelector('input').onchange = function() { window.ppUpdatePatchVisibility(); };
        boundaryList.appendChild(row);
    }

    boundaryGroup.appendChild(boundaryHeader);
    boundaryGroup.appendChild(boundaryList);
    tree.appendChild(boundaryGroup);

    // internalMesh (unchecked by default)
    var imRow = document.createElement('div');
    imRow.style.cssText = 'font-size:11px; padding:2px 0; display:flex; align-items:center; gap:4px;';
    imRow.innerHTML = '▶ <input type="checkbox" class="pp-patch-cb" data-patch="internalMesh"> <b>internalMesh</b>';
    tree.appendChild(imRow);

    // zones group (disabled until Read Zones checked)
    var zonesGroup = document.createElement('div');
    zonesGroup.id = 'pp-tree-zones';
    zonesGroup.style.cssText = 'opacity:0.4; pointer-events:none;';
    zonesGroup.innerHTML = '<div style="font-size:11px; padding:2px 0;">▶ <b>zones</b> <span style="color:#888; font-size:10px;">(勾选 Read Zones)</span></div>';
    tree.appendChild(zonesGroup);
};
```

- [ ] **Step 3: Add field dropdown population function**

```javascript
window._ppPopulateFieldDropdown = function(fields) {
    var sel = document.getElementById('pp-field-select');
    sel.innerHTML = '';
    sel.disabled = false;

    // Scalar fields group
    var scalarGroup = document.createElement('optgroup');
    scalarGroup.label = '标量场';
    // Vector fields group
    var vectorGroup = document.createElement('optgroup');
    vectorGroup.label = '向量场';

    for (var i = 0; i < fields.length; i++) {
        var f = fields[i];
        if (f.type === 'scalar') {
            var opt = document.createElement('option');
            opt.value = f.name;
            opt.textContent = f.name + (f.label && f.label !== f.name ? ' (' + f.label + ')' : '');
            scalarGroup.appendChild(opt);
        } else if (f.type === 'vector') {
            // Magnitude
            var optMag = document.createElement('option');
            optMag.value = f.name + 'mag';
            optMag.textContent = f.name + ' (Magnitude)';
            vectorGroup.appendChild(optMag);
            // Components
            if (f.components) {
                for (var j = 0; j < f.components.length; j++) {
                    var optC = document.createElement('option');
                    optC.value = f.components[j];
                    optC.textContent = f.components[j];
                    vectorGroup.appendChild(optC);
                }
            }
        }
    }

    if (scalarGroup.children.length) sel.appendChild(scalarGroup);
    if (vectorGroup.children.length) sel.appendChild(vectorGroup);
};
```

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add ParaView-style right panel UI with tree inspector"
```

---

### Task 5: Wire Frontend to Binary Backend

Connect the new UI controls to the binary API endpoints.

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (replace existing `initPostProcess`, `renderPPField` and add new functions)

- [ ] **Step 1: Add new post-processing state and core data holders**

Replace or augment the existing `_ppRenderState` (around line 11492):

```javascript
// ── New Post-Processing State ──
window._ppState = {
    initialized: false,
    meshMeta: null,          // patch metadata from /api/pp/mesh
    meshGeometries: {},      // patch_name → THREE.BufferGeometry (persistent)
    fieldCache: {},          // field_name → Float32Array (client-side cache)
    currentField: null,      // currently rendered field name
    currentClim: null,       // [min, max]
    colormapTexture: null,   // THREE.DataTexture
    shaderMaterial: null,    // THREE.ShaderMaterial (shared)
    patchMeshes: {},         // patch_name → THREE.Mesh
    ppGroup: new THREE.Group(), // container for all PP meshes
    streamlineGroup: new THREE.Group(),
    colorbar: null,
};
```

- [ ] **Step 2: Implement ppLoadRun — init + load mesh + render first field**

```javascript
window.ppLoadRun = async function() {
    var sel = document.getElementById('pp-run-select');
    var runDir = sel.value;
    logOperation('PP: Loading run ' + runDir);

    // Init
    var initResp = await fetch('/api/pp/init', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({runDir: runDir}),
    });
    var initData = await initResp.json();
    if (!initData.success) { logOperation('PP init failed: ' + initData.error); return; }
    logOperation('PP: ' + initData.message + (initData.cached ? ' (cached)' : ''));

    // Fetch mesh metadata
    var meshResp = await fetch('/api/pp/mesh');
    var meshData = await meshResp.json();
    window._ppState.meshMeta = meshData;

    // Fetch binary geometry
    var binResp = await fetch('/api/pp/mesh/binary');
    var binBuf = await binResp.arrayBuffer();
    var parsed = window._ppParseMeshBinary(binBuf, meshData.patches);

    // Build Three.js geometries per patch (PERSISTENT — never rebuilt)
    window._ppBuildPatchGeometries(parsed, meshData.patches);

    // Populate UI
    window._ppBuildTree(meshData.patches);
    window._ppPopulateFieldDropdown(meshData.fields);

    // Populate run selector if not done
    // Create colormap texture
    var preset = document.getElementById('pp-cmap-preset').value || 'coolwarm';
    window._ppState.colormapTexture = window._ppCreateColormapTexture(preset);

    // Dim original geometry
    _dimOriginalGeometry(0.15);

    // Add ppGroup to scene
    if (!globalGroup.children.includes(window._ppState.ppGroup)) {
        globalGroup.add(window._ppState.ppGroup);
    }
    if (!globalGroup.children.includes(window._ppState.streamlineGroup)) {
        globalGroup.add(window._ppState.streamlineGroup);
    }

    window._ppState.initialized = true;
    window._ppState.fieldCache = {};

    // Auto-select first field and render
    var fieldSel = document.getElementById('pp-field-select');
    if (fieldSel.options.length > 0) {
        // Select first scalar field (usually T)
        for (var i = 0; i < fieldSel.options.length; i++) {
            if (fieldSel.options[i].value && fieldSel.options[i].parentNode.label === '标量场') {
                fieldSel.selectedIndex = i;
                break;
            }
        }
        window.ppSwitchField(fieldSel.value);
    }

    logOperation('PP: Mesh loaded, ' + meshData.totalPoints + ' points, ' + meshData.totalTriangles + ' triangles');
};
```

- [ ] **Step 3: Implement _ppBuildPatchGeometries — create persistent geometries**

```javascript
window._ppBuildPatchGeometries = function(parsed, patches) {
    // Dispose old geometries
    for (var name in window._ppState.patchMeshes) {
        var m = window._ppState.patchMeshes[name];
        m.geometry.dispose();
        if (m.material) m.material.dispose();
        window._ppState.ppGroup.remove(m);
    }
    window._ppState.patchMeshes = {};
    window._ppState.meshGeometries = {};

    var ptOffset = 0, triOffset = 0;
    for (var i = 0; i < patches.length; i++) {
        var p = patches[i];
        var nPts = p.nPoints;
        var nTris = p.nTriangles;

        var geo = new THREE.BufferGeometry();
        // Slice positions for this patch
        var pos = parsed.positions.subarray(ptOffset * 3, (ptOffset + nPts) * 3);
        geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));

        // Slice and de-offset indices for this patch
        var rawIdx = parsed.indices.subarray(triOffset * 3, (triOffset + nTris) * 3);
        var localIdx = new Uint32Array(rawIdx.length);
        for (var j = 0; j < rawIdx.length; j++) {
            localIdx[j] = rawIdx[j] - ptOffset;
        }
        geo.setIndex(new THREE.BufferAttribute(localIdx, 1));
        geo.computeVertexNormals();

        window._ppState.meshGeometries[p.name] = geo;

        // Create placeholder mesh (material set when field is loaded)
        var mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({color: 0x888888}));
        mesh.visible = !!p.defaultVisible;
        mesh._ppPatchName = p.name;
        mesh._ppPointOffset = ptOffset;
        mesh._ppNPoints = nPts;
        window._ppState.patchMeshes[p.name] = mesh;
        window._ppState.ppGroup.add(mesh);

        ptOffset += nPts;
        triOffset += nTris;
    }
};
```

- [ ] **Step 4: Implement ppSwitchField — instant field switching**

```javascript
window.ppSwitchField = async function(fieldName) {
    if (!fieldName || !window._ppState.initialized) return;

    var state = window._ppState;

    // Check client-side cache first
    var fieldData;
    if (state.fieldCache[fieldName]) {
        fieldData = state.fieldCache[fieldName];
    } else {
        // Fetch from server
        var resp = await fetch('/api/pp/field/' + encodeURIComponent(fieldName));
        var buf = await resp.arrayBuffer();
        fieldData = window._ppParseFieldBinary(buf);
        state.fieldCache[fieldName] = fieldData;
    }

    state.currentField = fieldName;
    state.currentClim = [fieldData.min, fieldData.max];

    // Update colorbar inputs
    document.getElementById('pp-clim-min').value = fieldData.min.toFixed(2);
    document.getElementById('pp-clim-max').value = fieldData.max.toFixed(2);

    // Create or update shader material
    var preset = document.getElementById('pp-cmap-preset').value || 'coolwarm';
    if (!state.colormapTexture) {
        state.colormapTexture = window._ppCreateColormapTexture(preset);
    }
    state.shaderMaterial = window._ppFieldShaderMaterial(
        state.colormapTexture, fieldData.min, fieldData.max
    );

    // Upload field values to each patch mesh as vertex attribute
    var patches = state.meshMeta.patches;
    var valueOffset = 0;
    for (var i = 0; i < patches.length; i++) {
        var p = patches[i];
        var mesh = state.patchMeshes[p.name];
        if (!mesh) continue;

        var nPts = p.nPoints;
        var patchValues = fieldData.values.subarray(valueOffset, valueOffset + nPts);
        mesh.geometry.setAttribute('fieldValue',
            new THREE.Float32BufferAttribute(patchValues, 1));
        mesh.material = state.shaderMaterial;
        valueOffset += nPts;
    }

    // Update colorbar display
    window._ppUpdateColorbar(fieldName, fieldData.min, fieldData.max, state.colormapTexture);

    requestRender();
    logOperation('PP: Switched to field ' + fieldName + ' [' + fieldData.min.toFixed(1) + ', ' + fieldData.max.toFixed(1) + ']');
};
```

- [ ] **Step 5: Implement ppApplyClim — update shader uniforms only**

```javascript
window.ppApplyClim = function() {
    var state = window._ppState;
    if (!state.shaderMaterial) return;

    var newMin = parseFloat(document.getElementById('pp-clim-min').value);
    var newMax = parseFloat(document.getElementById('pp-clim-max').value);
    if (isNaN(newMin) || isNaN(newMax)) return;

    state.shaderMaterial.uniforms.uMinValue.value = newMin;
    state.shaderMaterial.uniforms.uMaxValue.value = newMax;
    state.currentClim = [newMin, newMax];

    // Update colorbar
    window._ppUpdateColorbar(state.currentField, newMin, newMax, state.colormapTexture);

    requestRender();
};

window.ppUpdatePatchVisibility = function() {
    var state = window._ppState;
    var checks = document.querySelectorAll('.pp-patch-cb');
    for (var i = 0; i < checks.length; i++) {
        var name = checks[i].getAttribute('data-patch');
        var mesh = state.patchMeshes[name];
        if (mesh) mesh.visible = checks[i].checked;
    }
    requestRender();
};

window.ppToggleAllPatches = function(checked) {
    var checks = document.querySelectorAll('#pp-tree-boundary input[type=checkbox]');
    for (var i = 0; i < checks.length; i++) {
        checks[i].checked = checked;
    }
    window.ppUpdatePatchVisibility();
};
```

- [ ] **Step 6: Implement ppInitPostProcess — wired to the "开启后处理" button**

```javascript
window.ppInitPostProcess = async function() {
    // Fetch available runs
    var resp = await fetch('/api/pp/runs');
    var data = await resp.json();
    var sel = document.getElementById('pp-run-select');
    sel.innerHTML = '';
    for (var i = 0; i < data.runs.length; i++) {
        var opt = document.createElement('option');
        opt.value = data.runs[i].path;
        opt.textContent = data.runs[i].name;
        sel.appendChild(opt);
    }
    // Show right panel
    document.getElementById('dctwin-postprocess-right').style.display = 'block';
    logOperation('PP: ' + data.runs.length + ' run directories found. Select one and click 加载.');
};
```

Update the init button's onclick to call `window.ppInitPostProcess()` instead of the old `window.initPostProcess()`.

- [ ] **Step 7: Add colorbar update function**

```javascript
window._ppUpdateColorbar = function(fieldName, minVal, maxVal, colormapTex) {
    // Remove old colorbar
    if (window._ppState.colorbar) {
        document.body.removeChild(window._ppState.colorbar);
        window._ppState.colorbar = null;
    }

    // Create a canvas-based colorbar overlay
    var bar = document.createElement('div');
    bar.style.cssText = 'position:fixed; right:320px; top:50%; transform:translateY(-50%); ' +
        'width:30px; height:200px; border:1px solid #666; z-index:1000; background:#222; padding:2px;';

    var canvas = document.createElement('canvas');
    canvas.width = 26;
    canvas.height = 196;
    var ctx = canvas.getContext('2d');

    // Draw colormap gradient
    var texData = colormapTex.image.data;
    for (var y = 0; y < 196; y++) {
        var t = 1.0 - y / 195; // top = max
        var idx = Math.floor(t * 255) * 4;
        ctx.fillStyle = 'rgb(' + texData[idx] + ',' + texData[idx + 1] + ',' + texData[idx + 2] + ')';
        ctx.fillRect(0, y, 26, 1);
    }
    bar.appendChild(canvas);

    // Labels
    var labelMax = document.createElement('div');
    labelMax.style.cssText = 'position:absolute; top:-14px; left:0; font-size:10px; color:#ccc;';
    labelMax.textContent = maxVal.toFixed(1);
    bar.appendChild(labelMax);

    var labelMin = document.createElement('div');
    labelMin.style.cssText = 'position:absolute; bottom:-14px; left:0; font-size:10px; color:#ccc;';
    labelMin.textContent = minVal.toFixed(1);
    bar.appendChild(labelMin);

    var labelField = document.createElement('div');
    labelField.style.cssText = 'position:absolute; top:-28px; left:0; font-size:10px; color:#fff; font-weight:bold;';
    labelField.textContent = fieldName;
    bar.appendChild(labelField);

    document.body.appendChild(bar);
    window._ppState.colorbar = bar;
};
```

- [ ] **Step 8: Test end-to-end**

Restart server, open browser, click "开启后处理":
1. Run selector should show available runs
2. Select a run, click "加载" — should load mesh + auto-render first field
3. Switch field dropdown — should switch near-instantly (first load ~100ms, second load ~0ms)
4. Change colorbar min/max + "应用" — should update instantly (no network)
5. Toggle patch checkboxes — should toggle visibility instantly

- [ ] **Step 9: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: wire frontend to binary PP backend with instant field switching"
```

---

### Task 6: Mesh Preview Backend

Independent feature: read polyMesh boundary surfaces for mesh preview.

**Files:**
- Modify: `dctwin/dctwin/runner/postprocess.py` (append `MeshPreviewLoader` class)
- Modify: `STLGeneratorForOpenFoam/server/server.py` (add mesh-preview endpoints)

- [ ] **Step 1: Implement MeshPreviewLoader class**

Append to `dctwin/dctwin/runner/postprocess.py`:

```python
class MeshPreviewLoader:
    """Load boundary surfaces from polyMesh for mesh preview (no foamToVTK)."""

    def __init__(self, mesh_dir: Path) -> None:
        self.mesh_dir = Path(mesh_dir).resolve()
        self._binary: Optional[bytes] = None
        self._meta: Optional[List[dict]] = None

    def load(self) -> Tuple[bytes, List[dict]]:
        """Read polyMesh and extract boundary surfaces as binary geometry."""
        if self._binary is not None:
            return self._binary, self._meta

        pv = _get_pyvista()

        # Create temporary .foam file for OpenFOAMReader
        # The mesh_dir should be .../Mesh, and OpenFOAM expects case dir
        case_dir = self.mesh_dir
        foam_file = case_dir / "preview.foam"
        foam_file.touch()
        try:
            reader = pv.OpenFOAMReader(str(foam_file))
            reader.set_active_time_value(0)
            # Disable all cell/point arrays (we only want geometry)
            for name in reader.cell_array_names:
                reader.set_cell_array_status(name, False)
            reader.update()
            mb = reader.read()
        finally:
            foam_file.unlink(missing_ok=True)

        # Extract boundary block
        boundary = None
        for i in range(mb.n_blocks):
            key = mb.keys()[i]
            if key == "boundary":
                boundary = mb[i]
                break

        if boundary is None:
            return b"", []

        patches = []
        for i in range(boundary.n_blocks):
            name = boundary.keys()[i]
            patch = boundary[i]
            if patch is None or patch.n_points == 0:
                continue
            tri = patch.triangulate()
            points = np.ascontiguousarray(tri.points, dtype=np.float32)
            if hasattr(tri, "regular_faces"):
                indices = np.ascontiguousarray(tri.regular_faces, dtype=np.uint32)
            else:
                faces_arr = tri.faces
                idx, tris_list = 0, []
                while idx < len(faces_arr):
                    n = faces_arr[idx]
                    tris_list.append(faces_arr[idx + 1 : idx + 1 + n])
                    idx += 1 + n
                indices = np.array(tris_list, dtype=np.uint32)

            patches.append({"name": name, "points": points, "indices": indices})

        self._binary, self._meta = CachedPostProcessor.pack_mesh_binary(patches)
        for m in self._meta:
            m["defaultVisible"] = not m["name"].endswith("_slave")
        return self._binary, self._meta
```

- [ ] **Step 2: Add mesh-preview endpoints to server.py**

```python
_MESH_PREVIEW: Optional[Any] = None  # MeshPreviewLoader instance

def handle_mesh_preview_load(handler, case_dir: Path):
    """POST /api/mesh-preview/load — Load mesh boundary surfaces."""
    global _MESH_PREVIEW
    from dctwin.runner.postprocess import MeshPreviewLoader

    mesh_dir = case_dir / "Mesh"
    if not (mesh_dir / "constant" / "polyMesh" / "boundary").exists():
        _send_json(handler, {"error": "No mesh found at " + str(mesh_dir)}, 404)
        return

    _MESH_PREVIEW = MeshPreviewLoader(mesh_dir)
    binary, meta = _MESH_PREVIEW.load()
    total_pts = sum(m["nPoints"] for m in meta)
    total_tris = sum(m["nTriangles"] for m in meta)
    _send_json(handler, {"patches": meta, "totalPoints": total_pts, "totalTriangles": total_tris})


def handle_mesh_preview_binary(handler):
    """GET /api/mesh-preview/binary — Return mesh geometry as binary."""
    global _MESH_PREVIEW
    if _MESH_PREVIEW is None:
        _send_json(handler, {"error": "Call /api/mesh-preview/load first"}, 400)
        return
    binary, _ = _MESH_PREVIEW.load()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Content-Length", str(len(binary)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(binary)
```

Wire into router:
```python
# do_POST:
elif path == "/api/mesh-preview/load":
    handle_mesh_preview_load(self, cd)

# do_GET:
elif path == "/api/mesh-preview/binary":
    handle_mesh_preview_binary(self)
```

- [ ] **Step 3: Test mesh-preview endpoints**

```bash
curl -s -X POST http://localhost:8000/api/mesh-preview/load | python3 -m json.tool | head -20
curl -s http://localhost:8000/api/mesh-preview/binary -o /tmp/mesh_preview.bin
ls -la /tmp/mesh_preview.bin
```

- [ ] **Step 4: Commit**

```bash
git add dctwin/dctwin/runner/postprocess.py STLGeneratorForOpenFoam/server/server.py
git commit -m "feat: add mesh preview backend (MeshPreviewLoader + endpoints)"
```

---

### Task 7: Mesh Preview Frontend

Surface-with-edges rendering with transparency slider and patch list.

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Add mesh preview state and UI panel**

Add state:

```javascript
window._meshPreviewState = {
    active: false,
    group: new THREE.Group(),
    patchMeshes: {},   // name → {surface: THREE.Mesh, edges: THREE.LineSegments}
    patchMeta: null,
    opacity: 0.85,
};
```

Add HTML panel (in the right panel area, create a new div):

```html
<div id="mesh-preview-right" style="display:none;">
  <div class="header">🔲 网格预览</div>
  <div style="padding:8px; display:flex; gap:4px;">
    <button onclick="window.mpLoadMesh()">加载网格</button>
    <button onclick="window.mpClose()">关闭预览</button>
  </div>
  <div style="padding:0 8px 8px;">
    <label style="font-size:11px;">透明度:</label>
    <input type="range" id="mp-opacity" min="0" max="1" step="0.05" value="0.85"
           oninput="window.mpSetOpacity(parseFloat(this.value))"
           style="width:100%;">
  </div>
  <div style="padding:0 8px;">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <label style="font-size:11px; color:#aaa;">Patches</label>
      <button onclick="window.mpToggleAll(true)" style="font-size:10px;">全选</button>
    </div>
    <div id="mp-patch-list" style="max-height:400px; overflow-y:auto;"></div>
  </div>
</div>
```

- [ ] **Step 2: Implement mesh preview load + render functions**

```javascript
window.mpLoadMesh = async function() {
    logOperation('Mesh Preview: Loading...');

    var resp = await fetch('/api/mesh-preview/load', {method: 'POST'});
    var data = await resp.json();
    if (data.error) { logOperation('Mesh Preview Error: ' + data.error); return; }

    var binResp = await fetch('/api/mesh-preview/binary');
    var binBuf = await binResp.arrayBuffer();
    var parsed = window._ppParseMeshBinary(binBuf, data.patches);

    // Close post-processing if active
    if (window._ppState && window._ppState.initialized) {
        window.ppClearAll();
    }

    // Clear previous preview
    window.mpClearMeshes();

    var state = window._meshPreviewState;
    state.patchMeta = data.patches;
    state.active = true;

    var ptOffset = 0, triOffset = 0;
    for (var i = 0; i < data.patches.length; i++) {
        var p = data.patches[i];
        var geo = new THREE.BufferGeometry();
        var pos = parsed.positions.subarray(ptOffset * 3, (ptOffset + p.nPoints) * 3);
        geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));

        var rawIdx = parsed.indices.subarray(triOffset * 3, (triOffset + p.nTriangles) * 3);
        var localIdx = new Uint32Array(rawIdx.length);
        for (var j = 0; j < rawIdx.length; j++) localIdx[j] = rawIdx[j] - ptOffset;
        geo.setIndex(new THREE.BufferAttribute(localIdx, 1));
        geo.computeVertexNormals();

        // Surface mesh
        var surfMat = new THREE.MeshBasicMaterial({
            color: 0xcccccc, transparent: true, opacity: state.opacity,
            side: THREE.DoubleSide, depthWrite: false,
        });
        var surfMesh = new THREE.Mesh(geo, surfMat);
        surfMesh.visible = !!p.defaultVisible;

        // Edge lines
        var edgeGeo = new THREE.EdgesGeometry(geo, 15);
        var edgeMat = new THREE.LineBasicMaterial({color: 0x333333});
        var edgeLines = new THREE.LineSegments(edgeGeo, edgeMat);
        edgeLines.visible = !!p.defaultVisible;

        state.patchMeshes[p.name] = {surface: surfMesh, edges: edgeLines};
        state.group.add(surfMesh);
        state.group.add(edgeLines);

        ptOffset += p.nPoints;
        triOffset += p.nTriangles;
    }

    if (!globalGroup.children.includes(state.group)) {
        globalGroup.add(state.group);
    }

    // Build patch list
    window.mpBuildPatchList(data.patches);
    document.getElementById('mesh-preview-right').style.display = 'block';

    requestRender();
    logOperation('Mesh Preview: ' + data.patches.length + ' patches, ' +
                 data.totalPoints + ' points, ' + data.totalTriangles + ' triangles');
};

window.mpClearMeshes = function() {
    var state = window._meshPreviewState;
    for (var name in state.patchMeshes) {
        var pm = state.patchMeshes[name];
        pm.surface.geometry.dispose();
        pm.surface.material.dispose();
        pm.edges.geometry.dispose();
        pm.edges.material.dispose();
        state.group.remove(pm.surface);
        state.group.remove(pm.edges);
    }
    state.patchMeshes = {};
};

window.mpClose = function() {
    window.mpClearMeshes();
    var state = window._meshPreviewState;
    globalGroup.remove(state.group);
    state.active = false;
    document.getElementById('mesh-preview-right').style.display = 'none';
    requestRender();
};

window.mpSetOpacity = function(val) {
    var state = window._meshPreviewState;
    state.opacity = val;
    for (var name in state.patchMeshes) {
        state.patchMeshes[name].surface.material.opacity = val;
    }
    requestRender();
};

window.mpToggleAll = function(checked) {
    var checks = document.querySelectorAll('#mp-patch-list input[type=checkbox]');
    for (var i = 0; i < checks.length; i++) checks[i].checked = checked;
    window.mpUpdateVisibility();
};

window.mpUpdateVisibility = function() {
    var state = window._meshPreviewState;
    var checks = document.querySelectorAll('#mp-patch-list input[type=checkbox]');
    for (var i = 0; i < checks.length; i++) {
        var name = checks[i].getAttribute('data-patch');
        var pm = state.patchMeshes[name];
        if (pm) {
            pm.surface.visible = checks[i].checked;
            pm.edges.visible = checks[i].checked;
        }
    }
    requestRender();
};

window.mpBuildPatchList = function(patches) {
    var list = document.getElementById('mp-patch-list');
    list.innerHTML = '';
    for (var i = 0; i < patches.length; i++) {
        var p = patches[i];
        var row = document.createElement('div');
        row.style.cssText = 'font-size:10px; padding:1px 0;';
        row.innerHTML = '<label><input type="checkbox" class="mp-cb" data-patch="' + p.name + '"' +
            (p.defaultVisible ? ' checked' : '') + ' onchange="window.mpUpdateVisibility()"> ' +
            p.name + ' <span style="color:#888;">(' + p.nTriangles + ')</span></label>';
        list.appendChild(row);
    }
};
```

- [ ] **Step 3: Add a "预览网格" button to the mesh generation controls**

Find the mesh generation section in the left panel and add a button next to the existing mesh controls:

```html
<button onclick="window.mpLoadMesh()" title="Preview mesh after generation">预览网格</button>
```

- [ ] **Step 4: Test mesh preview**

1. Generate mesh for CMI-China case
2. Click "预览网格" → mesh surfaces should appear with edges
3. Adjust opacity slider → transparency updates in real-time
4. Toggle patch checkboxes → patches show/hide immediately

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html STLGeneratorForOpenFoam/server/server.py
git commit -m "feat: add mesh preview with surface-with-edges rendering"
```

---

### Task 8: Streamline Sphere Widget Fix

Fix the seed sphere so it can be dragged in the 3D viewport with TransformControls, bidirectionally synced with input fields.

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (streamline sphere section ~lines 12340-12405)

- [ ] **Step 1: Import TransformControls**

Three.js r128 includes TransformControls as an example add-on. Add the import near the top of the `<script>` section where Three.js is loaded. If using CDN:

```html
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/TransformControls.js"></script>
```

Or if Three.js is loaded locally, add the equivalent. Check the existing Three.js loading mechanism in the HTML file and follow the same pattern.

- [ ] **Step 2: Create draggable seed sphere with TransformControls**

Replace the seed sphere creation in `ppToggleStreamlines` / `showStreamlineDialog` (or the new `pp-sl-show` checkbox handler):

```javascript
window._ppSeedSphere = null;
window._ppSeedTransform = null;

window.ppToggleStreamlines = function(show) {
    var config = document.getElementById('pp-sl-config');
    config.style.display = show ? 'block' : 'none';

    if (show) {
        // Default center from mesh bounds
        var state = window._ppState;
        if (state.meshMeta && !document.getElementById('pp-sl-cx').value) {
            var patches = state.meshMeta.patches;
            // Estimate center from all patches (rough)
            document.getElementById('pp-sl-cx').value = '4.0';
            document.getElementById('pp-sl-cy').value = '4.0';
            document.getElementById('pp-sl-cz').value = '2.0';
            document.getElementById('pp-sl-r').value = '1.0';
        }
        window._ppShowSeedSphere();
    } else {
        window._ppRemoveSeedSphere();
    }
};

window._ppShowSeedSphere = function() {
    window._ppRemoveSeedSphere();

    var cx = parseFloat(document.getElementById('pp-sl-cx').value) || 4;
    var cy = parseFloat(document.getElementById('pp-sl-cy').value) || 4;
    var cz = parseFloat(document.getElementById('pp-sl-cz').value) || 2;
    var r = parseFloat(document.getElementById('pp-sl-r').value) || 1;

    var geo = new THREE.SphereGeometry(1, 24, 16); // unit sphere, scaled by r
    var mat = new THREE.MeshBasicMaterial({
        color: 0x4488ff,
        transparent: true,
        opacity: 0.3,
        side: THREE.DoubleSide,
        depthTest: false,
        depthWrite: false,
    });
    var sphere = new THREE.Mesh(geo, mat);
    sphere.position.set(cx, cy, cz);
    sphere.scale.set(r, r, r);
    sphere.renderOrder = 999;
    sphere._isPPSeedSphere = true;
    globalGroup.add(sphere);
    window._ppSeedSphere = sphere;

    // TransformControls for dragging
    var tc = new THREE.TransformControls(camera, renderer.domElement);
    tc.setMode('translate');
    tc.setSize(0.8);
    tc.attach(sphere);
    scene.add(tc);
    window._ppSeedTransform = tc;

    // Disable OrbitControls while dragging
    tc.addEventListener('dragging-changed', function(event) {
        controls.enabled = !event.value;
        if (!event.value) {
            // Drag ended — sync position to inputs
            document.getElementById('pp-sl-cx').value = sphere.position.x.toFixed(2);
            document.getElementById('pp-sl-cy').value = sphere.position.y.toFixed(2);
            document.getElementById('pp-sl-cz').value = sphere.position.z.toFixed(2);
        }
    });

    // Real-time position sync during drag
    tc.addEventListener('change', function() {
        requestRender();
    });

    // Input field → sphere sync
    var inputs = ['pp-sl-cx', 'pp-sl-cy', 'pp-sl-cz', 'pp-sl-r'];
    inputs.forEach(function(id) {
        document.getElementById(id).addEventListener('input', function() {
            if (!window._ppSeedSphere) return;
            var x = parseFloat(document.getElementById('pp-sl-cx').value) || 0;
            var y = parseFloat(document.getElementById('pp-sl-cy').value) || 0;
            var z = parseFloat(document.getElementById('pp-sl-cz').value) || 0;
            var rad = parseFloat(document.getElementById('pp-sl-r').value) || 1;
            window._ppSeedSphere.position.set(x, y, z);
            window._ppSeedSphere.scale.set(rad, rad, rad);
            requestRender();
        });
    });

    requestRender();
};

window._ppRemoveSeedSphere = function() {
    if (window._ppSeedTransform) {
        window._ppSeedTransform.detach();
        scene.remove(window._ppSeedTransform);
        window._ppSeedTransform.dispose();
        window._ppSeedTransform = null;
    }
    if (window._ppSeedSphere) {
        globalGroup.remove(window._ppSeedSphere);
        window._ppSeedSphere.geometry.dispose();
        window._ppSeedSphere.material.dispose();
        window._ppSeedSphere = null;
    }
    controls.enabled = true;
    requestRender();
};
```

- [ ] **Step 3: Test sphere interaction**

1. Enable "显示流线" checkbox
2. Sphere should appear with TransformControls gizmo (3 axes)
3. Click and drag an axis → sphere moves, OrbitControls paused
4. Release → XYZ inputs update to new position
5. Edit XYZ input fields → sphere position updates
6. Edit R input → sphere size updates

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "fix: streamline seed sphere draggable via TransformControls"
```

---

### Task 9: Streamline Generation via Binary API

Wire the "生成流线" button to a new binary streamline endpoint.

**Files:**
- Modify: `STLGeneratorForOpenFoam/server/server.py` (add /api/pp/streamlines handler)
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (ppGenerateStreamlines function)

- [ ] **Step 1: Add streamline endpoint on server**

```python
def handle_pp_streamlines(handler):
    """POST /api/pp/streamlines — Compute streamlines on server."""
    cpp = _get_cached_pp()
    internal = cpp.get_internal_mesh()
    if internal is None:
        _send_json(handler, {"error": "No internal mesh loaded"}, 400)
        return

    body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", 0))))
    center = body.get("center", list(internal.center))
    radius = body.get("radius", 1.0)
    n_seeds = body.get("nSeeds", 27)

    pv = _get_pyvista()

    # Generate seed points (uniform in sphere)
    rng = np.random.default_rng(42)
    seed_points = []
    for _ in range(n_seeds):
        u, v, w = rng.uniform(), rng.uniform(), rng.uniform()
        theta = 2 * np.pi * u
        phi = np.arccos(2 * v - 1)
        r = radius * w ** (1.0 / 3.0)
        seed_points.append([
            center[0] + r * np.sin(phi) * np.cos(theta),
            center[1] + r * np.sin(phi) * np.sin(theta),
            center[2] + r * np.cos(phi),
        ])

    seed = pv.PolyData(np.array(seed_points, dtype=np.float64))
    try:
        streams = internal.streamlines_from_source(
            source=seed, vectors="U", max_steps=2000, integration_direction="both",
        )
    except Exception as e:
        _send_json(handler, {"error": f"Streamline computation failed: {e}"})
        return

    if streams is None or streams.n_points == 0:
        _send_json(handler, {"streamlines": [], "scalarRange": [0, 0]})
        return

    # Compute speed magnitude
    if "U" in streams.point_data:
        speed = np.linalg.norm(streams.point_data["U"], axis=1).astype(np.float32)
    else:
        speed = np.zeros(streams.n_points, dtype=np.float32)

    points = streams.points.astype(np.float32)
    smin, smax = float(speed.min()), float(speed.max())

    # Extract polylines from VTK lines array
    lines_arr = streams.lines
    streamlines_meta = []
    all_data = []  # [x,y,z,scalar] per point
    idx = 0
    while idx < len(lines_arr):
        n_pts = lines_arr[idx]
        pt_indices = lines_arr[idx + 1 : idx + 1 + n_pts]
        streamlines_meta.append({"nPoints": int(n_pts)})
        for pi in pt_indices:
            all_data.extend([points[pi, 0], points[pi, 1], points[pi, 2], speed[pi]])
        idx += 1 + n_pts

    # Pack as binary: header JSON + binary data
    header_json = json.dumps({
        "streamlines": streamlines_meta,
        "scalarRange": [smin, smax],
    }).encode("utf-8")
    data_binary = np.array(all_data, dtype=np.float32).tobytes()

    # Send header length (4 bytes) + header + binary
    header_len = struct.pack("<I", len(header_json))
    payload = header_len + header_json + data_binary

    handler.send_response(200)
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(payload)
```

Add `import struct` at top if not present. Wire: `elif path == "/api/pp/streamlines": handle_pp_streamlines(self)` in do_POST.

- [ ] **Step 2: Implement ppGenerateStreamlines on frontend**

```javascript
window.ppGenerateStreamlines = async function() {
    var cx = parseFloat(document.getElementById('pp-sl-cx').value) || 4;
    var cy = parseFloat(document.getElementById('pp-sl-cy').value) || 4;
    var cz = parseFloat(document.getElementById('pp-sl-cz').value) || 2;
    var r = parseFloat(document.getElementById('pp-sl-r').value) || 1;
    var n = parseInt(document.getElementById('pp-sl-n').value) || 27;
    var tubeRadius = parseFloat(document.getElementById('pp-sl-tube').value) || 0.02;

    logOperation('PP: Computing streamlines...');

    var resp = await fetch('/api/pp/streamlines', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({center: [cx, cy, cz], radius: r, nSeeds: n}),
    });
    var buf = await resp.arrayBuffer();

    // Parse: [Uint32 headerLen] [JSON header] [Float32 data]
    var view = new DataView(buf);
    var headerLen = view.getUint32(0, true);
    var headerStr = new TextDecoder().decode(new Uint8Array(buf, 4, headerLen));
    var header = JSON.parse(headerStr);
    var dataOffset = 4 + headerLen;
    var data = new Float32Array(buf, dataOffset);

    if (!header.streamlines || header.streamlines.length === 0) {
        logOperation('PP: No streamlines generated');
        return;
    }

    // Clear old streamlines
    var slGroup = window._ppState.streamlineGroup;
    while (slGroup.children.length > 0) {
        var c = slGroup.children[0];
        c.geometry.dispose();
        c.material.dispose();
        slGroup.remove(c);
    }

    var smin = header.scalarRange[0], smax = header.scalarRange[1];
    var colormapTex = window._ppCreateColormapTexture('jet');

    // Build tube meshes
    var dataIdx = 0;
    for (var i = 0; i < header.streamlines.length; i++) {
        var nPts = header.streamlines[i].nPoints;
        if (nPts < 2) { dataIdx += nPts * 4; continue; }

        var points = [];
        var scalars = [];
        for (var j = 0; j < nPts; j++) {
            points.push(new THREE.Vector3(data[dataIdx], data[dataIdx + 1], data[dataIdx + 2]));
            scalars.push(data[dataIdx + 3]);
            dataIdx += 4;
        }

        var curve = new THREE.CatmullRomCurve3(points);
        var segments = Math.min(points.length * 2, 500);
        var tubeGeo = new THREE.TubeGeometry(curve, segments, tubeRadius, 6, false);

        // Color by scalar (speed)
        var colors = new Float32Array(tubeGeo.attributes.position.count * 3);
        var texData = colormapTex.image.data;
        for (var vi = 0; vi < tubeGeo.attributes.position.count; vi++) {
            // Map tube vertex to nearest streamline point
            var frac = vi / tubeGeo.attributes.position.count;
            var si = Math.floor(frac * (scalars.length - 1));
            var t = Math.max(0, Math.min(1, (scalars[si] - smin) / (smax - smin + 1e-10)));
            var ci = Math.floor(t * 255) * 4;
            colors[vi * 3] = texData[ci] / 255;
            colors[vi * 3 + 1] = texData[ci + 1] / 255;
            colors[vi * 3 + 2] = texData[ci + 2] / 255;
        }
        tubeGeo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));

        var tubeMat = new THREE.MeshBasicMaterial({vertexColors: true});
        var tubeMesh = new THREE.Mesh(tubeGeo, tubeMat);
        slGroup.add(tubeMesh);
    }

    // Remove seed sphere after generating
    window._ppRemoveSeedSphere();
    document.getElementById('pp-sl-show').checked = false;
    document.getElementById('pp-sl-config').style.display = 'none';

    requestRender();
    logOperation('PP: ' + header.streamlines.length + ' streamlines generated, speed range [' +
                 smin.toFixed(2) + ', ' + smax.toFixed(2) + ']');
};
```

- [ ] **Step 3: Test streamlines end-to-end**

1. Load a run with solved results
2. Check "显示流线" → seed sphere appears with drag gizmo
3. Adjust position by dragging or input fields
4. Click "生成流线" → streamline tubes appear colored by speed
5. Sphere disappears after generation

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/server/server.py STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add binary streamline generation endpoint + frontend rendering"
```

---

### Task 10: Cleanup — Remove Old Post-Processing Handlers

Remove the old JSON-based handlers and globals that are now superseded.

**Files:**
- Modify: `STLGeneratorForOpenFoam/server/server.py`
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`

- [ ] **Step 1: Remove old server handlers**

Remove or comment out the following old functions in `server.py`:
- `_POST_PROCESSOR` global and `_POST_PROCESSOR_LOCK` (lines 237-238)
- `_get_or_init_postprocessor()` (lines 241-265)
- `handle_postprocess_init()` (lines 272-328)
- `handle_postprocess_render()` (lines 335-401)
- `handle_postprocess_patches()` (lines 408-422)
- `handle_postprocess_mesh_data()` (lines 534-844)

Also remove the old route matches in `do_GET` / `do_POST` for `/api/post-process/*`.

Keep old endpoint paths as thin redirects to new endpoints if backward compatibility is needed (optional — the frontend is updated to use new paths).

- [ ] **Step 2: Remove old frontend functions**

Remove or comment out old functions in `STL_generate.html`:
- Old `window.initPostProcess` (lines 11957-12013)
- Old `window.renderPPField` / `window.renderTemperature` / `window.renderVelocity` (lines 12167-12307)
- Old `scalarToColor` CPU loop (can keep as fallback but no longer primary)
- Old `_ppRenderState` (lines 11492-11504) — replaced by `_ppState`
- Old `window.applyStreamline` (lines 12408-12539)
- Old `window.showStreamlineDialog` (lines 12368-12405)

Update the "开启后处理" button to call `window.ppInitPostProcess()`.

- [ ] **Step 3: Verify no references to old endpoints remain**

```bash
grep -n "post-process" STLGeneratorForOpenFoam/web/STL_generate.html | head -20
grep -n "post-process" STLGeneratorForOpenFoam/server/server.py | head -20
```

Expected: Only comments or the old handler definitions (now removed). No active fetch() calls to `/api/post-process/*`.

- [ ] **Step 4: End-to-end regression test**

Full workflow test:
1. Open browser → load CMI-China project
2. Click "开启后处理" → run list populates
3. Select run → click "加载" → mesh loads, field dropdown populates
4. Switch fields (T → U → p) → each switch is near-instant
5. Adjust colorbar min/max → click "应用" → instant color update
6. Toggle patches → instant visibility change
7. Enable streamlines → drag sphere → generate → tubes appear
8. Close post-processing → click "预览网格" → mesh preview with edges
9. Adjust transparency → instant update

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/server/server.py STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "refactor: remove old JSON-based post-processing handlers"
```

---

## Dependency Graph

```
Task 1 (CachedPostProcessor backend)
  ↓
Task 2 (Binary API endpoints)
  ↓
Task 3 (GPU Shader + binary parsing) ──→ Task 5 (Wire frontend to backend)
  ↓                                         ↓
Task 4 (ParaView UI panel) ────────────→ Task 5
                                            ↓
Task 6 (Mesh Preview backend) ──→ Task 7 (Mesh Preview frontend)
                                            ↓
Task 8 (Streamline sphere fix) ──→ Task 9 (Streamline binary endpoint)
                                            ↓
                                     Task 10 (Cleanup old handlers)
```

Tasks 1-5 are the critical path. Tasks 6-7 and 8-9 can proceed in parallel after Task 2.
