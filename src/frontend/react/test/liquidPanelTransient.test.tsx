// The liquid experiment panel in a transient project (plan T2c.5): the timeline
// owns the duration and the load / supply changes are keyframes, so those
// scenario controls step aside — unless one is still switched on, which stays
// visible rather than acting unseen. A steady project keeps them all.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, useSyncExternalStore } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LiquidExperimentPanel } from '../src/components/LiquidExperimentPanel'
import * as store from '../src/projectStore'
import { getLocale, setLocale } from '../src/i18n'
import { readProjectFileText } from '../src/io/projectFiles'
import { readExperiment } from '../src/liquid/liquidExperiment'
import { setSimulationMode } from '../src/transient/simulationMode'

const mocks = vi.hoisted(() => ({ state: { session: null, frames: [], projectRevision: null, busy: false, error: null, connection: 'idle' } }))
vi.mock('../src/liquid/liquidRuntimeClient', async (importOriginal) => ({ ...(await importOriginal<typeof import('../src/liquid/liquidRuntimeClient')>()), subscribeRuntime: () => () => {}, runtimeState: () => mocks.state, createRuntimeSession: vi.fn() }))
vi.mock('../src/components/LiquidSolverPanel', () => ({ LiquidSolverPanel: () => null }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let prev: ReturnType<typeof getLocale>
function Harness() {
  useSyncExternalStore(store.subscribe, store.revision)
  return <LiquidExperimentPanel project={store.getProject()} />
}
beforeEach(async () => {
  prev = getLocale(); setLocale('zh-CN')
  const r = readProjectFileText(readFileSync(resolve(process.cwd(), 'test/fixtures/cases/NV_PEK02_mock.cfdproj'), 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  await act(async () => root.render(<Harness />))
})
afterEach(() => { act(() => root.unmount()); host.remove(); setLocale(prev) })

const loadStepBox = () => [...host.querySelectorAll('label')].find(l => l.textContent?.includes('添加冷板负载阶跃')) ?? null
const supplySelect = () => host.querySelector('select[aria-label="供水温度方案"]')
const durationInput = () => [...host.querySelectorAll('label')].find(l => l.textContent?.includes('仿真时长'))?.querySelector('input') ?? null

it('a transient project: duration from the timeline, load step and supply plan give way to keyframes', () => {
  expect(host.querySelector('#lce-duration-timeline')!.textContent).toContain('10')
  expect(durationInput()).toBeNull()
  expect(loadStepBox()).toBeNull()
  expect(supplySelect()).toBeNull()
  expect(host.querySelector('#lce-keyframe-hint')).not.toBeNull()
})

it('a load step still switched on stays visible in a transient project', async () => {
  const exp = readExperiment(store.getProject())
  await act(async () => { store.setRunConfig('liquid', 'experiment', { ...exp, settings: { ...exp.settings, loadStep: true } }) })
  expect(loadStepBox()).not.toBeNull()
})

it('liquid steady in the top bar is a single operating point: the DAE turns steady, no time axis', async () => {
  await act(async () => { setSimulationMode('steady', 'liquid') })
  expect(readExperiment(store.getProject()).dae?.analysis).toMatchObject({ mode: 'steady', initialization: 'steady' })
  expect(host.querySelector('#lce-duration-timeline')).toBeNull()
  expect(durationInput()).toBeNull()
  expect(host.querySelector('#lce-keyframe-hint')).toBeNull()
  expect(host.querySelector('#lce-analysis-mode')!.textContent).toContain('稳态')
})
