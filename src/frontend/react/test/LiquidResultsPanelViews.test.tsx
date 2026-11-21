import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import transient from './fixtures/liquidPostprocess/shinaide_transient.json'
import { LiquidResultsPanel } from '../src/components/LiquidResultsPanel'
import { getLocale, setLocale } from '../src/i18n'
import { fetchLiquidResults, resetLiquidRun } from '../src/liquid/liquidSession'
import { focusedLiquidEdge, setFocusedLiquidEdge } from '../src/liquid/liquidPostprocessFocus'
import { setResultSample } from '../src/liquid/liquidTempColor'

vi.mock('../src/components/TimeSeriesChart', () => ({
  TimeSeriesChart: ({ series }: { series: { label: string }[] }) => <div data-testid="chart">{series.map((s) => s.label).join('|')}</div>,
}))

/** The results panel over the real Shinaide result fixture (run b9bca1f1). */
let host: HTMLDivElement, root: Root
const originalLocale = getLocale()
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  act(() => { setLocale('en-US'); resetLiquidRun(); setResultSample(-1); setFocusedLiquidEdge(null) })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => { root.unmount(); setLocale(originalLocale); resetLiquidRun(); setFocusedLiquidEdge(null) })
  host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})
async function show(body: unknown, props: Parameters<typeof LiquidResultsPanel>[0] = {}) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json(body))
  await act(async () => { await fetchLiquidResults(); root.render(<LiquidResultsPanel {...props} />) })
}
const view = (v: string) => act(() => host.querySelector<HTMLButtonElement>(`[data-lq-view="${v}"]`)!.click())

describe('LiquidResultsPanel views', () => {
  it('labels a DAE run’s status as the Newton max scaled residual and the iteration total', async () => {
    await show(transient)
    view('overview')
    const status = host.querySelector('#lr-status')!.textContent!
    expect(status).toContain('Newton max scaled residual')
    expect(status).toContain('Newton iterations (total)')
    expect(host.querySelector('[data-status="residual"]')!.textContent).toBe((9.815458668072097e-11).toExponential(2))
    expect(host.querySelector('[data-status="iterations"]')!.textContent).toBe('14')
  })

  it('keeps the loss/epochs wording for a non-DAE result', async () => {
    await show({ ...transient, solver: 'adam_odil' })
    view('overview')
    expect(host.querySelector('#lr-status')!.textContent).toContain('Residual')
    expect(host.querySelector('#lr-status')!.textContent).not.toContain('Newton')
  })

  it('offers overview, network, heat and charts; a network row selects its object and focuses its edge', async () => {
    const picks: [string, unknown][] = []
    await show(transient, { onSelectObject: (uid, node) => picks.push([uid, node]) })
    expect([...host.querySelectorAll<HTMLElement>('[data-lq-view]')].map((b) => b.dataset.lqView))
      .toEqual(['overview', 'network', 'heat', 'charts'])
    view('network')
    expect(host.querySelector('#lq-pp-metrics')).not.toBeNull()
    expect(host.querySelectorAll('tbody tr[data-edge-id]')).toHaveLength(transient.edge_ids.length)
    act(() => host.querySelector<HTMLElement>('tr[data-edge-id="m_1_e_3_cp_CP-CU_1"]')!.click())
    expect(picks).toEqual([['m_1', { type: 'equipment', uid: 'm_1', equipmentId: 'e_3' }]])
    expect(focusedLiquidEdge()).toBe('m_1_e_3_cp_CP-CU_1')
    expect(host.querySelector<HTMLElement>('tr.lq-pp-row-active')!.dataset.edgeId).toBe('m_1_e_3_cp_CP-CU_1')
    view('heat')
    expect(host.querySelector('[data-heat-value="heat-input"]')!.textContent).toBe('25.20 kW')
    view('charts')
    // The charts follow the focused edge: a cold plate's three temperatures.
    expect(host.querySelector('#lq-pp-charts [data-testid="chart"]')!.textContent).toBe('Chip temperature|Coolant inlet|Coolant outlet')
    view('overview')
  })

  it('lights the rows of an outside selection', async () => {
    await show(transient, { focusEdgeIds: ['m_30_pump', 'm_30_hx'] })
    view('network')
    expect([...host.querySelectorAll<HTMLElement>('tr.lq-pp-row-focus')].map((r) => r.dataset.edgeId)).toEqual(['m_30_pump', 'm_30_hx'])
    view('overview')
  })

  it('follows the playhead sample in the network table', async () => {
    await show(transient)
    view('network')
    act(() => setResultSample(0))
    const pump = host.querySelector<HTMLElement>('tr[data-edge-id="m_30_pump"] [data-col="kgs"]')!.textContent
    expect(pump).toBe((0.509677232170846).toFixed(5))
    view('overview')
  })
})
