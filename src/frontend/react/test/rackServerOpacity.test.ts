import { beforeEach, describe, expect, it } from 'vitest'
import type * as THREE from 'three'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'
import { mergeSlotsIntoEquipment } from '../src/geometry/rackSlots'
import {
  applyServerOpacityToMeshes,
  commitGroupOpacity,
  commitRackServersOpacity,
  commitRacksServersOpacity,
  commitServerOpacity,
  previewGroupOpacity,
  previewRackServersOpacity,
  previewRacksServersOpacity,
  previewServerOpacity,
  rackServersSliderPercent,
  racksServersSliderPercent,
  racksWithServers,
  resetServerOpacityPreview,
  serverOpacityOf,
  type ServerRecord,
} from '../src/geometry/serverOpacity'
import {
  applyOpacityToMeshes,
  commitModelOpacity,
  fadeMeshMaterials,
  type MeshLike,
  previewModelOpacity,
  resetOpacityPreview,
  setRenderRootLookup,
} from '../src/geometry/modelOpacity'

/**
 * The two opacity knobs are independent (spec 2026-09-17 §5): the rack slider
 * fades the body only, the server slider is a BATCH writer over the per-server
 * `meta.display.opacity` fields, and the equipment inspector writes one
 * server's own field. Storage is the server record — there is no rack-level
 * "server opacity" variable anywhere, and no drag may leave more than one undo
 * entry behind.
 */

let rack = ''
let serverA = ''
let serverB = ''

type Servers = Record<string, ServerRecord>

function model(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, rack)!.model
}

function servers(): Servers {
  return (model().constructions as unknown as { servers: Servers }).servers
}

function opacityOf(uid: string): number {
  return serverOpacityOf(servers()[uid])
}

function rackBodyOpacity(): number | undefined {
  return model().meta?.display?.opacity
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  resetServerOpacityPreview()
  resetOpacityPreview()
  const { bucket, model: created } = createModel(store.getProject() as CfdProject, 'rack')
  store.addModel(bucket, created)
  rack = created.uid
  mergeSlotsIntoEquipment(rack, 'front', [4, 5], 'fanServer')
  mergeSlotsIntoEquipment(rack, 'front', [10], 'nonFan')
  const ids = Object.keys(servers())
  serverA = ids[0]!
  serverB = ids[1]!
})

// ---------------------------------------------------------------------------
// The batch knob
// ---------------------------------------------------------------------------

describe('the batch slider writes every server in ONE undo entry', () => {
  it('takes one checkpoint however many frames the drag emits', () => {
    const before = store.undoDepth()
    for (const percent of [90, 70, 50, 30, 15]) previewRackServersOpacity(rack, percent)
    commitRackServersOpacity(rack, 15)
    expect(store.undoDepth() - before).toBe(1)
    expect(opacityOf(serverA)).toBeCloseTo(0.15, 12)
    expect(opacityOf(serverB)).toBeCloseTo(0.15, 12)
  })

  it('undoes the whole drag in one step', () => {
    previewRackServersOpacity(rack, 40)
    commitRackServersOpacity(rack, 40)
    store.undo()
    expect(opacityOf(serverA)).toBe(1)
    expect(opacityOf(serverB)).toBe(1)
  })

  it('is a plain one-step edit when only `change` fires', () => {
    // The automation contract's path: no preview ran.
    const before = store.undoDepth()
    commitRackServersOpacity(rack, 25)
    expect(store.undoDepth() - before).toBe(1)
    expect(opacityOf(serverA)).toBeCloseTo(0.25, 12)
    expect(opacityOf(serverB)).toBeCloseTo(0.25, 12)
  })

  it('never touches the rack body opacity — and the body knob never touches servers', () => {
    const bodyBefore = rackBodyOpacity()
    commitRackServersOpacity(rack, 30)
    // The batch writer's storage is the server records and nothing else.
    expect(rackBodyOpacity()).toBe(bodyBefore)
    commitModelOpacity(rack, 45)
    expect(rackBodyOpacity()).toBeCloseTo(0.45, 12)
    expect(opacityOf(serverA)).toBeCloseTo(0.3, 12)
    expect(opacityOf(serverB)).toBeCloseTo(0.3, 12)
  })
})

describe('the batch slider initial value', () => {
  it('is the common value when every server agrees', () => {
    expect(rackServersSliderPercent(model())).toEqual({ percent: 100, mixed: false })
    commitRackServersOpacity(rack, 55)
    expect(rackServersSliderPercent(model())).toEqual({ percent: 55, mixed: false })
  })

  it('is the MEAN plus the mixed flag when they do not', () => {
    commitServerOpacity(rack, serverB, 20)
    // 100 and 20 -> 60, exactly what a multi-selection of models would show.
    expect(rackServersSliderPercent(model())).toEqual({ percent: 60, mixed: true })
  })

  it('is 100 for a rack with no servers rather than NaN', () => {
    const { bucket, model: empty } = createModel(store.getProject() as CfdProject, 'rack')
    store.addModel(bucket, empty)
    const fresh = store.findConstruction(store.getProject() as CfdProject, empty.uid)!.model
    expect(rackServersSliderPercent(fresh)).toEqual({ percent: 100, mixed: false })
  })
})

// ---------------------------------------------------------------------------
// The per-server knob
// ---------------------------------------------------------------------------

describe('the per-server slider', () => {
  it('writes only its own server, leaving siblings untouched', () => {
    const before = store.undoDepth()
    previewServerOpacity(rack, serverA, 80)
    previewServerOpacity(rack, serverA, 60)
    commitServerOpacity(rack, serverA, 60)
    expect(store.undoDepth() - before).toBe(1)
    expect(opacityOf(serverA)).toBeCloseTo(0.6, 12)
    expect(opacityOf(serverB)).toBe(1)
    store.undo()
    expect(opacityOf(serverA)).toBe(1)
  })

  it('is a plain edit without a preview', () => {
    const before = store.undoDepth()
    commitServerOpacity(rack, serverB, 35)
    expect(store.undoDepth() - before).toBe(1)
    expect(opacityOf(serverB)).toBeCloseTo(0.35, 12)
    expect(opacityOf(serverA)).toBe(1)
  })

  it('ignores a server that is not in the rack', () => {
    const before = store.undoDepth()
    commitServerOpacity(rack, 'e_nope', 10)
    expect(store.undoDepth() - before).toBe(1) // the edit ran; it just found nothing
    expect(opacityOf(serverA)).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// The material fades: two complementary filters over one scene graph
// ---------------------------------------------------------------------------

class FakeMaterial {
  opacity = 1
  transparent = false
  depthWrite = true
  needsUpdate = false
  clone(): FakeMaterial {
    const m = new FakeMaterial()
    m.opacity = this.opacity
    m.transparent = this.transparent
    m.depthWrite = this.depthWrite
    return m
  }
}

type FakeMesh = {
  isMesh: boolean
  visible: boolean
  material: FakeMaterial
  userData: Record<string, unknown>
}

function mesh(userData: Record<string, unknown>): FakeMesh {
  return { isMesh: true, visible: true, material: new FakeMaterial(), userData }
}

function fakeRoot(children: FakeMesh[]): THREE.Object3D {
  const root = {
    traverse(cb: (o: unknown) => void) {
      cb(root)
      for (const child of children) cb(child)
    },
  }
  return root as unknown as THREE.Object3D
}

describe('the fade filters', () => {
  function scene() {
    const body = mesh({ modelId: rack })
    const a = mesh({ modelId: rack, equipmentId: serverA, isRackEquipment: true })
    const b = mesh({ modelId: rack, equipmentId: serverB, isRackEquipment: true })
    return { root: fakeRoot([body, a, b]), body, a, b }
  }

  it('model-root fade skips server meshes', () => {
    const { root, body, a, b } = scene()
    applyOpacityToMeshes(root, 30)
    expect(body.material.opacity).toBeCloseTo(0.3, 12)
    expect(a.material.opacity).toBe(1)
    expect(b.material.opacity).toBe(1)
  })

  it('server fade targets only the matching equipmentIds', () => {
    const { root, body, a, b } = scene()
    applyServerOpacityToMeshes(root, new Set([serverA]), 40)
    expect(a.material.opacity).toBeCloseTo(0.4, 12)
    expect(b.material.opacity).toBe(1)
    expect(body.material.opacity).toBe(1)
    // The batch set covers every server of the rack.
    applyServerOpacityToMeshes(root, new Set([serverA, serverB]), 55)
    expect(a.material.opacity).toBeCloseTo(0.55, 12)
    expect(b.material.opacity).toBeCloseTo(0.55, 12)
  })

  it('the preview seam fades through the render-root lookup, with the same split', () => {
    const { root, body, a, b } = scene()
    setRenderRootLookup((uid) => (uid === rack ? root : null))
    try {
      previewModelOpacity(rack, 25)
      expect(body.material.opacity).toBeCloseTo(0.25, 12)
      expect(a.material.opacity).toBe(1)
      commitModelOpacity(rack, 25)
      resetOpacityPreview()

      previewRackServersOpacity(rack, 45)
      expect(a.material.opacity).toBeCloseTo(0.45, 12)
      expect(b.material.opacity).toBeCloseTo(0.45, 12)
      // The body keeps the value its own knob left it at.
      expect(body.material.opacity).toBeCloseTo(0.25, 12)
      commitRackServersOpacity(rack, 45)
    } finally {
      setRenderRootLookup(null)
    }
  })

  it('an empty id set fades nothing', () => {
    const { root, a } = scene()
    applyServerOpacityToMeshes(root, new Set(), 10)
    expect(a.material.opacity).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// A multi-selection (a select-all in the model tree)
// ---------------------------------------------------------------------------

describe('a multi-selection of racks', () => {
  let other = ''
  let box = ''

  beforeEach(() => {
    const second = createModel(store.getProject() as CfdProject, 'rack')
    store.addModel(second.bucket, second.model)
    other = second.model.uid
    mergeSlotsIntoEquipment(other, 'front', [2], 'fanServer')
    const plain = createModel(store.getProject() as CfdProject, 'box')
    store.addModel(plain.bucket, plain.model)
    box = plain.model.uid
  })

  function find(uid: string): Construction {
    return store.findConstruction(store.getProject() as CfdProject, uid)!.model
  }

  function allServerOpacities(): number[] {
    return [rack, other].flatMap((uid) =>
      Object.values((find(uid).constructions as unknown as { servers: Servers }).servers).map(serverOpacityOf),
    )
  }

  it('offers the server knob for the racks that hold servers only', () => {
    expect(racksWithServers([find(rack), find(other), find(box)]).map((m) => m.uid)).toEqual([rack, other])
  })

  it('the group slider fades the models AND their servers in ONE undo entry', () => {
    const uids = [rack, other, box]
    const before = store.undoDepth()
    for (const percent of [80, 50, 20]) previewGroupOpacity(uids, [rack, other], percent)
    commitGroupOpacity(uids, [rack, other], 20)
    expect(store.undoDepth() - before).toBe(1)
    for (const uid of uids) expect(find(uid).meta?.display?.opacity).toBeCloseTo(0.2, 12)
    for (const v of allServerOpacities()) expect(v).toBeCloseTo(0.2, 12)
    store.undo()
    for (const uid of uids) expect(find(uid).meta?.display?.opacity ?? 1).toBe(1)
    for (const v of allServerOpacities()) expect(v).toBe(1)
  })

  it('the group slider is one plain edit when only `change` fires', () => {
    const before = store.undoDepth()
    commitGroupOpacity([rack, other], [rack, other], 35)
    expect(store.undoDepth() - before).toBe(1)
    for (const v of allServerOpacities()) expect(v).toBeCloseTo(0.35, 12)
  })

  it('the servers-only slider leaves the rack bodies alone, in one undo entry', () => {
    const before = store.undoDepth()
    for (const percent of [70, 40]) previewRacksServersOpacity([rack, other], percent)
    commitRacksServersOpacity([rack, other], 40)
    expect(store.undoDepth() - before).toBe(1)
    for (const v of allServerOpacities()) expect(v).toBeCloseTo(0.4, 12)
    expect(find(rack).meta?.display?.opacity ?? 1).toBe(1)
    expect(find(other).meta?.display?.opacity ?? 1).toBe(1)
  })

  it('the servers-only slider reads the mean, and says when the servers differ', () => {
    expect(racksServersSliderPercent([find(rack), find(other)])).toEqual({ percent: 100, mixed: false })
    commitRackServersOpacity(other, 40)
    // Two servers of `rack` at 100 and one of `other` at 40.
    expect(racksServersSliderPercent([find(rack), find(other)])).toEqual({ percent: 80, mixed: true })
  })
})

describe('the fade flags a program rebuild only when transparency flips', () => {
  // Opacity is a uniform; only `transparent` is in the shader program. A flag
  // on every slider step re-derived every program every frame of a drag.
  it('flags the first fade and the return to opaque, not the steps between', () => {
    const m = mesh({}) as unknown as MeshLike & { material: FakeMaterial }
    fadeMeshMaterials(m, 0.8)
    expect(m.material.needsUpdate).toBe(true)
    m.material.needsUpdate = false
    fadeMeshMaterials(m, 0.5)
    fadeMeshMaterials(m, 0.3)
    expect(m.material.needsUpdate).toBe(false)
    expect(m.material.opacity).toBeCloseTo(0.3, 12)
    fadeMeshMaterials(m, 1)
    expect(m.material.needsUpdate).toBe(true)
    expect(m.material.transparent).toBe(false)
  })
})
