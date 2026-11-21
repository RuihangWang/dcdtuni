# Post-Processing Redesign: In-Memory Pipeline + ParaView-Style UI

**Date:** 2026-03-26
**Status:** Approved
**Scope:** Server-side data pipeline, frontend post-processing UI, mesh preview, streamline widget fix

---

## 1. Problem Statement

The current post-processing system has three performance issues:

1. **Slow init**: Clicking "开启后处理" triggers foamToVTK + PyVista VTM load + full mesh extraction every time. The global `_POST_PROCESSOR` is reset on each init call.
2. **Slow field switching**: Switching from Temperature to Velocity triggers a full server round-trip that re-triangulates all patches, re-extracts scalars, JSON-encodes everything, and rebuilds all Three.js meshes from scratch.
3. **No mesh preview**: After mesh generation, users cannot visually inspect the mesh quality without running the full simulation + post-processing pipeline.

Additionally, the streamline seed sphere cannot be selected or dragged in the 3D viewport.

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│ Server (Python, PyVista)                                  │
│                                                           │
│  CachedPostProcessor (singleton, in-memory)               │
│  ├─ foamToVTK (runs once per run dir)                     │
│  ├─ PyVista MultiBlock (persists in memory)                │
│  ├─ Triangulated patches (cached after first extraction)  │
│  └─ Field arrays (cached on demand per field)             │
│                                                           │
│  MeshPreviewLoader (independent, reads polyMesh directly) │
│                                                           │
│  Binary REST API:                                         │
│    POST /api/pp/init         → load VTM into memory       │
│    GET  /api/pp/mesh         → patch metadata (JSON)       │
│    GET  /api/pp/mesh/binary  → geometry (ArrayBuffer)      │
│    GET  /api/pp/field/{name} → scalars (ArrayBuffer)       │
│    GET  /api/pp/zones        → zone data (ArrayBuffer)     │
│    POST /api/pp/streamlines  → compute streamlines         │
│    POST /api/mesh-preview/load → mesh geometry             │
└───────────────────────┬──────────────────────────────────┘
                        │ Binary ArrayBuffer
                        ▼
┌──────────────────────────────────────────────────────────┐
│ Browser (Three.js)                                        │
│                                                           │
│  GPU Shader Colormap:                                     │
│  ├─ Vertex attribute: fieldValue (Float32)                │
│  ├─ Uniform: uMinValue, uMaxValue                        │
│  ├─ 1D texture: colormap (256 entries)                    │
│  └─ Field switch = re-upload attribute, geometry stays    │
│                                                           │
│  Colorbar range change = update uniforms only (<1ms)      │
│  Patch visibility = mesh.visible toggle (no server call)  │
└──────────────────────────────────────────────────────────┘
```

**Approach:** PyVista backend + Binary REST API + Three.js GPU shader rendering (Method 1 from brainstorming). Chosen for minimal rewrite, maximum reuse of existing PyVista + Three.js code.

## 3. Module 1: Server-Side Data Pipeline

### 3.1 CachedPostProcessor

A singleton that replaces the current `_POST_PROCESSOR` pattern. Persists in memory until the user explicitly switches run directories.

**Lifecycle:**
1. User selects run directory + clicks "加载"
2. Server checks if foamToVTK output exists and is fresh; runs foamToVTK if needed
3. PyVista loads VTM file → `MultiBlock` held in memory
4. Patches are triangulated on first mesh request and cached
5. Field arrays are extracted on first request per field and cached
6. Switching run directory → clear all caches → reload

**Cache structure:**
```python
class CachedPostProcessor:
    _instance: Optional["CachedPostProcessor"] = None
    _lock: threading.Lock

    run_dir: Path                              # current run directory
    multi_block: pyvista.MultiBlockDataSet     # VTM data, persists in memory
    _tri_cache: Dict[str, TriangulatedPatch]   # patch_name → {points, indices}
    _field_cache: Dict[str, FieldData]         # field_name → {scalars, range}
    _zones_cache: Optional[ZonesData]          # lazy-loaded zones

    def get_mesh(self) -> Tuple[bytes, List[PatchMeta]]:
        """Return binary geometry + patch metadata. Cached after first call."""

    def get_field(self, name: str) -> Tuple[bytes, float, float, int]:
        """Return binary scalars + range. Cached per field."""

    def get_zones(self) -> Tuple[bytes, List[ZoneMeta]]:
        """Return zone geometry. Only computed when Read Zones is checked."""

    def invalidate(self):
        """Clear all caches. Called when switching run directory."""
```

### 3.2 Binary Wire Protocol

**`POST /api/pp/init`** — Initialize post-processor for a run directory:
```json
Request:  { "runDir": "log/2026-03-26-14-46-51_CMI-China" }
Response: { "success": true, "message": "VTM loaded, 269 patches, 8 fields" }
```

Runs foamToVTK if VTK output is stale, then loads VTM into memory. If the same run directory is already loaded, returns immediately (no reload).

**`GET /api/pp/mesh`** — JSON metadata response:
```json
{
  "patches": [
    {"name": "Room_wall_front", "nPoints": 1234, "nTriangles": 2345, "defaultVisible": true},
    {"name": "ACU_4_Front_slave", "nPoints": 100, "nTriangles": 180, "defaultVisible": false}
  ],
  "fields": [
    {"name": "T", "type": "scalar", "label": "Temperature"},
    {"name": "p", "type": "scalar", "label": "Pressure"},
    {"name": "U", "type": "vector", "label": "Velocity", "components": ["Ux", "Uy", "Uz"]}
  ],
  "totalPoints": 56789,
  "totalTriangles": 98765
}
```

`defaultVisible` is `false` for patches ending in `_slave` and for `internalMesh`.

**`GET /api/pp/mesh/binary`** — Binary ArrayBuffer:
```
Layout:
  [Float32 × totalPoints × 3]    ← vertex positions (x,y,z interleaved)
  [Uint32  × totalTriangles × 3] ← triangle indices (i,j,k interleaved)
```

Patch boundaries are determined by `nPoints` and `nTriangles` from the metadata — patches are concatenated in order, with indices offset per patch.

**`GET /api/pp/field/{name}`** — Binary ArrayBuffer:
```
Layout:
  Header (16 bytes):
    [Float32 min] [Float32 max] [Uint32 nComponents] [Uint32 nPoints]
  Data:
    [Float32 × nPoints × nComponents]

  Scalar (T, p, k): nComponents=1 → nPoints floats
  Vector (U):       nComponents=3 → nPoints×3 floats (Ux,Uy,Uz interleaved)
```

For vector fields, the server also computes and appends magnitude as a derived field (`U_magnitude`), cached alongside the raw components.

**`GET /api/pp/zones`** — Same binary protocol as mesh, but only zone geometry.

### 3.3 Streamline Computation

Streamlines remain server-side (requires full volume mesh for integration).

**`POST /api/pp/streamlines`**
```json
Request: {
  "center": [x, y, z],
  "radius": r,
  "nSeeds": 27,
  "field": "U"
}
Response (binary):
  Header (JSON): { "streamlines": [{"nPoints": N}, ...], "scalarRange": [min, max] }
  Binary: [Float32 × totalPoints × 4]  ← x,y,z,scalar per point
```

## 4. Module 2: Frontend UI — ParaView-Style Controls

### 4.1 Right Panel Layout

```
┌─────────────────────────────────────────┐
│ 后处理控制面板                            │
├─────────────────────────────────────────┤
│ Run 目录: [▼ 2026-03-26-14-46-51_CMI.. ]│
│                              [加载]      │
├─────────────────────────────────────────┤
│ 渲染变量: [▼ T (Temperature)          ]  │
├─────────────────────────────────────────┤
│ Color Bar                               │
│  Min: [____20.0____]                    │
│  Max: [____45.0____]                    │
│  Preset: [▼ Cool to Warm  ]            │
│                              [应用]      │
├─────────────────────────────────────────┤
│ ☐ Read Zones                            │
├─────────────────────────────────────────┤
│ MultiBlock Inspector            [全选]   │
│ ▼ boundary                        ☑     │
│   ├ ☑ Room_wall_front          (1727)   │
│   ├ ☑ Room_wall_back           (1100)   │
│   ├ ☑ Room_floor               (5243)   │
│   ├ ☐ ACU_4_Front_slave         (280)   │
│   └ ...                                 │
│ ▶ internalMesh                    ☐     │
│ ▼ zones                                 │
│   ▶ cellZones                           │
│   └ ▶ faceZones                         │
├─────────────────────────────────────────┤
│ 流线设置                                 │
│  ☐ 显示流线                              │
│  种子球: X[___] Y[___] Z[___] R[___]    │
│  数量: [▼ 27 ]  管径: [0.02]            │
│                         [生成流线]       │
└─────────────────────────────────────────┘
```

### 4.2 Interaction Behavior Table

| Action | Behavior | Network | Latency |
|--------|----------|---------|---------|
| Select run dir + click "加载" | foamToVTK (if needed) + load VTM + fetch mesh | `POST /api/pp/init` + `GET /api/pp/mesh` + `GET /api/pp/mesh/binary` | 2-30s (first time) |
| Switch field dropdown | Fetch field scalars (or from cache) → upload to GPU | `GET /api/pp/field/{name}` (first time only) | ~100ms (cached: 0ms) |
| Change colorbar min/max + "应用" | Update shader uniforms `uMinValue`, `uMaxValue` | None | <1ms |
| Toggle patch checkbox | `mesh.visible = true/false` | None | <1ms |
| Check "Read Zones" | Fetch zone geometry from server | `GET /api/pp/zones` | ~1-5s |
| Drag streamline sphere | Update sphere position in real-time | None | <1ms |
| Click "生成流线" | Server computes streamlines | `POST /api/pp/streamlines` | ~5-15s |

### 4.3 GPU Shader Colormap

Custom `ShaderMaterial` replaces the current CPU-side `scalarToColor()` loop:

```glsl
// Vertex shader
attribute float fieldValue;
varying float vFieldValue;
void main() {
    vFieldValue = fieldValue;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}

// Fragment shader
uniform float uMinValue;
uniform float uMaxValue;
uniform sampler2D uColorMap;  // 1D texture, 256×1 pixels
varying float vFieldValue;
void main() {
    float t = clamp((vFieldValue - uMinValue) / (uMaxValue - uMinValue), 0.0, 1.0);
    gl_FragColor = texture2D(uColorMap, vec2(t, 0.5));
}
```

**Benefits:**
- Colorbar range adjustment = change 2 uniforms, zero data transfer
- Field switch = upload new Float32Array to `fieldValue` attribute, geometry stays in GPU
- Colormap preset switch = re-upload 256×1 texture

### 4.4 Field Dropdown Contents

```
▼ 渲染变量
  ─── 标量场 ───
  T (Temperature)
  p
  p_rgh
  k
  epsilon
  ─── 向量场 ───
  U (Magnitude)
  Ux
  Uy
  Uz
```

Vector field `U` is automatically expanded into Magnitude + 3 components, matching ParaView behavior. The dropdown is dynamically populated from the `fields` array returned by `/api/pp/mesh`.

### 4.5 MultiBlock Inspector Tree

- **Grouping**: `boundary` (collapsible group containing all patches), `internalMesh` (single entry), `zones` (collapsible, sub-grouped into `cellZones` and `faceZones`)
- **Default visibility**: all boundary patches visible except `*_slave` patterns; `internalMesh` unchecked; zones unchecked
- **Face count**: shown in parentheses after each patch name
- **Parent checkbox**: toggles all children. Indeterminate state (dash) when partially checked.
- **Zones group**: grayed out / disabled until "Read Zones" checkbox is checked. Checking it triggers server request to load zone data.

## 5. Module 3: Mesh Preview (Independent Feature)

### 5.1 Purpose

Preview the mesh geometry after mesh generation (snappyHexMesh) completes. Independent from the post-processing pipeline — reads directly from `Mesh/constant/polyMesh/`, no foamToVTK needed.

### 5.2 API

**`POST /api/mesh-preview/load`**
- Reads `Mesh/constant/polyMesh/` (points, faces, owner, neighbour, boundary)
- Uses PyVista `pyvista.OpenFOAMReader` or direct polyMesh parsing to extract boundary surfaces
- Returns same binary protocol as post-processing mesh endpoint
- Response: `{ patches: [{name, nFaces, defaultVisible}], binaryUrl }`

**`GET /api/mesh-preview/binary`**
- Binary ArrayBuffer with patch geometry (same format as `/api/pp/mesh/binary`)

### 5.3 Rendering Style: Surface with Edges

Each patch rendered as two overlaid Three.js objects:
1. **Surface**: `MeshBasicMaterial({ color: 0xcccccc, transparent: true, opacity: 0.85 })`
2. **Edges**: `EdgesGeometry` → `LineSegments({ color: 0x333333, linewidth: 1 })`

### 5.4 Right Panel

```
┌─────────────────────────────────┐
│ 网格预览                        │
│         [加载网格] [关闭预览]     │
├─────────────────────────────────┤
│ 透明度: ──●──────── 0.85        │
├─────────────────────────────────┤
│ Patches                  [全选] │
│ ☑ Room_wall_front        (1727) │
│ ☑ Room_floor             (5243) │
│ ☑ A00_frame               (48) │
│ ☐ A00_server_1_outlet_slave (0) │
│ └ ...                          │
└─────────────────────────────────┘
```

### 5.5 Isolation from Post-Processing

- Mesh preview and post-processing use separate Three.js `Group` objects
- Opening post-processing automatically closes mesh preview (and vice versa)
- Separate server-side instances — `MeshPreviewLoader` reads `Mesh/`, `CachedPostProcessor` reads `log/{run}/`
- No shared caches between the two features

## 6. Module 4: Streamline Sphere Widget Fix

### 6.1 Root Cause

The seed sphere's `Mesh` object is occluded by other geometry in the raycaster hit test, or `TransformControls` is not properly attached. `OrbitControls` may also be consuming mouse events before the sphere can receive them.

### 6.2 Fix Design

**Rendering priority:**
- Sphere material: `depthTest: false`, `renderOrder: 999` → always renders on top, never occluded
- Semi-transparent: `opacity: 0.3`, `transparent: true`

**Drag interaction via `TransformControls`:**
- `TransformControls` in `translate` mode attached to the sphere mesh
- When `TransformControls` is active (dragging): `OrbitControls.enabled = false`
- When drag ends: `OrbitControls.enabled = true`
- During drag: real-time update of XYZ input fields in the right panel

**Radius control:**
- Right panel `R` input field adjusts `sphere.scale.set(r, r, r)`
- No 3D drag handle for radius (complexity not justified — input field is precise enough)

**Bidirectional sync:**
- Drag sphere → input fields update in real-time
- Edit input fields → sphere position/radius updates
- Input fields: `X`, `Y`, `Z` (position), `R` (radius)

**Event priority chain:**
```
mousedown →
  1. TransformControls.onPointerDown (if hovering sphere gizmo) → consume event
  2. Raycaster picking (if hovering other mesh) → select patch
  3. OrbitControls.onMouseDown → rotate/pan/zoom camera
```

## 7. Performance Targets

| Metric | Current | Target |
|--------|---------|--------|
| "开启后处理" (first time, needs foamToVTK) | 30-120s | 30-120s (unavoidable, foamToVTK) |
| "开启后处理" (VTK cached) | 10-30s | 2-5s (skip foamToVTK, load VTM only) |
| Field switch (first time) | 5-15s | 100-500ms (binary transfer + GPU upload) |
| Field switch (cached) | 5-15s | <50ms (cached scalars, GPU upload only) |
| Colorbar range change | 2-5s | <1ms (shader uniform update) |
| Patch visibility toggle | 1-2s | <1ms (mesh.visible flag) |
| Mesh preview load | N/A (new) | 2-5s |

## 8. Files to Modify

| File | Changes |
|------|---------|
| `STLGeneratorForOpenFoam/server/server.py` | Replace post-process handlers with new binary API endpoints; add `CachedPostProcessor` singleton; add mesh-preview endpoints |
| `dctwin/dctwin/runner/postprocess.py` | Refactor `CFDPostProcessor` to support mesh/field separation and caching; add `MeshPreviewLoader` class |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | New ParaView-style right panel UI; GPU shader colormap material; binary ArrayBuffer parsing; field dropdown with instant switch; streamline sphere TransformControls fix |

## 9. Out of Scope

- Volume rendering (slicing through internalMesh) — future enhancement
- Multi-timestep animation — future enhancement
- Distributed/multi-user caching (Redis etc.) — single-user server is sufficient
- vtk.js or trame integration — staying with Three.js
- Probe tool (click-to-query point values) — future enhancement
