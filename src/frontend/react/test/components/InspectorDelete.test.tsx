import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CfdProject } from '../../src/schema/cfdproj'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import { createModel } from '../../src/legacy/factories'
import * as store from '../../src/projectStore'
import { Inspector } from '../../src/components/Inspector'
import { t } from '../../src/i18n'

/**
 * Every model panel ends with the red delete button.
 *
 * The legacy puts one at the bottom of each: the sheet panel
 * (STL_generate.html:8453), the partition wall (:8987), the generic box and
 * imported STL (:9035), the ACU (:8760) and the rack (:9304). React's inspector
 * ended at the rotation group, so deleting the thing you were looking at meant
 * finding its row in a tree of 177 or knowing the Delete key — and the panel is
 * where you are when you decide a model was a mistake.
 *
 * It deletes ONE model, with no confirmation, exactly as the legacy's
 * `removeModel(null, uid)` does: `evt` is null there, so the batch branch and
 * its confirm never run. The batch delete lives on the multi-select panel,
 * which is what replaces this one when more than one model is selected.
 */

let host: HTMLDivElement
let root: Root
let cleared: number

function add(kind: string): string {
  const { bucket, model } = createModel(store.getProject() as CfdProject, kind)
  store.addModel(bucket, model)
  return model.uid
}

function render(uid: string | null) {
  flushSync(() =>
    root.render(
      <Inspector
        project={store.getProject()}
        selectedUid={uid}
        clearSelection={() => {
          cleared += 1
        }}
      />,
    ),
  )
}

const button = () => document.getElementById('btn-remove-model') as HTMLButtonElement | null

function exists(uid: string): boolean {
  return !!store.findConstruction(store.getProject() as CfdProject, uid)
}

beforeEach(() => {
  store.loadProject(
    stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject,
    null,
  )
  cleared = 0
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
})

describe('the model inspector', () => {
  it('ends with a delete button', () => {
    render(add('baffle'))
    expect(button()).not.toBeNull()
    expect(button()!.className).toContain('danger')
  })

  it('names what it deletes — rack, ACU, or just the model', () => {
    render(add('rack'))
    expect(button()!.textContent).toBe(t('btn.remove_rack'))
    render(add('acu'))
    expect(button()!.textContent).toBe(t('btn.remove_acu'))
    render(add('baffle'))
    expect(button()!.textContent).toBe(t('btn.remove_model'))
  })

  it('removes the model and asks the host to drop the selection', () => {
    const uid = add('baffle')
    render(uid)
    flushSync(() => button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(exists(uid)).toBe(false)
    // Otherwise the panel keeps rendering a model that is no longer in the
    // project, and the next edit writes into a draft that drops it.
    expect(cleared).toBe(1)
  })

  it('is one undo step', () => {
    const uid = add('baffle')
    render(uid)
    const depth = store.undoDepth()
    flushSync(() => button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(store.undoDepth()).toBe(depth + 1)
    store.undo()
    expect(exists(uid)).toBe(true)
  })

  it('does not ask first — that is the batch panel’s job', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const uid = add('baffle')
    render(uid)
    flushSync(() => button()!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(confirm).not.toHaveBeenCalled()
    expect(exists(uid)).toBe(false)
    confirm.mockRestore()
  })

  it('is not there when nothing is selected', () => {
    render(null)
    expect(button()).toBeNull()
  })
})
