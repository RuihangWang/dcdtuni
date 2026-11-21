/**
 * The rows the geometry tree draws, in the order it draws them — and the
 * shift-click range that reads off that order.
 *
 * Two things are one thing here on purpose. The legacy keeps them apart:
 * `_generateCategoryChildrenHtml` (STL_generate.html:12589) renders the rows
 * through `sortModelsForTree`, while `getVisualModelOrder` (12791) rebuilds
 * what it calls the same order WITHOUT the sort — so under the default
 * `name-asc` a shift-range there selects the models between two INSERTION-order
 * positions, which are not the rows the user sees between the two they clicked.
 * Deriving the range from the rendered rows is the one way that cannot drift,
 * so `buildTreeCategories` is the single source and `visualModelOrder` flattens
 * its result. Where the sort is `manual` the two agree exactly.
 *
 * A group's rows come FIRST inside its category and the ungrouped rows follow,
 * because that is how the tree stacks them; a range therefore crosses group and
 * category boundaries the same way the legacy's does.
 */

import {
  allModels,
  type Bucket,
  CATEGORIES,
  type CfdProject,
  type Construction,
  modelsIn,
  type SortMode,
  sortModelsForTree,
} from '../schema/cfdproj'
import type { TranslationKey } from '../i18n'
import { groupedIds, type ModelGroup, sortGroupsByName } from '../geometry/modelGroups'

/** A group folder with its members already resolved and sorted. */
export interface TreeGroupRow {
  group: ModelGroup
  members: Construction[]
}

export interface TreeCategoryRow {
  bucket: Bucket
  icon: string
  labelKey: TranslationKey
  /** Every model in the category, grouped or not — what the badge shows. */
  count: number
  groups: TreeGroupRow[]
  /** The rows drawn directly under the category: the ungrouped ones. */
  models: Construction[]
}

/**
 * The tree's rows, category by category.
 *
 * Categories with nothing in them are dropped, as the legacy drops them — a
 * range must not step through a category that is not on screen.
 */
export function buildTreeCategories(
  project: CfdProject | null,
  groups: readonly ModelGroup[],
  sortMode: SortMode,
): TreeCategoryRow[] {
  if (!project) return []
  const bucketByUid = new Map<string, Bucket>()
  const modelByUid = new Map<string, Construction>()
  for (const { bucket, model } of allModels(project)) {
    bucketByUid.set(model.uid, bucket)
    modelByUid.set(model.uid, model)
  }
  const inGroup = groupedIds(groups as ModelGroup[])
  const out: TreeCategoryRow[] = []
  for (const cat of CATEGORIES) {
    const models = sortModelsForTree(modelsIn(project, cat.bucket), sortMode)
    if (!models.length) continue
    // A group belongs to the category of its FIRST member, as in the legacy
    // (STL_generate.html:12592). A group cannot span two kinds — it is created
    // from one selection of one kind — so the first member decides.
    const own = (groups as ModelGroup[]).filter(
      (g) => bucketByUid.get(g.memberIds[0] ?? '') === cat.bucket,
    )
    const ordered = sortMode === 'manual' ? own : sortGroupsByName(own, sortMode === 'name-desc')
    const groupRows: TreeGroupRow[] = []
    for (const group of ordered) {
      const members = sortModelsForTree(
        group.memberIds
          .map((uid) => modelByUid.get(uid))
          .filter((m): m is Construction => !!m),
        sortMode,
      )
      // A folder whose models were all deleted draws nothing, so it must not
      // occupy a position a range would step through either.
      if (members.length) groupRows.push({ group, members })
    }
    out.push({
      bucket: cat.bucket,
      icon: cat.icon,
      labelKey: cat.labelKey,
      count: models.length,
      groups: groupRows,
      // Grouped rows are drawn inside their folder, not twice.
      models: models.filter((m) => !inGroup.has(m.uid)),
    })
  }
  return out
}

/** Every model row's uid, top to bottom, exactly as `buildTreeCategories` stacks them. */
export function visualModelOrder(categories: readonly TreeCategoryRow[]): string[] {
  const out: string[] = []
  for (const cat of categories) {
    for (const row of cat.groups) for (const model of row.members) out.push(model.uid)
    for (const model of cat.models) out.push(model.uid)
  }
  return out
}

/**
 * The rows a shift-click selects: everything between the anchor and the click.
 *
 * The anchor is the last row clicked WITHOUT a modifier, and it does NOT move —
 * that is what lets one shift-drag over the tree grow the range and then shrink
 * it again instead of walking it downwards. With no anchor to extend from the
 * click is an ordinary one.
 *
 * Returned in display order; a caller that needs the clicked row to end up
 * ACTIVE has to apply it last.
 */
export function rangeSelect(
  order: readonly string[],
  anchor: string | null,
  clicked: string,
): string[] {
  const clickedIndex = order.indexOf(clicked)
  if (clickedIndex < 0) return []
  const anchorIndex = anchor ? order.indexOf(anchor) : -1
  if (anchorIndex < 0) return [clicked]
  const start = Math.min(anchorIndex, clickedIndex)
  const end = Math.max(anchorIndex, clickedIndex)
  return order.slice(start, end + 1)
}

/**
 * Where the anchor sits after a click.
 *
 * A plain click moves it to the row clicked. A modifier click keeps it, falling
 * back to whatever the inspector was following — the legacy's
 * `uiState.selection.anchorModelId || uiState.selection.modelId || modelId`
 * (STL_generate.html:12822), which is what makes a shift-click work right after
 * a VIEWPORT selection the tree never saw.
 */
export function nextAnchor(
  anchor: string | null,
  active: string | null,
  clicked: string,
  modifier: boolean,
): string {
  if (!modifier) return clicked
  return anchor ?? active ?? clicked
}
