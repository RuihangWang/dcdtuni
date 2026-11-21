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


it('re-measures when an existing full-width tool panel is disclosed', async () => {
  const panel = document.createElement('section')
  panel.hidden = true
  content.append(panel)
  act(() => root.render(<PanelResizers />))
  const before = measured
  await act(async () => {
    panel.hidden = false
    await Promise.resolve()
  })
  expect(measured).toBeGreaterThan(before)
})

// A user can shrink the tools, but must not shrink a wrapped timeline out of view.
it('reserves the measured timeline during keyboard resize and re-wrap', () => {
  const body = document.createElement('div'); body.id = 'toolbar-body'
  const timeline = document.createElement('div'); timeline.id = 'timeline-strip'
  content.append(body, timeline)
  let footerHeight = 120
  Object.defineProperty(timeline, 'scrollHeight', { configurable: true, get: () => footerHeight })
  Object.defineProperty(toolbar, 'scrollHeight', { configurable: true, get: () => 500 })
  Object.defineProperty(toolbar, 'getBoundingClientRect', { configurable: true, value: () => ({
    width: 700, height: Number.parseFloat(toolbar.style.height) || 500,
    bottom: Number.parseFloat(toolbar.style.height) || 500,
  }) })
  act(() => root.render(<PanelResizers />))
  const splitter = document.getElementById('toolbar-resizer')!
  act(() => {
    for (let step = 0; step < 3; step++) splitter.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', shiftKey: true, bubbles: true }))
  })
  expect(Number.parseFloat(toolbar.style.height)).toBe(152)
  expect(Number.parseFloat(toolbar.style.getPropertyValue('--timeline-available-height'))).toBe(120)
  expect(splitter.getAttribute('aria-valuemin')).toBe('152')
  footerHeight = 180
  act(() => refitToolbar())
  expect(Number.parseFloat(toolbar.style.height)).toBe(212)
  expect(Number.parseFloat(toolbar.style.getPropertyValue('--timeline-available-height'))).toBe(180)
  expect(splitter.getAttribute('aria-valuemin')).toBe('212')
})

it('keeps a footer taller than the window cap reachable inside a bounded scroll region', () => {
  const body = document.createElement('div'); body.id = 'toolbar-body'
  const timeline = document.createElement('div'); timeline.id = 'timeline-strip'
  content.append(body, timeline)
  Object.defineProperty(timeline, 'scrollHeight', { configurable: true, get: () => 800 })
  Object.defineProperty(toolbar, 'scrollHeight', { configurable: true, get: () => 1000 })
  act(() => root.render(<PanelResizers />))
  expect(Number.parseFloat(toolbar.style.height)).toBeCloseTo(window.innerHeight * .28)
  expect(toolbar.dataset.footerScrollable).toBe('true')
  expect(Number.parseFloat(toolbar.style.getPropertyValue('--timeline-available-height'))).toBeGreaterThan(0)
  expect(Number.parseFloat(toolbar.style.getPropertyValue('--timeline-available-height'))).toBeLessThan(800)
})

it('measures the full scrolling tools when an outer grid reports only its constrained height', () => {
  const body = document.createElement('div'); body.id = 'toolbar-body'
  const timeline = document.createElement('div'); timeline.id = 'timeline-strip'
  content.append(body, timeline)
  Object.defineProperty(body, 'scrollHeight', { configurable: true, get: () => 220 })
  Object.defineProperty(timeline, 'scrollHeight', { configurable: true, get: () => 80 })
  act(() => root.render(<PanelResizers />))
  expect(Number.parseFloat(toolbar.style.height)).toBeCloseTo(window.innerHeight * .28)
  expect(document.getElementById('toolbar-resizer')!.getAttribute('aria-valuemax')).toBe('300')
})
