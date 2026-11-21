// The solve tab's nonlinear-convergence group (E8c): steady shows the SIMPLE stop
// thresholds, transient shows the per-step outer-loop thresholds and the per-step
// maximum — two separate sets, so switching the mode never overwrites the other.
// A project saved before E8c gets the per-step set and a maximum of 20 once.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { SolverPanel } from '../src/components/SolverPanel'
import * as store from '../src/projectStore'
import { getLocale, setLocale } from '../src/i18n'
import { readProjectFileText } from '../src/io/projectFiles'
import { setSimulationMode } from '../src/transient/simulationMode'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let prev: ReturnType<typeof getLocale>
beforeEach(() => { prev = getLocale(); setLocale('zh-CN'); host = document.createElement('div'); document.body.append(host); root = createRoot(host) })
afterEach(() => { root.unmount(); host.remove(); setLocale(prev) })

const sim = () => (store.getProject() as unknown as { runDefaults: { simulationConfig: Record<string, any> } }).runDefaults.simulationConfig
function open(rel: string, edit?: (doc: any) => void) {
  const text = readFileSync(resolve(process.cwd(), '../../..', rel), 'utf8')
  const doc = JSON.parse(text)
  edit?.(doc)
  const r = readProjectFileText(JSON.stringify(doc))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  render()
}
const render = () => flushSync(() => root.render(<SolverPanel project={store.getProject()} />))
function type(id: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`#${id}`)!
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  flushSync(() => { set.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) })
  flushSync(() => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })) })
  flushSync(() => { input.dispatchEvent(new Event('blur', { bubbles: true })); input.dispatchEvent(new FocusEvent('focusout', { bubbles: true })) })
}

it('an older project gets the per-step set and a maximum of 20, once', () => {
  open('case/Base_coldAisleIso/Base_coldAisleIso.cfdproj', (doc) => {
    const sc = doc.runDefaults.simulationConfig
    delete sc.transientResidualControl
    sc.nOuterCorrectors = 2
  })
  expect(sim().nOuterCorrectors).toBe(20)
  expect(sim().transientResidualControl).toEqual({ p_rgh: 1e-3, U: 1e-4, T: 1e-4, k_epsilon: 1e-4 })
  open('case/Base_coldAisleIso/Base_coldAisleIso.cfdproj', (doc) => {
    doc.runDefaults.simulationConfig.transientResidualControl = { p_rgh: 5e-3, U: 1e-4, T: 1e-4, k_epsilon: 1e-4 }
    doc.runDefaults.simulationConfig.nOuterCorrectors = 5
  })
  expect(sim().nOuterCorrectors).toBe(5)                       // the user's value after E8c stays
  expect(sim().transientResidualControl.p_rgh).toBe(5e-3)
})

it('steady shows the stop thresholds; transient shows the per-step set and its maximum', () => {
  open('case/Base_coldAisleIso/Base_coldAisleIso.cfdproj')
  expect(host.querySelector('#slv-res-prgh')).not.toBeNull()
  expect(host.querySelector('#slv-tres-prgh')).toBeNull()
  expect(host.querySelector('#slv-n-outer')).toBeNull()
  setSimulationMode('transient')
  render()
  expect(host.querySelector('#slv-res-prgh')).toBeNull()
  expect(host.querySelector('#slv-tres-prgh')).not.toBeNull()
  expect(host.querySelector<HTMLInputElement>('#slv-n-outer')!.value).toBe('20')
  expect(host.querySelector('#slv-convergence')!.textContent).toContain('瞬态每步收敛阈值')
})

it('each set is written on its own, so switching the mode keeps the other', () => {
  open('case/Base_coldAisleIso/Base_coldAisleIso.cfdproj')
  const steadyP = sim().residualControl.p_rgh
  setSimulationMode('transient')
  render()
  type('slv-tres-prgh', '0.002')
  expect(sim().transientResidualControl.p_rgh).toBe(0.002)
  expect(sim().residualControl.p_rgh).toBe(steadyP)
  setSimulationMode('steady')
  render()
  type('slv-res-prgh', '0.0005')
  expect(sim().residualControl.p_rgh).toBe(0.0005)
  expect(sim().transientResidualControl.p_rgh).toBe(0.002)
})

// Owner 2026-10-27: the steady stop tolerance on the ACU return temperatures is
// a project setting (default 0.5 K), shown only for a steady run.
it('steady shows the return-air stop tolerance, defaulting to 0.5 K, and writes the project', () => {
  open('case/Base_coldAisleIso/Base_coldAisleIso.cfdproj', (doc) => { delete doc.runDefaults.simulationConfig.stopToleranceK })
  const input = host.querySelector<HTMLInputElement>('#slv-stop-tolerance')!
  expect(input.value).toBe('0.5')
  expect(host.querySelector('#slv-convergence')!.textContent).toContain('回风温度停止阈值')
  type('slv-stop-tolerance', '0.3')
  expect(sim().stopToleranceK).toBe(0.3)
  type('slv-stop-tolerance', '0')
  expect(sim().stopToleranceK).toBe(0.05)                     // clamped to the smallest tolerance
  setSimulationMode('transient')
  render()
  expect(host.querySelector('#slv-stop-tolerance')).toBeNull()
})

it('a new project defaults every steady residual threshold to 1e-3', async () => {
  const { defaultSolverConfig } = await import('../src/run/solverConfig')
  expect(defaultSolverConfig().residualControl).toEqual({ p_rgh: 1e-3, U: 1e-3, T: 1e-3, k_epsilon: 1e-3 })
  expect(defaultSolverConfig().stopToleranceK).toBe(0.5)
})
