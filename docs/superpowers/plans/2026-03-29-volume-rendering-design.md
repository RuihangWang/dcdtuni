# Volume Rendering for STLGenerator Post-Processing

## Overview

Add GPU ray-cast volume rendering to the browser-based CFD post-processing pipeline. Users can visualize the full 3D scalar field (temperature, velocity magnitude, etc.) as a semi-transparent volume, similar to ParaView's "Volume" representation.

## Architecture

### Data Flow

```
Backend (PyVista/VTK 9.5)              Frontend (Three.js r128, WebGL2)
──────────────────────────             ─────────────────────────────────
POST /api/pp/volume
  {field, resolution}
        │
        ▼
1. internal_mesh (UnstructuredGrid)
2. pv.ImageData(dims=[N,N,N]).sample(internal_mesh)
3. Extract scalar array → Float32
4. Pack binary: [header][data]
        │
        ▼                               1. Parse binary → Float32Array
   HTTP binary response ──────────────> 2. Upload as THREE.DataTexture3D (sampler3D)
                                        3. Create bounding box mesh (BoxGeometry)
                                        4. Custom ShaderMaterial: ray marching
                                        5. Front-to-back compositing
                                        6. Render into scene
```

### Backend API

**Endpoint:** `POST /api/pp/volume`

**Request:**
```json
{
  "field": "T",
  "resolution": 128
}
```

**Response:** `application/octet-stream`

Binary format:
```
[uint32 headerLength]
[JSON header, padded to 4-byte alignment]
[Float32Array: N*N*N scalar values, z-major order]
```

JSON header:
```json
{
  "dimensions": [128, 128, 128],
  "origin": [xmin, ymin, zmin],
  "spacing": [dx, dy, dz],
  "scalarRange": [min, max],
  "field": "T"
}
```

**Implementation (in postprocess.py):**

```python
def get_volume(self, field: str, resolution: int = 128) -> Tuple[dict, np.ndarray]:
    internal = self._internal  # PyVista UnstructuredGrid
    bounds = internal.bounds    # (xmin,xmax, ymin,ymax, zmin,zmax)

    # Create uniform grid covering the domain
    N = resolution
    origin = [bounds[0], bounds[2], bounds[4]]
    spacing = [
        (bounds[1] - bounds[0]) / (N - 1),
        (bounds[3] - bounds[2]) / (N - 1),
        (bounds[5] - bounds[4]) / (N - 1),
    ]
    grid = pv.ImageData(dimensions=(N, N, N), spacing=spacing, origin=origin)
    resampled = grid.sample(internal)

    # Extract scalar field
    scalars = resampled[field].astype(np.float32)
    # Mark points outside the mesh as NaN → shader treats as transparent
    mask = resampled['vtkValidPointMask']
    scalars[mask == 0] = np.nan

    header = {
        "dimensions": [N, N, N],
        "origin": origin,
        "spacing": spacing,
        "scalarRange": [float(np.nanmin(scalars)), float(np.nanmax(scalars))],
        "field": field,
    }
    return header, scalars
```

### Frontend Shader

**Vertex Shader:** Pass texture coordinates (bounding box [0,1]^3 mapped corners) and world position.

**Fragment Shader (ray marching):**

```glsl
uniform sampler3D uVolume;         // 3D scalar texture
uniform sampler2D uColorMap;       // 1D colormap (same as surface field)
uniform float uMinValue, uMaxValue;
uniform float uOpacity;            // global opacity scale
uniform vec3 uBoxMin, uBoxMax;     // world-space bounding box

// Ray marching parameters
const int MAX_STEPS = 256;
const float STEP_SIZE = 1.732 / 256.0;  // sqrt(3)/256, diagonal of unit cube

void main() {
    vec3 rayOrigin = ...; // camera position in box-local [0,1]^3
    vec3 rayDir = ...;    // normalized direction

    vec4 accumulated = vec4(0.0);

    for (int i = 0; i < MAX_STEPS; i++) {
        vec3 samplePos = rayOrigin + rayDir * float(i) * STEP_SIZE;

        // Exit if outside [0,1]^3
        if (any(lessThan(samplePos, vec3(0))) || any(greaterThan(samplePos, vec3(1))))
            break;

        float scalar = texture(uVolume, samplePos).r;

        // NaN check: skip points outside mesh (masked during resampling)
        if (scalar != scalar) continue;  // NaN != NaN

        // Normalize to [0,1]
        float t = clamp((scalar - uMinValue) / (uMaxValue - uMinValue + 1e-10), 0.0, 1.0);

        // Sample colormap + opacity
        vec4 color = texture2D(uColorMap, vec2(t, 0.5));
        float alpha = t * uOpacity * 0.05;  // linear opacity, scaled by step

        // Front-to-back compositing (premultiplied alpha)
        color.rgb *= alpha;
        accumulated += (1.0 - accumulated.a) * vec4(color.rgb, alpha);

        // Early termination
        if (accumulated.a > 0.95) break;
    }

    gl_FragColor = accumulated;
}
```

### UI Layout (Post-processing tab, left panel)

Added below the slice settings section:

```html
<!-- Volume Rendering Settings -->
<div class="prop-group">
  <div class="prop-title">🧊 体渲染设置</div>
  <div class="prop-row">
    <label>配置体渲染</label>
    <input type="checkbox" id="pp-vol-show" onchange="ppToggleVolumeConfig(this.checked)">
  </div>
  <div id="pp-vol-config" style="display:none;">
    <div class="prop-row">
      <label>渲染场</label>
      <select id="pp-vol-field"><!-- populated from available fields --></select>
    </div>
    <div class="prop-row">
      <label>分辨率</label>
      <div>
        <label><input type="radio" name="pp-vol-res" value="64"> 64</label>
        <label><input type="radio" name="pp-vol-res" value="128" checked> 128</label>
        <label><input type="radio" name="pp-vol-res" value="256"> 256</label>
      </div>
    </div>
    <div class="prop-row">
      <label>透明度</label>
      <input type="range" id="pp-vol-opacity" min="0" max="1" step="0.05" value="0.5">
    </div>
    <div class="prop-row">
      <label>色谱</label>
      <select id="pp-vol-cmap">
        <option value="coolwarm">Coolwarm</option>
        <option value="jet">Jet</option>
        <option value="viridis">Viridis</option>
      </select>
    </div>
    <div class="prop-row"><label>值域范围</label></div>
    <div>
      Min: <input type="number" id="pp-vol-clim-min" step="0.1">
      Max: <input type="number" id="pp-vol-clim-max" step="0.1">
    </div>
    <button onclick="ppApplyVolume()">应用</button>
  </div>
  <div class="prop-row">
    <label>显示体渲染</label>
    <input type="checkbox" id="pp-vol-visible" checked
           onchange="ppToggleVolumeVisibility(this.checked)">
  </div>
</div>
```

### Rendering Order

The volume bounding box mesh is added to a dedicated `volumeGroup` in the scene:

```
scene
  └─ globalGroup
       ├─ modelGroup (3D geometry — racks, ACUs, etc.)
       ├─ ppGroup (surface field patches)
       ├─ volumeGroup (volume rendering bounding box) ← NEW
       ├─ streamlineGroup
       └─ sliceGroup
```

The ShaderMaterial uses `transparent: true`, `depthWrite: false`, `depthTest: true` so:
- Volume does not occlude solid geometry (depthWrite off)
- Solid geometry correctly occludes the volume (depthTest on)
- Slices and streamlines render on top

### Key Design Decisions

1. **Resample on server, ray march on client.** The server does the heavy computation (unstructured → structured interpolation). The client receives a simple 3D array and does GPU ray marching. This keeps the shader simple and leverages WebGL2's native `sampler3D`.

2. **NaN masking for outside-mesh regions.** `vtkValidPointMask` from PyVista's `sample()` identifies grid points outside the fluid domain. These are set to NaN, and the shader skips them (`scalar != scalar`).

3. **Fixed step count (256).** ParaView uses adaptive stepping, but for V1 a fixed 256 steps with `sqrt(3)/256` step size gives adequate quality for 128^3 volumes.

4. **Same colormap system as surface field.** Reuses `_ppColormapPresets` and `_ppColormapTexture()` already in the HTML.

5. **Apply button (not real-time).** Resolution changes require a server round-trip (128^3 = 8MB), so changes are batched and applied on click.

### Files Modified

| File | Changes |
|------|---------|
| `dctwin/runner/postprocess.py` | Add `get_volume()` method to `CachedPostProcessor` |
| `STLGeneratorForOpenFoam/server/server.py` | Add `handle_pp_volume` endpoint |
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Add volume UI section, ray marching shader, volume state management |
| `STLGeneratorForOpenFoam/web/i18n/zh-CN.json` | Add i18n keys for volume UI |
| `STLGeneratorForOpenFoam/web/i18n/en-US.json` | Add i18n keys for volume UI |

### Estimated Effort

| Component | Effort |
|-----------|--------|
| Backend `get_volume()` + API endpoint | 2-3 hours |
| Frontend shader + 3D texture loading | 4-6 hours |
| UI controls + state management | 2-3 hours |
| Testing + tuning | 2-3 hours |
| **Total** | **~1.5 days** |
