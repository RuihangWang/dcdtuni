import { useEffect, useRef, useState } from 'react'
import { useTranslation } from '../useTranslation'

/**
 * A small line chart on a canvas, for solver time series.
 *
 * Canvas rather than SVG because these plots carry one point per time step and
 * a transient liquid run can have thousands — that many DOM nodes makes the
 * panel scroll badly, and nothing here needs to be individually hit-tested.
 *
 * Drawn at device-pixel resolution and scaled back down with CSS, the same way
 * `js/liquid_solve_monitor.js` does it: a canvas sized in CSS pixels renders
 * blurry on a HiDPI display, and a blurry temperature curve is one a user
 * cannot read a value off.
 */

export interface Series {
  label: string
  color: string
  points: number[]
  /** Dashed stroke — used for analytic reference overlays, never for run data. */
  dash?: boolean
}

export interface TimeSeriesChartProps {
  time: number[]
  series: Series[]
  id?: string
  height?: number
  xLabel?: string
  yLabel?: string
  /**
   * A− / A+ on the chart window, 0.6 to 2.0.
   *
   * Everything the chart draws text with scales by it, and the plot's margins
   * grow with the numbers so a 2x tick label does not run under the axis — the
   * legacy grows its left and bottom margins the same way
   * (`Math.max(65, 60 * fs + 5)`, js/liquid_solve_monitor.js:1155).
   */
  fontScale?: number
}

const PAD = { left: 46, right: 12, top: 20, bottom: 35 }

/** The margins at a given font scale: the text side grows, the rest does not. */
function padFor(fs: number) {
  return {
    left: Math.max(PAD.left, PAD.left * fs + 5),
    right: PAD.right,
    top: Math.max(PAD.top, PAD.top * fs),
    bottom: Math.max(PAD.bottom, PAD.bottom * fs + 5),
  }
}

export function TimeSeriesChart({
  time,
  series,
  id,
  height = 180,
  xLabel,
  yLabel,
  fontScale = 1,
}: TimeSeriesChartProps) {
  const ref = useRef<HTMLCanvasElement | null>(null)
  const [width, setWidth] = useState(0)
  const { t } = useTranslation()
  const drawn = series.filter((s) => s.points.some((v) => Number.isFinite(v)))

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const measure = () => setWidth(canvas.clientWidth)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const cssW = width || canvas.clientWidth || 300
    const cssH = height
    canvas.width = Math.max(1, Math.round(cssW * dpr))
    canvas.height = Math.max(1, Math.round(cssH * dpr))
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, cssW, cssH)

    // Every font the chart sets goes through this, so A− / A+ cannot miss one.
    const fs = (px: number) => `${Math.max(8, Math.round(px * fontScale))}px "Segoe UI", sans-serif`
    const pad = padFor(fontScale)

    if (drawn.length === 0 || time.length < 2) {
      ctx.fillStyle = '#9aabba'
      ctx.font = fs(11)
      ctx.fillText(t('chart.empty'), pad.left, cssH / 2)
      return
    }

    // A common y-range across all series: these are the same quantity in the
    // same unit, and per-series autoscaling would make a 2 K spread look the
    // same size as a 40 K one.
    let lo = Infinity
    let hi = -Infinity
    for (const s of drawn) {
      for (const v of s.points) {
        if (!Number.isFinite(v)) continue
        if (v < lo) lo = v
        if (v > hi) hi = v
      }
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return
    if (hi - lo < 1e-9) {
      // A flat series still deserves an axis, not a divide-by-zero.
      lo -= 0.5
      hi += 0.5
    }
    const padY = (hi - lo) * 0.08
    lo -= padY
    hi += padY
    // A 0.01 °C return-water response must not collapse to five identical
    // "25.0" ticks. Show only the precision needed to distinguish ticks.
    const tickDecimals = Math.max(1, Math.min(6, Math.ceil(-Math.log10((hi - lo) / 4))))

    ctx.font = fs(11)
    // Measure tick labels so small MMS responses keep their full precision.
    const labels = Array.from({ length: 5 }, (_, i) => (lo + (hi - lo) * i / 4).toFixed(tickDecimals))
    const widest = Math.max(...labels.map((label) => ctx.measureText(label).width))
    pad.left = Math.max(pad.left, widest + 10)
    const t0 = time[0] as number
    const t1 = time[time.length - 1] as number
    const spanT = t1 - t0 || 1
    const plotW = Math.max(1, cssW - pad.left - pad.right)
    const plotH = Math.max(1, cssH - pad.top - pad.bottom)
    const xOf = (t: number) => pad.left + ((t - t0) / spanT) * plotW
    const yOf = (v: number) => pad.top + (1 - (v - lo) / (hi - lo)) * plotH

    // axes
    ctx.strokeStyle = '#405166'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(pad.left, pad.top)
    ctx.lineTo(pad.left, pad.top + plotH)
    ctx.lineTo(pad.left + plotW, pad.top + plotH)
    ctx.stroke()

    ctx.fillStyle = '#9aabba'
    ctx.font = fs(11)
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4
      const y = yOf(v)
      const label = v.toFixed(tickDecimals)
      ctx.fillText(label, pad.left - ctx.measureText(label).width - 7, y + 3)
      ctx.strokeStyle = '#283545'
      ctx.beginPath()
      ctx.moveTo(pad.left, y)
      ctx.lineTo(pad.left + plotW, y)
      ctx.stroke()
    }
    ctx.fillStyle = '#9aabba'
    ctx.fillText(t0.toFixed(1), pad.left, cssH - 18)
    const endLabel = t1.toFixed(1)
    ctx.fillText(endLabel, pad.left + plotW - ctx.measureText(endLabel).width, cssH - 18)
    if (xLabel) ctx.fillText(xLabel, pad.left + (plotW - ctx.measureText(xLabel).width) / 2, cssH - 3)
    if (yLabel) ctx.fillText(yLabel, pad.left, 12 * fontScale)

    for (const s of drawn) {
      ctx.strokeStyle = s.color
      ctx.lineWidth = 1.5
      ctx.setLineDash(s.dash ? [6, 4] : [])
      ctx.beginPath()
      // A series can be shorter than `time` if the run was stopped mid-step;
      // plot what exists rather than reading past its end.
      const n = Math.min(s.points.length, time.length)
      // A non-finite sample BREAKS the line; it does not get dropped. Dropping
      // it would shift every later point one x-position to the left and draw a
      // curve that never happened — the caller must pass NaN for "no value at
      // this x", not a shorter array.
      let pen = false
      for (let i = 0; i < n; i++) {
        const v = s.points[i] as number
        if (!Number.isFinite(v)) {
          pen = false
          continue
        }
        const x = xOf(time[i] as number)
        const y = yOf(v)
        if (!pen) {
          ctx.moveTo(x, y)
          pen = true
        } else ctx.lineTo(x, y)
      }
      ctx.stroke()
    }

    ctx.setLineDash([])
  }, [time, series, height, xLabel, yLabel, fontScale, width, t])

  return (
    <div className="ts-chart-card">
      {drawn.length > 0 && (
        <div className="ts-chart-legend" style={{ fontSize: Math.max(10, 11 * fontScale) }}>
          {drawn.map((s, index) => (
            <span className="ts-chart-legend-item" key={`${s.label}-${index}`} title={s.label}>
              <span className={`ts-chart-swatch${s.dash ? ' dashed' : ''}`} style={{ color: s.color }} />
              <span className="ts-chart-legend-label">{s.label}</span>
            </span>
          ))}
        </div>
      )}
      <canvas
        id={id}
        ref={ref}
        className="ts-chart"
        aria-label={[yLabel, xLabel].filter(Boolean).join(' / ')}
        style={{ width: '100%', height, display: 'block' }}
      />
    </div>
  )
}
