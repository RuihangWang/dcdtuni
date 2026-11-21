import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import * as store from '../src/projectStore'
import { serializeProjectState, readProjectFileText } from '../src/io/projectFiles'
import { transientOf } from '../src/transient/model'
import { setGroupMoveEnabled } from '../src/interaction/transformToolToggle'
import { syncCasePathFromServer } from '../src/run/caseDir'
import { updateCpCuPair } from '../src/liquid/liquidColdPlates'
import { updatePatchPhysics } from '../src/geometry/patchPhysics'
import type { CfdProject } from '../src/schema/cfdproj'

const CASE = resolve(process.cwd(), '../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json')
type Server = { cooling: { power: number } }
const server = (p: CfdProject | null, rack = 'm_1', id = 'e_1') =>
  (p as any).constructions.racks[rack].constructions.servers[id] as Server
const track = (p: CfdProject | null, param: string, target: string) =>
  transientOf(p).tracks.find(t => t.param === param && t.target === target)!

function load() {
  const r = readProjectFileText(readFileSync(CASE, 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
}

describe('store with cfdproj v5 tracks', () => {
  beforeEach(load)

  it('loads live at t = 0 with the fields a v4 reader expects', () => {
    expect(store.playheadTime()).toBe(0)
    expect((store.getProject() as any).schemaVersion).toBe(5)
    expect(server(store.getProject()).cooling.power).toBe(1000)
    expect(track(store.getProject(), 'server.power', 'e_1').keyframes).toEqual([{ t: 0, value: 1000 }])
  })

  it('an edit off t = 0 becomes a keyframe; the playhead shows each time; undo restores', () => {
    store.edit('setTransient', '', d => { (d as any).transient.mode = 'transient'; (d as any).transient.durationS = 600 })
    store.setPlayhead(120)
    store.edit('setPower', 'e_1', d => { server(d).cooling.power = 1500 })
    expect(store.lastEditRejection()).toBeNull()
    expect(track(store.getProject(), 'server.power', 'e_1').keyframes).toEqual([{ t: 0, value: 1000 }, { t: 120, value: 1500, rampS: 0 }])
    expect(server(store.getProject()).cooling.power).toBe(1500)
    store.setPlayhead(60)
    expect(server(store.getProject()).cooling.power).toBe(1000)
    store.setPlayhead(300)
    expect(server(store.getProject()).cooling.power).toBe(1500)
    store.undo()
    expect(server(store.getProject()).cooling.power).toBe(1000)            // re-materialised at 300 s
    expect(track(store.getProject(), 'server.power', 'e_1').keyframes).toHaveLength(1)
    store.redo()
    expect(server(store.getProject()).cooling.power).toBe(1500)
  })

  it('undo/redo re-materialise the restored tracks at the current playhead', () => {
    store.edit('setTransient', '', d => { (d as any).transient.mode = 'transient'; (d as any).transient.durationS = 600 })
    store.setPlayhead(120)
    store.edit('setPower', 'e_1', d => { server(d).cooling.power = 1500 })
    store.setPlayhead(0)
    store.edit('setPower', 'e_1', d => { server(d).cooling.power = 900 })   // snapshot taken at t = 0 shows 1000
    store.setPlayhead(300)
    store.undo()
    expect(track(store.getProject(), 'server.power', 'e_1').keyframes[0]?.value).toBe(1000)
    expect(server(store.getProject()).cooling.power).toBe(1500)
    store.setPlayhead(0)
    store.redo()                                                             // snapshot taken at 300 s shows 1500
    expect(server(store.getProject()).cooling.power).toBe(900)
  })

  it('refuses geometry/static edits off t = 0 and leaves the project untouched', () => {
    store.edit('setTransient', '', d => { (d as any).transient.mode = 'transient' })
    store.setPlayhead(60)
    const before = store.getProject()
    const dirtyBefore = store.isDirty()
    store.edit('move', 'm_1', d => { (d as any).constructions.racks.m_1.geometry.position = { x: 9, y: 0, z: 9 } })
    expect(store.lastEditRejection()?.reason).toBe('static')
    expect(store.getProject()).toBe(before)
    expect(store.isDirty()).toBe(dirtyBefore)
  })

  it('project settings stay editable off t = 0: the case dir is not scene state', () => {
    store.edit('setTransient', '', d => { (d as any).transient.mode = 'transient'; (d as any).transient.durationS = 600 })
    store.setPlayhead(120)
    store.edit('applyDctwinCaseDir', '/40901', d => { (d as any).meta = { ...(d as any).meta, casePath: '/40901/case' } })
    expect(store.lastEditRejection()).toBeNull()
    expect((store.getProject() as any).meta.casePath).toBe('/40901/case')
  })

  it('snaps and clamps the playhead to the timeline', () => {
    store.edit('setTransient', '', d => { (d as any).transient.mode = 'transient'; (d as any).transient.durationS = 100; (d as any).transient.gridS = 10 })
    store.setPlayhead(37)
    expect(store.playheadTime()).toBe(40)
    store.setPlayhead(1e9)
    expect(store.playheadTime()).toBe(100)
  })

  it('folds a legacy in-place edit back into the tracks on notify', () => {
    server(store.getProject()).cooling.power = 777
    store.notifyLiveEdit('legacyPanel')
    expect(track(store.getProject(), 'server.power', 'e_1').keyframes[0]).toEqual({ t: 0, value: 777 })
  })

  it('saves stripped v5 and reopens to the same live values', () => {
    store.edit('setPower', 'e_1', d => { server(d).cooling.power = 1234 })
    const text = serializeProjectState(store.getProject()!)
    const saved = JSON.parse(text)
    expect(saved.schemaVersion).toBe(5)
    expect(saved.constructions.racks.m_1.constructions.servers.e_1.cooling.power).toBeUndefined()
    const r = readProjectFileText(text)
    if (!r.ok) throw new Error(r.error)
    store.loadProject(r.project, null)
    expect(server(store.getProject()).cooling.power).toBe(1234)
  })

  describe('freeze on every write path (T2a)', () => {
    const toTransientAt = (t: number) => {
      store.edit('setTransient', '', d => { (d as any).transient.mode = 'transient'; (d as any).transient.durationS = 600 })
      store.setPlayhead(t)
    }
    const rack = () => (store.getProject() as any).constructions.racks.m_1

    it('is not frozen at t = 0 or in a steady project', () => {
      expect(store.sceneFrozen()).toBe(false)
      store.setPlayhead(120)
      expect(store.sceneFrozen()).toBe(false)             // steady: the playhead cannot leave 0
      toTransientAt(120)
      expect(store.sceneFrozen()).toBe(true)
    })

    it('edit() reports a refusal and bumps rejectionSeq', () => {
      toTransientAt(120)
      const seq = store.rejectionSeq()
      expect(store.edit('move', 'm_1', d => { (d as any).constructions.racks.m_1.geometry.position = { x: 9, y: 0, z: 9 } })).toBe(false)
      expect(store.rejectionSeq()).toBe(seq + 1)
      expect(store.edit('setPower', 'e_1', d => { server(d).cooling.power = 1500 })).toBe(true)
    })

    it('an in-place scene change announced by notifyLiveEdit is reverted off t = 0', () => {
      toTransientAt(120)
      const before = JSON.stringify(rack().geometry)
      const seq = store.rejectionSeq()
      rack().geometry.position = { x: 9, y: 0, z: 9 }
      store.notifyLiveEdit('rotateModel', 'm_1')
      expect(JSON.stringify(rack().geometry)).toBe(before)
      expect(store.lastEditRejection()?.reason).toBe('static')
      expect(store.rejectionSeq()).toBe(seq + 1)
    })

    it('in-place display-only and tracked changes are kept off t = 0', () => {
      toTransientAt(120)
      rack().meta = { ...rack().meta, display: { opacity: 0.3 } }
      server(store.getProject()).cooling.power = 1700
      store.notifyLiveEdit('opacity', 'm_1')
      expect(store.lastEditRejection()).toBeNull()
      expect(rack().meta.display.opacity).toBe(0.3)
      expect(track(store.getProject(), 'server.power', 'e_1').keyframes).toEqual([{ t: 0, value: 1000 }, { t: 120, value: 1700, rampS: 0 }])
      // the accepted state is the new reference: a later refusal does not undo it
      rack().geometry.position = { x: 9, y: 0, z: 9 }
      store.notifyLiveEdit('rotateModel', 'm_1')
      expect(rack().meta.display.opacity).toBe(0.3)
      expect(server(store.getProject()).cooling.power).toBe(1700)
    })

    it('the reference follows undo and redo', () => {
      toTransientAt(120)
      store.edit('setPower', 'e_1', d => { server(d).cooling.power = 1500 })
      store.undo()
      rack().geometry.position = { x: 9, y: 0, z: 9 }
      store.notifyLiveEdit('rotateModel', 'm_1')
      expect(server(store.getProject()).cooling.power).toBe(1000)
      store.redo()
      expect(server(store.getProject()).cooling.power).toBe(1500)
    })

    it('mutateLive: scene drags do nothing while frozen, declared non-scene mutations still apply', () => {
      toTransientAt(120)
      const before = JSON.stringify(rack().geometry)
      store.mutateLive(p => { (p as any).constructions.racks.m_1.geometry.position = { x: 9, y: 0, z: 9 } })
      expect(JSON.stringify(rack().geometry)).toBe(before)
      store.mutateLive(p => { (p as any).meta = { ...(p as any).meta, casePath: '/elsewhere' } }, { scene: false })
      expect((store.getProject() as any).meta.casePath).toBe('/elsewhere')
      store.setPlayhead(0)
      store.mutateLive(p => { (p as any).constructions.racks.m_1.geometry.position = { x: 9, y: 0, z: 9 } })
      expect(rack().geometry.position).toEqual({ x: 9, y: 0, z: 9 })
    })

    it('the real tool-toggle and case-dir writers still work while frozen', () => {
      toTransientAt(120)
      setGroupMoveEnabled(['m_1'], true)
      expect(rack().moveTool?.enabled).toBe(true)
      syncCasePathFromServer('/40901/case', true)
      expect((store.getProject() as any).meta.casePath).toBe('/40901/case')
      expect(store.lastEditRejection()).toBeNull()
    })

    it('a cold-plate heat edit off t = 0 is a keyframe and keeps the preset; at t = 0 it re-types as before', () => {
      const pair = () => (server(store.getProject()) as any).liquidCooling.cpCuPairs[0]
      toTransientAt(120)
      updateCpCuPair('m_1', 'e_1', 0, 'Q_W', 180)
      expect(store.lastEditRejection()).toBeNull()
      expect(pair().Q_W).toBe(180)
      expect(pair().preset).toBe('cpu')
      expect(track(store.getProject(), 'cp.heat', 'e_1/0').keyframes.at(-1)).toEqual({ t: 120, value: 180, rampS: 0 })
      store.setPlayhead(0)
      updateCpCuPair('m_1', 'e_1', 0, 'Q_W', 90)
      expect(pair().preset).toBe('custom')
    })

    it('an ACU airflow edit off t = 0 is a keyframe; the return mirror follows only at t = 0', () => {
      const patch = (id: string) => Object.values((store.getProject() as any).constructions.acus.m_2.geometry.faces)
        .flatMap((f: any) => f.patches ?? []).find((p: any) => p.id === id).physics
      const returnFlow = () => Object.values((store.getProject() as any).constructions.acus.m_2.geometry.faces)
        .flatMap((f: any) => f.patches ?? []).filter((p: any) => p.physics?.role === 'return').map((p: any) => p.physics.volumeFlowRate)
      const before = returnFlow()
      toTransientAt(120)
      updatePatchPhysics('m_2', 'p_1', 'volumeFlowRate', 5)
      expect(store.lastEditRejection()).toBeNull()
      expect(patch('p_1').volumeFlowRate).toBe(5)
      expect(returnFlow()).toEqual(before)
      expect(track(store.getProject(), 'acu.airflow', 'p_1').keyframes.at(-1)).toEqual({ t: 120, value: 5, rampS: 0 })
      store.setPlayhead(0)
      updatePatchPhysics('m_2', 'p_1', 'volumeFlowRate', 6)
      expect(returnFlow()).not.toEqual(before)
    })

    it('at t = 0 an in-place scene change is accepted as before', () => {
      rack().geometry.position = { x: 9, y: 0, z: 9 }
      store.notifyLiveEdit('rotateModel', 'm_1')
      expect(store.lastEditRejection()).toBeNull()
      expect(rack().geometry.position).toEqual({ x: 9, y: 0, z: 9 })
    })
  })
})
