/**
 * The geometry drawn as one BatchedMesh per shared material and one merged
 * edge set per edge look.
 *
 * GTC's room is 3,366 opaque meshes over 18 materials plus 4,341 edge sets,
 * each edge set with its own material: 7,738 draw calls a frame, 89 % of the
 * frame inside WebGLRenderer.render (Chrome profiler, 2026-10-23) and 18 FPS
 * on an RTX 4090 with nothing faded. Batched it is a few dozen draws.
 *
 * Nothing of the scene changes. The originals stay where they are, visible
 * and pickable, with their materials: each is only kept out of the renderer's
 * draw list, by a per-object bounding sphere that the frustum test of the
 * renderer and of the shadow map reads (Frustum.intersectsObject) and the
 * raycaster does not (Mesh.raycast uses the geometry's). `sync`, run every
 * frame, hands an original back to the renderer the moment it stops matching
 * its batched copy: its material swapped (a selection highlight, a fade), its
 * shadow flags changed, it or a parent hidden, or (edges) moved. A moved mesh
 * just moves its instance.
 *
 * Meshes group by what their material DRAWS (`materialLook`), not by the
 * material object, so the selection highlight's clones batch again: every
 * tinted mesh of a selected model holds its own clone of the shared material
 * (interaction/selectionHighlight), and keyed by object each was a group of
 * one. GTC with every model selected drew 3,461 meshes one by one, 48 → 3,477
 * draw calls (headless Chromium, 2026-10-26). A group whose members share one
 * material draws with it, as before; a group of look-alike clones draws with a
 * copy of the first, and a member whose look then changes leaves the batch.
 */

import * as THREE from 'three'
import { BATCHED_LAYER } from './batchedLayer'

/** Interactive handles, which move and recolour on their own. A rack's U-slot
 * guides (isSlotBoundaryGuide) are batched: GTC draws 963 of them, all alike. */
const HELPER_FLAGS = [
  'isHelper', 'isAnchorMarker', 'isResizeHandle', 'isMoveGizmo', 'rotateGizmoAxis',
  'isSeedHandle', 'isPatchHandle', 'isWaypoint', 'isPipeWaypoint',
]

/** Out of every frustum: an original carrying it is never drawn. */
const CULLED = new THREE.Sphere(new THREE.Vector3(1e15, 1e15, 1e15), 0)

type Culled = THREE.Object3D & { boundingSphere?: THREE.Sphere | null }

export interface StaticBatch {
  readonly group: THREE.Group
  /** Originals drawn through the batch right now. */
  readonly meshes: number
  /** Meshes built into the batch that draw themselves now (a swapped material,
   * a hidden model): many of them mean the batch is due for a rebuild. */
  readonly releasedMeshes: number
  readonly edges: number
  /** Draw calls the batch issues in place of `meshes + edges`. */
  readonly draws: number
  sync(): void
  dispose(): void
}

interface MeshEntry {
  object: THREE.Mesh
  material: THREE.Material
  /** Set when the batch draws with a copy: the look the copy was taken at. */
  look: unknown[] | null
  /** With look: the fields of the mesh's material that change in place (materialPrint), as last compared. */
  print: Float64Array | null
  batch: THREE.BatchedMesh
  id: number
  world: number[]
  batched: boolean
  /** While batched and childless: position, quaternion, scale as last seen (its matrices are not auto-updated). */
  local: Float64Array | null
  parent: THREE.Object3D | null
  dirty: boolean
  full: boolean
}

interface EdgeEntry {
  object: THREE.Line
  material: THREE.LineBasicMaterial
  look: number[]
  positions: THREE.BufferAttribute
  start: number
  count: number
  world: number[]
  batched: boolean
  local: Float64Array | null
  parent: THREE.Object3D | null
  dirty: boolean
  full: boolean
}

function isHelper(object: THREE.Object3D): boolean {
  const data = object.userData as Record<string, unknown>
  return HELPER_FLAGS.some((flag) => data[flag])
}

/** Visible all the way up to (not including) `stop`. */
function shown(object: THREE.Object3D, stop: THREE.Object3D | null): boolean {
  for (let o: THREE.Object3D | null = object; o && o !== stop; o = o.parent) {
    if (!o.visible) return false
  }
  return true
}

/** Out of the camera's layers as well (batchedLayer.ts): three then skips its frustum test altogether. The layer
 * mask it had is kept to put back. */
const LAYER_BEFORE = new WeakMap<THREE.Object3D, number>()

function cull(object: THREE.Object3D): void {
  (object as Culled).boundingSphere = CULLED
  if (!LAYER_BEFORE.has(object)) LAYER_BEFORE.set(object, object.layers.mask)
  object.layers.set(BATCHED_LAYER)
}

function uncull(object: THREE.Object3D): void {
  if ((object as Culled).boundingSphere === CULLED) delete (object as Culled).boundingSphere
  const mask = LAYER_BEFORE.get(object)
  if (mask !== undefined) { object.layers.mask = mask; LAYER_BEFORE.delete(object) }
}

function sameMatrix(a: number[], b: THREE.Matrix4): boolean {
  const e = b.elements
  for (let i = 0; i < 16; i++) if (a[i] !== e[i]) return false
  return true
}

/*
 * A batched original is not drawn, but three still recomputes its matrices every frame (Alibaba-hongchou: 19,111
 * of them, a fifth of the frame). While it is batched and has no children its automatic updates are off: the batch
 * keeps its world matrix itself, from its parent's (checked once per parent and frame) and its own position,
 * rotation and scale (checked per mesh, ten numbers). Released, it updates as any object.
 */
function localOf(o: THREE.Object3D, out: Float64Array): Float64Array {
  out[0] = o.position.x; out[1] = o.position.y; out[2] = o.position.z
  out[3] = o.quaternion.x; out[4] = o.quaternion.y; out[5] = o.quaternion.z; out[6] = o.quaternion.w
  out[7] = o.scale.x; out[8] = o.scale.y; out[9] = o.scale.z
  return out
}

function sameLocal(a: Float64Array, o: THREE.Object3D): boolean {
  return a[0] === o.position.x && a[1] === o.position.y && a[2] === o.position.z
    && a[3] === o.quaternion.x && a[4] === o.quaternion.y && a[5] === o.quaternion.z && a[6] === o.quaternion.w
    && a[7] === o.scale.x && a[8] === o.scale.y && a[9] === o.scale.z
}

/** For a childless object with both automatic updates off, three's updateMatrixWorld computes nothing (it only clears
 * matrixWorldNeedsUpdate), yet it is called on every one of them every frame. */
function frozenUpdateMatrixWorld(): void {}

function freeze(o: THREE.Object3D): Float64Array | null {
  if (o.children.length || !o.parent) return null
  o.updateMatrix()
  o.matrixWorld.multiplyMatrices(o.parent.matrixWorld, o.matrix)
  o.matrixAutoUpdate = false
  o.matrixWorldAutoUpdate = false
  ;(o as unknown as { updateMatrixWorld: () => void }).updateMatrixWorld = frozenUpdateMatrixWorld
  return localOf(o, new Float64Array(10))
}

function thaw(o: THREE.Object3D): void {
  o.matrixAutoUpdate = true
  o.matrixWorldAutoUpdate = true
  delete (o as unknown as { updateMatrixWorld?: () => void }).updateMatrixWorld   // the prototype's again
}

/** What an edge set's material draws with; equal looks merge into one draw. */
function edgeLook(material: THREE.LineBasicMaterial): number[] {
  const c = material.color
  return [c.r, c.g, c.b, material.opacity, +material.transparent, +material.depthTest,
    +material.depthWrite, +material.visible]
}

function sameLook(a: number[], material: THREE.LineBasicMaterial): boolean {
  const c = material.color
  return a[0] === c.r && a[1] === c.g && a[2] === c.b && a[3] === material.opacity
    && a[4] === +material.transparent && a[5] === +material.depthTest && a[6] === +material.depthWrite
    && a[7] === +material.visible
}

const LOOK_MAPS = ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap',
  'lightMap', 'bumpMap', 'alphaMap', 'envMap', 'displacementMap'] as const

/**
 * Everything a MeshStandardMaterial or MeshBasicMaterial draws with, or null
 * for a material that may draw differently from a look-alike (another type, a
 * shader hook, clipping planes): those keep grouping by object.
 */
function materialLook(material: THREE.Material): unknown[] | null {
  const look: unknown[] = []
  return materialLookInto(material, look) ? look : null
}

/** materialLook written into `look` (cleared first); false for a material that has none. */
function materialLookInto(material: THREE.Material, look: unknown[]): boolean {
  look.length = 0
  if (material.type !== 'MeshStandardMaterial' && material.type !== 'MeshBasicMaterial') return false
  if (material.onBeforeCompile !== THREE.Material.prototype.onBeforeCompile
      || material.customProgramCacheKey !== THREE.Material.prototype.customProgramCacheKey
      || material.clippingPlanes?.length) return false
  const m = material as THREE.MeshStandardMaterial
  const defines = (material as { defines?: object }).defines
  look.push(
    m.type, m.side, m.transparent, m.opacity, m.depthWrite, m.depthTest, m.alphaTest, m.visible,
    m.vertexColors, m.wireframe, m.polygonOffset, m.polygonOffsetFactor, m.polygonOffsetUnits,
    m.toneMapped, m.fog, m.colorWrite, m.blending, m.premultipliedAlpha, m.dithering, m.forceSinglePass,
    m.color.r, m.color.g, m.color.b, defines ? JSON.stringify(defines) : 'null',
  )
  if (m.type === 'MeshStandardMaterial') {
    look.push(m.emissive.r, m.emissive.g, m.emissive.b, m.emissiveIntensity, m.metalness, m.roughness,
      m.envMapIntensity, m.flatShading, m.normalScale.x, m.normalScale.y, m.aoMapIntensity, m.bumpScale,
      m.lightMapIntensity, m.displacementScale, m.displacementBias, m.envMapRotation.x, m.envMapRotation.y,
      m.envMapRotation.z)
  } else {
    const b = material as THREE.MeshBasicMaterial
    look.push(b.reflectivity, b.refractionRatio, b.combine, b.aoMapIntensity, b.lightMapIntensity)
  }
  for (const key of LOOK_MAPS) {
    const texture = (m as unknown as Record<string, THREE.Texture | null | undefined>)[key]
    look.push(texture ? texture.uuid : null)
  }
  return true
}

/** What of a material the editor changes in place (fades, highlights, toggles): a cheap test before the full look. */
const PRINT = 16
const FULL_LOOK_EVERY = 30
/** Inside the viewport's render (batchedRenderPass) the batched members are hidden, so three's scene walk returns at
 * them at once: Alibaba-hongchou's 19,901 culled members were still a third of the frame. Their visibility is set
 * back right after, so picking, export and the editor read it unchanged. (An accessor that answered "hidden" during
 * the render cost more than the walk: three reads `visible` 20,000 times a frame.) */
const activeBatches = new Set<{ hide(): void; show(): void }>()

export function batchedRenderPass<T>(draw: () => T): T {
  for (const b of activeBatches) b.hide()
  try {
    return draw()
  } finally {
    for (const b of activeBatches) b.show()
  }
}

function materialPrint(material: THREE.Material, out: Float64Array): Float64Array {
  const m = material as THREE.MeshStandardMaterial
  out[0] = m.version; out[1] = m.opacity; out[2] = +m.transparent; out[3] = +m.visible; out[4] = +m.depthWrite
  out[5] = m.side; out[6] = m.color.r; out[7] = m.color.g; out[8] = m.color.b
  out[9] = m.emissive ? m.emissive.r : 0; out[10] = m.emissive ? m.emissive.g : 0; out[11] = m.emissive ? m.emissive.b : 0
  out[12] = m.emissiveIntensity ?? 0; out[13] = m.roughness ?? 0; out[14] = m.metalness ?? 0; out[15] = m.envMapIntensity ?? 0
  return out
}

function samePrint(a: Float64Array, b: Float64Array): boolean {
  for (let i = 0; i < PRINT; i++) if (a[i] !== b[i]) return false
  return true
}

function sameValues(a: unknown[], b: unknown[] | null): boolean {
  if (!b || a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}

/** A batched mesh: opaque, one plain material, culled like any other mesh. */
function batchableMesh(o: THREE.Object3D): o is THREE.Mesh {
  const mesh = o as THREE.Mesh
  if (!mesh.isMesh || (mesh as THREE.InstancedMesh).isInstancedMesh || (mesh as THREE.BatchedMesh).isBatchedMesh) return false
  const material = mesh.material
  if (!material || Array.isArray(material) || (material as THREE.ShaderMaterial).isShaderMaterial) return false
  // Transparent meshes batch too, sorted back to front per instance as three sorts objects: a faded room (the
  // post-processing tab draws the geometry at 15 %) was 19,111 transparent draws a frame on Alibaba-hongchou,
  // 4 FPS. A mesh off the default layer (a ghost, layer 2) keeps its own.
  if (!material.visible || mesh.frustumCulled === false || mesh.layers.mask !== 1) return false
  const geometry = mesh.geometry
  return !!geometry?.getAttribute('position') && !Object.keys(geometry.morphAttributes).length
}

function batchableEdge(o: THREE.Object3D): o is THREE.Line {
  const line = o as THREE.Line
  if (!line.isLine || line.frustumCulled === false) return false
  const material = line.material as THREE.LineBasicMaterial
  return !!material && !Array.isArray(material) && (material as THREE.Material).type === 'LineBasicMaterial'
    && !material.vertexColors && !!line.geometry?.getAttribute('position')
}

/** Vertex index pairs of a line's segments: LineSegments as drawn, Line and LineLoop as a strip. */
function segmentIndices(line: THREE.Line): number[] {
  const geometry = line.geometry
  const index = geometry.index
  const count = index ? index.count : geometry.getAttribute('position').count
  const at = (k: number) => (index ? index.getX(k) : k)
  const out: number[] = []
  if ((line as THREE.LineSegments).isLineSegments) {
    for (let k = 0; k + 1 < count; k += 2) out.push(at(k), at(k + 1))
  } else {
    for (let k = 0; k + 1 < count; k++) out.push(at(k), at(k + 1))
    if ((line as THREE.LineLoop).isLineLoop && count > 2) out.push(at(count - 1), at(0))
  }
  return out
}

/**
 * Batch every shown mesh and edge set under `roots` into a group added to
 * `parent`. Returns null when there is nothing worth batching.
 */
export function buildStaticBatch(
  roots: Iterable<THREE.Object3D | null | undefined>,
  parent: THREE.Object3D,
): StaticBatch | null {
  parent.updateMatrixWorld(true)
  const toParent = parent.matrixWorld.clone().invert()
  const meshGroups = new Map<string, THREE.Mesh[]>()
  const edgeGroups = new Map<string, THREE.Line[]>()
  for (const root of roots) {
    if (!root || root.name === 'meshPreview' || !root.visible) continue
    root.traverse((o) => {
      if (isHelper(o) || !shown(o, root.parent)) return
      if (batchableMesh(o)) {
        const g = o.geometry
        const layout = Object.keys(g.attributes).sort().map((name) => {
          const a = g.getAttribute(name)
          return `${name}:${a.itemSize}:${a.normalized}:${(a.array as ArrayLike<number>).constructor.name}`
        }).join(',')
        const material = o.material as THREE.Material
        const look = materialLook(material)
        const key = [look ? JSON.stringify(look) : material.uuid, layout, !!g.index, o.castShadow, o.receiveShadow,
          o.renderOrder].join('|')
        let list = meshGroups.get(key)
        if (!list) meshGroups.set(key, (list = []))
        list.push(o)
      } else if (batchableEdge(o)) {
        const key = edgeLook(o.material as THREE.LineBasicMaterial).join('/')
        let list = edgeGroups.get(key)
        if (!list) edgeGroups.set(key, (list = []))
        list.push(o)
      }
    })
  }

  const group = new THREE.Group()
  group.name = 'staticBatch'
  group.userData.isStaticBatch = true
  const meshEntries: MeshEntry[] = []
  const edgeEntries: EdgeEntry[] = []
  const matrix = new THREE.Matrix4()
  let draws = 0

  for (const list of meshGroups.values()) {
    // A batch costs a draw and its own buffers; a lone mesh gains nothing.
    if (list.length < 2) continue
    const geometries = new Map<THREE.BufferGeometry, number>()
    let vertices = 0
    let indices = 0
    for (const mesh of list) {
      if (geometries.has(mesh.geometry)) continue
      geometries.set(mesh.geometry, -1)
      vertices += mesh.geometry.getAttribute('position').count
      indices += mesh.geometry.index ? mesh.geometry.index.count : 0
    }
    const first = list[0]!
    // One shared material draws as itself. Look-alike clones draw with a copy
    // of the first: a clone is its mesh's own and goes when the highlight does.
    const shared = list.every((mesh) => mesh.material === first.material)
    const source = first.material as THREE.Material
    const material = shared ? source : source.clone()
    const look = shared ? null : materialLook(source)
    const batch = new THREE.BatchedMesh(list.length, vertices, Math.max(indices, 1), material)
    if (!shared) batch.userData.ownsMaterial = true
    batch.name = 'staticBatchMeshes'
    batch.renderOrder = first.renderOrder
    // transparent instances back to front, as the objects themselves would be
    batch.sortObjects = source.transparent
    batch.frustumCulled = false
    // Not per instance either: that tests every instance's sphere each frame (Alibaba-hongchou: 19,111, a tenth of
    // the frame), while drawing the few off-screen ones costs the GPU nothing measurable.
    batch.perObjectFrustumCulled = false
    batch.castShadow = first.castShadow
    batch.receiveShadow = first.receiveShadow
    batch.raycast = () => {}
    for (const geometry of geometries.keys()) geometries.set(geometry, batch.addGeometry(geometry))
    for (const mesh of list) {
      const id = batch.addInstance(geometries.get(mesh.geometry)!)
      batch.setMatrixAt(id, matrix.multiplyMatrices(toParent, mesh.matrixWorld))
      meshEntries.push({ object: mesh, material: mesh.material as THREE.Material, look, batch, id, local: freeze(mesh),
        parent: mesh.parent, dirty: false, full: false,
        print: look ? materialPrint(mesh.material as THREE.Material, new Float64Array(PRINT)) : null,
        world: [...mesh.matrixWorld.elements], batched: true })
      cull(mesh)
    }
    group.add(batch)
    draws += 1
  }

  const v = new THREE.Vector3()
  for (const list of edgeGroups.values()) {
    if (list.length < 2) continue
    const look = edgeLook(list[0]!.material as THREE.LineBasicMaterial)
    const pairs = list.map(segmentIndices)
    const total = pairs.reduce((n, p) => n + p.length, 0)
    const array = new Float32Array(total * 3)
    const positions = new THREE.BufferAttribute(array, 3)
    let base = 0
    list.forEach((line, k) => {
      const source = line.geometry.getAttribute('position')
      matrix.multiplyMatrices(toParent, line.matrixWorld)
      for (const index of pairs[k]!) {
        v.fromBufferAttribute(source, index).applyMatrix4(matrix)
        array[base * 3] = v.x; array[base * 3 + 1] = v.y; array[base * 3 + 2] = v.z
        base += 1
      }
      edgeEntries.push({
        object: line, material: line.material as THREE.LineBasicMaterial, look, positions,
        start: base - pairs[k]!.length, count: pairs[k]!.length, world: [...line.matrixWorld.elements], batched: true,
        local: freeze(line), parent: line.parent, dirty: false, full: false,
      })
      cull(line)
    })
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', positions)
    const edges = new THREE.LineSegments(geometry, (list[0]!.material as THREE.LineBasicMaterial).clone())
    edges.name = 'staticBatchEdges'
    edges.frustumCulled = false
    edges.raycast = () => {}
    group.add(edges)
    draws += 1
  }

  if (!meshEntries.length && !edgeEntries.length) return null
  parent.add(group)

  /** An edge set leaves the batch for good: its copy collapses to zero-length segments. */
  const release = (entry: EdgeEntry) => {
    const a = entry.positions.array as Float32Array
    const s = entry.start * 3
    for (let k = 1; k < entry.count; k++) {
      a[s + k * 3] = a[s]!; a[s + k * 3 + 1] = a[s + 1]!; a[s + k * 3 + 2] = a[s + 2]!
    }
    entry.positions.needsUpdate = true
    entry.batched = false
    if (entry.local) { thaw(entry.object); entry.local = null }
    uncull(entry.object)
  }

  let disposed = false
  const parentWorlds = new Map<THREE.Object3D, number[]>()
  const parentLive = new Map<THREE.Object3D, boolean>()
  const byParent = new Map<THREE.Object3D, { meshes: MeshEntry[]; edges: EdgeEntry[] }>()
  const lookEntries = meshEntries.filter((e) => e.look)
  const dirtyMeshes: MeshEntry[] = []
  const dirtyEdges: EdgeEntry[] = []
  const mark = (e: MeshEntry, full = false): void => {
    if (full) e.full = true
    if (!e.dirty) { e.dirty = true; dirtyMeshes.push(e) }
  }
  const markEdge = (e: EdgeEntry, full = false): void => {
    if (full) e.full = true
    if (!e.dirty) { e.dirty = true; dirtyEdges.push(e) }
  }
  const kidsOf = (p: THREE.Object3D) => {
    let kids = byParent.get(p)
    if (!kids) byParent.set(p, (kids = { meshes: [], edges: [] }))
    return kids
  }
  for (const e of meshEntries) if (e.parent) kidsOf(e.parent).meshes.push(e)
  for (const e of edgeEntries) if (e.parent) kidsOf(e.parent).edges.push(e)
  for (const p of byParent.keys()) parentWorlds.set(p, [...p.matrixWorld.elements])
  // Nothing is installed on the members: an accessor on an instance turns it into a slow (dictionary-mode) object,
  // and three then read its matrices and flags slower on every frame. A swapped material or a changed shadow flag is
  // found by three plain comparisons per member and frame; an own transform or an in-place look by the turn.
  // batchedRenderPass: hide the members drawn by the batch for the render, then put their visibility back
  let hidden: THREE.Object3D[] = []
  const pass = {
    hide() {
      hidden = []
      for (const e of meshEntries) if (e.batched && e.object.visible) { e.object.visible = false; hidden.push(e.object) }
      for (const e of edgeEntries) if (e.batched && e.object.visible) { e.object.visible = false; hidden.push(e.object) }
    },
    show() {
      for (const o of hidden) o.visible = true
      hidden = []
    },
  }
  activeBatches.add(pass)
  const scratchLook: unknown[] = []
  const scratchPrint = new Float64Array(PRINT)
  let frame = 0
  /** The full look of a look-alike member: when its print moved, and once in FULL_LOOK_EVERY frames for a change
   * the print does not see (a roughness, a map), which then shows a few frames late. The members take turns (one
   * in FULL_LOOK_EVERY each frame): all at once made every 30th frame 50 ms longer on Alibaba-hongchou. */
  const lookStill = (entry: MeshEntry, full: boolean): boolean => {
    const now = materialPrint(entry.material, scratchPrint)
    if (!full && samePrint(entry.print!, now)) return true
    entry.print!.set(now)
    return materialLookInto(entry.material, scratchLook) && sameValues(entry.look!, scratchLook)
  }
  return {
    group,
    get meshes() { return meshEntries.reduce((n, e) => n + (e.batched ? 1 : 0), 0) },
    get releasedMeshes() { return meshEntries.reduce((n, e) => n + (e.batched ? 0 : 1), 0) },
    get edges() { return edgeEntries.reduce((n, e) => n + (e.batched ? 1 : 0), 0) },
    draws,
    sync() {
      if (disposed) return
      // Per frame: each parent once (shown and in the scene, moved), the members whose watched properties were set
      // (material, visible, shadow flags), the look-alike members' prints, and one member in FULL_LOOK_EVERY in
      // full (its own transform, its full look): Alibaba-hongchou's 19,111 members all checked every frame were a
      // quarter of the frame.
      const phase = frame++ % FULL_LOOK_EVERY
      const liveMemo = new Map<THREE.Object3D, boolean>()
      const liveOf = (o: THREE.Object3D): boolean => {
        if (!o.visible) return false
        const p = o.parent
        if (!p) return (o as THREE.Scene).isScene === true
        let hit = liveMemo.get(p)
        if (hit === undefined) liveMemo.set(p, (hit = liveOf(p)))
        return hit
      }
      const moved = new Map<THREE.Object3D, boolean>()
      const parentMoved = (p: THREE.Object3D): boolean => {
        let hit = moved.get(p)
        if (hit === undefined) {
          const seen = parentWorlds.get(p)
          hit = !seen || !sameMatrix(seen, p.matrixWorld)
          if (hit) parentWorlds.set(p, [...p.matrixWorld.elements])
          moved.set(p, hit)
        }
        return hit
      }
      const follow = (o: THREE.Object3D, local: Float64Array, ownToo: boolean): void => {
        const own = ownToo && !sameLocal(local, o)
        if (own) { o.updateMatrix(); localOf(o, local) }
        if ((own || parentMoved(o.parent!)) && o.parent) o.matrixWorld.multiplyMatrices(o.parent.matrixWorld, o.matrix)
      }
      for (const [parentObject, kids] of byParent) {
        const live = liveOf(parentObject)
        const changed = parentLive.get(parentObject) !== live
        if (changed) parentLive.set(parentObject, live)
        const move = live && parentMoved(parentObject)
        if (!changed && !move) continue
        for (const e of kids.meshes) {
          if (move && e.local) follow(e.object, e.local, false)
          mark(e)
        }
        for (const e of kids.edges) {
          if (move && e.local) follow(e.object, e.local, false)
          markEdge(e)
        }
      }
      for (const e of meshEntries) {
        const o = e.object
        if (o.material !== e.material || o.castShadow !== e.batch.castShadow || o.receiveShadow !== e.batch.receiveShadow) {
          if (e.batched) mark(e)
        } else if (!e.batched) mark(e)          // released: taken back once it matches again
      }
      for (const e of edgeEntries) if (e.batched && e.object.material !== e.material) markEdge(e)
      for (let i = phase; i < meshEntries.length; i += FULL_LOOK_EVERY) mark(meshEntries[i]!, true)
      for (let i = phase; i < edgeEntries.length; i += FULL_LOOK_EVERY) markEdge(edgeEntries[i]!, true)
      for (const e of lookEntries) {
        if (e.batched && !e.dirty && !samePrint(e.print!, materialPrint(e.material, scratchPrint))) mark(e)
      }
      const meshes = dirtyMeshes.splice(0)
      for (const entry of meshes) {
        const full = entry.full
        entry.dirty = false
        entry.full = false
        const o = entry.object
        if (full && entry.local && o.parent) follow(o, entry.local, true)
        // Shadows switched on or off re-mark every mesh: the batch was built
        // with the old flags, so the original draws until the next build.
        const keep = o.material === entry.material && o.castShadow === entry.batch.castShadow
          && o.receiveShadow === entry.batch.receiveShadow && o.parent === entry.parent && liveOf(o)
          && (!entry.look || lookStill(entry, full))
        if (keep && !sameMatrix(entry.world, o.matrixWorld)) {
          entry.batch.setMatrixAt(entry.id, matrix.multiplyMatrices(toParent, o.matrixWorld))
          entry.world = [...o.matrixWorld.elements]
        }
        if (keep === entry.batched) continue
        entry.batched = keep
        entry.batch.setVisibleAt(entry.id, keep)
        if (keep) {
          cull(o)
          entry.local = freeze(o)
        } else {
          uncull(o)
          if (entry.local) { thaw(o); entry.local = null }
        }
      }
      const edgesNow = dirtyEdges.splice(0)
      for (const entry of edgesNow) {
        const full = entry.full
        entry.dirty = false
        entry.full = false
        if (!entry.batched) continue
        const o = entry.object
        if (full && entry.local && o.parent) follow(o, entry.local, true)
        if (o.material !== entry.material || !sameLook(entry.look, entry.material) || o.parent !== entry.parent
            || !liveOf(o) || !sameMatrix(entry.world, o.matrixWorld)) release(entry)
      }
    },
    dispose() {
      if (disposed) return
      disposed = true
      group.parent?.remove(group)
      activeBatches.delete(pass)
      for (const entry of meshEntries) { uncull(entry.object); if (entry.local) thaw(entry.object) }
      for (const entry of edgeEntries) { uncull(entry.object); if (entry.local) thaw(entry.object) }
      group.traverse((o) => {
        if ((o as THREE.BatchedMesh).isBatchedMesh) {
          const batch = o as THREE.BatchedMesh
          batch.dispose()  // its own buffers; the material is the scene's unless copied
          if (batch.userData.ownsMaterial) (batch.material as THREE.Material).dispose()
        } else if ((o as THREE.LineSegments).isLineSegments) {
          const edges = o as THREE.LineSegments
          edges.geometry.dispose()
          ;(edges.material as THREE.Material).dispose()
        }
      })
    },
  }
}
