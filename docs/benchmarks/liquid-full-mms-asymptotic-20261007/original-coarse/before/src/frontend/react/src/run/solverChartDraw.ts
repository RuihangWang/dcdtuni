/**
 * Painting the solver monitor chart.
 *
 * Canvas, not SVG: a steady run plots one point per iteration per field and
 * routinely reaches several thousand per series. As DOM nodes that is a panel
 * that stops scrolling, and nothing on this plot is individually hit-tested —
 * the legend is what a user clicks.
 *
 * The axis maths lives in `solverChart.ts` so it can be tested; this file is
 * only the strokes. Ported from `_smDrawChart` (STL_generate.html:25354).
 */

import {
  KELVIN_OFFSET,
  legendLabel,
  residualTickLabel,
  residualYRange,
  seriesColor,
  stepRange,
  temperatureYRange,
  xTickStep,
  yTickStep,
  type ChartTab,
} from './solverChart'
import type { SeriesMap } from './solverMonitorParse'

export interface DrawOptions {
  ctx: CanvasRenderingContext2D
  /** Logical (CSS) size; the caller has already applied the device-pixel transform. */
  width: number
  height: number
  tab: ChartTab
  datasets: SeriesMap
  hidden: Record<string, boolean>
  fontScale: number
  waitingText: string
  sensorColor?: (name: string) => string
}

/** Draws the plot and returns the series it found, in legend order. */
export function drawSolverChart(o: DrawOptions): string[] {
  const { ctx, width: lW, height: lH, tab, datasets, hidden, fontScale: fs } = o
  ctx.clearRect(0, 0, lW, lH)

  const names = Object.keys(datasets)
  if (names.length === 0) {
    ctx.fillStyle = '#666'
    ctx.font = `${Math.round(12 * fs)}px monospace`
    ctx.fillText(o.waitingText, lW / 2 - 50, lH / 2)
    return names
  }

  const isResidual = tab === 'residual'
  const labelFs = Math.round(9 * fs)
  // The left margin grows with the font: the y labels are `1e-07`, and a fixed
  // margin clips them the moment someone presses A+.
  const ml = Math.max(55, 50 * fs + 5)
  const mr = 10
  const mt = 10
  const mb = Math.max(25, 20 * fs + 5)
  const cw = lW - ml - mr
  const ch = lH - mt - mb
  if (cw < 20 || ch < 20) return names

  const xr = stepRange(datasets, names)
  if (!xr) return names
  const [xMin, xMax] = xr
  const [yMin, yMax] = isResidual
    ? residualYRange(datasets, names, hidden)
    : temperatureYRange(datasets, names, hidden)

  const xToPixel = (step: number) => ml + ((step - xMin) / (xMax - xMin)) * cw
  const yToPixel = (val: number) => {
    const v = isResidual ? Math.log10(Math.max(val, 1e-15)) : val - KELVIN_OFFSET
    return mt + ch - ((v - yMin) / (yMax - yMin)) * ch
  }

  ctx.strokeStyle = '#2a2a3a'
  ctx.lineWidth = 0.5
  ctx.setLineDash([2, 4])
  ctx.font = `${labelFs}px monospace`

  if (isResidual) {
    for (let gy = Math.ceil(yMin); gy <= Math.floor(yMax); gy++) {
      const py = mt + ch - ((gy - yMin) / (yMax - yMin)) * ch
      ctx.beginPath()
      ctx.moveTo(ml, py)
      ctx.lineTo(ml + cw, py)
      ctx.stroke()
      ctx.fillStyle = '#555'
      ctx.textAlign = 'right'
      ctx.fillText(residualTickLabel(gy), ml - 4, py + labelFs * 0.35)
    }
  } else {
    const step = yTickStep(yMax - yMin)
    for (let gy = Math.ceil(yMin / step) * step; gy <= yMax; gy += step) {
      const py = mt + ch - ((gy - yMin) / (yMax - yMin)) * ch
      ctx.beginPath()
      ctx.moveTo(ml, py)
      ctx.lineTo(ml + cw, py)
      ctx.stroke()
      ctx.fillStyle = '#555'
      ctx.textAlign = 'right'
      ctx.fillText(`${gy.toFixed(1)}°C`, ml - 4, py + labelFs * 0.35)
    }
  }

  ctx.textAlign = 'center'
  const maxXLabels = Math.max(3, Math.floor(cw / (40 * fs)))
  const xStep = xTickStep(xMax - xMin, maxXLabels)
  for (let gx = Math.ceil(xMin / xStep) * xStep; gx <= xMax; gx += xStep) {
    const px = xToPixel(gx)
    ctx.beginPath()
    ctx.moveTo(px, mt)
    ctx.lineTo(px, mt + ch)
    ctx.stroke()
    ctx.fillStyle = '#555'
    // A transient run's x axis is seconds and its ticks are fractional; a plain
    // String() there prints 0.30000000000000004.
    ctx.fillText(xStep >= 1 ? String(gx) : gx.toPrecision(3), px, lH - mb + labelFs + 3)
  }
  ctx.setLineDash([])
  ctx.textAlign = 'start'

  ctx.strokeStyle = '#555'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(ml, mt)
  ctx.lineTo(ml, mt + ch)
  ctx.lineTo(ml + cw, mt + ch)
  ctx.stroke()

  names.forEach((name, i) => {
    if (hidden[name]) return
    const points = datasets[name] ?? []
    ctx.strokeStyle = seriesColor(tab, name, i, o.sensorColor)
    ctx.lineWidth = 1.5
    ctx.beginPath()
    points.forEach((p, j) => {
      const px = xToPixel(p.step)
      const py = yToPixel(p.value)
      if (j === 0) ctx.moveTo(px, py)
      else ctx.lineTo(px, py)
    })
    ctx.stroke()
  })

  return names
}

export { legendLabel }
