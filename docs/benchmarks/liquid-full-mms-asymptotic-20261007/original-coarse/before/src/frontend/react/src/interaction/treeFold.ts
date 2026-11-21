/**
 * The feature tree's fold state, and what the root row does.
 *
 * ## Folding
 *
 * Every branch keys its own entry, and a branch with no entry uses its own
 * default (the rack face rows start folded, everything else open). The ROOT
 * caret folds or opens the whole tree at once: that is a third layer under the
 * per-branch entries, not 500 writes into them — and it clears those entries,
 * because "collapse all" that leaves a branch open from before did not
 * collapse all.
 *
 * ## The root row
 *
 * A click on the root selects EVERY model. A second click, with every model
 * already selected, drops the selection — the same click cannot both select
 * all and be the only way back, so it toggles.
 */

export interface FoldState {
  /** Per-branch overrides. */
  map: Record<string, boolean>
  /** The root caret's setting: folded, open, or untouched (null). */
  all: boolean | null
}

export const OPEN_ALL: FoldState = { map: {}, all: null }

export function isFolded(state: FoldState, key: string, defaultCollapsed = false): boolean {
  if (key in state.map) return state.map[key]!
  if (state.all !== null) return state.all
  return defaultCollapsed
}

/** Fold one branch. `open` is its CURRENT state when the caller knows it. */
export function toggleFold(state: FoldState, key: string, open?: boolean): FoldState {
  const folded = open ?? !isFolded(state, key)
  return { ...state, map: { ...state.map, [key]: folded } }
}

/**
 * The branches a SHIFT-click on one caret folds together.
 *
 * Shift means "this gesture is about the whole tree, not this one row". The
 * legacy's `toggleTreeNode` (STL_generate.html:13111-13136) only honoured it
 * when a MULTI-SELECTION already covered the clicked row — several selected
 * models for a model caret, several selected CATEGORIES for a category caret —
 * which made the gesture unreachable in the two situations a user reaches for
 * it: straight after 工程根节点 selected every model but no category, and with
 * nothing selected at all. Measured: shift on a type caret folded exactly one
 * type, every time.
 *
 * So the selection narrows the batch when it can, and the tree itself is the
 * fallback:
 *
 *   - a category caret batches over the selected categories when several are
 *     lit, and over EVERY category otherwise — "fold all the types";
 *   - a model or sub-node caret batches over the selected models when the
 *     clicked one is part of a multi-selection, and over EVERY model
 *     otherwise — the same branch, `<prefix>:<uid><rest>`, on each.
 *
 * Keys are `<prefix>:<uid>[:<rest>]`, so the uid is swapped and the rest kept;
 * a batch key for a model that has no such branch is simply never read.
 * Without shift it is always the one key.
 */
export function batchFoldKeys(
  key: string,
  shift: boolean,
  selectedUids: ReadonlySet<string>,
  selectedCategories: readonly string[] = [],
  allCategories: readonly string[] = [],
  allModelUids: readonly string[] = [],
): string[] {
  if (!shift) return [key]
  const parts = key.split(':')
  const prefix = parts[0] ?? ''
  const second = parts[1] ?? ''
  const rest = parts.length > 2 ? ':' + parts.slice(2).join(':') : ''
  if (prefix === 'cat') {
    if (selectedCategories.length > 1 && selectedCategories.includes(second)) {
      return selectedCategories.map((bucket) => `cat:${bucket}`)
    }
    return allCategories.length ? allCategories.map((bucket) => `cat:${bucket}`) : [key]
  }
  if (selectedUids.size > 1 && selectedUids.has(second)) {
    return [...selectedUids].map((uid) => `${prefix}:${uid}${rest}`)
  }
  return allModelUids.length ? allModelUids.map((uid) => `${prefix}:${uid}${rest}`) : [key]
}

/** Fold several branches to ONE state — the batch is one gesture. */
export function foldKeys(state: FoldState, keys: readonly string[], folded: boolean): FoldState {
  const map = { ...state.map }
  for (const key of keys) map[key] = folded
  return { ...state, map }
}

/** The root caret: fold everything, or open everything. */
export function foldAll(state: FoldState, folded: boolean): FoldState {
  void state
  return { map: {}, all: folded }
}

/** What the root caret shows: folded once "collapse all" is in force. */
export function rootFolded(state: FoldState): boolean {
  return state.all === true
}

export type RootClick = 'selectAll' | 'deselect'

export function rootClickAction(allUids: readonly string[], selected: ReadonlySet<string>): RootClick {
  if (!allUids.length) return 'deselect'
  return allUids.every((uid) => selected.has(uid)) ? 'deselect' : 'selectAll'
}
