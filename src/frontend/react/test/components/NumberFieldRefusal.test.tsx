// A number the store refuses must not stay on screen looking accepted.
//
// Off t = 0 a transient project freezes its scene: an edit to a static field
// is refused whole and the store keeps the old value. NumberField needs no
// help for that: the blur that commits also ends `editing`, and the re-sync
// on `editing` puts the stored value back. This pins that behaviour.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, useSyncExternalStore } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { NumberField, display } from '../../src/components/NumberField'
import * as store from '../../src/projectStore'
import { readProjectFileText } from '../../src/io/projectFiles'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const CASE = resolve(process.cwd(), '../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json')
let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
  const r = readProjectFileText(readFileSync(CASE, 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  store.edit('setTransient', '', d => { (d as any).transient.mode = 'transient'; (d as any).transient.durationS = 600 })
  store.setPlayhead(120)
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove() })

// The editor's panels re-render from the store; so does this host.
function Bound({ read, write }: { read: () => number; write: (v: number) => void }) {
  useSyncExternalStore(store.subscribe, store.getProject)
  return <NumberField value={read()} onCommit={write} />
}

function typeAndBlur(input: HTMLInputElement, text: string) {
  act(() => {
    input.focus()
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    set.call(input, text)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  act(() => { input.blur() })
}

it('reverts to the stored value when the store refuses the commit', () => {
  const x = () => (store.getProject() as any).constructions.racks.m_1.geometry.location.x as number
  const start = x()
  act(() => root.render(
    <Bound read={x} write={(v) => { store.edit('moveX', '', d => { (d as any).constructions.racks.m_1.geometry.location.x = v }) }} />,
  ))
  const input = host.querySelector('input')!
  typeAndBlur(input, String(start + 3))
  expect(store.lastEditRejection()?.reason).toBe('static')
  expect(x()).toBe(start)
  expect(input.value).toBe(display(start))
})

it('keeps an accepted value (a keyframe at the playhead)', () => {
  const power = () => (store.getProject() as any).constructions.racks.m_1.constructions.servers.e_1.cooling.power as number
  act(() => root.render(
    <Bound read={power} write={(v) => { store.edit('setPower', '', d => { (d as any).constructions.racks.m_1.constructions.servers.e_1.cooling.power = v }) }} />,
  ))
  const input = host.querySelector('input')!
  typeAndBlur(input, '1500')
  expect(power()).toBe(1500)
  expect(input.value).toBe('1500')
})

it('Escape cancels the draft without committing it on blur, including optional clears', () => {
  const commit = vi.fn()
  const clear = vi.fn()
  act(() => root.render(<NumberField value={0.000653} decimals={6} onCommit={commit} onClear={clear} />))
  const input = host.querySelector('input')!
  const draft = (text: string) => {
    act(() => {
      input.focus()
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, text)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    act(() => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })) })
  }
  draft('1500')
  expect(commit).not.toHaveBeenCalled()
  expect(input.value).toBe('0.000653')
  draft('')
  expect(clear).not.toHaveBeenCalled()
  expect(input.value).toBe('0.000653')
  draft('1')
  act(() => { input.focus(); input.blur() })
  expect(commit).not.toHaveBeenCalled()
})

it('leaves a full precision value unchanged when its rounded box is only focused', () => {
  const commit = vi.fn()
  act(() => root.render(<NumberField value={0.005780832674920559} decimals={2} onCommit={commit} />))
  const input = host.querySelector('input')!
  expect(input.value).toBe('0.01')
  act(() => { input.focus() })
  act(() => { input.blur() })
  expect(commit).not.toHaveBeenCalled()
  act(() => { input.focus() })
  act(() => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })) })
  expect(commit).not.toHaveBeenCalled()
})

it('accepts an intentional edit equal to the old rounded display', () => {
  const commit = vi.fn()
  act(() => root.render(<NumberField value={0.005780832674920559} decimals={2} onCommit={commit} />))
  const input = host.querySelector('input')!
  act(() => {
    input.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    for (const text of ['0.02', '0.01']) {
      setter.call(input, text)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    }
  })
  act(() => input.blur())
  expect(commit).toHaveBeenLastCalledWith(0.01)
})
