import * as THREE from 'three'
import { describe, expect, it, beforeEach } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'
import { mergeSlotsIntoEquipment } from '../src/geometry/rackSlots'
import {
  commitServerPatchGeometry,
  enforceServerPatchSafety,
  findServerPatch,
  serverBoxFromScene,
  serverFaceLayout,
  serverFacePoint,
  serverRectMetres,
} from '../src/geometry/serverPatchSite'

/**
 * Where a SERVER's face patch lives, and how big its face is.
 *
 * A rack's patches are not the rack's: they belong to the servers inside it,
 * each with its own six faces and its own rectangle. That is why
 * `PATCH_DRAG_KINDS` (faceDrag.ts) leaves `rack` out — and why a vent on a
 * server cannot be dragged in the viewport, which is the last gap in the
 * round-4 list.
 *
 * ## Why the box comes off the built meshes
 *
 * The drag needs the server's face rectangle in RACK-LOCAL space. The shared
 * builder computes that box inline while building
 * (`serverBox` in geometry_scene.js's rack branch) and exports no function for
 * it, and the scene has no per-server group to read a transform from — measured
 * in the browser: a server patch mesh's parent chain is
 * `Mesh → Group{modelId} → Group → Group → Scene`, the rack root and no more.
 *
 * Recomputing the slot arithmetic here would be a second implementation of it,
 * which is the shape this port keeps finding broken. So the box is recovered
 * from the meshes the builder already produced: they are what is drawn and what
 * is exported, so it cannot drift from either.
 */

let rack: string

function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, rack)!.model
}

/** The server record the fixture makes, by walking the rack. */
function servers(): Record<string, { faces?: Record<string, { patches?: unknown[] }> }> {
  return (model().constructions as { servers?: Record<string, never> } | undefined)?.servers ?? {}
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const { bucket, model: created } = createModel(store.getProject() as CfdProject, 'rack')
  store.addModel(bucket, created)
  rack = created.uid
  mergeSlotsIntoEquipment(rack, 'front', [10, 11, 12, 13], 'fanServer')
})

describe('findServerPatch', () => {
  it('finds nothing on a rack whose servers have no patches yet', () => {
    expect(findServerPatch(model(), 'p_nope')).toBeNull()
  })

  it('finds a patch by id, and says which server and face it is on', () => {
    const [equipmentId] = Object.keys(servers())
    const face = servers()[equipmentId!]!.faces!.inlet as { patches: unknown[] }
    face.patches = [{ id: 'p_vent', type: 'rect', x: 0.1, y: 0.2, w: 0.3, h: 0.4 }]
    const site = findServerPatch(model(), 'p_vent')
    expect(site).toMatchObject({ equipmentId, faceId: 'inlet' })
    // The SAME object, not a copy: the drag mutates it sixty times a second and
    // a copy would move nothing on screen.
    expect(site!.patch).toBe(face.patches[0])
  })
})

describe('serverBoxFromScene', () => {
  /** A rack root holding one server's six face meshes, as the builder leaves it. */
  function rackRoot(equipmentId: string, box: THREE.Box3): THREE.Group {
    const root = new THREE.Group()
    root.userData = { modelId: rack }
    const corners = [
      [box.min.x, box.min.y, box.min.z],
      [box.max.x, box.max.y, box.max.z],
    ] as const
    for (const faceId of ['inlet', 'outlet']) {
      const geom = new THREE.BufferGeometry()
      geom.setAttribute(
        'position',
        new THREE.Float32BufferAttribute(corners.flatMap((c) => [...c]), 3),
      )
      const mesh = new THREE.Mesh(geom, new THREE.MeshBasicMaterial())
      mesh.userData = { modelId: rack, isRackEquipment: true, equipmentId, faceId }
      root.add(mesh)
    }
    // A mesh of ANOTHER server, which must not widen the box.
    const other = new THREE.Mesh(
      new THREE.BoxGeometry(9, 9, 9),
      new THREE.MeshBasicMaterial(),
    )
    other.userData = { modelId: rack, isRackEquipment: true, equipmentId: 'e_other', faceId: 'top' }
    root.add(other)
    return root
  }

  it('unions that server’s meshes, and only that server’s', () => {
    const box = new THREE.Box3(
      new THREE.Vector3(0.05, 1.2, -0.4),
      new THREE.Vector3(0.55, 1.4, 0.4),
    )
    const found = serverBoxFromScene(rackRoot('e_1', box), 'e_1')
    expect(found).not.toBeNull()
    expect(found!.min.toArray().map((v) => +v.toFixed(3))).toEqual([0.05, 1.2, -0.4])
    expect(found!.max.toArray().map((v) => +v.toFixed(3))).toEqual([0.55, 1.4, 0.4])
  })

  it('says so when that server has nothing built', () => {
    const box = new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(1, 1, 1))
    expect(serverBoxFromScene(rackRoot('e_1', box), 'e_missing')).toBeNull()
  })

  it('has nothing to say without a root', () => {
    expect(serverBoxFromScene(null, 'e_1')).toBeNull()
  })
})

describe('commitServerPatchGeometry', () => {
  function addVent(): { equipmentId: string; patchId: string } {
    const [equipmentId] = Object.keys(servers())
    const face = servers()[equipmentId!]!.faces!.inlet as { patches: unknown[] }
    face.patches = [{ id: 'p_vent', type: 'rect', x: 0.1, y: 0.2, w: 0.3, h: 0.4 }]
    return { equipmentId: equipmentId!, patchId: 'p_vent' }
  }

  function vent(): Record<string, number> {
    const site = findServerPatch(model(), 'p_vent')
    return site!.patch as unknown as Record<string, number>
  }

  it('writes into the project, as ONE undo entry', () => {
    const { patchId } = addVent()
    const depth = store.undoDepth()
    expect(commitServerPatchGeometry(rack, patchId, { x: 0.42, y: 0.11 })).toBe(true)
    expect(vent().x).toBeCloseTo(0.42, 6)
    expect(vent().y).toBeCloseTo(0.11, 6)
    expect(store.undoDepth()).toBe(depth + 1)
  })

  it('undoes back to where the drag started', () => {
    const { patchId } = addVent()
    commitServerPatchGeometry(rack, patchId, { x: 0.42 })
    store.undo()
    expect(vent().x).toBeCloseTo(0.1, 6)
  })

  it('refuses a patch that is not there rather than writing an empty entry', () => {
    addVent()
    const depth = store.undoDepth()
    expect(commitServerPatchGeometry(rack, 'p_ghost', { x: 1 })).toBe(false)
    expect(store.undoDepth()).toBe(depth)
  })
})

describe('serverFaceLayout', () => {
  /** One server's meshes, as `serverBoxFromScene` expects to find them. */
  function rootWith(equipmentId: string, box: THREE.Box3): THREE.Group {
    const root = new THREE.Group()
    const geom = new THREE.BufferGeometry()
    geom.setAttribute(
      'position',
      new THREE.Float32BufferAttribute(
        [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z],
        3,
      ),
    )
    const mesh = new THREE.Mesh(geom, new THREE.MeshBasicMaterial())
    mesh.userData = { isRackEquipment: true, equipmentId, faceId: 'inlet' }
    root.add(mesh)
    return root
  }

  const BOX = new THREE.Box3(new THREE.Vector3(0.05, 1.2, -0.4), new THREE.Vector3(0.55, 1.4, 0.4))

  it('hands the recovered box to the SHARED rect function, in its own shape', () => {
    // `getServerFaceRect` takes x0/x1/y0/y1/z0/z1 (geometry_pure.js) — the
    // rectangle arithmetic is the shared module's, and a second copy of it here
    // is what would drift from the drawn and exported geometry.
    let seen: Record<string, number> | null = null
    ;(window as unknown as { geometryPure?: unknown }).geometryPure = {
      getServerFaceRect: (box: Record<string, number>) => {
        seen = box
        return { origin: new THREE.Vector3(), right: new THREE.Vector3(1, 0, 0), up: new THREE.Vector3(0, 1, 0), w: 0.5, h: 0.2 }
      },
    }
    const layout = serverFaceLayout(rootWith('e_1', BOX), 'e_1', 'inlet')
    expect(layout).toMatchObject({ w: 0.5, h: 0.2 })
    // Close, not equal: the positions live in a Float32 attribute, so 0.05
    // comes back as 0.05000000074505806.
    const want: Record<string, number> = { x0: 0.05, x1: 0.55, y0: 1.2, y1: 1.4, z0: -0.4, z1: 0.4 }
    expect(Object.keys(seen!).sort()).toEqual(Object.keys(want).sort())
    for (const [key, value] of Object.entries(want)) {
      expect(seen![key], key).toBeCloseTo(value, 6)
    }
  })

  it('is null when that server has nothing built — no box, no drag', () => {
    expect(serverFaceLayout(rootWith('e_1', BOX), 'e_other', 'inlet')).toBeNull()
  })

  it('is null when the shared module is not loaded, rather than throwing', () => {
    delete (window as unknown as { geometryPure?: unknown }).geometryPure
    expect(serverFaceLayout(rootWith('e_1', BOX), 'e_1', 'inlet')).toBeNull()
  })
})

describe('fraction space', () => {
  // A server patch is stored as fractions of its face (legacy
  // `getPointOnServerFace` divides by the face's w/h, :6992). The first wiring
  // wrote metres into those fields: on a 0.6 m face the vent moved roughly the
  // right amount, which is the worst kind of wrong.
  const layout = { w: 0.6, h: 0.8 }

  it('a face point in metres becomes a fraction of the face', () => {
    expect(serverFacePoint({ x: 0.3, y: 0.2 }, layout)).toEqual({ x: 0.5, y: 0.25 })
  })

  it('refuses a degenerate face rather than dividing by zero', () => {
    expect(serverFacePoint({ x: 0.3, y: 0.2 }, { w: 0, h: 0.8 })).toBeNull()
  })

  it('a fractional rect becomes metres for the handles', () => {
    const m = serverRectMetres({ x: 0.75, y: 0, w: 0.25, h: 1 }, layout)
    expect(m.x).toBeCloseTo(0.45, 9)
    expect(m.y).toBeCloseTo(0, 9)
    expect(m.w).toBeCloseTo(0.15, 9)
    expect(m.h).toBeCloseTo(0.8, 9)
  })
})

describe('enforceServerPatchSafety', () => {
  it('keeps a rect inside the face — the legacy clamp (:6799-6808)', () => {
    const p: Record<string, unknown> = { type: 'rect', x: 0.9, y: -0.2, w: 0.25, h: 1.2 }
    expect(enforceServerPatchSafety(p)).toBe(true)
    expect(p).toMatchObject({ x: 0.75, y: 0, w: 0.25, h: 1 })
  })

  it('a full-height vent dragged upward stays at y = 0', () => {
    // Measured in the browser before the clamp: y drifted to 0.0092 with
    // h = 1, so the vent poked 0.9% out of the server's face.
    const p: Record<string, unknown> = { type: 'rect', x: 0.35, y: 0.0092, w: 0.25, h: 1 }
    expect(enforceServerPatchSafety(p)).toBe(true)
    expect(p.y).toBe(0)
  })

  it('leaves a rect that fits alone, and says so', () => {
    const p: Record<string, unknown> = { type: 'rect', x: 0.1, y: 0.1, w: 0.3, h: 0.4 }
    expect(enforceServerPatchSafety(p)).toBe(false)
    expect(p).toEqual({ type: 'rect', x: 0.1, y: 0.1, w: 0.3, h: 0.4 })
  })

  it('a circle keeps its radius in [0.01, 0.5] and its centre r from every edge', () => {
    const p: Record<string, unknown> = { type: 'circle', cx: 0.05, cy: 0.99, r: 0.7 }
    enforceServerPatchSafety(p)
    expect(p).toMatchObject({ r: 0.5, cx: 0.5, cy: 0.5 })
  })
})
