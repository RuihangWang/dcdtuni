/**
 * Volume rendering: a ray-marched 3D texture of the field.
 *
 * The server resamples the unstructured result onto a regular grid
 * (/api/pp/volume) and this marches a ray through it per fragment. Ported from
 * `_ppVolumeShaderMaterial` / `_ppBuildVolumeMesh` (STL_generate.html:20027),
 * including the three things that are not obvious and each of which produces a
 * plausible-looking wrong picture:
 *
 * - **Original VTK validity controls interpolation.** The grid is a box; the
 *   room is not. A legitimate physical scalar may equal the legacy sentinel,
 *   so original 0/1 occupancy, carried separately, selects valid nodes.
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
import {bindNumericProduct} from './numericProducts'
import { colormapData, createColormapTexture, type ColorMap } from './ppColormaps'
import { assertRevisionHeader, revisionHeaders, type PostprocessSession } from './ppRevision'

export interface VolumePayload {
  dimensions: [number, number, number]
  origin: [number, number, number]
  spacing: [number, number, number]
  scalarRange: [number, number]
  sentinel: number
  scalars: Float32Array
  /** Original VTK point occupancy: scalar equality is never validity. */
  validity?: Uint8Array
}

/**
 * Decode /api/pp/volume: `uint32 headerLen | JSON header | Float32 scalars | Uint8 validity`.
 *
 * Signed shared products retain original values and binary VTK validity.
 * Older Three-only diagnostic products retain their legacy NaN handling.
 * The existing CPP product contains point nodes at origin + ijk * spacing.
 */
export function decodeVolume(buffer: ArrayBuffer): VolumePayload {
  if (buffer.byteLength < 8 || buffer.byteLength > 641 * 1024 * 1024) throw new Error('Volume product byte count is invalid')
  const view = new DataView(buffer)
  const headerLen = view.getUint32(0, true)
  if (headerLen > 1024 * 1024 || headerLen % 4 || 4 + headerLen > buffer.byteLength) throw new Error('Volume product header is invalid')
  const headerText = new TextDecoder().decode(new Uint8Array(buffer, 4, headerLen))
  const header = JSON.parse(headerText.replace(/\0+$/, '')) as {
    dimensions: [number, number, number]
    origin: [number, number, number]
    spacing: [number, number, number]
    scalarRange: [number, number]
    sentinel?: number
    validityMask?: { encoding: string; byteCount: number }
  }
  const dims = header.dimensions
  if (!Array.isArray(dims) || dims.length !== 3 || dims.some(n => !Number.isInteger(n) || n < 1 || n > 512)) throw new Error('Volume grid dimensions are invalid')
  for (const [name, vec] of [['origin', header.origin], ['spacing', header.spacing]] as const) {
    if (!Array.isArray(vec) || vec.length !== 3 || vec.some(v => typeof v !== 'number' || !Number.isFinite(v) || (name === 'spacing' && v <= 0))) throw new Error('Volume grid coordinates are invalid')
  }
  if (!Array.isArray(header.scalarRange) || header.scalarRange.length !== 2 || !header.scalarRange.every(v => typeof v === 'number' && Number.isFinite(v)) || header.scalarRange[0] > header.scalarRange[1]) throw new Error('Volume scalar range is invalid')
  const total = dims[0] * dims[1] * dims[2]
  const scalarOffset = 4 + headerLen
  const maskOffset = scalarOffset + total * 4
  const maskMeta = header.validityMask
  let validity: Uint8Array | undefined
  if (maskMeta !== undefined) {
    if (!maskMeta || typeof maskMeta !== 'object' || Object.keys(maskMeta).sort().join(',') !== 'byteCount,encoding' || maskMeta.encoding !== 'uint8-01' || !Number.isInteger(maskMeta.byteCount) || maskMeta.byteCount !== total || maskOffset + total !== buffer.byteLength) throw new Error('Volume validity byte count is invalid')
    validity = new Uint8Array(buffer, maskOffset, total)
    if (validity.some(v => v !== 0 && v !== 1)) throw new Error('Volume validity mask is not binary')
  } else if (maskOffset !== buffer.byteLength) throw new Error('Volume dimensions differ from actual bytes')
  const scalars = new Float32Array(buffer, scalarOffset, total)
  const sentinel = header.sentinel ?? header.scalarRange[0] - 1.0
  if (typeof sentinel !== 'number' || !Number.isFinite(sentinel)) throw new Error('Volume sentinel is invalid')
  let low = Infinity, high = -Infinity
  for (let i = 0; i < total; i++) {
    if (validity) {
      if (validity[i] === 0) continue
      if (!Number.isFinite(scalars[i])) throw new Error('Volume valid sample is nonfinite')
      low = Math.min(low, scalars[i]!); high = Math.max(high, scalars[i]!)
    } else if (Number.isNaN(scalars[i])) scalars[i] = sentinel
    else if (!Number.isFinite(scalars[i])) throw new Error('Volume sample is infinite')
  }
  if (validity && (header.scalarRange[0] !== (low === Infinity ? 0 : low) || header.scalarRange[1] !== (high === -Infinity ? 0 : high))) throw new Error('Volume range differs from original valid samples')
  return { ...header, sentinel, scalars, validity }
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
  return bindNumericProduct(decodeVolume(await resp.arrayBuffer()),resp,'volume',session)
}

/**
 * A robust [lo, hi] from percentiles using original occupancy when available.
 * Volume masks retain literal flat ranges; legacy surface callers keep widening.
 *
 * Min/max would let one 87 °C cell stretch the scale until the real 21-37 °C
 * data washes to a single colour. Ported from `_ppPercentileRange`.
 */
export function percentileRange(
  values: Float32Array,
  loFrac: number,
  hiFrac: number,
  sentinel?: number,
  validity?: Uint8Array,
): [number, number] | null {
  if (validity && (validity.length !== values.length || validity.some(v => v !== 0 && v !== 1))) throw new Error('Volume percentile validity is invalid')
  const marker = sentinel !== undefined ? Math.fround(sentinel) : null
  const kept: number[] = []
  for (let i = 0; i < values.length; i++) {
    const v = values[i] as number
    if (!Number.isFinite(v)) continue
    if (validity ? validity[i] === 0 : marker !== null && v === marker) continue
    kept.push(v)
  }
  if (!kept.length) return null
  kept.sort((a, b) => a - b)
  const lo = kept[Math.max(0, Math.floor(loFrac * (kept.length - 1)))] as number
  let hi = kept[Math.min(kept.length - 1, Math.ceil(hiFrac * (kept.length - 1)))] as number
  if (!validity && hi <= lo) hi = lo + 1e-6
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
uniform sampler3D uValidity;
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

vec2 sampleVolumeSafe(vec3 tc) {
    tc = clamp(tc, vec3(0.0), vec3(1.0));
    vec3 voxelPos = tc * (uVolDims - 1.0);
    vec3 f = fract(voxelPos);
    vec3 base = (floor(voxelPos) + 0.5) / uVolDims;
    vec3 step = 1.0 / uVolDims;
    float sumW = 0.0, sumV = 0.0;
    for (int dz = 0; dz <= 1; dz++) {
    for (int dy = 0; dy <= 1; dy++) {
    for (int dx = 0; dx <= 1; dx++) {
        vec3 offset = vec3(float(dx), float(dy), float(dz));
        float v = texture(uVolume, base + offset * step).r;
        if (texture(uValidity, base + offset * step).r > 0.001) {
            vec3 w3 = mix(1.0-f, f, offset);
            float w = w3.x * w3.y * w3.z;
            sumW += w;
            sumV += w * v;
        }
    }}}
    if (sumW < 0.001) return vec2(0.0, 0.0);
    return vec2(sumV / sumW, 1.0);
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

        vec2 sampleValue = sampleVolumeSafe(texCoord);

        if (sampleValue.y > 0.5) {
            float scalar = sampleValue.x;
            float val = clamp((scalar - uMinValue) / max(uMaxValue - uMinValue, 1e-6), 0.0, 1.0);
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
  if (clim.length !== 2 || clim.some(v => typeof v !== 'number' || !Number.isFinite(v) || Math.abs(v) > 3.4028234663852886e38) || clim[0] > clim[1] || typeof opacity !== 'number' || !Number.isFinite(opacity) || opacity < 0 || opacity > 1) throw new Error('Volume domain/opacity is invalid')
  const dims = payload.dimensions
  const texture = new THREE.Data3DTexture(payload.scalars, dims[0], dims[1], dims[2])
  texture.format = THREE.RedFormat
  texture.type = THREE.FloatType
  // Hardware trilinear interpolation cannot exclude invalid original nodes.
  // Both scalar and original occupancy use nearest storage fetches.
  texture.minFilter = THREE.NearestFilter
  texture.magFilter = THREE.NearestFilter
  texture.wrapS = THREE.ClampToEdgeWrapping
  texture.wrapT = THREE.ClampToEdgeWrapping
  texture.wrapR = THREE.ClampToEdgeWrapping
  texture.unpackAlignment = 1
  texture.needsUpdate = true

  // Legacy Three-only diagnostic products lack a mask. Signed shared grids
  // require original VTK validity, including legal values equal to sentinel.
  const validity = payload.validity ?? Uint8Array.from(payload.scalars, v => Number.isFinite(v) && v !== Math.fround(payload.sentinel) ? 1 : 0)
  if (validity.length !== payload.scalars.length || validity.some(v => v !== 0 && v !== 1)) throw new Error('Volume validity is invalid')
  const maskTexture = new THREE.Data3DTexture(validity, dims[0], dims[1], dims[2])
  maskTexture.format = THREE.RedFormat; maskTexture.type = THREE.UnsignedByteType
  maskTexture.minFilter = THREE.NearestFilter; maskTexture.magFilter = THREE.NearestFilter
  maskTexture.wrapS = maskTexture.wrapT = maskTexture.wrapR = THREE.ClampToEdgeWrapping
  maskTexture.unpackAlignment = 1; maskTexture.needsUpdate = true

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
      uValidity: { value: maskTexture },
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
      maskTexture.dispose()
      colormap.dispose()
    },
  }
}

/** The colour-map bytes, for a colorbar drawn beside a volume. */
export { colormapData }
