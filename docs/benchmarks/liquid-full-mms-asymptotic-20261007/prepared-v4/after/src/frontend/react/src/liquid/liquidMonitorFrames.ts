/**
 * The live solve stream, turned into plottable rows.
 *
 * `/api/liquid/solve` sends one SSE frame per logged epoch, and every frame
 * carries the WHOLE monitor snapshot: the loss terms, `cdu_monitor`, and one
 * `cp_monitor` entry per cold plate the run was asked to watch. That entry has
 * three numbers, and `_lsmOnSSE` (js/liquid_solve_monitor.js:286-302) pushes all
 * three as separate series — `T_chip`, `T_out` and `m`.
 *
 * All three matter and they answer different questions. A chip temperature
 * alone cannot tell a plate that is cooling well from one that is barely
 * flowing: a starved plate whose heat has nowhere to go still reads plausibly
 * until it runs away. `T_water_out` against the supply is the heat it actually
 * removed, and `m` is whether coolant is reaching it at all.
 *
 * Kept out of the panel because a frame parser is where a wire-format change
 * lands, and this one has to be testable without a running solve.
 */

import type { LiquidProgress } from './liquidSession'

/** One cold plate in one frame. A missing number stays missing rather than becoming 0. */
export interface CpSample {
  chip?: number
  waterOut?: number
  flow?: number
}

export interface EpochFrame {
  epoch: number
  total?: number
  losses: Record<string, number>
  cp: Record<string, CpSample>
  cdu: Record<string, number>
}

export type CpMetric = 'chip' | 'waterOut' | 'flow'

/** The three per-plate series, with the legacy's own series-name suffixes. */
export const CP_METRICS: { key: CpMetric; suffix: string }[] = [
  { key: 'chip', suffix: 'T_chip' },
  { key: 'waterOut', suffix: 'T_out' },
  { key: 'flow', suffix: 'm' },
]

function num(v: unknown): number | undefined {
  return typeof v === 'number' ? v : undefined
}

export function epochFrames(progress: LiquidProgress[]): EpochFrame[] {
  const out: EpochFrame[] = []
  for (const p of progress) {
    const epoch = p.epoch
    if (typeof epoch !== 'number') continue
    const losses: Record<string, number> = {}
    const raw = p.losses
    if (raw && typeof raw === 'object') {
      for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
        if (typeof v === 'number') losses[k] = v
      }
    }
    const cp: Record<string, CpSample> = {}
    const cpRaw = p.cp_monitor
    if (cpRaw && typeof cpRaw === 'object') {
      for (const [id, v] of Object.entries(cpRaw as Record<string, unknown>)) {
        const d = (v ?? {}) as Record<string, unknown>
        cp[id] = {
          chip: num(d.chip_temp_K) ?? num(d.T_chip),
          waterOut: num(d.T_water_out) ?? num(d.water_outlet_temp_K),
          flow: num(d.m) ?? num(d.mass_flow_kg_s),
        }
      }
    }
    const cdu: Record<string, number> = {}
    const cduRaw = p.cdu_monitor
    if (cduRaw && typeof cduRaw === 'object') {
      for (const [k, v] of Object.entries(cduRaw as Record<string, unknown>)) {
        if (typeof v === 'number') cdu[k] = v
      }
    }
    out.push({ epoch, total: num(p.total), losses, cp, cdu })
  }
  return out
}

/**
 * Ids as they arrive in `cp_monitor`, which is what the solve actually
 * reported — a plate renamed since the run still has its old curve.
 */
export function reportedCpIds(frames: EpochFrame[]): string[] {
  const ids = new Set<string>()
  for (const f of frames) for (const id of Object.keys(f.cp)) ids.add(id)
  return [...ids].sort()
}

/**
 * One plate's curve for one metric, one point per frame.
 *
 * NaN, not a shorter array: the chart plots points[i] against time[i], so
 * dropping a missing sample would slide every later epoch left.
 */
export function cpSeriesPoints(
  frames: EpochFrame[],
  id: string,
  metric: CpMetric,
  transform: (v: number) => number = (v) => v,
): number[] {
  return frames.map((f) => {
    const v = f.cp[id]?.[metric]
    return typeof v === 'number' ? transform(v) : NaN
  })
}

/** True when a curve has at least one real sample; an all-NaN one is not drawn. */
export function hasSamples(points: number[]): boolean {
  return points.some((v) => Number.isFinite(v))
}
