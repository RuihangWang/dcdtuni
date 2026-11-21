/**
 * The feature tree's row geometry.
 *
 * ## Why this exists
 *
 * The tree is not a list of models, it is a list of ROWS: on
 * `case/Alibaba-hongchou` 287 models open into 25,489 rows and 121,825 DOM
 * nodes, and rendering all of them made a single click in the 3D view — which
 * selects one model and scrolls its row into view — take seconds. Nothing about
 * that render was wasted work in the abstract; it was wasted because 99.8% of
 * those rows were below the fold and nobody could see them.
 *
 * So the tree is laid out as a flat row space FIRST, in pure data: every entry
 * gets the index of its own row and the number of rows its whole branch spans.
 * The renderer then draws only the rows a scroll position puts on screen, and
 * stands a spacer of the right pixel height in for the ones it skips. The
 * nested `<ul>`/`<li>` markup, the indentation and every `data-*` hook stay
 * exactly as they were — a row that is off screen is simply not in the DOM yet,
 * which is what a browser's own `content-visibility` would do if it could also
 * skip building the element.
 *
 * ## Fixed pitch
 *
 * A spacer can only stand in for rows it cannot see if every row is the same
 * height, so the virtualised tree pins one (`--tree-row-h`, read back from the
 * stylesheet rather than duplicated here). `styles.css` zeroes the two margins
 * that used to make the pitch uneven — `li.tree-node`'s 2px and the nested
 * `ul`'s 3px — and gives `.node-label` a fixed height instead of letting the
 * 22px action buttons stretch some rows to 36px and leave others at 32px.
 *
 * ## When it does not apply
 *
 * `rowWindow` returns null for a container that reports no height: a jsdom
 * test, or the left panel collapsed to `display: none`. A null window means
 * "draw every row", which is the pre-windowing behaviour, so nothing that
 * cannot be measured silently draws nothing.
 */

import type { Construction } from '../schema/cfdproj'
import type { ModelGroup } from '../geometry/modelGroups'
import { isFolded, type FoldState } from './treeFold'
import type { TreeChild } from './treeNodes'
import type { TreeCategoryRow, TreeGroupRow } from './treeVisualOrder'

/** A sub-node row, with its place in the flat row space. */
export interface LayoutChild {
  node: TreeChild
  /** Index of this row. */
  row: number
  /** Rows this branch spans: its own row plus its open descendants. */
  rows: number
  /** Whether its children are drawn — false for a leaf, folded or not. */
  open: boolean
  children: LayoutChild[]
}

export interface LayoutModel {
  kind: 'model'
  model: Construction
  /** Its category's icon — the row draws the category's, not the kind's. */
  icon: string
  /** The model's sub-hierarchy, from the caller's cache. */
  children: TreeChild[]
  grouped: boolean
  row: number
  rows: number
  open: boolean
  subtree: LayoutChild[]
}

export interface LayoutGroup {
  kind: 'group'
  group: ModelGroup
  row: number
  rows: number
  open: boolean
  members: LayoutModel[]
}

export type LayoutEntry = LayoutModel | LayoutGroup

export interface LayoutCategory {
  cat: TreeCategoryRow
  row: number
  rows: number
  open: boolean
  /** In draw order: group folders first, then the ungrouped models. */
  entries: LayoutEntry[]
}

export interface TreeLayout {
  categories: LayoutCategory[]
  /** Rows in the whole tree, which is what the scroll height has to cover. */
  totalRows: number
  /** Row index of each model's own row, for reveal-on-pick. */
  modelRow: Map<string, number>
}

export interface LayoutSource {
  /** The model's cached sub-hierarchy. */
  childrenOf(model: Construction): TreeChild[]
  /** Whether the model sits in a group folder — it draws an ⏏ when it does. */
  isGrouped(uid: string): boolean
}

const MODEL_KEY = (uid: string) => `model:${uid}`
const GROUP_KEY = (id: string) => `group:${id}`
const CAT_KEY = (bucket: string) => `cat:${bucket}`

/**
 * Walk the tree in draw order, numbering every row.
 *
 * One pass, no allocation beyond the layout itself: collapsed branches are not
 * descended into, so a tree the user has folded costs nothing to lay out.
 */
export function buildTreeLayout(
  categories: readonly TreeCategoryRow[],
  fold: FoldState,
  source: LayoutSource,
): TreeLayout {
  const modelRow = new Map<string, number>()
  let cursor = 0

  const layoutChildren = (children: readonly TreeChild[]): LayoutChild[] => {
    const out: LayoutChild[] = []
    for (const node of children) {
      const start = cursor
      const kids = node.children ?? []
      const open = kids.length > 0 && !isFolded(fold, node.key, node.defaultCollapsed)
      cursor += 1
      const entry: LayoutChild = {
        node,
        row: start,
        rows: 0,
        open,
        children: [],
      }
      if (open) entry.children = layoutChildren(kids)
      entry.rows = cursor - start
      out.push(entry)
    }
    return out
  }

  const layoutModel = (model: Construction, grouped: boolean, icon: string): LayoutModel => {
    const start = cursor
    const children = source.childrenOf(model)
    const open = children.length > 0 && !isFolded(fold, MODEL_KEY(model.uid))
    cursor += 1
    modelRow.set(model.uid, start)
    const entry: LayoutModel = {
      kind: 'model',
      model,
      icon,
      children,
      grouped,
      row: start,
      rows: 0,
      open,
      subtree: [],
    }
    if (open) entry.subtree = layoutChildren(children)
    entry.rows = cursor - start
    return entry
  }

  const layoutGroup = (row: TreeGroupRow, icon: string): LayoutGroup => {
    const start = cursor
    const open = !isFolded(fold, GROUP_KEY(row.group.id))
    cursor += 1
    const entry: LayoutGroup = {
      kind: 'group',
      group: row.group,
      row: start,
      rows: 0,
      open,
      members: [],
    }
    if (open) entry.members = row.members.map((m) => layoutModel(m, true, icon))
    entry.rows = cursor - start
    return entry
  }

  const out: LayoutCategory[] = []
  for (const cat of categories) {
    const start = cursor
    const open = !isFolded(fold, CAT_KEY(cat.bucket))
    cursor += 1
    const entry: LayoutCategory = { cat, row: start, rows: 0, open, entries: [] }
    if (open) {
      const groups: LayoutEntry[] = cat.groups.map((g) => layoutGroup(g, cat.icon))
      const models: LayoutEntry[] = cat.models.map((m) =>
        layoutModel(m, source.isGrouped(m.uid), cat.icon),
      )
      entry.entries = [...groups, ...models]
    }
    entry.rows = cursor - start
    out.push(entry)
  }

  return { categories: out, totalRows: cursor, modelRow }
}

/**
 * The viewport height the tree measured last, remembered across mounts.
 *
 * The tree cannot measure itself before it exists, and "no height" means "draw
 * every row" — so a remount (a tab switch back to the geometry tab) would build
 * the whole tree once and throw it away a frame later. The panel is a fixed
 * 330px column, so the last number is right for every mount after the first and
 * the ResizeObserver corrects it when it is not.
 */
let rememberedHeight = 0

export function rememberedViewportHeight(): number {
  return rememberedHeight
}

export function rememberViewportHeight(height: number): void {
  if (height > 0) rememberedHeight = height
}

/** Test seam: drop the remembered height so a mount starts unmeasured again. */
export function resetRememberedViewportForTest(): void {
  rememberedHeight = 0
}

/** The half-open row range a scroll position puts on screen. */
export interface RowWindow {
  start: number
  end: number
}

/**
 * Which rows to draw.
 *
 * Null when the container cannot be measured — see the module note. `padTop` is
 * the scroll container's own top padding, which `scrollTop` counts but the row
 * grid does not.
 */
export function rowWindow(
  totalRows: number,
  scrollTop: number,
  viewportHeight: number,
  rowHeight: number,
  padTop: number,
  overscan: number,
): RowWindow | null {
  if (!(viewportHeight > 0) || !(rowHeight > 0)) return null
  const first = Math.floor((scrollTop - padTop) / rowHeight)
  const visible = Math.ceil(viewportHeight / rowHeight)
  // Clamped at BOTH ends. `scrollTop` cannot outrun the content in a browser,
  // but a stale one — read before a fold made the tree shorter — can, and an
  // unclamped start would then sit past the last row and draw nothing at all.
  const start = Math.max(0, Math.min(first - overscan, Math.max(0, totalRows - 1)))
  const end = Math.max(start, Math.min(totalRows, first + visible + overscan))
  return { start, end }
}

export interface WindowSlice<T> {
  /** Rows skipped above the window — one spacer's worth of height. */
  before: number
  shown: T[]
  /** Rows skipped below the window. */
  after: number
}

/**
 * The entries a window keeps, and how many rows to spacer out on each side.
 *
 * An entry that STRADDLES either edge is kept whole: a group whose header is
 * above the window but whose members are in it still has to draw, and so does a
 * model whose own row is on screen with a subtree that runs off the bottom.
 */
export function windowSlice<T extends { row: number; rows: number }>(
  items: readonly T[],
  win: RowWindow | null,
): WindowSlice<T> {
  if (!win) return { before: 0, shown: items as T[], after: 0 }
  let before = 0
  let after = 0
  const shown: T[] = []
  for (const item of items) {
    if (item.row + item.rows <= win.start) before += item.rows
    else if (item.row >= win.end) after += item.rows
    else shown.push(item)
  }
  return { before, shown, after }
}

/**
 * The row a sub-node key sits on, or null when the tree has no such row.
 *
 * A walk rather than an index: the only caller is a panel's "jump to this
 * server" button, and keeping a 25,000-entry map up to date for one click would
 * cost more than the click does.
 */
export function findRowForKey(layout: TreeLayout, key: string): number | null {
  const inChildren = (list: readonly LayoutChild[]): number | null => {
    for (const child of list) {
      if (child.node.key === key) return child.row
      if (child.children.length) {
        const found = inChildren(child.children)
        if (found !== null) return found
      }
    }
    return null
  }
  for (const cat of layout.categories) {
    for (const entry of cat.entries) {
      const models = entry.kind === 'group' ? entry.members : [entry]
      for (const model of models) {
        const found = inChildren(model.subtree)
        if (found !== null) return found
      }
    }
  }
  return null
}
