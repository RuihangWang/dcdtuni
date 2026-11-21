/**
 * Drag-reorder in the feature tree.
 *
 * A port of `window.reorderModel` (STL_generate.html:1567). The V4 buckets are
 * uid-keyed dictionaries, so "order" is the KEY ORDER of one bucket — which is
 * why a reorder rebuilds the dictionary instead of moving an element in an
 * array, and why a drag can only reorder within a bucket: a rack cannot be
 * given a position among the ACUs when it is not in that dictionary at all.
 *
 * The order matters beyond the panel. `allModels` walks the buckets in key
 * order, and that is the order the STL export writes its solids in, so a drop
 * here changes the exported file — unlike the tree's name sort, which is a view
 * setting and never touches the project.
 */

import { type CfdProject, type Construction } from '../schema/cfdproj'
import * as store from '../projectStore'

export type DropPosition = 'before' | 'after'

/**
 * The bucket's keys with `sourceUid` moved next to `targetUid`.
 *
 * Returns null when the move is impossible or would change nothing, so the
 * caller can skip the undo checkpoint entirely — the legacy's `return false`.
 */
export function reorderKeys(
  keys: string[],
  sourceUid: string,
  targetUid: string,
  position: DropPosition,
): string[] | null {
  if (sourceUid === targetUid) return null
  if (!keys.includes(sourceUid) || !keys.includes(targetUid)) return null
  const order = keys.filter((k) => k !== sourceUid)
  let index = order.indexOf(targetUid)
  if (position === 'after') index += 1
  order.splice(index, 0, sourceUid)
  return order
}

/** Which bucket holds `uid`, or null. */
export function bucketOf(project: CfdProject, uid: string): string | null {
  return store.findConstruction(project, uid)?.bucket ?? null
}

/**
 * Apply a drop to the project. True when the order actually changed.
 *
 * Feasibility is checked against the live project BEFORE `edit` is called: an
 * impossible drop — a rack onto an ACU, a node onto itself — must not leave an
 * undo entry that restores the state it is already in.
 */
export function reorderModel(
  sourceUid: string,
  targetUid: string,
  position: DropPosition,
): boolean {
  const project = store.getProject()
  if (!project) return false
  const bucket = bucketOf(project, sourceUid)
  if (!bucket || bucket !== bucketOf(project, targetUid)) return false
  const dict = project.constructions?.[bucket]
  if (!dict) return false
  const next = reorderKeys(Object.keys(dict), sourceUid, targetUid, position)
  if (!next) return false

  store.edit('dropModelTree', `${sourceUid} ${position} ${targetUid}`, (draft) => {
    const target = draft.constructions?.[bucket]
    if (!target || !draft.constructions) return
    const rebuilt: Record<string, Construction> = {}
    for (const key of next) {
      const model = target[key]
      if (model) rebuilt[key] = model
    }
    draft.constructions[bucket] = rebuilt
  })
  return true
}
