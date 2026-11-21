import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import transient from './fixtures/liquidPostprocess/shinaide_transient.json'
import steadyLegacy from './fixtures/liquidPostprocess/shinaide_steady_legacy.json'
import { LiquidResultCharts } from '../src/components/LiquidResultCharts'
import { getLocale, setLocale, t } from '../src/i18n'

/** Charts of the real Shinaide result fixture; expected points come from its arrays. */
const drawn: { yLabel?: string; series: { label: string; points: number[] }[] }[] = []
vi.mock('../src/components/TimeSeriesChart', () => ({
  TimeSeriesChart: (props: { yLabel?: string; series: { label: string; points: number[] }[] }) => {
    drawn.push(props)
    return <div data-testid="chart">{props.series.map((s) => s.label).join('|')}</div>
  },
}))

let host: HTMLDivElement, root: Root
const originalLocale = getLocale()
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  drawn.length = 0
  act(() => setLocale('en-US'))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => { root.unmount(); setLocale(originalLocale) })
  host.remove(); vi.unstubAllGlobals()
})
const raw = () => JSON.parse(JSON.stringify(transient))
const tab = (q: string) => act(() => host.querySelector<HTMLButtonElement>(`[data-chart-tab="${q}"]`)!.click())
const card = (id: string) => host.querySelector<HTMLElement>(`[data-series-value="${id}"]`)!.textContent
const nodeIdx = (n: string) => transient.node_ids.indexOf(n)

describe('LiquidResultCharts', () => {
  it('draws temperature, flow and pressure tabs of a server as time curves', () => {
    act(() => root.render(<LiquidResultCharts raw={raw()} sample={2} transient target={{ kind: 'server', key: 'm_1/e_3' }} />))
    const server = transient.rack_summaries[0]!.servers[2]!
    expect(drawn.at(-1)!.yLabel).toBe('°C')
    expect(drawn.at(-1)!.series.map((s) => s.label)).toEqual(['Chip temperature', 'Coolant inlet', 'Coolant outlet'])
    expect(drawn.at(-1)!.series[0]!.points).toEqual(server.max_chip_temp_K.map((k) => k - 273.15))
    expect(card('chip')).toBe(`${(server.max_chip_temp_K[2]! - 273.15).toFixed(2)} °C`)
    tab('flow')
    expect(drawn.at(-1)!.yLabel).toBe('L/min')
    expect(drawn.at(-1)!.series[0]!.points).toEqual(server.flow_kg_s.map((m) => m / 1032 * 60000))
    expect(card('flow')).toBe(`${(server.flow_kg_s[2]! / 1032 * 60000).toFixed(3)} L/min${server.flow_kg_s[2]!.toPrecision(4)} kg/s`)
    tab('pressure')
    expect(host.querySelector('[data-pressure-note]')!.textContent).toBe('Absolute pressure, including the hydrostatic head')
    expect(drawn.at(-1)!.series[0]!.points).toEqual(transient.node_pressure_Pa[nodeIdx('m_1_e_3_in')]!.map((p) => p / 1000))
    const gauge = host.querySelector<HTMLInputElement>('input[type=checkbox]')!
    act(() => gauge.click())
    expect(host.querySelector('[data-pressure-note]')!.textContent).toBe("Minus the loop's CDU return reference pressure, 150.000 kPa")
    expect(drawn.at(-1)!.series[0]!.points).toEqual(transient.node_pressure_Pa[nodeIdx('m_1_e_3_in')]!.map((p) => (p - 150000) / 1000))
  })

  it('shows value cards and no single-point chart for a steady result', () => {
    act(() => root.render(<LiquidResultCharts raw={JSON.parse(JSON.stringify(steadyLegacy))} sample={0} transient={false}
      target={{ kind: 'edge', edgeId: 'm_1_e_1_cp_CP-CU_1' }} />))
    expect(host.querySelector('[data-testid="chart"]')).toBeNull()
    const cp = steadyLegacy.cp_details[0]!
    expect(host.textContent).toContain('Steady values')
    expect(card('chip')).toBe(`${(cp.chip_temp_K[0]! - 273.15).toFixed(2)} °C`)
    expect(card('outlet')).toBe(`${(cp.water_outlet_temp_K[0]! - 273.15).toFixed(2)} °C`)
    tab('pressure')
    expect(card('p_inlet')).toBe(`${(cp.inlet_pressure_Pa[0]! / 1000).toFixed(3)} kPa`)
  })

  it('picks a category and an object, and follows a new target from outside', () => {
    act(() => root.render(<LiquidResultCharts raw={raw()} sample={2} transient target={null} picker />))
    const [category, object] = [...host.querySelectorAll<HTMLSelectElement>('select')]
    expect(category!.value).toBe('server')
    act(() => { category!.value = 'cdu'; category!.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(object!.value).toBe('m_30')
    expect(drawn.at(-1)!.series.map((s) => s.label)).toEqual(['Supply temperature', 'Return temperature', 'Primary inlet', 'Primary return'])
    act(() => root.render(<LiquidResultCharts raw={raw()} sample={2} transient target={{ kind: 'edge', edgeId: 'm_32_control_valve_1778059337713' }} picker />))
    expect(host.querySelectorAll('select')[0]!.value).toBe('pipe')
    expect(host.querySelectorAll('select')[1]!.value).toBe('m_32_control_valve_1778059337713')
  })

  it('names pipe parts in the object picker as the network table does (translated)', () => {
    act(() => setLocale('zh-CN'))
    act(() => root.render(<LiquidResultCharts raw={raw()} sample={2} transient target={{ kind: 'edge', edgeId: 'm_30_pump' }} picker />))
    const option = (id: string) => [...host.querySelectorAll('select')[1]!.options].find((o) => o.value === id)!.textContent
    expect(option('m_30_pump')).toBe(`CDU_1 · ${t('lq_pp.part_pump')}`)
    expect(t('lq_pp.part_pump')).not.toBe('pump')
    const branch = transient.edge_ids.find((e) => e.endsWith('_branch_in'))!
    expect(option(branch)).toContain(t('lq_pp.part_branch_in'))
    expect(option(branch)).not.toContain('branch_in')
  })
})
