import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LiquidResultsPanel } from '../src/components/LiquidResultsPanel'
import { fetchLiquidResults, resetLiquidRun } from '../src/liquid/liquidSession'
import { getLocale, setLocale } from '../src/i18n'

vi.mock('../src/components/TimeSeriesChart', () => ({
  TimeSeriesChart: ({ series }: { series: { label: string }[] }) =>
    <div data-testid="chart-series">{series.map(s => <span key={s.label}>{s.label}</span>)}</div>,
}))
let host: HTMLDivElement, root: Root
const originalLocale = getLocale()
const nativeDialogDescriptors = {
  showModal: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal'),
  close: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close'),
}
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  // jsdom has no modal focus behavior. Model opening/focusing and closing;
  // the production component must restore its own invoking control on unmount.
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true, value: function (this: HTMLDialogElement) {
      this.open = true
      this.querySelector<HTMLButtonElement>('button')?.focus()
    },
  })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true, value: function (this: HTMLDialogElement) { this.open = false },
  })
  act(() => { resetLiquidRun(); setLocale('en-US') })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => { root.unmount(); resetLiquidRun(); setLocale(originalLocale) })
  host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals()
  for (const method of ['showModal', 'close'] as const) {
    const descriptor = nativeDialogDescriptors[method]
    if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, method, descriptor)
    else Reflect.deleteProperty(HTMLDialogElement.prototype, method)
  }
})
async function showChart(bindings = true) {
  const raw = {
    run_id: 'saved-run', converged: true, status: 'completed', completed: true,
    analysis: { mode: 'transient' }, expected_duration_s: 10, time_s: [0, 10],
    object_bindings: bindings ? {
      'rack-uid': { label: 'Rack East' },
      'rack-uid/server-uid': { label: 'Compute 01' },
    } : {},
    rack_summaries: [{
      rack_id: 'rack-uid', total_flow_kg_s: [0.1, 0.1],
      supply_temp_K: [295, 297], return_temp_K: [300, 302],
      servers: [{
        server_id: 'server-uid', flow_kg_s: [0.1, 0.1],
        max_chip_temp_K: [300, 305], heat_removed_W: [10, 12],
        cp_edge_ids: ['cp-internal-uuid'],
      }],
    }],
    cp_details: [{
      edge_id: 'cp-internal-uuid', chip_temp_K: [300, 305],
      water_inlet_temp_K: [295, 297], water_outlet_temp_K: [299, 302],
      inlet_pressure_Pa: [100, 100], outlet_pressure_Pa: [90, 90],
    }],
  }
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(raw), { status: 200 }))
  await act(async () => { await fetchLiquidResults(); root.render(<LiquidResultsPanel />) })
  const server = host.querySelector<HTMLButtonElement>('tbody button')!
  act(() => { server.focus(); server.click() })
  return server
}

it('identifies the selected cold plate by its result-bound rack and server', async () => {
  await showChart()
  const title = document.querySelector('#lsm-chart-group .prop-title')!.textContent!
  expect(title).toContain('Rack East / Compute 01')
  expect(title).not.toContain('cp-internal-uuid')
  const labels = document.querySelector('[data-testid="chart-series"]')!.textContent!
  expect(labels).toContain('Chip temperature')
  expect(labels).toContain('Coolant inlet')
  expect(labels).toContain('Coolant outlet')
})

it('localizes the chart and gives its close button a descriptive accessible name', async () => {
  act(() => setLocale('zh-CN')); await showChart()
  expect(document.querySelector('[data-testid="chart-series"]')!.textContent).toBe('芯片温度进水温度出水温度')
  const close = document.querySelector<HTMLButtonElement>('#lsm-chart-group button')!
  expect(close.getAttribute('aria-label')).toBe('关闭温度曲线')
  act(() => close.click())
  expect(document.querySelector('#lsm-chart-group')).toBeNull()
})

it('keeps legacy unlabelled result identities readable without changing their data', async () => {
  await showChart(false)
  expect(document.querySelector('#lsm-chart-group .prop-title')!.textContent).toContain('rack-uid / server-uid')
  expect(document.querySelector('#lsm-chart-group .prop-title')!.textContent).not.toContain('cp-internal-uuid')
})

it('allows the native dialog Escape cancellation to dismiss the chart', async () => {
  await showChart()
  const dialog = document.querySelector<HTMLDialogElement>('#lsm-chart-group')!
  expect(dialog.tagName).toBe('DIALOG')
  expect(dialog.getAttribute('aria-labelledby')).toBe('lsm-chart-title')
  act(() => dialog.dispatchEvent(new Event('cancel', { bubbles: false, cancelable: true })))
  expect(document.querySelector('#lsm-chart-group')).toBeNull()
})
it('returns keyboard focus to the selected server when the chart closes', async () => {
  const server = await showChart()
  const close = document.querySelector<HTMLButtonElement>('#lsm-chart-group button')!
  expect(document.activeElement).toBe(close)
  act(() => close.click())
  expect(document.querySelector('#lsm-chart-group')).toBeNull()
  expect(document.activeElement).toBe(server)
})
