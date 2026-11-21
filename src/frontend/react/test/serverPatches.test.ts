import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'
import { createEquipmentAtSlot } from '../src/geometry/rackSlots'
import {
  addServerPatch,
  convertServerPatchType,
  defaultServerPatch,
  deleteServerPatch,
  enforceServerPatchSafety,
  findServerPatch,
  nextServerPatchId,
  resetServerPatchDragWindow,
  serverFacePatches,
  setServerFaceColor,
  setServerPatchProp,
  type ServerPatch,
} from '../src/geometry/serverPatches'

/**
 * The clamp and the shape conversion are the parts worth pinning down: both
 * decide what an exported boundary looks like, and both are silent when they
 * are wrong — a patch hanging off the face still renders, and a patch carrying
 * both `w` and `r` still saves.
 */

function loadWithRack(): string {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  store.loadProject(project, null)
  const { bucket, model } = createModel(store.getProject() as CfdProject, 'rack')
  return store.addModel(bucket, model)
}

function rack(uid: string): Construction {
  return store.findConstruction(store.getProject() as CfdProject, uid)!.model
}

function firstServerId(uid: string): string {
  const servers = (rack(uid).constructions as { servers: Record<string, unknown> }).servers
  return Object.keys(servers)[0] as string
}

describe('enforceServerPatchSafety', () => {
  it('pulls a rectangle back inside the face rather than clipping it', () => {
    const patch: ServerPatch = { id: 'p_1', type: 'rect', x: 0.8, y: 0.9, w: 0.5, h: 0.4 }
    expect(enforceServerPatchSafety(patch)).toBe(true)
    expect(patch.x).toBeCloseTo(0.5)
    expect(patch.y).toBeCloseTo(0.6)
    expect(patch.w).toBeCloseTo(0.5)
  })

  it('gives a zero-size rectangle the 1% minimum', () => {
    const patch: ServerPatch = { id: 'p_1', type: 'rect', x: 0, y: 0, w: 0, h: -3 }
    enforceServerPatchSafety(patch)
    expect(patch.w).toBe(0.01)
    expect(patch.h).toBe(0.01)
  })

  it('keeps a circle inside the face on both axes', () => {
    const patch: ServerPatch = { id: 'p_1', type: 'circle', cx: 0.95, cy: 0.02, r: 0.3 }
    enforceServerPatchSafety(patch)
    expect(patch.cx).toBeCloseTo(0.7)
    expect(patch.cy).toBeCloseTo(0.3)
  })

  it('caps a radius at half the face, since a bigger circle cannot fit', () => {
    const patch: ServerPatch = { id: 'p_1', type: 'circle', cx: 0.5, cy: 0.5, r: 4 }
    enforceServerPatchSafety(patch)
    expect(patch.r).toBe(0.5)
  })

  it('reports no change when the patch already fits', () => {
    const patch: ServerPatch = { id: 'p_1', type: 'rect', x: 0.2, y: 0.1, w: 0.6, h: 0.8 }
    expect(enforceServerPatchSafety(patch)).toBe(false)
  })
})

describe('convertServerPatchType', () => {
  it('inscribes the circle in the rectangle it replaces', () => {
    const patch = convertServerPatchType(
      { id: 'p_1', type: 'rect', x: 0.2, y: 0.1, w: 0.6, h: 0.8 },
      'circle',
    )
    expect(patch.cx).toBeCloseTo(0.5)
    expect(patch.cy).toBeCloseTo(0.5)
    expect(patch.r).toBeCloseTo(0.3)
    // The rect keys must GO: a patch carrying both shapes exports whichever the
    // writer reads first.
    expect('x' in patch).toBe(false)
    expect('w' in patch).toBe(false)
  })

  it('circumscribes the rectangle around the circle it replaces', () => {
    const patch = convertServerPatchType(
      { id: 'p_1', type: 'circle', cx: 0.5, cy: 0.5, r: 0.2 },
      'rect',
    )
    expect(patch.x).toBeCloseTo(0.3)
    expect(patch.w).toBeCloseTo(0.4)
    expect('r' in patch).toBe(false)
  })

  it('clamps the result, so a circle at the edge yields a rectangle that fits', () => {
    const patch = convertServerPatchType(
      { id: 'p_1', type: 'circle', cx: 0.9, cy: 0.5, r: 0.1 },
      'rect',
    )
    expect((patch.x as number) + (patch.w as number)).toBeLessThanOrEqual(1)
  })

  it('leaves a patch alone when the type did not change', () => {
    const patch: ServerPatch = { id: 'p_1', type: 'rect', x: 0.2, y: 0.1, w: 0.6, h: 0.8 }
    expect(convertServerPatchType(patch, 'rect')).toEqual(patch)
  })
})

describe('defaultServerPatch', () => {
  it('is already inside the face', () => {
    const patch = defaultServerPatch('p_9')
    expect(enforceServerPatchSafety(patch)).toBe(false)
  })
})

describe('server patch writes', () => {
  let uid = ''
  let eid = ''
  beforeEach(() => {
    resetServerPatchDragWindow()
    uid = loadWithRack()
    createEquipmentAtSlot(uid, 4, 'fanServer')
    eid = firstServerId(uid)
  })

  it('adds an opening to a face that had none', () => {
    expect(serverFacePatches(rack(uid), eid, 'outlet').length).toBe(0)
    const id = addServerPatch(uid, eid, 'outlet')
    expect(id).toBeTruthy()
    const patches = serverFacePatches(rack(uid), eid, 'outlet')
    expect(patches.length).toBe(1)
    expect(patches[0]!.id).toBe(id)
    expect(patches[0]!.type).toBe('rect')
  })

  it('gives every opening its own id, so two patches are two boundaries', () => {
    const a = addServerPatch(uid, eid, 'outlet')
    const b = addServerPatch(uid, eid, 'outlet')
    expect(a).not.toBe(b)
    expect(nextServerPatchId(store.getProject())).not.toBe(b)
  })

  it('deletes only the opening asked for', () => {
    const a = addServerPatch(uid, eid, 'outlet') as string
    const b = addServerPatch(uid, eid, 'outlet') as string
    deleteServerPatch(uid, eid, 'outlet', a)
    const left = serverFacePatches(rack(uid), eid, 'outlet')
    expect(left.map((p) => p.id)).toEqual([b])
  })

  it('finds which server owns a patch, which is all the tree node says', () => {
    const id = addServerPatch(uid, eid, 'inlet') as string
    const hit = findServerPatch(rack(uid), 'inlet', id)
    expect(hit?.equipmentId).toBe(eid)
    expect(hit?.index).toBe(0)
    expect(findServerPatch(rack(uid), 'outlet', id)).toBeNull()
  })

  it('clamps a geometry write instead of storing a patch off the face', () => {
    const id = addServerPatch(uid, eid, 'outlet') as string
    setServerPatchProp(uid, eid, 'outlet', id, 'w', 1.5)
    const patch = serverFacePatches(rack(uid), eid, 'outlet')[0]!
    expect(patch.w).toBe(1)
    expect(patch.x).toBe(0)
  })

  it('switches shape through the same writer the select uses', () => {
    const id = addServerPatch(uid, eid, 'outlet') as string
    setServerPatchProp(uid, eid, 'outlet', id, 'type', 'circle')
    const patch = serverFacePatches(rack(uid), eid, 'outlet')[0]!
    expect(patch.type).toBe('circle')
    expect(patch.r).toBeCloseTo(0.3)
  })

  it('spends ONE undo step on a whole slider drag', () => {
    const id = addServerPatch(uid, eid, 'outlet') as string
    const before = store.undoDepth()
    for (const v of [0.3, 0.4, 0.5, 0.6]) setServerPatchProp(uid, eid, 'outlet', id, 'w', v)
    expect(store.undoDepth()).toBe(before + 1)
    store.undo()
    expect(serverFacePatches(rack(uid), eid, 'outlet')[0]!.w).toBeCloseTo(0.6)
  })

  it('undoes an add back to an empty face', () => {
    addServerPatch(uid, eid, 'outlet')
    store.undo()
    expect(serverFacePatches(rack(uid), eid, 'outlet').length).toBe(0)
  })

  it('stores the face colour where the renderer reads it', () => {
    setServerFaceColor(uid, eid, 'inlet', '#123456')
    const servers = (rack(uid).constructions as { servers: Record<string, { faces: Record<string, { color?: string }> }> })
      .servers
    expect(servers[eid]!.faces.inlet!.color).toBe('#123456')
  })
})
