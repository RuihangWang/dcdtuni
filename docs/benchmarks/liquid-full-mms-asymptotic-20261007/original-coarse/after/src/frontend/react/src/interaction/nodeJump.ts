import { scrollTreeToKey } from './treeScroll'

/**
 * Selecting a tree node from a panel that names it.
 *
 * The legacy panels call `selectRackEquipment(uid, id)` and the inspector
 * follows, because the selection is a global there. In this port the narrower
 * selection is React state owned by `App`, handed DOWN to the panels — so a
 * panel has no way to set it, and a "open the server in this slot" button had
 * nothing to call.
 *
 * The route is the tree itself, the same one `selectionBridge` uses for models:
 * every sub-row carries its node key as `data-node`, and its click handler is
 * the app's real `onSelectNode`. Clicking that row therefore IS selection
 * rather than an imitation of it, and it needs no wiring in a file this port
 * splits across many owners.
 *
 * Reaches only as far as the tree is rendered — the geometry tab, with the
 * model's branch expanded — which is where these buttons are shown anyway. The
 * caller is told when the row was not found so it can fall back to selecting
 * the model.
 */

/**
 * Escape a node key for an attribute selector.
 *
 * `CSS.escape` where the platform has it — jsdom does not ship it, and a
 * panel-driven selection there must not throw inside a click handler just
 * because the tree is absent.
 */
function escapeForSelector(key: string): string {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') return CSS.escape(key)
  return key.replace(/["\\]/g, '\\$&')
}

/** Click the feature-tree row for `key`. Returns whether a row was found. */
export function requestSelectNode(key: string): boolean {
  if (typeof document === 'undefined') return false
  const find = () =>
    document.querySelector<HTMLElement>(`.node-label[data-node="${escapeForSelector(key)}"]`)
  const row = find()
  if (row) {
    row.click()
    return true
  }
  // The tree only keeps its visible rows in the DOM (`treeScroll`), so a row
  // below the fold has to be scrolled in first — and then clicked once the
  // scroll has committed, which is a macrotask away.
  if (!scrollTreeToKey(key)) return false
  setTimeout(() => find()?.click(), 0)
  return true
}
