import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, useSyncExternalStore } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LiquidExperimentPanel } from '../src/components/LiquidExperimentPanel'
import * as store from '../src/projectStore'
import { readProjectFileText } from '../src/io/projectFiles'
import type { RuntimeClientState, RuntimeSession } from '../src/liquid/liquidRuntimeClient'
import { getLocale, setLocale, t } from '../src/i18n'

const transport = vi.hoisted(() => ({
  state: { session: null, frames: [], projectRevision: null, busy: false, error: null, connection: 'idle' } as RuntimeClientState,
  create: vi.fn().mockResolvedValue(null),
}))
vi.mock('../src/liquid/liquidRuntimeClient', () => ({
  subscribeRuntime: () => () => {},
  runtimeState: () => transport.state,
  createRuntimeSession: transport.create,
}))
vi.mock('../src/components/LiquidSolverPanel', () => ({ LiquidSolverPanel: () => null }))

// On the unpatched baseline this uses its only revision, reproducing the real
// prepared-session snapshot. The final module exposes the separate edit clock.
function inputRevision(): number {
  const get = Reflect.get(store, 'documentInputRevision') as (() => number) | undefined
  return get ? get() : store.revision()
}
let host: HTMLDivElement
let cleanup = () => {}
let previousLocale: ReturnType<typeof getLocale>
function session(): RuntimeSession {
  return {
    run_id: 'actual-store-session', status: 'completed', config_hash: 'hash',
    metadata: { node_ids: [], edge_ids: [], cp_edge_ids: [], total_sample_count: 50, dt_s: .5, object_bindings: {}, solver: 'differentiable_newton_dae' },
    frame: { kind: 'frame', run_id: 'actual-store-session', seq: 49, time_s: 24.5,
      node_pressure_Pa: [], node_temperature_K: [], edge_flow_kg_s: [], chip_temp_K: [],
      edge_outlet_temp_K: [], controls: {}, diagnostics: {}, heat_multiplier: 1.2 },
  }
}
function prepareSnapshot() {
  transport.state = { session: session(), frames: [], projectRevision: inputRevision(), busy: false, error: null, connection: 'connected' }
}
function staleVisible() {
  return [...host.querySelectorAll('.liquid-experiment-warning')].some(p => p.textContent === t('lce.stale_project'))
}
type ServerProject = { constructions: { racks: { m_1: { constructions: { servers: { e_1: { cooling: { power: number } } } } } } } }
function changeHeat(project: unknown, power: number) {
  ;(project as ServerProject).constructions.racks.m_1.constructions.servers.e_1.cooling.power = power
}
beforeEach(async () => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  previousLocale = getLocale(); setLocale('en')
  const parsed = readProjectFileText(readFileSync(resolve(process.cwd(), '../../../case/Shinaide_Liquid/Shinaide_liquid.cfdproj'), 'utf8'))
  if (!parsed.ok) throw new Error(parsed.error)
  store.loadProject(parsed.project, null)
  store.edit('timeline', '', draft => {
    const transient = Reflect.get(draft, 'transient') as { mode: string; durationS: number; gridS: number }
    transient.mode = 'transient'; transient.durationS = 24.5; transient.gridS = .5
  })
  prepareSnapshot()
  host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  cleanup = () => { act(() => root.unmount()); host.remove(); setLocale(previousLocale) }
  function Harness() {
    useSyncExternalStore(store.subscribe, store.revision)
    return <LiquidExperimentPanel project={store.getProject()} />
  }
  await act(async () => root.render(<Harness />))
  expect(staleVisible()).toBe(false)
})
afterEach(() => { cleanup(); transport.create.mockClear() })

it('does not invalidate a prepared run as the real playhead reaches its final sample', () => {
  const before = inputRevision(), rendering = store.revision()
  act(() => store.setPlayhead(2.5))
  expect(staleVisible()).toBe(false)
  act(() => store.setPlayhead(24.5))
  expect(store.playheadTime()).toBe(24.5)
  expect(store.revision()).toBeGreaterThan(rendering)
  expect(inputRevision()).toBe(before)
  expect(staleVisible()).toBe(false)
})
it('does not invalidate on log or normal save notifications', () => {
  const before = inputRevision()
  act(() => store.logAction('actual-run-completed', '24.5s'))
  act(() => store.markSaved('/owned/unchanged.cfdproj'))
  expect(inputRevision()).toBe(before)
  expect(staleVisible()).toBe(false)
})
it('still invalidates and repaints after a committed physical heat/keyframe edit', () => {
  const before = inputRevision()
  act(() => { expect(store.edit('heat', '', draft => changeHeat(draft, 1500))).toBe(true) })
  expect(inputRevision()).toBeGreaterThan(before)
  expect(staleVisible()).toBe(true)
})
it('counts undo and redo as committed document changes', () => {
  act(() => store.edit('heat', '', draft => changeHeat(draft, 1500)))
  const edited = inputRevision()
  act(() => store.undo())
  expect(inputRevision()).toBeGreaterThan(edited)
  const undone = inputRevision()
  act(() => store.redo())
  expect(inputRevision()).toBeGreaterThan(undone)
  expect(staleVisible()).toBe(true)
})
it('counts a normal document load even if its UID and inputs are reused', () => {
  const before = inputRevision()
  const current = structuredClone(store.getProject()!)
  act(() => store.loadProject(current, '/owned/reloaded.cfdproj'))
  expect(inputRevision()).toBeGreaterThan(before)
  expect(staleVisible()).toBe(true)
})
it('counts accepted legacy in-place physics commits', () => {
  const before = inputRevision()
  act(() => {
    store.pushUndoCheckpoint('legacy heat')
    store.mutateLive(project => changeHeat(project, 1600), { scene: false })
    store.notifyLiveEdit()
  })
  expect(inputRevision()).toBeGreaterThan(before)
  expect(staleVisible()).toBe(true)
})
it('refused off-start geometry edits do not invalidate the prepared inputs', () => {
  act(() => store.setPlayhead(.5))
  prepareSnapshot()
  act(() => store.logAction('refresh-test-snapshot'))
  const before = inputRevision()
  act(() => {
    expect(store.edit('move', 'm_1', draft => {
      const model = store.findConstruction(draft, 'm_1')!.model
      const geometry = model.geometry as unknown as { position?: { x: number; y: number; z: number } }
      geometry.position = { x: 9, y: 0, z: 9 }
    })).toBe(false)
  })
  expect(store.lastEditRejection()?.reason).toBe('static')
  expect(inputRevision()).toBe(before)
  expect(staleVisible()).toBe(false)
})
it('Prepare captures the committed input clock after saving materialized assumptions', async () => {
  act(() => store.logAction('view-only-before-prepare'))
  act(() => store.markSaved('/owned/source.cfdproj'))
  const button = [...host.querySelectorAll('button')].find(b => b.textContent === t('lce.prepare'))!
  expect(button).toBeDefined()
  await act(async () => button.click())
  expect(transport.create).toHaveBeenCalledOnce()
  expect(transport.create.mock.calls[0]?.[2]).toBe(inputRevision())
  expect(inputRevision()).toBeLessThan(store.revision())
})
