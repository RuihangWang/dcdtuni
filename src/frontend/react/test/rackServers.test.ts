import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import { addModel, getProject, loadProject } from '../src/projectStore'
import * as store from '../src/projectStore'
import { updateRackSetting } from '../src/geometry/rackGeometry'
import { addRackServerQuick, planQuickAdd, rackServerRows, rackSlotCount } from '../src/geometry/rackServers'

function loadWithRack(): string {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  loadProject(project, null)
  const { bucket, model } = createModel(getProject() as CfdProject, 'rack')
  return addModel(bucket, model)
}

function rack(uid: string): Construction {
  return store.findConstruction(getProject() as CfdProject, uid)!.model
}

const free = () => false

describe('planQuickAdd', () => {
  it('fills exactly the span asked for', () => {
    expect(planQuickAdd(42, 5, 3, free)).toEqual({ ok: true, indices: [5, 6, 7], clamped: false })
  })

  it('shortens a span that runs off the top instead of refusing it', () => {
    expect(planQuickAdd(10, 8, 4, free)).toEqual({ ok: true, indices: [8, 9], clamped: true })
  })

  it('refuses a start unit past the last slot', () => {
    expect(planQuickAdd(10, 10, 1, free)).toEqual({ ok: false, error: 'out-of-range' })
    expect(planQuickAdd(0, 0, 1, free)).toEqual({ ok: false, error: 'out-of-range' })
  })

  it('refuses when any slot in the span is taken', () => {
    expect(planQuickAdd(42, 4, 3, (i) => i === 6)).toEqual({ ok: false, error: 'occupied' })
  })

  it('treats a span below 1 as 1', () => {
    expect(planQuickAdd(42, 0, 0, free)).toEqual({ ok: true, indices: [0], clamped: false })
  })

  it('clamps a negative start to the bottom slot', () => {
    expect(planQuickAdd(42, -4, 2, free)).toEqual({ ok: true, indices: [0, 1], clamped: false })
  })
})

describe('addRackServerQuick', () => {
  let uid = ''
  beforeEach(() => {
    uid = loadWithRack()
  })

  /** The absolute setbacks of one server, as the document stores them. */
  function depthOf(serverUid: string) {
    const servers = (rack(uid).constructions as unknown as {
      servers: Record<string, Record<string, number>>
    }).servers
    const s = servers[serverUid]!
    return { inletOffset: Number(s.inletOffset), outletOffset: Number(s.outletOffset) }
  }

  it('mounts a server across the whole span and returns its uid', () => {
    const result = addRackServerQuick(uid, 11, 2, 'fanServer')
    expect(result).toMatchObject({ ok: true, clamped: false })
    const rows = rackServerRows(rack(uid))
    expect(rows.length).toBe(1)
    expect(result.ok && result.uid).toBe(rows[0]!.id)
    expect(rows[0]!.startUnit).toBe(11)
    expect(rows[0]!.spanUnits).toBe(2)
    expect(rows[0]!.endUnit).toBe(12)
    expect(rows[0]!.kind).toBe('fanServer')
  })

  it('creates a server on the rack plane, which is the empty-box default', () => {
    // The rack `createModel` builds has no row insets, so the plane is 0/0 and
    // the server spans the full 1.2 m depth.
    const result = addRackServerQuick(uid, 0, 2, 'fanServer')
    expect(result.ok).toBe(true)
    expect(depthOf((result as { uid: string }).uid)).toEqual({
      inletOffset: 0,
      outletOffset: 0,
    })
  })

  it('refuses to overlap a server that is already there', () => {
    addRackServerQuick(uid, 0, 4, 'fanServer')
    expect(addRackServerQuick(uid, 2, 2, 'nonFan')).toEqual({ ok: false, error: 'occupied' })
    expect(rackServerRows(rack(uid)).length).toBe(1)
  })

  it('STACKS a second server at taken U’s when the depth is disjoint', () => {
    // Spec §7.1: occupancy is conflict-aware — a 4U fan server in the front
    // half (z 0 -> 0.5) leaves the back half free at the very same U's.
    addRackServerQuick(uid, 0, 4, 'fanServer', { inletOffset: 0, outletOffset: 0.7 })
    const second = addRackServerQuick(uid, 0, 4, 'nonFan', {
      inletOffset: 0.6,
      outletOffset: 0.05,
    })
    expect(second).toMatchObject({ ok: true, clamped: false })
    const rows = rackServerRows(rack(uid))
    expect(rows.length).toBe(2)
    expect(depthOf((second as { uid: string }).uid)).toEqual({
      inletOffset: 0.6,
      outletOffset: 0.05,
    })
    // The normalizer kept the stack: nothing dropped, nothing reported.
    const cons = rack(uid).constructions as unknown as { serverConflicts?: unknown[] }
    expect(cons.serverConflicts).toEqual([])
  })

  it('refuses a stacked server whose depth intersects the one already there', () => {
    addRackServerQuick(uid, 0, 4, 'fanServer', { inletOffset: 0, outletOffset: 0.7 })
    // z 0.25 -> 1.2 runs straight through the first box (z 0 -> 0.5).
    expect(
      addRackServerQuick(uid, 0, 4, 'nonFan', { inletOffset: 0.25, outletOffset: 0 }),
    ).toEqual({ ok: false, error: 'occupied' })
    expect(rackServerRows(rack(uid)).length).toBe(1)
  })

  it('accepts touching faces — the epsilon boundary is a legal stack', () => {
    addRackServerQuick(uid, 0, 4, 'fanServer', { inletOffset: 0, outletOffset: 0.7 })
    // First box ends at z 0.5; this one starts exactly there.
    expect(
      addRackServerQuick(uid, 0, 4, 'nonFan', { inletOffset: 0.5, outletOffset: 0.4 }),
    ).toMatchObject({ ok: true })
  })

  it('refuses a depth that breaks the §3.1 budget instead of clamping it', () => {
    // 0.9 + 0.9 eats more than 90% of the 1.2 m rack.
    expect(addRackServerQuick(uid, 0, 2, 'fanServer', { inletOffset: 0.9, outletOffset: 0.9 })).toEqual(
      { ok: false, error: 'depth-range' },
    )
    expect(addRackServerQuick(uid, 0, 2, 'fanServer', { inletOffset: -0.1 })).toEqual({
      ok: false,
      error: 'depth-range',
    })
    expect(rackServerRows(rack(uid)).length).toBe(0)
  })

  it('fills one given half from the rack plane and keeps the other explicit', () => {
    // A single box typed in the panel: the missing half comes from
    // `defaultServerDepthOffsets`, not from zero.
    const result = addRackServerQuick(uid, 0, 2, 'fanServer', { inletOffset: 0.3 })
    expect(result).toMatchObject({ ok: true })
    expect(depthOf((result as { uid: string }).uid)).toEqual({
      inletOffset: 0.3,
      outletOffset: 0,
    })
  })

  it('lists servers in mounting order whatever order they were added in', () => {
    addRackServerQuick(uid, 20, 1, 'fanServer')
    addRackServerQuick(uid, 3, 2, 'nonFan')
    addRackServerQuick(uid, 10, 1, 'liquidServer')
    expect(rackServerRows(rack(uid)).map((r) => r.startUnit)).toEqual([3, 10, 20])
  })

  it('clamps a span that overruns the top of the rack', () => {
    updateRackSetting(uid, 'unitCount', 6)
    expect(addRackServerQuick(uid, 4, 5, 'fanServer')).toMatchObject({ ok: true, clamped: true })
    expect(rackServerRows(rack(uid))[0]!.spanUnits).toBe(2)
  })

  it('leaves the rack default span alone after the insert', () => {
    const before = (rack(uid) as unknown as { defaultSpanUnits?: number }).defaultSpanUnits
    addRackServerQuick(uid, 0, 4, 'fanServer')
    expect((rack(uid) as unknown as { defaultSpanUnits?: number }).defaultSpanUnits).toBe(before)
  })

  it('reports a missing rack rather than throwing', () => {
    expect(addRackServerQuick('no_such_uid', 0, 1, 'fanServer')).toEqual({
      ok: false,
      error: 'missing',
    })
  })
})

describe('rackSlotCount', () => {
  it('reads the V4 geometry.slot field', () => {
    const uid = loadWithRack()
    updateRackSetting(uid, 'unitCount', 18)
    expect(rackSlotCount(rack(uid))).toBe(18)
  })

  it('is zero for anything that is not a rack', () => {
    expect(rackSlotCount(null)).toBe(0)
  })
})
