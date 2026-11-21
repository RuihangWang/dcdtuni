/**
 * Reaching the app's model selection from code that is not a React component.
 *
 * The legacy editor keeps the selection in a global `uiState.selection` and the
 * shared liquid modules read and write it through the bare globals
 * `getSelectedModel()` and `setSelection('model', uid)` — nine call sites
 * between `js/liquid_mode.js` and `js/liquid_pipe_renderer.js`. In this port
 * the selection is React state owned by `App`, so those names resolve to
 * nothing and the module silently skips the branch: splitting a pipe leaves the
 * new T-branch unselected, and `refreshJunctionGizmo` reads a null selection and
 * therefore DETACHES the rotate gizmo on every pipe rebuild, which reads as "the
 * junction gizmo does not work".
 *
 * Two ways in, in priority order:
 *
 *   1. a bridge registered by the component that owns the selection, and
 *   2. the tree itself — the model row carries the legacy's own
 *      `data-model-id`, and clicking it is the app's real selection path, so a
 *      click on that row is not a simulation of selection, it IS selection.
 *
 * The DOM route is what makes this work with no wiring at all. It only reaches
 * as far as the tree is rendered, which is the geometry tab — and the geometry
 * tab is where pipes are edited, so the paths that need it are covered.
 */

import { scrollTreeToUid } from './treeScroll'

type GetUid = () => string | null
type SetUid = (uid: string | null) => void

let registeredGet: GetUid | null = null
let registeredSet: SetUid | null = null

/**
 * Hand the bridge a real selection accessor.
 *
 * Returns the un-register function, so a component can call it from an effect
 * cleanup and never leave a closure over a stale setState behind.
 */
export function registerSelectionBridge(get: GetUid, set: SetUid): () => void {
  registeredGet = get
  registeredSet = set
  return () => {
    if (registeredGet === get) registeredGet = null
    if (registeredSet === set) registeredSet = null
  }
}

/** The tree row for `uid`, or null when the tree is not on screen. */
export function treeRowFor(uid: string): HTMLElement | null {
  if (typeof document === 'undefined') return null
  return document.querySelector<HTMLElement>(
    `.model-root-label[data-model-id="${CSS.escape(uid)}"]`,
  )
}

/** The uid of the selected model, or null. */
export function currentSelectedUid(): string | null {
  if (registeredGet) return registeredGet()
  if (typeof document === 'undefined') return null
  const row = document.querySelector<HTMLElement>('.model-root-label.selected[data-model-id]')
  return row?.dataset.modelId ?? null
}

/** Select `uid`. Returns whether a selection path was found. */
export function requestSelect(uid: string | null): boolean {
  if (registeredSet) {
    registeredSet(uid)
    return true
  }
  if (!uid) return false
  const row = treeRowFor(uid)
  if (row) {
    row.click()
    return true
  }
  // A windowed tree holds only the rows on screen (`treeScroll`): scroll the
  // model's row in, then click it once the scroll has committed.
  if (!scrollTreeToUid(uid)) return false
  setTimeout(() => treeRowFor(uid)?.click(), 0)
  return true
}
