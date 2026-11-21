/** Alignment of the MMS analytic reference curves against a run's frame-time
 * axis — extracted from LiquidRuntimeMonitor so the honesty gate is unit-
 * testable independent of React, canvas and the SSE client.
 *
 * The fixture curves (generated module mmsAnalytic.ts, sha-bound to
 * docs/benchmarks/liquid-dae-mms-product-20260928/analytic_curves.json) are
 * sampled over seq 1..N_t-1, i.e. t = dt .. 60 s of a 61-frame dt=1 s run.
 * A replay's frame history normally starts at the seq-0 initial frame (t=0),
 * so the run axis is tail-aligned with the fixture axis: offset 1 with the
 * initial frame, 0 for a stream that lacks it. Anything else — a partial run,
 * a different dt, a shifted or stretched axis — is NOT comparable and the
 * monitor must draw nothing.
 */
import { mmsAnalyticCurves } from './mmsAnalytic'

export type AnalyticScenario = 'load' | 'supply'
export type AnalyticCurveKey = 'analytic_solid_K' | 'analytic_primary_K' | 'analytic_secondary_K'

export interface AnalyticAlignment {
  /** Index shift between the run's time axis and the fixture axis (0 or 1). */
  offset: number
  scenario: AnalyticScenario
}

/** Returns the alignment when `time` is a sample-for-sample replay of the
 * fixture axis, otherwise null. Callers gate ALL overlay drawing on this. */
export function alignAnalyticAxis(time: number[], scenario: AnalyticScenario): AnalyticAlignment | null {
  const curves = mmsAnalyticCurves[scenario]
  const offset = time.length - curves.t_s.length
  if (offset !== 0 && offset !== 1) return null
  for (let i = 0; i < curves.t_s.length; i++) {
    const sample = time[offset + i]
    if (sample === undefined || Math.abs(sample - (curves.t_s[i] as number)) >= 1e-6) return null
  }
  return { offset, scenario }
}

/** Celsius points for `key`, padded to the run's time axis with leading NaN.
 * TimeSeriesChart breaks the line at NaN instead of shifting a short array,
 * so padding (never truncation) is what keeps sample i at time[offset+i]. */
export function analyticCelsiusPoints(alignment: AnalyticAlignment, key: AnalyticCurveKey): number[] {
  const curves = mmsAnalyticCurves[alignment.scenario]
  return [...new Array<number>(alignment.offset).fill(NaN), ...curves[key].map(v => v - 273.15)]
}

/** Kelvin tolerance for the solid-trace identity check. A replay of the same
 * fixture and scenario is deterministic (Newton residuals ~1e-10, BE in
 * float64), so genuine replays land within orders of magnitude of this; the
 * closest WRONG scenario (e.g. load factor 1.5 instead of the fixture's 2)
 * moves the solid temperature by ≥0.1 K within a few samples. 0.02 K sits far
 * above numerical noise (including cross-version/thread jitter) and far below
 * any scenario difference. */
export const SOLID_TRACE_TOLERANCE_K = 0.02

/** Fixture-identity check: the time axis alone does not identify the fixture —
 * ANY run configured to duration 60 s / dt 1 s would axis-match. The receipt's
 * own DAE solid-temperature trace (`dae_solid_K`, part of the sha-bound
 * generated module) is the identity: a run that truly replays the fixture and
 * scenario follows it sample-for-sample; a different case, a different load
 * factor or a different scenario does not. `chipTempK` is the run's raw
 * (Kelvin) solid trace over ALL frames including the seq-0 initial frame. */
export function solidTraceMatches(chipTempK: (number | undefined)[], alignment: AnalyticAlignment): boolean {
  const dae = mmsAnalyticCurves[alignment.scenario].dae_solid_K
  if (chipTempK.length !== dae.length + alignment.offset) return false
  for (let i = 0; i < dae.length; i++) {
    const v = chipTempK[alignment.offset + i]
    if (v === undefined || !Number.isFinite(v)) return false
    if (Math.abs(v - (dae[i] as number)) > SOLID_TRACE_TOLERANCE_K) return false
  }
  return true
}
