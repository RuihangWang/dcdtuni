import { describe, expect, it } from 'vitest'
import {
  applyHiddenFilter,
  DEFAULT_SORT,
  formatMtime,
  nextSortState,
  sortEntries,
  visibleEntries,
  type BrowseEntry,
  type ListedEntry,
} from '../src/io/fileBrowserModel'

function entry(name: string, over: Partial<BrowseEntry> = {}): BrowseEntry {
  return { name, path: `/d/${name}`, hidden: name.startsWith('.'), mtime: 0, ...over }
}

function listed(name: string, type: 'dir' | 'file', mtime = 0): ListedEntry {
  return { ...entry(name, { mtime }), type }
}

describe('nextSortState', () => {
  it('reverses the column that is already active', () => {
    expect(nextSortState({ col: 'name', asc: true }, 'name')).toEqual({ col: 'name', asc: false })
    expect(nextSortState({ col: 'name', asc: false }, 'name')).toEqual({ col: 'name', asc: true })
  })

  it('switches to a new column ascending', () => {
    expect(nextSortState({ col: 'name', asc: false }, 'date')).toEqual({ col: 'date', asc: true })
  })
})

describe('applyHiddenFilter', () => {
  it('drops dot-entries unless asked for', () => {
    const all = [entry('.git'), entry('case'), entry('.cache')]
    expect(applyHiddenFilter(all, false).map((e) => e.name)).toEqual(['case'])
    expect(applyHiddenFilter(all, true).map((e) => e.name)).toEqual(['.git', 'case', '.cache'])
  })

  it('never mutates the listing it was given', () => {
    const all = [entry('b'), entry('a')]
    applyHiddenFilter(all, true).reverse()
    expect(all.map((e) => e.name)).toEqual(['b', 'a'])
  })
})

describe('sortEntries', () => {
  it('keeps directories above files in BOTH directions', () => {
    const rows = [listed('b.cfdproj', 'file'), listed('a', 'dir')]
    expect(sortEntries(rows, { col: 'name', asc: true }).map((e) => e.type)).toEqual(['dir', 'file'])
    expect(sortEntries(rows, { col: 'name', asc: false }).map((e) => e.type)).toEqual([
      'dir',
      'file',
    ])
  })

  it('compares names case-insensitively', () => {
    const rows = [listed('beta', 'dir'), listed('Alpha', 'dir')]
    expect(sortEntries(rows, DEFAULT_SORT).map((e) => e.name)).toEqual(['Alpha', 'beta'])
  })

  it('sorts by date descending — the newest run first', () => {
    const rows = [
      listed('2026-01-01', 'dir', 100),
      listed('2026-06-01', 'dir', 300),
      listed('2026-03-01', 'dir', 200),
    ]
    expect(sortEntries(rows, { col: 'date', asc: false }).map((e) => e.name)).toEqual([
      '2026-06-01',
      '2026-03-01',
      '2026-01-01',
    ])
  })

  it('treats a missing mtime as the oldest rather than as NaN', () => {
    const rows = [listed('dated', 'dir', 100), { ...listed('undated', 'dir'), mtime: 0 }]
    expect(sortEntries(rows, { col: 'date', asc: true }).map((e) => e.name)).toEqual([
      'undated',
      'dated',
    ])
  })
})

describe('visibleEntries', () => {
  it('merges, filters and sorts one browse-dir response', () => {
    const rows = visibleEntries(
      [entry('.hidden'), entry('runs')],
      [entry('b.cfdproj'), entry('a.cfdproj'), entry('.swp')],
      false,
      DEFAULT_SORT,
    )
    expect(rows.map((e) => e.name)).toEqual(['runs', 'a.cfdproj', 'b.cfdproj'])
  })

  it('survives a response with no files key at all', () => {
    expect(visibleEntries([entry('runs')], undefined, false, DEFAULT_SORT)).toHaveLength(1)
  })
})

describe('formatMtime', () => {
  it('reads the stamp as POSIX SECONDS, not milliseconds', () => {
    // 2026-06-01 12:34 local. Fed as milliseconds this would land in 1970 and
    // the date column would look broken rather than mis-scaled.
    const stamp = new Date(2026, 5, 1, 12, 34).getTime() / 1000
    expect(formatMtime(stamp)).toBe('2026-06-01 12:34')
  })

  it('shows a dash when the server could not stat the entry', () => {
    expect(formatMtime(0)).toBe('—')
    expect(formatMtime(undefined)).toBe('—')
  })
})
