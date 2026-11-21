/**
 * The mesh tab's 3D annotation layer.
 *
 * Four helper families the legacy editor draws over the model geometry, ported
 * together because they share one lifecycle: they exist only while the mesh tab
 * is open, and `_applyLeftTabUI` (STL_generate.html:23033) removes them on the
 * way out. Drawing them on other tabs would put an orange ball and a blue grid
 * over a geometry screenshot.
 *
 * - **locationInMesh sphere** — the seed point snappyHexMesh keeps the mesh
 *   around. Draggable, and the drag is constrained by the panel's X/Y/Z boxes.
 * - **Extra seed spheres** — one per additional connected fluid region.
 * - **BlockMesh grid preview** — the background grid the base cell size gives,
 *   which is the only way to see what `meshBaseSize` actually costs.
 * - **Refinement zone boxes** — wireframe + six invisible drag planes + a
 *   centre sphere, the searchableBox regions.
 *
 * Ported from `_createLocInMeshSphere` / `toggleBmGridPreview` /
 * `_createRZVisuals` and their drag handlers. Three things are easy to lose:
 *
 * - **Drags mutate live and commit once.** A resize that ran `edit()` per frame
 *   would push sixty undo entries a second; the legacy code pushes ONE
 *   checkpoint on the first movement and logs on release.
 * - **The grid line count is capped.** A 0.01 m base size over a 40 m room is
 *   tens of millions of lines; the legacy refuses over 60000 and so does this.
 * - **Depth testing is off** for the seed spheres. The seed is usually inside
 *   the room, and a sphere hidden behind a wall cannot be dragged.
 */

import * as THREE from 'three'
import type { CfdProject } from '../schema/cfdproj'
import { createMarker, unregisterHelper, type MarkerMaterial } from '../geometry/helperRegistry'
import { meshConfigOf } from './meshConfig'
import * as store from '../projectStore'
import type { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

export interface Bounds3 {
  min: { x: number; y: number; z: number }
  max: { x: number; y: number; z: number }
}

export interface RefinementZoneBox {
  id: string
  name?: string
  visible?: boolean
  min: { x: number; y: number; z: number }
  max: { x: number; y: number; z: number }
}

export interface ExtraSeed {
  id: number
  x: number
  y: number
  z: number
  color: string
  visible?: boolean
}

/** Face order, shared with the legacy `BM_FACE_INFO` (STL_generate.html:26729). */
export const FACE_DEFS: { name: string; axis: 'x' | 'y' | 'z'; isMin: boolean }[] = [
  { name: '-X', axis: 'x', isMin: true },
  { name: '+X', axis: 'x', isMin: false },
  { name: '-Y', axis: 'y', isMin: true },
  { name: '+Y', axis: 'y', isMin: false },
  { name: '-Z', axis: 'z', isMin: true },
  { name: '+Z', axis: 'z', isMin: false },
]

export const RZ_COLOR = 0x00bcd4
export const RZ_HOVER_COLOR = 0xffd700
export const RZ_DRAG_COLOR = 0xff8c00

/** Above this the preview is refused rather than drawn — see the header note. */
export const MAX_GRID_LINES = 60000

export const EXTRA_LOC_COLORS = [
  '#00bfff', '#ff4081', '#69f0ae', '#ffab40',
  '#7c4dff', '#ffd740', '#18ffff', '#ff6e40',
  '#b388ff', '#64ffda', '#ff80ab', '#8c9eff',
]

/** The next unused colour, so two seeds are never the same shade. */
export function pickSeedColor(existing: ExtraSeed[]): string {
  const used = existing.map((e) => e.color)
  const free = EXTRA_LOC_COLORS.find((c) => !used.includes(c))
  return free ?? EXTRA_LOC_COLORS[existing.length % EXTRA_LOC_COLORS.length]!
}

/** The monospace bounds line under the BlockMesh heading. */
export function formatBounds(b: Bounds3): string {
  const f = (v: number) => v.toFixed(4)
  return (
    `X: [${f(b.min.x)}, ${f(b.max.x)}]  ` +
    `Y: [${f(b.min.y)}, ${f(b.max.y)}]  ` +
    `Z: [${f(b.min.z)}, ${f(b.max.z)}]`
  )
}

export interface GridPlan {
  nx: number
  ny: number
  nz: number
  /** Cell size actually used per axis — the extent divided by a whole count. */
  sx: number
  sy: number
  sz: number
  lines: number
}

/**
 * How many cells the base size gives across the block, per axis.
 *
 * The count is rounded, then the SPACING is recomputed from it, so the grid
 * lands exactly on both faces. Taking `baseSize` as the spacing instead would
 * leave a ragged partial cell at the far face — which is not what blockMesh
 * does, and the preview would be lying about the mesh it stands for.
 */
export function planGrid(b: Bounds3, baseSize: number): GridPlan {
  const s = Math.max(Number(baseSize), 0.01)
  const xExt = b.max.x - b.min.x
  const yExt = b.max.y - b.min.y
  const zExt = b.max.z - b.min.z
  const nx = Math.max(1, Math.round(xExt / s))
  const ny = Math.max(1, Math.round(yExt / s))
  const nz = Math.max(1, Math.round(zExt / s))
  const lines = (nx + 1) * (ny + 1) + (ny + 1) * (nz + 1) + (nx + 1) * (nz + 1)
  return { nx, ny, nz, sx: xExt / nx, sy: yExt / ny, sz: zExt / nz, lines }
}

/** Line-segment endpoints for the grid, in the legacy's axis order. */
export function gridVertices(b: Bounds3, plan: GridPlan): number[] {
  const v: number[] = []
  const { nx, ny, nz, sx, sy, sz } = plan
  for (let iy = 0; iy <= ny; iy++)
    for (let iz = 0; iz <= nz; iz++) {
      const y = b.min.y + iy * sy
      const z = b.min.z + iz * sz
      v.push(b.min.x, y, z, b.min.x + nx * sx, y, z)
    }
  for (let ix = 0; ix <= nx; ix++)
    for (let iz = 0; iz <= nz; iz++) {
      const x = b.min.x + ix * sx
      const z = b.min.z + iz * sz
      v.push(x, b.min.y, z, x, b.min.y + ny * sy, z)
    }
  for (let ix = 0; ix <= nx; ix++)
    for (let iy = 0; iy <= ny; iy++) {
      const x = b.min.x + ix * sx
      const y = b.min.y + iy * sy
      v.push(x, y, b.min.z, x, y, b.min.z + nz * sz)
    }
  return v
}

/**
 * A face drag applied to a box, with the legacy's 0.01 m minimum thickness.
 *
 * Pure so the clamp can be tested: dragging the -X face past +X must stop at a
 * 1 cm box, not invert it into a negative-volume searchableBox the runner would
 * write out as-is.
 */
export function resizeBox(
  start: Bounds3,
  faceIdx: number,
  delta: number,
): Bounds3 {
  const fd = FACE_DEFS[faceIdx]!
  const out: Bounds3 = { min: { ...start.min }, max: { ...start.max } }
  const a = fd.axis
  if (fd.isMin) {
    out.min[a] = start.min[a] + delta
    if (out.min[a] > out.max[a] - 0.01) out.min[a] = out.max[a] - 0.01
  } else {
    out.max[a] = start.max[a] + delta
    if (out.max[a] < out.min[a] + 0.01) out.max[a] = out.min[a] + 0.01
  }
  return out
}

/** A whole-box move. */
export function moveBox(start: Bounds3, d: { x: number; y: number; z: number }): Bounds3 {
  return {
    min: { x: start.min.x + d.x, y: start.min.y + d.y, z: start.min.z + d.z },
    max: { x: start.max.x + d.x, y: start.max.y + d.y, z: start.max.z + d.z },
  }
}

/**
 * Where a constrained seed drag lands.
 *
 * The panel's three checkboxes are a per-axis mask: an unchecked axis keeps its
 * value from the drag START, not from the current point, so releasing X mid-drag
 * cannot smear the seed sideways. Rounded to 1 cm, as the legacy does, because
 * the seed is a hand-placed point and 15 decimals in the .cfdproj are noise.
 */
export function constrainSeed(
  start: { x: number; y: number; z: number },
  target: { x: number; y: number; z: number },
  axes: { x: boolean; y: boolean; z: boolean },
): { x: number; y: number; z: number } {
  const r = (v: number) => Math.round(v * 100) / 100
  return {
    x: axes.x ? r(target.x) : start.x,
    y: axes.y ? r(target.y) : start.y,
    z: axes.z ? r(target.z) : start.z,
  }
}

export interface MeshVisualsHost {
  camera: THREE.Camera
  domElement: HTMLElement
  /** Identity-transform parent, so local coordinates ARE world coordinates. */
  globalGroup: THREE.Group
  orbit: OrbitControls | null
  project(): CfdProject | null
  /** The block bounds, from the shared cfdrun builder. */
  bounds(): Bounds3 | null
  /** Which left tab is open — the helpers only exist on `mesh`. */
  activeTab(): string
  /** The panel's drag-axis checkboxes. */
  dragAxes(): { x: boolean; y: boolean; z: boolean }
  requestRender(): void
  /** Told when a drag changed the project, so the panel's boxes follow. */
  onChange(): void
}

export interface MeshVisuals {
  /** Re-read the project and rebuild what changed. */
  refresh(): void
  toggleGrid(): { shown: boolean; plan?: GridPlan; refused?: number }
  gridShown(): boolean
  dragging(): boolean
  dispose(): void
}

interface ZoneVisual {
  wireframe: THREE.LineSegments
  facePlanes: THREE.Mesh[]
  centerSphere: THREE.Mesh
}

export function createMeshVisuals(host: MeshVisualsHost): MeshVisuals {
  const group = new THREE.Group()
  group.name = 'meshVisuals'
  host.globalGroup.add(group)

  let seedSphere: THREE.Mesh<THREE.BufferGeometry, MarkerMaterial> | null = null
  let extraSpheres: { id: number; mesh: THREE.Mesh<THREE.BufferGeometry, MarkerMaterial> }[] = []
  let grid: THREE.LineSegments | null = null
  const zones = new Map<string, ZoneVisual>()
  let hoveredZone: string | null = null
  let hoveredFace = -1

  type Drag =
    | { kind: 'seed'; pointerId: number; plane: THREE.Plane; start: { x: number; y: number; z: number }; pushed: boolean }
    | { kind: 'extra'; pointerId: number; id: number; plane: THREE.Plane; start: { x: number; y: number; z: number }; pushed: boolean }
    | {
        kind: 'zone'
        pointerId: number
        zoneId: string
        type: 'center' | 'face'
        faceIdx: number
        plane: THREE.Plane
        startWorld: THREE.Vector3
        startBounds: Bounds3
        pushed: boolean
      }
  let drag: Drag | null = null

  const meshCfg = () => meshConfigOf(host.project())
  const simCfg = () =>
    ((host.project() as { runDefaults?: { simulationConfig?: Record<string, unknown> } } | null)
      ?.runDefaults?.simulationConfig ?? {}) as Record<string, unknown>
  const seeds = (): ExtraSeed[] => (simCfg().extraLocations as ExtraSeed[] | undefined) ?? []
  const zoneList = (): RefinementZoneBox[] =>
    (meshCfg().refinementZones as unknown as RefinementZoneBox[] | undefined) ?? []
  const onMeshTab = () => host.activeTab() === 'mesh'

  // --- picking helpers ----------------------------------------------------
  const ndc = new THREE.Vector2()
  const ray = new THREE.Raycaster()
  const castFrom = (e: PointerEvent) => {
    const rect = host.domElement.getBoundingClientRect()
    ndc.set(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    )
    ray.setFromCamera(ndc, host.camera)
    return ray
  }
  const cameraPlaneAt = (point: THREE.Vector3) => {
    const dir = new THREE.Vector3()
    host.camera.getWorldDirection(dir)
    return new THREE.Plane().setFromNormalAndCoplanarPoint(dir.clone().negate(), point)
  }

  // --- seed sphere --------------------------------------------------------
  function ensureSeed() {
    if (seedSphere) return seedSphere
    // A point marker (geometry_pure.js): the shared look, held at a constant
    // size on screen by the render loop. Raycasts read the scale, so the drag
    // target stays exactly as large as the ball that is drawn.
    seedSphere = createMarker(0xff8c00, 'seed')
    seedSphere.renderOrder = 1000
    seedSphere.userData = { isLocInMesh: true }
    group.add(seedSphere)
    return seedSphere
  }

  function syncSeed() {
    const cfg = meshCfg()
    const loc = cfg.locationInMesh as { x: number; y: number; z: number } | undefined
    const s = ensureSeed()
    if (loc) s.position.set(Number(loc.x) || 0, Number(loc.y) || 0, Number(loc.z) || 0)
    s.visible = cfg.locInMeshVisible !== false && onMeshTab()
  }

  function syncExtras() {
    const list = seeds()
    const wanted = new Set(list.map((e) => e.id))
    extraSpheres = extraSpheres.filter((item) => {
      if (wanted.has(item.id)) return true
      unregisterHelper(item.mesh)
      group.remove(item.mesh)
      item.mesh.geometry.dispose()
      item.mesh.material.dispose()
      return false
    })
    const visible = meshCfg().locInMeshVisible !== false && onMeshTab()
    list.forEach((entry) => {
      let item = extraSpheres.find((i) => i.id === entry.id)
      if (!item) {
        const mesh = createMarker(entry.color || '#00bfff', 'extraSeed')
        mesh.renderOrder = 1000
        mesh.userData = { isExtraLocInMesh: true, extraLocId: entry.id }
        group.add(mesh)
        item = { id: entry.id, mesh }
        extraSpheres.push(item)
      }
      item.mesh.position.set(Number(entry.x) || 0, Number(entry.y) || 0, Number(entry.z) || 0)
      item.mesh.material.color.set(entry.color || '#00bfff')
      item.mesh.visible = visible && entry.visible !== false
    })
  }

  // --- refinement zones ---------------------------------------------------
  function ensureZone(id: string): ZoneVisual {
    const found = zones.get(id)
    if (found) return found
    const edges = new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1))
    const wireframe = new THREE.LineSegments(
      edges,
      new THREE.LineBasicMaterial({ color: RZ_COLOR, depthTest: false, transparent: true, opacity: 0.8 }),
    )
    wireframe.renderOrder = 10
    wireframe.userData = { isRZone: true, rzId: id }
    group.add(wireframe)

    const facePlanes: THREE.Mesh[] = []
    for (let i = 0; i < 6; i++) {
      const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(1, 1),
        new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide }),
      )
      plane.userData = { isRZPlane: true, rzId: id, faceIdx: i }
      group.add(plane)
      facePlanes.push(plane)
    }

    const centerSphere = createMarker(RZ_COLOR, 'zoneCentre')
    centerSphere.renderOrder = 11
    centerSphere.userData = { isRZCenter: true, rzId: id }
    group.add(centerSphere)

    const vis: ZoneVisual = { wireframe, facePlanes, centerSphere }
    zones.set(id, vis)
    return vis
  }

  function dropZone(id: string) {
    const vis = zones.get(id)
    if (!vis) return
    group.remove(vis.wireframe)
    vis.wireframe.geometry.dispose()
    ;(vis.wireframe.material as THREE.Material).dispose()
    vis.facePlanes.forEach((p) => {
      group.remove(p)
      p.geometry.dispose()
      ;(p.material as THREE.Material).dispose()
    })
    unregisterHelper(vis.centerSphere)
    group.remove(vis.centerSphere)
    vis.centerSphere.geometry.dispose()
    ;(vis.centerSphere.material as THREE.Material).dispose()
    zones.delete(id)
  }

  function syncZones() {
    const list = onMeshTab() ? zoneList() : []
    const alive = new Set(list.filter((z) => z.visible !== false).map((z) => z.id))
    ;[...zones.keys()].forEach((id) => {
      if (!alive.has(id)) dropZone(id)
    })
    list.forEach((zone) => {
      if (zone.visible === false || !zone.min || !zone.max) return
      const vis = ensureZone(zone.id)
      const n = zone.min
      const x = zone.max
      const c = { x: (n.x + x.x) / 2, y: (n.y + x.y) / 2, z: (n.z + x.z) / 2 }
      const s = {
        x: Math.max(x.x - n.x, 0.001),
        y: Math.max(x.y - n.y, 0.001),
        z: Math.max(x.z - n.z, 0.001),
      }
      vis.wireframe.position.set(c.x, c.y, c.z)
      vis.wireframe.scale.set(s.x, s.y, s.z)
      const dragged = drag?.kind === 'zone' && drag.zoneId === zone.id
      const hovered = hoveredZone === zone.id
      ;(vis.wireframe.material as THREE.LineBasicMaterial).color.setHex(
        dragged ? RZ_DRAG_COLOR : hovered ? RZ_HOVER_COLOR : RZ_COLOR,
      )
      const faces: { center: [number, number, number]; normal: [number, number, number]; w: number; h: number }[] = [
        { center: [n.x, c.y, c.z], normal: [1, 0, 0], w: s.z, h: s.y },
        { center: [x.x, c.y, c.z], normal: [1, 0, 0], w: s.z, h: s.y },
        { center: [c.x, n.y, c.z], normal: [0, 1, 0], w: s.x, h: s.z },
        { center: [c.x, x.y, c.z], normal: [0, 1, 0], w: s.x, h: s.z },
        { center: [c.x, c.y, n.z], normal: [0, 0, 1], w: s.x, h: s.y },
        { center: [c.x, c.y, x.z], normal: [0, 0, 1], w: s.x, h: s.y },
      ]
      vis.facePlanes.forEach((p, i) => {
        const fd = faces[i]!
        p.position.set(fd.center[0], fd.center[1], fd.center[2])
        p.quaternion.setFromUnitVectors(
          new THREE.Vector3(0, 0, 1),
          new THREE.Vector3(fd.normal[0], fd.normal[1], fd.normal[2]),
        )
        p.scale.set(Math.max(fd.w, 0.01), Math.max(fd.h, 0.01), 1)
      })
      vis.centerSphere.position.set(c.x, c.y, c.z)
    })
  }

  // --- grid ---------------------------------------------------------------
  function removeGrid() {
    if (!grid) return
    group.remove(grid)
    grid.geometry.dispose()
    ;(grid.material as THREE.Material).dispose()
    grid = null
  }

  function buildGrid(): { shown: boolean; plan?: GridPlan; refused?: number } {
    const b = host.bounds()
    if (!b) return { shown: false }
    const plan = planGrid(b, Number(meshCfg().meshBaseSize) || 0.1)
    if (plan.lines > MAX_GRID_LINES) return { shown: false, refused: plan.lines }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(gridVertices(b, plan), 3))
    grid = new THREE.LineSegments(
      geo,
      new THREE.LineBasicMaterial({ color: 0x4488ff, opacity: 0.45, transparent: true }),
    )
    grid.userData = { isBMGrid: true }
    grid.renderOrder = 5
    group.add(grid)
    return { shown: true, plan }
  }

  // --- drags --------------------------------------------------------------
  function onPointerDown(e: PointerEvent) {
    if (e.button !== 0 || !onMeshTab()) return
    const rc = castFrom(e)

    if (seedSphere?.visible && rc.intersectObject(seedSphere, false).length) {
      const loc = meshCfg().locationInMesh as { x: number; y: number; z: number }
      drag = {
        kind: 'seed',
        pointerId: e.pointerId,
        plane: cameraPlaneAt(seedSphere.position.clone()),
        start: { x: Number(loc.x), y: Number(loc.y), z: Number(loc.z) },
        pushed: false,
      }
    } else {
      const extraHit = rc.intersectObjects(
        extraSpheres.filter((i) => i.mesh.visible).map((i) => i.mesh),
        false,
      )[0]
      if (extraHit) {
        const id = (extraHit.object.userData as { extraLocId: number }).extraLocId
        const entry = seeds().find((s) => s.id === id)
        if (!entry) return
        drag = {
          kind: 'extra',
          pointerId: e.pointerId,
          id,
          plane: cameraPlaneAt(extraHit.object.position.clone()),
          start: { x: entry.x, y: entry.y, z: entry.z },
          pushed: false,
        }
      } else if (!beginZoneDrag(e, rc)) return
    }
    if (!drag) return
    if (host.orbit) host.orbit.enabled = false
    try {
      ;(host.domElement as HTMLElement & { setPointerCapture(id: number): void }).setPointerCapture(e.pointerId)
    } catch {
      /* jsdom and some browsers refuse capture on a non-focused element */
    }
    e.stopPropagation()
  }

  function beginZoneDrag(e: PointerEvent, rc: THREE.Raycaster): boolean {
    const centers: THREE.Mesh[] = []
    const planes: THREE.Mesh[] = []
    zones.forEach((v) => {
      centers.push(v.centerSphere)
      v.facePlanes.forEach((p) => planes.push(p))
    })
    const centerHit = rc.intersectObjects(centers, false)[0]
    const faceHit = centerHit ? undefined : rc.intersectObjects(planes, false)[0]
    const hitData = (centerHit ?? faceHit)?.object.userData as
      | { rzId: string; faceIdx?: number }
      | undefined
    if (!hitData) return false
    const zone = zoneList().find((z) => z.id === hitData.rzId)
    if (!zone) return false

    const center = new THREE.Vector3(
      (zone.min.x + zone.max.x) / 2,
      (zone.min.y + zone.max.y) / 2,
      (zone.min.z + zone.max.z) / 2,
    )
    const camDir = new THREE.Vector3()
    host.camera.getWorldDirection(camDir)
    const plane = new THREE.Plane()
    const isCenter = !!centerHit
    if (isCenter) {
      plane.setFromNormalAndCoplanarPoint(camDir, center)
    } else {
      // A face slides along ONE axis, so the drag plane must contain that axis;
      // a plain camera-facing plane would let a side-on view move the face by
      // the full mouse travel and the box would jump.
      const fd = FACE_DEFS[hitData.faceIdx ?? 0]!
      const normal = new THREE.Vector3()
      normal[fd.axis] = 1
      const cross = new THREE.Vector3().crossVectors(normal, camDir)
      const planeNormal = new THREE.Vector3().crossVectors(cross, normal).normalize()
      if (planeNormal.lengthSq() < 1e-6) planeNormal.copy(camDir)
      const faceCenter = center.clone()
      faceCenter[fd.axis] = fd.isMin ? zone.min[fd.axis] : zone.max[fd.axis]
      plane.setFromNormalAndCoplanarPoint(planeNormal, faceCenter)
    }
    const startWorld = new THREE.Vector3()
    if (!rc.ray.intersectPlane(plane, startWorld)) return false
    drag = {
      kind: 'zone',
      pointerId: e.pointerId,
      zoneId: zone.id,
      type: isCenter ? 'center' : 'face',
      faceIdx: hitData.faceIdx ?? 0,
      plane,
      startWorld,
      startBounds: { min: { ...zone.min }, max: { ...zone.max } },
      pushed: false,
    }
    return true
  }

  function onPointerMove(e: PointerEvent) {
    if (!drag) {
      hoverCheck(e)
      return
    }
    if (e.pointerId !== drag.pointerId) return
    const rc = castFrom(e)
    const hit = new THREE.Vector3()
    if (!rc.ray.intersectPlane(drag.plane, hit)) return

    if (drag.kind === 'seed') {
      if (!drag.pushed) {
        store.pushUndoCheckpoint('dragLocInMesh')
        drag.pushed = true
      }
      const next = constrainSeed(drag.start, hit, host.dragAxes())
      store.mutateLive((p) => {
        const cfg = (p as { runDefaults?: { meshConfig?: Record<string, unknown> } }).runDefaults
          ?.meshConfig
        if (cfg) cfg.locationInMesh = next
      }, { scene: false })
      seedSphere?.position.set(next.x, next.y, next.z)
    } else if (drag.kind === 'extra') {
      if (!drag.pushed) {
        store.pushUndoCheckpoint('dragExtraLoc')
        drag.pushed = true
      }
      const next = constrainSeed(drag.start, hit, host.dragAxes())
      const id = drag.id
      store.mutateLive((p) => {
        const sim = (p as { runDefaults?: { simulationConfig?: { extraLocations?: ExtraSeed[] } } })
          .runDefaults?.simulationConfig
        const entry = sim?.extraLocations?.find((s) => s.id === id)
        if (entry) Object.assign(entry, next)
      }, { scene: false })
      const item = extraSpheres.find((i) => i.id === id)
      item?.mesh.position.set(next.x, next.y, next.z)
    } else {
      const delta = hit.clone().sub(drag.startWorld)
      if (!drag.pushed && delta.length() > 0.001) {
        store.pushUndoCheckpoint('dragRefinementZone')
        drag.pushed = true
      }
      const next =
        drag.type === 'center'
          ? moveBox(drag.startBounds, delta)
          : resizeBox(drag.startBounds, drag.faceIdx, delta[FACE_DEFS[drag.faceIdx]!.axis])
      const id = drag.zoneId
      store.mutateLive((p) => {
        const cfg = (p as { runDefaults?: { meshConfig?: { refinementZones?: RefinementZoneBox[] } } })
          .runDefaults?.meshConfig
        const zone = cfg?.refinementZones?.find((z) => z.id === id)
        if (zone) {
          zone.min = next.min
          zone.max = next.max
        }
      }, { scene: false })
      syncZones()
    }
    host.onChange()
    host.requestRender()
  }

  function hoverCheck(e: PointerEvent) {
    if (!onMeshTab() || !zones.size) return
    const rc = castFrom(e)
    const centers: THREE.Mesh[] = []
    const planes: THREE.Mesh[] = []
    zones.forEach((v) => {
      centers.push(v.centerSphere)
      v.facePlanes.forEach((p) => planes.push(p))
    })
    const centerHit = rc.intersectObjects(centers, false)[0]
    const faceHit = centerHit ? undefined : rc.intersectObjects(planes, false)[0]
    const data = (centerHit ?? faceHit)?.object.userData as { rzId?: string; faceIdx?: number } | undefined
    const nextZone = data?.rzId ?? null
    const nextFace = centerHit ? -2 : (data?.faceIdx ?? -1)
    if (nextZone === hoveredZone && nextFace === hoveredFace) return
    hoveredZone = nextZone
    hoveredFace = nextFace
    const cursors: Record<string, string> = { x: 'ew-resize', y: 'ns-resize', z: 'row-resize' }
    host.domElement.style.cursor = centerHit
      ? 'grab'
      : faceHit
        ? (cursors[FACE_DEFS[nextFace]?.axis ?? 'x'] ?? '')
        : ''
    syncZones()
    host.requestRender()
  }

  function onPointerUp(e: PointerEvent) {
    if (!drag || e.pointerId !== drag.pointerId) return
    try {
      ;(host.domElement as HTMLElement & { releasePointerCapture(id: number): void }).releasePointerCapture(e.pointerId)
    } catch {
      /* capture may never have been granted */
    }
    if (host.orbit) host.orbit.enabled = true
    if (drag.pushed) {
      // One log line per drag, after the live mutation — the checkpoint above
      // already holds the pre-drag project, so this only marks it dirty.
      store.notifyLiveEdit(
        drag.kind === 'zone' ? 'dragRefinementZone' : drag.kind === 'seed' ? 'dragLocInMesh' : 'dragExtraLoc',
        '',
      )
    }
    drag = null
    host.domElement.style.cursor = ''
    host.onChange()
  }

  host.domElement.addEventListener('pointerdown', onPointerDown)
  host.domElement.addEventListener('pointermove', onPointerMove)
  host.domElement.addEventListener('pointerup', onPointerUp)
  host.domElement.addEventListener('pointercancel', onPointerUp)

  return {
    refresh() {
      syncSeed()
      syncExtras()
      syncZones()
      if (!onMeshTab()) removeGrid()
      else if (grid) {
        // Bounds and base size both change under the panel; rebuild so the
        // preview never shows a grid the current settings would not produce.
        removeGrid()
        buildGrid()
      }
      host.requestRender()
    },
    toggleGrid() {
      if (grid) {
        removeGrid()
        host.requestRender()
        return { shown: false }
      }
      const r = buildGrid()
      host.requestRender()
      return r
    },
    gridShown: () => !!grid,
    dragging: () => !!drag,
    dispose() {
      host.domElement.removeEventListener('pointerdown', onPointerDown)
      host.domElement.removeEventListener('pointermove', onPointerMove)
      host.domElement.removeEventListener('pointerup', onPointerUp)
      host.domElement.removeEventListener('pointercancel', onPointerUp)
      removeGrid()
      ;[...zones.keys()].forEach(dropZone)
      extraSpheres.forEach((i) => {
        unregisterHelper(i.mesh)
        i.mesh.geometry.dispose()
        i.mesh.material.dispose()
      })
      extraSpheres = []
      if (seedSphere) {
        unregisterHelper(seedSphere)
        seedSphere.geometry.dispose()
        seedSphere.material.dispose()
        seedSphere = null
      }
      host.globalGroup.remove(group)
    },
  }
}
