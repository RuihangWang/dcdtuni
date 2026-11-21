// The track panel (transient design §2): the selected object's parameter
// tracks, filtered to the air/liquid mode, with keyframes that can be added,
// dragged, ramped, edited exactly and deleted.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import * as store from '../src/projectStore'
import { readProjectFileText } from '../src/io/projectFiles'
import { publishSelection } from '../src/selectionBus'
import { transientOf } from '../src/transient/model'
import { setSimulationMode } from '../src/transient/simulationMode'
import { setTrackPanelOpen } from '../src/transient/timelineUi'
import { TrackPanel } from '../src/transient/TrackPanel'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const CASE = resolve(process.cwd(), '../../../case/Base_coldAisleIso/Base_coldAisleIso.cfdproj')
let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
const power = () => transientOf(store.getProject()).tracks.find(t => t.param === 'server.power' && t.target === 'e_3')!

beforeEach(() => {
  const r = readProjectFileText(readFileSync(CASE, 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  setSimulationMode('transient')                  // durationS 600, gridS 1
  setTrackPanelOpen(true)
  publishSelection({ uid: null, node: null })
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove(); setTrackPanelOpen(false) })

const render = (mode: 'air' | 'liquid' = 'air') => act(() => root.render(<TrackPanel coolingMode={mode} />))
const lane = (el: Element) => {
  Object.defineProperty(el, 'getBoundingClientRect', {
    configurable: true, value: () => ({ left: 0, top: 0, width: 1000, height: 40, right: 1000, bottom: 40, x: 0, y: 0 }),
  })
  return el
}
const pointer = (target: EventTarget, type: string, clientX: number) =>
  act(() => { target.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX, button: 0 })) })
const rowOf = (target: string) => host.querySelector(`.track-row[data-target="${target}"]`)!
const setField = (input: HTMLInputElement, v: string) => {
  act(() => {
    input.focus()
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, v)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  act(() => { input.blur() })
}

it('is hidden while closed and asks for a selection when open with none', () => {
  setTrackPanelOpen(false)
  render()
  expect(host.querySelector('#track-panel')).toBeNull()
  act(() => setTrackPanelOpen(true))
  render()
  expect(host.querySelector('#track-panel')).not.toBeNull()
  expect(host.querySelectorAll('.track-row')).toHaveLength(0)
})

it("lists the selected rack's air tracks and hides them in liquid mode", () => {
  act(() => publishSelection({ uid: 'm_1', node: null }))
  render()
  expect(rowOf('e_3')).toBeTruthy()
  render('liquid')
  expect(host.querySelectorAll('.track-row')).toHaveLength(0)
})

it('adds a keyframe at the playhead with the value there', () => {
  act(() => publishSelection({ uid: 'm_1', node: null }))
  act(() => store.setPlayhead(120))
  render()
  act(() => { (rowOf('e_3').querySelector('.track-add') as HTMLButtonElement).click() })
  expect(power().keyframes).toEqual([{ t: 0, value: 150 }, { t: 120, value: 150, rampS: 0 }])
})

it('edits a keyframe exactly and deletes it', () => {
  act(() => publishSelection({ uid: 'm_1', node: null }))
  act(() => store.setPlayhead(120))
  render()
  act(() => { (rowOf('e_3').querySelector('.track-add') as HTMLButtonElement).click() })
  const dot = () => rowOf('e_3').querySelectorAll('.track-key')[1]!
  pointer(dot(), 'pointerdown', 200)
  pointer(window, 'pointerup', 200)
  const editor = host.querySelector('#track-key-editor')!
  setField(editor.querySelector('.track-key-value input') as HTMLInputElement, '300')
  expect(power().keyframes[1]).toEqual({ t: 120, value: 300, rampS: 0 })
  act(() => { (host.querySelector('#track-key-delete') as HTMLButtonElement).click() })
  expect(power().keyframes).toEqual([{ t: 0, value: 150 }])
})

it('drags a keyframe in time and drags out a ramp', () => {
  act(() => publishSelection({ uid: 'm_1', node: null }))
  act(() => store.setPlayhead(120))
  render()
  act(() => { (rowOf('e_3').querySelector('.track-add') as HTMLButtonElement).click() })
  lane(rowOf('e_3').querySelector('.track-lane')!)
  pointer(rowOf('e_3').querySelectorAll('.track-key')[1]!, 'pointerdown', 200)
  pointer(window, 'pointermove', 400)
  pointer(window, 'pointerup', 400)
  expect(power().keyframes[1]!.t).toBe(240)
  lane(rowOf('e_3').querySelector('.track-lane')!)
  pointer(rowOf('e_3').querySelectorAll('.track-ramp')[0]!, 'pointerdown', 400)
  pointer(window, 'pointermove', 300)
  pointer(window, 'pointerup', 300)
  expect(power().keyframes[1]!.rampS).toBe(60)
})
