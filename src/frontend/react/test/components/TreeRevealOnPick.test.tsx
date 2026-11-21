import { useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createAirPipe, createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import * as groupStore from '../../src/interaction/groupStore'
import { FeatureTree } from '../../src/components/FeatureTree'
import { requestTreeReveal } from '../../src/interaction/treeReveal'

/**
 * A pick in the 3D view brings the picked model's tree row on screen.
 *
 * Measured on DAML-Testbed: the Duct_riser rows sit ~11,700 px down the tree,
 * so a viewport click on a riser lit a row nobody could see, and a folded
 * category did not even have the row. The legacy fix
 * (`revealModelInTree`, called after `handleViewportSelection`) unfolds the
 * category and group and scrolls with block 'nearest'; a click IN the tree
 * never scrolls it.
 */

let host: HTMLDivElement
let root: Root
let pipe: string
let rack: string
let boxA: string
let boxB: string
let setSelected: (uid: string | null) => void
let scrolled: { uid: string | undefined; arg: unknown }[]
const originalScroll = Element.prototype.scrollIntoView

function Harness() {
  const [uid, setUid] = useState<string | null>(null)
  setSelected = setUid
  return (
    <FeatureTree
      project={store.getProject()}
      selectedUid={uid}
      selectedUids={uid ? [uid] : []}
      onSelect={(u) => setUid(u)}
    />
  )
}

/** Lets the fold update scheduled from an effect render and commit. */
async function settle() {
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0))
}

/** What the viewport does: change the selection, then ask for the reveal. */
async function viewportPick(uid: string | null) {
  flushSync(() => {
    setSelected(uid)
    requestTreeReveal()
  })
  await settle()
}

const category = (bucket: string) => host.querySelector<HTMLElement>(`[data-category="${bucket}"]`)!
const categoryOpen = (bucket: string) => !!host.querySelector(`[data-tree-children="cat:${bucket}"]`)
const modelRow = (uid: string) => host.querySelector<HTMLElement>(`.model-root-label[data-model-id="${uid}"]`)

function caret(el: HTMLElement) {
  flushSync(() =>
    el.querySelector<HTMLElement>('.node-expander')!.dispatchEvent(new MouseEvent('click', { bubbles: true })),
  )
}

beforeEach(() => {
  localStorage.clear()
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  const add = (created: { bucket: string; model: { uid: string } }) => {
    store.addModel(created.bucket, created.model as never)
    return created.model.uid
  }
  rack = add(createModel(store.getProject() as CfdProject, 'rack'))
  boxA = add(createModel(store.getProject() as CfdProject, 'box'))
  boxB = add(createModel(store.getProject() as CfdProject, 'box'))
  pipe = add(createAirPipe(store.getProject() as CfdProject))

  scrolled = []
  Element.prototype.scrollIntoView = function (this: Element, arg?: unknown) {
    scrolled.push({ uid: (this as HTMLElement).dataset?.modelId, arg })
  } as typeof Element.prototype.scrollIntoView

  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  flushSync(() => root.render(<Harness />))
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  if (originalScroll) Element.prototype.scrollIntoView = originalScroll
  else delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView
  groupStore.resetGroupsForTest()
  localStorage.clear()
})

describe('reveal on a viewport pick', () => {
  it('opens a folded category and scrolls the picked row into view', async () => {
    caret(category('pipes'))
    expect(categoryOpen('pipes')).toBe(false)
    expect(modelRow(pipe)).toBeNull()

    await viewportPick(pipe)

    expect(categoryOpen('pipes')).toBe(true)
    expect(modelRow(pipe)!.classList.contains('selected')).toBe(true)
    expect(scrolled).toEqual([{ uid: pipe, arg: { block: 'nearest' } }])
  })

  it('scrolls a row whose category is already open, without folding anything', async () => {
    await viewportPick(rack)
    expect(scrolled).toEqual([{ uid: rack, arg: { block: 'nearest' } }])
    expect(categoryOpen('racks')).toBe(true)
    expect(categoryOpen('pipes')).toBe(true)
  })

  it('reveals on every pick, including a second pick of the model already selected', async () => {
    await viewportPick(pipe)
    await viewportPick(pipe)
    expect(scrolled.map((s) => s.uid)).toEqual([pipe, pipe])
  })

  it('opens the group folder a grouped model sits in, as well as its category', async () => {
    const gid = groupStore.createModelGroup('Pair', [boxA, boxB])
    flushSync(() => root.render(<Harness />))
    const folder = host.querySelector<HTMLElement>(`[data-group="${gid}"]`)
    expect(folder).not.toBeNull()
    caret(folder!)
    caret(category('boxes'))
    expect(categoryOpen('boxes')).toBe(false)

    await viewportPick(boxB)

    expect(categoryOpen('boxes')).toBe(true)
    expect(host.querySelector(`[data-group="${gid}"]`)!.parentElement!.querySelector('ul')).not.toBeNull()
    expect(modelRow(boxB)).not.toBeNull()
    expect(scrolled).toEqual([{ uid: boxB, arg: { block: 'nearest' } }])
  })

  it('does nothing for a pick on empty space', async () => {
    caret(category('pipes'))
    await viewportPick(null)
    expect(scrolled).toEqual([])
    expect(categoryOpen('pipes')).toBe(false)
  })
})

describe('no reveal without a viewport pick', () => {
  it('a click on a tree row does not scroll the tree', async () => {
    flushSync(() => modelRow(pipe)!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await settle()
    expect(modelRow(pipe)!.classList.contains('selected')).toBe(true)
    expect(scrolled).toEqual([])
  })

  it('a selection change from elsewhere neither unfolds nor scrolls', async () => {
    caret(category('pipes'))
    flushSync(() => setSelected(pipe))
    await settle()
    expect(categoryOpen('pipes')).toBe(false)
    expect(scrolled).toEqual([])
  })
})
