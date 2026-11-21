import * as THREE from 'three'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

/**
 * The shared pipe renderer draws a V4 T-branch, and rotating one moves the
 * pipes attached to it.
 *
 * `js/liquid_pipe_renderer.js` is shared by both editors and predates the V4
 * rename. `renderPipeJunction` still read `junctionModel.enabled` and
 * `junctionModel.id` — the flag moved to `meta.enabled` and the id to `uid` —
 * so every junction failed the guard on its first line and NO cube was ever
 * drawn, in either app. The rotate gizmo then had nothing to attach to, which
 * is why "the T-branch gizmo does not work" was reported as a missing feature:
 * the gizmo was written, and the object it grabs did not exist.
 *
 * The module reads its dependencies as bare globals, so this test installs the
 * five it needs and calls it the way the app does.
 */

type Bag = Record<string, unknown>

interface StubModel {
  uid: string
  kind: string
  meta: { name: string; enabled: boolean }
  geometry: Bag
  cooling?: Bag
}

let globalGroup: THREE.Group
let models: StubModel[]
/** Records the WP-S1 registry calls the renderer makes, per test. */
let registered: Map<THREE.Object3D, number>

function junction(over: Partial<StubModel> = {}): StubModel {
  return {
    uid: 'j_1',
    kind: 'pipe_junction',
    meta: { name: 'T-Branch_1', enabled: true },
    geometry: { location: { x: 1, y: 1.2, z: 1 } },
    cooling: {
      quaternion: { x: 0, y: 0, z: 0, w: 1 },
      ports: [{ id: 'p2', offset: { x: 0.3, y: 0, z: 0 }, connectedPipeId: 'seg_1' }],
    },
    ...over,
  }
}

function pipe(): StubModel {
  return {
    uid: 'seg_1',
    kind: 'pipe_segment',
    meta: { name: 'Pipe_1', enabled: true },
    geometry: {
      waypoints: [
        { x: 0, y: 1.2, z: 1 },
        { x: 1.3, y: 1.2, z: 1 },
      ],
    },
    cooling: { connection: { startPortId: '', endPortId: 'j_1.p2' } },
  }
}

/**
 * The junction's container Group — the object tagged `isJunctionBody`, which
 * is what `attachJunctionGizmo` finds by the same traverse. Since the WP-W4
 * restructure it carries the junction's position + quaternion, with the body
 * cube and the port spheres as independent children.
 */
function junctionRoots(): THREE.Object3D[] {
  const found: THREE.Object3D[] = []
  globalGroup.traverse((o) => {
    if ((o.userData as Bag | undefined)?.isJunctionBody) found.push(o)
  })
  return found
}

function bodyCube(root: THREE.Object3D): THREE.Mesh {
  const body = root.children.find((c) => (c.userData as Bag).isJunctionBodyMesh)
  expect(body).toBeDefined()
  return body as THREE.Mesh
}

beforeEach(async () => {
  const w = window as unknown as Bag
  globalGroup = new THREE.Group()
  models = []
  registered = new Map()
  w.THREE = THREE
  w.globalGroup = globalGroup
  w.state = {}
  w.allModels = () => models
  w.requestRender = () => {}
  // Recording stand-in for geometry_pure's helper scale registry (WP-S1): the
  // renderer reads `window.geometryPure` defensively, so a stub that captures
  // registerHelper/unregisterHelper is enough to assert what it registered at.
  w.geometryPure = {
    registerHelper: (mesh: THREE.Object3D, r: number) => {
      registered.set(mesh, r)
    },
    unregisterHelper: (mesh: THREE.Object3D) => {
      registered.delete(mesh)
    },
  }
  // The module registers itself on `window` at import; vitest caches it, so a
  // second suite in the same worker reuses the same registration — which is
  // fine, because every dependency above is re-installed per test.
  await import('../../../shared/js/liquid_pipe_renderer.js')
})

afterEach(() => {
  const w = window as unknown as Bag
  delete w.globalGroup
  delete w.state
  delete w.allModels
  delete w.geometryPure
})

function renderer(): { updatePipeGeometry(): void } {
  return (window as unknown as { _liquidPipeRenderer: { updatePipeGeometry(): void } })
    ._liquidPipeRenderer
}

function reroute(uid: string) {
  ;(window as unknown as { _reroutePipesForJunction(id: string): void })._reroutePipesForJunction(
    uid,
  )
}

describe('renderPipeJunction', () => {
  it('draws a junction root for an enabled V4 junction', () => {
    models = [junction()]
    renderer().updatePipeGeometry()

    const drawn = junctionRoots()
    expect(drawn.length).toBe(1)
    expect(drawn[0]!.position.toArray()).toEqual([1, 1.2, 1])
  })

  it('tags the root with the junction’s uid', () => {
    models = [junction()]
    renderer().updatePipeGeometry()

    // `attachJunctionGizmo` finds its target by comparing this to the selected
    // uid. `undefined` here — which is what `.id` gave on a V4 model — means the
    // gizmo silently attaches to nothing.
    expect((junctionRoots()[0]!.userData as Bag).junctionId).toBe('j_1')
  })

  it('draws nothing for a disabled one', () => {
    models = [junction({ meta: { name: 'T-Branch_1', enabled: false } })]
    renderer().updatePipeGeometry()
    expect(junctionRoots().length).toBe(0)
  })

  it('carries the transform on the container, not on the body cube', () => {
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI / 2)
    models = [
      junction({
        cooling: {
          quaternion: { x: q.x, y: q.y, z: q.z, w: q.w },
          ports: [{ id: 'p2', offset: { x: 0.3, y: 0, z: 0 }, connectedPipeId: 'seg_1' }],
        },
      }),
    ]
    renderer().updatePipeGeometry()

    const root = junctionRoots()[0]!
    expect(root.quaternion.toArray().map((v) => +v.toFixed(6))).toEqual(
      [q.x, q.y, q.z, q.w].map((v) => +v.toFixed(6)),
    )
    // The gizmo rotates the container and objectChange reads ITS quaternion
    // back into the model, so the body cube must stay transform-neutral.
    const body = bodyCube(root)
    expect(body.position.toArray()).toEqual([0, 0, 0])
    expect(body.quaternion.toArray()).toEqual([0, 0, 0, 1])
  })

  it('builds the body cube as unit geometry registered at the 0.03 half edge', () => {
    models = [junction()]
    renderer().updatePipeGeometry()

    const body = bodyCube(junctionRoots()[0]!)
    const params = (body.geometry as THREE.BoxGeometry).parameters
    expect([params.width, params.height, params.depth]).toEqual([1, 1, 1])
    // Baseline scale = data size before the first syncHelperScales frame…
    expect(body.scale.x).toBeCloseTo(0.03, 12)
    // …and the registry entry that owns the scale from then on: HALF edge
    // 0.03, so the shown edge is 2r = 0.06 m — the historic cube size.
    expect(registered.get(body)).toBe(0.03)
  })

  it('hangs the ports off the container, so they turn with it', () => {
    models = [junction()]
    renderer().updatePipeGeometry()
    const ports = junctionRoots()[0]!.children.filter((c) => (c.userData as Bag).isJunctionPort)
    expect(ports.length).toBe(1)
    // The DATA offset, untouched by the body cube's helper scale: the ports
    // are the container's children, not the cube's, so no per-frame scale on
    // the body can compress the offset (the pre-WP-W4 defect the restructure
    // was for).
    expect(ports[0]!.position.toArray()).toEqual([0.3, 0, 0])
    expect(registered.get(ports[0]!)).toBe(0.015)
  })

  it('releases the body and the ports from the registry on rebuild', () => {
    models = [junction()]
    renderer().updatePipeGeometry()
    const root = junctionRoots()[0]!
    const body = bodyCube(root)
    const port = root.children.find((c) => (c.userData as Bag).isJunctionPort)!
    expect(registered.size).toBe(2)

    // updatePipeGeometry starts with clearPipes(), which unregisters both.
    renderer().updatePipeGeometry()
    expect(registered.has(body)).toBe(false)
    expect(registered.has(port)).toBe(false)
    expect(registered.size).toBe(2) // the fresh junction's own pair
  })
})

/**
 * The server chip-temperature overlay.
 *
 * Same file, same vintage, same defect: `_toggleServerTemperatureColoring`
 * matched `child.userData.modelId === rackModel.id`, and the scene tags meshes
 * with `model.uid` — so no server was ever recoloured, in either editor. The
 * line directly above it already reads `m.meta.name` the V4 way.
 *
 * It then called `_updateColorbar`, a function defined nowhere in the repo, so
 * every invocation ended in a ReferenceError.
 */
describe('server chip-temperature colouring', () => {
  function withResults(): THREE.Mesh {
    const rack: StubModel = {
      uid: 'r_1',
      kind: 'rack',
      meta: { name: 'Rack_1', enabled: true },
      geometry: {},
    }
    ;(rack as unknown as Bag).constructions = {
      servers: { s_1: { uid: 'srv_a', meta: { name: 'Server_1', enabled: true } } },
    }
    models = [rack]
    ;(window as unknown as Bag).state = {
      liquidResults: {
        chip_temp_K: [[300, 320]],
        rack_summaries: [{ rack_id: 'Rack_1', servers: [{ server_id: 'Server_1' }] }],
      },
    }
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshBasicMaterial({ color: 0x123456 }),
    )
    mesh.userData = { modelId: 'r_1', equipmentId: 'srv_a' }
    globalGroup.add(mesh)
    return mesh
  }

  function toggle(on: boolean) {
    ;(window as unknown as { _toggleServerTemperatureColoring(v: boolean): void })
      ._toggleServerTemperatureColoring(on)
  }

  it('recolours the server mesh, and does not throw on the way out', () => {
    const mesh = withResults()
    const before = (mesh.material as THREE.MeshBasicMaterial).color.getHex()
    expect(() => toggle(true)).not.toThrow()
    expect((mesh.material as THREE.MeshBasicMaterial).color.getHex()).not.toBe(before)
  })

  it('puts the original colour back when switched off', () => {
    const mesh = withResults()
    const before = (mesh.material as THREE.MeshBasicMaterial).color.getHex()
    toggle(true)
    expect(() => toggle(false)).not.toThrow()
    expect((mesh.material as THREE.MeshBasicMaterial).color.getHex()).toBe(before)
  })

  it('leaves a mesh belonging to another rack alone', () => {
    const mesh = withResults()
    mesh.userData.modelId = 'r_2'
    const before = (mesh.material as THREE.MeshBasicMaterial).color.getHex()
    toggle(true)
    expect((mesh.material as THREE.MeshBasicMaterial).color.getHex()).toBe(before)
  })
})

describe('reroutePipesForJunction', () => {
  it('moves the connected pipe’s endpoint onto the rotated port', () => {
    const j = junction()
    const p = pipe()
    models = [j, p]
    // A quarter turn about Z: the port at +0.3x swings to +0.3y.
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI / 2)
    ;(j.cooling as Bag).quaternion = { x: q.x, y: q.y, z: q.z, w: q.w }

    reroute('j_1')

    const end = (p.geometry.waypoints as { x: number; y: number; z: number }[])[1]!
    expect(end.x).toBeCloseTo(1, 6)
    expect(end.y).toBeCloseTo(1.5, 6)
    expect(end.z).toBeCloseTo(1, 6)
  })

  it('moves the START waypoint when the pipe starts at the port', () => {
    const j = junction()
    const p = pipe()
    p.cooling = { connection: { startPortId: 'j_1.p2', endPortId: '' } }
    models = [j, p]
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI)
    ;(j.cooling as Bag).quaternion = { x: q.x, y: q.y, z: q.z, w: q.w }

    reroute('j_1')

    const start = (p.geometry.waypoints as { x: number; y: number; z: number }[])[0]!
    expect(start.x).toBeCloseTo(0.7, 6)
    expect(start.y).toBeCloseTo(1.2, 6)
  })

  it('leaves a port with no pipe alone', () => {
    const j = junction()
    ;(j.cooling as Bag).ports = [{ id: 'p3', offset: { x: 0, y: 0.3, z: 0 }, connectedPipeId: null }]
    const p = pipe()
    models = [j, p]

    reroute('j_1')

    expect((p.geometry.waypoints as { x: number }[])[1]!.x).toBeCloseTo(1.3, 6)
  })
})
