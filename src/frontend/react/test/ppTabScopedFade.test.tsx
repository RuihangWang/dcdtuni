/** 3D模型透明度 on the post-processing tab is a VIEW fade, up only while the tab is open.
 *
 * 0a10e8df0 had made it an edit of the models' own opacity (the geometry tab's value): the first visit
 * dimmed the whole room to 15% in the document, and leaving the tab left it there. The fade now goes
 * through the shared display (viewFades.model): mounting the panel puts it up, unmounting takes it down,
 * the value is kept for the next visit, and the document is never touched. */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PostprocessRightPanel } from '../src/components/PostprocessRightPanel'
import { sharedPostprocessDisplay } from '../src/postprocess/sharedDisplay'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createModel } from '../src/legacy/factories'

let host: HTMLDivElement, root: Root
const fades = () => vi.mocked(sharedPostprocessDisplay.configureAppearance).mock.calls
  .map(([patch]) => (patch as { modelOpacity?: number }).modelOpacity).filter((v) => v !== undefined)
const mount = () => act(() => root.render(<PostprocessRightPanel project={store.getProject()} />))
const unmount = () => act(() => root.render(<></>))

beforeEach(() => {
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
  store.loadProject(stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject, null)
  const { bucket, model } = createModel(store.getProject() as CfdProject, 'rack')
  store.addModel(bucket, model)
  vi.spyOn(sharedPostprocessDisplay, 'configureAppearance')
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks() })

it('puts the fade up while the tab is open, takes it down on leaving, keeps the value, never edits the models', () => {
  const before = JSON.stringify(store.getProject())
  mount()
  expect(fades().at(-1)).toBeCloseTo(0.15)
  const input = host.querySelector<HTMLInputElement>('#pp-model-opacity')!
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '40')
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    input.dispatchEvent(new Event('pointerup', { bubbles: true }))
  })
  expect(fades().at(-1)).toBeCloseTo(0.4)
  unmount()
  expect(fades().at(-1)).toBe(1)
  mount()
  expect(fades().at(-1)).toBeCloseTo(0.4)
  expect(JSON.stringify(store.getProject())).toBe(before)
})
