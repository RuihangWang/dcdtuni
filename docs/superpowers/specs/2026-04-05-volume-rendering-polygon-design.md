# Volume Rendering: Polygon Room Shape + Performance — Design Spec

**Date**: 2026-04-05
**Goal**: Fix volume rendering to conform to actual room polygon shape (not rectangular AABB), and improve both render quality and interactive performance using ParaView-inspired techniques.

---

## 1. Problem Statement

### Shape Bug
The volume renderer uses `THREE.BoxGeometry` as ray-march proxy and renders ALL voxels in the bounding box. The server correctly marks out-of-mesh voxels as NaN (via `vtkValidPointMask`), and the client replaces NaN with a sentinel value (`scalarRange[0] - 1.0`). However, the shader's NaN check (`scalar == scalar`) never triggers because sentinels are valid floats. The sentinel gets clamped to `uMinValue`, maps to the leftmost colormap color, and renders with full opacity — filling the entire rectangular box.

**Result**: A triangular room (Tri_angle case) shows a rectangular volume instead of a triangle.

### Performance
- Fixed 256 ray steps with coarse step size (`maxDim / 128`) — insufficient for high-res volumes
- No jittering → visible banding/wood-grain artifacts
- No interactive downsampling → rotating the camera with volume visible is sluggish
- Rays march through entire AABB even when 50%+ of voxels are outside the mesh

---

## 2. Fix 1: Sentinel Skip in Shader

**Root cause**: Client replaces NaN → sentinel, but shader doesn't skip sentinels.

**Fix**: Pass sentinel value as a shader uniform. In the ray march loop, skip samples at or below sentinel.

```glsl
uniform float uSentinel;

// In ray march loop:
float scalar = texture(uVolume, texCoord).r;
if (scalar < uSentinel + 0.5) {
    t += stepSize;
    continue;  // skip out-of-mesh voxel
}
```

**Effect**: Volume naturally conforms to the mesh boundary shape. For the Tri_angle case, only the triangular prism region has valid data → only that region renders.

**Uniform plumbing**:
- `_ppVolumeShaderMaterial()`: add `uSentinel` uniform
- `_ppBuildVolumeMesh()`: pass `sentinel` value from header to material
- `_ppFetchVolumeData()`: include sentinel in returned data (already computed)

---

## 3. Fix 2: Ray Jittering (Anti-Banding)

ParaView uses random ray offset to eliminate banding artifacts. Implement the same technique.

**Approach**: Hash-based noise from `gl_FragCoord` (no noise texture needed).

```glsl
// Pseudo-random from screen coords
float hash(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

// In main():
float jitter = hash(gl_FragCoord.xy) * stepSize;
float t = tHit.x + jitter;  // offset ray start
```

**Effect**: Each pixel's ray starts at a slightly different offset → banding artifacts average out across adjacent pixels.

---

## 4. Fix 3: Adaptive Step Count & Size

Current: hardcoded 256 steps, step size = `maxDim / 128`.

**New formula**:
```glsl
float stepSize = maxDim / (float(resolution) * 1.5);
// Max steps scales with resolution:
//   64  → 192 steps
//   128 → 384 steps
//   256 → 768 steps
```

Since GLSL for-loops require compile-time constant limits, use a fixed upper bound of 768 with runtime break:

```glsl
int maxSteps = int(float(RESOLUTION) * 3.0);  // passed as #define
for (int i = 0; i < 768; i++) {
    if (i >= maxSteps || t > tHit.y) break;
    // ... sample and accumulate
}
```

Pass resolution as a `#define` substitution when building the shader string, since it changes only when user clicks "Apply".

---

## 5. Fix 4: Two-Pass Interactive Rendering

**Problem**: Rotating/zooming with volume visible is slow because every frame renders at full resolution.

**Solution**: Render volume to a lower-resolution offscreen buffer during interaction, then render full-res on idle.

**Implementation**:
- Create a `THREE.WebGLRenderTarget` at 1/4 resolution (e.g., 480x270 for 1920x1080)
- During camera interaction (mousedown → mousemove → mouseup):
  - Render volume group to low-res target
  - Display low-res target as fullscreen quad over the scene
- On interaction end (mouseup + 200ms debounce):
  - Render full-res volume directly to screen

**Alternative (simpler)**: Use `renderer.setPixelRatio()` to dynamically lower during interaction. This affects the entire scene though. 

**Chosen approach**: The simpler `setPixelRatio` method during drag, restored on mouseup+debounce. This avoids the complexity of offscreen render targets while giving immediate interactive performance gains.

```javascript
// In orbit controls change listener:
if (isDragging && volumeVisible) {
    renderer.setPixelRatio(window.devicePixelRatio * 0.5);  // half res
}
// On interaction end (debounced):
renderer.setPixelRatio(window.devicePixelRatio);  // restore
```

---

## 6. Data Flow (Updated)

```
Server (postprocess.py):
  internal.bounds → regular grid → sample(internal) → vtkValidPointMask
  → NaN for outside-mesh points → binary payload with header

Client (STL_generate.html):
  fetch binary → parse header + Float32Array
  → replace NaN with sentinel (scalarRange[0] - 1.0)
  → create DataTexture3D (Float32, RedFormat)
  → create shader material with uSentinel uniform
  → BoxGeometry proxy (unchanged — rays still use AABB)
  → shader ray march: skip sentinel samples → polygon-shaped output
```

No server-side changes needed. All fixes are client-side (shader + JS).

---

## 7. Shader Uniforms (Updated)

| Uniform | Type | Purpose |
|---------|------|---------|
| `uVolume` | sampler3D | 3D scalar field texture |
| `uColorMap` | sampler2D | 256x1 colormap RGBA |
| `uMinValue` | float | Clipping range min |
| `uMaxValue` | float | Clipping range max |
| `uOpacity` | float | Base opacity per sample |
| `uBoxMin` | vec3 | Volume AABB min corner |
| `uBoxMax` | vec3 | Volume AABB max corner |
| `uCameraPos` | vec3 | Camera world position |
| `uBrightness` | float | RGB multiplier |
| **`uSentinel`** | **float** | **Out-of-mesh sentinel value (new)** |

Compile-time define: `MAX_STEPS` (192/384/768 based on resolution).

---

## 8. Complete Updated Fragment Shader

```glsl
precision highp float;
precision highp sampler3D;

uniform sampler3D uVolume;
uniform sampler2D uColorMap;
uniform float uMinValue;
uniform float uMaxValue;
uniform float uOpacity;
uniform vec3 uBoxMin;
uniform vec3 uBoxMax;
uniform vec3 uCameraPos;
uniform float uBrightness;
uniform float uSentinel;

varying vec3 vWorldPos;

vec2 intersectBox(vec3 orig, vec3 dir, vec3 bmin, vec3 bmax) {
    vec3 invDir = 1.0 / dir;
    vec3 t0 = (bmin - orig) * invDir;
    vec3 t1 = (bmax - orig) * invDir;
    vec3 tmin = min(t0, t1);
    vec3 tmax = max(t0, t1);
    float tNear = max(max(tmin.x, tmin.y), tmin.z);
    float tFar  = min(min(tmax.x, tmax.y), tmax.z);
    return vec2(tNear, tFar);
}

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

void main() {
    vec3 rayDir = normalize(vWorldPos - uCameraPos);
    vec2 tHit = intersectBox(uCameraPos, rayDir, uBoxMin, uBoxMax);
    if (tHit.x > tHit.y) discard;
    tHit.x = max(tHit.x, 0.0);

    vec3 boxSize = uBoxMax - uBoxMin;
    float maxDim = max(max(boxSize.x, boxSize.y), boxSize.z);
    float stepSize = maxDim / (float(MAX_STEPS) * 0.5);

    // Jitter ray start to eliminate banding
    float jitter = hash(gl_FragCoord.xy) * stepSize;
    float t = tHit.x + jitter;

    vec4 accumulated = vec4(0.0);

    for (int i = 0; i < MAX_STEPS; i++) {
        if (t > tHit.y) break;
        vec3 sampleWorld = uCameraPos + rayDir * t;
        vec3 texCoord = (sampleWorld - uBoxMin) / boxSize;

        float scalar = texture(uVolume, texCoord).r;

        // Skip out-of-mesh sentinel values (polygon room conformance)
        if (scalar > uSentinel + 0.5) {
            float val = clamp((scalar - uMinValue) / (uMaxValue - uMinValue + 1e-10), 0.0, 1.0);
            vec4 color = texture2D(uColorMap, vec2(val, 0.5));
            color.rgb *= uBrightness;
            float alpha = uOpacity * 0.15;

            color.rgb *= alpha;
            accumulated += (1.0 - accumulated.a) * vec4(color.rgb, alpha);

            if (accumulated.a > 0.95) break;  // early ray termination
        }

        t += stepSize;
    }

    if (accumulated.a < 0.01) discard;
    gl_FragColor = accumulated;
}
```

---

## 9. Testing

1. **Tri_angle case**: Open → post-processing → enable volume rendering → volume should be triangular prism, NOT rectangular box
2. **Rectangular room**: Volume should look identical to before (no regression)
3. **Banding**: Compare before/after jittering — wood-grain artifacts should disappear
4. **Interactive performance**: Rotate camera with volume visible — should feel responsive
5. **Resolution sweep**: Test 64/128/256 — higher res should produce smoother volumes
6. **Opacity/brightness sliders**: Should still work in real-time

---

## 10. Non-Goals

- Polygon proxy geometry (extruded room polygon replacing BoxGeometry) — not needed since sentinel skip achieves the same visual result
- Binary mask texture / occupancy octree — overkill for current use cases
- Server-side changes — everything is client-side
- Multi-volume rendering
- Gradient-based opacity (ParaView's 2D transfer function)
