import { describe, expect, it } from 'vitest'
import {
  detailPanelShows,
  clampWidthRatio,
  DEFAULT_DOOR_PERFORATION,
  doorUid,
  effectiveDepth,
  findDoor,
  ratioToPct,
  setServerDepthAbsolute,
} from '../src/geometry/rackDetail'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { rackState, stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'
import * as store from '../src/projectStore'
import { mergeSlotsIntoEquipment } from '../src/geometry/rackSlots'

/** The shared model layer's writer, which the rack-detail panel is moving onto. */
type RackStateApi = {
  syncRackModelState(model: Construction): void
  setServerDepthAbsolute(
    model: unknown,
    serverUid: string,
    inletOffset: number,
    outletOffset: number,
  ): { ok: true } | { ok: false; error: string }
}

const rackApi = () => rackState() as unknown as RackStateApi

function rackWith(faces: unknown): Construction {
  return { uid: 'r1', kind: 'rack', meta: {}, geometry: { faces } } as unknown as Construction
}

describe('clampWidthRatio', () => {
  it('holds the legacy 0.2 - 0.95 band', () => {
    // Outside it the mounting rails leave the rack, and syncRackModelState
    // clamps anyway — doing it here keeps the slider and the model agreeing.
    expect(clampWidthRatio(0.05)).toBe(0.2)
    expect(clampWidthRatio(2)).toBe(0.95)
    expect(clampWidthRatio(0.8)).toBe(0.8)
  })

  it('rounds to whole percent for the slider', () => {
    expect(ratioToPct(0.8)).toBe(80)
    expect(ratioToPct(0.014)).toBe(20)
  })
})

describe('effectiveDepth', () => {
  it('subtracts both door setbacks', () => {
    expect(effectiveDepth(1.2, 0.1, 0.05)).toBeCloseTo(1.05, 12)
  })

  it('subtracts a server’s own extra setback on top', () => {
    expect(effectiveDepth(1.2, 0.1, 0.05, 0.2, 0.1)).toBeCloseTo(0.75, 12)
  })

  it('can go negative, and says so rather than clamping', () => {
    // A negative number is the signal that the offsets have eaten the rack;
    // clamping to 0 here would hide it until syncRackModelState rescaled both.
    expect(effectiveDepth(1.0, 0.6, 0.6)).toBeCloseTo(-0.2, 12)
  })
})

describe('findDoor', () => {
  it('matches on uid AND role', () => {
    const rack = rackWith([
      { uid: 'inletDoor', role: 'door', renderDoor: true },
      { uid: 'outletDoor', role: 'panel' },
    ])
    expect(findDoor(rack, 'inlet')?.renderDoor).toBe(true)
    // Right uid, wrong role: not a door.
    expect(findDoor(rack, 'outlet')).toBeNull()
  })

  it('returns null for the v3 object-shaped faces', () => {
    expect(findDoor(rackWith({ front: {}, back: {} }), 'inlet')).toBeNull()
  })

  it('returns null when there are no faces at all', () => {
    expect(findDoor(rackWith(undefined), 'inlet')).toBeNull()
    expect(findDoor(null, 'inlet')).toBeNull()
  })

  it('names the two door uids the way the runner expects', () => {
    expect(doorUid('inlet')).toBe('inletDoor')
    expect(doorUid('outlet')).toBe('outletDoor')
  })
})

describe('DEFAULT_DOOR_PERFORATION', () => {
  it('is a perforated door, not a solid plate', () => {
    // A door with no perforation block meshes as a WALL, which is the silent
    // failure this default exists to prevent.
    expect(DEFAULT_DOOR_PERFORATION.openAreaPct).toBe(64)
    expect(DEFAULT_DOOR_PERFORATION.shape).toBe('circle')
    expect(DEFAULT_DOOR_PERFORATION.holeDiameterMm).toBe(3)
  })
})

describe('detailPanelShows', () => {
  // The detail panel replaced the inspector for EVERY selection while detail
  // mode was on, so a patch picked inside the rack showed the rack's sliders
  // and looked unselectable. The legacy's panel is one branch of the MODEL
  // inspector (STL_generate.html:9242); a narrower selection never reaches it.
  it('is off outside detail mode', () => {
    expect(detailPanelShows(null, 'm_1', null)).toBe(false)
  })

  it('shows for the detail rack itself, and for an empty selection', () => {
    expect(detailPanelShows('m_1', 'm_1', null)).toBe(true)
    expect(detailPanelShows('m_1', 'm_1', { type: 'model' })).toBe(true)
    expect(detailPanelShows('m_1', null, null)).toBe(true)
  })

  it('yields to the inspector for a patch, a server or a slot inside the rack', () => {
    expect(detailPanelShows('m_1', 'm_1', { type: 'serverPatch' })).toBe(false)
    expect(detailPanelShows('m_1', 'm_1', { type: 'equipment' })).toBe(false)
    expect(detailPanelShows('m_1', 'm_1', { type: 'rackSlot' })).toBe(false)
  })

  it('yields to the inspector when another model is selected', () => {
    expect(detailPanelShows('m_1', 'm_2', null)).toBe(false)
  })
})

describe('setServerDepthAbsolute over a drag', () => {
  // The writer the two depth panels now share (spec §6). It wraps the SHARED
  // `setServerDepthAbsolute` — one definition of the §3.1 range rule and the
  // depth-conflict dry run — and carries the transaction: a live frame mutates
  // with no undo entry, a release commits ONE `store.edit`, and `dragStart`
  // puts the pre-drag setbacks back before that edit so the undo entry holds
  // the START of the gesture. Without the restore, `store.edit` would snapshot
  // the live project — already dragged — and Ctrl+Z would "restore" the drag.
  function loaded(): { uid: string; server: string } {
    store.loadProject(
      stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
      null,
    )
    const { bucket, model } = createModel(store.getProject() as CfdProject, 'rack')
    store.addModel(bucket, model)
    mergeSlotsIntoEquipment(model.uid, 'front', [4, 5], 'fanServer')
    const found = store.findConstruction(store.getProject() as CfdProject, model.uid)!.model
    const server = Object.keys((found.constructions as { servers: Record<string, unknown> }).servers)[0]!
    // Give the server a depth of its own, off the rack plane, so "the drag did
    // not move it" is a statement about something.
    store.mutateLive((project) => {
      const rack = store.findConstruction(project, model.uid)!.model
      rackApi().setServerDepthAbsolute(rack, server, 0.3, 0.2)
      rackApi().syncRackModelState(rack)
    })
    return { uid: model.uid, server }
  }

  const depthOf = (uid: string, server: string) => {
    const m = store.findConstruction(store.getProject() as CfdProject, uid)!.model
    const s = (m.constructions as { servers: Record<string, Record<string, number>> }).servers[server]!
    return { inletOffset: Number(s.inletOffset), outletOffset: Number(s.outletOffset) }
  }

  const recordOf = (uid: string, server: string) => {
    const m = store.findConstruction(store.getProject() as CfdProject, uid)!.model
    return (m.constructions as { servers: Record<string, object> }).servers[server]!
  }

  it('live frames write without an undo entry', () => {
    const { uid, server } = loaded()
    const before = store.undoDepth()
    for (const x of [0.02, 0.05, 0.08]) {
      expect(setServerDepthAbsolute(uid, server, x, x / 2, true)).toEqual({ ok: true })
    }
    expect(store.undoDepth()).toBe(before)
    expect(depthOf(uid, server)).toEqual({ inletOffset: 0.08, outletOffset: 0.04 })
  })

  it('commits ONE undo entry whose undo restores the pre-drag depth', () => {
    const { uid, server } = loaded()
    const start = depthOf(uid, server)
    expect(start).toEqual({ inletOffset: 0.3, outletOffset: 0.2 })
    const before = store.undoDepth()
    for (const x of [0.02, 0.05, 0.08]) setServerDepthAbsolute(uid, server, x, x / 2, true)
    expect(store.undoDepth()).toBe(before)
    const result = setServerDepthAbsolute(uid, server, 0.1, 0.05, false, {
      inletOffset: start.inletOffset,
      outletOffset: start.outletOffset,
    })
    expect(result).toEqual({ ok: true })
    expect(store.undoDepth() - before).toBe(1)
    expect(depthOf(uid, server)).toEqual({ inletOffset: 0.1, outletOffset: 0.05 })
    // The retired rack-relative keys never reach the document, so no reader can
    // add the rack's insets to a server's absolute setback a second time.
    expect(recordOf(uid, server)).not.toHaveProperty('extraInletOffset')
    expect(recordOf(uid, server)).not.toHaveProperty('extraOutletOffset')
    store.undo()
    expect(depthOf(uid, server)).toEqual(start)
  })

  it('without a drag start, a commit is a plain one-step edit', () => {
    const { uid, server } = loaded()
    const before = store.undoDepth()
    expect(setServerDepthAbsolute(uid, server, 0.03, 0.04, false)).toEqual({ ok: true })
    expect(store.undoDepth() - before).toBe(1)
    expect(depthOf(uid, server)).toEqual({ inletOffset: 0.03, outletOffset: 0.04 })
    store.undo()
    expect(depthOf(uid, server)).toEqual({ inletOffset: 0.3, outletOffset: 0.2 })
  })

  it('refuses a pair that breaks §3.1 rather than clamping it, and pushes nothing', () => {
    const { uid, server } = loaded()
    const start = depthOf(uid, server)
    const before = store.undoDepth()
    // Negative setbacks, and a pair eating more than 90% of the 1.2 m rack.
    expect(setServerDepthAbsolute(uid, server, -0.1, 0.2, false)).toEqual({
      ok: false,
      error: 'range',
    })
    expect(setServerDepthAbsolute(uid, server, 0.9, 0.9, false)).toEqual({
      ok: false,
      error: 'range',
    })
    expect(setServerDepthAbsolute(uid, server, 0.9, 0.9, true)).toEqual({
      ok: false,
      error: 'range',
    })
    expect(store.undoDepth()).toBe(before)
    expect(depthOf(uid, server)).toEqual(start)
  })

  it('refuses a depth that would interpenetrate another server at the same U', () => {
    const { uid, server } = loaded()
    // Park the first server in the front half — z 0 -> 0.5 — so the second one
    // has a disjoint back half to sit in.
    expect(setServerDepthAbsolute(uid, server, 0, 0.7, false)).toEqual({ ok: true })
    const start = depthOf(uid, server)
    // A second server sharing U4-U5, recessed near the outlet: z 0.6 -> 1.15.
    store.mutateLive((project) => {
      const rack = store.findConstruction(project, uid)!.model
      const servers = (rack.constructions as { servers: Record<string, unknown> }).servers
      servers['near'] = {
        uid: 'near',
        type: 'nonFan',
        geometry: { slotPosition: 4, slotOccupation: 2 },
        cooling: {},
        constructions: { chips: {}, coldPlates: {} },
        meta: { name: 'near', enabled: true },
        inletOffset: 0.6,
        outletOffset: 0.05,
      }
      rackApi().syncRackModelState(rack)
    })
    // Both records survived the sync: the stack is disjoint, so the normalizer
    // has nothing to drop and nothing to report.
    const serversNow = (store.findConstruction(store.getProject() as CfdProject, uid)!.model
      .constructions as { servers: Record<string, unknown> }).servers
    expect(Object.keys(serversNow).sort()).toEqual([server, 'near'].sort())
    const before = store.undoDepth()
    // Stretching the first server across the whole rack would run it through
    // `near`: refused, nothing written, no undo entry.
    expect(setServerDepthAbsolute(uid, server, 0, 0, false)).toEqual({
      ok: false,
      error: 'overlap',
    })
    expect(setServerDepthAbsolute(uid, server, 0, 0, true)).toEqual({
      ok: false,
      error: 'overlap',
    })
    expect(store.undoDepth()).toBe(before)
    expect(depthOf(uid, server)).toEqual(start)
    // A move that stays in the front half is accepted.
    expect(setServerDepthAbsolute(uid, server, 0.05, 0.75, false)).toEqual({ ok: true })
    expect(depthOf(uid, server)).toEqual({ inletOffset: 0.05, outletOffset: 0.75 })
  })

  it('answers missing for a server that is not there', () => {
    const { uid } = loaded()
    expect(setServerDepthAbsolute(uid, 'e_nope', 0.1, 0.1, false)).toEqual({
      ok: false,
      error: 'missing',
    })
    expect(setServerDepthAbsolute('m_nope', 'e_nope', 0.1, 0.1, true)).toEqual({
      ok: false,
      error: 'missing',
    })
  })
})
