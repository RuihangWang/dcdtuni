import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { FeatureTree } from '../../src/components/FeatureTree'
import {
  resetRememberedViewportForTest,
  rowWindow,
  windowSlice,
} from '../../src/interaction/treeLayout'

/**
 * The tree draws only the rows a scroll position puts on screen.
 *
 * On `case/Alibaba-hongchou` — 287 models — the fold-out tree is 25,489 rows
 * and 121,825 DOM nodes, and every one of them was in the document whether or
 * not anybody could see it. One click in the 3D view selects a model and
 * reveals its row; repainting all of that took seconds, which read as "the
 * editor froze" rather than as "the editor is slow".
 *
 * jsdom reports no layout, so these stub a viewport. An element WITH a
 * `clientHeight` is the case the windowing exists for; one without it is the
 * case that has to keep drawing everything, so a headless test and a collapsed
 * panel both still get a complete tree.
 */

const ROW_H = 32
const VIEW_H = 320
/** A box opens into six face rows, so each model is 7 rows and the tree is 281. */
const ROWS_PER_BOX = 7
const TOTAL_ROWS = 1 + 40 * ROWS_PER_BOX

let host: HTMLDivElement
let root: Root
let uids: string[]
const originalScroll = Element.prototype.scrollIntoView

/** jsdom has no layout: hand every element the box a browser would report. */
function giveViewport(height: number) {
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get: () => height,
  })
}

async function render() {
  flushSync(() =>
    root.render(
      <FeatureTree
        project={store.getProject()}
        selectedUid={null}
        selectedUids={[]}
        onSelect={() => {}}
      />,
    ),
  )
  // The measurement runs in a passive effect; let it and the re-render it
  // schedules land before asserting on the DOM.
  await new Promise((r) => setTimeout(r, 0))
  flushSync(() => {})
}

const container = () => host.querySelector<HTMLElement>('#tree-container')!
const modelRows = () => host.querySelectorAll<HTMLElement>('.model-root-label[data-model-id]')
const spacers = () => host.querySelectorAll<HTMLElement>('li.tree-spacer')
const rowUids = () => [...modelRows()].map((el) => el.dataset.modelId)
const spacerRows = (el: HTMLElement) => Number(el.style.height.replace('px', '')) / ROW_H

function scrollTo(top: number) {
  const el = container()
  let value = top
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => value,
    set(next: number) {
      value = next
    },
  })
  el.dispatchEvent(new Event('scroll'))
}

/** The scroll handler is rAF-throttled, so a frame has to pass. */
async function settle() {
  await new Promise((r) => setTimeout(r, 30))
  flushSync(() => {})
}

beforeEach(() => {
  localStorage.clear()
  resetRememberedViewportForTest()
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  uids = []
  for (let i = 0; i < 40; i++) {
    const { model } = createModel(store.getProject() as CfdProject, 'box')
    store.addModel('boxes', model as never)
    uids.push(model.uid)
  }
  Element.prototype.scrollIntoView = function () {} as never
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  delete (HTMLElement.prototype as { clientHeight?: unknown }).clientHeight
  if (originalScroll) Element.prototype.scrollIntoView = originalScroll
  resetRememberedViewportForTest()
  localStorage.clear()
})

describe('rowWindow', () => {
  it('returns null for a viewport that cannot be measured', () => {
    expect(rowWindow(100, 0, 0, ROW_H, 12, 4)).toBeNull()
    expect(rowWindow(100, 0, 320, 0, 12, 4)).toBeNull()
  })

  it('covers the visible rows plus the overscan on both sides', () => {
    // scrollTop 320 over a 12px pad: row 9 is the first one on screen, ten rows
    // fit, and four more are drawn on each side.
    expect(rowWindow(100, 320, VIEW_H, ROW_H, 12, 4)).toEqual({ start: 5, end: 23 })
  })

  it('clamps at both ends of the tree', () => {
    expect(rowWindow(100, 0, VIEW_H, ROW_H, 12, 4)!.start).toBe(0)
    // A scrollTop past the end — a stale one, read before a fold shortened the
    // tree — still has to draw the tail rather than nothing.
    const past = rowWindow(20, 10_000, VIEW_H, ROW_H, 12, 4)!
    expect(past.end).toBe(20)
    expect(past.start).toBeLessThan(past.end)
  })
})

describe('windowSlice', () => {
  const items = [
    { row: 0, rows: 1 },
    { row: 1, rows: 5 },
    { row: 6, rows: 1 },
    { row: 7, rows: 9 },
  ]

  it('draws everything for a null window', () => {
    const slice = windowSlice(items, null)
    expect(slice.shown).toHaveLength(4)
    expect(slice.before).toBe(0)
    expect(slice.after).toBe(0)
  })

  it('keeps an entry that straddles an edge and spacers out the rest', () => {
    const slice = windowSlice(items, { start: 4, end: 8 })
    // The 5-row branch starts above the window and runs into it, the row at 6
    // sits inside it, and the 9-row one starts inside it and runs out. Only the
    // single row above the window is spacer.
    expect(slice.before).toBe(1)
    expect(slice.shown.map((i) => i.row)).toEqual([1, 6, 7])
    expect(slice.after).toBe(0)
  })

  it('spacers out a run that is entirely below the window', () => {
    expect(windowSlice(items, { start: 0, end: 2 }).after).toBe(10)
  })
})

describe('the windowed tree', () => {
  it('draws a slice of the rows and spacers out the rest', async () => {
    giveViewport(VIEW_H)
    await render()
    expect(container().classList.contains('tree-virtual')).toBe(true)
    expect(rowUids().length).toBeLessThan(40)
    expect(rowUids()[0]).toBe(uids[0])
    const list = spacers()
    expect(list.length).toBeGreaterThan(0)
    const drawn = rowUids().length
    const skipped = [...list].reduce((n, el) => n + spacerRows(el), 0)
    // Every row is accounted for: the ones drawn, the ones skipped, and the
    // six face rows under each drawn model.
    expect(skipped + drawn * ROWS_PER_BOX + 1).toBe(TOTAL_ROWS)
  })

  it('draws a different slice after a scroll', async () => {
    giveViewport(VIEW_H)
    await render()
    const first = rowUids()
    scrollTo((TOTAL_ROWS - 10) * ROW_H)
    await settle()
    const last = rowUids()
    expect(last).not.toEqual(first)
    expect(last).toContain(uids[39]!)
    expect(last).not.toContain(uids[0]!)
  })

  it('draws every row when the container reports no height', async () => {
    giveViewport(0)
    await render()
    expect(container().classList.contains('tree-virtual')).toBe(false)
    expect(rowUids()).toHaveLength(40)
    expect(spacers()).toHaveLength(0)
  })

  it('starts windowed on a remount, using the height it measured last time', async () => {
    giveViewport(VIEW_H)
    await render()
    flushSync(() => root.unmount())
    host.remove()
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    // No settle: the FIRST commit has to be the windowed one, or a tab switch
    // back to the geometry tab rebuilds 25,000 rows to throw them away again.
    flushSync(() =>
      root.render(
        <FeatureTree
          project={store.getProject()}
          selectedUid={null}
          selectedUids={[]}
          onSelect={() => {}}
        />,
      ),
    )
    expect(rowUids().length).toBeLessThan(40)
  })
})
