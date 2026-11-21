import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { Toolbar, DEFAULT_VIEW_OPTIONS, THEME_STORAGE_KEY } from '../../src/components/Toolbar'
import { TimeSeriesChart } from '../../src/components/TimeSeriesChart'

vi.mock('../../src/components/ViewCube', () => ({ ViewCube: () => null }))
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLDivElement
let root: Root
let resize: (() => void) | undefined
let chartWidth = 280
const ctx = {
  setTransform: vi.fn(), clearRect: vi.fn(), fillText: vi.fn(),
  beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), setLineDash: vi.fn(),
  measureText: (text: string) => ({ width: text.length * 6 }),
  font: '', fillStyle: '', strokeStyle: '', lineWidth: 1,
}
class ChartResizeObserver {
  constructor(callback: () => void) { resize = callback }
  observe() {}
  disconnect() {}
}

beforeEach(() => {
  localStorage.removeItem(THEME_STORAGE_KEY)
  document.documentElement.removeAttribute('data-theme')
  host = document.createElement('div'); document.body.append(host)
  root = createRoot(host)
  resize = undefined; chartWidth = 280
  vi.stubGlobal('ResizeObserver', ChartResizeObserver)
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ctx as unknown as CanvasRenderingContext2D)
  vi.spyOn(HTMLCanvasElement.prototype, 'clientWidth', 'get').mockImplementation(() => chartWidth)
  vi.clearAllMocks()
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  localStorage.removeItem(THEME_STORAGE_KEY)
  document.documentElement.removeAttribute('data-theme')
  vi.restoreAllMocks(); vi.unstubAllGlobals()
})

function toolbar() {
  act(() => root.render(<Toolbar view={{ ...DEFAULT_VIEW_OPTIONS }} onChange={() => {}} hasSelection={false} project={null} />))
}

describe('workspace preferences', () => {
  it('applies the selected theme immediately and restores it after remounting', () => {
    toolbar()
    const select = document.getElementById('theme-selector') as HTMLSelectElement
    act(() => {
      select.value = 'vscode-dark'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(document.documentElement.dataset.theme).toBe('vscode-dark')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('vscode-dark')
    act(() => root.unmount())
    root = createRoot(host); toolbar()
    expect((document.getElementById('theme-selector') as HTMLSelectElement).value).toBe('vscode-dark')
    expect(document.documentElement.dataset.theme).toBe('vscode-dark')
  })

  it('recovers an unknown stored theme to the engineering preset', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'removed-theme')
    toolbar()
    expect(document.documentElement.dataset.theme).toBe('cyberpunk-dark')
  })
})

describe('chart layout', () => {
  it('redraws at the new panel width instead of stretching the old drawing buffer', () => {
    act(() => root.render(<TimeSeriesChart id="chart" time={[0, 1, 2]} series={[{ label: 'return', color: '#8ab', points: [25, 25.01, 25.02] }]} />))
    const canvas = document.getElementById('chart') as HTMLCanvasElement
    expect(canvas.width).toBe(Math.round(280 * (window.devicePixelRatio || 1)))
    chartWidth = 220
    act(() => resize?.())
    expect(canvas.width).toBe(Math.round(220 * (window.devicePixelRatio || 1)))
    expect(ctx.clearRect).toHaveBeenLastCalledWith(0, 0, 220, 180)
    // Small responses remain distinguishable after the layout redraw.
    const labels = ctx.fillText.mock.calls.map(([value]) => value)
    expect(new Set(labels.filter((value) => /^25\./.test(String(value)))).size).toBeGreaterThan(1)
  })

  it('keeps analytic dashes and breaks the curve at missing samples', () => {
    act(() => root.render(<TimeSeriesChart time={[0, 1, 2]} series={[{ label: 'analytic cold-plate reference', color: '#8ab', points: [20, NaN, 21], dash: true }]} />))
    expect(host.querySelector('.ts-chart-swatch.dashed')).not.toBeNull()
    expect(host.querySelector('.ts-chart-legend-label')?.textContent).toBe('analytic cold-plate reference')
    expect(ctx.setLineDash).toHaveBeenCalledWith([6, 4])
    const moves = ctx.moveTo.mock.calls
    // Axes + five grid lines + both disjoint samples.
    expect(moves).toHaveLength(ctx.clearRect.mock.calls.length * 8)
    // Axes + five grid lines; no segment crosses the missing sample.
    expect(ctx.lineTo.mock.calls).toHaveLength(ctx.clearRect.mock.calls.length * 7)
  })
})
