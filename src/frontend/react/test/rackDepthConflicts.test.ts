import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { rackState, stateV4 } from '../src/legacy/cfdprojModules'
import { geometryPure } from '../src/legacy/geometryBuilder'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'

/**
 * The depth-conflict predicate: `findServerDepthConflicts(model)`.
 *
 * Since a U-slot may hold a STACK — a 5 U fan server recessed from the outlet
 * and a 1 U switch near the inlet, both at U16 — "is this slot taken" is no
 * longer the question. The question is whether two boxes occupy the same space,
 * and the answer is a pair of intervals:
 *
 *   slots intersect  AND  depth intervals intersect by more than 1e-6 m
 *
 * Both halves matter. Drop the slot half and two servers side by side in
 * different U's collide; drop the depth half and stacking becomes impossible
 * again, which is the whole point of the change. The epsilon is what lets two
 * boxes meet face to face: a switch whose inlet face sits exactly on the fan
 * server's outlet face is a legal stack, and refusing it would push users back
 * to a gap nobody can see.
 *
 * One predicate, three callers — the add-server panel's dry run,
 * `setServerDepthAbsolute`, and the normalizer's last-resort drop — so a
 * disagreement between them is a document the UI said was fine and the next
 * load silently deleted a server from. These tests hold the predicate to the
 * matrix, on documents that were NEVER synced: that is the shape a file edited
 * outside the editor arrives in, and the shape the normalizer has to judge.
 */

type Conflict = { aUid: string; bUid: string; slots: number[]; overlapM: number }

type WriteResult = { ok: true } | { ok: false; error: 'range' | 'overlap' | 'missing' }

type RackStateApi = {
  syncRackModelState(model: Construction): void
  findServerDepthConflicts(model: unknown): Conflict[]
  setServerDepthAbsolute(
    model: unknown,
    serverUid: string,
    inletOffset: number,
    outletOffset: number,
  ): WriteResult
  defaultServerDepthOffsets(model: unknown): { inletOffset: number; outletOffset: number }
  SERVER_DEPTH_EPSILON_M: number
  _rackDepthM(model: unknown): number
}

const rack = () => rackState() as unknown as RackStateApi
const pure = () =>
  geometryPure() as unknown as {
    getServerDepthRange(model: unknown, server: unknown): { z0: number; z1: number }
  }

/** The rack `createModel` builds is 1.2 m deep (EIA-310 cabinet, base.D). */
const D = 1.2

/**
 * Powers of two, so the overlap the predicate computes is EXACT. `1e-6` is not
 * representable in binary, so "an overlap of exactly the epsilon" cannot be
 * built out of decimals: these are the nearest values on either side of it that
 * are, which is what makes the boundary test a test rather than a coin flip.
 */
const BELOW_EPSILON = Math.pow(2, -21) // 4.77e-7 m
const ABOVE_EPSILON = Math.pow(2, -19) // 1.91e-6 m

type Seed = {
  uid: string
  kind?: 'fanServer' | 'nonFan' | 'liquidServer'
  startUnit: number
  spanUnits?: number
  /** Absolute setback from the rack inlet plane, metres. */
  inletOffset?: number
  /** Absolute setback from the rack outlet plane, metres. */
  outletOffset?: number
  /** The retired rack-relative pair, still legal as migration input. */
  extraInletOffset?: number
  extraOutletOffset?: number
}

let uid = ''

beforeEach(() => {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  store.loadProject(project, null)
  const created = createModel(store.getProject() as CfdProject, 'rack')
  uid = store.addModel(created.bucket, created.model)
})

function rackModel(): Construction {
  return store.findConstruction(store.getProject() as CfdProject, uid)!.model
}

/**
 * Write server records straight into the rack.
 *
 * `sync: false` leaves them exactly as written — no clamping, no placement —
 * which is what a conflict test needs: the predicate has to judge a document
 * that still holds BOTH interpenetrating records, and a sync would have deleted
 * one of them before the assertion ran.
 */
function seedServers(seeds: Seed[], sync = false) {
  store.mutateLive((project) => {
    const model = store.findConstruction(project, uid)!.model
    const cons = model.constructions as unknown as {
      servers: Record<string, Record<string, unknown>>
    }
    const servers: Record<string, Record<string, unknown>> = {}
    for (const s of seeds) {
      const record: Record<string, unknown> = {
        uid: s.uid,
        type: s.kind ?? 'fanServer',
        geometry: { slotPosition: s.startUnit, slotOccupation: s.spanUnits ?? 1 },
        cooling: {},
        constructions: { chips: {}, coldPlates: {} },
        meta: { name: s.uid, enabled: true },
      }
      if (s.inletOffset !== undefined) record.inletOffset = s.inletOffset
      if (s.outletOffset !== undefined) record.outletOffset = s.outletOffset
      if (s.extraInletOffset !== undefined) record.extraInletOffset = s.extraInletOffset
      if (s.extraOutletOffset !== undefined) record.extraOutletOffset = s.extraOutletOffset
      servers[s.uid] = record
    }
    cons.servers = servers
    if (sync) rack().syncRackModelState(model)
  })
}

const conflicts = () => rack().findServerDepthConflicts(rackModel())

/** A 5 U server filling the front half of the rack: z 0 -> 0.5. */
const frontHalf = (id: string, startUnit = 16, spanUnits = 5): Seed => ({
  uid: id,
  startUnit,
  spanUnits,
  inletOffset: 0,
  outletOffset: 0.7,
})

describe('findServerDepthConflicts — the slot half of the predicate', () => {
  it('finds nothing in a rack with one server', () => {
    seedServers([frontHalf('only')])
    expect(conflicts()).toEqual([])
  })

  it('finds nothing in an empty rack', () => {
    seedServers([])
    expect(conflicts()).toEqual([])
  })

  it('leaves two servers in different U ranges alone at the same depth', () => {
    // Same depth, no shared slot: two boxes side by side, not inside each other.
    seedServers([frontHalf('a', 16), frontHalf('b', 22)])
    expect(conflicts()).toEqual([])
  })

  it('leaves adjacent U ranges alone even when they touch', () => {
    seedServers([frontHalf('a', 16, 5), frontHalf('b', 21, 5)])
    expect(conflicts()).toEqual([])
  })
})

describe('findServerDepthConflicts — the depth half of the predicate', () => {
  it('reports two servers that share a U and interpenetrate', () => {
    seedServers([frontHalf('deep'), { uid: 'near', startUnit: 16, inletOffset: 0.25, outletOffset: 0 }])
    expect(conflicts()).toEqual([
      { aUid: 'deep', bUid: 'near', slots: [16], overlapM: 0.25 },
    ])
  })

  it('allows faces that touch', () => {
    // deep ends at z 0.5; near starts at z 0.5. A zero-thickness gap is still a
    // legal stack — this is the case a `>=` would wrongly refuse.
    seedServers([frontHalf('deep'), { uid: 'near', startUnit: 16, inletOffset: 0.5, outletOffset: 0 }])
    expect(conflicts()).toEqual([])
  })

  it('allows an overlap below the epsilon', () => {
    seedServers([
      frontHalf('deep'),
      { uid: 'near', startUnit: 16, inletOffset: 0.5 - BELOW_EPSILON, outletOffset: 0 },
    ])
    expect(conflicts()).toEqual([])
  })

  it('reports an overlap above the epsilon, and how deep it goes', () => {
    seedServers([
      frontHalf('deep'),
      { uid: 'near', startUnit: 16, inletOffset: 0.5 - ABOVE_EPSILON, outletOffset: 0 },
    ])
    const found = conflicts()
    expect(found).toHaveLength(1)
    expect(found[0]!.aUid).toBe('deep')
    expect(found[0]!.bUid).toBe('near')
    expect(found[0]!.overlapM).toBe(ABOVE_EPSILON)
    expect(rack().SERVER_DEPTH_EPSILON_M).toBe(1e-6)
    // The bracket, stated outright: the epsilon sits between the two cases above.
    expect(BELOW_EPSILON).toBeLessThan(rack().SERVER_DEPTH_EPSILON_M)
    expect(ABOVE_EPSILON).toBeGreaterThan(rack().SERVER_DEPTH_EPSILON_M)
  })

  it('allows the same depth in different U ranges', () => {
    seedServers([
      { uid: 'a', startUnit: 4, inletOffset: 0.25, outletOffset: 0.25 },
      { uid: 'b', startUnit: 9, inletOffset: 0.25, outletOffset: 0.25 },
    ])
    expect(conflicts()).toEqual([])
  })
})

describe('findServerDepthConflicts — the report', () => {
  it('lists only the slots the two U ranges actually share', () => {
    // deep covers U16-U20, near covers U19-U20: the overlap is real but two
    // slots wide, so it is ONE entry naming both — an entry per slot would read
    // as two problems, and one naming U16-U18 would point at empty space.
    seedServers([frontHalf('deep'), { uid: 'near', startUnit: 19, spanUnits: 2, inletOffset: 0.25, outletOffset: 0 }])
    expect(conflicts()).toEqual([
      { aUid: 'deep', bUid: 'near', slots: [19, 20], overlapM: 0.25 },
    ])
  })

  it('names the record placed first as aUid', () => {
    // Insertion order is the OPPOSITE of placement order: the deeper box wins
    // the slot because it is nearer the inlet plane, not because it was written
    // first. Without a defined order the badge would name a different culprit
    // on every reload.
    seedServers([{ uid: 'near', startUnit: 16, inletOffset: 0.25, outletOffset: 0 }, frontHalf('deep')])
    expect(conflicts()).toEqual([
      { aUid: 'deep', bUid: 'near', slots: [16], overlapM: 0.25 },
    ])
  })

  it('reports a record that interpenetrates two neighbours twice', () => {
    // a covers U19-U20 at z [0,0.4], b covers U19-U21 at z [0.5,0.9]: the two
    // survivors are depth-disjoint, so both stay. x covers U20-U21 at z
    // [0.3,0.6], which reaches into a on U20 and into b on U20 and U21 — one
    // entry per pair, each listing the slots THAT pair shares. A panel has to
    // name both neighbours, or the user fixes one and wonders about the next.
    seedServers([
      { uid: 'a', startUnit: 19, spanUnits: 2, inletOffset: 0, outletOffset: 0.8 },
      { uid: 'b', startUnit: 19, spanUnits: 3, inletOffset: 0.5, outletOffset: 0.3 },
      { uid: 'x', startUnit: 20, spanUnits: 2, inletOffset: 0.3, outletOffset: 0.6 },
    ])
    const found = conflicts()
    expect(found.map((c) => [c.aUid, c.bUid, c.slots])).toEqual([
      ['a', 'x', [20]],
      ['b', 'x', [20, 21]],
    ])
    expect(found[0]!.overlapM).toBeCloseTo(0.1, 12)
    expect(found[1]!.overlapM).toBeCloseTo(0.1, 12)
  })

  it('reports a dropped record against its survivor only', () => {
    // Three boxes at one U: a [0,0.5], b [0.25,1.2], c [0.75,1.2]. b goes, and
    // because it is gone it cannot also conflict with c — one entry, not two.
    seedServers([
      frontHalf('a'),
      { uid: 'b', startUnit: 16, inletOffset: 0.25, outletOffset: 0 },
      { uid: 'c', startUnit: 16, inletOffset: 0.75, outletOffset: 0 },
    ])
    expect(conflicts()).toEqual([{ aUid: 'a', bUid: 'b', slots: [16], overlapM: 0.25 }])
  })

  it('reads a depth still spelled with the retired rack-relative extras', () => {
    // A document from before the absolute fields: `near` carries only extras,
    // and the predicate has to see the same box the normalizer would build.
    seedServers([frontHalf('deep'), { uid: 'near', startUnit: 16, extraInletOffset: 0.25, extraOutletOffset: 0 }])
    const near = (rackModel().constructions as unknown as { servers: Record<string, Seed> }).servers['near']!
    expect(pure().getServerDepthRange(rackModel(), near)).toEqual({ z0: 0.25, z1: D })
    expect(conflicts()).toEqual([{ aUid: 'deep', bUid: 'near', slots: [16], overlapM: 0.25 }])
  })
})

describe('findServerDepthConflicts — purity', () => {
  it('writes nothing back', () => {
    seedServers([frontHalf('deep'), { uid: 'near', startUnit: 16, inletOffset: 0.25, outletOffset: 0 }])
    const before = JSON.stringify(rackModel())
    expect(conflicts()).toHaveLength(1)
    expect(conflicts()).toHaveLength(1)
    expect(JSON.stringify(rackModel())).toBe(before)
  })

  it('is empty for a model that is not a rack', () => {
    expect(rack().findServerDepthConflicts({ kind: 'box', constructions: { servers: {} } })).toEqual([])
    expect(rack().findServerDepthConflicts(null)).toEqual([])
  })

  it('is empty for a rack with no servers dict at all', () => {
    store.mutateLive((project) => {
      const model = store.findConstruction(project, uid)!.model
      delete (model.constructions as unknown as Record<string, unknown>).servers
    })
    expect(conflicts()).toEqual([])
  })
})

describe('setServerDepthAbsolute', () => {
  beforeEach(() => {
    seedServers([frontHalf('deep'), { uid: 'near', startUnit: 16, inletOffset: 0.6, outletOffset: 0.1 }], true)
  })

  const nearRecord = () =>
    (rackModel().constructions as unknown as {
      servers: Record<string, Record<string, number>>
    }).servers['near']!

  it('writes the pair where the caller can see it', () => {
    expect(rack().setServerDepthAbsolute(rackModel(), 'near', 0.75, 0.05)).toEqual({ ok: true })
    expect(nearRecord().inletOffset).toBeCloseTo(0.75, 12)
    expect(nearRecord().outletOffset).toBeCloseTo(0.05, 12)
  })

  it('refuses a move into the neighbour and writes nothing', () => {
    const before = { ...nearRecord() }
    expect(rack().setServerDepthAbsolute(rackModel(), 'near', 0.25, 0.05)).toEqual({
      ok: false,
      error: 'overlap',
    })
    expect(nearRecord()).toEqual(before)
  })

  it('accepts a move that only touches the neighbour', () => {
    // deep ends at z 0.5, so inlet 0.5 is face to face — the boundary case the
    // panel has to accept or the last millimetre of the rack is unusable.
    expect(rack().setServerDepthAbsolute(rackModel(), 'near', 0.5, 0.05)).toEqual({ ok: true })
    expect(nearRecord().inletOffset).toBeCloseTo(0.5, 12)
  })

  it('refuses a pair deeper than 90% of the rack', () => {
    const before = { ...nearRecord() }
    expect(rack().setServerDepthAbsolute(rackModel(), 'near', 0.9, 0.9)).toEqual({
      ok: false,
      error: 'range',
    })
    expect(nearRecord()).toEqual(before)
  })

  it('refuses a negative setback, a NaN and a missing server', () => {
    expect(rack().setServerDepthAbsolute(rackModel(), 'near', -0.01, 0)).toEqual({
      ok: false,
      error: 'range',
    })
    expect(rack().setServerDepthAbsolute(rackModel(), 'near', NaN, 0)).toEqual({
      ok: false,
      error: 'range',
    })
    expect(rack().setServerDepthAbsolute(rackModel(), 'no_such_server', 0.1, 0.1)).toEqual({
      ok: false,
      error: 'missing',
    })
    expect(rack().setServerDepthAbsolute(null, 'near', 0.1, 0.1)).toEqual({
      ok: false,
      error: 'missing',
    })
  })

  it('writes on a draft the caller owns, inside ONE undo entry', () => {
    // The writer takes the model it is given: no store, no checkpoint, no sync.
    // The caller's edit wrapper supplies all three, and this is the shape both
    // frontends call it in.
    const before = store.undoDepth()
    let result: WriteResult | null = null
    store.edit('test server depth', 'near', (draft) => {
      const model = store.findConstruction(draft, uid)!.model
      result = rack().setServerDepthAbsolute(model, 'near', 0.7, 0.15)
    })
    expect(result).toEqual({ ok: true })
    expect(store.undoDepth() - before).toBe(1)
    store.undo()
    expect(nearRecord().inletOffset).toBeCloseTo(0.6, 12)
  })
})
