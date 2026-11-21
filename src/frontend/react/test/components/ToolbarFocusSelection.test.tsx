import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Toolbar, DEFAULT_VIEW_OPTIONS } from '../../src/components/Toolbar'
import type { CfdProject } from '../../src/schema/cfdproj'

vi.mock('../../src/components/ViewCube', () => ({ ViewCube: () => null }))
const session = vi.hoisted(() => ({
  frozen: false,
  listeners: new Set<() => void>(),
}))
vi.mock('../../src/run/session', () => ({
  isUiFrozen: () => session.frozen,
  getCaseStatus: () => null,
  getCurrentCaseJob: () => null,
  isMeshPresent: () => false,
  subscribeSession: (fn: () => void) => {
    session.listeners.add(fn)
    return () => session.listeners.delete(fn)
  },
}))
const project = { constructions: {} } as CfdProject
let host: HTMLDivElement
let root: Root
let selected: string[]
const requested = vi.fn()
function render() {
  flushSync(() => root.render(<Toolbar view={DEFAULT_VIEW_OPTIONS} onChange={() => {}}
    hasSelection={selected.length > 0} selectedUids={selected} project={project} />))
}
function button() {
  const b = host.querySelector<HTMLButtonElement>('#focus-selection-btn')
  expect(b, 'a visible native button inside View tools').not.toBeNull()
  expect(b?.closest('#toolbar-view-tools')).not.toBeNull()
  return b!
}
beforeEach(() => {
  selected = []
  session.frozen = false
  requested.mockClear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  window.addEventListener('dcdtuni-focus-selection', requested)
})
afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  window.removeEventListener('dcdtuni-focus-selection', requested)
})
describe('Focus selection toolbar action', () => {
  it('is disabled without model selection', () => {
    render()
    expect(button().disabled).toBe(true)
    button().click()
    expect(requested).not.toHaveBeenCalled()
  })
  it('has a localised accessible name and requests only a camera action', () => {
    selected = ['cdu-1', 'rack-2']
    render()
    const before = JSON.stringify(project)
    expect(button().textContent?.trim()).toMatch(/定位选中模型|Focus selection/)
    expect(button().disabled).toBe(false)
    button().click()
    expect(requested).toHaveBeenCalledTimes(1)
    expect(selected).toEqual(['cdu-1', 'rack-2'])
    expect(JSON.stringify(project)).toBe(before)
  })
  it('reacts to the job freeze and thaws through the existing session', () => {
    selected = ['cdu-1']
    render()
    flushSync(() => {
      session.frozen = true
      session.listeners.forEach(fn => fn())
    })
    expect(button().disabled).toBe(true)
    button().click()
    expect(requested).not.toHaveBeenCalled()
    flushSync(() => {
      session.frozen = false
      session.listeners.forEach(fn => fn())
    })
    expect(button().disabled).toBe(false)
    button().click()
    expect(requested).toHaveBeenCalledTimes(1)
  })
  it('clears availability when the selection is removed', () => {
    selected = ['cdu-1']
    render()
    expect(button().disabled).toBe(false)
    selected = []
    render()
    expect(button().disabled).toBe(true)
  })
})
