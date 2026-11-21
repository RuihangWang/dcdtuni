// The liquid results panel on the timeline (plan T5a): its time readout follows
// the playhead, and its slider moves the playhead.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import '../src/legacy/geometryBuilder'
import * as store from '../src/projectStore'
import { readProjectFileText } from '../src/io/projectFiles'
import { LiquidResultsPanel } from '../src/components/LiquidResultsPanel'
import { fetchLiquidResults, resetLiquidRun } from '../src/liquid/liquidSession'
import { setResultSample } from '../src/liquid/liquidTempColor'
import { setSimulationMode } from '../src/transient/simulationMode'
import { startResultPlayback } from '../src/transient/resultPlayback'

const CASE = resolve(process.cwd(), '../../../case/Base_coldAisleIso/Base_coldAisleIso.cfdproj')
const TIMES = [0, 10, 20, 30]
const s = (v: number) => TIMES.map(() => v)
const RESULT = { run_id: 'a', converged: true, time_s: TIMES,
  rack_summaries: [{ rack_id: 'rack', total_flow_kg_s: s(1), supply_temp_K: s(300), return_temp_K: s(310),
    servers: [{ server_id: 'server', flow_kg_s: s(1), max_chip_temp_K: TIMES.map((t) => 320 + t), heat_removed_W: s(100), cp_edge_ids: [] }] }],
  cp_details: [] }

let host: HTMLDivElement, root: Root, stop: () => void
beforeEach(async () => {
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
  const r = readProjectFileText(readFileSync(CASE, 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  setSimulationMode('transient')
  resetLiquidRun()
  setResultSample(-1)
  stop = startResultPlayback()
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(RESULT), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  await fetchLiquidResults()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  await act(async () => root.render(<LiquidResultsPanel />))
})
afterEach(() => {
  act(() => root.unmount()); host.remove()
  stop(); store.setPlayhead(0); resetLiquidRun(); vi.restoreAllMocks()
})

const slider = () => host.querySelector('input[type=range]') as HTMLInputElement
const maxChip = () => host.querySelector('#lr-max-chip')!.textContent

it('the readout and the tables follow the playhead', async () => {
  expect(slider().value).toBe('3')                      // opens at the latest sample
  expect((host.querySelector('#lr-max-chip') as HTMLElement).style.color).toBe('rgb(255, 204, 0)')
  await act(async () => store.setPlayhead(11))
  expect(slider().value).toBe('1')
  expect(maxChip()).toContain((330 - 273.15).toFixed(1))
  expect((host.querySelector('#lr-max-chip') as HTMLElement).style.color).toBe('rgb(204, 204, 204)')
})

it('the slider moves the playhead', async () => {
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => { set.call(slider(), '2'); slider().dispatchEvent(new Event('input', { bubbles: true })) })
  expect(store.playheadTime()).toBe(20)
  expect(slider().value).toBe('2')
  // Opening the panel again keeps the pick (it used to reset to the latest sample on mount).
  act(() => root.unmount()); root = createRoot(host)
  await act(async () => root.render(<LiquidResultsPanel />))
  expect(slider().value).toBe('2')
})
