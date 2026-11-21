import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { FeatureTree } from '../../src/components/FeatureTree'

/**
 * The visibility checkbox acts on the SELECTION, not just the row it is on.
 *
 * Hiding is how a crowded room is worked in: select a row of racks, hide them,
 * look at what is behind. React's checkbox called `setEnabled(uid)` on the one
 * model it sat on, so hiding twenty racks was twenty clicks and twenty undo
 * steps. The legacy's `toggleModelEnabled` (STL_generate.html:12953) has three
 * branches:
 *
 * 1. the clicked model is part of a multi-selection → toggle ALL of them;
 * 2. shift+click on a model outside the selection → toggle the range from the
 *    last-toggled row to this one;
 * 3. otherwise → toggle the one row.
 *
 * And it drops what it hid from the selection: a hidden model that stays
 * selected leaves the inspector editing something nobody can see.
 */

let host: HTMLDivElement
let root: Root
let uids: string[]
/** The host's selection, which this component only ever asks to change. */
let selection: string[]

function render() {
  flushSync(() =>
    root.render(
      <FeatureTree
        project={store.getProject()}
        selectedUid={selection[selection.length - 1] ?? null}
        selectedUids={selection}
        onSelect={(uid, toggle) => {
          if (uid === null) selection = []
          else if (!toggle) selection = [uid]
          else if (selection.includes(uid)) selection = selection.filter((u) => u !== uid)
          else selection = [...selection, uid]
        }}
      />,
    ),
  )
}

function box(uid: string): HTMLInputElement {
  const el = host.querySelector<HTMLInputElement>(
    `[data-model-id="${uid}"] input.node-visibility-toggle`,
  )
  expect(el, uid).not.toBeNull()
  return el!
}

/**
 * Click a checkbox. React derives a checkbox's `onChange` from the CLICK event,
 * so `change` alone runs nothing — and the modifier keys ride on that same
 * event, which is how shift reaches the handler at all.
 */
function click(el: HTMLInputElement, shift = false) {
  flushSync(() => el.dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: shift })))
  render()
}

function enabled(uid: string): boolean {
  return store.findConstruction(store.getProject() as CfdProject, uid)!.model.meta.enabled !== false
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  uids = []
  for (let i = 0; i < 4; i++) {
    const { bucket, model } = createModel(store.getProject() as CfdProject, 'baffle')
    store.addModel(bucket, model)
    uids.push(model.uid)
  }
  selection = []
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('with several models selected', () => {
  beforeEach(() => {
    selection = [uids[0]!, uids[1]!, uids[2]!]
    render()
  })

  it('hides all of them from one click', () => {
    click(box(uids[1]!))
    expect(uids.map(enabled)).toEqual([false, false, false, true])
  })

  it('is ONE undo step for the whole batch', () => {
    const depth = store.undoDepth()
    click(box(uids[1]!))
    expect(store.undoDepth()).toBe(depth + 1)
    store.undo()
    expect(uids.map(enabled)).toEqual([true, true, true, true])
  })

  it('drops what it hid from the selection', () => {
    // A hidden model that stays selected leaves the inspector editing something
    // nobody can see, and the next drag moves it invisibly.
    click(box(uids[1]!))
    expect(selection).toEqual([])
  })

  it('brings them all back on the second click', () => {
    click(box(uids[1]!))
    // The selection is gone by now, so this is the single-row branch — but the
    // three that were hidden are still hidden, which is what re-selecting is
    // for. Only the clicked one comes back.
    click(box(uids[1]!))
    expect(uids.map(enabled)).toEqual([false, true, false, true])
  })

  it('shows a batch too, and SHOWING disturbs no selection', () => {
    selection = [uids[0]!, uids[1]!]
    render()
    click(box(uids[0]!))
    expect(uids.map(enabled)).toEqual([false, false, true, true])
    // Hiding cleared the selection, so they are re-selected the way a user
    // would: the pair is still what the tree highlights.
    selection = [uids[0]!, uids[1]!]
    render()
    click(box(uids[0]!))
    expect(uids.map(enabled)).toEqual([true, true, true, true])
    // Nothing was hidden by that click, so nothing leaves the selection.
    expect(selection).toEqual([uids[0], uids[1]])
  })
})

describe('with nothing much selected', () => {
  it('toggles only the row that was clicked', () => {
    selection = [uids[0]!]
    render()
    click(box(uids[2]!))
    expect(uids.map(enabled)).toEqual([true, true, false, true])
  })

  it('shift+click toggles the range from the last row toggled', () => {
    click(box(uids[0]!))
    expect(uids.map(enabled)).toEqual([false, true, true, true])
    // The anchor is the row toggled a moment ago, not the selection anchor:
    // the legacy keeps `modelVisibilityToggleAnchorId` for this alone.
    click(box(uids[2]!), true)
    expect(uids.map(enabled)).toEqual([false, false, false, true])
  })

  it('a plain click moves the anchor, so the next shift+click starts there', () => {
    click(box(uids[0]!))
    click(box(uids[3]!))
    click(box(uids[1]!), true)
    // Anchor is row 3, so the range is rows 1..3 — row 0 is untouched by this
    // click and stays hidden from the first one.
    expect(uids.map(enabled)).toEqual([false, false, false, false])
  })
})
