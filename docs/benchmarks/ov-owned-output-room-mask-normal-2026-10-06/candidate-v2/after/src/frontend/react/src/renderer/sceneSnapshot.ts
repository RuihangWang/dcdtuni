/** Derived render data only. The editable project and solver manifests stay authoritative. */
import * as THREE from 'three'
import { validateSceneSnapshot } from './validateScene'
import { encodePngRgba, pngDataUrl } from './pngEncode'

export type Vec3 = [number, number, number]
export interface CameraState {
  position: Vec3
  target: Vec3
  up: Vec3
  fov: number
  aspect: number
  near: number
  far: number
}
export interface RenderViewState {
  camera?: CameraState
  selectedUids: string[]
  timeSeconds: number | null
}
export interface RenderMaterial {
  color: Vec3
  emissive: Vec3
  opacity: number
  roughness: number
  metalness: number
  doubleSided: boolean
  unlit: boolean
  map?: string
  normalMap?: string
  roughnessMap?: string
  metalnessMap?: string
  normalScale?: [number, number]
  alphaTest?: number
  mapColorSpace?: 'srgb' | 'linear'
  normalColorSpace?: 'srgb' | 'linear'
  wrapS?: 'repeat' | 'clamp' | 'mirror'
  wrapT?: 'repeat' | 'clamp' | 'mirror'
}
export interface RenderPrimitive {
  id: string
  modelUid: string
  equipmentUid?: string
  /** Derived display-only enclosure role; never a model visibility or physics edit. */
  displayRole?: 'room-shell'
  /** Which side of a rack's liquid manifold this primitive belongs to, so
   * result colouring can tell manifolds from the rack frame. */
  liquidPart?: 'supply-manifold' | 'return-manifold'
  name: string
  kind: 'triangles' | 'lines' | 'points'
  positions: number[]
  indices: number[]
  normals?: number[]
  uvs?: number[]
  colors?: number[]
  matrix: number[]
  visible: boolean
  material: RenderMaterial
  width?: number
}
export interface SceneSnapshot {
  schema: 'dcdtuni.render-scene/v1'
  project: { uid: string; name: string }
  coordinateSystem: { upAxis: 'Y'; metersPerUnit: 1; handedness: 'right' }
  primitives: RenderPrimitive[]
  textures: Record<string, string>
  view: RenderViewState
  warnings: string[]
}
export interface SnapshotOptions {
  root: THREE.Object3D
  project: { uid?: string; name?: string; meta?: { projectName?: string }; constructions?: unknown }
  camera?: THREE.PerspectiveCamera
  target?: THREE.Vector3
  selectedUids?: readonly string[]
  timeSeconds?: number | null
  helpers?: readonly THREE.Object3D[]
  /** Material to export instead of the mesh's current one (e.g. the
   * original under a result-colour overlay); undefined keeps the mesh's. */
  materialOf?: (mesh: THREE.Mesh) => THREE.Material | THREE.Material[] | undefined
}

const HELPER_TAGS = [
  'isHelper', 'isAnchorMarker', 'isSlotBoundaryGuide', 'isWaypoint',
  'isPipeWaypoint', 'isPipeControlPoint', 'isPipeSegmentWaypoint',
  'isJunctionPort', 'isJunctionPortSphere', 'isSeedHandle', 'isPatchHandle',
  'rotateGizmoAxis', 'rotateGizmoModelId', 'isMoveGizmo', 'isResizeHandle',
  'isFaceHandle', 'isSnapIndicator', 'isPreviewNormalHelper',
  // run/meshVisuals creates these annotation meshes outside sceneBuilder's
  // renderHelpers array. Match their explicit semantic tags, never group names.
  'isLocInMesh', 'isExtraLocInMesh', 'isRZone', 'isRZPlane', 'isRZCenter', 'isBMGrid',
  // run/sensorSpheres: probe balls, also under globalGroup and hidden off the Solve tab.
  'isSensorSphere',
  // shared/js/supply_flow_anim: the animated wall-ACU supply chevrons.
  'isSupplyFlow',
]

function identityEvidence(object: THREE.Object3D, path: string) {
  const keys = ['modelId', 'pipeModelId', 'pipeId', 'junctionId', 'equipmentId', 'valveId',
    'isPipeGroup', 'isPipe', 'isValve', 'isValveConnection', 'isRoomBoundary', 'isLiquidManifold',
    'isJunctionBody', 'isPortCoupler', 'isBlindEndCap', 'liquidVisualDetail', ...HELPER_TAGS]
  const ancestry: object[] = []
  let current: THREE.Object3D | null = object
  while (current && ancestry.length < 6) {
    const identity: Record<string, unknown> = {}
    for (const key of keys) {
      const value = current.userData[key]
      if (value === null || typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number') identity[key] = value
    }
    ancestry.push({ type: current.type, name: current.name,
      geometryType: (current as THREE.Mesh).geometry?.type,
      geometryName: (current as THREE.Mesh).geometry?.name, identity })
    current = current.parent
  }
  return JSON.stringify({ path, ancestry })
}

/** Standalone fitting-to-manifold tubes carry only valveId in the live renderer.
 * The authored pipe record, rather than a nearest-object guess, determines ownership.
 */
function valveOwners(project: SnapshotOptions['project']): Map<string, string> {
  const constructions = project.constructions as { pipeSegments?: Record<string, { uid?: string; cooling?: { valves?: unknown } }> } | undefined
  const owners = new Map<string, string>()
  for (const [uid, pipe] of Object.entries(constructions?.pipeSegments ?? {})) {
    if (pipe.uid && pipe.uid !== uid) throw new Error(`Pipe UID does not match construction key: ${uid}`)
    const valves = pipe.cooling?.valves
    if (valves === undefined || valves === null) continue
    if (!Array.isArray(valves)) throw new Error(`Invalid valve records on pipe ${uid}`)
    for (const valve of valves) {
      const id = (valve as { id?: unknown })?.id
      if (typeof id !== 'string' || !id) throw new Error(`Valve on ${uid} has no authored identity`)
      if (owners.has(id)) throw new Error(`Ambiguous valve identity ${id}: ${owners.get(id)} and ${uid}`)
      owners.set(id, uid)
    }
  }
  return owners
}

function finite(values: number[], context: string): number[] {
  if (!values.every(Number.isFinite)) throw new Error(`Non-finite ${context}`)
  return values
}
function attribute(a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute, size: number): number[] {
  const out: number[] = []
  for (let i = 0; i < a.count; i++) {
    out.push(a.getX(i), ...(size > 1 ? [a.getY(i)] : []), ...(size > 2 ? [a.getZ(i)] : []))
  }
  return finite(out, 'geometry attribute')
}

/** JPEG sources stay JPEG: as PNG the photo textures were ~4x larger and made
 * up most of a ~40 MB scene transfer. Alpha-bearing PNG sources stay PNG. */
export function textureMimeFor(url: string | null): 'image/png' | 'image/jpeg' {
  return url && /\.jpe?g$/i.test(url.split(/[?#]/, 1)[0]!) ? 'image/jpeg' : 'image/png'
}

/** A loaded URL image never changes, so each resend reuses its encoding.
 * Canvases are not cached: procedural ones may be redrawn. */
const encodedImages = new WeakMap<object, string>()

/** Canvas-backed procedural textures are embedded, so a Kit host needs no Mac paths. */
function textureImage(texture: THREE.Texture): string {
  const image = texture.image as HTMLImageElement | HTMLCanvasElement | ImageBitmap | undefined
  if (!image || !image.width || !image.height) throw new Error('Texture image is not ready')
  if ('toDataURL' in image) return image.toDataURL('image/png')
  const cached = encodedImages.get(image)
  if (cached) return cached
  const canvas = document.createElement('canvas')
  canvas.width = image.width
  canvas.height = image.height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Cannot serialize texture without a canvas')
  ctx.drawImage(image, 0, 0)
  const mime = textureMimeFor(imageUrl(image))
  const data = mime === 'image/jpeg' ? canvas.toDataURL(mime, 0.92) : canvas.toDataURL(mime)
  encodedImages.set(image, data)
  return data
}

/** cyrb53: a fast 53-bit string hash. Texture ids only need to be stable per image. */
function contentHash(text: string): string {
  let h1 = 0xdeadbeef
  let h2 = 0x41c6ce57
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i)
    h1 = Math.imul(h1 ^ ch, 2654435761)
    h2 = Math.imul(h2 ^ ch, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16).padStart(14, '0')
}

function imageUrl(image: unknown): string | null {
  const img = image as { currentSrc?: unknown; src?: unknown } | null | undefined
  const src = typeof img?.currentSrc === 'string' && img.currentSrc ? img.currentSrc : img?.src
  return typeof src === 'string' && src && !src.startsWith('data:') ? src : null
}

/**
 * The snapshot's texture table: every IMAGE is serialised once and keyed by
 * its content, not by the texture object.
 *
 * Several texture objects routinely show one image: clones at different
 * repeats share `Texture.source`; two kinds that load one folder get two
 * loads of the same URL; two folders can hold byte-identical files. Keyed by
 * `texture.uuid`, each of those was a separate multi-megabyte PNG in the
 * transfer. Colour space and wrapping ride on the material, so one image
 * serves every use of it.
 */
class TextureTable {
  private readonly bySource = new Map<object, string>()
  private readonly byUrl = new Map<string, string>()
  constructor(private readonly out: Record<string, string>) {}
  key(tex: THREE.Texture): string {
    const identity = (tex.source as object | undefined) ?? (tex.image as object)
    const known = this.bySource.get(identity)
    if (known) return known
    const url = imageUrl(tex.image)
    const byUrl = url ? this.byUrl.get(url) : undefined
    if (byUrl) {
      this.bySource.set(identity, byUrl)
      return byUrl
    }
    const data = textureImage(tex)
    const base = `tex-${contentHash(data)}`
    let key = base
    for (let n = 1; this.out[key] !== undefined && this.out[key] !== data; n++) key = `${base}-${n}`
    this.out[key] = data
    this.bySource.set(identity, key)
    if (url) this.byUrl.set(url, key)
    return key
  }
}

/** geometry_pure's `_rackDoorShaderMaterial` publishes its pattern here. */
interface PerforatedDoorSpec {
  kind: 'perforatedDoor'
  color: [number, number, number]
  opacity: number
  shape: number
  halfExtent: number
  cells: [number, number]
}

const DOOR_TILE_W = 32
const DOOR_TILE_H = 64 // two lattice rows: round and hexagonal holes are staggered
const doorTiles = new Map<string, string>()

function smoothstep(a: number, b: number, x: number) {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)))
  return t * t * (3 - 2 * t)
}

/**
 * One cell wide, two rows high, of the door shader's hole pattern: white
 * metal with alpha = the shader's `edge` (0 inside a hole). Row 0 is the
 * BOTTOM row, as the shader counts rows up the door (v up, flipY).
 */
export function perforatedDoorTile(shape: number, half: number): string {
  const key = `${shape}|${half.toFixed(5)}`
  const cached = doorTiles.get(key)
  if (cached) return cached
  const rgba = new Uint8Array(DOOR_TILE_W * DOOR_TILE_H * 4)
  for (let py = 0; py < DOOR_TILE_H; py++) {
    const v = ((DOOR_TILE_H - py - 0.5) / DOOR_TILE_H) * 2
    const row = Math.floor(v)
    const stagger = shape === 0 || shape === 3 ? (row % 2) * 0.5 : 0
    for (let px = 0; px < DOOR_TILE_W; px++) {
      const u = (px + 0.5) / DOOR_TILE_W + stagger
      const fx = u - Math.floor(u) - 0.5
      const fy = v - Math.floor(v) - 0.5
      const ax = Math.abs(fx)
      const ay = Math.abs(fy)
      const d = shape === 0 ? Math.hypot(fx, fy)
        : shape === 1 ? Math.max(ax, ay)
          : shape === 2 ? ax + ay
            : Math.max(ax * 0.5 + ay * 0.8660254, ax)
      const i = (py * DOOR_TILE_W + px) * 4
      rgba[i] = rgba[i + 1] = rgba[i + 2] = 255
      rgba[i + 3] = Math.round(smoothstep(half, half + 0.03, d) * 255)
    }
  }
  const url = pngDataUrl(encodePngRgba(DOOR_TILE_W, DOOR_TILE_H, rgba))
  doorTiles.set(key, url)
  return url
}

/**
 * What the snapshot exports for a material, which is the material itself
 * unless it is a shader the USD side cannot run. The perforated rack door is
 * the one such shader in the scene; it is approximated by a textured,
 * alpha-tested standard material with the same hole pattern and pitch, so a
 * room with rack doors can be sent to Omniverse at all.
 */
function exportableMaterial(m: THREE.Material, proxies: Map<THREE.Material, THREE.Material>): THREE.Material {
  if (m.type !== 'ShaderMaterial') return m
  const spec = m.userData?.renderSnapshot as PerforatedDoorSpec | undefined
  if (spec?.kind !== 'perforatedDoor') throw new Error('Unsupported render material ShaderMaterial')
  const known = proxies.get(m)
  if (known) return known
  const image = {
    width: DOOR_TILE_W,
    height: DOOR_TILE_H,
    toDataURL: () => perforatedDoorTile(spec.shape, spec.halfExtent),
  }
  const map = new THREE.Texture(image as unknown as HTMLCanvasElement)
  map.colorSpace = THREE.SRGBColorSpace
  map.wrapS = map.wrapT = THREE.RepeatWrapping
  map.repeat.set(spec.cells[0], spec.cells[1] / 2)
  const proxy = new THREE.MeshStandardMaterial({
    // the shader writes uColor straight to the (sRGB) output
    color: new THREE.Color().setRGB(spec.color[0], spec.color[1], spec.color[2], THREE.SRGBColorSpace),
    map,
    opacity: spec.opacity,
    transparent: true,
    alphaTest: spec.opacity * 0.5,
    side: m.side,
    metalness: 0.6,
    roughness: 0.45,
    depthWrite: false,
  })
  proxy.name = 'perforatedDoor (render snapshot)'
  proxy.visible = m.visible
  proxies.set(m, proxy)
  return proxy
}

type SupportedMaterial = THREE.Material & {
  color?: THREE.Color; emissive?: THREE.Color; emissiveIntensity?: number
  roughness?: number; metalness?: number; shininess?: number
  map?: THREE.Texture | null; normalMap?: THREE.Texture | null
  roughnessMap?: THREE.Texture | null; metalnessMap?: THREE.Texture | null
  vertexColors?: boolean
  normalScale?: THREE.Vector2
}
function clamp01(value: number) {
  return Math.min(1, Math.max(0, value))
}

function materialSnapshot(m: THREE.Material, textures: TextureTable, warnings: Set<string>): RenderMaterial {
  if (!['MeshStandardMaterial', 'MeshPhysicalMaterial', 'MeshPhongMaterial', 'MeshLambertMaterial', 'MeshBasicMaterial', 'LineBasicMaterial', 'LineDashedMaterial', 'PointsMaterial'].includes(m.type)) {
    throw new Error(`Unsupported render material ${m.type}`)
  }
  const mat = m as SupportedMaterial
  for (const key of ['alphaMap', 'aoMap', 'lightMap', 'emissiveMap', 'bumpMap', 'displacementMap', 'envMap', 'specularMap'] as const) {
    if ((m as unknown as Record<string, unknown>)[key]) throw new Error(`Unsupported material texture ${key}; conversion cannot silently discard it`)
  }
  const colour = mat.color ?? new THREE.Color(1, 1, 1)
  const emissive = (mat.emissive ?? new THREE.Color(0, 0, 0)).clone().multiplyScalar(mat.emissiveIntensity ?? 1)
  // A PBR material's own scalars win: its roughness map, when bound, is
  // multiplied by that scalar on both sides. A Phong preset material carries
  // its preset's physical values in userData.pbr (geometry_scene.js); only
  // a Phong material without them falls back to converting shininess.
  const hint = (m.userData?.pbr ?? {}) as { roughness?: unknown; metalness?: unknown }
  const roughness = typeof mat.roughness === 'number' ? mat.roughness
    : typeof hint.roughness === 'number' ? hint.roughness
      : Math.sqrt(2 / ((mat.shininess ?? 30) + 2))
  const metalness = typeof mat.metalness === 'number' ? mat.metalness
    : typeof hint.metalness === 'number' ? hint.metalness : 0
  const out: RenderMaterial = {
    color: colour.toArray() as Vec3, emissive: emissive.toArray() as Vec3,
    opacity: mat.opacity, roughness: clamp01(roughness),
    metalness: clamp01(metalness), doubleSided: mat.side === THREE.DoubleSide,
    unlit: mat.type === 'MeshBasicMaterial' || mat.type.startsWith('Line') || mat.type === 'PointsMaterial',
    alphaTest: mat.alphaTest,
  }
  if (mat.type === 'MeshPhongMaterial' && typeof hint.roughness !== 'number') warnings.add('Phong shininess is converted to USD Preview Surface roughness; lighting is renderer-specific.')
  if (mat.type === 'MeshPhysicalMaterial' || mat.type === 'LineDashedMaterial') throw new Error(`Unsupported render material ${mat.type}; physical extensions/dashed curves require an adapter`)
  const textureKeys = ['map', 'normalMap', 'roughnessMap', 'metalnessMap'] as const
  for (const key of textureKeys) {
    const tex = mat[key]
    if (!tex) continue
    if (tex.channel !== 0) throw new Error('Only UV channel 0 is currently supported')
    if (tex.matrixAutoUpdate) tex.updateMatrix()
    out[key] = textures.key(tex)
    if (key === 'map' || key === 'normalMap') out[key === 'map' ? 'mapColorSpace' : 'normalColorSpace'] = tex.colorSpace === THREE.SRGBColorSpace ? 'srgb' : 'linear'
    else if (tex.colorSpace === THREE.SRGBColorSpace) throw new Error(`${key} must use linear data colour space`)
  }
  if (mat.normalMap) out.normalScale = (mat.normalScale?.toArray() ?? [1, 1]) as [number, number]
  if (mat.map && mat.normalMap && !mat.map.matrix.equals(mat.normalMap.matrix)) throw new Error('Texture transforms differ between colour and normal maps')
  if (mat.map && mat.normalMap && (mat.map.flipY !== mat.normalMap.flipY || mat.map.wrapS !== mat.normalMap.wrapS || mat.map.wrapT !== mat.normalMap.wrapT)) throw new Error('Texture wrapping/orientation differs between colour and normal maps')
  const tex = mat.map ?? mat.normalMap ?? mat.roughnessMap ?? mat.metalnessMap
  if (tex) {
    for (const key of textureKeys) {
      const other = mat[key]
      if (other && (!other.matrix.equals(tex.matrix) || other.flipY !== tex.flipY || other.wrapS !== tex.wrapS || other.wrapT !== tex.wrapT)) throw new Error(`Texture UV transform/wrapping differs for ${key}`)
    }
    const wrap = (value: number) => value === THREE.RepeatWrapping ? 'repeat' : value === THREE.MirroredRepeatWrapping ? 'mirror' : 'clamp'
    out.wrapS = wrap(tex.wrapS)
    out.wrapT = wrap(tex.wrapT)
  }
  if (mat.vertexColors && mat.map) throw new Error('Vertex colours combined with a colour texture require a shader adapter')
  if (mat.side === THREE.BackSide) warnings.add('Back-side materials are exported with reversed triangle winding.')
  return out
}

export function captureViewState(options: SnapshotOptions): RenderViewState {
  options.root.updateWorldMatrix(true, true)
  const inverse = options.root.matrixWorld.clone().invert()
  const { camera } = options
  let viewCamera: CameraState | undefined
  if (camera) {
    camera.updateWorldMatrix(true, false)
    const position = camera.getWorldPosition(new THREE.Vector3()).applyMatrix4(inverse)
    const target = (options.target?.clone() ?? camera.getWorldDirection(new THREE.Vector3()).add(camera.position)).applyMatrix4(inverse)
    viewCamera = {
      position: position.toArray() as Vec3, target: target.toArray() as Vec3,
      up: camera.up.clone().transformDirection(inverse).toArray() as Vec3,
      fov: camera.fov, aspect: camera.aspect, near: camera.near, far: camera.far,
    }
  }
  const time = options.timeSeconds ?? null
  if (time !== null && (!Number.isFinite(time) || time < 0)) throw new Error('Physical time must be finite and non-negative')
  return { ...(viewCamera ? { camera: viewCamera } : {}), selectedUids: [...(options.selectedUids ?? [])], timeSeconds: time }
}

function roomShellUids(project: SnapshotOptions['project']): Set<string> {
  const constructions = project.constructions
  const result = new Set<string>()
  if (!constructions || typeof constructions !== 'object') return result
  for (const bucket of ['walls', 'floors']) {
    const entries = (constructions as Record<string, unknown>)[bucket]
    if (!entries || typeof entries !== 'object' || Array.isArray(entries)) continue
    for (const [key, value] of Object.entries(entries)) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) continue
      const record = value as { uid?: unknown; isRoomBoundary?: unknown }
      if (record.isRoomBoundary === true) result.add(typeof record.uid === 'string' ? record.uid : key)
    }
  }
  return result
}

export function buildSceneSnapshot(options: SnapshotOptions): SceneSnapshot {
  const { root } = options
  root.updateWorldMatrix(true, true)
  const inverse = root.matrixWorld.clone().invert()
  const helpers = new Set(options.helpers ?? [])
  const fittingOwners = valveOwners(options.project)
  const roomShells = roomShellUids(options.project)
  const warnings = new Set<string>()
  const proxies = new Map<THREE.Material, THREE.Material>()
  const packet: SceneSnapshot = {
    schema: 'dcdtuni.render-scene/v1',
    project: { uid: options.project.uid ?? '', name: options.project.meta?.projectName ?? options.project.name ?? '' },
    coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' },
    primitives: [], textures: {}, view: captureViewState(options), warnings: [],
  }
  const textureTable = new TextureTable(packet.textures)
  function visit(o: THREE.Object3D, parentUid: string, path: string, parentVisible: boolean, parentEquipmentUid = '') {
    if (helpers.has(o) || /Helper$/.test(o.type) || HELPER_TAGS.some(k => !!o.userData[k])) return
    const d = o.userData
    let uid = String(d.modelId ?? d.pipeModelId ?? d.pipeId ?? d.junctionId ?? parentUid ?? '')
    const equipmentUid = String(d.equipmentId ?? d.valveId ?? parentEquipmentUid)
    // Rack manifolds and their stubs (geometry_pure isLiquidManifold) carry the
    // side as pipeType/stubType; an explicit userData.liquidPart wins.
    const side = d.isLiquidManifold ? (d.pipeType ?? d.stubType) : undefined
    const liquidPart = d.liquidPart === 'supply-manifold' || d.liquidPart === 'return-manifold' ? d.liquidPart as RenderPrimitive['liquidPart']
      : side === 'supply' ? 'supply-manifold' as const : side === 'return' ? 'return-manifold' as const : undefined
    if (d.valveId) {
      const owner = fittingOwners.get(String(d.valveId))
      if (!owner) throw new Error(`Unresolved authored valve ${String(d.valveId)}: ${identityEvidence(o, path)}`)
      if (uid && uid !== owner) throw new Error(`Valve owner mismatch ${String(d.valveId)}: render=${uid}, project=${owner}`)
      uid = owner
    }
    const visible = parentVisible && o.visible
    const renderable = o as THREE.Mesh
    if (renderable.isMesh || (o as THREE.Line).isLine || (o as THREE.Points).isPoints) {
      if (!uid) throw new Error(`Renderable has no project identity: ${identityEvidence(o, path)}`)
      if ((o as THREE.SkinnedMesh).isSkinnedMesh || Object.keys(renderable.geometry.morphAttributes).length) throw new Error(`Unsupported deformed geometry: ${o.type}`)
      const g = renderable.geometry
      const pos = g.getAttribute('position')
      if (!pos || pos.itemSize !== 3) throw new Error(`Missing vec3 positions: ${uid}`)
      const kind = renderable.isMesh ? 'triangles' : (o as THREE.Line).isLine ? 'lines' : 'points'
      const rawIndices = g.index ? attribute(g.index, 1) : Array.from({ length: pos.count }, (_, i) => i)
      const material = options.materialOf?.(renderable as THREE.Mesh) ?? renderable.material
      const mats = Array.isArray(material) ? material : [material]
      const groups = Array.isArray(material) ? g.groups : [{ start: 0, count: rawIndices.length, materialIndex: 0 }]
      if (!groups.length) throw new Error(`Multi-material geometry has no groups: ${uid}`)
      const instanced = o as THREE.InstancedMesh
      const count = instanced.isInstancedMesh ? instanced.count : 1
      for (let instance = 0; instance < count; instance++) {
        const matrix = inverse.clone().multiply(o.matrixWorld)
        if (instanced.isInstancedMesh) {
          const im = new THREE.Matrix4()
          instanced.getMatrixAt(instance, im)
          matrix.multiply(im)
        }
        for (let gi = 0; gi < groups.length; gi++) {
          const group = groups[gi]!
          const start = Math.max(group.start, g.drawRange.start)
          const end = Math.min(group.start + group.count, g.drawRange.start + g.drawRange.count, rawIndices.length)
          if (end <= start) continue
          const liveMat = mats[group.materialIndex ?? 0]
          if (!liveMat) throw new Error(`Missing material for ${uid}, group ${gi}`)
          const mat = exportableMaterial(liveMat, proxies)
          const material = materialSnapshot(mat, textureTable, warnings)
          if (instanced.isInstancedMesh && instanced.instanceColor) {
            const tint = new THREE.Color()
            instanced.getColorAt(instance, tint)
            material.color = new THREE.Color(...material.color).multiply(tint).toArray() as Vec3
          }
          let indices = rawIndices.slice(start, end)
          if (kind === 'triangles' && indices.length % 3) throw new Error(`Incomplete triangles: ${uid}`)
          if (kind === 'lines' && !(o as THREE.LineSegments).isLineSegments) {
            const joined: number[] = []
            for (let i = 1; i < indices.length; i++) joined.push(indices[i - 1]!, indices[i]!)
            if ((o as THREE.LineLoop).isLineLoop && indices.length > 1) joined.push(indices.at(-1)!, indices[0]!)
            indices = joined
          }
          if (kind === 'lines' && indices.length % 2) throw new Error(`Incomplete line segments: ${uid}`)
          if (mat.side === THREE.BackSide && kind === 'triangles') {
            for (let i = 0; i < indices.length; i += 3) [indices[i + 1], indices[i + 2]] = [indices[i + 2]!, indices[i + 1]!]
          }
          const item: RenderPrimitive = {
            id: `${uid}/${path}/${instance}/${gi}`, modelUid: uid,
            ...(equipmentUid ? { equipmentUid } : {}),
            ...(roomShells.has(uid) ? { displayRole: 'room-shell' as const } : {}),
            ...(liquidPart ? { liquidPart } : {}),
            name: o.name || String(d.exportName ?? ''), kind,
            positions: attribute(pos, 3), indices,
            matrix: finite(matrix.toArray(), 'world transform'), visible: visible && mat.visible, material,
          }
          if (kind !== 'triangles') {
            item.width = (mat as THREE.PointsMaterial).size ?? .001
            if (kind === 'lines') warnings.add('Screen-space line width is rendered as a 1 mm USD curve; surface geometry and pipe diameters are unchanged.')
          }
          const n = g.getAttribute('normal')
          if (n) item.normals = attribute(n, 3)
          const uv = g.getAttribute('uv')
          if (uv) {
            item.uvs = attribute(uv, 2)
            const textureMaterial = mat as SupportedMaterial
            const tex = textureMaterial.map ?? textureMaterial.normalMap ?? textureMaterial.roughnessMap ?? textureMaterial.metalnessMap
            if (tex) {
              if (tex.matrixAutoUpdate) tex.updateMatrix()
              for (let i = 0; i < item.uvs.length; i += 2) {
                const v = new THREE.Vector2(item.uvs[i], item.uvs[i + 1])
                v.applyMatrix3(tex.matrix)
                item.uvs[i] = v.x
                item.uvs[i + 1] = tex.flipY ? v.y : 1 - v.y
              }
            }
          } else if (material.map || material.normalMap || material.roughnessMap || material.metalnessMap) throw new Error(`Textured mesh lacks UVs: ${uid}`)
          const colors = g.getAttribute('color')
          if (colors && (mat as SupportedMaterial).vertexColors) {
            item.colors = attribute(colors, 3).map((v, i) => v * material.color[i % 3]!)
          }
          packet.primitives.push(item)
        }
      }
    } else if ((o as THREE.Sprite).isSprite && visible) {
      throw new Error(`Unsupported render object: Sprite (${path})`)
    }
    o.children.forEach((child, i) => visit(child, uid, `${path}.${i}`, visible, equipmentUid))
  }
  visit(root, '', 'root', true)
  packet.warnings = [...warnings]
  validateSceneSnapshot(packet)
  return packet
}
