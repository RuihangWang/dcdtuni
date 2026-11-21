/**
 * What a server directory listing looks like on screen: hidden, sorted, dated.
 *
 * The legacy editor has this logic twice — once in the case-directory browser
 * (`getSortedDirs` / `_fbSort` / `_fbToggleHidden`, STL_generate.html:18821-18926)
 * and once in the project load/save dialog (`getSortedEntries` / `_lpSort`,
 * :15066-15093). Both dialogs are ported, so the rules live here once instead of
 * drifting apart the way the two originals already have: only the project dialog
 * sorts files, and only the case browser can create a folder.
 *
 * Pure on purpose. Sorting a listing is the part that is easy to get subtly
 * wrong — a locale compare that ignores case, directories that stop floating to
 * the top, a second click on the same column that resets instead of reversing —
 * and none of it needs a DOM to check.
 */

export interface BrowseEntry {
  name: string
  path: string
  hidden: boolean
  mtime: number
  size?: number
}

/** An entry tagged with which list it came from, so directories can sort first. */
export type ListedEntry = BrowseEntry & { type: 'dir' | 'file' }

export type SortColumn = 'name' | 'date'

export interface SortState {
  col: SortColumn
  asc: boolean
}

export const DEFAULT_SORT: SortState = { col: 'name', asc: true }

/**
 * What clicking a column header does.
 *
 * Clicking the ACTIVE column reverses it; clicking another column switches to it
 * ascending. Resetting to ascending on a re-click would make "newest first"
 * unreachable, which is the whole reason the date column exists — a case
 * directory of timestamped runs is read newest-first.
 */
export function nextSortState(current: SortState, col: SortColumn): SortState {
  if (current.col === col) return { col, asc: !current.asc }
  return { col, asc: true }
}

/** Dot-entries are dropped unless the user asked for them, exactly as both legacy dialogs do. */
export function applyHiddenFilter<T extends { hidden: boolean }>(
  entries: readonly T[],
  showHidden: boolean,
): T[] {
  return showHidden ? entries.slice() : entries.filter((e) => !e.hidden)
}

/**
 * Directories first, then the chosen column.
 *
 * The type split is NOT part of the reversal: flipping to descending must not
 * push the folders below the files, or navigating deeper means scrolling past
 * every .cfdproj in the directory first.
 */
export function sortEntries(entries: readonly ListedEntry[], sort: SortState): ListedEntry[] {
  const out = entries.slice()
  out.sort((a, b) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
    const cmp =
      sort.col === 'date'
        ? (a.mtime || 0) - (b.mtime || 0)
        : a.name.toLowerCase().localeCompare(b.name.toLowerCase())
    return sort.asc ? cmp : -cmp
  })
  return out
}

/** Hidden-filter and sort a raw `/api/browse-dir` response into one display list. */
export function visibleEntries(
  dirs: readonly BrowseEntry[] | undefined,
  files: readonly BrowseEntry[] | undefined,
  showHidden: boolean,
  sort: SortState,
): ListedEntry[] {
  const tagged: ListedEntry[] = [
    ...applyHiddenFilter(dirs ?? [], showHidden).map((d) => ({ ...d, type: 'dir' as const })),
    ...applyHiddenFilter(files ?? [], showHidden).map((f) => ({ ...f, type: 'file' as const })),
  ]
  return sortEntries(tagged, sort)
}

/**
 * The listing's modified column: `YYYY-MM-DD HH:MM` from a POSIX seconds stamp.
 *
 * Seconds, not milliseconds — `/api/browse-dir` returns `entry.stat().st_mtime`
 * (server.py:2871). Feeding that straight to `new Date()` dates every file to
 * 1970 and the date sort then looks broken rather than mis-scaled.
 */
export function formatMtime(mtime: number | undefined): string {
  if (!mtime) return '—'
  const d = new Date(mtime * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
