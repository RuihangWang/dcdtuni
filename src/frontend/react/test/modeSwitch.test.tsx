// The steady/transient choice has ONE writer, per cooling domain (E8b).
//
// `transient.airMode` / `transient.liquidMode` are the truth and
// `transient.mode` is derived (transient when either is). In the same edit the
// air mode is mirrored to `simulationConfig.solverType` (and, for a transient
// project, `endTimeSeconds`) and the liquid mode to the DAE's analysis mode.
// The top-bar switch acts on the domain on screen (风冷 / 液冷).
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import * as store from '../src/projectStore'
import { readProjectFileText, serializeProjectState } from '../src/io/projectFiles'
import { setLiquidModeActive } from '../src/liquid/liquidModeState'
import { airTransientRun } from '../src/transient/airSchedules'
import { domainModesOf } from '../src/transient/model'
import { ModeSwitch } from '../src/transient/ModeSwitch'
import { liquidTimeline } from '../src/transient/runSchedules'
import { setSimulationMode } from '../src/transient/simulationMode'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const caseFile = (rel: string) => resolve(process.cwd(), '../../../case', rel)
const sim = () => (store.getProject() as any).runDefaults.simulationConfig
const tr = () => (store.getProject() as any).transient
const dae = () => (store.getProject() as any).runDefaults.liquid.experiment.dae
let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

function load(rel: string) {
  const r = readProjectFileText(readFileSync(caseFile(rel), 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
}
const seg = (mode: string) => host.querySelector(`#transient-mode-selector [data-mode="${mode}"]`) as HTMLButtonElement

beforeEach(() => {
  setLiquidModeActive(false)
  load('Base_coldAisleIso/Base_coldAisleIso.cfdproj')
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove(); setLiquidModeActive(false) })

describe('air view', () => {
  it('switching air to transient writes airMode, the derived mode and the solver mirrors only', () => {
    expect(tr()).toMatchObject({ mode: 'steady', airMode: 'steady', liquidMode: 'steady' })
    expect(setSimulationMode('transient')).toBe(true)
    expect(tr()).toMatchObject({ mode: 'transient', airMode: 'transient', liquidMode: 'steady' })
    expect(sim().solverType).toBe('transient')
    expect(sim().endTimeSeconds).toBe(tr().durationS)
  })

  it('switching back to steady returns the playhead to the start', () => {
    setSimulationMode('transient')
    store.setPlayhead(120)
    setSimulationMode('steady')
    expect(tr()).toMatchObject({ mode: 'steady', airMode: 'steady' })
    expect(sim().solverType).toBe('steady')
    expect(store.playheadTime()).toBe(0)
    expect(store.sceneFrozen()).toBe(false)
  })

  it('the top-bar segments show and change the air mode; undo takes every mirror back', () => {
    act(() => root.render(<ModeSwitch />))
    const group = host.querySelector('#transient-mode-selector')!
    expect(group.getAttribute('data-domain')).toBe('air')
    expect(seg('steady').getAttribute('aria-checked')).toBe('true')
    act(() => seg('transient').click())
    expect(tr()).toMatchObject({ mode: 'transient', airMode: 'transient' })
    expect(seg('transient').getAttribute('aria-checked')).toBe('true')
    // the other domain now runs the other way: the badge names it
    expect(host.querySelector('.mode-switch-other')?.getAttribute('data-other')).toBe('liquid')
    act(() => store.undo())
    expect(tr()).toMatchObject({ mode: 'steady', airMode: 'steady' })
    expect(sim().solverType).toBe('steady')
    expect(seg('steady').getAttribute('aria-checked')).toBe('true')
    expect(host.querySelector('.mode-switch-other')).toBeNull()
  })

  it('both domain modes survive save and reopen', () => {
    setSimulationMode('transient', 'air')
    const r = readProjectFileText(serializeProjectState(store.getProject()!))
    if (!r.ok) throw new Error(r.error)
    store.loadProject(r.project, null)
    expect(tr()).toMatchObject({ mode: 'transient', airMode: 'transient', liquidMode: 'steady' })
    expect(sim().solverType).toBe('transient')
  })
})

describe('liquid view', () => {
  // A v5 file saved before E8b: no domain modes, steady timeline, a DAE that
  // marches in time on its own.
  beforeEach(() => {
    const raw = JSON.parse(readFileSync(resolve(process.cwd(), 'test/fixtures/cases/LiquidSingle_Control_dae_v5.cfdproj'), 'utf8'))
    delete raw.transient.airMode
    delete raw.transient.liquidMode
    raw.transient.mode = 'steady'
    raw.runDefaults.liquid.experiment.dae.analysis = { mode: 'transient', initialization: 'steady' }
    const r = readProjectFileText(JSON.stringify(raw))
    if (!r.ok) throw new Error(r.error)
    store.loadProject(r.project, null)
  })

  it('a v5 file from before E8b keeps what it ran: steady timeline, DAE marching on its own', () => {
    expect(domainModesOf(store.getProject())).toEqual({ air: 'steady', liquid: 'steady' })
    expect(tr().mode).toBe('steady')
    expect(dae().analysis.mode).toBe('transient')
  })

  it('the top bar in liquid view switches the liquid mode and its DAE, never the air solver', () => {
    act(() => setLiquidModeActive(true))
    act(() => root.render(<ModeSwitch />))
    expect(host.querySelector('#transient-mode-selector')!.getAttribute('data-domain')).toBe('liquid')
    act(() => seg('transient').click())
    expect(tr()).toMatchObject({ mode: 'transient', airMode: 'steady', liquidMode: 'transient' })
    expect(dae().analysis.mode).toBe('transient')
    expect(sim().solverType).toBe('steady')
    act(() => seg('steady').click())
    expect(tr()).toMatchObject({ mode: 'steady', liquidMode: 'steady' })
    expect(dae().analysis).toMatchObject({ mode: 'steady', initialization: 'steady' })
  })

  it('each exporter follows its own domain', () => {
    setSimulationMode('transient', 'liquid')
    const p = store.getProject()!
    expect(airTransientRun(p)).toBeNull()
    expect(liquidTimeline(p, true)).not.toBeNull()
    setSimulationMode('steady', 'liquid')
    setSimulationMode('transient', 'air')
    const q = store.getProject()!
    expect(airTransientRun(q)).not.toBeNull()
    expect(liquidTimeline(q, true)).toBeNull()
  })
})

it('a migrated v4 hybrid (air steady, DAE marching in time) opens as air steady + liquid transient', () => {
  load('verification/shinaide-legacy-projects/Shinaide_dae_cpu.json')
  expect(tr()).toMatchObject({ mode: 'transient', airMode: 'steady', liquidMode: 'transient' })
  expect(sim().solverType).toBe('steady')
})
