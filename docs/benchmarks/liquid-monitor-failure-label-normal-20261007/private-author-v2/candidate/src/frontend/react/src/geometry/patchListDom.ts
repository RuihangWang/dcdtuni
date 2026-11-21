/**
 * The DOM half of the patch-list filter and selection.
 *
 * `patch_filter.js` produces a predicate and `patch_selection.js` produces a set
 * of names; neither touches the list. Everything that does live in the legacy
 * page itself (STL_generate.html:24594-24631) and is ported here so both patch
 * lists — post-processing and mesh preview — apply it the same way.
 *
 * ## Why the classes are written imperatively and not through React
 *
 * `filter-hidden` and `row-selected` are toggled on rows React rendered. That
 * works, and keeps working, only because the rows carry a CONSTANT `className`
 * prop: React writes `className` to the DOM only when its own previous value
 * differs, so a row that re-renders for a changed `checked` prop keeps the
 * classes we put on it. Move either class into the JSX and the other one gets
 * wiped every time the first changes.
 *
 * When React genuinely rebuilds the rows — a new run, a group expanded — the
 * classes go with the old nodes, which is why `patchListControls.ts` re-applies
 * both after every rebuild.
 */

/** The purple the legacy paints a selected patch and its row (`:24592`). */
export const PATCH_HIGHLIGHT_COLOR = 0x9b59d0

export interface CountSink {
  updateCount(matched: number, total: number): void
}

/**
 * Hide the rows the predicate rejects, and collapse groups left with nothing.
 *
 * The ancestor walk is the part that is easy to leave out: without it a kind
 * header (RACK / ACU / WALL) stays on screen with an empty body when its whole
 * subtree is filtered away, and the list reads as "the filter matched a RACK"
 * when it matched nothing.
 */
export function applyFilterToDOM(
  container: HTMLElement,
  predicate: (name: string) => boolean,
  filterBar?: CountSink | null,
): { matched: number; total: number } {
  const rows = container.querySelectorAll<HTMLElement>('div[data-patch]')
  let matched = 0
  rows.forEach((row) => {
    const ok = predicate(row.dataset.patch as string)
    row.classList.toggle('filter-hidden', !ok)
    if (ok) matched += 1
  })

  const ancestors = new Set<HTMLElement>()
  rows.forEach((row) => {
    let a = row.parentElement
    while (a && a !== container) {
      ancestors.add(a)
      a = a.parentElement
    }
  })
  ancestors.forEach((group) => {
    const anyVisible = Array.from(group.querySelectorAll('div[data-patch]')).some(
      (r) => !r.classList.contains('filter-hidden'),
    )
    group.classList.toggle('filter-hidden-group', !anyVisible)
  })

  filterBar?.updateCount(matched, rows.length)
  return { matched, total: rows.length }
}

/**
 * The names a shift-range runs over: rows in DOM order, filtered rows excluded.
 *
 * Excluding them is what makes shift-click mean "everything between these two
 * as I can see them" rather than "everything between them in the unfiltered
 * list", which would select rows the user cannot see.
 */
export function getVisibleOrderedNames(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll<HTMLElement>('div[data-patch]'))
    .filter((r) => !r.classList.contains('filter-hidden'))
    .map((r) => r.dataset.patch as string)
}

/**
 * The names a GROUP action applies to: everything the filter left on screen.
 *
 * The legacy guards its three group gestures with the same line — the kind
 * checkbox (STL_generate.html:21029), the per-model one (:21080) and the mesh
 * preview's 全选/全不选 (:24565) all skip a row carrying `filter-hidden`.
 * Without it, filtering to `acu` and ticking the group turns off the three
 * hundred patches the filter is hiding, which is the opposite of what the
 * filter is for.
 *
 * Only rows that are PRESENT and hidden are dropped. A collapsed group renders
 * no rows at all in React — the legacy merely sets `display:none`, so its
 * checkboxes stay queryable and the case never arises there — and treating an
 * absent row as filtered out would make ticking a folded group do nothing.
 */
export function namesOnScreen(
  container: HTMLElement | null,
  names: readonly string[],
): string[] {
  if (!container) return [...names]
  const filtered = new Set(
    Array.from(
      container.querySelectorAll<HTMLElement>('div[data-patch].filter-hidden'),
    ).map((r) => r.dataset.patch as string),
  )
  if (filtered.size === 0) return [...names]
  return names.filter((n) => !filtered.has(n))
}

/** Every patch name with a row, filtered or not, in DOM order. */
export function getRowNames(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll<HTMLElement>('div[data-patch]')).map(
    (r) => r.dataset.patch as string,
  )
}

export function diffSets(
  prev: Set<string>,
  curr: Set<string>,
  handlers: { added?: (name: string) => void; removed?: (name: string) => void },
) {
  prev.forEach((n) => {
    if (!curr.has(n)) handlers.removed?.(n)
  })
  curr.forEach((n) => {
    if (!prev.has(n)) handlers.added?.(n)
  })
}

/** The row for a patch, or null. Names come from OpenFOAM, so they are escaped. */
export function rowFor(container: HTMLElement, name: string): HTMLElement | null {
  const escape = (window as unknown as { CSS?: { escape?: (s: string) => string } }).CSS?.escape
  const selector = `div[data-patch="${escape ? escape(name) : name.replace(/"/g, '\\"')}"]`
  return container.querySelector<HTMLElement>(selector)
}

export function markRowSelected(container: HTMLElement, name: string, on: boolean) {
  rowFor(container, name)?.classList.toggle('row-selected', on)
  syncGroupHeads(container)
}

/**
 * The group heads (RACK / ACU ... and the per-model line under them) light up
 * with the rows inside them: a patch picked in the viewport is one row deep
 * in a tree of a few hundred, and the head is what tells you WHICH rack it
 * belongs to before the list has scrolled there.
 */
export function syncGroupHeads(container: HTMLElement) {
  container
    .querySelectorAll<HTMLElement>('.pp-kind-head, .pp-model-head')
    .forEach((head) => {
      const group = head.parentElement
      const any = !!group?.querySelector('div[data-patch].row-selected')
      head.classList.toggle('row-selected-parent', any)
    })
}
