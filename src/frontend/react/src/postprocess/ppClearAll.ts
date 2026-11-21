/**
 * 清除全部渲染 — one button, three subtrees.
 *
 * The button lives in the RIGHT panel and could already empty the Three.js
 * group. Everything else the legacy's `ppClearAll` (STL_generate.html:24129)
 * tears down belongs to somebody else: the slice and streamline SETS and the
 * id counter are the LEFT panel's state, and the 15% dimming of the geometry is
 * the right panel's own slider. So pressing it wiped the picture and left the
 * lists behind — `Slice #1` still listed with its colorbar, nothing drawn, and
 * a new cut numbered #2 as though the first still existed.
 *
 * A module store rather than props, matching `ppViewState`: the two panels are
 * separate React subtrees under different parents, and nothing between them
 * should re-render because a teardown ran.
 *
 * Not part of `ppViewState` because it is an EVENT, not state. Everything there
 * has a current value a late subscriber can read; this has none — a subscriber
 * that arrives after the press has nothing to catch up on.
 */

type Listener = () => void

const listeners = new Set<Listener>()

/** Tear the post-processing session down. Fires synchronously. */
export function requestPpClearAll(): void {
  for (const fn of [...listeners]) fn()
}

export function subscribePpClearAll(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/** Test seam: forget every subscriber. */
export function _resetPpClearAllListeners(): void {
  listeners.clear()
}
