import type { CSSProperties } from 'react'

/**
 * Two thumbs on one track: the low and the high end of a window. The track is filled between the
 * thumbs, or outside them when `invert` is set, so it shows which values are kept.
 */
export function RangeSlider({ id, min, max, lo, hi, invert = false, steps = 500, onChange, format }: {
  id?: string
  min: number
  max: number
  lo: number
  hi: number
  invert?: boolean
  steps?: number
  onChange: (lo: number, hi: number) => void
  format?: (v: number) => string
}) {
  const span = max - min
  const step = span > 0 ? span / steps : 1
  const pct = (v: number) => (span > 0 ? Math.min(100, Math.max(0, ((v - min) / span) * 100)) : 0)
  const a = pct(lo), b = pct(hi)
  const style = { '--lo': `${a}%`, '--hi': `${b}%` } as CSSProperties
  const show = format ?? ((v: number) => v.toPrecision(4))
  return (
    <div className={`range-slider${invert ? ' inverted' : ''}`} id={id} style={style}
      data-lo={lo} data-hi={hi} data-invert={invert ? '1' : '0'}>
      <div className="range-slider-track">
        <input type="range" className="range-slider-lo" aria-label="min" min={min} max={max} step={step}
          value={lo} disabled={!(span > 0)}
          onChange={(e) => onChange(Math.min(Number(e.target.value), hi), hi)} />
        <input type="range" className="range-slider-hi" aria-label="max" min={min} max={max} step={step}
          value={hi} disabled={!(span > 0)}
          onChange={(e) => onChange(lo, Math.max(Number(e.target.value), lo))} />
      </div>
      <div className="range-slider-values">
        <span>{show(lo)}</span>
        <span>{show(hi)}</span>
      </div>
    </div>
  )
}
