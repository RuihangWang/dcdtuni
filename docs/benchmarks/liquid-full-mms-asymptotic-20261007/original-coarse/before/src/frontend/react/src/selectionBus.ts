/**
 * The app's current selection, readable from a panel it was never handed to.
 *
 * The legacy liquid toolbar reads `getSelectedModel()` and `uiState.selection`
 * as page globals (STL_generate.html:12358-12362). This app keeps both in App
 * state and passes them down, and the creation toolbar is the one consumer
 * outside that path: it is rendered above the inspector and receives only the
 * project, so its contextual buttons had nothing to key off.
 *
 * A module-level channel rather than two more props threaded through every
 * panel in between, following `viewportBus`. It is also what the parity harness
 * drives, under `window.__selectionBus`, so a contextual button can be
 * exercised from a browser eval exactly as a click would exercise it.
 */

import { useEffect, useState } from 'react'
import type { TreeSelection } from './interaction/treeNodes'
import { requestSelect as selectThroughTree } from './interaction/selectionBridge'

export interface AppSelection {
  /** The selected MODEL, or null. */
  uid: string | null
  /** The narrower selection under it — a face, a patch, a server — or null. */
  node: TreeSelection | null
}

const EMPTY: AppSelection = { uid: null, node: null }

let current: AppSelection = EMPTY
const listeners = new Set<() => void>()
let selectHandler: ((uid: string) => void) | null = null

/** Called by the app whenever its selection changes. */
export function publishSelection(next: AppSelection) {
  if (next.uid === current.uid && next.node === current.node) return
  current = next
  listeners.forEach((fn) => fn())
}

export function getSelection(): AppSelection {
  return current
}

export function subscribeSelection(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** Re-render on selection change. Returns the live selection. */
export function useAppSelection(): AppSelection {
  const [, bump] = useState(0)
  useEffect(() => subscribeSelection(() => bump((n) => n + 1)), [])
  return current
}

/** Register the app's own `select`, so a panel can select a model it lists. */
export function setSelectHandler(fn: ((uid: string) => void) | null) {
  selectHandler = fn
}

/**
 * Select a model from a panel that lists it — the legacy `selectModelInScene`.
 *
 * Falls back to clicking the model's own feature-tree row while no handler is
 * registered. That row carries `data-model-id` and its click handler IS the
 * app's `select`, so the fallback goes through the same code path a user click
 * would, instead of leaving the row inert.
 */
export function requestSelect(uid: string) {
  if (selectHandler) {
    selectHandler(uid)
    return
  }
  // Through the tree row, which the tree windows: a model whose row is
  // scrolled out of the virtual list has no `[data-model-id]` element at all,
  // so querying the document silently selected nothing — the automation
  // contract's `select('m_26')` on Tri_angle's floor did exactly that
  // (2026-09-23). The bridge scrolls the row in first.
  selectThroughTree(uid)
}

if (typeof window !== 'undefined') {
  ;(window as unknown as { __selectionBus?: unknown }).__selectionBus = {
    publish: publishSelection,
    get: getSelection,
    requestSelect,
  }
}
