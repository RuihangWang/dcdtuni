// Timeline operations (transient design §2): each op edits the transient block
// of a draft and is refused whole, with the validator's reason, when the result
// would not be a valid timeline. Times snap to the grid.
import { setSimulationMode } from '../src/transient/simulationMode'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import { readProjectFileText } from '../src/io/projectFiles'
import { transientOf, type TransientBlock } from '../src/transient/model'
import {
  addKeyframe, applyTimelineOp, deleteKeyframe, keyframeTimes, moveKeyframe, setDuration,
  setKeyframe, setGrid, setStart, shiftTime, trackKeyOf, tryTimelineOp,
} from '../src/transient/trackEdits'

const block = (): TransientBlock => ({
  mode: 'transient', durationS: 600, gridS: 10,
  start: { kind: 'uniform', uniform: { airT_C: 22, waterT_C: 30, solidT_C: 40 } },
  tracks: [
    { modelUid: 'm_1', param: 'server.power', target: 'e_1', keyframes: [{ t: 0, value: 1000 }, { t: 120, value: 1500, rampS: 0 }, { t: 300, value: 800, rampS: 60 }] },
    { modelUid: 'm_1', param: 'server.power', target: 'e_2', keyframes: [{ t: 0, value: 500 }, { t: 120, value: 700, rampS: 0 }] },
  ],
})
const e1 = trackKeyOf({ modelUid: 'm_1', param: 'server.power', target: 'e_1' })

describe('timeline operations on a block', () => {
  it('lists the keyframe times of all tracks, t = 0 excluded', () => {
    expect(keyframeTimes(block())).toEqual([120, 300])
  })

  it('shifting a time moves every keyframe at it, snapped to the grid', () => {
    const r = tryTimelineOp(block(), shiftTime(120, 153))
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.block.tracks.map(t => t.keyframes.map(k => k.t))).toEqual([[0, 150, 300], [0, 150]])
  })

  it('refuses a shift past a neighbour, onto t = 0, or beyond the end', () => {
    expect(tryTimelineOp(block(), shiftTime(120, 310)).ok).toBe(false)
    expect(tryTimelineOp(block(), shiftTime(120, 0)).ok).toBe(false)
    expect(tryTimelineOp(block(), shiftTime(0, 50)).ok).toBe(false)
    expect(tryTimelineOp(block(), shiftTime(300, 700)).ok).toBe(false)
  })

  it('refuses a move that would squeeze the next ramp below its length', () => {
    // e_1's 300 s keyframe ramps from 240; its predecessor cannot move past 240
    expect(tryTimelineOp(block(), moveKeyframe(e1, 1, 230)).ok).toBe(true)
    const r = tryTimelineOp(block(), moveKeyframe(e1, 1, 250))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toMatch(/ramp/)
  })

  it('edits value and ramp within the registry bounds and the gap', () => {
    const ok = tryTimelineOp(block(), setKeyframe(e1, 2, { value: 900, rampS: 180 }))
    expect(ok.ok && ok.block.tracks[0]!.keyframes[2]).toEqual({ t: 300, value: 900, rampS: 180 })
    expect(tryTimelineOp(block(), setKeyframe(e1, 2, { rampS: 190 })).ok).toBe(false)   // gap is 180
    expect(tryTimelineOp(block(), setKeyframe(e1, 1, { value: -5 })).ok).toBe(false)     // power >= 0
    expect(tryTimelineOp(block(), setKeyframe(e1, 0, { rampS: 10 })).ok).toBe(false)     // t = 0 has no ramp
  })

  it('adds a keyframe in order and deletes any but the first', () => {
    const add = tryTimelineOp(block(), addKeyframe(e1, 204, 1200))
    expect(add.ok && add.block.tracks[0]!.keyframes.map(k => k.t)).toEqual([0, 120, 200, 300])
    expect(tryTimelineOp(block(), addKeyframe(e1, 120, 1)).ok).toBe(false)   // already a keyframe there
    const del = tryTimelineOp(block(), deleteKeyframe(e1, 1))
    expect(del.ok && del.block.tracks[0]!.keyframes.map(k => k.t)).toEqual([0, 300])
    expect(tryTimelineOp(block(), deleteKeyframe(e1, 0)).ok).toBe(false)
  })

  it('shortening the duration past keyframes needs trim, which drops them', () => {
    const r = tryTimelineOp(block(), setDuration(200, { trim: false }))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toBe('trim')
    const t = tryTimelineOp(block(), setDuration(200, { trim: true }))
    expect(t.ok && t.block.durationS).toBe(200)
    expect(t.ok && t.block.tracks[0]!.keyframes.map(k => k.t)).toEqual([0, 120])
  })

  it('sets the start condition', () => {
    const r = tryTimelineOp(block(), setStart({ kind: 'steadyConverged' }))
    expect(r.ok && r.block.start.kind).toBe('steadyConverged')
  })
})

describe('applyTimelineOp commits one undoable edit and mirrors the duration', () => {
  beforeEach(() => {
    const txt = readFileSync(resolve(process.cwd(), '../../../case/Base_coldAisleIso/Base_coldAisleIso.cfdproj'), 'utf8')
    const r = readProjectFileText(txt)
    if (!r.ok) throw new Error(r.error)
    store.loadProject(r.project, null)
    setSimulationMode('transient', 'air')
  })

  it('adds a keyframe as one undo step; a refused op changes nothing', () => {
    const trk = transientOf(store.getProject()).tracks.find(t => t.param === 'server.power')!
    const depth = store.undoDepth()
    expect(applyTimelineOp('addKeyframe', addKeyframe(trackKeyOf(trk), 60, 999))).toBe(true)
    expect(store.undoDepth()).toBe(depth + 1)
    expect(transientOf(store.getProject()).tracks.find(t => t.target === trk.target)!.keyframes.at(-1)).toEqual({ t: 60, value: 999, rampS: 0 })
    const before = store.getProject()
    expect(applyTimelineOp('deleteFirst', deleteKeyframe(trackKeyOf(trk), 0))).toBe(false)
    expect(store.getProject()).toBe(before)
  })

  it('a duration change mirrors simulationConfig.endTimeSeconds', () => {
    expect(applyTimelineOp('setDuration', setDuration(900, { trim: false }))).toBe(true)
    expect(transientOf(store.getProject()).durationS).toBe(900)
    expect((store.getProject() as any).runDefaults.simulationConfig.endTimeSeconds).toBe(900)
  })
})

describe('timeline seek grid', () => {
  it('changes the grid without moving duration, keyframes or ramp starts', () => {
    const tr = block()
    tr.durationS = 600.25
    const frames = JSON.stringify(tr.tracks)
    const result = tryTimelineOp(tr, setGrid(.1))
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.block.gridS).toBe(.1)
    expect(result.block.durationS).toBe(600.25)
    expect(JSON.stringify(result.block.tracks)).toBe(frames)
    expect(tr.gridS).toBe(10)
  })
  it('refuses incompatible existing keyframe times or ramp starts without resampling', () => {
    const tr = block()
    const before = JSON.stringify(tr)
    expect(tryTimelineOp(tr, setGrid(7))).toEqual({
      ok: false, reason: 'grid change must preserve existing keyframe and ramp times',
    })
    tr.tracks[0]!.keyframes[2]!.rampS = 50
    // t=120 and t=300 align with 30; the ramp start at t=250 does not.
    expect(tryTimelineOp(tr, setGrid(30))).toEqual({
      ok: false, reason: 'grid change must preserve existing keyframe and ramp times',
    })
    tr.tracks[0]!.keyframes[2]!.rampS = 60
    expect(JSON.stringify(tr)).toBe(before)
  })
  it('requires a finite positive step', () => {
    for (const step of [0, -1, NaN, Infinity, -Infinity]) {
      expect(tryTimelineOp(block(), setGrid(step))).toEqual({ ok: false, reason: 'grid must be finite and > 0' })
    }
  })
  it('makes grid changes undoable, leaves CFD dt alone and normalizes only the playhead', () => {
    const txt = readFileSync(resolve(process.cwd(), '../../../case/Base_coldAisleIso/Base_coldAisleIso.cfdproj'), 'utf8')
    const result = readProjectFileText(txt)
    if (!result.ok) throw new Error(result.error)
    store.loadProject(result.project, null)
    setSimulationMode('transient', 'air')
    expect(applyTimelineOp('setGrid', setGrid(.1))).toBe(true)
    store.setPlayhead(.4)
    const cfg = structuredClone((store.getProject() as any).runDefaults.simulationConfig)
    const frames = JSON.stringify(transientOf(store.getProject()).tracks)
    const depth = store.undoDepth()
    expect(applyTimelineOp('setGrid', setGrid(1))).toBe(true)
    expect(store.playheadTime()).toBe(0)
    expect(store.undoDepth()).toBe(depth + 1)
    expect(JSON.stringify(transientOf(store.getProject()).tracks)).toBe(frames)
    expect((store.getProject() as any).runDefaults.simulationConfig).toEqual(cfg)
    store.undo()
    expect(transientOf(store.getProject()).gridS).toBe(.1)
  })
})
