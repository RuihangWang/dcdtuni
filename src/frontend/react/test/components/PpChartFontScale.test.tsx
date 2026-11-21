import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TimeSeriesChart } from '../../src/components/TimeSeriesChart'
/** Canvas font, analytic reference and missing-sample behavior used by current charts. */

let host: HTMLDivElement
let root: Root
let fonts: string[]
let labels: string[]
let dashes: number[][]

function stubCanvas() {
  fonts = []
  labels = []
  dashes = []
  const ctx = {
    setTransform() {},
    clearRect() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    stroke() {},
    fill() {},
    fillRect() {},
    fillText(value: string) { labels.push(value) },
    measureText(value: string) { return { width: value.length * 6 } },
    setLineDash(value: number[]) { dashes.push(value) },
    save() {},
    restore() {},
    translate() {},
    rotate() {},
    set font(v: string) {
      fonts.push(v)
    },
    get font() {
      return fonts[fonts.length - 1] ?? ''
    },
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 1,
  }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    ctx as unknown as CanvasRenderingContext2D,
  )
}

/** Every px size the chart set a font to, largest first. */
function fontSizes(): number[] {
  return fonts.map((f) => Number(f.match(/^(\d+(?:\.\d+)?)px/)?.[1] ?? 0)).filter(Boolean)
}

const SERIES = [{ label: 'cp_1', color: '#e06c75', points: [300, 305, 310] }]

beforeEach(() => {
  stubCanvas()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
})

describe('TimeSeriesChart', () => {
  function draw(fontScale?: number) {
    fonts = []
    flushSync(() =>
      root.render(
        <TimeSeriesChart time={[0, 1, 2]} series={SERIES} fontScale={fontScale} height={200} />,
      ),
    )
  }

  it('draws at its own size when no scale is given', () => {
    draw()
    expect(fontSizes().length).toBeGreaterThan(0)
    expect(Math.max(...fontSizes())).toBeLessThanOrEqual(12)
  })

  it('scales every font it sets', () => {
    draw(1)
    const base = fontSizes()
    draw(2)
    const big = fontSizes()
    expect(big.length).toBe(base.length)
    for (let i = 0; i < base.length; i++) expect(big[i]!).toBeGreaterThan(base[i]!)
  })

  it('shrinks as well as grows', () => {
    draw(1)
    const base = Math.max(...fontSizes())
    draw(0.6)
    expect(Math.max(...fontSizes())).toBeLessThan(base)
  })

  it('dashes exactly the series flagged as analytic reference, in draw order', () => {
    // The analytic overlay is the only dashed thing the chart may ever draw:
    // run data stays solid so a viewer can never mistake a reference curve
    // for a measurement (or vice versa). The legend swatch wears the same
    // dash as its line in the wrapping DOM legend, so it cannot present a
    // reference as solid run data either.
    dashes = []
    flushSync(() => root.render(
      <TimeSeriesChart time={[0, 1, 2]} series={[
        { label: 'run', color: '#e06c75', points: [300, 305, 310] },
        { label: 'analytic', color: '#9a9a9a', points: [300, 305, 310], dash: true },
      ]} height={100} />,
    ))
    expect(dashes).toEqual([[], [6, 4], []])
    const swatches=host.querySelectorAll('.ts-chart-swatch')
    expect(swatches).toHaveLength(2)
    expect(swatches[0]?.classList.contains('dashed')).toBe(false)
    expect(swatches[1]?.classList.contains('dashed')).toBe(true)
  })

  it('breaks the line at NaN instead of shifting later samples left', () => {
    // The monitor pads an analytic overlay whose fixture axis starts at t=dt
    // with a leading NaN; the pen must lift there, not plot sample i at x[i-1].
    // Record pen commands per path (beginPath opens a bucket) so the series
    // subpath can be told apart from the axis/grid paths, which also moveTo
    // and lineTo at overlapping coordinates.
    const paths: string[][] = []
    let pen: string[] = []
    const ctxWithSpy = { setTransform() {}, clearRect() {}, stroke() {},
      fillRect() {}, fillText() {}, setLineDash() {},
      measureText(value: string) { return { width: value.length * 6 } },
      beginPath() { pen = []; paths.push(pen) },
      moveTo(x: number, y: number) { pen.push(`M${Math.round(x)},${Math.round(y)}`) },
      lineTo(x: number, y: number) { pen.push(`L${Math.round(x)},${Math.round(y)}`) } }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockReturnValue(ctxWithSpy as unknown as CanvasRenderingContext2D)
    flushSync(() => root.render(
      <TimeSeriesChart time={[0, 1, 2]} series={[
        { label: 'padded', color: '#9a9a9a', points: [NaN, 305, 310], dash: true },
      ]} height={100} />,
    ))
    // On the 300 px fallback canvas the axis origin sits at x=51 and t=1 maps
    // to ~171: the series pen must come down THERE (first finite sample), not
    // at the origin — a left-shifted short array would start at x=51 and draw
    // a curve that never happened.
    const series = paths.filter(path => path.length > 0 && path[0]!.startsWith('M') &&
      parseFloat(path[0]!.slice(1)) > 100)
    expect(series).toHaveLength(1)
    // NaN breaks the line: pen down at t=1, exactly one segment on to t=2.
    expect(series[0]).toHaveLength(2)
    expect(series[0]![1]!.startsWith('L')).toBe(true)
  })

  it('keeps a small return-temperature change readable on its own axis', () => {
    flushSync(() => root.render(
      <TimeSeriesChart time={[0, 60, 120]} series={[
        { label: 'primary return', color: '#e7a967', points: [25.020, 25.025, 25.030] },
      ]} height={175} />,
    ))
    expect(new Set(labels.filter(value => /^25\.\d{3}$/.test(value))).size).toBeGreaterThan(1)
  })
})
