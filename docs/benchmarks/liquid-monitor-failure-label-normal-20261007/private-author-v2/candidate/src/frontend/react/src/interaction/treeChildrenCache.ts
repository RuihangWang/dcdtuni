/**
 * A cache for the feature tree's per-model sub-hierarchy.
 *
 * `modelChildren` walks one model's faces, servers, slots and patches and
 * allocates a `TreeChild` per row. On `case/Alibaba-hongchou` (200 racks, 1,979
 * servers, 8,600 partitions) one pass over the project allocates ~25,000 of
 * them — and the tree needs that pass on EVERY render, because the row layout
 * has to know how tall each model's branch is before it can decide which rows
 * are on screen. Rebuilding it per render measured in the hundreds of
 * milliseconds; the answer almost never changes.
 *
 * ## Why the key is (model, revision, wall regions) and not just the model
 *
 * A live edit mutates the project IN PLACE and keeps the same object identity
 * (see `projectStore.stateRevision`), so the model alone cannot tell a stale
 * entry from a fresh one. `revision()` bumps on every store notification, which
 * is exactly the signal the tree already re-renders on.
 *
 * The wall regions are part of the key for the same reason from the other side:
 * they come from the BUILT scene rather than the project, so a rebuild that
 * splits a wall into more regions has to invalidate the row that lists them.
 *
 * And the LOCALE: the rows are translated (a rack's sides read "进风面 (Inlet)"
 * or "Inlet"), and a language switch bumps neither of the other two — the rows
 * stayed in the old language until the project was reloaded.
 *
 * A `WeakMap`, so a project that is closed takes its entries with it — there is
 * nothing to clear and nothing to leak when a new .cfdproj is loaded.
 */

import type { Construction } from '../schema/cfdproj'
import { getLocale } from '../i18n'
import { modelChildren, type TreeChild } from './treeNodes'

interface Entry {
  revision: number
  regions: string
  locale: string
  children: TreeChild[]
}

const cache = new WeakMap<Construction, Entry>()

/**
 * The model's tree children, rebuilt only when something that feeds them moved.
 *
 * The returned array is SHARED: callers must treat it as read-only, which the
 * tree does — it renders rows from it and never writes back.
 */
export function cachedModelChildren(
  model: Construction,
  wallRegions: readonly string[],
  revision: number,
): TreeChild[] {
  const regions = wallRegions.join('\u0000')
  const locale = getLocale()
  const hit = cache.get(model)
  if (hit && hit.revision === revision && hit.regions === regions && hit.locale === locale) {
    return hit.children
  }
  const children = modelChildren(model, { wallRegions })
  cache.set(model, { revision, regions, locale, children })
  return children
}

/** Test seam: how many models are cached right now. */
export function cachedModelCount(probe: readonly Construction[]): number {
  let n = 0
  for (const model of probe) if (cache.has(model)) n += 1
  return n
}
