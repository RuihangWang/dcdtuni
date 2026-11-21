/**
 * Multi-model selection.
 *
 * Ctrl (or Cmd) click adds a model to the selection instead of replacing it,
 * which is what makes "move these eight racks together" possible at all. The
 * rules are the legacy viewport's (STL_generate.html:17764), and two of them
 * are easy to get wrong:
 *
 * - **Order follows the PROJECT, not the click order.** The selection drives
 *   batch operations — duplicate, delete, Bool Diff — and those must produce the
 *   same result whichever order the user happened to click in.
 * - **A modifier-click with a single model already selected PROMOTES it**, so
 *   the first ctrl-click on a second model gives two selected, not one.
 *
 * The ACTIVE model — the one the inspector shows — is the one just clicked
 * whenever it is still in the selection, and the last remaining one otherwise.
 */

export interface SelectionChange {
  uids: string[]
  /** The model the inspector follows, or null when nothing is selected. */
  active: string | null
}

/**
 * Apply a click to the current selection.
 *
 * `order` is the project's model order; anything not in it is dropped, which is
 * how a deleted model leaves the selection without a separate sweep.
 */
export function applyClick(
  current: string[],
  active: string | null,
  clicked: string | null,
  toggle: boolean,
  order: string[],
): SelectionChange {
  if (!clicked) return { uids: [], active: null }
  if (!toggle) return { uids: [clicked], active: clicked }

  const known = new Set(order)
  const set = new Set(current.filter((uid) => known.has(uid)))
  if (set.size === 0 && active && active !== clicked && known.has(active)) {
    // Promote the single selection rather than dropping it: ctrl-clicking a
    // second model means "both", not "only the new one".
    set.add(active)
  }
  if (set.has(clicked)) set.delete(clicked)
  else set.add(clicked)

  const uids = order.filter((uid) => set.has(uid))
  if (!uids.length) return { uids: [], active: null }
  return {
    uids,
    active: uids.includes(clicked) ? clicked : (uids[uids.length - 1] ?? null),
  }
}

/**
 * Apply a MULTI-model click in one step.
 *
 * The tree's root, category, group and shift-range gestures each select a set.
 * Expressed through `applyClick` — the only thing the host's single-model
 * callback offers — a set of N becomes one plain click plus N-1 additive ones:
 * on `case/Alibaba-hongchou` the root row's "select all" was 287 store updates,
 * each re-deriving the project's model order, an O(n^2) warm-up in front of a
 * render that already had 287 rows to repaint.
 *
 * The result is defined to be IDENTICAL to that fold of `applyClick` — same uids
 * in the same project order, same active model — and the test pins that rather
 * than trusting the two to agree by construction. One deliberate exception: a
 * uid the project no longer has is dropped, where a single `applyClick` takes
 * the click on trust. A set can name a model deleted between the gesture and
 * the update; a single click cannot, because it comes from a row on screen.
 *
 * `makeActive` is the row the gesture means the inspector to follow — the one a
 * range or a group click ended on. It is applied last, exactly as the fold
 * applies its last click last.
 */
export function applyMany(
  current: string[],
  active: string | null,
  clicked: readonly string[],
  additive: boolean,
  order: string[],
  makeActive?: string | null,
): SelectionChange {
  const known = new Set(order)
  const ordered =
    makeActive && clicked.includes(makeActive)
      ? [...clicked.filter((uid) => uid !== makeActive), makeActive]
      : [...clicked]

  const set = new Set<string>()
  const starting = new Set<string>()
  if (additive) {
    for (const uid of current) if (known.has(uid)) starting.add(uid)
    // `applyClick` promotes a lone active model on the first modifier-click, so
    // an additive gesture that finds an empty `current` still keeps it.
    if (starting.size === 0 && active && active !== ordered[0] && known.has(active)) {
      starting.add(active)
    }
    for (const uid of starting) set.add(uid)
  }

  let lastAdded: string | null = null
  for (const uid of ordered) {
    if (!known.has(uid)) continue
    if (additive && starting.has(uid)) continue
    set.add(uid)
    lastAdded = uid
  }

  const uids = order.filter((uid) => set.has(uid))
  if (!uids.length) return { uids: [], active: null }
  if (additive) {
    // Nothing was added: the gesture changes no selection at all.
    if (!lastAdded) {
      return active && set.has(active) ? { uids, active } : { uids, active: uids[uids.length - 1]! }
    }
    return { uids, active: lastAdded }
  }
  return { uids, active: lastAdded ?? uids[uids.length - 1]! }
}

/** The minimum corner of a set of world bounding boxes. */
export function minPoint(
  bounds: ({ min: { x: number; y: number; z: number } } | null | undefined)[],
): { x: number; y: number; z: number } {
  let x = Infinity
  let y = Infinity
  let z = Infinity
  for (const b of bounds) {
    if (!b) continue
    if (b.min.x < x) x = b.min.x
    if (b.min.y < y) y = b.min.y
    if (b.min.z < z) z = b.min.z
  }
  return {
    x: Number.isFinite(x) ? x : 0,
    y: Number.isFinite(y) ? y : 0,
    z: Number.isFinite(z) ? z : 0,
  }
}
