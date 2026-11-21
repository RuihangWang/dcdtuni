/**
 * Clicking a CATEGORY row selects everything in it.
 *
 * A port of `window.selectCategory` (STL_generate.html:13167). A category node
 * is the only place a "select all the racks" gesture exists — without it a
 * batch operation on 142 racks is 142 ctrl-clicks.
 *
 * Shift extends the selection across ADJACENT CATEGORIES, from the anchor
 * category to the clicked one, and the anchor is the last category clicked
 * WITHOUT shift. That is what makes 🗄️ then shift-❄️ select every rack and
 * every ACU, and it is why the anchor has to survive between clicks.
 */

import { allModels, type CfdProject } from '../schema/cfdproj'

export interface CategoryClick {
  /** The highlighted category rows. */
  categories: string[]
  /** The anchor a later shift-click extends from. */
  anchor: string
}

/**
 * Which categories a click selects.
 *
 * `present` is the categories that have models, in tree order — a category with
 * nothing in it is not drawn, so a range across it must not include it.
 */
export function categoryClick(
  present: string[],
  anchor: string | null,
  clicked: string,
  shift: boolean,
): CategoryClick {
  const clickedIndex = present.indexOf(clicked)
  if (shift && anchor) {
    const anchorIndex = present.indexOf(anchor)
    if (anchorIndex >= 0 && clickedIndex >= 0) {
      const start = Math.min(anchorIndex, clickedIndex)
      const end = Math.max(anchorIndex, clickedIndex)
      // The anchor stays put: dragging a shift-selection back and forth has to
      // grow and shrink the same range, not walk it across the tree.
      return { categories: present.slice(start, end + 1), anchor }
    }
  }
  return { categories: [clicked], anchor: clicked }
}

/**
 * Every model in those categories, in PROJECT order.
 *
 * Project order, not tree order: the selection drives batch operations, and the
 * legacy's `setSelection(... allModelIds ...)` passes the models in the order
 * `allModels()` returns them whatever the tree is currently sorted by.
 */
export function modelsInCategories(project: CfdProject | null, buckets: string[]): string[] {
  if (!project) return []
  const wanted = new Set(buckets)
  return allModels(project)
    .filter((m) => wanted.has(m.bucket))
    .map((m) => m.model.uid)
}
