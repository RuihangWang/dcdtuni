/**
 * Volume rendering: a ray-marched 3D texture of the field.
 *
 * The server resamples the unstructured result onto a regular grid
 * (/api/pp/volume) and this marches a ray through it per fragment. Ported from
 * `_ppVolumeShaderMaterial` / `_ppBuildVolumeMesh` (STL_generate.html:20027),
 * including the three things that are not obvious and each of which produces a
 * plausible-looking wrong picture:
 *
 * - **Sentinel voxels are skipped, and the interpolation skips them too.** The
 *   grid is a box; the room is not. Cells outside the fluid carry a large
 *   NEGATIVE sentinel, and a plain trilinear sample next to the wall would mix
 *   that into the colour and paint a dark rind around every surface.
 *
 * - **The opacity transfer function emphasises BOTH extremes.** A constant
 *   alpha per sample makes the volume a uniform fog with no structure; here
 *   values far from mid-range — hot aisles AND cold aisles — become dense while
 *   the middle stays translucent.
 *
 * - **The box is drawn from the INSIDE** (`BackSide`, depthTest off). Front
 *   faces would clip the moment the camera entered the room, which is exactly
 *   when a volume is worth looking at.
 */

import * as THREE from 'three'
import { colormapData, createColormapTexture, type ColorMap } from './ppColormaps'
import { assertRevisionHeader, revisionHeaders, type PostprocessSession } from './ppRevision'

export interface VolumePayload {
  dimensions: [number, number, number]
  origin: [number, number, number]
  spacing: [number, number, number]
  scalarRange: [number, number]
  sentinel: number
  scalars: Float32Array
}

/**
 * Decode /api/pp/volume: `uint32 headerLen | JSON header | Float32 scalars`.
 *
 * NaNs are replaced by the sentinel. The shader's skip test is `v > sentinel *
 * 0.01`, and NaN fails every comparison — so a NaN voxel would be neither
 * skipped nor blended, and would come out as whatever the hardware does with
 * NaN in a mix.
 */
export function decodeVolume(buffer: ArrayBuffer): VolumePayload {
  const view = new DataView(buffer)
  const headerLen = view.getUint32(0, true)
  const headerText = new TextDecoder().decode(new Uint8Array(buffer, 4, headerLen))
  const header = JSON.parse(headerText.replace(/\0+$/, '')) as {
    dimensions: [number, number, number]
    origin: [number, number, number]
    spacing: [number, number, number]
    scalarRange: [number, number]
    sentinel?: number
  }
  const dims = header.dimensions
  const total = dims[0] * dims[1] * dims[2]
  const scalars = new Float32Array(buffer, 4 + headerLen, total)
  const sentinel = header.sentinel ?? header.scalarRange[0] - 1.0
  for (let i = 0; i < total; i++) if (Number.isNaN(scalars[i])) scalars[i] = sentinel
  return { ...header, sentinel, scalars }
}

export async function fetchVolume(field: string, resolution: number, session: PostprocessSession): Promise<VolumePayload> {
  const resp = await fetch('/api/pp/volume', {
    method: 'POST',
    headers: revisionHeaders(session, true),
    body: JSON.stringify({ field, resolution }),
  })
  if (!resp.ok) {
    let detail = String(resp.status)
    try {
      detail = ((await resp.json()) as { error?: string }).error ?? detail
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new Error(`/api/pp/volume -> ${detail}`)
  }
  assertRevisionHeader(resp, session)
  return decodeVolume(await resp.arrayBuffer())
}

/**
 * A robust [lo, hi] from percentiles, ignoring NaN and sentinel voxels.
 *
 * Min/max would let one 87 °C cell stretch the scale until the real 21-37 °C
 * data washes to a single colour. Ported from `_ppPercentileRange`.
 */
export function percentileRange(
  values: Float32Array,
  loFrac: number,
  hiFrac: number,
  sentinel?: number,
): [number, number] | null {
  const threshold = sentinel !== undefined ? sentinel * 0.01 : null
  const kept: number[] = []
  for (let i = 0; i < values.length; i++) {
    const v = values[i] as number
    if (Number.isNaN(v)) continue
    if (threshold !== null && v <= threshold) continue
    kept.push(v)
  }
  if (!kept.length) return null
  kept.sort((a, b) => a - b)
  const lo = kept[Math.max(0, Math.floor(loFrac * (kept.length - 1)))] as number
  let hi = kept[Math.min(kept.length - 1, Math.ceil(hiFrac * (kept.length - 1)))] as number
  if (hi <= lo) hi = lo + 1e-6
  return [lo, hi]
}

const VERT = `varying vec3 vWorldPos;
void main() {
    vec4 worldPos = modelMatrix * vec4(position, 1.0);
    vWorldPos = worldPos.xyz;
    gl_Position = projectionMatrix * viewMatrix * worldPos;
}`

function frag(maxSteps: number): string {
  return `#define MAX_STEPS ${maxSteps}
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
uniform vec3 uVolDims;
varying vec3 vWorldPos;

vec2 intersectBox(vec3 orig, vec3 dir, vec3 bmin, vec3 bmax) {
    vec3 invDir = 1.0 / dir;
    vec3 t0 = (bmin - orig) * invDir;
    vec3 t1 = (bmax - orig) * invDir;
    vec3 tmin = min(t0, t1);
    vec3 tmax = max(t0, t1);
    return vec2(max(max(tmin.x, tmin.y), tmin.z), min(min(tmax.x, tmax.y), tmax.z));
}

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

float sampleVolumeSafe(vec3 tc) {
    vec3 voxelPos = tc * uVolDims - 0.5;
    vec3 f = fract(voxelPos);
    vec3 base = (floor(voxelPos) + 0.5) / uVolDims;
    vec3 step = 1.0 / uVolDims;
    float thresh = uSentinel * 0.01;
    float sumW = 0.0, sumV = 0.0;
    for (int dz = 0; dz <= 1; dz++) {
    for (int dy = 0; dy <= 1; dy++) {
    for (int dx = 0; dx <= 1; dx++) {
        vec3 offset = vec3(float(dx), float(dy), float(dz));
        float v = texture(uVolume, base + offset * step).r;
        if (v > thresh) {
            vec3 w3 = mix(1.0-f, f, offset);
            float w = w3.x * w3.y * w3.z;
            sumW += w;
            sumV += w * v;
        }
    }}}
    if (sumW < 0.001) return uSentinel;
    return sumV / sumW;
}

void main() {
    vec3 rayDir = normalize(vWorldPos - uCameraPos);
    vec2 tHit = intersectBox(uCameraPos, rayDir, uBoxMin, uBoxMax);
    if (tHit.x > tHit.y) discard;
    tHit.x = max(tHit.x, 0.0);

    vec3 boxSize = uBoxMax - uBoxMin;
    float maxDim = max(max(boxSize.x, boxSize.y), boxSize.z);
    float stepSize = maxDim / (float(MAX_STEPS) * 0.5);

    float jitter = hash(gl_FragCoord.xy) * stepSize;
    vec4 accumulated = vec4(0.0);
    float t = tHit.x + jitter;

    for (int i = 0; i < MAX_STEPS; i++) {
        if (t > tHit.y) break;
        vec3 sampleWorld = uCameraPos + rayDir * t;
        vec3 texCoord = (sampleWorld - uBoxMin) / boxSize;

        float scalar = sampleVolumeSafe(texCoord);

        if (scalar > uSentinel * 0.01) {
            float val = clamp((scalar - uMinValue) / (uMaxValue - uMinValue + 1e-10), 0.0, 1.0);
            vec4 color = texture2D(uColorMap, vec2(val, 0.5));
            color.rgb *= uBrightness;
            float tf = pow(abs(val - 0.5) * 2.0, 1.5);
            float alpha = uOpacity * (0.02 + 0.6 * tf);

            color.rgb *= alpha;
            accumulated += (1.0 - accumulated.a) * vec4(color.rgb, alpha);

            if (accumulated.a > 0.95) break;
        }

        t += stepSize;
    }

    if (accumulated.a < 0.01) discard;
    gl_FragColor = accumulated;
}`
}

export interface BuiltVolume {
  object: THREE.Mesh
  /** Called every frame: the ray marches from the camera. */
  setCameraPosition(p: THREE.Vector3): void
  dispose(): void
}

export function buildVolume(
  payload: VolumePayload,
  colorMap: ColorMap,
  clim: [number, number],
  opacity: number,
  resolution: number,
): BuiltVolume {
  const dims = payload.dimensions
  const texture = new THREE.Data3DTexture(payload.scalars, dims[0], dims[1], dims[2])
  texture.format = THREE.RedFormat
  texture.type = THREE.FloatType
  // NEAREST, not linear: the hardware's own trilinear filter cannot skip
  // sentinel voxels, and the shader does its own interpolation for exactly
  // that reason.
  texture.minFilter = THREE.NearestFilter
  texture.magFilter = THREE.NearestFilter
  texture.wrapS = THREE.ClampToEdgeWrapping
  texture.wrapT = THREE.ClampToEdgeWrapping
  texture.wrapR = THREE.ClampToEdgeWrapping
  texture.unpackAlignment = 1
  texture.needsUpdate = true

  const colormap = createColormapTexture(colorMap)
  const origin = payload.origin
  const spacing = payload.spacing
  const boxMin: [number, number, number] = [origin[0], origin[1], origin[2]]
  const boxMax: [number, number, number] = [
    origin[0] + spacing[0] * (dims[0] - 1),
    origin[1] + spacing[1] * (dims[1] - 1),
    origin[2] + spacing[2] * (dims[2] - 1),
  ]
  const size: [number, number, number] = [
    boxMax[0] - boxMin[0],
    boxMax[1] - boxMin[1],
    boxMax[2] - boxMin[2],
  ]
  // Steps scale with the grid so a 256³ volume is not sampled as coarsely as a
  // 64³ one, capped at 768 — beyond that the cost is per pixel and the picture
  // stops changing.
  const maxSteps = Math.min(Math.max(resolution || 128, 64) * 3, 768)

  const material = new THREE.ShaderMaterial({
    uniforms: {
      uVolume: { value: texture },
      uColorMap: { value: colormap },
      uMinValue: { value: clim[0] },
      uMaxValue: { value: clim[1] },
      uOpacity: { value: opacity },
      uBoxMin: { value: new THREE.Vector3(...boxMin) },
      uBoxMax: { value: new THREE.Vector3(...boxMax) },
      uCameraPos: { value: new THREE.Vector3() },
      uBrightness: { value: 1.0 },
      uSentinel: { value: payload.sentinel },
      uVolDims: { value: new THREE.Vector3(dims[0], dims[1], dims[2]) },
    },
    vertexShader: VERT,
    fragmentShader: frag(maxSteps),
    side: THREE.BackSide,
    transparent: true,
    depthWrite: false,
    depthTest: false,
  })

  const geometry = new THREE.BoxGeometry(size[0], size[1], size[2])
  const object = new THREE.Mesh(geometry, material)
  object.position.set(
    (boxMin[0] + boxMax[0]) / 2,
    (boxMin[1] + boxMax[1]) / 2,
    (boxMin[2] + boxMax[2]) / 2,
  )
  object.renderOrder = 900
  object.userData.ppLayer = 'volume'

  return {
    object,
    setCameraPosition(p) {
      ;(material.uniforms.uCameraPos!.value as THREE.Vector3).copy(p)
    },
    dispose() {
      geometry.dispose()
      material.dispose()
      texture.dispose()
      colormap.dispose()
    },
  }
}

/** The colour-map bytes, for a colorbar drawn beside a volume. */
export { colormapData }
