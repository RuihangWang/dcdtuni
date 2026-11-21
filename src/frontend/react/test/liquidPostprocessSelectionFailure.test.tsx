import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getLocale, setLocale } from '../src/i18n'
import { LiquidPostprocessRightPanel } from '../src/components/LiquidPostprocessRightPanel'
import { LiquidResultsPanel } from '../src/components/LiquidResultsPanel'
import { fetchLiquidResults, liquidRunState, resetLiquidRun, subscribeLiquid } from '../src/liquid/liquidSession'
import { selectedLiquidRun, setSelectedLiquidRun } from '../src/liquid/liquidPostprocess'

vi.mock('../src/run/session', () => ({
  getCaseStatus: () => ({ liquidRuns: [{ name: 'run-old' }, { name: 'run-new' }] }),
  subscribeSession: () => () => {},
  scanCaseStatus: async () => {},
}))

let host: HTMLDivElement, root: Root
const initialLocale = getLocale()
const response = (value: unknown) => new Response(JSON.stringify(value), {
  status: 200, headers: { 'Content-Type': 'application/json' },
})
function result(runId: string, duration: number) {
  return { run_id: runId, converged: true, time_s: [0, duration],
    rack_summaries: [{ rack_id: 'rack', total_flow_kg_s: [1, 1],
      supply_temp_K: [300, 300], return_temp_K: [310, 310],
      servers: [{ server_id: 'server', flow_kg_s: [1, 1], max_chip_temp_K: [320, 320],
        heat_removed_W: [100, 100], cp_edge_ids: [] }] }], cp_details: [] }
}
const previous = result('old', 600)
function selector() { return host.querySelector('#lsm-pp-run-select') as HTMLSelectElement }
function choose(value: string) {
  const select = selector()
  select.value = value
  select.dispatchEvent(new Event('change', { bubbles: true }))
}
async function renderLoaded() {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(previous))
  setSelectedLiquidRun('run-old')
  await fetchLiquidResults('run-old')
  await act(async () => root.render(<><LiquidResultsPanel /><LiquidPostprocessRightPanel /></>))
}
beforeEach(() => {
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
  resetLiquidRun(); setSelectedLiquidRun(''); act(() => setLocale('en-US'))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount()); host.remove()
  resetLiquidRun(); setSelectedLiquidRun(''); act(() => setLocale(initialLocale)); vi.restoreAllMocks()
})

it('keeps the last valid record and its values while a historical read is pending and after a 404', async () => {
  await renderLoaded()
  let release!: (value: Response) => void
  vi.mocked(fetch).mockReturnValueOnce(new Promise(resolve => { release = resolve }))
  await act(async () => choose('run-new'))
  const pendingValue = selector().value
  const pendingGlobal = selectedLiquidRun()
  expect(selector().disabled).toBe(true)
  await act(async () => release(new Response('{}', { status: 404 })))
  expect(pendingValue).toBe('run-old')
  expect(pendingGlobal).toBe('run-old')
  expect(selector().value).toBe('run-old')
  expect(selector().title).toBe('run-old')
  expect(selectedLiquidRun()).toBe('run-old')
  expect(liquidRunState().results).toEqual(previous)
  expect(host.querySelector('#lr-time-samples')?.textContent).toContain('600.0 s')
  expect(host.querySelector('#lsm-pp-status')?.textContent).toContain('previous selection')
  expect(selector().disabled).toBe(false)
})

it('does not relabel last valid values when fetching a historical record throws', async () => {
  await renderLoaded()
  vi.mocked(fetch).mockRejectedValueOnce(new TypeError('network unavailable'))
  await act(async () => choose('run-new'))
  expect(selector().value).toBe('run-old')
  expect(selectedLiquidRun()).toBe('run-old')
  expect(liquidRunState().results).toEqual(previous)
  expect(host.querySelector('#lsm-pp-status')?.textContent).toContain('previous selection')
  expect(selector().disabled).toBe(false)
})

it('commits a historical record only after success, and can subsequently select explicit Latest', async () => {
  await renderLoaded()
  let release!: (value: Response) => void
  const next = result('new', 120), latest = result('latest', 20)
  vi.mocked(fetch).mockReturnValueOnce(new Promise(resolve => { release = resolve }))
    .mockResolvedValueOnce(response(latest))
  await act(async () => choose('run-new'))
  const pendingValue = selector().value
  await act(async () => release(response(next)))
  expect(pendingValue).toBe('run-old')
  expect(selector().value).toBe('run-new')
  expect(selectedLiquidRun()).toBe('run-new')
  expect(liquidRunState().results).toEqual(next)
  expect(host.querySelector('#lr-time-samples')?.textContent).toContain('120.0 s')
  expect(host.querySelector('#lsm-pp-status')).toBeNull()
  await act(async () => choose(''))
  expect(selector().value).toBe('')
  expect(selectedLiquidRun()).toBe('')
  expect(liquidRunState().results).toEqual(latest)
  expect(host.querySelector('#lr-time-samples')?.textContent).toContain('20.0 s')
  expect(vi.mocked(fetch).mock.calls.at(-1)?.[0]).toBe('/api/liquid/results')
})

it('does not commit a stale historical response superseded by a refresh of the last valid record', async () => {
  await renderLoaded()
  let release!: (value: Response) => void
  vi.mocked(fetch).mockReturnValueOnce(new Promise(resolve => { release = resolve }))
    .mockResolvedValueOnce(response(previous))
  await act(async () => choose('run-new'))
  await act(async () => { await fetchLiquidResults('run-old') })
  await act(async () => release(response(result('new', 120))))
  expect(selector().value).toBe('run-old')
  expect(selectedLiquidRun()).toBe('run-old')
  expect(liquidRunState().results).toEqual(previous)
  expect(selector().disabled).toBe(false)
})

it('rejects HTTP200 malformed results before publishing them or changing the record', async () => {
  await renderLoaded()
  const saved = liquidRunState().results
  const published: unknown[] = []
  const unsubscribe = subscribeLiquid(() => published.push(liquidRunState().results))
  vi.mocked(fetch).mockResolvedValueOnce(response({ run_id: 'new', malformed: true }))
  await act(async () => choose('run-new'))
  unsubscribe()
  expect(liquidRunState().results).toBe(saved)
  expect(published).toEqual([])
  expect(selector().value).toBe('run-old')
  expect(selectedLiquidRun()).toBe('run-old')
  expect(host.querySelector('#lr-time-samples')?.textContent).toContain('600.0 s')
  expect(host.querySelector('#lsm-pp-status')?.textContent).toContain('previous selection')
  expect(selector().disabled).toBe(false)
})

it('announces the failed read in Chinese while retaining the last valid record', async () => {
  act(() => setLocale('zh-CN'))
  await renderLoaded()
  const saved = liquidRunState().results
  vi.mocked(fetch).mockResolvedValueOnce(new Response('{}', { status: 404 }))
  await act(async () => choose('run-new'))
  expect(host.querySelector('[role="alert"]')?.textContent).toBe('无法读取此求解记录，继续显示上一次有效记录。')
  expect(selector().value).toBe('run-old')
  expect(liquidRunState().results).toBe(saved)
})
