// The timeline strip under the top bar (transient design §2): start control,
// union diamonds, playhead, duration, and the track panel toggle.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as store from '../src/projectStore'
import { readProjectFileText } from '../src/io/projectFiles'
import { transientOf } from '../src/transient/model'
import { setSimulationMode } from '../src/transient/simulationMode'
import { addKeyframe, applyTimelineOp, setKeyframe, trackKeyOf } from '../src/transient/trackEdits'
import { TimelineStrip } from '../src/transient/TimelineStrip'
import { trackPanelOpen } from '../src/transient/timelineUi'
import { setLocale } from '../src/i18n'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const CASE = resolve(process.cwd(), '../../../case/Base_coldAisleIso/Base_coldAisleIso.cfdproj')
let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
const tr = () => transientOf(store.getProject())

beforeEach(() => {
  const r = readProjectFileText(readFileSync(CASE, 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks() })

const render = () => act(() => root.render(<TimelineStrip />))
const bar = () => {
  const el = host.querySelector('#timeline-bar') as HTMLDivElement
  Object.defineProperty(el, 'getBoundingClientRect', {
    configurable: true, value: () => ({ left: 0, top: 0, width: 1000, height: 20, right: 1000, bottom: 20, x: 0, y: 0 }),
  })
  return el
}
const pointer = (target: EventTarget, type: string, clientX: number) =>
  act(() => { target.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX, button: 0 })) })
const firstTrack = () => tr().tracks.find(t => t.param === 'server.power')!

function transientWithKeyframe(at: number) {
  setSimulationMode('transient')                       // durationS 600, gridS 1
  applyTimelineOp('addKeyframe', addKeyframe(trackKeyOf(firstTrack()), at, 400))
}

it('shows nothing for a steady project', () => {
  render()
  expect(host.querySelector('#timeline-strip')).toBeNull()
})

it('draws a diamond per keyframe time and seeks where the bar is clicked', () => {
  transientWithKeyframe(120)
  render()
  const diamonds = host.querySelectorAll('.timeline-diamond')
  expect(diamonds).toHaveLength(1)
  expect((diamonds[0] as HTMLElement).style.left).toBe('20%')
  const b = bar()
  pointer(b, 'pointerdown', 500)
  pointer(window, 'pointerup', 500)
  expect(store.playheadTime()).toBe(300)
  expect((host.querySelector('#timeline-playhead-time') as HTMLInputElement).value).toBe('300')
})

it('dragging a diamond shifts every keyframe at that time', () => {
  transientWithKeyframe(120)
  render()
  bar()
  const d = host.querySelector('.timeline-diamond')!
  pointer(d, 'pointerdown', 200)
  pointer(window, 'pointermove', 400)
  pointer(window, 'pointerup', 400)
  expect(firstTrack().keyframes.map(k => k.t)).toEqual([0, 240])
})

it('a plain click on a diamond seeks to it instead of moving it', () => {
  transientWithKeyframe(120)
  render()
  bar()
  const d = host.querySelector('.timeline-diamond')!
  pointer(d, 'pointerdown', 200)
  pointer(window, 'pointerup', 201)
  expect(firstTrack().keyframes.map(k => k.t)).toEqual([0, 120])
  expect(store.playheadTime()).toBe(120)
})

it('shortening the duration past a keyframe asks first', () => {
  transientWithKeyframe(500)
  render()
  const input = document.querySelector('#timeline-duration') as HTMLInputElement
  const commit = (v: string) => {
    act(() => {
      input.focus()
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, v)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    act(() => { input.blur() })
  }
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  commit('300')
  expect(confirm).toHaveBeenCalledOnce()
  expect(tr().durationS).toBe(600)
  confirm.mockReturnValue(true)
  commit('300')
  expect(tr().durationS).toBe(300)
  expect(firstTrack().keyframes.map(k => k.t)).toEqual([0])
})

it('switches the start condition', () => {
  setSimulationMode('transient')
  render()
  const sel = host.querySelector('#timeline-start') as HTMLSelectElement
  act(() => { sel.value = 'steadyConverged'; sel.dispatchEvent(new Event('change', { bubbles: true })) })
  expect(tr().start.kind).toBe('steadyConverged')
})

it('toggles the track panel', () => {
  setSimulationMode('transient')
  render()
  const btn = host.querySelector('#timeline-tracks-toggle') as HTMLButtonElement
  expect(trackPanelOpen()).toBe(false)
  act(() => { btn.click() })
  expect(trackPanelOpen()).toBe(true)
})


const playheadInput = () => host.querySelector<HTMLInputElement>('#timeline-playhead-time')!
const typePlayhead = (text: string, enter = false) => {
  const input = playheadInput()
  act(() => {
    input.focus()
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, text)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  act(() => {
    if (enter) input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    else input.blur()
  })
}

it('commits an exact t=2 playhead and lets a real server power edit create the step there', () => {
  setSimulationMode('transient')
  store.loadProject(store.getProject()!, null)
  render()
  const beforeLog = store.getLog().length
  typePlayhead('2', true)
  expect(store.playheadTime()).toBe(2)
  expect(playheadInput().value).toBe('2')
  expect(store.isDirty()).toBe(false)
  expect(store.getLog()).toHaveLength(beforeLog)
  const target = firstTrack()
  const start = target.keyframes[0]!.value
  act(() => {
    expect(store.edit('setServerPower', '', draft => {
      const rack = (draft as any).constructions.racks[target.modelUid]
      rack.constructions.servers[target.target].cooling.power = start + 100
    })).toBe(true)
  })
  expect(firstTrack().keyframes).toContainEqual({ t: 2, value: start + 100, rampS: 0 })
  expect(firstTrack().keyframes[0]!.value).toBe(start)
})

it('keeps precise subsecond grid steps and clamps the numeric playhead to the duration', () => {
  setSimulationMode('transient')
  const doc = JSON.parse(JSON.stringify(store.getProject()))
  doc.transient.gridS = .25
  store.loadProject(doc, null)
  render()
  expect(playheadInput().min).toBe('0')
  expect(playheadInput().max).toBe('600')
  expect(playheadInput().step).toBe('0.25')
  typePlayhead('2.5')
  expect(store.playheadTime()).toBe(2.5)
  expect(playheadInput().value).toBe('2.5')
  typePlayhead('-4')
  expect(store.playheadTime()).toBe(0)
  typePlayhead('900')
  expect(store.playheadTime()).toBe(600)
  typePlayhead('2.62')
  expect(store.playheadTime()).toBe(2.5)
  expect(playheadInput().value).toBe('2.5')
})

it('seeks the accessible slider with keyboard arrows and Home/End without moving timeline diamonds', () => {
  transientWithKeyframe(120)
  render()
  const b = bar()
  expect(b.getAttribute('role')).toBe('slider')
  expect(b.tabIndex).toBe(0)
  expect(b.getAttribute('aria-valuemin')).toBe('0')
  expect(b.getAttribute('aria-valuemax')).toBe('600')
  const seek = (key: string) => {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
    act(() => { b.dispatchEvent(event) })
    expect(event.defaultPrevented).toBe(true)
  }
  act(() => { b.focus() })
  act(() => {
    b.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }))
    b.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }))
  })
  expect(store.playheadTime()).toBe(2)
  expect(b.getAttribute('aria-valuenow')).toBe('2')
  expect(playheadInput().value).toBe('2')
  seek('ArrowLeft'); expect(store.playheadTime()).toBe(1)
  seek('Home'); seek('ArrowLeft'); expect(store.playheadTime()).toBe(0)
  seek('End'); seek('ArrowRight'); expect(store.playheadTime()).toBe(600)
  expect(firstTrack().keyframes.map(k => k.t)).toEqual([0, 120])
  expect(document.activeElement).toBe(b)
})

it('gives the numeric playhead and slider localized accessible names', () => {
  setSimulationMode('transient')
  act(() => { setLocale('en-US') })
  render()
  expect(playheadInput().getAttribute('aria-label')).toBe('Playhead (s)')
  expect(bar().getAttribute('aria-label')).toBe('Playhead (s)')
  act(() => { setLocale('zh-CN') })
  expect(playheadInput().getAttribute('aria-label')).toBe('播放头（秒）')
  expect(bar().getAttribute('aria-label')).toBe('播放头（秒）')
})

function typeNumber(id: string, value: string) {
  const el = document.querySelector<HTMLInputElement>(`#${id}`)!
  act(() => {
    el.focus()
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
  act(() => { el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })) })
}

it('exposes compact accessible time settings and creates a real subsecond power keyframe after changing the seek step', () => {
  setSimulationMode('transient')
  render()
  const settings = host.querySelector<HTMLDetailsElement>('#timeline-settings')!
  expect(settings.open).toBe(false)
  act(() => { settings.querySelector('summary')!.click() })
  expect(settings.open).toBe(true)
  const grid = document.querySelector<HTMLInputElement>('#timeline-grid')!
  expect(grid.getAttribute('aria-label')).toBe('定位步长 (s)')
  expect(grid.getAttribute('aria-describedby')).toBe('timeline-grid-help')
  expect(document.querySelector('#timeline-grid-help')!.textContent).toContain('CFD 自适应时间步另行设置')
  const cfg = structuredClone((store.getProject() as any).runDefaults.simulationConfig)
  typeNumber('timeline-grid', '.1')
  expect(tr().gridS).toBe(.1)
  expect(playheadInput().step).toBe('0.1')
  typePlayhead('.2', true)
  expect(store.playheadTime()).toBe(.2)
  const target = firstTrack()
  act(() => { store.edit('powerAtPointTwo', '', draft => {
    const rack = (draft as any).constructions.racks[target.modelUid]
    rack.constructions.servers[target.target].cooling.power = 1200
  }) })
  expect(firstTrack().keyframes).toContainEqual({ t: .2, value: 1200, rampS: 0 })
  expect((store.getProject() as any).runDefaults.simulationConfig).toEqual(cfg)
  expect(document.querySelector<HTMLInputElement>('#timeline-duration')!.getAttribute('aria-label')).toBe('时长 (s)')
})

it('refuses an incompatible seek step and keeps existing times, ramps and duration visible unchanged', () => {
  transientWithKeyframe(2)
  render()
  act(() => { host.querySelector<HTMLDetailsElement>('#timeline-settings')!.querySelector('summary')!.click() })
  typeNumber('timeline-grid', '.1')
  act(() => { expect(applyTimelineOp('setRamp', setKeyframe(trackKeyOf(firstTrack()), 1, { rampS: .3 }))).toBe(true) })
  const before = JSON.stringify(tr())
  typeNumber('timeline-grid', '1')
  expect(JSON.stringify(tr())).toBe(before)
  expect(document.querySelector<HTMLInputElement>('#timeline-grid')!.value).toBe('0.1')
  expect(document.querySelector('.timeline-settings-error')!.textContent).toContain('关键帧或斜坡起点')
  typeNumber('timeline-grid', '0')
  expect(JSON.stringify(tr())).toBe(before)
  expect(document.querySelector<HTMLInputElement>('#timeline-grid')!.value).toBe('0.1')
  expect(document.querySelector('.timeline-settings-error')!.textContent).toContain('有限且大于 0')
  act(() => { store.loadProject(store.getProject()!, null) })
  expect(document.querySelector('.timeline-settings-error')).toBeNull()
})

it('lets keyboard users close time settings with Escape and return to its summary', () => {
  setSimulationMode('transient')
  render()
  const settings = host.querySelector<HTMLDetailsElement>('#timeline-settings')!
  act(() => { settings.querySelector('summary')!.click() })
  const grid = document.querySelector<HTMLInputElement>('#timeline-grid')!
  act(() => { grid.focus(); grid.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
  expect(settings.open).toBe(false)
  expect(document.activeElement).toBe(settings.querySelector('summary'))
})

it('puts settings outside a clipped toolbar and closes on an outside pointer without changing tracks', () => {
  setSimulationMode('transient')
  host.id = 'toolbar'
  host.style.overflow = 'hidden'
  render()
  const summary = host.querySelector('summary')!
  act(() => { summary.click() })
  const panel = document.querySelector<HTMLDivElement>('#timeline-settings-panel')!
  expect(panel.parentElement).toBe(document.body)
  expect(host.contains(panel)).toBe(false)
  expect(panel.hidden).toBe(false)
  expect(document.activeElement?.id).toBe('timeline-duration')
  const before = JSON.stringify(tr())
  pointer(document.body, 'pointerdown', 0)
  expect(panel.hidden).toBe(true)
  expect(summary.getAttribute('aria-expanded')).toBe('false')
  expect(JSON.stringify(tr())).toBe(before)
})
