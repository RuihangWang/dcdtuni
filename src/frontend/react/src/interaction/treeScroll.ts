/**
 * Asking the feature tree to put one of its rows on screen.
 *
 * The tree is windowed: only the rows a scroll position shows are in the DOM
 * (see `treeLayout`). Two callers reach a row by DOM query rather than through
 * React — `nodeJump` clicks the sub-row a panel names, and `selectionBridge`
 * clicks the model row the shared liquid modules ask for — and both used to
 * fail silently whenever the row they wanted was below the fold.
 *
 * This is the seam that fixes it: the tree registers a scroller when it mounts,
 * a caller scrolls FIRST and then queries, and retries once the scroll has
 * committed. It returns whether the row is already in the DOM, so the caller
 * knows whether a retry is needed at all.
 */

export interface TreeScroller {
  /** Bring `uid`'s model row into the window. False when the tree has no such row. */
  toUid(uid: string): boolean
  /** Bring a sub-node's row into the window. False when the tree has no such row. */
  toKey(key: string): boolean
  /**
   * True when the row is in the DOM NOW.
   *
   * False means the scroll was only just requested and the caller has to look
   * again after React commits — a `setTimeout(0)` is enough.
   */
  readonly windowed: boolean
}

let scroller: TreeScroller | null = null

export function registerTreeScroll(next: TreeScroller | null): () => void {
  scroller = next
  return () => {
    if (scroller === next) scroller = null
  }
}

/**
 * Scroll `uid`'s row into the window.
 *
 * Returns `true` when the row is in the DOM already (or the tree is not
 * windowed at all), `false` when the caller has to come back for it.
 */
export function scrollTreeToUid(uid: string): boolean {
  if (!scroller) return true
  if (!scroller.toUid(uid)) return true
  return !scroller.windowed
}

/** The same for a sub-node key — a server, a face, a patch row. */
export function scrollTreeToKey(key: string): boolean {
  if (!scroller) return true
  if (!scroller.toKey(key)) return true
  return !scroller.windowed
}

