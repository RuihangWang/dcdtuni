import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { rackState, stateV4 } from '../src/legacy/cfdprojModules'
import { geometryPure } from '../src/legacy/geometryBuilder'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'

/**
 * The normalizer's half of stacked servers: what `syncRackModelState` does to a
 * rack whose U ranges overlap.
 *
 * Three things changed at once, and each has a failure mode that renders fine
 * and exports wrong:
 *
 *   1. **Depth is absolute.** A server carries `inletOffset` / `outletOffset`
 *      from the two rack FACES, not extras on top of the rack's row insets. The
 *      row sliders write the rack's insets only, so dragging them can no longer
 *      move — or rescale — a single server. That decoupling is structural: no
 *      code path adds the two together any more, and the regression below is the
 *      assertion that keeps it that way.
 *   2. **Occupancy is a depth interval list.** One server per U was the rule;
 *      now a record is dropped only when it interpenetrates one already placed,
 *      and the drop is written to `constructions.serverConflicts` instead of
 *      being silent. A silently deleted server is a missing heat load in the
 *      solve, discovered by reading a temperature field.
 *   3. **Old documents migrate on load.** The retired extras become absolute
 *      values (rack inset + extra, the sum the old derivation used) and are then
 *      deleted, and a server with no opacity inherits the rack's current one, so
 *      a project saved last month opens looking exactly the same.
 */

type Conflict = { aUid: string; bUid: string; slots: number[]; overlapM: number }

type RackStateApi = {
  syncRackModelState(model: Construction): void
  findServerDepthConflicts(model: unknown): Conflict[]
  defaultServerDepthOffsets(model: unknown): { inletOffset: number; outletOffset: number }
  SERVER_DEPTH_MAX_FRACTION: number
  _rackDepthM(model: unknown): number
}

type ServerView = { id: string; inletOffset: number; outletOffset: number; opacity: number }

type PureApi = {
  getRackServersForSlot(model: unknown, slotIndex: number): ServerView[]
  getRackEquipmentForSlot(model: unknown, slotIndex: number): ServerView | null
  getServerDepthRange(model: unknown, server: unknown): { z0: number; z1: number }
  getSlotDepthRange(model: unknown, slotIndex: number): { y0: number; y1: number }
  getPartitionDepthRange(model: unknown, index: number): { y0: number; y1: number; depth: number }
}

const rack = () => rackState() as unknown as RackStateApi
const pure = () => geometryPure() as unknown as PureApi

/** The rack `createModel` builds is 1.2 m deep, with no row insets. */
const D = 1.2

type Seed = {
  uid: string
  kind?: 'fanServer' | 'nonFan' | 'liquidServer'
  startUnit: number
  spanUnits?: number
  inletOffset?: number
  outletOffset?: number
  /** The retired rack-relative pair: migration input, deleted by the sync. */
  extraInletOffset?: number
  extraOutletOffset?: number
  opacity?: number
  power?: number
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

type ServerRecord = Record<string, unknown> & {
  uid: string
  inletOffset: number
  outletOffset: number
  geometry: { slotPosition: number; slotOccupation: number }
  meta: { name: string; display?: { opacity?: number } }
}

function servers(): Record<string, ServerRecord> {
  return (rackModel().constructions as unknown as { servers: Record<string, ServerRecord> }).servers
}

function conflicts(): Conflict[] {
  return (rackModel().constructions as unknown as { serverConflicts?: Conflict[] }).serverConflicts!
}

/** Row insets, the way the rack-detail U-slot Depth group writes them. */
function setRowDepth(inletOffset: number, outletOffset: number) {
  store.mutateLive((project) => {
    const model = store.findConstruction(project, uid)!.model
    const row = model as unknown as Record<string, unknown>
    row.inletOffset = inletOffset
    row.outletOffset = outletOffset
    rack().syncRackModelState(model)
  })
}

function setRackOpacity(opacity: number) {
  store.mutateLive((project) => {
    const model = store.findConstruction(project, uid)!.model
    const meta = model.meta as unknown as { display?: Record<string, unknown> }
    meta.display = { ...(meta.display ?? {}), opacity }
  })
}

/** Write server records straight into the rack, then normalise. */
function seedServers(seeds: Seed[], sync = true) {
  store.mutateLive((project) => {
    const model = store.findConstruction(project, uid)!.model
    const cons = model.constructions as unknown as { servers: Record<string, ServerRecord> }
    const next: Record<string, ServerRecord> = {}
    for (const s of seeds) {
      const record = {
        uid: s.uid,
        type: s.kind ?? 'fanServer',
        geometry: { slotPosition: s.startUnit, slotOccupation: s.spanUnits ?? 1 },
        cooling: s.power === undefined ? {} : { power: s.power },
        constructions: { chips: {}, coldPlates: {} },
        meta: {
          name: s.uid,
          enabled: true,
          ...(s.opacity === undefined ? {} : { display: { opacity: s.opacity } }),
        },
        inletOffset: s.inletOffset,
        outletOffset: s.outletOffset,
        extraInletOffset: s.extraInletOffset,
        extraOutletOffset: s.extraOutletOffset,
      } as unknown as ServerRecord
      // `undefined` values would survive a JSON round trip as absent keys only by
      // luck; drop them so a seed without a field really has no field.
      for (const key of Object.keys(record)) {
        if ((record as unknown as Record<string, unknown>)[key] === undefined) {
          delete (record as unknown as Record<string, unknown>)[key]
        }
      }
      next[s.uid] = record
    }
    cons.servers = next
    if (sync) rack().syncRackModelState(model)
  })
}

/** A 5 U fan server in the front half of the rack: z 0 -> 0.5. */
const frontHalf = (id: string, startUnit = 16, spanUnits = 5): Seed => ({
  uid: id,
  startUnit,
  spanUnits,
  inletOffset: 0,
  outletOffset: 0.7,
})

describe('syncRackModelState — occupancy is a depth interval list', () => {
  it('keeps two servers that share a U range at different depths', () => {
    seedServers([
      frontHalf('deep'),
      { uid: 'near', kind: 'nonFan', startUnit: 16, inletOffset: 0.6, outletOffset: 0.05 },
    ])
    expect(Object.keys(servers()).sort()).toEqual(['deep', 'near'])
    expect(conflicts()).toEqual([])
    expect(pure().getServerDepthRange(rackModel(), servers()['deep']!)).toEqual({ z0: 0, z1: 0.5 })
    expect(pure().getServerDepthRange(rackModel(), servers()['near']!)).toEqual({ z0: 0.6, z1: 1.15 })
  })

  it('keeps three servers stacked through the depth of one U', () => {
    seedServers([
      { uid: 'a', startUnit: 20, inletOffset: 0, outletOffset: 0.9 },
      { uid: 'b', startUnit: 20, inletOffset: 0.35, outletOffset: 0.6 },
      { uid: 'c', startUnit: 20, inletOffset: 0.7, outletOffset: 0.1 },
    ])
    expect(Object.keys(servers()).sort()).toEqual(['a', 'b', 'c'])
    expect(conflicts()).toEqual([])
  })

  it('drops the interpenetrating one and says which pair it was', () => {
    seedServers([frontHalf('deep'), { uid: 'near', startUnit: 16, inletOffset: 0.25, outletOffset: 0 }])
    expect(Object.keys(servers())).toEqual(['deep'])
    expect(conflicts()).toEqual([{ aUid: 'deep', bUid: 'near', slots: [16], overlapM: 0.25 }])
  })

  it('carries serverConflicts on a rack that has never conflicted', () => {
    // An absent field and an empty array are different reads for a badge: the
    // first says "not checked", the second says "clean".
    seedServers([frontHalf('deep')])
    expect('serverConflicts' in (rackModel().constructions as object)).toBe(true)
    expect(conflicts()).toEqual([])
  })

  it('clears the report once the document stops conflicting', () => {
    seedServers([frontHalf('deep'), { uid: 'near', startUnit: 16, inletOffset: 0.25, outletOffset: 0 }])
    expect(conflicts()).toHaveLength(1)
    seedServers([frontHalf('deep'), { uid: 'near', startUnit: 16, inletOffset: 0.6, outletOffset: 0 }])
    expect(conflicts()).toEqual([])
    expect(Object.keys(servers()).sort()).toEqual(['deep', 'near'])
  })

  it('agrees with the pure predicate about what survives', () => {
    // The normalizer's drop and the predicate the panels dry-run are one walk,
    // so a rack the predicate calls clean is a rack the sync keeps whole.
    const seeds = [
      frontHalf('deep'),
      { uid: 'mid', startUnit: 16, inletOffset: 0.4, outletOffset: 0.2 },
      { uid: 'near', startUnit: 16, inletOffset: 0.9, outletOffset: 0 },
    ]
    seedServers(seeds, false)
    const reported = rack().findServerDepthConflicts(rackModel())
    seedServers(seeds, true)
    expect(conflicts()).toEqual(reported)
    const dropped = reported.map((c) => c.bUid)
    expect(Object.keys(servers()).sort()).toEqual(
      seeds.map((s) => s.uid).filter((id) => !dropped.includes(id)).sort(),
    )
  })

  it('preserves the survivor’s physics through a drop next to it', () => {
    // The dict is rebuilt on every sync; a server that survives must come out
    // with its cooling, its meta and its faces, not a reshaped copy of them.
    seedServers([
      { ...frontHalf('deep'), power: 250 },
      { uid: 'near', startUnit: 16, inletOffset: 0.25, outletOffset: 0 },
    ])
    const deep = servers()['deep']!
    expect((deep as unknown as { cooling: { power: number } }).cooling.power).toBe(250)
    expect(deep.meta.name).toBe('deep')
    expect(deep.type).toBe('fanServer')
    expect(Object.keys(deep.faces as object).sort()).toEqual([
      'bottom',
      'inlet',
      'left',
      'outlet',
      'right',
      'top',
    ])
  })
})

describe('syncRackModelState — migrating the retired extras', () => {
  beforeEach(() => setRowDepth(0.1, 0.05))

  it('turns rack-relative extras into absolute setbacks', () => {
    // The old derivation was rack inset + extra; the migrated record has to sit
    // where the old one rendered, or opening a project moves every server.
    seedServers([{ uid: 'old', startUnit: 8, spanUnits: 2, extraInletOffset: 0.02, extraOutletOffset: 0.3 }])
    const old = servers()['old']!
    expect(old.inletOffset).toBeCloseTo(0.12, 12)
    expect(old.outletOffset).toBeCloseTo(0.35, 12)
  })

  it('deletes the extras so nothing can add the rack insets twice', () => {
    seedServers([{ uid: 'old', startUnit: 8, extraInletOffset: 0.02, extraOutletOffset: 0.3 }])
    const old = servers()['old']!
    expect(old).not.toHaveProperty('extraInletOffset')
    expect(old).not.toHaveProperty('extraOutletOffset')
  })

  it('prefers an absolute field already in the document', () => {
    // A half-migrated file: the extras are stale leftovers, and reading them
    // would move the server by the rack inset a second time.
    seedServers([
      { uid: 'half', startUnit: 8, inletOffset: 0.4, outletOffset: 0.2, extraInletOffset: 0.9, extraOutletOffset: 0.9 },
    ])
    const half = servers()['half']!
    expect(half.inletOffset).toBeCloseTo(0.4, 12)
    expect(half.outletOffset).toBeCloseTo(0.2, 12)
    expect(half).not.toHaveProperty('extraInletOffset')
  })

  it('gives a record with neither the rack’s own plane', () => {
    seedServers([{ uid: 'plain', startUnit: 8 }])
    const plain = servers()['plain']!
    expect(plain.inletOffset).toBeCloseTo(0.1, 12)
    expect(plain.outletOffset).toBeCloseTo(0.05, 12)
    expect(rack().defaultServerDepthOffsets(rackModel())).toEqual({
      inletOffset: 0.1,
      outletOffset: 0.05,
    })
  })

  it('replaces a non-finite absolute field rather than keeping it', () => {
    seedServers([{ uid: 'bad', startUnit: 8, extraInletOffset: 0.05 }])
    store.mutateLive((project) => {
      const model = store.findConstruction(project, uid)!.model
      const rec = (model.constructions as unknown as { servers: Record<string, ServerRecord> }).servers['bad']!
      rec.inletOffset = Number.NaN as unknown as number
      rack().syncRackModelState(model)
    })
    expect(servers()['bad']!.inletOffset).toBeCloseTo(0.1, 12)
  })
})

describe('syncRackModelState — per-server opacity', () => {
  it('backfills a missing opacity from the rack', () => {
    setRackOpacity(0.35)
    seedServers([{ uid: 'a', startUnit: 4 }, { uid: 'b', startUnit: 8, opacity: 0.8 }])
    expect(servers()['a']!.meta.display?.opacity).toBeCloseTo(0.35, 12)
    // A value the document carries is the user's, not the rack's.
    expect(servers()['b']!.meta.display?.opacity).toBeCloseTo(0.8, 12)
  })

  it('defaults to opaque when the rack carries no opacity of its own', () => {
    seedServers([{ uid: 'a', startUnit: 4 }])
    expect(servers()['a']!.meta.display?.opacity).toBe(1)
  })

  it('clamps a stored opacity into 0..1', () => {
    seedServers([{ uid: 'hot', startUnit: 4, opacity: 1.7 }, { uid: 'cold', startUnit: 8, opacity: -0.4 }])
    expect(servers()['hot']!.meta.display?.opacity).toBe(1)
    expect(servers()['cold']!.meta.display?.opacity).toBe(0)
  })

  it('survives a rack opacity change without following it', () => {
    // The split the whole opacity change is for: from the first sync onwards the
    // two knobs are independent, so fading the rack body must not fade servers.
    setRackOpacity(0.5)
    seedServers([{ uid: 'a', startUnit: 4 }])
    expect(servers()['a']!.meta.display?.opacity).toBeCloseTo(0.5, 12)
    setRackOpacity(0.15)
    expect(servers()['a']!.meta.display?.opacity).toBeCloseTo(0.5, 12)
  })
})

describe('syncRackModelState — the §3.1 clamps', () => {
  it('takes a negative setback to the rack face', () => {
    seedServers([{ uid: 'a', startUnit: 4, inletOffset: -0.3, outletOffset: -0.2 }])
    expect(servers()['a']!.inletOffset).toBe(0)
    expect(servers()['a']!.outletOffset).toBe(0)
  })

  it('rescales a pair deeper than 90% of the rack, keeping the server centred', () => {
    seedServers([{ uid: 'a', startUnit: 4, inletOffset: 0.9, outletOffset: 0.9 }])
    const a = servers()['a']!
    const budget = D * rack().SERVER_DEPTH_MAX_FRACTION
    expect(a.inletOffset + a.outletOffset).toBeCloseTo(budget, 12)
    // Equal setbacks in, equal setbacks out: the box shrank, it did not slide.
    expect(a.inletOffset).toBeCloseTo(a.outletOffset, 12)
    expect(a.inletOffset).toBeCloseTo(0.54, 12)
  })

  it('leaves a legal pair exactly as it was written', () => {
    seedServers([{ uid: 'a', startUnit: 4, inletOffset: 0.1, outletOffset: 0.2 }])
    expect(servers()['a']!.inletOffset).toBe(0.1)
    expect(servers()['a']!.outletOffset).toBe(0.2)
  })

  it('clamps a migrated pair too', () => {
    // The old code clamped the extras against the rack insets; the absolute
    // fields get the same 90% budget, whatever spelled them.
    setRowDepth(0.6, 0.6)
    seedServers([{ uid: 'a', startUnit: 4, extraInletOffset: 0.4, extraOutletOffset: 0.4 }])
    const a = servers()['a']!
    expect(a.inletOffset + a.outletOffset).toBeCloseTo(D * rack().SERVER_DEPTH_MAX_FRACTION, 12)
  })
})

describe('row-depth decoupling', () => {
  beforeEach(() => {
    seedServers([
      frontHalf('deep'),
      { uid: 'near', kind: 'nonFan', startUnit: 16, inletOffset: 0.6, outletOffset: 0.05 },
    ])
  })

  const depthFields = () =>
    JSON.stringify(
      Object.keys(servers())
        .sort()
        .map((id) => [id, servers()[id]!.inletOffset, servers()[id]!.outletOffset]),
    )

  it('leaves every server field byte-identical across a row-slider drag', () => {
    const before = depthFields()
    const drags: [number, number][] = [[0.05, 0.05], [0.2, 0.1], [0, 0]]
    for (const [inlet, outlet] of drags) {
      setRowDepth(inlet, outlet)
      expect(depthFields()).toBe(before)
    }
  })

  it('moves the structure the row sliders do own', () => {
    expect(pure().getSlotDepthRange(rackModel(), 16)).toEqual({ y0: 0, y1: D })
    setRowDepth(0.2, 0.1)
    // Compared field by field: 1.2 - 0.1 is not the double `1.1`, and a depth is
    // a millimetre-scale quantity where that difference is meaningless.
    const moved = pure().getSlotDepthRange(rackModel(), 16)
    expect(moved.y0).toBeCloseTo(0.2, 12)
    expect(moved.y1).toBeCloseTo(1.1, 12)
    // A partition spans the same structural plane, not the servers beside it.
    const partition = pure().getPartitionDepthRange(rackModel(), 16)
    expect(partition.y0).toBeCloseTo(0.2, 12)
    expect(partition.y1).toBeCloseTo(1.1, 12)
    expect(partition.depth).toBeCloseTo(0.9, 12)
  })

  it('reports the rack plane for an occupied slot and an empty one alike', () => {
    // getSlotDepthRange used to add the occupying server's setbacks, so two
    // slots of the same rack could disagree. Blanking panels and slot guides are
    // built from it; with a stack in the slot there is no single server to follow.
    setRowDepth(0.15, 0.15)
    expect(pure().getSlotDepthRange(rackModel(), 16)).toEqual(pure().getSlotDepthRange(rackModel(), 3))
    expect(pure().getSlotDepthRange(rackModel(), 16)).toEqual({ y0: 0.15, y1: 1.05 })
  })

  it('leaves a server’s own depth box alone while the row moves', () => {
    setRowDepth(0.3, 0.3)
    expect(pure().getServerDepthRange(rackModel(), servers()['near']!)).toEqual({ z0: 0.6, z1: 1.15 })
  })
})

describe('getRackServersForSlot', () => {
  it('returns every server covering the slot, nearest the inlet plane first', () => {
    // Seeded deepest-last on purpose, and unsynced, so the dict order is the
    // insertion order: the sort is what has to produce the reading order.
    seedServers(
      [
        { uid: 'far', startUnit: 16, inletOffset: 0.6, outletOffset: 0.05 },
        { uid: 'mid', startUnit: 16, spanUnits: 3, inletOffset: 0.3, outletOffset: 0.4 },
        frontHalf('deep'),
      ],
      false,
    )
    expect(pure().getRackServersForSlot(rackModel(), 16).map((v) => v.id)).toEqual(['deep', 'mid', 'far'])
  })

  it('returns only the servers that cover the slot asked for', () => {
    seedServers([frontHalf('deep', 16, 5), { uid: 'other', startUnit: 30, inletOffset: 0, outletOffset: 0 }])
    expect(pure().getRackServersForSlot(rackModel(), 19).map((v) => v.id)).toEqual(['deep'])
    expect(pure().getRackServersForSlot(rackModel(), 21)).toEqual([])
    expect(pure().getRackServersForSlot(rackModel(), 30).map((v) => v.id)).toEqual(['other'])
  })

  it('exposes the absolute offsets and the opacity on the view it returns', () => {
    setRackOpacity(0.4)
    seedServers([{ uid: 'a', startUnit: 4, inletOffset: 0.1, outletOffset: 0.2 }])
    const view = pure().getRackServersForSlot(rackModel(), 4)[0]!
    expect(view.inletOffset).toBeCloseTo(0.1, 12)
    expect(view.outletOffset).toBeCloseTo(0.2, 12)
    expect(view.opacity).toBeCloseTo(0.4, 12)
  })

  it('leaves getRackEquipmentForSlot answering with one server', () => {
    // Back-compat: the slot-status and blanking-panel queries ask "is anything
    // here", and the first record is as good an answer as any.
    seedServers([frontHalf('deep'), { uid: 'near', startUnit: 16, inletOffset: 0.6, outletOffset: 0.05 }])
    expect(pure().getRackEquipmentForSlot(rackModel(), 16)!.id).toBe('deep')
    expect(pure().getRackEquipmentForSlot(rackModel(), 3)).toBeNull()
  })

  it('is empty for a model that is not a rack', () => {
    expect(pure().getRackServersForSlot({ kind: 'box' }, 0)).toEqual([])
  })
})

describe('defaultServerDepthOffsets', () => {
  it('is the rack plane, which is where a server used to be created', () => {
    expect(rack().defaultServerDepthOffsets(rackModel())).toEqual({ inletOffset: 0, outletOffset: 0 })
    setRowDepth(0.12, 0.08)
    expect(rack().defaultServerDepthOffsets(rackModel())).toEqual({ inletOffset: 0.12, outletOffset: 0.08 })
  })

  it('is zero for a model with no insets at all', () => {
    expect(rack().defaultServerDepthOffsets({})).toEqual({ inletOffset: 0, outletOffset: 0 })
  })
})
