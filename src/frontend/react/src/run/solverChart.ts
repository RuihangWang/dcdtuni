/**
 * Axis maths for the solver monitor chart.
 *
 * Split out from the drawing so the parts that decide what a user READS off the
 * plot — the decade range of a log axis, the °C span, where the ticks fall —
 * are testable without a canvas.
 *
 * Ported from `_smDrawChart` (STL_generate.html:25354).
 */

import type { SeriesMap, SeriesPoint } from './solverMonitorParse'

export type ChartTab = 'residual' | 'temperature' | 'sensor'

/** The legacy per-field residual colours (STL_generate.html:25061). */
export const RESIDUAL_COLORS: Record<string, string> = {
  Ux: '#e06c75',
  Uy: '#e5c07b',
  Uz: '#61afef',
  T: '#c678dd',
  p_rgh: '#56b6c2',
  k: '#98c379',
  epsilon: '#d19a66',
}

/**
 * The rotating palette temperature and sensor series are coloured from (:25065).
 *
 * The SAME list the viewport balls and the sensor rows use — see
 * `sensorPalette.ts`. It was a second copy here, which is the shape a silent
 * drift takes: the legend and the ball disagree and no test knows.
 */
import { SENSOR_PALETTE } from './sensorPalette'

export const SERIES_COLORS = SENSOR_PALETTE

/** Absolute zero in kelvin — temperature series arrive in K and are plotted in °C. */
export const KELVIN_OFFSET = 273.15

/**
 * The x-span, across EVERY series including hidden ones.
 *
 * Hidden series still count here on purpose: hiding a curve should not shift
 * the time axis under the ones still shown, or the plot appears to jump when a
 * legend entry is clicked.
 */
export function stepRange(datasets: SeriesMap, names: string[]): [number, number] | null {
  let min = Infinity
  let max = -Infinity
  for (const name of names) {
    for (const p of datasets[name] ?? []) {
      if (p.step < min) min = p.step
      if (p.step > max) max = p.step
    }
  }
  if (min === Infinity) return null
  return [min, max <= min ? min + 1 : max]
}

/**
 * The decade range of the log-scale residual axis.
 *
 * Starts at [-8, 0] and only ever GROWS: a residual axis that autoscaled to the
 * data would rescale on every step early in a run, and a curve that keeps
 * changing height is one nobody can read a trend off. One decade of padding on
 * each side keeps the newest point off the frame edge, and the top is clamped
 * at 0 because an initial residual above 1 is not a thing worth axis room.
 */
export function residualYRange(datasets: SeriesMap, names: string[], hidden: Record<string, boolean>): [number, number] {
  let min = -8
  let max = 0
  for (const name of names) {
    if (hidden[name]) continue
    for (const p of datasets[name] ?? []) {
      // a zero residual (a component with nothing to solve) has no place on a log axis
      if (!(p.value > 0)) continue
      const lv = Math.log10(p.value)
      if (lv < min) min = lv
      if (lv > max) max = lv
    }
  }
  return [Math.floor(min) - 1, Math.min(0, Math.ceil(max) + 1)]
}

/**
 * The °C range of a temperature axis, with 10% padding.
 *
 * A minimum half-degree of padding, because a converged run's patch average
 * moves by millikelvin and a purely proportional pad would draw that as a
 * full-height wander.
 */
export function temperatureYRange(datasets: SeriesMap, names: string[], hidden: Record<string, boolean>): [number, number] {
  let min = Infinity
  let max = -Infinity
  for (const name of names) {
    if (hidden[name]) continue
    for (const p of datasets[name] ?? []) {
      const c = p.value - KELVIN_OFFSET
      if (c < min) min = c
      if (c > max) max = c
    }
  }
  if (min === Infinity) return [0, 100]
  const pad = Math.max((max - min) * 0.1, 0.5)
  return [min - pad, max + pad]
}

/**
 * The label for a decade on the residual axis.
 *
 * Plain decimals down to 1e-3 because `0.001` is what a user typed into the
 * residual-control box; exponents below that, where a decimal is unreadable.
 */
export function residualTickLabel(exponent: number): string {
  const value = Math.pow(10, exponent)
  return exponent >= -3 && exponent <= 0
    ? value.toFixed(Math.abs(exponent))
    : value.toExponential(0)
}

/**
 * A round tick step for the x axis.
 *
 * 1/2/5 x a power of ten, and the power may be NEGATIVE: a transient run's x
 * axis is physical seconds and can span 0.001 s, where a step of 1 would draw
 * no ticks at all.
 */
export function xTickStep(range: number, maxLabels: number): number {
  if (!(range > 0) || !(maxLabels > 0)) return 1
  const raw = range / maxLabels
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const ratio = raw / mag
  const step = ratio > 5 ? 10 * mag : ratio > 2 ? 5 * mag : ratio > 1 ? 2 * mag : mag
  return step > 0 ? step : 1
}

/** Round tick step for a linear (temperature) y axis, matching the legacy rule. */
export function yTickStep(range: number): number {
  let step = Math.pow(10, Math.floor(Math.log10(range / 5)))
  if (range / step > 10) step *= 2
  return step > 0 ? step : 1
}

/** The colour a series is drawn in, given which tab it belongs to. */
export function seriesColor(tab: ChartTab, name: string, index: number, sensorColor?: (n: string) => string): string {
  if (tab === 'residual') return RESIDUAL_COLORS[name] ?? '#ccc'
  if (tab === 'sensor' && sensorColor) return sensorColor(name)
  return SERIES_COLORS[index % SERIES_COLORS.length] as string
}

/** `avgT_ACU1_return` is the function-object name; the patch is what a user recognises. */
export function legendLabel(name: string): string {
  return name.replace(/^avgT_/, '')
}

export type { SeriesPoint }
