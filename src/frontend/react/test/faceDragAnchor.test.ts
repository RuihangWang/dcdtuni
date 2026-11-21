import * as THREE from 'three'
import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { geometryPure } from '../src/legacy/geometryBuilder'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'
import { mergeSlotsIntoEquipment } from '../src/geometry/rackSlots'
import { createFaceDragTools } from '../src/geometry/faceDrag'
import type { TreeSelection } from '../src/interaction/treeNodes'

/**
 * Where the selected patch's red ball goes: `FaceDragTools.anchorPoint()`.
 *
 * The anchor is the point the inspector's numbers describe — a rect's (x, y)
 * corner, a circle's centre — and it has to land ON the drawn patch, on every
 * kind of face the handles can be drawn on. The layout-only computation
 * (`patchAnchorWorld`) gets two of them wrong: a server patch is on the SERVER's
 * face, in fractions of it, and a wall-mounted unit's front is a curve the flat
 * layout plane cuts straight through. So the ball takes its point from the same
 * code that draws the handles, and these tests hold it to the handles.
 */

type Pure = {
  applyModelTransformToObject(object: THREE.Object3D, model: unknown): void
  getFaceLayouts(model: unknown): Record<string, { origin: THREE.Vector3; right: THREE.Vector3; up: THREE.Vector3 }>
  getServerFaceRect(
    box: Record<string, number>,
    faceId: string,
  ): { origin: THREE.Vector3; right: THREE.Vector3; up: THREE.Vector3; w: number; h: number }
  wallAcuProfile(model: unknown): unknown
  wallAcuPointLocal(profile: unknown, u: number, v: number): THREE.Vector3
  handleCubeSize(dist: number): number
}
const pure = () => geometryPure() as unknown as Pure

function harness(
  models: Construction[],
  extraRoot?: (uid: string, root: THREE.Group) => void,
  handlesRefreshed?: () => void,
) {
  const scene = new THREE.Scene()
  // An offset between the scene and the model roots, so a point that forgot
  // the world transform cannot pass by accident.
  const content = new THREE.Group()
  content.position.set(0.5, 0, -0.25)
  scene.add(content)
  const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 1000)
  camera.position.set(6, 4, 8)
  const renderRootMap = new Map<string, THREE.Object3D>()
  for (const m of models) {
    const root = new THREE.Group()
    root.userData = { modelId: m.uid }
    pure().applyModelTransformToObject(root, m)
    extraRoot?.(m.uid, root)
    content.add(root)
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
    handlesRefreshed,
  })
  const rootOf = (uid: string) => renderRootMap.get(uid)!
  const instance = () => scene.getObjectByName('_resizeHandles')!.children[0] as THREE.Group | undefined
  return {
    scene,
    camera,
    tools,
    rootOf,
    select(next: TreeSelection | null) {
      node = next
      tools.refresh()
    },
    /** The first point of the yellow outline — the patch's (x, y) corner when it is not a trapezoid. */
    outlineStart(): THREE.Vector3 {
      const line = instance()!.children.find((c) => (c as THREE.Line).isLine) as THREE.Line
      const a = line.geometry.attributes.position as THREE.BufferAttribute
      return new THREE.Vector3(a.getX(0), a.getY(0), a.getZ(0))
    },
    cornerCube(index: number): THREE.Mesh {
      return instance()!.children.find((c) => c.userData.cornerIndex === index) as THREE.Mesh
    },
    /**
     * The DRAWN side of a handle cube. The geometry is a unit box and `scale`
     * carries the size, so a zoom can re-measure the overlay without rebuilding
     * it — reading the geometry alone would say every cube is 1 m.
     */
    cubeSide(mesh: THREE.Mesh): number {
      return (mesh.geometry as THREE.BoxGeometry).parameters.width * mesh.scale.x
    },
    /** The circle handles' centre: halfway between two opposite grab dots. */
    ringCentre(): THREE.Vector3 {
      const dots = instance()!.children.filter((c) => c.userData.edgeKey === 'circle')
      return dots[0]!.position.clone().add(dots[2]!.position).multiplyScalar(0.5)
    },
    handlesShown: () => !!instance(),
  }
}

function expectAt(actual: THREE.Vector3 | null, want: THREE.Vector3, digits = 9) {
  expect(actual).not.toBeNull()
  expect(actual!.x).toBeCloseTo(want.x, digits)
  expect(actual!.y).toBeCloseTo(want.y, digits)
  expect(actual!.z).toBeCloseTo(want.z, digits)
}

const YAW_90 = { x: 0, y: Math.SQRT1_2, z: 0, w: Math.SQRT1_2 }

function box(): Construction {
  return {
    uid: 'm_box',
    kind: 'box',
    meta: { name: 'electric_box' },
    geometry: {
      location: { x: 1, y: 0, z: 2 },
      size: { x: 1.2, y: 2, z: 0.6 },
      quaternion: YAW_90,
      scale: { x: 1, y: 1, z: 1 },
      faces: {
        front: {
          id: 'front',
          wType: 'W',
          hType: 'H',
          patches: [{ id: 'p_rect', type: 'rect', x: 0.1, y: 0.2, w: 0.5, h: 0.5 }],
        },
        top: {
          id: 'top',
          wType: 'W',
          hType: 'D',
          patches: [{ id: 'p_circle', type: 'circle', cx: 0.4, cy: 0.3, r: 0.1 }],
        },
      },
    },
  } as unknown as Construction
}

describe('anchorPoint on a flat face', () => {
  it('is a rect patch’s (x, y) corner — where the first blue corner cube sits', () => {
    const m = box()
    const h = harness([m])
    h.select({ type: 'patch', uid: 'm_box', faceId: 'front', patchId: 'p_rect' })
    const layout = pure().getFaceLayouts(m).front!
    const want = h
      .rootOf('m_box')
      .localToWorld(layout.origin.clone().addScaledVector(layout.right, 0.1).addScaledVector(layout.up, 0.2))
    expectAt(h.tools.anchorPoint(), want)
    expectAt(h.cornerCube(0).position, want)
    // A cube is sized from ITS OWN distance to the camera, and the red ball's
    // floor is 0.9 of the cube side at the same point — so the two agree here,
    // on the point they share, whatever the zoom.
    const side = h.cubeSide(h.cornerCube(0))
    expect(side).toBeCloseTo(pure().handleCubeSize(h.camera.position.distanceTo(want)), 12)
  })

  it('stays at (x, y) on a trapezoid while the corner cube moves with its offset', () => {
    const m = box()
    const patch = (m.geometry as { faces: { front: { patches: Record<string, unknown>[] } } }).faces.front.patches[0]!
    patch.cornerOffsets = [{ x: 0.05, y: 0.04 }, { x: 0, y: 0 }, { x: 0, y: 0 }, { x: 0, y: 0 }]
    const h = harness([m])
    h.select({ type: 'patch', uid: 'm_box', faceId: 'front', patchId: 'p_rect' })
    const layout = pure().getFaceLayouts(m).front!
    const want = h
      .rootOf('m_box')
      .localToWorld(layout.origin.clone().addScaledVector(layout.right, 0.1).addScaledVector(layout.up, 0.2))
    expectAt(h.tools.anchorPoint(), want)
    expect(h.cornerCube(0).position.distanceTo(want)).toBeGreaterThan(0.06)
  })

  it('is a circle’s centre — the point its grab dots surround', () => {
    const m = box()
    const h = harness([m])
    h.select({ type: 'patch', uid: 'm_box', faceId: 'top', patchId: 'p_circle' })
    const layout = pure().getFaceLayouts(m).top!
    const want = h
      .rootOf('m_box')
      .localToWorld(layout.origin.clone().addScaledVector(layout.right, 0.4).addScaledVector(layout.up, 0.3))
    expectAt(h.tools.anchorPoint(), want)
    expectAt(h.ringCentre(), want)
  })

  it('is nothing without a selected patch', () => {
    const m = box()
    const floor = {
      uid: 'm_floor',
      kind: 'floor',
      meta: { name: 'floor' },
      geometry: { location: { x: 0, y: 0, z: 0 }, size: { x: 4, y: 0.05, z: 3 }, faces: {} },
    } as unknown as Construction
    const h = harness([m, floor])
    h.select(null)
    expect(h.tools.anchorPoint()).toBeNull()
    h.select({ type: 'face', uid: 'm_box', faceId: 'front' })
    expect(h.handlesShown()).toBe(true)
    expect(h.tools.anchorPoint()).toBeNull()
    h.select({ type: 'model', uid: 'm_floor' })
    expect(h.handlesShown()).toBe(true)
    expect(h.tools.anchorPoint()).toBeNull()
    h.select({ type: 'patch', uid: 'm_box', faceId: 'front', patchId: 'p_ghost' })
    expect(h.tools.anchorPoint()).toBeNull()
  })

  it('is nothing for a partition wall’s circle, which has no ring either', () => {
    const wall = {
      uid: 'm_pw',
      kind: 'partitionWall',
      meta: { name: 'pw' },
      geometry: {
        location: { x: 0, y: 0, z: 0 },
        size: { x: 3, y: 2.5, z: 0.1 },
        faces: { front: { id: 'front', patches: [{ id: 'p_hole', type: 'circle', cx: 1, cy: 1, r: 0.2 }] } },
      },
    } as unknown as Construction
    const h = harness([wall])
    h.select({ type: 'patch', uid: 'm_pw', faceId: 'front', patchId: 'p_hole' })
    expect(h.handlesShown()).toBe(false)
    expect(h.tools.anchorPoint()).toBeNull()
  })
})

describe('anchorPoint on a wall-mounted unit’s curved front', () => {
  function wallAcu(): Construction {
    return {
      uid: 'm_ac2',
      kind: 'acu',
      meta: { name: 'AC2' },
      cooling: { unitType: 'wallMounted' },
      geometry: {
        location: { x: 2.45, y: 2.07, z: 0 },
        size: { x: 0.8, y: 0.3, z: 0.2 },
        quaternion: YAW_90,
        scale: { x: 1, y: 1, z: 1 },
        faces: {
          front: {
            id: 'front',
            wType: 'W',
            hType: 'H',
            patches: [
              // v = 0.12 is on the quarter cylinder (flat 0.08 m, arc 0.188 m)
              { id: 'p_supply', type: 'rect', x: 0.1, y: 0.12, w: 0.6, h: 0.2 },
              { id: 'p_round', type: 'circle', cx: 0.4, cy: 0.15, r: 0.03 },
            ],
          },
        },
      },
    } as unknown as Construction
  }

  function flatPoint(h: ReturnType<typeof harness>, m: Construction, u: number, v: number) {
    const layout = pure().getFaceLayouts(m).front!
    return h
      .rootOf(m.uid)
      .localToWorld(layout.origin.clone().addScaledVector(layout.right, u).addScaledVector(layout.up, v))
  }

  it('puts a rect’s corner ON the surface, where the outline starts', () => {
    const m = wallAcu()
    const h = harness([m])
    h.select({ type: 'patch', uid: 'm_ac2', faceId: 'front', patchId: 'p_supply' })
    const want = h.rootOf('m_ac2').localToWorld(pure().wallAcuPointLocal(pure().wallAcuProfile(m), 0.1, 0.12))
    expectAt(h.tools.anchorPoint(), want)
    // The outline's points live in a Float32 attribute.
    expectAt(h.outlineStart(), want, 6)
    // …and NOT on the flat layout plane, which the old placement used.
    expect(h.tools.anchorPoint()!.distanceTo(flatPoint(h, m, 0.1, 0.12))).toBeGreaterThan(0.01)
  })

  it('puts a circle’s centre on the surface too', () => {
    const m = wallAcu()
    const h = harness([m])
    h.select({ type: 'patch', uid: 'm_ac2', faceId: 'front', patchId: 'p_round' })
    const want = h.rootOf('m_ac2').localToWorld(pure().wallAcuPointLocal(pure().wallAcuProfile(m), 0.4, 0.15))
    expectAt(h.tools.anchorPoint(), want)
    expect(h.tools.anchorPoint()!.distanceTo(flatPoint(h, m, 0.4, 0.15))).toBeGreaterThan(0.01)
  })
})

describe('anchorPoint on a server’s patch', () => {
  let rackUid: string
  const BOX = { x0: 0.05, x1: 0.55, y0: 1.2, y1: 1.4, z0: -0.4, z1: 0.4 }

  beforeEach(() => {
    store.loadProject(
      stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
      null,
    )
    const { bucket, model } = createModel(store.getProject() as CfdProject, 'rack')
    store.addModel(bucket, model)
    rackUid = model.uid
    mergeSlotsIntoEquipment(rackUid, 'front', [10, 11, 12, 13], 'fanServer')
  })

  function rackWithVents(): { rack: Construction; equipmentId: string } {
    const rack = store.findConstruction(store.getProject() as CfdProject, rackUid)!.model
    ;(rack.geometry as Record<string, unknown>).location = { x: 3, y: 0, z: 1 }
    ;(rack.geometry as Record<string, unknown>).quaternion = YAW_90
    const servers = (rack.constructions as { servers: Record<string, { faces: Record<string, { patches?: unknown[] }> }> })
      .servers
    const [equipmentId] = Object.keys(servers)
    servers[equipmentId!]!.faces.inlet!.patches = [
      { id: 'p_vent', type: 'rect', x: 0.1, y: 0.2, w: 0.3, h: 0.4 },
      { id: 'p_hole', type: 'circle', cx: 0.5, cy: 0.25, r: 0.2 },
    ]
    return { rack, equipmentId: equipmentId! }
  }

  /** The server's built inlet face, as `serverBoxFromScene` reads it off the rack root. */
  function serverMeshes(equipmentId: string) {
    return (_uid: string, root: THREE.Group) => {
      const geom = new THREE.BufferGeometry()
      geom.setAttribute(
        'position',
        new THREE.Float32BufferAttribute([BOX.x0, BOX.y0, BOX.z0, BOX.x1, BOX.y1, BOX.z1], 3),
      )
      const mesh = new THREE.Mesh(geom, new THREE.MeshBasicMaterial())
      mesh.userData = { isRackEquipment: true, equipmentId, faceId: 'inlet' }
      root.add(mesh)
    }
  }

  function onServerFace(h: ReturnType<typeof harness>, fu: number, fv: number) {
    const layout = pure().getServerFaceRect(BOX, 'inlet')
    return h
      .rootOf(rackUid)
      .localToWorld(
        layout.origin.clone().addScaledVector(layout.right, fu * layout.w).addScaledVector(layout.up, fv * layout.h),
      )
  }

  it('is a rect vent’s corner on the SERVER face, in metres from its fractions', () => {
    const { rack, equipmentId } = rackWithVents()
    const h = harness([rack], serverMeshes(equipmentId))
    h.select({ type: 'serverPatch', uid: rackUid, equipmentId, faceId: 'inlet', patchId: 'p_vent' })
    const want = onServerFace(h, 0.1, 0.2)
    // Float32 positions: 0.05 comes back as 0.0500000007.
    expectAt(h.tools.anchorPoint(), want, 6)
    expectAt(h.outlineStart(), want, 6)
  })

  it('is a round vent’s centre, scaled out of fractions the same way', () => {
    const { rack, equipmentId } = rackWithVents()
    const h = harness([rack], serverMeshes(equipmentId))
    h.select({ type: 'serverPatch', uid: rackUid, equipmentId, faceId: 'inlet', patchId: 'p_hole' })
    expectAt(h.tools.anchorPoint(), onServerFace(h, 0.5, 0.25), 6)
  })

  it('is nothing when the server has no built face to measure', () => {
    const { rack, equipmentId } = rackWithVents()
    const h = harness([rack])
    h.select({ type: 'serverPatch', uid: rackUid, equipmentId, faceId: 'inlet', patchId: 'p_vent' })
    expect(h.tools.anchorPoint()).toBeNull()
  })
})

describe('handlesRefreshed', () => {
  // The anchor balls are sized against the cubes, so they must be re-sized
  // whenever the cubes are rebuilt. A click on the ALREADY-selected patch is not
  // a selection at all — its pointerdown starts a body drag and the no-move
  // release only redraws the handles — so this callback is the one signal that
  // reaches the balls (measured on 8044: cube 0.073, ball 0.044 after zooming
  // out and clicking the same patch).
  it('fires after every redraw, including one that hides the handles', () => {
    let calls = 0
    const h = harness([box()], undefined, () => {
      calls++
    })
    h.select({ type: 'patch', uid: 'm_box', faceId: 'front', patchId: 'p_rect' })
    expect(h.handlesShown()).toBe(true)
    expect(calls).toBe(1)
    h.tools.refresh()
    expect(calls).toBe(2)
    h.select(null)
    expect(h.handlesShown()).toBe(false)
    expect(calls).toBe(3)
  })
})
