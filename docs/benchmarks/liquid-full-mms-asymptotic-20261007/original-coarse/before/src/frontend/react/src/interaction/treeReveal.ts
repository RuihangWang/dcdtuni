/**
 * Bring a model's tree row on screen after a pick made in the 3D view.
 *
 * The tree lit the picked row but never showed it: on DAML-Testbed the
 * Duct_riser rows sit ~11,700 px down the panel, and a folded category does not
 * render its rows at all, so a viewport pick highlighted something nobody could
 * see. The legacy fix is `revealModelInTree`, called after
 * `handleViewportSelection`: unfold the model's category (and group), then
 * `scrollIntoView({ block: 'nearest' })`, which leaves a row already in view
 * where it is.
 *
 * A request rather than a reaction to the selection: the reveal belongs to a
 * VIEWPORT pick, and only the viewport knows one happened. Keyed on the
 * selection instead, a click in the tree would scroll the tree under the
 * pointer, and a second pick of the model already selected — after the user
 * scrolled away — would not reveal it. The request carries no uid: the tree
 * reveals whatever is ACTIVE once the pick has been applied, which for a
 * ctrl-click that removed the clicked model is not the model clicked.
 */

import type { TreeCategoryRow } from './treeVisualOrder'

const listeners = new Set<() => void>()

/** Called by the viewport after every pick; the tree acts on its next render. */
export function requestTreeReveal(): void {
  for (const fn of [...listeners]) fn()
}

export function subscribeTreeReveal(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/**
 * The fold keys that have to be open for `uid`'s row to exist: its category,
 * and its group folder when it is in one. Null when the tree has no row for it.
 */
export function treeRevealKeys(categories: readonly TreeCategoryRow[], uid: string): string[] | null {
  for (const cat of categories) {
    if (cat.models.some((m) => m.uid === uid)) return [`cat:${cat.bucket}`]
    const row = cat.groups.find((g) => g.members.some((m) => m.uid === uid))
    if (row) return [`cat:${cat.bucket}`, `group:${row.group.id}`]
  }
  return null
}
