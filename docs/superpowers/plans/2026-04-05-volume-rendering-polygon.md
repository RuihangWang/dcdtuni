# Volume Rendering: Polygon Room Shape + Performance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix volume rendering to conform to polygon room shapes and improve render quality + interactive performance.

**Architecture:** Four changes to the client-side volume renderer in `STL_generate.html`: (1) pass sentinel value to shader and skip out-of-mesh voxels so volumes follow polygon room shape, (2) add ray jittering to eliminate banding artifacts, (3) make step count and size adaptive to resolution, (4) add interactive resolution downscaling during camera orbit. No server-side changes.

**Tech Stack:** Three.js r128 (WebGL2 / GLSL ES 3.0), single-file HTML

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | Modify | All changes — shader, fetch, build, orbit hooks |

All four changes touch the same file in distinct function blocks:
- `_ppVolumeShaderMaterial()` — lines 15445-15535 (shader uniforms + GLSL)
- `_ppFetchVolumeData()` — lines 15537-15563 (sentinel value in return data)
- `_ppBuildVolumeMesh()` — lines 15565-15626 (pass sentinel + resolution to shader)
- `ppApplyVolume()` — lines 15628-15718 (pass resolution through)
- OrbitControls listeners — lines 941-947 (interactive downscaling hooks)

---

### Task 1: Sentinel Skip — Fix Polygon Room Shape

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:15445-15563`

This is the critical bug fix. Currently, the shader's `scalar == scalar` NaN check never fires because NaN was already replaced with a sentinel float on the client side. The sentinel gets clamped to `uMinValue` and rendered as the lowest colormap color, filling the entire rectangular bounding box.

- [ ] **Step 1: Add `uSentinel` uniform to shader material**

In `_ppVolumeShaderMaterial()`, add the uniform declaration and GLSL code. Replace the entire function:

Find the existing uniforms block (line 15447-15456):
```javascript
            uniforms: {
                uVolume: { value: volumeTexture },
                uColorMap: { value: colormapTexture },
                uMinValue: { value: minVal },
                uMaxValue: { value: maxVal },
                uOpacity: { value: 0.5 },
                uBoxMin: { value: new THREE.Vector3(boxMin[0], boxMin[1], boxMin[2]) },
                uBoxMax: { value: new THREE.Vector3(boxMax[0], boxMax[1], boxMax[2]) },
                uCameraPos: { value: new THREE.Vector3() },
                uBrightness: { value: 1.0 },
            },
```

Replace with (adds `sentinelVal` param and `uSentinel` uniform):

Change the function signature from:
```javascript
    window._ppVolumeShaderMaterial = function(volumeTexture, colormapTexture, minVal, maxVal, boxMin, boxMax) {
```
to:
```javascript
    window._ppVolumeShaderMaterial = function(volumeTexture, colormapTexture, minVal, maxVal, boxMin, boxMax, sentinelVal, maxSteps) {
```

Add `uSentinel` to the uniforms object after `uBrightness`:
```javascript
                uBrightness: { value: 1.0 },
                uSentinel: { value: sentinelVal },
```

- [ ] **Step 2: Add sentinel uniform declaration and skip logic in fragment shader**

In the fragment shader string array, add the `uSentinel` declaration after the `uBrightness` line (after line 15477):
```javascript
                'uniform float uSentinel;',
```

Replace the NaN check block (lines 15511-15521):
```javascript
                '        if (scalar == scalar) {',
                '            float val = clamp((scalar - uMinValue) / (uMaxValue - uMinValue + 1e-10), 0.0, 1.0);',
                '            vec4 color = texture2D(uColorMap, vec2(val, 0.5));',
                '            color.rgb *= uBrightness;',
                '            float alpha = uOpacity * 0.15;',
                '',
                '            color.rgb *= alpha;',
                '            accumulated += (1.0 - accumulated.a) * vec4(color.rgb, alpha);',
                '',
                '            if (accumulated.a > 0.95) break;',
                '        }',
```

with sentinel-skip logic:
```javascript
                '        if (scalar > uSentinel + 0.5) {',
                '            float val = clamp((scalar - uMinValue) / (uMaxValue - uMinValue + 1e-10), 0.0, 1.0);',
                '            vec4 color = texture2D(uColorMap, vec2(val, 0.5));',
                '            color.rgb *= uBrightness;',
                '            float alpha = uOpacity * 0.15;',
                '',
                '            color.rgb *= alpha;',
                '            accumulated += (1.0 - accumulated.a) * vec4(color.rgb, alpha);',
                '',
                '            if (accumulated.a > 0.95) break;',
                '        }',
```

The key change: `scalar == scalar` (NaN check that never fires) → `scalar > uSentinel + 0.5` (sentinel skip that works correctly). The `+ 0.5` margin avoids float precision issues.

- [ ] **Step 3: Store sentinel in fetch return data**

In `_ppFetchVolumeData()` (line 15562), the sentinel is already computed but not returned. Change the return statement from:
```javascript
        return { header: header, scalars: scalars };
```
to:
```javascript
        return { header: header, scalars: scalars, sentinel: sentinel };
```

- [ ] **Step 4: Pass sentinel to shader material in `_ppBuildVolumeMesh`**

In `_ppBuildVolumeMesh()`, change the function signature from:
```javascript
    window._ppBuildVolumeMesh = function(volData, cmapPreset, climMin, climMax, opacity) {
```
to:
```javascript
    window._ppBuildVolumeMesh = function(volData, cmapPreset, climMin, climMax, opacity, resolution) {
```

Change the material creation call (line 15606) from:
```javascript
        var mat = window._ppVolumeShaderMaterial(tex3d, cmapTex, climMin, climMax, boxMin, boxMax);
```
to:
```javascript
        var sentinelVal = volData.sentinel !== undefined ? volData.sentinel : (volData.header.scalarRange[0] - 1.0);
        var maxSteps = Math.min(Math.max(resolution || 128, 64) * 3, 768);
        var mat = window._ppVolumeShaderMaterial(tex3d, cmapTex, climMin, climMax, boxMin, boxMax, sentinelVal, maxSteps);
```

- [ ] **Step 5: Pass resolution through from `ppApplyVolume`**

In `ppApplyVolume()`, update the two calls to `_ppBuildVolumeMesh`:

Line 15700, change from:
```javascript
            window._ppBuildVolumeMesh(cached.data, cmapPreset, climMin2, climMax2, opacity);
```
to:
```javascript
            window._ppBuildVolumeMesh(cached.data, cmapPreset, climMin2, climMax2, opacity, resolution);
```

Also check if the `_ppBuildVolumeMesh` is called anywhere else — search for `_ppBuildVolumeMesh(` and update all call sites with the extra `resolution` parameter.

- [ ] **Step 6: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "fix: volume rendering conforms to polygon room shape via sentinel skip

Volume was rendering as rectangular box because NaN-replaced sentinel
values passed the shader's NaN check and got colored. Now pass sentinel
value as uniform and skip samples below it. Triangular/polygon rooms
render correctly."
```

---

### Task 2: Ray Jittering — Eliminate Banding Artifacts

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:15466-15528` (fragment shader)

- [ ] **Step 1: Add hash function to fragment shader**

In the fragment shader string array, after the `intersectBox` function closing brace (after the `'}',` line at the end of intersectBox), add:

```javascript
                '',
                'float hash(vec2 p) {',
                '    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);',
                '}',
```

- [ ] **Step 2: Add jitter to ray start position**

In the fragment shader's `main()`, after the step size computation and before `vec4 accumulated`, change:

From:
```javascript
                '    vec4 accumulated = vec4(0.0);',
                '    float t = tHit.x;',
```

To:
```javascript
                '    float jitter = hash(gl_FragCoord.xy) * stepSize;',
                '    vec4 accumulated = vec4(0.0);',
                '    float t = tHit.x + jitter;',
```

This offsets each pixel's ray start by a random fraction of one step, averaging out the banding pattern.

- [ ] **Step 3: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: add ray jittering to volume rendering for anti-banding

Each pixel's ray starts at a random offset within one step size,
eliminating the visible banding/wood-grain artifacts. Uses screen-space
hash function (no noise texture needed)."
```

---

### Task 3: Adaptive Step Count & Size

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:15466-15528` (fragment shader)

Currently: hardcoded `256` max steps and `maxDim / 128.0` step size regardless of resolution. For 256^3 volumes this is far too coarse; for 64^3 it wastes cycles.

- [ ] **Step 1: Replace hardcoded step count and step size with `MAX_STEPS` define**

In `_ppVolumeShaderMaterial`, the fragment shader is built as a string array joined with `\n`. We need to inject the `MAX_STEPS` define at the top of the fragment shader.

Change the first line of the fragmentShader array from:
```javascript
                'precision highp float;',
```
to:
```javascript
                '#define MAX_STEPS ' + (maxSteps || 256),
                'precision highp float;',
```

Then change the step size line from:
```javascript
                '    float stepSize = maxDim / 128.0;',
```
to:
```javascript
                '    float stepSize = maxDim / (float(MAX_STEPS) * 0.5);',
```

And change the for loop from:
```javascript
                '    for (int i = 0; i < 256; i++) {',
```
to:
```javascript
                '    for (int i = 0; i < MAX_STEPS; i++) {',
```

The `maxSteps` parameter was already added to `_ppVolumeShaderMaterial` in Task 1, Step 1. The value is computed as `min(resolution * 3, 768)`:
- Resolution 64 → 192 steps, step size = maxDim/96
- Resolution 128 → 384 steps, step size = maxDim/192
- Resolution 256 → 768 steps, step size = maxDim/384

- [ ] **Step 2: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: adaptive ray step count scales with volume resolution

Step count and size now scale with resolution (64→192, 128→384, 256→768
steps) via compile-time MAX_STEPS define. Higher resolutions get finer
sampling for smoother volumes; lower resolutions use fewer steps for
faster rendering."
```

---

### Task 4: Interactive Resolution Downscaling

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html:941-947` (orbit controls listeners)

When rotating/zooming with volume rendering visible, the full-res ray march runs every frame. We downsample during interaction and restore on idle.

- [ ] **Step 1: Add downscaling logic to orbit controls listeners**

Find the existing orbit controls listeners (lines 941-947):
```javascript
        controls.addEventListener('start', () => {
            cameraTransition = null;
            normalizeMainCameraUp(true);
        });
        controls.addEventListener('end', () => {
            normalizeMainCameraUp(true);
        });
```

Replace with:
```javascript
        controls.addEventListener('start', () => {
            cameraTransition = null;
            normalizeMainCameraUp(true);
            // Downscale during interaction if volume rendering is active
            if (window._ppState && window._ppState.volumeMesh && window._ppState.volumeGroup.visible) {
                renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2) * 0.5);
            }
        });
        var _volRestoreTimer = null;
        controls.addEventListener('end', () => {
            normalizeMainCameraUp(true);
            // Restore full resolution after interaction ends (debounced)
            if (_volRestoreTimer) clearTimeout(_volRestoreTimer);
            _volRestoreTimer = setTimeout(function() {
                renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
                requestRender();
            }, 150);
        });
```

This renders at half pixel ratio during orbit/zoom/pan, then restores full resolution 150ms after interaction ends. The debounce prevents flicker during rapid start/end cycles.

- [ ] **Step 2: Commit**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: downscale volume rendering during camera interaction

Renders at half pixel ratio while orbiting/zooming/panning, then
restores full resolution 150ms after interaction ends. Significantly
improves interactive frame rate when volume rendering is visible."
```

---

### Task 5: Verification

**Files:**
- No changes — testing only

- [ ] **Step 1: Start the server**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
fuser -k 8005/tcp 2>/dev/null; fuser -k 8006/tcp 2>/dev/null
conda run -n dcdtuni python STLGeneratorForOpenFoam/server/server.py --port 8005 &
sleep 3
```

- [ ] **Step 2: Open the Tri_angle case and verify polygon-shaped volume**

Using browser-use:
```bash
export PATH="$HOME/.local/bin:$PATH"
browser-use open http://localhost:8005/STL_generate.html
```

Load the Tri_angle project, initialize post-processing, and enable volume rendering. Take a screenshot to verify the volume is triangular, not rectangular.

```bash
browser-use screenshot /tmp/vol_triangle_test.png
```

Read the screenshot with the Read tool to visually confirm.

**Expected**: Volume rendering shows a triangular prism shape matching the room polygon. No rectangular box artifacts.

- [ ] **Step 3: Test a rectangular room case for regression**

Load a standard rectangular case (e.g., V5_CFD_Station), enable volume rendering, screenshot and verify the volume fills the rectangular room correctly.

**Expected**: Identical behavior to before — rectangular room volume looks the same.

- [ ] **Step 4: Check banding artifacts**

Compare volume quality visually — the jittering should produce smoother gradients without visible banding lines.

- [ ] **Step 5: Test interactive performance**

With volume rendering visible, use browser-use to orbit the camera and check responsiveness. The half-res downscaling during interaction should be visible (slightly blurry during drag, sharp on release).

- [ ] **Step 6: Test resolution settings**

Test with all three resolution options (64, 128, 256). Higher resolutions should produce smoother volumes with more detail.

- [ ] **Step 7: Commit verification notes**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
git add -f docs/superpowers/plans/2026-04-05-volume-rendering-polygon.md
git commit -m "docs: add volume rendering polygon implementation plan"
```
