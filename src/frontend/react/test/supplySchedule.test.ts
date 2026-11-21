/** Stage 3 contracts: supply-temperature schedule expansion + client-side
 * validation of the online primary_inlet_K command (backend channel: Stage 1,
 * commit 2015dfc5). Expansion semantics must match the MMS acceptance script
 * exactly: sample i carries the value active on (t_{i-1}, t_i], i.e. a step at
 * stepTime takes effect at the first sample with i*dt >= stepTime. */
import { describe, expect, it } from 'vitest'
import { applySupplySchedule, expandSupplySchedule } from '../src/liquid/liquidExperiment'
import { runtimeControlInputs } from '../src/liquid/liquidRuntimeClient'
import type { SavedDAEConfig } from '../src/schema/liquidrunDAE'

function minimalDAE(mode: 'steady' | 'transient'): SavedDAEConfig {
  return {
    analysis: { mode },
    primary_circuits: [{ edge_id: 'hx_1', inlet_K: 290 }],
  } as unknown as SavedDAEConfig
}

describe('expandSupplySchedule', () => {
  it('fixed keeps the scalar base inlet', () => {
    expect(expandSupplySchedule(undefined, 290, 5, 1)).toBe(290)
    expect(expandSupplySchedule({ mode: 'fixed' }, 290, 5, 1)).toBe(290)
  })

  it('step matches the acceptance-script semantics', () => {
    const out = expandSupplySchedule({ mode: 'step', from: 290, to: 295, stepTime: 2 }, 290, 5, 1)
    expect(out).toEqual([290, 290, 295, 295, 295])
  })

  it('ramp interpolates linearly and clamps at the target', () => {
    const out = expandSupplySchedule(
      { mode: 'ramp', from: 290, to: 295, stepTime: 1, rampTime: 2 }, 290, 5, 1) as number[]
    expect(out[0]).toBe(290)
    expect(out[1]).toBeCloseTo(290, 12)
    expect(out[2]).toBeCloseTo(292.5, 12)
    expect(out[3]).toBeCloseTo(295, 12)
    expect(out[4]).toBe(295)
  })

  it('sinusoid stays positive and periodic', () => {
    const out = expandSupplySchedule(
      { mode: 'sinusoid', mean: 290, amplitude: 2, period: 4 }, 290, 5, 1) as number[]
    expect(out[0]).toBeCloseTo(290, 12)
    expect(out[1]).toBeCloseTo(292, 12)
    expect(out[2]).toBeCloseTo(290, 10)
    expect(out.every(v => v > 0)).toBe(true)
  })

  it('rejects off-grid steps, non-positive temperatures and a dipping sinusoid', () => {
    expect(() => expandSupplySchedule({ mode: 'step', from: 290, to: 295, stepTime: 1.5 }, 290, 5, 1))
      .toThrow(/grid/)
    expect(() => expandSupplySchedule({ mode: 'step', from: -1, to: 295, stepTime: 1 }, 290, 5, 1))
      .toThrow(/positive/)
    expect(() => expandSupplySchedule({ mode: 'sinusoid', mean: 290, amplitude: 400, period: 4 }, 290, 5, 1))
      .toThrow(/positive/)
    expect(() => expandSupplySchedule({ mode: 'ramp', from: 290, to: 295, stepTime: 1, rampTime: 0 }, 290, 5, 1))
      .toThrow(/positive/)
  })
})

describe('applySupplySchedule guards', () => {
  const step = { mode: 'step', from: 285, to: 295, stepTime: 1 } as const

  it('returns steady analysis untouched — N_t=1 has no time axis to schedule on', () => {
    const dae = minimalDAE('steady')
    expect(applySupplySchedule(dae, step, 1, 1)).toBe(dae)
  })

  it('returns a sub-two-sample transient grid untouched instead of throwing off-grid', () => {
    // A steady-shaped grid would reject every positive stepTime; ignoring the
    // schedule keeps the scalar binding valid rather than failing the compile.
    const dae = minimalDAE('transient')
    expect(applySupplySchedule(dae, step, 1, 1)).toBe(dae)
  })

  it('expands onto every primary circuit when the grid carries time', () => {
    const dae = minimalDAE('transient')
    const out = applySupplySchedule(dae, step, 3, 1)
    expect(out).not.toBe(dae)
    expect(out.primary_circuits[0]!.inlet_K).toEqual([285, 295, 295])
  })
})

describe('runtimeControlInputs supply-temperature validation (fires before any session/transport)', () => {
  it('rejects malformed primary_inlet_K commands client-side', async () => {
    await expect(runtimeControlInputs({ primary_inlet_K: {} }))
      .rejects.toThrow(/nonempty edge-ID map/)
    await expect(runtimeControlInputs({ primary_inlet_K: { hx: 0 } }))
      .rejects.toThrow(/positive Kelvin/)
    await expect(runtimeControlInputs({ primary_inlet_K: { hx: 1500 } }))
      .rejects.toThrow(/positive Kelvin/)
    await expect(runtimeControlInputs({ primary_inlet_K: { '  ': 290 } }))
      .rejects.toThrow(/positive Kelvin/)
    await expect(runtimeControlInputs({ primary_inlet_K: [290] as unknown as Record<string, number> }))
      .rejects.toThrow(/nonempty edge-ID map/)
  })

  it('still requires at least one input field', async () => {
    await expect(runtimeControlInputs({}))
      .rejects.toThrow(/heat, pump, valve or supply-temperature/)
  })
})
