import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SolverPanel } from '../src/components/SolverPanel'
import * as store from '../src/projectStore'
import * as session from '../src/run/session'
import { getLocale, setLocale, t } from '../src/i18n'
import { readProjectFileText } from '../src/io/projectFiles'
import { setSimulationMode } from '../src/transient/simulationMode'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let prev: ReturnType<typeof getLocale>
const sim = () => (store.getProject()!.runDefaults!.simulationConfig as Record<string, any>)
const render = () => flushSync(() => root.render(<SolverPanel project={store.getProject()} />))
const click = (id: string) => flushSync(() => host.querySelector<HTMLButtonElement>(id)!.click())
function change(value: string) {
  const input = host.querySelector<HTMLSelectElement>('#slv-pressure-preconditioner')!
  flushSync(() => { input.value = value; input.dispatchEvent(new Event('change', { bubbles: true })) })
  render()
}
function type(id: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(id)!
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  flushSync(() => input.focus())
  flushSync(() => { setter.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) })
  flushSync(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
  render()
}
beforeEach(() => {
  prev = getLocale(); setLocale('zh-CN')
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  const opened = readProjectFileText(readFileSync(resolve(process.cwd(), '../../../case/Base_leakage/Base_leakage.cfdproj'), 'utf8'))
  if (!opened.ok) throw new Error(opened.error)
  store.loadProject(opened.project, null)
  render()
})
afterEach(() => { flushSync(() => root.unmount()); host.remove(); setLocale(prev); vi.restoreAllMocks() })

it('has an accessible advanced button and shows mode defaults without writing options', () => {
  const before = JSON.stringify(sim())
  const toggle = host.querySelector<HTMLButtonElement>('#slv-advanced-toggle')!
  expect(toggle.tagName).toBe('BUTTON')
  expect(toggle.getAttribute('aria-expanded')).toBe('false')
  click('#slv-advanced-toggle')
  expect(toggle.getAttribute('aria-expanded')).toBe('true')
  expect(host.querySelector('#slv-pressure-preconditioner')!.textContent).toContain('GAMG')
  expect(host.querySelector<HTMLInputElement>('#slv-pressure-tolerance')!.value).toBe('')
  expect(host.querySelector<HTMLInputElement>('#slv-pressure-tolerance')!.placeholder).toBe('0.000001')
  expect(host.querySelector<HTMLInputElement>('#slv-pressure-rel-tol')!.placeholder).toBe('0.01')
  expect(host.querySelector<HTMLInputElement>('#slv-pressure-max-iter')!.placeholder).toBe('200')
  expect(JSON.stringify(sim())).toBe(before)
  setSimulationMode('transient'); render()
  expect(host.querySelector('#slv-pressure-preconditioner')!.textContent).toContain(t('solver_pressure.default_transient'))
  expect(sim().pressureLinearSolver).toBeUndefined()
  expect(host.querySelector<HTMLInputElement>('#slv-pressure-tolerance')!.placeholder).toBe('1e-8')
  expect(host.querySelector<HTMLInputElement>('#slv-pressure-max-iter')!.placeholder).toBe('1000')
})

it('stores method and optional numbers independently, preserves them across presets, and clears to absent defaults', () => {
  click('#slv-advanced-toggle')
  const residuals = structuredClone(sim().residualControl)
  change('GAMG')
  type('#slv-pressure-tolerance', '0.00000001')
  type('#slv-pressure-rel-tol', '0')
  type('#slv-pressure-max-iter', '150')
  expect(sim().pressureLinearSolver).toEqual({ preconditioner: 'GAMG', tolerance: 1e-8, relTol: 0, maxIter: 150 })
  click('#preset-fast'); render()
  expect(sim().pressureLinearSolver.preconditioner).toBe('GAMG')
  expect(sim().residualControl).toEqual(residuals)
  type('#slv-pressure-max-iter', '')
  expect(sim().pressureLinearSolver.maxIter).toBeUndefined()
  type('#slv-pressure-tolerance', '')
  type('#slv-pressure-rel-tol', '')
  change('')
  expect(Object.prototype.hasOwnProperty.call(sim(), 'pressureLinearSolver')).toBe(false)
})

it('refuses illegal numerical values without changing their stored or displayed value', () => {
  click('#slv-advanced-toggle')
  type('#slv-pressure-tolerance', '1e-8')
  for (const value of ['0', '-0.01']) type('#slv-pressure-tolerance', value)
  expect(sim().pressureLinearSolver.tolerance).toBe(1e-8)
  expect(host.querySelector<HTMLInputElement>('#slv-pressure-tolerance')!.value).toBe('1e-8')
  type('#slv-pressure-rel-tol', '.01')
  for (const value of ['-0.1', '1', '2']) type('#slv-pressure-rel-tol', value)
  expect(sim().pressureLinearSolver.relTol).toBe(.01)
  type('#slv-pressure-max-iter', '200')
  for (const value of ['0', '-3', '1.2']) type('#slv-pressure-max-iter', value)
  expect(sim().pressureLinearSolver.maxIter).toBe(200)
})

it('blocks new controls while a native run freezes the panel', () => {
  click('#slv-advanced-toggle')
  const before = JSON.stringify(sim())
  vi.spyOn(session, 'isUiFrozen').mockReturnValue(true)
  render()
  expect(host.querySelector('[inert]')).not.toBeNull()
  change('GAMG'); type('#slv-pressure-tolerance', '1e-8')
  expect(JSON.stringify(sim())).toBe(before)
})

it('exports the explicit pressure settings through the actual shared manifest compiler', async () => {
  click('#slv-advanced-toggle'); change('GAMG')
  type('#slv-pressure-tolerance', '1e-8'); type('#slv-pressure-rel-tol', '.01')
  const before = JSON.stringify(store.getProject())
  const { buildManifestV2 } = await import('../src/legacy/cfdrunBuild')
  const manifest = await buildManifestV2({
    buildComprehensiveStlMap: () => ({}),
    buildCfdrunJson: () => ({ simulationConfig: { roomTemperature: 22 } }),
  }, store.getProject()!, '2026-10-05T00:00:00Z')
  expect(manifest.simulationConfig.pressureLinearSolver).toEqual({ preconditioner: 'GAMG', tolerance: 1e-8, relTol: .01 })
  expect(JSON.stringify(store.getProject())).toBe(before)
})
