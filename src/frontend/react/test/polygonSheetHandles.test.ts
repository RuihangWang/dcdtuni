import * as THREE from 'three'
import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { geometryPure } from '../src/legacy/geometryBuilder'
import '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import { commitSheetPolygon } from '../src/geometry/faceDragActions'
import { createFaceDragTools } from '../src/geometry/faceDrag'
import type { TreeSelection } from '../src/interaction/treeNodes'

/**
 * A floor or ceiling drawn in the 2D room editor is an arbitrary polygon
 * (`geometry.polygon`, in the top face layout's (u, v)). Selecting it has to
 * outline THAT polygon and offer a cube on every vertex and every edge — the
 * bounding rectangle's four edges and four corners are handles for a shape
 * that is not there (the Tri_angle room, 2026-09-23).
 */

type Pure = {
  applyModelTransformToObject(object: THREE.Object3D, model: unknown): void
}
const pure = () => geometryPure() as unknown as Pure

/** Tri_angle's room floor, as the 2D editor wrote it. */
const TRIANGLE = [
  [3.663123644251627, 3.8251626898047726],
  [7.6, 0],
  [0, 0],
]

function triangleFloor(quaternion = { x: 0, y: 0, z: 0, w: 1 }): Construction {
  return {
    uid: 'm_26',
    kind: 'floor',
    meta: { name: 'Room_Floor' },
    geometry: {
      location: { x: 0.8368763557483734, y: 0, z: 3.3000000000000003 },
      size: { x: 7.6, y: 0, z: 3.8251626898047726 },
      quaternion,
      scale: { x: 1, y: 1, z: 1 },
      polygon: TRIANGLE.map((p) => [...p]),
      faces: { top: { id: 'top', wType: 'W', hType: 'D', patches: [] } },
    },
  } as unknown as Construction
}

function rectFloor(): Construction {
  return {
    uid: 'm_f',
    kind: 'floor',
    meta: { name: 'Floor' },
    geometry: {
      location: { x: 0, y: 0, z: 0 },
      size: { x: 4, y: 0, z: 3 },
      quaternion: { x: 0, y: 0, z: 0, w: 1 },
      scale: { x: 1, y: 1, z: 1 },
      faces: { top: { id: 'top', wType: 'W', hType: 'D', patches: [] } },
    },
  } as unknown as Construction
}

/** Where polygon vertex (u, v) sits in the world: local (u, H, D - v). */
function worldVertex(model: Construction, u: number, v: number): THREE.Vector3 {
  const g = model.geometry as unknown as {
    location: { x: number; y: number; z: number }
    size: { x: number; y: number; z: number }
    quaternion: { x: number; y: number; z: number; w: number }
  }
  const q = new THREE.Quaternion(g.quaternion.x, g.quaternion.y, g.quaternion.z, g.quaternion.w)
  return new THREE.Vector3(u, g.size.y, g.size.z - v)
    .applyQuaternion(q)
    .add(new THREE.Vector3(g.location.x, g.location.y, g.location.z))
}

function harness(models: Construction[]) {
  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 1000)
  camera.position.set(4, 10, 2)
  const renderRootMap = new Map<string, THREE.Object3D>()
  for (const m of models) {
    const root = new THREE.Group()
    root.userData = { modelId: m.uid }
    pure().applyModelTransformToObject(root, m)
    // the scene builder places the root at the model's location
    const loc = (m.geometry as unknown as { location: { x: number; y: number; z: number } }).location
    root.position.set(loc.x, loc.y, loc.z)
    scene.add(root)
    renderRootMap.set(m.uid, root)
  }
  scene.updateMatrixWorld(true)
  let node: TreeSelection | null = null
  const registries = {
    renderMeshes: [] as THREE.Object3D[],
    renderRoots: [...renderRootMap.values()],
    renderHelpers: [] as THREE.Object3D[],
    renderRootMap,
    modelMap: new Map(models.map((m) => [m.uid, m])),
    geometryCache: new Map<string, unknown>(),
  }
  const tools = createFaceDragTools({
    scene,
    camera,
    domElement: document.createElement('div'),
    orbit: null,
    registries: () => registries,
    tools: () => null,
    project: () => null,
    selection: () => ({ uid: node?.uid ?? null, node }),
    snap: () => ({ face: false, edge: false }),
    rebuildSoon: () => {},
    rebuildNow: () => {},
    enabled: () => true,
  })
  const instance = () => scene.getObjectByName('_resizeHandles')!.children[0] as THREE.Group | undefined
  return {
    select(uid: string) {
      node = { type: 'model', uid } as TreeSelection
      tools.refresh()
    },
    cubes: () => (instance()?.children.filter((c) => (c as THREE.Mesh).isMesh) ?? []) as THREE.Mesh[],
    outline(): THREE.Vector3[] {
      const line = instance()!.children.find((c) => (c as THREE.Line).isLine) as THREE.Line
      const a = line.geometry.attributes.position as THREE.BufferAttribute
      return Array.from({ length: a.count }, (_, i) => new THREE.Vector3(a.getX(i), a.getY(i), a.getZ(i)))
    },
  }
}

function expectAt(actual: THREE.Vector3 | undefined, want: THREE.Vector3, digits = 9) {
  expect(actual).toBeDefined()
  expect(actual!.x).toBeCloseTo(want.x, digits)
  expect(actual!.y).toBeCloseTo(want.y, digits)
  expect(actual!.z).toBeCloseTo(want.z, digits)
}

describe('a polygon floor’s selection handles', () => {
  it('put one cube on every vertex and one on every edge — no bounding-box cubes', () => {
    const floor = triangleFloor()
    const h = harness([floor])
    h.select('m_26')
    const cubes = h.cubes()
    const vertices = cubes.filter((c) => typeof c.userData.polygonVertex === 'number')
    const edges = cubes.filter((c) => typeof c.userData.polygonEdge === 'number')
    expect(vertices).toHaveLength(3)
    expect(edges).toHaveLength(3)
    expect(cubes.some((c) => c.userData.edgeKey || typeof c.userData.cornerIndex === 'number')).toBe(false)
    TRIANGLE.forEach(([u, v], i) => {
      expectAt(vertices.find((c) => c.userData.polygonVertex === i)?.position, worldVertex(floor, u!, v!))
    })
    // edge i runs vertex i -> i + 1; its cube is at the midpoint
    TRIANGLE.forEach(([u, v], i) => {
      const [u2, v2] = TRIANGLE[(i + 1) % 3]!
      expectAt(
        edges.find((c) => c.userData.polygonEdge === i)?.position,
        worldVertex(floor, (u! + u2!) / 2, (v! + v2!) / 2),
      )
    })
  })

  it('draw the outline through the polygon, closed', () => {
    const floor = triangleFloor()
    const h = harness([floor])
    h.select('m_26')
    const outline = h.outline()
    expect(outline).toHaveLength(4)
    // the line's buffer is Float32
    TRIANGLE.forEach(([u, v], i) => expectAt(outline[i], worldVertex(floor, u!, v!), 5))
    expectAt(outline[3], outline[0]!, 5)
  })

  it('follow the floor’s rotation', () => {
    const yaw = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 5)
    const floor = triangleFloor({ x: yaw.x, y: yaw.y, z: yaw.z, w: yaw.w })
    const h = harness([floor])
    h.select('m_26')
    const vertex = h.cubes().find((c) => c.userData.polygonVertex === 1)
    expectAt(vertex?.position, worldVertex(floor, TRIANGLE[1]![0]!, TRIANGLE[1]![1]!))
  })

  it('leave a floor added in 3D (no polygon) with its 4 edge + 4 corner cubes', () => {
    const h = harness([rectFloor()])
    h.select('m_f')
    const cubes = h.cubes()
    expect(cubes.filter((c) => c.userData.edgeKey)).toHaveLength(4)
    expect(cubes.filter((c) => typeof c.userData.cornerIndex === 'number')).toHaveLength(4)
    expect(cubes.some((c) => typeof c.userData.polygonVertex === 'number')).toBe(false)
  })
})

describe('the shared polygon edits', () => {
  it('move an edge along its outward normal only', () => {
    // Edge 1 is the triangle's bottom (v = 0), whose outward normal is -v.
    const moved = geometryPure().movePolygonEdge(TRIANGLE, 1, 0.3, -0.5)
    expect(moved[1]![1]).toBeCloseTo(-0.5, 12)
    expect(moved[2]![1]).toBeCloseTo(-0.5, 12)
    expect(moved[1]![0]).toBeCloseTo(7.6, 12)
    expect(moved[0]).toEqual(TRIANGLE[0])
  })

  it('refuse a vertex move that folds the outline over itself', () => {
    const square = [
      [0, 0],
      [2, 0],
      [2, 2],
      [0, 2],
    ]
    // vertex 0 dragged across the right side: edge 3 would cross edge 1
    expect(geometryPure().movePolygonVertex(square, 0, 3, 1)).toEqual(square)
    expect(geometryPure().polygonSelfIntersects([[0, 0], [2, 2], [2, 0], [0, 2]])).toBe(true)
  })
})

function projectWith(floor: Construction): CfdProject {
  const { kind: _kind, ...stored } = floor as unknown as Record<string, unknown>
  return {
    schemaVersion: 4,
    name: 'T',
    geometry: {},
    constructions: {
      floors: { [floor.uid]: stored },
      racks: {},
    },
  } as unknown as CfdProject
}

describe('commitSheetPolygon', () => {
  let floor: Construction
  beforeEach(() => {
    const yaw = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.4)
    floor = triangleFloor({ x: yaw.x, y: yaw.y, z: yaw.z, w: yaw.w })
    const g = floor.geometry as unknown as Record<string, unknown>
    g.faces = {
      top: { id: 'top', wType: 'W', hType: 'D', patches: [{ id: 'p', type: 'rect', x: 3, y: 1, w: 0.5, h: 0.4 }] },
    }
    store.loadProject(projectWith(floor), null)
  })

  it('re-bases to the new bounding box without moving anything in the world, as ONE undo entry', () => {
    const live = () => store.findConstruction(store.getProject() as CfdProject, 'm_26')!.model
    const before = structuredClone(live())
    // vertex 2 dragged out past the old box (negative u and v)
    const next = geometryPure().movePolygonVertex(TRIANGLE, 2, -1, -0.5)
    expect(commitSheetPolygon('m_26', next)).toBe(true)
    const after = live()
    const g = after.geometry as unknown as {
      polygon: number[][]
      size: { x: number; z: number }
      faces: { top: { patches: { x: number; y: number }[] } }
    }
    // the stored outline is its own box again
    expect(Math.min(...g.polygon.map((p) => p[0]!))).toBeCloseTo(0, 6)
    expect(Math.min(...g.polygon.map((p) => p[1]!))).toBeCloseTo(0, 6)
    expect(g.size.x).toBeCloseTo(8.6, 6)
    expect(g.size.z).toBeCloseTo(3.8251626898047726 + 0.5, 6)
    // every vertex is where the drag left it, in the world
    next.forEach(([u, v], i) => {
      const beforeWorld = worldVertex(before, u!, v!)
      const [u2, v2] = g.polygon[i]!
      expectAt(worldVertex(after, u2!, v2!), beforeWorld, 5)
    })
    // and so is the patch cut into the top face
    const patchBefore = worldVertex(before, 3, 1)
    const p = g.faces.top.patches[0]!
    expectAt(worldVertex(after, p.x, p.y), patchBefore, 5)
    // one step back is the outline the drag started from
    store.undo()
    expect((live().geometry as unknown as { polygon: number[][] }).polygon).toEqual(TRIANGLE)
  })
})
