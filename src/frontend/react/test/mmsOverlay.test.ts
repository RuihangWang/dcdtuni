/** The MMS analytic-overlay honesty gate as pure logic: which run time axes
 * are sample-for-sample replays of the fixture, and where each fixture sample
 * lands on the run axis. A one-sample shift of the reference curve must fail
 * here — the integration test only proves the wiring, not the placement. */
import { describe, expect, it } from 'vitest'
import { mmsAnalyticCurves } from '../src/liquid/mmsAnalytic'
import { alignAnalyticAxis, analyticCelsiusPoints, solidTraceMatches, SOLID_TRACE_TOLERANCE_K } from '../src/liquid/mmsOverlay'

const fixture = mmsAnalyticCurves.load.t_s
/** The canonical replay axis: seq-0 initial frame at t=0, then the fixture grid. */
const replayAxis = () => [0, ...fixture]

describe('alignAnalyticAxis', () => {
  it('tail-aligns a full 61-frame replay that includes the seq-0 initial frame', () => {
    expect(alignAnalyticAxis(replayAxis(), 'load')).toEqual({ offset: 1, scenario: 'load' })
  })

  it('aligns a stream without the initial frame at offset 0', () => {
    expect(alignAnalyticAxis([...fixture], 'load')).toEqual({ offset: 0, scenario: 'load' })
  })

  it('rejects a partial run', () => {
    expect(alignAnalyticAxis([0, ...fixture.slice(0, 30)], 'load')).toBeNull()
    expect(alignAnalyticAxis([0], 'load')).toBeNull()
    expect(alignAnalyticAxis([], 'load')).toBeNull()
  })

  it('rejects a correct-length axis at the wrong dt — caught per-sample, not by length', () => {
    // 61 samples, dt=2 s: the length clause alone would pass this through.
    const stretched = [0, ...fixture.map(t => t * 2)]
    expect(stretched).toHaveLength(61)
    expect(alignAnalyticAxis(stretched, 'load')).toBeNull()
  })

  it('rejects a single perturbed sample beyond tolerance and accepts one within it', () => {
    const perturbed = replayAxis()
    perturbed[30] = (perturbed[30] as number) + 1e-5
    expect(alignAnalyticAxis(perturbed, 'load')).toBeNull()
    const jittered = replayAxis()
    jittered[30] = (jittered[30] as number) + 1e-9
    expect(alignAnalyticAxis(jittered, 'load')).toEqual({ offset: 1, scenario: 'load' })
  })

  it('aligns both scenarios independently', () => {
    expect(alignAnalyticAxis([0, ...mmsAnalyticCurves.supply.t_s], 'supply'))
      .toEqual({ offset: 1, scenario: 'supply' })
  })
})

describe('analyticCelsiusPoints', () => {
  it('places every fixture sample at its own time index — a one-sample shift fails', () => {
    const time = replayAxis()
    const alignment = alignAnalyticAxis(time, 'load')!
    const points = analyticCelsiusPoints(alignment, 'analytic_solid_K')
    expect(points).toHaveLength(time.length)
    // seq-0 has no fixture sample: NaN breaks the line there
    expect(Number.isNaN(points[0])).toBe(true)
    const curve = mmsAnalyticCurves.load.analytic_solid_K
    for (let i = 0; i < curve.length; i++) {
      expect(points[i + 1]).toBeCloseTo((curve[i] as number) - 273.15, 12)
    }
  })

  it('pads nothing for an offset-0 stream', () => {
    const alignment = alignAnalyticAxis([...fixture], 'load')!
    const points = analyticCelsiusPoints(alignment, 'analytic_secondary_K')
    expect(points.some(Number.isNaN)).toBe(false)
    expect(points[0]).toBeCloseTo((mmsAnalyticCurves.load.analytic_secondary_K[0] as number) - 273.15, 12)
  })

  it('converts every key the monitor overlays', () => {
    const alignment = alignAnalyticAxis(replayAxis(), 'supply')!
    for (const key of ['analytic_solid_K', 'analytic_primary_K', 'analytic_secondary_K'] as const) {
      const points = analyticCelsiusPoints(alignment, key)
      expect(points).toHaveLength(61)
      expect(points[60]).toBeCloseTo((mmsAnalyticCurves.supply[key][59] as number) - 273.15, 12)
    }
  })
})

describe('solidTraceMatches (fixture identity — the axis alone does not identify the fixture)', () => {
  const daeLoad = mmsAnalyticCurves.load.dae_solid_K
  /** Frame-shaped Kelvin trace: seq-0 initial frame + the 60 fixture samples. */
  const traceOf = (samples: number[]) => [samples[0] as number, ...samples]
  const alignment = () => alignAnalyticAxis(replayAxis(), 'load')!

  it('accepts a run whose solid trace follows the receipt DAE trace', () => {
    expect(solidTraceMatches(traceOf(daeLoad), alignment())).toBe(true)
  })

  it('accepts numerical noise far below the tolerance', () => {
    const noisy = traceOf(daeLoad).map((v, i) => (i === 30 ? v + SOLID_TRACE_TOLERANCE_K / 10 : v))
    expect(solidTraceMatches(noisy, alignment())).toBe(true)
  })

  it('rejects a trace perturbed beyond the tolerance at a single sample', () => {
    const off = traceOf(daeLoad).map((v, i) => (i === 30 ? v + SOLID_TRACE_TOLERANCE_K * 2 : v))
    expect(solidTraceMatches(off, alignment())).toBe(false)
  })

  it('rejects the wrong scenario: a load replay is not a supply replay', () => {
    // The supply-step solid trace departs the load trace by ~0.5 K at t=60 —
    // five orders of magnitude above the tolerance.
    const supplyAlignment = alignAnalyticAxis(replayAxis(), 'supply')!
    expect(solidTraceMatches(traceOf(daeLoad), supplyAlignment)).toBe(false)
  })

  it('rejects different physics on an axis-identical grid (the Shinaide case: 338→353 K chips)', () => {
    // A reviewer-reachable false positive before the identity term: ANY case
    // configured to duration 60 s / dt 1 s axis-matches the fixture.
    const shinaideLike = traceOf(daeLoad).map((_, i) => 338 + (i * 15) / 60)
    expect(solidTraceMatches(shinaideLike, alignment())).toBe(false)
  })

  it('rejects a wrong load magnitude on the true fixture (factor 1.5 ≠ 2)', () => {
    // Halving the step response deviation: solid sits ~0.2 K below the fixture
    // trace after the step — still an order of magnitude above tolerance.
    const halfStep = traceOf(daeLoad).map((v, i) =>
      i <= 30 ? v : v - (v - (daeLoad[29] as number)) / 2)
    expect(solidTraceMatches(halfStep, alignment())).toBe(false)
  })

  it('rejects length mismatches and non-finite samples', () => {
    expect(solidTraceMatches(traceOf(daeLoad).slice(0, 60), alignment())).toBe(false)
    const withHole = traceOf(daeLoad)
    withHole[30] = undefined as unknown as number
    expect(solidTraceMatches(withHole, alignment())).toBe(false)
  })
})
