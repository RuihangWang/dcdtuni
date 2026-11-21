import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ledgerResponse from './fixtures/liquidPostprocess/heat_ledger_shinaide.json'
import transient from './fixtures/liquidPostprocess/shinaide_transient.json'
import { HybridHeatLedgerPanel, HybridPostprocessSwitch, isHybridProject } from '../src/components/HybridHeatLedgerPanel'
import { getLocale, setLocale } from '../src/i18n'
import { fetchLiquidResults, resetLiquidRun } from '../src/liquid/liquidSession'
import type { CfdProject } from '../src/schema/cfdproj'

/**
 * The heat ledger as GET /api/hybrid/heat-ledger returns it for Shinaide
 * (2026-10-20 air run on liquid run 3153c5d4; servers cut to rack m_1).
 */
let host: HTMLDivElement, root: Root
const originalLocale = getLocale()
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  act(() => { setLocale('en-US'); resetLiquidRun() })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => { root.unmount(); setLocale(originalLocale); resetLiquidRun() })
  host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})
const value = (id: string) => host.querySelector<HTMLElement>(`[data-ledger-value="${id}"]`)!.textContent
const kw = (w: number) => `${(w / 1000).toFixed(2)} kW`

describe('HybridHeatLedgerPanel', () => {
  it('shows the ledger totals, its liquid source and pairing, and every server', async () => {
    const request = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) =>
      String(url) === '/api/hybrid/heat-ledger' ? json(ledgerResponse) : json({}, 404))
    await act(async () => root.render(<HybridHeatLedgerPanel />))
    expect(request.mock.calls[0]![0]).toBe('/api/hybrid/heat-ledger')
    const totals = ledgerResponse.ledger.totals
    expect(value('total-it')).toBe(kw(totals.total_it_W))
    expect(value('cp-input')).toBe(kw(totals.cp_input_W))
    expect(value('liquid-removed')).toBe(kw(totals.liquid_removed_W))
    expect(value('storage')).toBe('0.0 W')
    expect(value('air-residual')).toBe(kw(totals.air_residual_W))
    expect(host.querySelector('[data-ledger-source]')!.textContent).toBe(
      'From liquid run 3153c5d4d0144e7eba11adc40ee0b1cf, sample 10 (t = 20.0 s), heat multiplier 1.000')
    expect(host.querySelector('[data-ledger-identity]')!.textContent).toBe('Liquid run identity verified')
    expect(host.querySelector('[data-ledger-pairing]')!.textContent).toBe('Paired with the active .cfdrun')
    const rows = [...host.querySelectorAll<HTMLTableRowElement>('[data-ledger-servers] tbody tr')]
    expect(rows.map((r) => r.dataset.serverUid)).toEqual(['m_1/e_1', 'm_1/e_2', 'm_1/e_3', 'm_1/e_4'])
    const e2 = ledgerResponse.ledger.servers[1]!
    expect([...rows[1]!.cells].map((td) => td.textContent)).toEqual(
      ['m_1/e_2', e2.total_it_W.toFixed(1), e2.liquid_removed_W.toFixed(3), e2.air_residual_W.toFixed(3)])
  })

  it('names servers through the loaded liquid result and offers to open the ledger source run', async () => {
    const request = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) =>
      String(url) === '/api/hybrid/heat-ledger' ? json(ledgerResponse) : json(transient))
    await act(async () => { await fetchLiquidResults(); root.render(<HybridHeatLedgerPanel />) })
    expect(host.querySelector('[data-server-uid="m_1/e_2"] td')!.textContent).toBe('Rack_A_01-server_2')
    const open = host.querySelector<HTMLButtonElement>('[data-ledger-open-run]')!
    await act(async () => open.click())
    expect(request.mock.calls.at(-1)![0]).toBe('/api/liquid/results?run=run-3153c5d4d0144e7eba11adc40ee0b1cf')
  })

  it('explains a missing ledger and flags one that no longer pairs with the active .cfdrun', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ code: 'heat_ledger_missing', error: 'none' }, 404))
    await act(async () => root.render(<HybridHeatLedgerPanel />))
    expect(host.querySelector('[data-ledger-missing]')!.textContent).toContain('No hybrid air run yet')
    act(() => root.unmount()); root = createRoot(host)
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ ...ledgerResponse, matches_active_cfdrun: false }))
    await act(async () => root.render(<HybridHeatLedgerPanel />))
    expect(host.querySelector('[data-ledger-pairing]')!.textContent).toBe('Not paired with the active .cfdrun: the ledger belongs to another air export')
  })
})

describe('HybridPostprocessSwitch', () => {
  const hybrid = { runDefaults: { hybridHeatBudget: { version: 2 } } } as unknown as CfdProject
  it('appears only for a hybrid project and switches between the air field and the liquid network', () => {
    expect(isHybridProject(hybrid)).toBe(true)
    expect(isHybridProject(null)).toBe(false)
    const modes: string[] = []
    act(() => root.render(<HybridPostprocessSwitch project={hybrid} mode="liquid" onMode={(m) => modes.push(m)} />))
    const [air, liquid] = [...host.querySelectorAll<HTMLButtonElement>('#lq-pp-hybrid-switch button')]
    expect(liquid!.getAttribute('aria-pressed')).toBe('true')
    act(() => air!.click())
    expect(modes).toEqual(['air'])
    act(() => root.render(<HybridPostprocessSwitch project={null} mode="liquid" onMode={() => {}} />))
    expect(host.querySelector('#lq-pp-hybrid-switch')).toBeNull()
  })
})
