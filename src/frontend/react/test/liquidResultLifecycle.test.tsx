import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LiquidResultsPanel } from '../src/components/LiquidResultsPanel'
import { isTransient, parseLiquidResults } from '../src/liquid/liquidResults'
import { fetchLiquidResults, resetLiquidRun } from '../src/liquid/liquidSession'
import { getLocale, setLocale } from '../src/i18n'

const payload = (extra: Record<string, unknown> = {}) => ({
  run_id: 'cancelled-at-initial-state', converged: true, final_loss: 5.222391337156296e-11, epochs: 1,
  time_s: [0], rack_summaries: [{ rack_id: 'rack', total_flow_kg_s: [1],
    supply_temp_K: [300], return_temp_K: [310], servers: [] }], cp_details: [], ...extra,
})
let host: HTMLDivElement, root: Root
const initialLocale = getLocale()
beforeEach(() => {
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
  resetLiquidRun(); act(() => setLocale('en-US'))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount()); host.remove(); resetLiquidRun(); act(() => setLocale(initialLocale)); vi.restoreAllMocks()
})
async function show(raw: unknown) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(raw), { status: 200 }))
  await act(async () => { await fetchLiquidResults(); root.render(<LiquidResultsPanel />) })
}
describe('liquid result lifecycle is separate from numerical initialization', () => {
  it('does not present a cancelled t0 DAE initializer as a complete steady solve', async () => {
    // Actual run-e9a79ec wire fields: cancellation retains one converged initializer.
    const raw = payload({ status: 'cancelled', completed: false,
      parameter_provenance: { transient: { durationS: 600 } } })
    await show(raw)
    expect(host.querySelector('#lr-status')!.textContent).toContain('Cancelled')
    expect(host.querySelector('#lr-status')!.textContent).toContain('Initial-state numerical solve converged')
    expect(host.querySelector('#lr-time-samples')!.textContent).toContain('/ 600.0 s')
    expect(host.querySelector('#lr-time-samples')!.textContent).not.toContain('steady')
    expect(parseLiquidResults(raw)!.converged).toBe(true)
    expect(parseLiquidResults(raw)!.time_s).toEqual([0])
  })
  it.each(['ready', 'paused', 'partial', 'running'])('labels %s transient accepted samples as incomplete', async status => {
    await show(payload({ status, completed: false, analysis: { mode: 'transient', initialization: 'steady' },
      expected_duration_s: 600 }))
    expect(host.querySelector('#lr-status')!.textContent).toContain('Transient incomplete')
    expect(host.querySelector('#lr-time-samples')!.textContent).not.toContain('steady')
  })
  it('shows failed transport independently of converged accepted samples', async () => {
    await show(payload({ status: 'failed', completed: false, time_s: [0, 10],
      analysis: { mode: 'transient', initialization: 'steady' }, expected_duration_s: 600 }))
    expect(host.querySelector('#lr-status')!.textContent).toContain('Run failed')
    expect(host.querySelector('#lr-status')!.textContent).not.toContain('Transient completed')
  })
  it('identifies a completed one-sample transient by mode instead of sample count', async () => {
    const raw = payload({ status: 'completed', completed: true,
      analysis: { mode: 'transient', initialization: 'steady' }, expected_duration_s: 0 })
    await show(raw)
    expect(isTransient(parseLiquidResults(raw)!)).toBe(true)
    expect(host.querySelector('#lr-status')!.textContent).toContain('Transient completed')
    expect(host.querySelector('#lr-time-samples')!.textContent).not.toContain('steady')
  })
  it('requires the requested horizon even when an inconsistent payload says completed', async () => {
    await show(payload({ status: 'completed', completed: true, time_s: [0, 10],
      analysis: { mode: 'transient' }, expected_duration_s: 600 }))
    expect(host.querySelector('#lr-status')!.textContent).toContain('Transient incomplete')
  })
  it('preserves explicit direct steady results and ignores stale project transient provenance', async () => {
    await show(payload({ status: 'completed', completed: true, analysis: { mode: 'steady', initialization: 'steady' },
      expected_duration_s: 0, parameter_provenance: { transient: { durationS: 600 } } }))
    expect(host.querySelector('#lr-status')!.textContent).toContain('Steady converged')
    expect(host.querySelector('#lr-time-samples')!.textContent).toContain('steady')
  })
  it('keeps numerical-only legacy single-sample results readable', async () => {
    await show(payload())
    expect(host.querySelector('#lr-status')!.textContent).toContain('Numerical solve converged')
    expect(host.querySelector('#lr-time-samples')!.textContent).toContain('steady')
  })
  it('localizes lifecycle and diagnostic labels together', async () => {
    act(() => setLocale('zh-CN'))
    await show(payload({ status: 'cancelled', completed: false, analysis: { mode: 'transient', initialization: 'steady' },
      expected_duration_s: 600 }))
    const text = host.querySelector('#lr-status')!.textContent!
    expect(text).toContain('已取消')
    expect(text).toContain('初态数值求解已收敛')
    expect(text).toContain('残差')
    expect(text).toContain('迭代')
    expect(text).not.toContain('Loss')
  })
})
