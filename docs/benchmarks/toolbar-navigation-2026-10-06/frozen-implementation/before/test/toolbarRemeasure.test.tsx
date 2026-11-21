// A row appearing inside the toolbar (the transient timeline strip) must make
// the toolbar fit re-measure. The fit pins the toolbar's height to what its
// content needed; a new row grows the content without changing the pinned
// box, so no ResizeObserver fires and the row stayed clipped (T2b browser
// gate, 2026-09-30: strip at y 214-252 under a toolbar pinned at 215 px).
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { PanelResizers, refitToolbar } from '../src/components/PanelResizers'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
class NoopResizeObserver { observe() {} unobserve() {} disconnect() {} }
let toolbar: HTMLDivElement
let content: HTMLDivElement
let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let measured = 0

beforeEach(() => {
  ;(globalThis as { ResizeObserver?: unknown }).ResizeObserver = NoopResizeObserver
  toolbar = document.createElement('div'); toolbar.id = 'toolbar'
  content = document.createElement('div'); content.id = 'toolbar-content'
  toolbar.append(content); document.body.append(toolbar)
  measured = 0
  Object.defineProperty(toolbar, 'scrollHeight', { configurable: true, get: () => { measured++; return 120 } })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove(); toolbar.remove() })

it('re-measures the toolbar when a row is added to its content', async () => {
  act(() => root.render(<PanelResizers />))
  const before = measured
  expect(before).toBeGreaterThan(0)
  await act(async () => {
    content.append(document.createElement('div'))
    await Promise.resolve()
  })
  expect(measured).toBeGreaterThan(before)
})

it('re-measures on request (a row that re-wraps inside itself, like the timeline strip)', () => {
  act(() => root.render(<PanelResizers />))
  const before = measured
  act(() => refitToolbar())
  expect(measured).toBeGreaterThan(before)
})

it('caps a tall toolbar without scaling its text or controls', () => {
  Object.defineProperty(toolbar, 'scrollHeight', { configurable: true, get: () => 500 })
  act(() => root.render(<PanelResizers />))
  expect(toolbar.style.transform).toBe('')
  expect(toolbar.dataset.scrollable).toBe('true')
  expect(Number.parseFloat(toolbar.style.height)).toBeLessThan(500)
})
