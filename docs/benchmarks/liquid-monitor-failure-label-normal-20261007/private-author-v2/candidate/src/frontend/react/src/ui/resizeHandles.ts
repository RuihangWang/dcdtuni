/**
 * The yellow outline and the four green cubes that make a patch draggable.
 *
 * A port of the legacy overlay (`_createResizeHandles`, STL_generate.html:2255
 * and `_applyResizeHandleVisualStates`, :2387). Three details are behaviour,
 * not decoration:
 *
 * - **It lives on the SCENE, not on the model group.** The handles are drawn
 *   with `depthTest: false` so they stay visible through the body they sit on;
 *   parenting them under the model would put them behind whatever the geometry
 *   builder rebuilt last, and a rebuild happens every frame of a drag.
 *
 * - **The cube size follows the camera.** A fixed size is a speck across a
 *   30 m room and a boulder inside a rack. 1.8% of the distance to the face
 *   keeps it near a constant number of pixels. The rule is the SHARED
 *   `handleCubeSize` (geometry_pure.js): the red anchor ball is sized against
 *   this cube, in this editor and in the legacy one.
 *
 * - **Three states, three colours.** Idle green, hover cyan, dragging orange,
 *   with the cube growing as it goes. Without the hover state there is nothing
 *   to tell a user the edge can be grabbed at all.
 */

import * as THREE from 'three'
import type { EdgeKey } from '../geometry/faceDragMath'
import { geometryPure } from '../legacy/geometryBuilder'

export type HandleState = 'idle' | 'hover' | 'active'

const HANDLE_COLORS: Record<HandleState, number> = {
  idle: 0x00cc44,
  hover: 0x00e5ff,
  active: 0xff8c00,
}
const OUTLINE_COLORS: Record<HandleState, number> = {
  idle: 0xffdd00,
  hover: 0x00e5ff,
  active: 0xff8c00,
}
const HANDLE_SCALE: Record<HandleState, number> = { idle: 1.0, hover: 1.55, active: 1.9 }

export const EDGE_KEYS: EdgeKey[] = ['top', 'bottom', 'left', 'right']

export interface HandleMeta {
  uid: string
  faceId: string
  /** Null for a model-level face resize. */
  patchId: string | null
  /** Set when the patch belongs to a SERVER inside a rack. */
  equipmentId?: string | null
}

export interface RectHandleSpec extends HandleMeta {
  /** World-space quad, counter-clockwise from bottom-left. */
  corners: THREE.Vector3[]
  /**
   * A rectangle drawn on a CURVED face (the wall-mounted unit's front): the
   * outline follows the surface and the grab cubes sit on it, so both come
   * sampled from the surface instead of derived from the four corners.
   */
  outline?: THREE.Vector3[]
  edgeCentres?: Record<EdgeKey, THREE.Vector3>
  /**
   * Blue corner cubes (world space, same order as `corners`): dragging one
   * moves that corner alone, so a sheet or a patch becomes a trapezoid.
   */
  cornerHandles?: THREE.Vector3[]
}

/**
 * A sheet drawn as an arbitrary polygon (a floor or ceiling from the 2D room
 * editor): the outline runs through every vertex, and there is a blue cube on
 * each vertex and a green one on each edge's midpoint. The bounding
 * rectangle's four edges and corners mean nothing on a triangle.
 */
export interface PolygonHandleSpec extends HandleMeta {
  /** World-space vertices, in the polygon's own order. */
  vertices: THREE.Vector3[]
  /** World-space midpoint of edge i (vertex i -> i+1). */
  edgeMids: THREE.Vector3[]
}

/**
 * What `setStates` lights: a rectangle edge, a circle's ring, or one of a
 * polygon's handles (`pv:<i>` a vertex, `pe:<i>` an edge).
 */
export type HandleKey = EdgeKey | 'circle' | `pv:${number}` | `pe:${number}`

export interface CircleHandleSpec extends HandleMeta {
  centre: THREE.Vector3
  ring: THREE.Vector3[]
  dots: THREE.Vector3[]
}

interface ActiveHandles extends HandleMeta {
  group: THREE.Group
  outline: THREE.Line
  cubes: Partial<Record<EdgeKey, THREE.Mesh>>
  dots: THREE.Mesh[]
  cornerCubes: THREE.Mesh[]
  polyVertexCubes: THREE.Mesh[]
  polyEdgeCubes: THREE.Mesh[]
  isCircle: boolean
  isPolygon: boolean
}

/** The corner cubes' colour: blue, so they read apart from the green edges. */
export const CORNER_HANDLE_COLOR = 0x3a8dff

export interface ResizeHandles {
  showRect(spec: RectHandleSpec): void
  showCircle(spec: CircleHandleSpec): void
  showPolygon(spec: PolygonHandleSpec): void
  hide(): void
  /**
   * Re-size the drawn cubes for where the camera is NOW.
   *
   * The cubes are sized in WORLD metres (1.8% of the camera distance), so the
   * size decided when the overlay was BUILT goes stale the moment the user
   * zooms: select a floor from across the room, roll in, and its cubes are
   * still room-scale — a green boulder over the thing being edited. Called once
   * per frame from the render loop. It costs one `distanceTo` per cube and
   * writes nothing when no size changed, so an idle camera is free and a zoom
   * is eight `scale.setScalar` calls.
   */
  updateScale(): void
  /** The meshes a pointer can grab. Empty when nothing is shown. */
  pickables(): THREE.Object3D[]
  /** Move the drawn handles to a new quad, mid-drag, without rebuilding them. */
  moveRect(
    corners: THREE.Vector3[],
    outline?: THREE.Vector3[],
    edgeCentres?: Record<EdgeKey, THREE.Vector3>,
    cornerHandles?: THREE.Vector3[],
  ): void
  moveCircle(ring: THREE.Vector3[], dots: THREE.Vector3[]): void
  /**
   * Move a polygon's drawn handles mid-drag. The vertex count is fixed during
   * a drag, so the cubes and the outline are moved, not rebuilt.
   */
  movePolygon(vertices: THREE.Vector3[], edgeMids: THREE.Vector3[]): void
  /** What is currently shown, so a caller can tell whether to rebuild it. */
  current(): (HandleMeta & { isCircle: boolean; isPolygon: boolean }) | null
  setStates(hover: HandleKey | null, active: HandleKey | null): void
  dispose(): void
}

function cubeSize(camera: THREE.Camera, at: THREE.Vector3): number {
  return geometryPure().handleCubeSize(camera.position.distanceTo(at))
}

/** A unit cube: the DRAWN size comes from `scale`, so a zoom re-sizes it. */
function unitCubeGeometry(): THREE.BoxGeometry {
  return new THREE.BoxGeometry(1, 1, 1)
}

export function createResizeHandles(scene: THREE.Scene, camera: THREE.Camera): ResizeHandles {
  const root = new THREE.Group()
  root.name = '_resizeHandles'
  root.renderOrder = 10
  scene.add(root)

  let active: ActiveHandles | null = null

  /** Every mesh the overlay draws and a pointer can grab, without a new array. */
  function eachMesh(fn: (mesh: THREE.Mesh) => void) {
    if (!active) return
    for (const edge of EDGE_KEYS) {
      const cube = active.cubes[edge]
      if (cube) fn(cube)
    }
    for (const dot of active.dots) fn(dot)
    for (const corner of active.cornerCubes) fn(corner)
    for (const vertex of active.polyVertexCubes) fn(vertex)
    for (const edge of active.polyEdgeCubes) fn(edge)
  }

  /**
   * The side a mesh is drawn at, from ITS OWN distance to the camera.
   *
   * Per mesh, not per face: one size for the whole overlay is measured at the
   * face centre, and on a 9 m floor seen from 3 m the cubes at the near edge are
   * then metres from that centre — measured 85 px across, straight back to
   * covering the thing being edited. Per mesh, every cube is the same size ON
   * SCREEN wherever it sits, which is also what keeps the red anchor ball and
   * the blue corner cube that share a point at their agreed 0.9 ratio.
   */
  function sizeOf(mesh: THREE.Mesh): number {
    // The group hangs off the scene with an identity transform, so a mesh's
    // local position IS its world one.
    return cubeSize(camera, mesh.position)
  }

  function updateScale() {
    if (!active) return
    let changed = false
    eachMesh((mesh) => {
      const size = sizeOf(mesh)
      if (Math.abs(size - Number(mesh.userData.cubeSize)) < 1e-6) return
      mesh.userData.cubeSize = size
      mesh.scale.setScalar(size * (Number(mesh.userData.stateScale) || 1))
      changed = true
    })
    if (changed) active.group.updateMatrixWorld(true)
  }

  function clear() {
    if (!active) return
    active.group.traverse((child) => {
      const withGeometry = child as THREE.Mesh
      withGeometry.geometry?.dispose()
      const material = withGeometry.material
      if (Array.isArray(material)) material.forEach((m) => m.dispose())
      else material?.dispose()
    })
    active.group.parent?.remove(active.group)
    active = null
  }

  function edgeCentresOf(corners: THREE.Vector3[]): Record<EdgeKey, THREE.Vector3> {
    return {
      bottom: new THREE.Vector3().addVectors(corners[0]!, corners[1]!).multiplyScalar(0.5),
      right: new THREE.Vector3().addVectors(corners[1]!, corners[2]!).multiplyScalar(0.5),
      top: new THREE.Vector3().addVectors(corners[2]!, corners[3]!).multiplyScalar(0.5),
      left: new THREE.Vector3().addVectors(corners[3]!, corners[0]!).multiplyScalar(0.5),
    }
  }

  function showRect(spec: RectHandleSpec) {
    clear()
    const group = new THREE.Group()
    root.add(group)

    const outlinePts = spec.outline ?? spec.corners
    const outline = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([...outlinePts, outlinePts[0]!]),
      new THREE.LineBasicMaterial({
        color: OUTLINE_COLORS.idle,
        depthTest: false,
        transparent: true,
      }),
    )
    outline.renderOrder = 10
    group.add(outline)

    const centres = spec.edgeCentres ?? edgeCentresOf(spec.corners)
    const cubes: Partial<Record<EdgeKey, THREE.Mesh>> = {}
    for (const edge of EDGE_KEYS) {
      const mesh = new THREE.Mesh(
        unitCubeGeometry(),
        new THREE.MeshBasicMaterial({
          color: HANDLE_COLORS.idle,
          depthTest: false,
          transparent: true,
        }),
      )
      mesh.position.copy(centres[edge])
      mesh.renderOrder = 11
      mesh.userData = {
        isResizeHandle: true,
        edgeKey: edge,
        modelId: spec.uid,
        faceId: spec.faceId,
        patchId: spec.patchId,
        equipmentId: spec.equipmentId ?? null,
      }
      group.add(mesh)
      cubes[edge] = mesh
    }

    const cornerCubes: THREE.Mesh[] = []
    if (spec.cornerHandles && spec.cornerHandles.length === 4) {
      spec.cornerHandles.forEach((position, index) => {
        const mesh = new THREE.Mesh(
          unitCubeGeometry(),
          new THREE.MeshBasicMaterial({
            color: CORNER_HANDLE_COLOR,
            depthTest: false,
            transparent: true,
          }),
        )
        mesh.position.copy(position)
        mesh.renderOrder = 12
        mesh.userData = {
          isResizeHandle: true,
          cornerIndex: index,
          modelId: spec.uid,
          faceId: spec.faceId,
          patchId: spec.patchId,
          equipmentId: spec.equipmentId ?? null,
        }
        group.add(mesh)
        cornerCubes.push(mesh)
      })
    }

    active = {
      group,
      outline,
      cubes,
      dots: [],
      cornerCubes,
      polyVertexCubes: [],
      polyEdgeCubes: [],
      isCircle: false,
      isPolygon: false,
      uid: spec.uid,
      faceId: spec.faceId,
      patchId: spec.patchId,
    }
    updateScale()
    // Raycasting reads `matrixWorld`, which the renderer only refreshes on its
    // next frame — so a handle grabbed in the same tick it was built would be
    // tested at the ORIGIN and missed.
    group.updateMatrixWorld(true)
  }

  function showCircle(spec: CircleHandleSpec) {
    clear()
    const group = new THREE.Group()
    root.add(group)

    const ring = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(spec.ring),
      new THREE.LineBasicMaterial({
        color: OUTLINE_COLORS.idle,
        depthTest: false,
        transparent: true,
      }),
    )
    ring.renderOrder = 10
    group.add(ring)

    const dots = spec.dots.map((position) => {
      const mesh = new THREE.Mesh(
        unitCubeGeometry(),
        new THREE.MeshBasicMaterial({
          color: HANDLE_COLORS.idle,
          depthTest: false,
          transparent: true,
        }),
      )
      mesh.position.copy(position)
      mesh.renderOrder = 11
      mesh.userData = {
        isResizeHandle: true,
        edgeKey: 'circle',
        modelId: spec.uid,
        faceId: spec.faceId,
        patchId: spec.patchId,
        equipmentId: spec.equipmentId ?? null,
      }
      group.add(mesh)
      return mesh
    })

    active = {
      group,
      outline: ring,
      cubes: {},
      dots,
      cornerCubes: [],
      polyVertexCubes: [],
      polyEdgeCubes: [],
      isCircle: true,
      isPolygon: false,
      uid: spec.uid,
      faceId: spec.faceId,
      patchId: spec.patchId,
    }
    updateScale()
    group.updateMatrixWorld(true)
  }

  function showPolygon(spec: PolygonHandleSpec) {
    clear()
    const group = new THREE.Group()
    root.add(group)

    const outline = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([...spec.vertices, spec.vertices[0]!]),
      new THREE.LineBasicMaterial({
        color: OUTLINE_COLORS.idle,
        depthTest: false,
        transparent: true,
      }),
    )
    outline.renderOrder = 10
    group.add(outline)

    const cube = (position: THREE.Vector3, color: number, order: number, data: Record<string, unknown>) => {
      const mesh = new THREE.Mesh(
        unitCubeGeometry(),
        new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true }),
      )
      mesh.position.copy(position)
      mesh.renderOrder = order
      mesh.userData = {
        isResizeHandle: true,
        modelId: spec.uid,
        faceId: spec.faceId,
        patchId: spec.patchId,
        equipmentId: spec.equipmentId ?? null,
        ...data,
      }
      group.add(mesh)
      return mesh
    }
    const polyEdgeCubes = spec.edgeMids.map((p, i) =>
      cube(p, HANDLE_COLORS.idle, 11, { polygonEdge: i, handleKey: `pe:${i}` }),
    )
    const polyVertexCubes = spec.vertices.map((p, i) =>
      cube(p, CORNER_HANDLE_COLOR, 12, { polygonVertex: i, handleKey: `pv:${i}` }),
    )

    active = {
      group,
      outline,
      cubes: {},
      dots: [],
      cornerCubes: [],
      polyVertexCubes,
      polyEdgeCubes,
      isCircle: false,
      isPolygon: true,
      uid: spec.uid,
      faceId: spec.faceId,
      patchId: spec.patchId,
    }
    updateScale()
    group.updateMatrixWorld(true)
  }

  function movePolygon(vertices: THREE.Vector3[], edgeMids: THREE.Vector3[]) {
    if (!active || !active.isPolygon) return
    active.polyVertexCubes.forEach((cube, i) => {
      const p = vertices[i]
      if (p) cube.position.copy(p)
    })
    active.polyEdgeCubes.forEach((cube, i) => {
      const p = edgeMids[i]
      if (p) cube.position.copy(p)
    })
    const position = active.outline.geometry.attributes.position as THREE.BufferAttribute
    if (position.count === vertices.length + 1) {
      vertices.forEach((p, i) => position.setXYZ(i, p.x, p.y, p.z))
      position.setXYZ(vertices.length, vertices[0]!.x, vertices[0]!.y, vertices[0]!.z)
      position.needsUpdate = true
    } else {
      active.outline.geometry.dispose()
      active.outline.geometry = new THREE.BufferGeometry().setFromPoints([...vertices, vertices[0]!])
    }
    updateScale()
    active.group.updateMatrixWorld(true)
  }

  function moveRect(
    corners: THREE.Vector3[],
    outlinePts?: THREE.Vector3[],
    edgeCentres?: Record<EdgeKey, THREE.Vector3>,
    cornerHandles?: THREE.Vector3[],
  ) {
    if (!active || active.isCircle || active.isPolygon) return
    if (cornerHandles) {
      active.cornerCubes.forEach((cube, i) => {
        const p = cornerHandles[i]
        if (p) cube.position.copy(p)
      })
    }
    const pts = outlinePts ?? corners
    const position = active.outline.geometry.attributes.position as THREE.BufferAttribute
    if (position.count === pts.length + 1) {
      for (let i = 0; i < pts.length; i++) position.setXYZ(i, pts[i]!.x, pts[i]!.y, pts[i]!.z)
      position.setXYZ(pts.length, pts[0]!.x, pts[0]!.y, pts[0]!.z)
      position.needsUpdate = true
    } else {
      active.outline.geometry.dispose()
      active.outline.geometry = new THREE.BufferGeometry().setFromPoints([...pts, pts[0]!])
    }
    const centres = edgeCentres ?? edgeCentresOf(corners)
    for (const edge of EDGE_KEYS) active.cubes[edge]?.position.copy(centres[edge])
    updateScale()
    active.group.updateMatrixWorld(true)
  }

  function moveCircle(ring: THREE.Vector3[], dots: THREE.Vector3[]) {
    if (!active || !active.isCircle) return
    const position = active.outline.geometry.attributes.position as THREE.BufferAttribute
    for (let i = 0; i < ring.length && i < position.count; i++) {
      position.setXYZ(i, ring[i]!.x, ring[i]!.y, ring[i]!.z)
    }
    position.needsUpdate = true
    active.dots.forEach((dot, index) => {
      const next = dots[index]
      if (next) dot.position.copy(next)
    })
    updateScale()
    active.group.updateMatrixWorld(true)
  }

  function setStates(hover: HandleKey | null, activeEdge: HandleKey | null) {
    if (!active) return
    const stateOf = (key: HandleKey): HandleState => {
      if (activeEdge === key) return 'active'
      if (hover === key) return 'hover'
      return 'idle'
    }
    // The state scale MULTIPLIES the camera-driven cube size, and is recorded
    // on the mesh so the next `updateScale` (every frame, and on every zoom)
    // re-applies both factors rather than overwriting one with the other.
    const drawn = (mesh: THREE.Mesh, state: HandleState) => {
      mesh.userData.stateScale = HANDLE_SCALE[state]
      mesh.scale.setScalar((Number(mesh.userData.cubeSize) || 1) * HANDLE_SCALE[state])
    }
    for (const edge of EDGE_KEYS) {
      const cube = active.cubes[edge]
      if (!cube) continue
      const state = stateOf(edge)
      ;(cube.material as THREE.MeshBasicMaterial).color.setHex(HANDLE_COLORS[state])
      drawn(cube, state)
    }
    if (active.dots.length) {
      const state = stateOf('circle')
      for (const dot of active.dots) {
        ;(dot.material as THREE.MeshBasicMaterial).color.setHex(HANDLE_COLORS[state])
        drawn(dot, state)
      }
    }
    // A polygon's vertex cubes keep their blue at rest, like the rectangle's
    // corner cubes; hover and drag use the shared state colours.
    for (const mesh of [...active.polyVertexCubes, ...active.polyEdgeCubes]) {
      const key = mesh.userData.handleKey as HandleKey
      const state = stateOf(key)
      const idle = mesh.userData.polygonVertex !== undefined ? CORNER_HANDLE_COLOR : HANDLE_COLORS.idle
      ;(mesh.material as THREE.MeshBasicMaterial).color.setHex(state === 'idle' ? idle : HANDLE_COLORS[state])
      drawn(mesh, state)
    }
    // The outline reacts to ANY of its edges, so grabbing one lights the whole
    // patch: that is what says which patch the drag belongs to.
    const outlineState: HandleState = activeEdge ? 'active' : hover ? 'hover' : 'idle'
    ;(active.outline.material as THREE.LineBasicMaterial).color.setHex(OUTLINE_COLORS[outlineState])
  }

  return {
    showRect,
    showCircle,
    showPolygon,
    hide: clear,
    updateScale,
    moveRect,
    moveCircle,
    movePolygon,
    setStates,
    pickables: () => {
      if (!active) return []
      return [
        ...Object.values(active.cubes),
        ...active.dots,
        ...active.cornerCubes,
        ...active.polyVertexCubes,
        ...active.polyEdgeCubes,
      ].filter(Boolean) as THREE.Object3D[]
    },
    current: () =>
      active
        ? {
            uid: active.uid,
            faceId: active.faceId,
            patchId: active.patchId,
            isCircle: active.isCircle,
            isPolygon: active.isPolygon,
          }
        : null,
    dispose() {
      clear()
      root.parent?.remove(root)
    },
  }
}
