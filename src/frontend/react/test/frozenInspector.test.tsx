// The inspector off t = 0 (transient design §2): a banner says what still edits
// and leads back to the start; a refused edit shows its reason in Chinese.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, useSyncExternalStore } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as store from '../src/projectStore'
import { getLocale, setLocale } from '../src/i18n'
import { readProjectFileText } from '../src/io/projectFiles'
import { FrozenInspector } from '../src/transient/FrozenInspector'
import { setSimulationMode } from '../src/transient/simulationMode'
import { EquipmentInspector } from '../src/components/EquipmentInspector'
import { transientModel, transientOf } from '../src/transient/model'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let prev: ReturnType<typeof getLocale>

beforeEach(() => {
  prev = getLocale(); setLocale('zh-CN')
  const r = readProjectFileText(readFileSync(resolve(process.cwd(), '../../../case/Base_coldAisleIso/Base_coldAisleIso.cfdproj'), 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  act(() => root.render(<FrozenInspector><input id="static-field" /></FrozenInspector>))
})
afterEach(() => { act(() => root.unmount()); host.remove(); setLocale(prev) })

const banner = () => host.querySelector('.frozen-banner')
const wrapper = () => host.querySelector('.inspector-frozen')

it('is plain in a steady project and at the start of a transient one', () => {
  expect(banner()).toBeNull()
  act(() => { setSimulationMode('transient') })
  expect(banner()).toBeNull()
  expect(wrapper()).toBeNull()
  expect(host.querySelector('#static-field')).not.toBeNull()
})

it('off the start: banner with the time, frozen wrapper, and a way back', () => {
  act(() => { setSimulationMode('transient') })
  act(() => { store.setPlayhead(120) })
  expect(banner()!.textContent).toContain('t = 120 s')
  expect(wrapper()!.querySelector('#static-field')).not.toBeNull()
  act(() => { (banner()!.querySelector('button') as HTMLButtonElement).click() })
  expect(store.playheadTime()).toBe(0)
  expect(banner()).toBeNull()
})

it('a refused static edit shows its reason', () => {
  act(() => { setSimulationMode('transient') })
  act(() => { store.setPlayhead(120) })
  let ok = true
  act(() => {
    ok = store.edit('rename', '', d => { (store.findConstruction(d, 'm_1')!.model as { meta: { name: string } }).meta.name = 'x' })
  })
  expect(ok).toBe(false)
  const notice = host.querySelector('.frozen-notice')!
  expect(notice.textContent).toContain('未执行')
  expect(notice.textContent).toContain('只读')
})

const input = (id: string) => host.querySelector<HTMLInputElement>(`#${id}`)!
const firstPowerTrack = () => transientOf(store.getProject()).tracks.find(track => track.param === 'server.power')!

function CurrentEquipment() {
  const project = useSyncExternalStore(store.subscribe, store.getProject)
  const target = firstPowerTrack()
  const model = store.findConstruction(project!, target.modelUid)!.model
  return <FrozenInspector><EquipmentInspector model={model} equipmentId={target.target} /></FrozenInspector>
}
function renderEquipment() {
  act(() => { setSimulationMode('transient') })
  act(() => root.render(<CurrentEquipment />))
}
function type(input: HTMLInputElement, value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

it('uses native disabled for static equipment fields, excluding them from keyboard focus off t=0', () => {
  renderEquipment()
  act(() => { store.setPlayhead(2) })
  const before = JSON.stringify(store.getProject())
  for (const id of ['eq-name', 'eq-inlet-offset', 'eq-outlet-offset', 'eq-fan-power-mode', 'eq-bc-mode']) {
    const control = host.querySelector<HTMLInputElement | HTMLSelectElement>(`#${id}`)!
    expect(control, id).not.toBeNull()
    expect(control.disabled, id).toBe(true)
    expect(control.matches(':disabled'), id).toBe(true)
    act(() => { control.focus() })
    expect(document.activeElement, id).not.toBe(control)
  }
  // The next actionable controls retain normal keyboard focus. Native disabled
  // fields cannot receive Tab focus or text entry; real-browser Tab is checked
  // in the product receipt, since jsdom does not perform default Tab navigation.
  const start = banner()!.querySelector<HTMLButtonElement>('button')!
  act(() => { start.focus() })
  expect(document.activeElement).toBe(start)
  act(() => { input('eq-power').focus() })
  expect(document.activeElement).toBe(input('eq-power'))
  act(() => { input('eq-power').blur() })
  expect(JSON.stringify(store.getProject())).toBe(before)
})

it('drops an uncommitted static name before freezing and never displays a rejected fake name', () => {
  renderEquipment()
  const name = input('eq-name')
  const committed = name.value
  const before = transientModel().staticText(store.getProject()!)
  act(() => { name.focus() })
  type(name, 'uncommitted fake name')
  expect(name.value).toBe('uncommitted fake name')
  act(() => { store.setPlayhead(2) })
  expect(name.disabled).toBe(true)
  expect(name.value).toBe(committed)
  act(() => { name.focus(); name.blur() })
  expect(name.value).toBe(committed)
  expect(transientModel().staticText(store.getProject()!)).toBe(before)
  act(() => { store.setPlayhead(0) })
  expect(name.disabled).toBe(false)
  expect(name.value).toBe(committed)
})

it('keeps a tracked equipment power field editable and records an actual t=2 keyframe', () => {
  renderEquipment()
  act(() => { store.setPlayhead(2) })
  const before = transientModel().staticText(store.getProject()!)
  const power = input('eq-power')
  expect(power.disabled).toBe(false)
  act(() => { power.focus() })
  type(power, '1200')
  act(() => { power.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })) })
  expect(firstPowerTrack().keyframes).toContainEqual({ t: 2, value: 1200, rampS: 0 })
  expect(power.value).toBe('1200')
  expect(transientModel().staticText(store.getProject()!)).toBe(before)
})

it('preserves independent disabled controls and both display/track allowlist forms across seek', () => {
  act(() => root.render(<FrozenInspector>
    <input id="static" defaultValue="committed" />
    <input id="independent" disabled defaultValue="already disabled" />
    <input id="display" className="frozen-ok" />
    <div className="frozen-ok"><select id="display-group"><option>opacity</option></select></div>
    <div className="kf-field"><input id="track" /><input id="disabled-track" disabled /></div>
  </FrozenInspector>))
  act(() => { setSimulationMode('transient'); store.setPlayhead(2) })
  expect(input('static').disabled).toBe(true)
  expect(input('independent').disabled).toBe(true)
  expect(input('display').disabled).toBe(false)
  expect(host.querySelector<HTMLSelectElement>('#display-group')!.disabled).toBe(false)
  expect(input('track').disabled).toBe(false)
  expect(input('disabled-track').disabled).toBe(true)
  act(() => { store.setPlayhead(0) })
  expect(input('static').disabled).toBe(false)
  expect(input('independent').disabled).toBe(true)
  expect(input('disabled-track').disabled).toBe(true)
})

it('locks late legacy controls and restores only its own lock when the allowlist changes', async () => {
  act(() => { setSimulationMode('transient'); store.setPlayhead(2) })
  const added = document.createElement('input')
  added.id = 'late'; added.defaultValue = 'legacy'
  await act(async () => { wrapper()!.append(added) })
  expect(added.disabled).toBe(true)
  await act(async () => { added.disabled = false })
  expect(added.disabled).toBe(true)
  await act(async () => { added.classList.add('frozen-ok') })
  expect(added.disabled).toBe(false)
  await act(async () => { added.classList.remove('frozen-ok') })
  expect(added.disabled).toBe(true)
  act(() => { store.setPlayhead(0) })
  expect(added.disabled).toBe(false)
  added.remove()
})

it('disables static buttons for focus and native click while preserving navigation/display and tracked actions', () => {
  const mutate = vi.fn()
  const navigate = vi.fn()
  const keyframe = vi.fn()
  act(() => root.render(<FrozenInspector>
    <button id="static-add" onClick={mutate}>Add</button>
    <button id="static-delete" onClick={mutate}>Delete</button>
    <button id="independent-button" disabled>Already disabled</button>
    <button id="display-button" className="frozen-ok" onClick={navigate}>Back</button>
    <div className="kf-field"><button id="track-button" onClick={keyframe}>Dynamic action</button></div>
  </FrozenInspector>))
  act(() => { setSimulationMode('transient'); store.setPlayhead(2) })
  for (const id of ['static-add', 'static-delete']) {
    const button = host.querySelector<HTMLButtonElement>(`#${id}`)!
    expect(button.disabled).toBe(true)
    act(() => { button.focus(); button.click() })
    expect(document.activeElement).not.toBe(button)
  }
  expect(mutate).not.toHaveBeenCalled()
  const display = host.querySelector<HTMLButtonElement>('#display-button')!
  const tracked = host.querySelector<HTMLButtonElement>('#track-button')!
  expect(display.disabled).toBe(false)
  expect(tracked.disabled).toBe(false)
  act(() => { display.focus(); display.click(); tracked.click() })
  expect(navigate).toHaveBeenCalledOnce()
  expect(keyframe).toHaveBeenCalledOnce()
  act(() => { store.setPlayhead(0) })
  expect(host.querySelector<HTMLButtonElement>('#static-add')!.disabled).toBe(false)
  expect(host.querySelector<HTMLButtonElement>('#independent-button')!.disabled).toBe(true)
})
