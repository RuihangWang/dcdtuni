import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { FeatureTree } from '../../src/components/FeatureTree'

/**
 * The tree's root row: a click selects every model, and its caret folds or
 * opens the whole tree.
 *
 * Both were missing: the root's caret was a static `▾` with no handler, and a
 * click on the row dropped the selection instead of taking all of it.
 */

let host: HTMLDivElement
let root: Root
let uids: string[]
/** Every `onSelect` call, in order: `[uid, toggle]`. */
let calls: [string | null, boolean | undefined][]
let selected: string[]

function render() {
  flushSync(() =>
    root.render(
      <FeatureTree
        project={store.getProject()}
        selectedUid={selected[selected.length - 1] ?? null}
        selectedUids={selected}
        onSelect={(uid, toggle) => {
          calls.push([uid, toggle])
        }}
      />,
    ),
  )
}

const rootRow = () => host.querySelector<HTMLElement>('#tree-root-sticky .node-label')!
const rootCaret = () => rootRow().querySelector<HTMLElement>('.node-expander')!
const modelRows = () => host.querySelectorAll('[data-model-id]').length
const categoryChildren = () => host.querySelectorAll('[data-tree-children]').length

function clickRoot() {
  flushSync(() => rootRow().dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

function clickRootCaret() {
  flushSync(() => rootCaret().dispatchEvent(new MouseEvent('click', { bubbles: true })))
  render()
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  uids = []
  for (const kind of ['rack', 'rack', 'acu']) {
    const { bucket, model } = createModel(store.getProject() as CfdProject, kind)
    store.addModel(bucket, model)
    uids.push(model.uid)
  }
  calls = []
  selected = []
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the root row', () => {
  it('selects every model on a click — one plain click, then additive ones', () => {
    clickRoot()
    expect(calls.map(([uid]) => uid).sort()).toEqual([...uids].sort())
    expect(calls[0]![1]).toBeFalsy()
    expect(calls.slice(1).every(([, toggle]) => toggle === true)).toBe(true)
  })

  it('does NOT select the models when the caret is clicked', () => {
    clickRootCaret()
    expect(calls).toEqual([])
  })

  it('drops the selection when everything is already selected', () => {
    selected = [...uids]
    render()
    clickRoot()
    expect(calls).toEqual([[null, undefined]])
  })

  it('folds the whole tree from the caret, and opens it again', () => {
    expect(modelRows()).toBe(3)
    expect(categoryChildren()).toBeGreaterThan(0)
    clickRootCaret()
    expect(modelRows()).toBe(0)
    expect(categoryChildren()).toBe(0)
    expect(rootCaret().textContent).toBe('▸')
    clickRootCaret()
    expect(modelRows()).toBe(3)
    expect(rootCaret().textContent).toBe('▾')
  })

  it('"collapse all" also folds a branch that was opened by hand', () => {
    // Fold one category by its own caret, then open all from the root: the
    // hand-folded one opens too. Then collapse all: it folds along with the rest.
    const catCaret = host.querySelector<HTMLElement>('[data-category] .node-expander')!
    flushSync(() => catCaret.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    render()
    const before = categoryChildren()
    clickRootCaret() // collapse all
    expect(categoryChildren()).toBe(0)
    clickRootCaret() // open all
    expect(categoryChildren()).toBeGreaterThan(before)
  })
})
