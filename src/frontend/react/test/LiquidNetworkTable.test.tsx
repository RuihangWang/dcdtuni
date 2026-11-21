import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import transient from './fixtures/liquidPostprocess/shinaide_transient.json'
import { LiquidNetworkMetricCards, LiquidNetworkTable } from '../src/components/LiquidNetworkTable'
import { getLocale, setLocale } from '../src/i18n'
import type { NetworkRow } from '../src/liquid/liquidNetwork'

/**
 * The network table renders the values of the real Shinaide result fixture
 * (run b9bca1f1, t = 20 s = fixture sample 2); each expected string is built
 * here from the fixture arrays, not from the module under test.
 */
const S = 2
const rho = transient.fluid.rho_kg_m3
const raw = () => JSON.parse(JSON.stringify(transient))
const idx = (e: string) => transient.edge_ids.indexOf(e)
const nodeIdx = (n: string) => transient.node_ids.indexOf(n)
const c = (k: number) => (k - 273.15).toFixed(2)

let host: HTMLDivElement, root: Root
const originalLocale = getLocale()
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  act(() => setLocale('en-US'))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => { root.unmount(); setLocale(originalLocale) })
  host.remove(); vi.unstubAllGlobals()
})
const row = (edgeId: string) => host.querySelector<HTMLTableRowElement>(`tr[data-edge-id="${edgeId}"]`)!
const cell = (edgeId: string, col: string) => row(edgeId).querySelector<HTMLElement>(`[data-col="${col}"]`)!

describe('LiquidNetworkTable', () => {
  it('lists every edge with flow, velocity, Δp and temperatures equal to result.json', () => {
    act(() => root.render(<LiquidNetworkTable raw={raw()} sample={S} />))
    expect(host.querySelectorAll('tbody tr[data-edge-id]')).toHaveLength(transient.edge_ids.length)
    expect(host.querySelector('[data-network-count]')!.textContent).toBe(`Showing ${transient.edge_ids.length} of ${transient.edge_ids.length}`)
    const valve = 'm_32_control_valve_1778059337713'
    const m = Math.abs(transient.edge_flow_kg_s[idx(valve)]![S]!)
    expect(cell(valve, 'lpm').firstChild!.textContent).toBe((m / rho * 60000).toFixed(3))
    expect(cell(valve, 'kgs').textContent).toBe(m.toFixed(5))
    expect(cell(valve, 'velocity').textContent).toBe((m / (rho * Math.PI * 0.025 * 0.025 / 4)).toFixed(3))
    const pIn = transient.node_pressure_Pa[nodeIdx(`${valve}_in`)]![S]!, pOut = transient.node_pressure_Pa[nodeIdx(`${valve}_out`)]![S]!
    // The narrow table shows the loss (elevation removed); the static Δp is its tooltip.
    const yIn = transient.node_positions_m[nodeIdx(`${valve}_in`)]![1]!, yOut = transient.node_positions_m[nodeIdx(`${valve}_out`)]![1]!
    expect(cell(valve, 'loss').firstChild!.textContent!.trim()).toBe(((pOut - pIn + rho * 9.81 * (yOut - yIn)) / 1000).toFixed(2))
    expect(cell(valve, 'loss').title).toBe(`Δp kPa: ${((pOut - pIn) / 1000).toFixed(3)}`)
    expect(cell(valve, 'temps').firstChild!.textContent).toBe(c(transient.node_temperature_K[nodeIdx(`${valve}_out`)]![S]!))
    expect(cell(valve, 'outlet').textContent).toBe(c(transient.edge_outlet_temp_K[idx(valve)]![S]!))
    // Actual direction (rack -> ring), named through object_bindings, and flagged as reversed.
    const text = row(valve).textContent!
    expect(text).toContain('valve_1778059337713')
    expect(row(valve).querySelector('.lq-pp-badge.reversed')).not.toBeNull()
    expect(row(valve).querySelector('.lq-pp-sub')!.textContent).toBe(
      'Valve · valve_1778059337713 · outlet → valve_1778059337713 · inlet')
    // Cold plate rows read "server · cold plate <pair>".
    expect(row('m_1_e_1_cp_CP-CU_1').querySelector('.lq-pp-main')!.textContent).toBe('Rack_A_01-server_1 · cold plate CP-CU_1')
    expect(cell('m_1_e_1_cp_CP-CU_1', 'velocity').textContent).toBe('—')
    expect(row('m_30_hx').querySelector('.lq-pp-sub')!.textContent).toBe('Heat exchanger · CDU_1 · pump discharge → CDU_1 · supply')
  })

  it('filters by type, searches names and nodes, and sorts by a column', () => {
    act(() => root.render(<LiquidNetworkTable raw={raw()} sample={S} />))
    const select = host.querySelector<HTMLSelectElement>('select')!
    act(() => { select.value = 'cold_plate'; select.dispatchEvent(new Event('change', { bubbles: true })) })
    expect([...host.querySelectorAll<HTMLElement>('tbody tr[data-edge-id]')].map((r) => r.dataset.edgeId))
      .toEqual(['m_1_e_1_cp_CP-CU_1', 'm_1_e_2_cp_CP-CU_1', 'm_1_e_3_cp_CP-CU_1', 'm_1_e_4_cp_CP-CU_1'])
    act(() => { select.value = 'all'; select.dispatchEvent(new Event('change', { bubbles: true })) })
    const search = host.querySelector<HTMLInputElement>('input[type=search]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    act(() => { setter.call(search, 'supply branch'); search.dispatchEvent(new Event('input', { bubbles: true })) })
    expect([...host.querySelectorAll<HTMLElement>('tbody tr[data-edge-id]')].map((r) => r.dataset.edgeId))
      .toEqual(['m_1_e_1_branch_in', 'm_1_e_2_branch_in', 'm_1_e_3_branch_in', 'm_1_e_4_branch_in'])
    act(() => { setter.call(search, ''); search.dispatchEvent(new Event('input', { bubbles: true })) })
    const flowHeader = [...host.querySelectorAll<HTMLButtonElement>('thead button')].find((b) => b.textContent!.startsWith('Flow L/min'))!
    act(() => flowHeader.click())
    const first = host.querySelector<HTMLElement>('tbody tr[data-edge-id]')!.dataset.edgeId!
    const largest = transient.edge_ids.reduce((a, b) => (Math.abs(transient.edge_flow_kg_s[idx(b)]![S]!) > Math.abs(transient.edge_flow_kg_s[idx(a)]![S]!) ? b : a))
    expect(Math.abs(transient.edge_flow_kg_s[idx(first)]![S]!)).toBe(Math.abs(transient.edge_flow_kg_s[idx(largest)]![S]!))
  })

  it('reports a row click and lights the rows of the selection', () => {
    const picked: NetworkRow[] = []
    const focus = ['m_1_e_2_branch_in', 'm_1_e_2_branch_out', 'm_1_e_2_cp_CP-CU_1']
    act(() => root.render(<LiquidNetworkTable raw={raw()} sample={S} focusEdgeIds={focus}
      activeEdgeId="m_1_e_2_cp_CP-CU_1" onPickRow={(r) => picked.push(r)} />))
    expect([...host.querySelectorAll<HTMLElement>('tr.lq-pp-row-focus')].map((r) => r.dataset.edgeId)).toEqual(focus)
    expect(host.querySelector<HTMLElement>('tr.lq-pp-row-active')!.dataset.edgeId).toBe('m_1_e_2_cp_CP-CU_1')
    act(() => row('m_32_control_valve_1778059337713').click())
    expect(picked.map((r) => [r.edgeId, r.select])).toEqual([['m_32_control_valve_1778059337713',
      { uid: 'm_32', node: { type: 'fitting', uid: 'm_32', fittingId: 'valve_1778059337713' } }]])
  })

  it('greys a stagnant edge and an edge at a dry node, with the reason', () => {
    const r = raw()
    r.edge_stagnant[idx('m_1_e_4_branch_in')] = true
    r.node_observable[nodeIdx('m_1_e_4_in')][S] = false
    act(() => root.render(<LiquidNetworkTable raw={r} sample={S} />))
    expect(row('m_1_e_4_branch_in').dataset.inactive).toBe('stagnant')
    expect(row('m_1_e_4_branch_in').title).toContain('Stagnant')
    expect(cell('m_1_e_4_branch_in', 'lpm').firstChild!.textContent).toBe('0.000')
    expect(row('m_1_e_4_cp_CP-CU_1').dataset.inactive).toBe('unobservable')
    expect(row('m_1_e_4_cp_CP-CU_1').title).toBe('No coolant flows into Rack_A_01-server_4 · inlet; its temperature is undefined.')
    expect(row('m_1_e_1_branch_in').dataset.inactive).toBeUndefined()
  })

  it('says what is missing on a run written before the result topology', async () => {
    const legacy = (await import('./fixtures/liquidPostprocess/shinaide_steady_legacy.json')).default
    act(() => root.render(<LiquidNetworkTable raw={JSON.parse(JSON.stringify(legacy))} sample={0} />))
    expect(host.querySelector('[data-network-no-topology]')).not.toBeNull()
    expect(host.querySelectorAll('tbody tr[data-edge-id]')).toHaveLength(legacy.edge_ids.length)
  })
})

describe('LiquidNetworkMetricCards', () => {
  it('shows total flow, pump head and speed, chip, loop temperatures and the weakest branch', () => {
    act(() => root.render(<LiquidNetworkMetricCards raw={raw()} sample={S} />))
    const metric = (id: string) => host.querySelector<HTMLElement>(`[data-metric="${id}"]`)!.textContent!
    const pump = 0.35277983675517166
    expect(metric('total-flow')).toBe(`${(pump / rho * 60000).toFixed(2)} L/min${pump.toPrecision(4)} kg/s`)
    expect(metric('pump')).toContain('Speed ratio 0.7')
    const head = transient.node_pressure_Pa[nodeIdx('m_30_pump_discharge')]![S]! - transient.node_pressure_Pa[nodeIdx('m_30_return')]![S]!
    expect(metric('pump')).toContain(`Head ${(head / 1000).toFixed(2)} kPa`)
    expect(metric('max-chip')).toBe(`${c(316.64426642176846)} °CRack_A_01-server_4`)
    expect(metric('supply-return')).toBe(`${c(307.8251237262553)} °C / ${c(319.37569684222296)} °C`)
    expect(metric('min-branch')).toContain('Rack_A_01-server_1')
  })
})
