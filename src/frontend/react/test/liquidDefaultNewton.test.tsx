import { readFileSync, writeFileSync } from 'node:fs'
import { gunzipSync } from 'node:zlib'
import { resolve } from 'node:path'
import { act, useSyncExternalStore } from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as experiments from '../src/liquid/liquidExperiment'
import * as dynamic from '../src/liquid/dynamicLiquidGraph'
import * as store from '../src/projectStore'
import { LiquidExperimentPanel } from '../src/components/LiquidExperimentPanel'
import { setSimulationMode } from '../src/transient/simulationMode'
import { transientOf } from '../src/transient/model'
import { checkProjectDocument, readProjectFileText, serializeProjectState } from '../src/io/projectFiles'
import { cfdrunExport } from '../src/legacy/cfdrunBuild'
import { schema } from '../src/legacy/cfdprojModules'
import { getLocale, setLocale, t } from '../src/i18n'
import type { CfdProject } from '../src/schema/cfdproj'

const mocks = vi.hoisted(() => ({
  create: vi.fn().mockResolvedValue(null),
  state: { session: null, frames: [], projectRevision: null, busy: false, error: null, connection: 'idle' },
}))
vi.mock('../src/liquid/liquidRuntimeClient', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/liquid/liquidRuntimeClient')>()),
  subscribeRuntime: () => () => {}, runtimeState: () => mocks.state, createRuntimeSession: mocks.create,
}))
vi.mock('../src/components/LiquidSolverPanel', () => ({ LiquidSolverPanel: () => null }))
const repo = resolve(process.cwd(), '../../..')
const proof = resolve(repo, 'docs/benchmarks/shinaide-product-2026-10-05/evidence/liquid-canonical-default-newton')
const canonical = () => JSON.parse(readFileSync(resolve(repo, 'case/verification/shinaide-legacy-projects/Shinaide_liquid.json'), 'utf8')) as CfdProject
const original = (name = 'Shinaide_liquid') => JSON.parse(gunzipSync(readFileSync(resolve(proof, 'original-' + name + '.cfdproj.gz'))).toString()) as CfdProject
const noExperiment = () => {
  const p = canonical() as any
  delete p.runDefaults.liquid.experiment
  return p as CfdProject
}
const loaded = (p: CfdProject) => {
  store.loadProject(p, null)
  return store.getProject()!
}
/**
 * Rendering compiles only the readiness checklist's read-only snapshot (one
 * 1 s sample, no parameter pack) and never the saved scenario; the project and
 * its experiment stay untouched, which each test checks separately.
 */
function readinessOnly(spy: { mock: { calls: unknown[][] } }): boolean {
  return spy.mock.calls.every(([, options]) => JSON.stringify(options) === JSON.stringify({ duration_s: 1, dt_s: 1 }))
}
let cleanup = () => {}
let previousLocale: ReturnType<typeof getLocale>
beforeEach(() => {
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
  previousLocale = getLocale(); setLocale('en-US')
})
afterEach(() => { cleanup(); cleanup = () => {}; vi.restoreAllMocks(); mocks.create.mockClear(); setLocale(previousLocale) })
async function panel(p: CfdProject, assertPureRender = false) {
  loaded(p)
  const beforeRender=serializeProjectState(store.getProject()!), beforeRevision=store.revision()
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  function Harness() {
    useSyncExternalStore(store.subscribe, store.revision)
    return <LiquidExperimentPanel project={store.getProject()} />
  }
  cleanup = () => { act(() => root.unmount()); host.remove() }
  await act(async () => root.render(<Harness />))
  if(assertPureRender) {
    expect(serializeProjectState(store.getProject()!)).toBe(beforeRender)
    expect(store.revision()).toBe(beforeRevision)
  }
  return host
}
async function click(host: HTMLElement, key: Parameters<typeof t>[0]) {
  const button = [...host.querySelectorAll('button')].find(el => el.textContent === t(key))
  expect(button).toBeDefined()
  await act(async () => button!.click())
}
function input(host: HTMLElement, label: string, value: number) {
  const el = [...host.querySelectorAll('input')].find(e => e.getAttribute('aria-label') === label) as HTMLInputElement
  expect(el).toBeDefined()
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => { setter.call(el, String(value)); flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true }))) })
}

describe('canonical and default Newton entry', () => {
  it('canonical saved inputs explicitly use synthetic DAE at the original 180s/1s without a load step', () => {
    const raw = canonical() as any
    expect(raw.runDefaults.liquid.experiment).toMatchObject({
      engine: 'newton-dae', duration_s: 180, dt_s: 1, settings: { mode: 'manual', loadStep: false },
      dae: { analysis: { mode: 'steady', initialization: 'steady' }, execution: { device: 'cpu', max_threads: 2 } },
    })
    expect(raw.meta.provenance.topology.rack_count).toBe(14)
    expect(raw.meta.provenance.topology.server_count).toBe(56)
    expect(raw.constructions).toEqual((original() as any).constructions)
    expect(raw.runDefaults.liquid.solver).toEqual((original() as any).runDefaults.liquid.solver)
  })
  it('the canonical real loader exports one steady point, then the original 180s/1s v4 timeline', () => {
    loaded(canonical())
    const steady=experiments.compileSavedExperiment(store.getProject()!).liquidrun as any
    expect(steady.schemaVersion).toBe(3)
    expect(steady.time).toMatchObject({N_t:1,dt_s:1})
    expect(steady.analysis.mode).toBe('steady')
    expect(steady.events).toEqual([])
    const steadyProject=serializeProjectState(store.getProject()!)
    setSimulationMode('transient','liquid')
    const transient=experiments.compileSavedExperiment(store.getProject()!).liquidrun as any
    expect(transient.schemaVersion).toBe(4)
    expect(transient.time).toMatchObject({N_t:181,dt_s:1})
    expect(transient.transient.durationS).toBe(180)
    expect(transient.analysis.mode).toBe('transient')
    expect(experiments.readExperiment(store.getProject()).settings.loadStep).toBe(false)
    const transientProject=serializeProjectState(store.getProject()!)
    const reopened=readProjectFileText(transientProject)
    if(!reopened.ok)throw new Error(reopened.error)
    expect(experiments.compileSavedExperiment(reopened.project).liquidrun).toEqual(transient)
    if(process.env.LIQUID_DEFAULT_EXPORT_DIR) {
      const dir=process.env.LIQUID_DEFAULT_EXPORT_DIR
      writeFileSync(resolve(dir,'canonical-steady-project.cfdproj'),steadyProject)
      writeFileSync(resolve(dir,'canonical-steady.liquidrun'),JSON.stringify(steady,null,2))
      writeFileSync(resolve(dir,'canonical-transient-project.cfdproj'),transientProject)
      writeFileSync(resolve(dir,'canonical-transient.liquidrun'),JSON.stringify(transient,null,2))
    }
  })
  it('new explicit adoption from legacy v2 also follows a steady top-bar choice', async () => {
    const p=noExperiment() as any
    const defaults=experiments.readExperiment(p)
    p.runDefaults.liquid.experiment={duration_s:180,dt_s:1,settings:defaults.settings}
    const host=await panel(p)
    await click(host,'lce.adopt_mock')
    expect(experiments.readExperiment(store.getProject()).dae?.analysis).toEqual({mode:'steady',initialization:'steady'})
    expect(experiments.compileSavedExperiment(store.getProject()!).liquidrun.time.N_t).toBe(1)
  })
  it('a marked Newton experiment follows domainModes even when its saved analysis is stale', () => {
    const p=loaded(canonical()) as any
    p.runDefaults.liquid.experiment.dae.analysis.mode='transient'
    const before=serializeProjectState(p)
    const run=experiments.compileSavedExperiment(p).liquidrun as any
    expect(run.analysis.mode).toBe('steady')
    expect(run.time.N_t).toBe(1)
    expect(serializeProjectState(p)).toBe(before)
  })
  it('marked stale-mode UI displays the same effective domain mode as export, without writing it', async () => {
    const p=loaded(canonical()) as any
    p.runDefaults.liquid.experiment.dae.analysis={mode:'transient',initialization:'consistent'}
    const host=await panel(p,true)
    expect(host.querySelector('#lce-analysis-mode')?.textContent).toContain(t('lce.mode_steady'))
    expect(host.querySelector('#lce-analysis-mode')?.textContent).not.toContain(t('lce.mode_transient'))
    expect([...host.querySelectorAll('input')].some(el=>el.getAttribute('aria-label')===t('lce.duration'))).toBe(false)
    expect(host.querySelector('select[aria-label="'+t('lce.supply_mode')+'"]')?.matches(':disabled')).toBe(true)
    expect(experiments.readExperiment(store.getProject()).dae?.analysis).toEqual({mode:'transient',initialization:'consistent'})
  })
  it('saved unmarked steady DAE retains steady initialization when a legacy document has a transient uniform timeline', async () => {
    const p=loaded(canonical()) as any
    delete p.runDefaults.liquid.experiment.engine
    p.transient.liquidMode='transient'
    p.transient.mode='transient'
    p.transient.start={kind:'uniform',uniform:{waterT_C:22,solidT_C:22}}
    const host=await panel(p,true)
    expect(host.querySelector('#lce-analysis-mode')?.textContent).toContain(t('lce.mode_steady'))
    const note={textContent:[...host.querySelectorAll('[role=note]')].map(n=>n.getAttribute('aria-label')??'').find(text=>text.includes(t('lce.init_steady'))||text.includes(t('lce.init_consistent')))}
    expect(note?.textContent).toContain(t('lce.init_steady'))
    expect(note?.textContent).not.toContain(t('lce.init_consistent'))
    const run=experiments.compileSavedExperiment(store.getProject()!).liquidrun as any
    expect(run.schemaVersion).toBe(3)
    expect(run.analysis).toEqual({mode:'steady',initialization:'steady'})
    expect(run.time.N_t).toBe(1)
  })
  it.each(['missing','marker-only'] as const)('default intent gates time/load controls purely for %s configuration before materialization', async kind => {
    const p=noExperiment() as any
    if(kind==='marker-only')p.runDefaults.liquid.experiment=experiments.readExperiment(p)
    const graph=vi.spyOn(dynamic,'compileDynamicLiquidGraph')
    const host=await panel(p,true)
    const saved=JSON.stringify((store.getProject() as any).runDefaults.liquid.experiment)
    const loadStep=()=>[...host.querySelectorAll('label')].find(label=>label.textContent?.includes(t('lce.add_load_step')))?.querySelector('input') as HTMLInputElement|undefined
    expect([...host.querySelectorAll('input')].some(el=>el.getAttribute('aria-label')===t('lce.duration'))).toBe(false)
    expect(loadStep()?.disabled).toBe(true)
    expect(readinessOnly(graph)).toBe(true)
    await act(async()=>setSimulationMode('transient','liquid'))
    expect(host.querySelector('#lce-duration-timeline')).not.toBeNull()
    expect(host.querySelector('#lce-keyframe-hint')).not.toBeNull()
    expect(loadStep()).toBeUndefined()
    expect(JSON.stringify((store.getProject() as any).runDefaults.liquid.experiment)).toBe(saved)
    expect(experiments.readExperiment(store.getProject()).dae).toBeUndefined()
    expect(readinessOnly(graph)).toBe(true)
  })
  it('unmarked saved legacy v2 keeps its existing duration and load controls in a steady project', async () => {
    const p=noExperiment() as any
    const {engine,...legacy}=experiments.readExperiment(p)
    p.runDefaults.liquid.experiment=legacy
    const host=await panel(p,true)
    expect([...host.querySelectorAll('input')].some(el=>el.getAttribute('aria-label')===t('lce.duration'))).toBe(true)
    const load=[...host.querySelectorAll('label')].find(label=>label.textContent?.includes(t('lce.add_load_step')))?.querySelector('input') as HTMLInputElement
    expect(load.disabled).toBe(false)
    expect(experiments.readExperiment(store.getProject()).engine).toBeUndefined()
  })
  it('reading and rendering a missing experiment never compiles or edits even an unconnected project', async () => {
    const p = noExperiment() as any
    p.constructions = Object.fromEntries(schema().BUCKETS.map(bucket => [bucket, {}]))
    const spy = vi.spyOn(dynamic, 'compileDynamicLiquidGraph')
    const host = await panel(p,true)
    const before = serializeProjectState(store.getProject()!), rev = store.revision()
    expect(experiments.readExperiment(store.getProject())).toMatchObject({ engine: 'newton-dae', dt_s: 1 })
    expect(host.querySelector('#lce-default-newton-assumptions')?.getAttribute('title')).toBe(t('lce.default_newton_assumptions'))
    // The DAE solves the pump loop as a PI law inside each step: its
    // controls are offered, with a note on what the DAE does with them.
    expect(host.querySelector('#lce-control-mode')).not.toBeNull()
    expect(host.querySelector('#lce-pi-dae')).not.toBeNull()
    input(host, t('lce.timestep'), 2)
    expect(experiments.readExperiment(store.getProject())).toMatchObject({ engine: 'newton-dae', dt_s: 2 })
    expect(spy).not.toHaveBeenCalled()
    expect(store.revision()).toBeGreaterThan(rev)
    expect(JSON.parse(before).runDefaults.liquid.experiment).toBeUndefined()
    expect((store.getProject()! as any).runDefaults.liquid.experiment.dae).toBeUndefined()
  })
  it('a pure compile resolves missing default to one steady sample without mutating or materializing it', () => {
    const p = loaded(noExperiment())
    const before = serializeProjectState(p)
    if(process.env.LIQUID_DEFAULT_ADOPTION)writeFileSync(process.env.LIQUID_DEFAULT_ADOPTION,JSON.stringify(experiments.resolveSavedExperiment(p),null,2))
    const run = experiments.compileSavedExperiment(p).liquidrun as any
    expect(run.schemaVersion).toBe(3)
    expect(run.analysis).toEqual({ mode: 'steady', initialization: 'steady' })
    expect(run.time).toMatchObject({ dt_s: 1, N_t: 1 })
    expect(run.events).toEqual([])
    expect(run.edges.filter((e: any) => e.type === 'cold_plate')).toHaveLength(56)
    expect(serializeProjectState(p)).toBe(before)
    expect((p as any).runDefaults.liquid.experiment).toBeUndefined()
  })
  it('preparing after a time edit saves the actual assumptions before hashing the submitted project', async () => {
    const host = await panel(noExperiment())
    input(host, t('lce.timestep'), 2)
    const before = cfdrunExport().projectContentHashSync(store.getProject()!)
    const originalModels=structuredClone(store.getProject()!.constructions)
    await click(host, 'lq_ux.solve_steady')
    expect(mocks.create).toHaveBeenCalledTimes(1)
    const submitted = mocks.create.mock.calls[0]![0] as any
    const saved = experiments.readExperiment(store.getProject())
    expect(saved).toMatchObject({ engine: 'newton-dae', dt_s: 2, dae: { analysis: { mode: 'steady' }, execution: { device: 'cpu', max_threads: 2 } } })
    expect(saved.parameterPack?.source).toBe('mock')
    expect(store.getProject()!.constructions).toEqual(originalModels)
    expect(saved.dae!.primary_circuits[0]).toMatchObject({ edge_id: 'm_30_hx', flow_kg_s: 1, primary_volume_m3: .02, secondary_volume_m3: .02, source: 'mock' })
    expect(submitted.time).toMatchObject({ dt_s: 2, N_t: 1 })
    expect(submitted.source_project.sha256).toBe(cfdrunExport().projectContentHashSync(store.getProject()!))
    expect(submitted.source_project.sha256).not.toBe(before)
    expect(submitted.parameter_pack).toEqual(saved.parameterPack)
    expect(submitted.primary_circuits).toEqual(saved.dae!.primary_circuits)
    expect(host.querySelector('#lce-saved-mock-assumptions')?.getAttribute('title')).toBe(t('lce.saved_mock_assumptions'))
  })
  it('download also materializes first and binds the saved project, without starting a runtime', async () => {
    const host = await panel(noExperiment())
    let blob: Blob | undefined
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL(value: Blob) { blob = value; return 'blob:test' }
      static revokeObjectURL() {}
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    try {
      await click(host, 'lce.export_manifest')
      expect(blob).toBeDefined()
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob!)
      })
      const run = JSON.parse(text)
      expect(run.source_project.sha256).toBe(cfdrunExport().projectContentHashSync(store.getProject()!))
      expect(experiments.readExperiment(store.getProject()).dae).toBeDefined()
      expect(mocks.create).not.toHaveBeenCalled()
    } finally {
      // The panel revokes the URL a second later; keep the stub until then.
      await new Promise((resolve) => setTimeout(resolve, 1100))
      vi.unstubAllGlobals()
    }
  })
  it('only the top-bar mode switch enables the v4 timeline and a saved/reopened export is identical', () => {
    const p = loaded(noExperiment())
    store.setRunConfig('liquid', 'experiment', experiments.resolveSavedExperiment(p))
    expect(experiments.compileSavedExperiment(store.getProject()!).liquidrun.time.N_t).toBe(1)
    expect(setSimulationMode('transient', 'liquid')).toBe(true)
    expect(experiments.readExperiment(store.getProject()).dae!.analysis.mode).toBe('transient')
    const tr = transientOf(store.getProject())
    const run = experiments.compileSavedExperiment(store.getProject()!).liquidrun as any
    expect(run.schemaVersion).toBe(4)
    expect(run.time.N_t).toBe(tr.durationS / 1 + 1)
    expect(run.transient.durationS).toBe(tr.durationS)
    expect(run.events).toEqual([])
    const reopened = readProjectFileText(serializeProjectState(store.getProject()!))
    expect(reopened.ok).toBe(true)
    if (!reopened.ok) return
    expect(experiments.compileSavedExperiment(reopened.project)).toEqual(experiments.compileSavedExperiment(store.getProject()!))
    setSimulationMode('steady', 'liquid')
    expect(experiments.compileSavedExperiment(store.getProject()!).liquidrun.time.N_t).toBe(1)
  })
  it('an edited duration survives default resolution before timeline mode is chosen', () => {
    const p = loaded(noExperiment())
    store.setRunConfig('liquid', 'experiment', { ...experiments.readExperiment(p), duration_s: 240, dt_s: 2 })
    const resolved = experiments.resolveSavedExperiment(store.getProject()!)
    expect(resolved).toMatchObject({ engine: 'newton-dae', duration_s: 240, dt_s: 2, dae: { analysis: { mode: 'steady' } } })
    expect(experiments.readExperiment(store.getProject()).dae).toBeUndefined()
  })
  it('saved explicit legacy PID remains v2 with all controller settings intact', () => {
    const p = loaded(noExperiment())
    const settings = { ...experiments.readExperiment(p).settings, mode: 'temperature' as const, pumpId: 'm_30_pump', measurementId: 'm_1_e_1', kp: .017, ki: .003 }
    const graph = dynamic.compileDynamicLiquidGraph(p)
    settings.measurementId = graph.edges.find(e => e.type === 'cold_plate')!.id
    store.setRunConfig('liquid', 'experiment', { duration_s: 180, dt_s: 1, settings })
    const before = serializeProjectState(store.getProject()!)
    const run = experiments.compileSavedExperiment(store.getProject()!)
    expect(run.liquidrun.schemaVersion).toBe(2)
    expect(run.controllers).toHaveLength(1)
    expect(run.controllers[0]!.pid.kp).toBe(.017)
    expect(run.controllers[0]!.pid.ki).toBe(.003)
    expect(serializeProjectState(store.getProject()!)).toBe(before)
  })
  it('Newton intent never silently discards saved PID: the loop goes into the DAE', () => {
    const p = loaded(noExperiment())
    store.setRunConfig('liquid', 'experiment', { ...experiments.readExperiment(p), settings: { ...experiments.readExperiment(p).settings, mode: 'temperature' } })
    // The saved loop names no pump yet, so it cannot compile -- it is not dropped.
    expect(() => experiments.compileSavedExperiment(store.getProject()!)).toThrow(/pump/)
  })
  it('a saved pump loop compiles into the DAE as PI, without the sampled slew limit', () => {
    const control = { id: 'editor-pump-pid', measurement: { kind: 'solid_temperature' as const, target_id: 'cp' },
      actuator: { edge_id: 'pump', field: 'rpm_ratio' as const },
      pid: { kp: .03, ki: .002, kd: 0, setpoint: 318.15, bias: .8, output_min: .2, output_max: 1, action: 'direct' as const, rate_limit_per_s: .05 },
      sample_period_s: 1 }
    const [loop] = experiments.daeControllers([control], .5)
    expect(loop!.pid).not.toHaveProperty('rate_limit_per_s')
    expect(loop!.pid).toMatchObject({ kp: .03, ki: .002, kd: 0, setpoint: 318.15 })
    expect(loop!.sample_period_s).toBe(.5)
    expect(() => experiments.daeControllers([{ ...control, pid: { ...control.pid, ki: 0 } }], 1)).toThrow(/integral gain/)
  })
  it('rejects an unknown engine in the shared document validator and compile entry', () => {
    const p = loaded(noExperiment()) as any
    p.runDefaults.liquid.experiment = { ...experiments.readExperiment(p), engine: 'invented-engine' }
    expect(checkProjectDocument(p).ok).toBe(false)
    expect(() => experiments.compileSavedExperiment(p)).toThrow(/engine/)
  })
  it('does not alter any effective DAE-CPU physics, times or MMS binding when provenance wording is corrected', () => {
    const p = JSON.parse(readFileSync(resolve(repo, 'case/verification/shinaide-legacy-projects/Shinaide_dae_cpu.json'), 'utf8'))
    const old = original('Shinaide_dae_cpu') as any
    expect(p.constructions).toEqual(old.constructions)
    expect(p.runDefaults).toEqual(old.runDefaults)
    expect(p.meta.provenance.topology.description).toMatch(/14/)
    expect(JSON.stringify(p.meta.provenance.display_only)).toMatch(/curve|曲线/)
  })
})
