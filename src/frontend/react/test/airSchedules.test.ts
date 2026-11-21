// Air keyframes into cfdrun v3 (transient design §4, plan T4b): the manifest is
// compiled before and at every corner of the moving air tracks and the changed
// binding parameters become corner-row schedules; what a schedule cannot carry,
// and a track that changes nothing, are refused by name.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { beforeEach, describe, expect, it } from 'vitest'
import '../src/legacy/geometryBuilder'
import { stateV4 } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import { readProjectFileText } from '../src/io/projectFiles'
import { buildManifestV2, createCfdrunBuild } from '../src/legacy/cfdrunBuild'
import '../../shared/js/cfdrun_schema.js'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { addAirSchedules, airTimeline, type AirManifest } from '../src/transient/airSchedules'
import { liveProject, transientOf, type Track } from '../src/transient/model'
import { setSimulationMode } from '../src/transient/simulationMode'
import { addKeyframe, applyTimelineOp, setKeyframe, trackKeyOf } from '../src/transient/trackEdits'
import type { CfdProject, Construction } from '../src/schema/cfdproj'

const cfdrunSchema = () => (window as unknown as { cfdrunSchema: { validateManifest: (m: unknown) => string[] } }).cfdrunSchema
const CASE = resolve(process.cwd(), '../../../case/Base_coldAisleIso/Base_coldAisleIso.cfdproj')
const tracks = (param: string): Track[] => transientOf(store.getProject()).tracks.filter(t => t.param === param)
const power = (target: string) => tracks('server.power').find(t => t.target === target)!

beforeEach(() => {
  const r = readProjectFileText(readFileSync(CASE, 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  setSimulationMode('transient')                   // durationS 600, gridS 1, solver transient
})

/** A stand-in compile: one fan outlet reading e_3's power, plus a knob. */
function fake(extra: (p: CfdProject) => Record<string, unknown> = () => ({})) {
  return async (p: CfdProject): Promise<AirManifest> => {
    const model = store.findConstruction(p, 'm_1')!.model as unknown as { constructions: { servers: Record<string, { cooling: { power: number } }> } }
    return { schemaVersion: 2, boundaryBindings: {
      out: { patchName: 'S3_outlet', bcType: 'fan_outlet', parameters: { powerW: model.constructions.servers.e_3!.cooling.power, flowRateM3s: 0.5, ...extra(p) } },
    } }
  }
}

describe('the sampler', () => {
  it('writes corner rows: a step as two rows at one time, a ramp as its two ends', async () => {
    applyTimelineOp('a', addKeyframe(trackKeyOf(power('e_3')), 120, 400))
    applyTimelineOp('b', addKeyframe(trackKeyOf(power('e_3')), 300, 250))
    applyTimelineOp('c', setKeyframe(trackKeyOf(power('e_3')), 2, { rampS: 60 }))
    const source = store.getProject()!
    const timeline = airTimeline(source)!
    const compile = fake()
    const manifest = await compile(source)
    await addAirSchedules(manifest, source, timeline, compile)
    expect(manifest.schemaVersion).toBe(2)                   // the exporter sets the version
    expect(manifest.transient).toEqual({ durationS: 600, start: transientOf(source).start })
    const p = manifest.boundaryBindings.out!.parameters!
    expect(p.schedules).toEqual({ powerW: [[0, 150], [120, 150], [120, 400], [240, 400], [300, 250], [600, 250]] })
  })

  it('a step at or before 1 s still has its pre-step row (the sample sits clear of the evaluator\'s EPS)', async () => {
    applyTimelineOp('a', addKeyframe(trackKeyOf(power('e_3')), 1, 400))
    const source = store.getProject()!
    const compile = fake()
    const manifest = await compile(source)
    await addAirSchedules(manifest, source, airTimeline(source)!, compile)
    expect(manifest.boundaryBindings.out!.parameters!.schedules).toEqual({ powerW: [[0, 150], [1, 150], [1, 400], [600, 400]] })
  })

  it('refuses a change to a parameter a schedule cannot carry', async () => {
    applyTimelineOp('a', addKeyframe(trackKeyOf(power('e_3')), 120, 400))
    const source = store.getProject()!
    const compile = fake(p => ({ sigma: (store.findConstruction(p, 'm_1')!.model as any).constructions.servers.e_3.cooling.power }))
    await expect(addAirSchedules(await compile(source), source, airTimeline(source)!, compile)).rejects.toThrow(/S3_outlet\.sigma/)
  })

  it('refuses a moving track that changes nothing', async () => {
    applyTimelineOp('a', addKeyframe(trackKeyOf(power('e_4')), 120, 400))
    const source = store.getProject()!
    const compile = fake()
    await expect(addAirSchedules(await compile(source), source, airTimeline(source)!, compile)).rejects.toThrow(/server\.power \(e_4\) change nothing/)
  })

  it('a steady solver, or no moving air track, keeps the v2 export', () => {
    expect(airTimeline(store.getProject()!)).toBeNull()
    applyTimelineOp('a', addKeyframe(trackKeyOf(power('e_3')), 120, 400))
    expect(airTimeline(store.getProject()!)).not.toBeNull()
    setSimulationMode('steady')
    expect(airTimeline(store.getProject()!)).toBeNull()
  })
})

describe('the real exporter', () => {
  async function air(project: CfdProject) {
    project = liveProject(stateV4().normalizeStateV4(JSON.parse(JSON.stringify(project))) as unknown as CfdProject, 0)
    const globalGroup = new THREE.Group(), wrapperGroup = new THREE.Group(), root = new THREE.Group()
    root.add(wrapperGroup); wrapperGroup.add(globalGroup)
    const host: SceneHost = { globalGroup, wrapperGroup, registries: emptyRegistries(), selectedUid: () => null,
      project: () => project, webglAvailable: false, previewMode: () => false }
    const globals = window as unknown as { allModels?: () => Construction[] }
    const oldModels = globals.allModels
    globals.allModels = () => stateV4().allModels(project) as Construction[]
    const builder = createSceneBuilder(host)
    try {
      builder.rebuild()
      wrapperGroup.updateMatrixWorld(true)
      const api = createCfdrunBuild({ state: project, globalGroup,
        LENGTH_UNITS: { mm: { scale: .001, label: 'mm', step: .1, precision: 4 }, cm: { scale: .01, label: 'cm', step: .01, precision: 4 }, m: { scale: 1, label: 'm', step: .0001, precision: 4 } },
        _MESH_MODES: ['bodyFitted', 'cartesianLocalized', 'structured'],
        buildGroupedStlMap: (ids, leakage) => builder.api.buildGroupedStlMap(ids, leakage) })
      return await buildManifestV2(api, project, '2026-09-30T00:00:00.000Z') as unknown as AirManifest
    } finally {
      builder.clear()
      if (oldModels) globals.allModels = oldModels
      else delete globals.allModels
    }
  }

  it('exports cfdrun v4: one simulationConfig, the root, carrying room temperature and monitoring', async () => {
    setSimulationMode('steady')
    const m = await air(store.getProject()!) as AirManifest & { meshGeometry: Record<string, unknown>; simulationConfig: Record<string, unknown> }
    expect(m.schemaVersion).toBe(4)
    expect(m.transient).toBeUndefined()
    expect(m.meshGeometry.simulationConfig).toBeUndefined()
    expect(typeof m.simulationConfig.roomTemperatureC).toBe('number')
    expect(m.simulationConfig.monitoring).toBeTypeOf('object')
    expect(cfdrunSchema().validateManifest(m)).toEqual([])
  })

  it('a transient air run without keyframes still carries its timeline start and duration', async () => {
    // Found in the E8c gate: without moving air tracks the export had no transient
    // block, so the start condition (uniform field / steady converged) never reached the runner.
    const m = await air(store.getProject()!)
    expect(m.schemaVersion).toBe(4)
    expect(m.transient).toEqual({ durationS: 600, start: transientOf(store.getProject()!).start })
    expect(Object.values(m.boundaryBindings).some((b) => b.parameters?.schedules)).toBe(false)
    expect(cfdrunSchema().validateManifest(m)).toEqual([])
  })

  it('exports the timeline duration even when an imported solver mirror is stale, with and without moving keyframes', async () => {
    for (const moving of [false, true]) {
      if (moving) applyTimelineOp('step', addKeyframe(trackKeyOf(power('e_3')), 120, 400))
      const source = structuredClone(store.getProject()!)
      ;(source as any).runDefaults.simulationConfig.endTimeSeconds = 5
      const m = await air(source) as AirManifest & { simulationConfig: Record<string, unknown> }
      expect(m.simulationConfig.endTimeSeconds).toBe(600)
      expect((m.transient as any).durationS).toBe(600)
      expect((source as any).runDefaults.simulationConfig.endTimeSeconds).toBe(5)
    }
  })

  it('a server-power step reaches that server\'s fan bindings and nothing else', async () => {
    const before = await air(store.getProject()!)
    expect(before.schemaVersion).toBe(4)
    expect(before.transient).toBeDefined()                  // a transient air run carries its timeline
    applyTimelineOp('a', addKeyframe(trackKeyOf(power('e_3')), 120, 400))
    const after = await air(store.getProject()!)
    expect(after.schemaVersion).toBe(4)
    expect(after.transient).toBeDefined()
    const scheduled = Object.values(after.boundaryBindings).filter(b => b.parameters?.schedules)
    expect(scheduled.length).toBeGreaterThan(0)
    for (const b of scheduled) {
      expect(['fan_inlet', 'fan_outlet']).toContain(b.bcType)
      const rows = (b.parameters!.schedules as Record<string, [number, number][]>).powerW!
      expect(rows.map(r => r[0])).toEqual([0, 120, 120, 600])
      expect(rows[2]![1] / rows[1]![1]).toBeCloseTo(400 / 150, 9)
    }
    // Everything but the schedules, the version and the transient block is the v2 export.
    const strip = (m: AirManifest) => JSON.parse(JSON.stringify(m, (k, v) => (k === 'schedules' || k === 'transient' || k === 'schemaVersion' || k === 'contentHash') ? undefined : v))
    expect(strip(after)).toEqual(strip(before))
  })
})
