import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { FeatureTree } from '../../src/components/FeatureTree'
import { mergeSlotsIntoEquipment } from '../../src/geometry/rackSlots'

/**
 * Shift on a caret folds the same branch on every selected model.
 *
 * The legacy's `toggleTreeNode` (STL_generate.html:13118): with several
 * models selected, shift+click on any of their carets batches the fold across
 * all of them. React folded only the one caret.
 */

let host: HTMLDivElement
let root: Root
let uids: string[]
let boxUids: string[]

function render(selected: string[]) {
  flushSync(() =>
    root.render(
      <FeatureTree
        project={store.getProject()}
        selectedUid={selected[selected.length - 1] ?? null}
        selectedUids={selected}
        onSelect={() => {}}
      />,
    ),
  )
}

const modelRow = (uid: string) => host.querySelector<HTMLElement>(`[data-model-id="${uid}"]`)!
const modelOpen = (uid: string) => !!host.querySelector(`[data-node="rack-equipment:${uid}"]`)
const categoryRow = (bucket: string) => host.querySelector<HTMLElement>(`[data-category="${bucket}"]`)!
const categoryOpen = (bucket: string) => !!host.querySelector(`[data-tree-children="cat:${bucket}"]`)

function caretClick(el: HTMLElement, shift: boolean, selected: string[]) {
  const caret = el.querySelector<HTMLElement>('.node-expander')!
  flushSync(() => caret.dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: shift })))
  render(selected)
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  uids = []
  for (let i = 0; i < 3; i++) {
    const { bucket, model } = createModel(store.getProject() as CfdProject, 'rack')
    store.addModel(bucket, model)
    uids.push(model.uid)
    // A fresh rack has NO servers, so its Equipments header has no children
    // and no caret; one server gives the sub-branch case something to fold.
    mergeSlotsIntoEquipment(model.uid, 'front', [10, 11], 'fanServer')
  }
  // A second category, so "every type" is more than one row.
  boxUids = []
  for (let i = 0; i < 2; i++) {
    const { bucket, model } = createModel(store.getProject() as CfdProject, 'box')
    store.addModel(bucket, model)
    boxUids.push(model.uid)
  }
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('shift on a caret', () => {
  it('folds the model branch on every selected model, and leaves the unselected one open', () => {
    const selected = [uids[0]!, uids[1]!]
    render(selected)
    expect(uids.map(modelOpen)).toEqual([true, true, true])
    caretClick(modelRow(uids[0]!), true, selected)
    expect(uids.map(modelOpen)).toEqual([false, false, true])
    // And back, from the OTHER selected model's caret.
    caretClick(modelRow(uids[1]!), true, selected)
    expect(uids.map(modelOpen)).toEqual([true, true, true])
  })

  it('without shift folds only the one caret', () => {
    const selected = [uids[0]!, uids[1]!]
    render(selected)
    caretClick(modelRow(uids[0]!), false, selected)
    expect(uids.map(modelOpen)).toEqual([false, true, true])
  })

  it('shift on a TYPE caret folds every type, selected or not', () => {
    // The case the old rule could not reach: 工程根节点 selects every MODEL but
    // no category, so `selectedCategories` was empty and shift folded one type.
    render([...uids, ...boxUids])
    expect(['racks', 'boxes'].map(categoryOpen)).toEqual([true, true])
    caretClick(categoryRow('racks'), true, [...uids, ...boxUids])
    expect(['racks', 'boxes'].map(categoryOpen)).toEqual([false, false])
    caretClick(categoryRow('boxes'), true, [...uids, ...boxUids])
    expect(['racks', 'boxes'].map(categoryOpen)).toEqual([true, true])
  })

  it('shift on an item caret batches with NOTHING selected', () => {
    render([])
    expect(uids.map(modelOpen)).toEqual([true, true, true])
    caretClick(modelRow(uids[0]!), true, [])
    expect(uids.map(modelOpen)).toEqual([false, false, false])
  })

  it('without shift a type caret folds only that type', () => {
    render([...uids, ...boxUids])
    caretClick(categoryRow('racks'), false, [...uids, ...boxUids])
    expect(['racks', 'boxes'].map(categoryOpen)).toEqual([false, true])
  })

  it('folds a SUB-branch — the Equipments header — across the selection', () => {
    const selected = [uids[0]!, uids[1]!]
    render(selected)
    const eq = (uid: string) => host.querySelector<HTMLElement>(`[data-node="rack-equipment:${uid}"]`)!
    const eqOpen = (uid: string) => !!host.querySelector(`[data-node^="srv-eq:${uid}:"]`)
    expect([eqOpen(uids[0]!), eqOpen(uids[1]!), eqOpen(uids[2]!)]).toEqual([true, true, true])
    caretClick(eq(uids[1]!), true, selected)
    expect([eqOpen(uids[0]!), eqOpen(uids[1]!), eqOpen(uids[2]!)]).toEqual([false, false, true])
  })
})
