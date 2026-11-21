import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { FeatureTree } from '../../src/components/FeatureTree'

/**
 * The tree follows an edit that mutated the project IN PLACE.
 *
 * `store.edit` swaps in a new project object, so the tree's
 * `useMemo(..., [project])` recomputes. `mutateLive` + `notifyLiveEdit` does
 * not — and that pair is how the shared liquid modules add a model:
 * `window.addModel()` is a `mutateLive`, and `window.updateUI()` is a
 * `notifyLiveEdit`. Splitting a pipe therefore created a T-branch the tree did
 * not show until the next real edit, and `setRefinementZoneVisible` already
 * works around the same thing by hand.
 *
 * ## Which of these actually guards the fix
 *
 * The root-count case does: revert the `revision` dependency and it goes red.
 * The rows case does NOT — React discards the `categories` memo between these
 * renders under jsdom, so the rows come back fresh either way. It is kept as a
 * statement of the intended behaviour, not as a guard, and the browser is what
 * proved the rows: before the fix, `window.addModel()` + `window.updateUI()`
 * left the tree at 「工程根节点 · 0」 with no rows; after it, the T-branch row and
 * the count 1 both appear.
 */

let host: HTMLDivElement
let root: Root

/** What `window.addModel` reaches for (legacy/liquidGlobals.ts:45). */
function addConstruction(project: CfdProject, model: unknown): void {
  ;(stateV4() as unknown as {
    addConstruction(state: unknown, model: unknown): void
  }).addConstruction(project, model)
}

function render() {
  flushSync(() =>
    root.render(
      <FeatureTree project={store.getProject()} selectedUid={null} onSelect={() => {}} />,
    ),
  )
}

function rowIds(): string[] {
  return [...host.querySelectorAll<HTMLElement>('[data-model-id]')].map(
    (el) => el.dataset.modelId ?? '',
  )
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  render()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('FeatureTree after a live edit', () => {
  it('shows a model added with mutateLive once notifyLiveEdit fires', () => {
    expect(rowIds()).toEqual([])

    // Exactly what `window.addModel` does (legacy/liquidGlobals.ts:276).
    const { model } = createModel(store.getProject() as CfdProject, 'baffle')
    store.mutateLive((p) => addConstruction(p, model))
    store.notifyLiveEdit()
    render()

    expect(rowIds()).toEqual([model.uid])
  })

  it('updates the root row’s model count too', () => {
    const root0 = host.querySelector('.node-label')!.textContent ?? ''
    const { model } = createModel(store.getProject() as CfdProject, 'baffle')
    store.mutateLive((p) => addConstruction(p, model))
    store.notifyLiveEdit()
    render()

    // The count is its own `useMemo`, so it goes stale independently of the
    // rows — a tree that lists the model but still says 0 is the half-fixed
    // version of this bug.
    expect(host.querySelector('.node-label')!.textContent).not.toBe(root0)
  })

  it('still follows an ordinary store.edit', () => {
    const { bucket, model } = createModel(store.getProject() as CfdProject, 'baffle')
    store.addModel(bucket, model)
    render()
    expect(rowIds()).toContain(model.uid)
  })
})
