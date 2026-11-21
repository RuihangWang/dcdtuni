import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { PanelResizers } from '../src/components/PanelResizers'
import { setLocale } from '../src/i18n'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const edges = ['left', 'right', 'toolbar', 'log'] as const
type Edge = typeof edges[number]
let host: HTMLDivElement
let root: Root
let width = 1280
let height = 720
let toolbarContentHeight = 500
let originalStyle: string | null
let originalClass: string
const panels: Partial<Record<Edge, HTMLDivElement>> = {}
class NoopResizeObserver { observe() {} disconnect() {} }

function drawn(edge: Edge): number {
  const style = document.documentElement.style
  if (edge === 'left' || edge === 'right') {
    if (document.body.classList.contains('panel-' + edge + '-collapsed')) return 0
    const requested = parseFloat(style.getPropertyValue('--' + edge + '-panel-width')) || (edge === 'left' ? 330 : 340)
    const fit = Math.max(0, (width - 260) / 2)
    // Mirror the existing visual constraint, including the explicit per-side
    // manual share override. The real CSS rule is asserted below; jsdom has no layout.
    const share = style.getPropertyValue('--' + edge + '-panel-share') ? fit : width * .23
    return Math.min(Math.max(220, Math.min(requested, share)), fit)
  }
  if (edge === 'toolbar') {
    const panel = panels.toolbar!
    if (panel.classList.contains('collapsed')) return 28
    const css = getComputedStyle(panel)
    const border = (parseFloat(css.borderTopWidth) || 0) + (parseFloat(css.borderBottomWidth) || 0)
    return parseFloat(panel.style.height) || toolbarContentHeight + border
  }
  return Math.max(28, Math.min(parseFloat(style.getPropertyValue('--log-panel-height')) || 160, height * .5))
}
function handle(edge: Edge) { return document.getElementById(edge + '-resizer')! }
function key(edge: Edge, value: string, shiftKey = false) {
  const event = new KeyboardEvent('keydown', { key: value, shiftKey, bubbles: true, cancelable: true })
  act(() => handle(edge).dispatchEvent(event))
  return event
}
function value(edge: Edge, attr: string) { return Number(handle(edge).getAttribute(attr)) }
beforeEach(() => {
  width = 1280; height = 720; toolbarContentHeight = 500
  originalStyle = document.documentElement.getAttribute('style')
  originalClass = document.body.className
  document.documentElement.style.cssText = '--panel-floor:220px;--min-viewport-width:260px'
  document.body.className = ''
  setLocale('en-US')
  vi.stubGlobal('ResizeObserver', NoopResizeObserver)
  vi.spyOn(window, 'innerWidth', 'get').mockImplementation(() => width)
  vi.spyOn(window, 'innerHeight', 'get').mockImplementation(() => height)
  vi.spyOn(document.body, 'clientWidth', 'get').mockImplementation(() => width)
  vi.spyOn(document.body, 'clientHeight', 'get').mockImplementation(() => height)
  for (const edge of edges) {
    const el = document.createElement('div')
    el.id = edge === 'toolbar' ? 'toolbar' : edge + '-panel'
    panels[edge] = el
    Object.defineProperty(el, 'getBoundingClientRect', { value: () => ({
      width: edge === 'left' || edge === 'right' ? drawn(edge) : 600,
      height: edge === 'left' || edge === 'right' ? height - drawn('log') : drawn(edge),
      top: 0, bottom: drawn(edge), left: 0, right: drawn(edge),
    }) })
    if (edge === 'toolbar') {
      Object.defineProperty(el, 'scrollHeight', { get: () => el.classList.contains('collapsed') ? 28 : Math.round(toolbarContentHeight) })
      Object.defineProperty(el, 'offsetWidth', { get: () => 600 })
      const content = document.createElement('div'); content.id = 'toolbar-content'; el.append(content)
    }
    document.body.append(el)
  }
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root.render(<PanelResizers />))
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  for (const edge of edges) panels[edge]?.remove()
  if (originalStyle === null) document.documentElement.removeAttribute('style')
  else document.documentElement.setAttribute('style', originalStyle)
  document.body.className = originalClass
  vi.restoreAllMocks(); vi.unstubAllGlobals()
})

it.each(edges)('exposes the %s edge as a focusable, named separator with actual bounds', edge => {
  const el = handle(edge)
  expect(el.getAttribute('role')).toBe('separator')
  expect(el.tabIndex).toBe(0)
  expect(el.getAttribute('aria-orientation')).toBe(edge === 'left' || edge === 'right' ? 'vertical' : 'horizontal')
  expect(document.getElementById(el.getAttribute('aria-controls')!)).toBe(panels[edge])
  expect(el.getAttribute('aria-label')).toMatch(/Resize/)
  expect(document.getElementById(el.getAttribute('aria-describedby')!)?.textContent).toMatch(/10.*40/)
  expect(value(edge, 'aria-valuenow')).toBeCloseTo(drawn(edge), 3)
})

it('moves the left edge from the drawn 294.4px, overriding the automatic share only on that side', () => {
  expect(drawn('left')).toBeCloseTo(294.4)
  handle('left').focus()
  key('left', 'ArrowRight')
  expect(document.activeElement).toBe(handle('left'))
  expect(drawn('left')).toBeCloseTo(304.4)
  expect(drawn('right')).toBeCloseTo(294.4)
  expect(value('left', 'aria-valuenow')).toBeCloseTo(304.4)
  expect(document.documentElement.style.getPropertyValue('--left-panel-share')).toBe('var(--panel-fit-max)')
  const css = readFileSync('src/styles.css', 'utf8')
  expect(css).toContain('var(--left-panel-share, var(--panel-share))')
  expect(css).toContain('var(--right-panel-share, var(--panel-share))')
  key('left', 'ArrowLeft')
  expect(drawn('left')).toBeCloseTo(294.4)
})
it('moves the right edge in screen direction, with Shift using a larger step', () => {
  key('right', 'ArrowLeft')
  expect(drawn('right')).toBeCloseTo(304.4)
  key('right', 'ArrowRight', true)
  expect(drawn('right')).toBeCloseTo(264.4)
})
it('resizes the toolbar at readable scale and keeps its separator on the drawn edge', () => {
  const before = drawn('toolbar')
  key('toolbar', 'ArrowDown')
  expect(drawn('toolbar')).toBeCloseTo(before + 10)
  expect(panels.toolbar!.style.transform).toBe('')
  expect(value('toolbar', 'aria-valuenow')).toBeCloseTo(drawn('toolbar'))
  expect(parseFloat(handle('toolbar').style.top)).toBeCloseTo(drawn('toolbar'), 0)
})
it('moves the log edge in screen direction and can reopen the collapsed dock', () => {
  key('log', 'ArrowUp')
  expect(drawn('log')).toBe(170)
  key('log', 'ArrowDown', true)
  expect(drawn('log')).toBe(130)
  document.documentElement.style.setProperty('--log-panel-height', '28px')
  key('log', 'ArrowUp')
  expect(drawn('log')).toBe(38)
})

it.each(edges)('does not change %s on an off-axis arrow or leak it to viewport shortcuts', edge => {
  const before = document.documentElement.style.cssText
  const bubble = vi.fn(); document.body.addEventListener('keydown', bubble)
  const event = key(edge, edge === 'left' || edge === 'right' ? 'ArrowUp' : 'ArrowLeft')
  document.body.removeEventListener('keydown', bubble)
  expect(document.documentElement.style.cssText).toBe(before)
  expect(event.defaultPrevented).toBe(true)
  expect(bubble).not.toHaveBeenCalled()
})
it('clamps requested sizes to the actual CSS fit and half-height limits', () => {
  for (let i = 0; i < 30; i++) {
    key('left', 'ArrowRight', true); key('right', 'ArrowLeft', true)
    key('toolbar', 'ArrowDown', true); key('log', 'ArrowUp', true)
  }
  expect(drawn('left')).toBe(510); expect(drawn('right')).toBe(510)
  expect(width - drawn('left') - drawn('right')).toBe(260)
  expect(value('left', 'aria-valuemax')).toBe(510)
  expect(drawn('toolbar')).toBe(360); expect(drawn('log')).toBe(360)
  expect(value('toolbar', 'aria-valuemax')).toBe(360)
  expect(value('log', 'aria-valuemax')).toBe(360)
})
it.each(['left', 'right', 'toolbar'] as const)('hides the collapsed %s separator without overwriting its remembered size', async edge => {
  const before = document.documentElement.style.cssText
  await act(async () => {
    if (edge === 'toolbar') panels.toolbar!.classList.add('collapsed')
    else document.body.classList.add('panel-' + edge + '-collapsed')
    await Promise.resolve()
  })
  expect(handle(edge).hidden).toBe(true)
  expect(handle(edge).tabIndex).toBe(-1)
  key(edge, edge === 'right' ? 'ArrowLeft' : edge === 'left' ? 'ArrowRight' : 'ArrowDown')
  expect(document.documentElement.style.cssText).toBe(before)
  expect(value(edge, 'aria-valuenow')).toBe(0)
})
it('keeps the manual request across window changes while reporting the smaller drawn bounds', () => {
  key('left', 'ArrowRight')
  const request = document.documentElement.style.getPropertyValue('--left-panel-width')
  width = 480
  act(() => window.dispatchEvent(new Event('resize')))
  expect(drawn('left')).toBe(110)
  expect(value('left', 'aria-valuemin')).toBe(110)
  expect(value('left', 'aria-valuemax')).toBe(110)
  expect(value('left', 'aria-valuenow')).toBe(110)
  key('left', 'ArrowRight')
  expect(document.documentElement.style.getPropertyValue('--left-panel-width')).toBe(request)
})
it('localizes the log separator and keyboard help when the locale changes', () => {
  act(() => setLocale('zh-CN'))
  expect(handle('log').getAttribute('aria-label')).toContain('日志')
  expect(handle('log').title).toContain('Shift')
  expect(document.getElementById(handle('log').getAttribute('aria-describedby')!)?.textContent).toContain('像素')
})

it('reports the CSS minimum when a very short window makes it exceed the height cap', () => {
  height = 40
  act(() => window.dispatchEvent(new Event('resize')))
  expect(drawn('log')).toBe(28)
  expect(value('log', 'aria-valuemin')).toBe(28)
  expect(value('log', 'aria-valuemax')).toBe(28)
  expect(value('log', 'aria-valuenow')).toBe(28)
  expect(drawn('toolbar')).toBe(28)
  expect(value('toolbar', 'aria-valuemax')).toBe(28)
})

it('retains manual side widths through the actual App collapse reset of both requests', async () => {
  key('left', 'ArrowRight'); key('right', 'ArrowLeft', true)
  const left = drawn('left'); const right = drawn('right')
  await act(async () => {
    document.documentElement.style.setProperty('--left-panel-width', '0px')
    document.documentElement.style.setProperty('--right-panel-width', '340px')
    document.body.classList.add('panel-left-collapsed')
    await Promise.resolve()
  })
  expect(drawn('left')).toBe(0)
  expect(document.documentElement.style.getPropertyValue('--left-panel-width')).toBe('0px')
  expect(drawn('right')).toBeCloseTo(right)
  await act(async () => {
    document.documentElement.style.setProperty('--left-panel-width', '330px')
    document.documentElement.style.setProperty('--right-panel-width', '340px')
    document.body.classList.remove('panel-left-collapsed')
    await Promise.resolve()
  })
  expect(drawn('left')).toBeCloseTo(left)
  expect(drawn('right')).toBeCloseTo(right)
})
it('retains a manual request through a resizer remount and the App default-width write', () => {
  key('left', 'ArrowRight')
  const before = drawn('left')
  act(() => root.unmount())
  document.documentElement.style.setProperty('--left-panel-width', '330px')
  root = createRoot(host)
  act(() => root.render(<PanelResizers />))
  expect(drawn('left')).toBeCloseTo(before)
})

it('includes nonzero borders and fractional natural height in toolbar limits and keyboard steps', () => {
  act(() => root.unmount())
  toolbarContentHeight = 100.25
  panels.toolbar!.style.border = '1px solid red'
  root = createRoot(host)
  act(() => root.render(<PanelResizers />))
  expect(drawn('toolbar')).toBe(102.25)
  expect(value('toolbar', 'aria-valuenow')).toBe(102.25)
  expect(value('toolbar', 'aria-valuemax')).toBe(102.25)
  key('toolbar', 'ArrowUp')
  expect(drawn('toolbar')).toBe(92.25)
  expect(value('toolbar', 'aria-valuenow')).toBe(92.25)
  key('toolbar', 'ArrowDown')
  expect(drawn('toolbar')).toBe(102.25)
  expect(value('toolbar', 'aria-valuenow')).toBeLessThanOrEqual(value('toolbar', 'aria-valuemax'))
})
