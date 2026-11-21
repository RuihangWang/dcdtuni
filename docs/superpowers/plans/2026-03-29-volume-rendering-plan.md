# Volume Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GPU ray-cast volume rendering to the browser-based CFD post-processing pipeline.

**Architecture:** Server resamples the unstructured OpenFOAM mesh onto a regular 3D grid (NxNxN) via PyVista, sends the scalar array as binary. Frontend uploads it as a WebGL2 `sampler3D` texture, renders a bounding box with a ray-marching fragment shader that does front-to-back compositing through the volume.

**Tech Stack:** Python/PyVista (backend resampling), Three.js r128 WebGL2 (frontend ray marching), existing colormap system.

**Spec:** `docs/Omniverse-paraview/2026-03-29-volume-rendering-design.md`

---

### Task 1: Backend — `get_volume()` method in CachedPostProcessor

**Files:**
- Modify: `dctwin/dctwin/runner/postprocess.py` (add method after `get_slice` at ~line 2020)

- [ ] **Step 1: Add `get_volume()` method**

Insert after the `get_slice()` method (after line ~2020) in the `CachedPostProcessor` class:

```python
def get_volume(self, field_name: str, resolution: int = 128) -> Tuple[bytes, dict]:
    """Resample internal mesh onto a regular 3D grid and return binary volume data.

    Returns:
        (binary, info) where binary layout is:
          [uint32 headerLen][JSON header padded to 4-byte][float32 NxNxN scalars]
        and info is the parsed header dict.
    """
    internal = self._internal
    if internal is None:
        return b"", {"error": "No internal mesh loaded"}

    pv = _get_pyvista()
    bounds = internal.bounds  # (xmin,xmax, ymin,ymax, zmin,zmax)
    N = max(16, min(resolution, 512))  # clamp to sane range

    origin = [bounds[0], bounds[2], bounds[4]]
    spacing = [
        (bounds[1] - bounds[0]) / max(N - 1, 1),
        (bounds[3] - bounds[2]) / max(N - 1, 1),
        (bounds[5] - bounds[4]) / max(N - 1, 1),
    ]
    grid = pv.ImageData(dimensions=(N, N, N), spacing=spacing, origin=origin)
    resampled = grid.sample(internal)

    # Resolve field name (handles Umag, Ux, etc.)
    resolved, is_component, component_idx = self._resolve_field_name(field_name)
    scalars = self._extract_scalars_from_mesh(resampled, resolved, is_component, component_idx)
    scalars = scalars.ravel().astype(np.float32)

    # Mark points outside the mesh as NaN (shader skips them)
    mask = resampled["vtkValidPointMask"]
    if mask is not None:
        scalars[np.asarray(mask) == 0] = np.nan

    smin = float(np.nanmin(scalars)) if len(scalars) > 0 else 0.0
    smax = float(np.nanmax(scalars)) if len(scalars) > 0 else 0.0

    header_dict = {
        "dimensions": [N, N, N],
        "origin": origin,
        "spacing": spacing,
        "scalarRange": [smin, smax],
        "field": field_name,
    }
    header_bytes = json.dumps(header_dict).encode("utf-8")
    # Pad to 4-byte alignment
    pad = (4 - len(header_bytes) % 4) % 4
    header_bytes += b"\x00" * pad

    payload = struct.pack("<I", len(header_bytes)) + header_bytes + scalars.tobytes()
    return payload, header_dict
```

- [ ] **Step 2: Verify import availability**

The method uses `np`, `json`, `struct` which are already imported at the top of `postprocess.py`. `_resolve_field_name` and `_extract_scalars_from_mesh` are existing methods on `CachedPostProcessor`. No new imports needed.

- [ ] **Step 3: Commit**

```bash
git add dctwin/dctwin/runner/postprocess.py
git commit -m "feat: add get_volume() for 3D grid resampling"
```

---

### Task 2: Backend — API endpoint `POST /api/pp/volume`

**Files:**
- Modify: `STLGeneratorForOpenFoam/server/server.py` (add handler + route)

- [ ] **Step 1: Add handler function**

Insert after `handle_pp_slice` (after line ~808) in `server.py`:

```python
def handle_pp_volume(handler):
    """POST /api/pp/volume — Resample internal mesh to 3D grid for volume rendering."""
    cpp = _get_cached_pp()
    if cpp.multi_block is None:
        _send_json(handler, 400, {"error": "Not initialized. Call /api/pp/init first."})
        return

    try:
        body = json.loads(handler.rfile.read(int(handler.headers.get("Content-Length", 0))))
        field_name = body.get("field", "T")
        resolution = int(body.get("resolution", 128))

        payload, info = cpp.get_volume(field_name=field_name, resolution=resolution)

        if not payload:
            _send_json(handler, 400, {"error": info.get("error", "Volume resampling failed")})
            return

        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(payload)
    except Exception as e:
        _send_json(handler, 500, {"error": str(e)})
```

- [ ] **Step 2: Add route to `do_POST`**

In the `do_POST` method (around line 1664), add before the `else: self.send_error(404)` line:

```python
        elif parsed.path == "/api/pp/volume":
            handle_pp_volume(self)
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/server/server.py
git commit -m "feat: add POST /api/pp/volume endpoint"
```

---

### Task 3: Frontend — Volume rendering HTML UI section

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (insert HTML after slice section, ~line 396)

- [ ] **Step 1: Add volume settings HTML**

Insert between the closing `</div>` of the slice section (line 396) and the `<!-- Display Options -->` comment (line 398):

```html
                <!-- Volume Rendering Settings -->
                <div class="prop-group" style="border-color:var(--section-header-zone); margin:8px;">
                    <div class="prop-title" style="color:var(--section-header-zone)" data-i18n="pp.volume_settings">🧊 体渲染设置</div>
                    <div class="prop-row">
                        <label data-i18n="pp.config_volume">配置体渲染</label>
                        <input type="checkbox" id="pp-vol-show" onchange="window.ppToggleVolumeConfig(this.checked)">
                    </div>
                    <div id="pp-vol-config" style="display:none; margin-top:4px;">
                        <div class="prop-row">
                            <label data-i18n="pp.vol_field">渲染场</label>
                            <select id="pp-vol-field" style="flex:1; min-width:0; background:var(--bg-input); border:1px solid var(--border); color:var(--text); border-radius:3px; padding:2px 4px; font-size:0.78rem; box-sizing:border-box;"></select>
                        </div>
                        <div class="prop-row">
                            <label data-i18n="pp.vol_resolution">分辨率</label>
                            <div style="display:flex; gap:6px; flex:1; justify-content:flex-end;">
                                <label style="display:flex;align-items:center;gap:2px;cursor:pointer;font-size:0.8rem;"><input type="radio" name="pp-vol-res" value="64"> 64</label>
                                <label style="display:flex;align-items:center;gap:2px;cursor:pointer;font-size:0.8rem;"><input type="radio" name="pp-vol-res" value="128" checked> 128</label>
                                <label style="display:flex;align-items:center;gap:2px;cursor:pointer;font-size:0.8rem;"><input type="radio" name="pp-vol-res" value="256"> 256</label>
                            </div>
                        </div>
                        <div class="prop-row">
                            <label data-i18n="pp.vol_opacity">透明度</label>
                            <input type="range" id="pp-vol-opacity" min="0" max="1" step="0.05" value="0.5"
                                   oninput="window.ppSetVolumeOpacity(parseFloat(this.value))" style="flex:1;">
                        </div>
                        <div class="prop-row">
                            <label data-i18n="pp.vol_colormap">色谱</label>
                            <select id="pp-vol-cmap" style="flex:1; min-width:0; background:var(--bg-input); border:1px solid var(--border); color:var(--text); border-radius:3px; padding:2px 4px; font-size:0.78rem; box-sizing:border-box;">
                                <option value="coolwarm">Coolwarm</option>
                                <option value="jet">Jet</option>
                                <option value="viridis">Viridis</option>
                            </select>
                        </div>
                        <div class="prop-row" style="margin-top:4px;">
                            <label data-i18n="pp.vol_range">值域范围</label>
                        </div>
                        <div style="display:flex; gap:4px; margin-top:2px; align-items:center;">
                            <label style="font-size:0.72rem; flex-shrink:0;">Min:</label>
                            <input type="number" id="pp-vol-clim-min" step="0.1" style="flex:1; min-width:0; padding:2px 4px; font-size:0.72rem; background:var(--bg-input); border:1px solid var(--border); color:var(--text); border-radius:3px; box-sizing:border-box;">
                            <label style="font-size:0.72rem; flex-shrink:0;">Max:</label>
                            <input type="number" id="pp-vol-clim-max" step="0.1" style="flex:1; min-width:0; padding:2px 4px; font-size:0.72rem; background:var(--bg-input); border:1px solid var(--border); color:var(--text); border-radius:3px; box-sizing:border-box;">
                        </div>
                        <div style="text-align:right; margin-top:6px;">
                            <button class="pp-btn-sm" onclick="window.ppApplyVolume()" style="padding:3px 12px;" data-i18n="pp.apply_volume">应用</button>
                        </div>
                    </div>
                    <div class="prop-row" style="margin-top:4px;">
                        <label data-i18n="pp.show_volume">显示体渲染</label>
                        <input type="checkbox" id="pp-vol-visible" checked onchange="window.ppToggleVolumeVisibility(this.checked)">
                    </div>
                </div>
```

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add volume rendering UI section in post-processing tab"
```

---

### Task 4: Frontend — Volume state, group, and control functions

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (add JS near existing PP state init ~line 12243)

- [ ] **Step 1: Add volumeGroup to _ppState initialization**

Find the `_ppState` init block (around line 12243) and add `volumeGroup`:

```javascript
// In the _ppState initialization block, after sliceGroup:
// sliceGroup: new THREE.Group(),
// ADD:
volumeGroup: new THREE.Group(),
volumeMesh: null,
volumeTexture: null,
```

- [ ] **Step 2: Add volumeGroup to scene in ppInitPostProcess**

Find where ppGroup/streamlineGroup/sliceGroup are added to `globalGroup` (around line 12316-12324) and add:

```javascript
if (!globalGroup.children.includes(window._ppState.volumeGroup)) {
    globalGroup.add(window._ppState.volumeGroup);
}
```

- [ ] **Step 3: Add volume control functions**

Insert after the existing `ppSetLightIntensity` function (find it by searching for `ppSetLightIntensity`). Add these functions:

```javascript
// ── Volume Rendering Functions ──

window.ppToggleVolumeConfig = function(show) {
    var config = document.getElementById('pp-vol-config');
    if (config) config.style.display = show ? 'block' : 'none';
    if (show) {
        // Populate field dropdown from available fields (same as slice field)
        var srcSel = document.getElementById('pp-slice-field');
        var volSel = document.getElementById('pp-vol-field');
        if (srcSel && volSel) {
            volSel.innerHTML = srcSel.innerHTML;
            volSel.value = srcSel.value || 'T';
        }
    }
};

window.ppToggleVolumeVisibility = function(visible) {
    window._ppState.volumeGroup.visible = visible;
    requestRender();
};

window.ppSetVolumeOpacity = function(val) {
    var mesh = window._ppState.volumeMesh;
    if (mesh && mesh.material && mesh.material.uniforms && mesh.material.uniforms.uOpacity) {
        mesh.material.uniforms.uOpacity.value = val;
    }
    requestRender();
};
```

- [ ] **Step 4: Add volume cleanup in ppClearAll**

Find the `ppClearAll` function (search for `window.ppClearAll = function`). Add volume cleanup before the `// Remove from scene` comment:

```javascript
// Clear volume rendering
if (state.volumeMesh) {
    state.volumeGroup.remove(state.volumeMesh);
    state.volumeMesh.geometry.dispose();
    state.volumeMesh.material.dispose();
    state.volumeMesh = null;
}
if (state.volumeTexture) {
    state.volumeTexture.dispose();
    state.volumeTexture = null;
}
while (state.volumeGroup.children.length > 0) {
    state.volumeGroup.remove(state.volumeGroup.children[0]);
}
```

Also add `globalGroup.remove(state.volumeGroup);` next to the existing `globalGroup.remove(state.ppGroup);` line, and re-create it:
```javascript
state.volumeGroup = new THREE.Group();
```

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add volume rendering state management and controls"
```

---

### Task 5: Frontend — Ray marching shader and `ppApplyVolume()`

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (add shader factory + apply function)

- [ ] **Step 1: Add volume shader factory**

Insert after the `_ppVertexColorShaderMaterial` function (search for `// ── Binary ArrayBuffer Parsing ──` and insert before it):

```javascript
// ParaView-style GPU ray-cast volume rendering shader
window._ppVolumeShaderMaterial = function(volumeTexture, colormapTexture, minVal, maxVal, boxMin, boxMax) {
    return new THREE.ShaderMaterial({
        uniforms: {
            uVolume: { value: volumeTexture },
            uColorMap: { value: colormapTexture },
            uMinValue: { value: minVal },
            uMaxValue: { value: maxVal },
            uOpacity: { value: 0.5 },
            uBoxMin: { value: new THREE.Vector3(boxMin[0], boxMin[1], boxMin[2]) },
            uBoxMax: { value: new THREE.Vector3(boxMax[0], boxMax[1], boxMax[2]) },
            uCameraPos: { value: new THREE.Vector3() },
        },
        vertexShader: [
            'varying vec3 vWorldPos;',
            'void main() {',
            '    vec4 worldPos = modelMatrix * vec4(position, 1.0);',
            '    vWorldPos = worldPos.xyz;',
            '    gl_Position = projectionMatrix * viewMatrix * worldPos;',
            '}',
        ].join('\n'),
        fragmentShader: [
            'precision highp float;',
            'precision highp sampler3D;',
            'uniform sampler3D uVolume;',
            'uniform sampler2D uColorMap;',
            'uniform float uMinValue;',
            'uniform float uMaxValue;',
            'uniform float uOpacity;',
            'uniform vec3 uBoxMin;',
            'uniform vec3 uBoxMax;',
            'uniform vec3 uCameraPos;',
            'varying vec3 vWorldPos;',
            '',
            '// Ray-AABB intersection (returns tNear, tFar)',
            'vec2 intersectBox(vec3 orig, vec3 dir, vec3 bmin, vec3 bmax) {',
            '    vec3 invDir = 1.0 / dir;',
            '    vec3 t0 = (bmin - orig) * invDir;',
            '    vec3 t1 = (bmax - orig) * invDir;',
            '    vec3 tmin = min(t0, t1);',
            '    vec3 tmax = max(t0, t1);',
            '    float tNear = max(max(tmin.x, tmin.y), tmin.z);',
            '    float tFar = min(min(tmax.x, tmax.y), tmax.z);',
            '    return vec2(tNear, tFar);',
            '}',
            '',
            'void main() {',
            '    vec3 rayDir = normalize(vWorldPos - uCameraPos);',
            '    vec2 tHit = intersectBox(uCameraPos, rayDir, uBoxMin, uBoxMax);',
            '    if (tHit.x > tHit.y) discard;',
            '    tHit.x = max(tHit.x, 0.0);',  // camera might be inside
            '',
            '    vec3 boxSize = uBoxMax - uBoxMin;',
            '    float maxDim = max(max(boxSize.x, boxSize.y), boxSize.z);',
            '    float stepSize = maxDim / 128.0;',
            '',
            '    vec4 accumulated = vec4(0.0);',
            '    float t = tHit.x;',
            '',
            '    for (int i = 0; i < 256; i++) {',
            '        if (t > tHit.y) break;',
            '        vec3 sampleWorld = uCameraPos + rayDir * t;',
            '        vec3 texCoord = (sampleWorld - uBoxMin) / boxSize;',
            '',
            '        float scalar = texture(uVolume, texCoord).r;',
            '',
            '        // Skip NaN (outside mesh — masked during resampling)',
            '        if (scalar == scalar) {',  // NaN != NaN
            '            float val = clamp((scalar - uMinValue) / (uMaxValue - uMinValue + 1e-10), 0.0, 1.0);',
            '            vec4 color = texture2D(uColorMap, vec2(val, 0.5));',
            '            float alpha = val * uOpacity * 0.04;',
            '',
            '            // Front-to-back compositing (premultiplied alpha)',
            '            color.rgb *= alpha;',
            '            accumulated += (1.0 - accumulated.a) * vec4(color.rgb, alpha);',
            '',
            '            if (accumulated.a > 0.95) break;',
            '        }',
            '',
            '        t += stepSize;',
            '    }',
            '',
            '    if (accumulated.a < 0.01) discard;',
            '    gl_FragColor = accumulated;',
            '}',
        ].join('\n'),
        side: THREE.BackSide,
        transparent: true,
        depthWrite: false,
        depthTest: true,
    });
};
```

- [ ] **Step 2: Add `ppApplyVolume()` function**

Insert after the volume shader factory:

```javascript
window.ppApplyVolume = async function() {
    var state = window._ppState;
    if (!state.initialized) { logOperation('PP: 请先初始化后处理'); return; }

    var fieldName = (document.getElementById('pp-vol-field') || {}).value || 'T';
    var resolution = 128;
    var radios = document.querySelectorAll('input[name="pp-vol-res"]');
    for (var i = 0; i < radios.length; i++) {
        if (radios[i].checked) { resolution = parseInt(radios[i].value); break; }
    }

    logOperation('PP: 正在计算体渲染 (field=' + fieldName + ', res=' + resolution + ')...');

    try {
        var resp = await fetch('/api/pp/volume', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ field: fieldName, resolution: resolution }),
        });
        if (!resp.ok) {
            var err = await resp.json();
            logOperation('PP: 体渲染计算失败 — ' + (err.error || resp.statusText));
            return;
        }
        var buf = await resp.arrayBuffer();
        var dv = new DataView(buf);
        var headerLen = dv.getUint32(0, true);
        var headerStr = new TextDecoder().decode(new Uint8Array(buf, 4, headerLen)).replace(/\0+$/, '');
        var header = JSON.parse(headerStr);

        var dataOffset = 4 + headerLen;
        var dims = header.dimensions;
        var totalVoxels = dims[0] * dims[1] * dims[2];
        var scalars = new Float32Array(buf, dataOffset, totalVoxels);

        // Replace NaN with 0 for WebGL (NaN in textures is undefined behavior in some drivers)
        // We use a sentinel value (uMinValue - 1) instead
        var sentinel = header.scalarRange[0] - 1.0;
        for (var vi = 0; vi < totalVoxels; vi++) {
            if (isNaN(scalars[vi])) scalars[vi] = sentinel;
        }

        // Dispose old volume
        if (state.volumeMesh) {
            state.volumeGroup.remove(state.volumeMesh);
            state.volumeMesh.geometry.dispose();
            state.volumeMesh.material.dispose();
            state.volumeMesh = null;
        }
        if (state.volumeTexture) {
            state.volumeTexture.dispose();
            state.volumeTexture = null;
        }

        // Create 3D texture (Three.js r128 uses DataTexture3D)
        var tex3d = new THREE.DataTexture3D(scalars, dims[0], dims[1], dims[2]);
        tex3d.format = THREE.RedFormat;
        tex3d.type = THREE.FloatType;
        tex3d.minFilter = THREE.LinearFilter;
        tex3d.magFilter = THREE.LinearFilter;
        tex3d.wrapS = THREE.ClampToEdgeWrapping;
        tex3d.wrapT = THREE.ClampToEdgeWrapping;
        tex3d.wrapR = THREE.ClampToEdgeWrapping;
        tex3d.unpackAlignment = 1;
        tex3d.needsUpdate = true;
        state.volumeTexture = tex3d;

        // Colormap texture
        var cmapPreset = (document.getElementById('pp-vol-cmap') || {}).value || 'coolwarm';
        var cmapData = (window._ppColormapPresets[cmapPreset] || window._ppColormapPresets.coolwarm)();
        var cmapTex = new THREE.DataTexture(new Uint8Array(cmapData), 256, 1, THREE.RGBAFormat);
        cmapTex.needsUpdate = true;

        // Bounding box
        var origin = header.origin;
        var spacing = header.spacing;
        var boxMin = origin;
        var boxMax = [
            origin[0] + spacing[0] * (dims[0] - 1),
            origin[1] + spacing[1] * (dims[1] - 1),
            origin[2] + spacing[2] * (dims[2] - 1),
        ];
        var center = [(boxMin[0]+boxMax[0])/2, (boxMin[1]+boxMax[1])/2, (boxMin[2]+boxMax[2])/2];
        var size = [boxMax[0]-boxMin[0], boxMax[1]-boxMin[1], boxMax[2]-boxMin[2]];

        // Value range
        var isTemp = window._ppIsTemperatureField && window._ppIsTemperatureField(fieldName);
        var smin = header.scalarRange[0], smax = header.scalarRange[1];
        var userMin = parseFloat(document.getElementById('pp-vol-clim-min').value);
        var userMax = parseFloat(document.getElementById('pp-vol-clim-max').value);
        var climMin = isNaN(userMin) ? smin : (isTemp ? window._ppDisplay2K(userMin) : userMin);
        var climMax = isNaN(userMax) ? smax : (isTemp ? window._ppDisplay2K(userMax) : userMax);

        // Auto-fill clim inputs
        var dispMin = isTemp && window._ppK2Display ? window._ppK2Display(smin) : smin;
        var dispMax = isTemp && window._ppK2Display ? window._ppK2Display(smax) : smax;
        if (isNaN(userMin)) document.getElementById('pp-vol-clim-min').value = dispMin.toFixed(1);
        if (isNaN(userMax)) document.getElementById('pp-vol-clim-max').value = dispMax.toFixed(1);

        var opacity = parseFloat(document.getElementById('pp-vol-opacity').value) || 0.5;

        // Create material
        var mat = window._ppVolumeShaderMaterial(tex3d, cmapTex, climMin, climMax, boxMin, boxMax);
        mat.uniforms.uOpacity.value = opacity;

        // Create bounding box mesh (BackSide so rays start from back faces)
        var geo = new THREE.BoxGeometry(size[0], size[1], size[2]);
        var mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(center[0], center[1], center[2]);

        state.volumeMesh = mesh;
        state.volumeGroup.add(mesh);

        // Update camera position uniform each frame
        if (!state._volRenderHook) {
            state._volRenderHook = true;
            var origRender = window._renderScene || requestRender;
            // Patch requestRender to update camera pos
            var _origRequestRender = requestRender;
            requestRender = function() {
                if (state.volumeMesh && state.volumeMesh.material.uniforms.uCameraPos) {
                    state.volumeMesh.material.uniforms.uCameraPos.value.copy(camera.position);
                }
                _origRequestRender();
            };
        }
        if (state.volumeMesh.material.uniforms.uCameraPos) {
            state.volumeMesh.material.uniforms.uCameraPos.value.copy(camera.position);
        }

        // Ensure visible
        var visCb = document.getElementById('pp-vol-visible');
        if (visCb) visCb.checked = true;
        state.volumeGroup.visible = true;

        requestRender();
        logOperation('PP: 体渲染完成 (' + dims[0] + 'x' + dims[1] + 'x' + dims[2] + ', ' + fieldName + ': ' + dispMin.toFixed(1) + '~' + dispMax.toFixed(1) + ')');
    } catch (e) {
        logOperation('PP: 体渲染请求失败 — ' + e.message);
    }
};
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add ray marching shader and ppApplyVolume function"
```

---

### Task 6: i18n translations

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Add zh-CN translations**

Add after the existing `pp.show_slices` entry:

```json
  "pp.volume_settings": "🧊 体渲染设置",
  "pp.config_volume": "配置体渲染",
  "pp.vol_field": "渲染场",
  "pp.vol_resolution": "分辨率",
  "pp.vol_opacity": "透明度",
  "pp.vol_colormap": "色谱",
  "pp.vol_range": "值域范围",
  "pp.apply_volume": "应用",
  "pp.show_volume": "显示体渲染",
```

- [ ] **Step 2: Add en-US translations**

Add at the same position:

```json
  "pp.volume_settings": "🧊 Volume Rendering",
  "pp.config_volume": "Configure Volume",
  "pp.vol_field": "Field",
  "pp.vol_resolution": "Resolution",
  "pp.vol_opacity": "Opacity",
  "pp.vol_colormap": "Colormap",
  "pp.vol_range": "Value Range",
  "pp.apply_volume": "Apply",
  "pp.show_volume": "Show Volume",
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/i18n/zh-CN.json STLGeneratorForOpenFoam/web/i18n/en-US.json
git commit -m "feat: add i18n translations for volume rendering UI"
```

---

### Task 7: Integration testing — end-to-end verification

- [ ] **Step 1: Restart server**

```bash
fuser -k 8000/tcp 2>/dev/null
CONDA_BASE="$(conda info --base)"
"$CONDA_BASE/envs/dcdtuni/bin/python" STLGeneratorForOpenFoam/server/server.py \
  --case-dir dctwin/case/V5_CFD_Station &
```

- [ ] **Step 2: Initialize post-processing via API**

```bash
curl -s -X POST http://localhost:8000/api/pp/init \
  -H "Content-Type: application/json" \
  -d '{"runDir": ""}' | python3 -m json.tool
```

Expected: `{"success": true, "message": "VTM loaded, N patches, M fields", ...}`

- [ ] **Step 3: Test volume endpoint**

```bash
curl -s -X POST http://localhost:8000/api/pp/volume \
  -H "Content-Type: application/json" \
  -d '{"field": "T", "resolution": 64}' \
  -o /tmp/volume_test.bin
ls -la /tmp/volume_test.bin
python3 -c "
import struct, json
with open('/tmp/volume_test.bin', 'rb') as f:
    hlen = struct.unpack('<I', f.read(4))[0]
    header = json.loads(f.read(hlen).rstrip(b'\x00'))
    print(json.dumps(header, indent=2))
    import numpy as np
    data = np.frombuffer(f.read(), dtype=np.float32)
    print(f'Data shape: {data.shape}, expected: {header[\"dimensions\"][0]**3}')
    print(f'NaN count: {np.isnan(data).sum()}, valid: {(~np.isnan(data)).sum()}')
"
```

Expected: header with dimensions [64,64,64], data shape (262144,), some NaN values (outside mesh).

- [ ] **Step 4: Test in browser**

Open the browser UI, go to post-processing tab, initialize post-processing, then:
1. Check "配置体渲染" checkbox
2. Select field "T", resolution 128
3. Click "应用"
4. Verify a semi-transparent colored volume appears in the 3D viewport
5. Drag the opacity slider — volume should become more/less transparent
6. Uncheck "显示体渲染" — volume should disappear

- [ ] **Step 5: Kill test server**

```bash
fuser -k 8000/tcp 2>/dev/null
```

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: volume rendering - end-to-end integration complete"
```
