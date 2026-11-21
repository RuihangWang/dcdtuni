import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'
import {
  createEquipmentAtSlot,
  isSlotDeleted,
  partitionAt,
  rackPartitions,
  setRackPartitionDeleted,
  setRackSelectionDeleted,
  setSlotDeleted,
  slotPartitionIndices,
  syncDeleteOpposite,
  syncDeleteTargets,
  voidRunPartitions,
  voidRuns,
} from '../src/geometry/rackSlots'

/**
 * Two pieces of arithmetic decide what these panels offer, and both are easy to
 * get off by one:
 *
 *   - which DIVIDER bounds a slot (the partition at index i sits between slots
 *     i and i+1, so a slot's bottom divider is one below its own index), and
 *   - which slots a range covers on one face, so a run of openings can be
 *     closed off as a group.
 *
 * Getting either wrong writes a partition into the wrong gap, which still
 * renders and still exports — as a solid where the airflow was meant to pass.
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

describe('slotPartitionIndices', () => {
  it('names the divider above and the divider below', () => {
    expect(slotPartitionIndices(5, 42)).toEqual({ top: 5, bottom: 4 })
  })

  it('gives the bottom slot no divider beneath it', () => {
    expect(slotPartitionIndices(0, 42)).toEqual({ top: 0, bottom: null })
  })

  it('gives the top slot no divider above it', () => {
    expect(slotPartitionIndices(41, 42)).toEqual({ top: null, bottom: 40 })
  })

  it('gives a one-slot rack no dividers at all', () => {
    expect(slotPartitionIndices(0, 1)).toEqual({ top: null, bottom: null })
  })
})

describe('voidRuns', () => {
  const open = (set: number[]) => (i: number) => set.includes(i)

  it('groups adjacent openings into one run', () => {
    expect(voidRuns([3, 4, 5, 6], open([3, 4, 5, 6]))).toEqual([{ start: 3, end: 6 }])
  })

  it('splits a run where a closed slot interrupts it', () => {
    expect(voidRuns([3, 4, 5, 6, 7], open([3, 4, 6, 7]))).toEqual([
      { start: 3, end: 4 },
      { start: 6, end: 7 },
    ])
  })

  it('finds nothing when the range is fully closed', () => {
    expect(voidRuns([3, 4, 5], open([]))).toEqual([])
  })

  it('closes a run that reaches the end of the selection', () => {
    expect(voidRuns([3, 4, 5], open([5]))).toEqual([{ start: 5, end: 5 }])
  })
})

describe('voidRunPartitions', () => {
  it('bounds a run with the divider below its first slot and above its last', () => {
    expect(voidRunPartitions({ start: 3, end: 6 }, 42)).toEqual({ top: 6, bottom: 2 })
  })

  it('offers no divider where the rack panel already closes the run', () => {
    expect(voidRunPartitions({ start: 0, end: 41 }, 42)).toEqual({ top: null, bottom: null })
  })
})

describe('partition writes', () => {
  let uid = ''
  beforeEach(() => {
    uid = loadWithRack()
  })

  it('starts with every divider present but deleted, one per gap', () => {
    const list = rackPartitions(rack(uid))
    const slots = Number((rack(uid).geometry as { slot: number }).slot)
    expect(list.length).toBe(slots - 1)
    expect(list.every((p) => p.deleted)).toBe(true)
  })

  it('adds a divider by clearing its deleted flag', () => {
    setRackPartitionDeleted(uid, 7, false)
    expect(partitionAt(rack(uid), 7)?.deleted).toBe(false)
  })

  it('removes a divider again', () => {
    setRackPartitionDeleted(uid, 7, false)
    setRackPartitionDeleted(uid, 7, true)
    expect(partitionAt(rack(uid), 7)?.deleted).toBe(true)
  })

  it('costs one undo step, so a divider can be taken back', () => {
    const before = store.undoDepth()
    setRackPartitionDeleted(uid, 7, false)
    expect(store.undoDepth()).toBe(before + 1)
    store.undo()
    expect(partitionAt(rack(uid), 7)?.deleted).toBe(true)
  })
})

describe('batch slot writes', () => {
  let uid = ''
  beforeEach(() => {
    uid = loadWithRack()
  })

  it('opens a whole range on one face in a single step', () => {
    const before = store.undoDepth()
    expect(setRackSelectionDeleted(uid, 'front', [2, 3, 4, 5], true)).toBe('ok')
    expect(store.undoDepth()).toBe(before + 1)
    for (const i of [2, 3, 4, 5]) expect(isSlotDeleted(rack(uid), 'front', i)).toBe(true)
    expect(isSlotDeleted(rack(uid), 'front', 6)).toBe(false)
  })

  it('closes a range again', () => {
    setRackSelectionDeleted(uid, 'front', [2, 3], true)
    setRackSelectionDeleted(uid, 'front', [2, 3], false)
    expect(isSlotDeleted(rack(uid), 'front', 2)).toBe(false)
  })

  it('refuses a range holding equipment instead of skipping those slots', () => {
    createEquipmentAtSlot(uid, 4, 'fanServer')
    expect(setRackSelectionDeleted(uid, 'front', [3, 4, 5], true)).toBe('occupied')
    expect(isSlotDeleted(rack(uid), 'front', 3)).toBe(false)
  })

  // A fresh rack has its BACK face blanked on every slot (`syncRackModelState`
  // seeds `deletedFaces.back` true), so the interesting states are reached by
  // closing the back first — which is also what a real project looks like where
  // servers are mounted.
  it('lists only the half-open slots as sync-delete targets', () => {
    setRackSelectionDeleted(uid, 'back', [1, 2, 3], false)
    setRackSelectionDeleted(uid, 'front', [2, 3], true)
    setSlotDeleted(uid, 'back', 3, true)
    expect(syncDeleteTargets(rack(uid), 'front', [1, 2, 3])).toEqual([2])
  })

  it('turns half-open slots into through-channels and leaves the rest alone', () => {
    setRackSelectionDeleted(uid, 'back', [1, 2, 3], false)
    setRackSelectionDeleted(uid, 'front', [2, 3], true)
    expect(syncDeleteOpposite(uid, 'front', [1, 2, 3])).toBe('ok')
    expect(isSlotDeleted(rack(uid), 'back', 2)).toBe(true)
    expect(isSlotDeleted(rack(uid), 'back', 3)).toBe(true)
    expect(isSlotDeleted(rack(uid), 'back', 1)).toBe(false)
  })

  it('does nothing when no slot in the range is half open', () => {
    setRackSelectionDeleted(uid, 'back', [1, 2], false)
    const before = store.undoDepth()
    expect(syncDeleteOpposite(uid, 'front', [1, 2])).toBe('missing')
    expect(store.undoDepth()).toBe(before)
  })
})

describe('createEquipmentAtSlot — a mounted server closes the slots it fills', () => {
  // The legacy `createRackEquipmentAtSlot` (STL_generate.html, after
  // `_rackInsertServer`) clears deletedFaces.front/back over the new server's
  // span, and `setSlotDeleted` refuses to open a slot under equipment: a slot
  // holding a server has no open face, the server IS the face. The React port
  // dropped the clearing, so a fan server added to a rack whose back is open
  // (the default rack) exported without its outlet face while the manifest
  // still bound `fan_outlet` -- the mesher then dropped the whole server and
  // its heat never reached the air.
  beforeEach(() => {
    store.loadProject(stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject, null)
  })

  it('clears both faces over the whole span, on an open-backed rack', () => {
    const uid = loadWithRack()
    for (let i = 8; i < 16; i += 1) expect(setSlotDeleted(uid, 'back', i, true)).toBe('ok')
    expect(setSlotDeleted(uid, 'front', 15, true)).toBe('ok')
    store.mutateLive((p) => {
      const target = store.findConstruction(p, uid)!.model as unknown as { defaultSpanUnits?: number }
      target.defaultSpanUnits = 4
    })
    expect(createEquipmentAtSlot(uid, 10, 'fanServer')).toBe('ok')
    for (let i = 10; i < 14; i += 1) {
      expect(isSlotDeleted(rack(uid), 'back', i)).toBe(false)
      expect(isSlotDeleted(rack(uid), 'front', i)).toBe(false)
    }
    // the slots around it keep what the user set
    expect(isSlotDeleted(rack(uid), 'back', 9)).toBe(true)
    expect(isSlotDeleted(rack(uid), 'back', 14)).toBe(true)
    expect(isSlotDeleted(rack(uid), 'front', 15)).toBe(true)
  })
})
