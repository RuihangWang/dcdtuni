import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import transient from './fixtures/liquidPostprocess/shinaide_transient.json'
import steadyLegacy from './fixtures/liquidPostprocess/shinaide_steady_legacy.json'
import { LiquidHeatPanel } from '../src/components/LiquidHeatPanel'
import { getLocale, setLocale } from '../src/i18n'

/** Values of the real Shinaide result fixture (run b9bca1f1, t = 20 s), as result.json writes them. */
const charts: { label: string; points: number[] }[][] = []
vi.mock('../src/components/TimeSeriesChart', () => ({
  TimeSeriesChart: ({ series }: { series: { label: string; points: number[] }[] }) => {
    charts.push(series)
    return <div data-testid="chart">{series.map((s) => s.label).join('|')}</div>
  },
}))

let host: HTMLDivElement, root: Root
const originalLocale = getLocale()
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  charts.length = 0
  act(() => setLocale('en-US'))
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => { root.unmount(); setLocale(originalLocale) })
  host.remove(); vi.unstubAllGlobals()
})
const value = (id: string) => host.querySelector<HTMLElement>(`[data-heat-value="${id}"]`)!.textContent
const c = (k: number) => `${(k - 273.15).toFixed(2)} °C`

describe('LiquidHeatPanel', () => {
  it('reads heat, HX temperatures and the operating inputs from the saved result', () => {
    act(() => root.render(<LiquidHeatPanel raw={JSON.parse(JSON.stringify(transient))} sample={2} transient />))
    expect(host.querySelector('[data-heat-source]')!.textContent).toBe('Read from the saved result (b9bca1f1), t = 20.0 s.')
    expect(value('heat-input')).toBe('25.20 kW')
    expect(value('heat-rejection')).toBe(`${(20509.550510529254 / 1000).toFixed(2)} kW`)
    expect(value('storage-rate')).toBe('4690.4 W')
    expect(value('energy-balance')).toBe(`${(-5.424226401373744e-09).toExponential(2)} W`)
    expect(value('mass-residual')).toBe(`${(5.551115123125783e-17).toExponential(2)} kg/s`)
    expect(value('hx-model-m_30_hx')).toBe('Mixed volumes')
    expect(value('hx-primary-inlet-m_30_hx')).toBe(c(293.15))
    expect(value('hx-primary-return-m_30_hx')).toBe(c(298.0565910312271))
    expect(value('hx-secondary-supply-m_30_hx')).toBe(c(307.8251237262553))
    expect(value('hx-secondary-inlet-m_30_hx')).toBe(c(319.37569684222296))
    expect(value('hx-primary-flow-m_30_hx')).toBe(`${(1 / 992.2 * 60000).toFixed(2)} L/min · 1.000 kg/s`)
    expect(value('hx-secondary-flow-m_30_hx')).toBe(`${(0.3527798367551716 / 1032 * 60000).toFixed(2)} L/min · 0.3528 kg/s`)
    expect(value('heat-multiplier')).toBe('1.200')
    expect(value('pump-m_30_pump')).toBe('0.700')
    expect(value('valves')).toContain('valve_1778059294098: 0.300')
    // Heat over time, in kW, from each sample's diagnostics.
    expect(charts.at(-1)!.map((s) => s.label)).toEqual(['Heat input', 'Primary heat rejection', 'Energy-storage rate'])
    expect(charts.at(-1)![0]!.points).toEqual(transient.diagnostics.map((d) => d.heat_input_W / 1000))
  })

  it('states that an older run did not record its HX model, and draws no time chart for one sample', () => {
    act(() => root.render(<LiquidHeatPanel raw={JSON.parse(JSON.stringify(steadyLegacy))} sample={0} transient={false} />))
    expect(value('hx-model-m_30_hx')).toBe('Not recorded (this run predates the record; mixed volumes was then the only model)')
    expect(host.querySelector('[data-testid="chart"]')).toBeNull()
    expect(value('heat-input')).toBe(`${(steadyLegacy.diagnostics[0]!.heat_input_W / 1000).toFixed(2)} kW`)
    expect(host.querySelector('[data-heat-value="law-pump"]')).toBeNull()  // no laws in the document: no section
  })

  it('names the solver laws the run recorded, and only those', () => {
    act(() => root.render(<LiquidHeatPanel raw={JSON.parse(JSON.stringify(transient))} sample={2} transient />))
    // This run was solved between the two 2026-10-21 pump fixes, before friction was versioned.
    expect(value('law-pump')).toBe('H = N²·H₀ − a·Q² (v2: reverse flow gained energy)')
    expect(value('law-hydrostatics')).toBe('ρ·g·Δh on every edge (v2)')
    expect(value('law-friction')).toBe('—')
  })

  it('marks the laws the backend restored for a run saved before they were recorded', () => {
    // What saved_run._with_solver_laws hands back for such a run.
    const raw = JSON.parse(JSON.stringify(steadyLegacy))
    raw.parameter_provenance = { ...raw.parameter_provenance, solver_laws: {
      pump_law: 'affinity_N2_times_H0_minus_aQ2/v1', hydrostatics: 'pipes_only_rho_g_dh/v1',
      friction_law: 'laminar64_swamee_jain_blend_uncapped/v1', hx_models: { m_30_hx: 'mixed-volumes' },
      inferred: ['friction_law', 'hx_models', 'hydrostatics', 'pump_law'] } }
    act(() => root.render(<LiquidHeatPanel raw={raw} sample={0} transient={false} />))
    const inferred = (law: string) => `${law} — inferred: this run predates the record, and this was then the only law`
    expect(value('law-pump')).toBe(inferred('H = N²·(H₀ − a·Q²) (v1: zero-head flow did not follow speed)'))
    expect(value('law-friction')).toBe(inferred('64/Re laminar blended with Swamee–Jain, uncapped (v1)'))
    expect(value('law-hydrostatics')).toBe(inferred('ρ·g·Δh on pipes only (v1)'))
    expect(value('hx-model-m_30_hx')).toBe(inferred('Mixed volumes'))
  })

  it('shows an unknown law id as it is', () => {
    const raw = JSON.parse(JSON.stringify(transient))
    raw.parameter_provenance.solver_laws.pump_law = 'some_future_law/v9'
    act(() => root.render(<LiquidHeatPanel raw={raw} sample={2} transient />))
    expect(value('law-pump')).toBe('some_future_law/v9')
  })
})
