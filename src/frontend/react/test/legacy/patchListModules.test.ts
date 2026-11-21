import { describe, expect, it } from 'vitest'
import { createFilterBar, createSelectionModel } from '../../src/legacy/patchListModules'

/**
 * These verify the SHARED modules through the accessors, not a reimplementation
 * of them. That is the point: the port reuses `shared/js/patch_filter.js` and
 * `shared/js/patch_selection.js`, so what has to be pinned is that they are
 * reachable from here and still behave the way both apps rely on. A rewrite in
 * TypeScript would pass its own tests while diverging from the legacy list.
 */

function bar() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  return { container, filter: createFilterBar({ container }) }
}

describe('createFilterBar', () => {
  it('inserts its bar as the first child of the host', () => {
    const { container, filter } = bar()
    expect(container.firstElementChild).toBe(filter.element)
    expect(container.querySelectorAll('.patch-filter-bar')).toHaveLength(1)
  })

  it('substring-matches, case-insensitively, by default', () => {
    const { filter } = bar()
    filter.setState({ text: 'RACK' })
    expect(filter.predicate('Rack_1_inlet')).toBe(true)
    expect(filter.predicate('wall_north')).toBe(false)
  })

  it('Aa makes the match case-sensitive', () => {
    const { filter } = bar()
    filter.setState({ text: 'RACK', case: true })
    expect(filter.predicate('Rack_1_inlet')).toBe(false)
    filter.setState({ text: 'Rack', case: true })
    expect(filter.predicate('Rack_1_inlet')).toBe(true)
  })

  it('ab| demands the whole name, not a substring', () => {
    const { filter } = bar()
    filter.setState({ text: 'rack_1_inlet', whole: true })
    expect(filter.predicate('Rack_1_inlet')).toBe(true)
    expect(filter.predicate('Rack_1_inlet_extra')).toBe(false)
  })

  it('.* takes precedence over ab|, so the user writes their own anchors', () => {
    const { filter } = bar()
    filter.setState({ text: '^rack_\\d+_inlet$', regex: true, whole: true })
    expect(filter.predicate('Rack_1_inlet')).toBe(true)
    expect(filter.predicate('Rack_1_inlet_extra')).toBe(false)
  })

  it('matches nothing while the regex is unfinished, and flags the input', () => {
    const { filter } = bar()
    filter.setState({ text: 'rack_(', regex: true })
    expect(filter.predicate('Rack_1_inlet')).toBe(false)
    expect(filter.element.querySelector('.filter-input')!.classList.contains('invalid')).toBe(true)
  })

  it('round-trips its state, which is how it survives a list rebuild', () => {
    const { filter } = bar()
    filter.setState({ text: 'acu', case: true, whole: false, regex: false })
    const saved = filter.getState()
    filter.clear()
    expect(filter.getState().text).toBe('')
    filter.setState(saved)
    expect(filter.getState()).toEqual({ text: 'acu', case: true, whole: false, regex: false })
  })

  it('renders the matched / total counter', () => {
    const { filter } = bar()
    filter.updateCount(3, 53)
    expect(filter.element.querySelector('.filter-count')!.textContent).toBe('3 / 53')
  })
})

describe('createSelectionModel', () => {
  const ordered = ['a', 'b', 'c', 'd']

  it('a plain click replaces the selection', () => {
    const m = createSelectionModel()
    m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ordered })
    m.handleRowClick('c', { shift: false, ctrl: false, orderedNames: ordered })
    expect([...m.getAll()]).toEqual(['c'])
  })

  it('ctrl adds and removes one at a time', () => {
    const m = createSelectionModel()
    m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ordered })
    m.handleRowClick('c', { shift: false, ctrl: true, orderedNames: ordered })
    expect([...m.getAll()].sort()).toEqual(['a', 'c'])
    m.handleRowClick('a', { shift: false, ctrl: true, orderedNames: ordered })
    expect([...m.getAll()]).toEqual(['c'])
  })

  it('shift takes the range from the anchor', () => {
    const m = createSelectionModel()
    m.handleRowClick('b', { shift: false, ctrl: false, orderedNames: ordered })
    m.handleRowClick('d', { shift: true, ctrl: false, orderedNames: ordered })
    expect([...m.getAll()].sort()).toEqual(['b', 'c', 'd'])
  })

  it('shift ranges backwards too', () => {
    const m = createSelectionModel()
    m.handleRowClick('d', { shift: false, ctrl: false, orderedNames: ordered })
    m.handleRowClick('b', { shift: true, ctrl: false, orderedNames: ordered })
    expect([...m.getAll()].sort()).toEqual(['b', 'c', 'd'])
  })

  it('shift+ctrl UNIONS the range with what is already selected', () => {
    const m = createSelectionModel()
    m.handleRowClick('a', { shift: false, ctrl: false, orderedNames: ordered })
    m.handleRowClick('c', { shift: false, ctrl: true, orderedNames: ordered })
    m.handleRowClick('d', { shift: true, ctrl: true, orderedNames: ordered })
    expect([...m.getAll()].sort()).toEqual(['a', 'c', 'd'])
  })

  it('a checkbox with a modifier fans the new state over the whole selection', () => {
    const m = createSelectionModel()
    m.setSelection(['a', 'b'])
    expect(
      m.handleCheckboxClick('c', { shift: false, ctrl: true, nextVisible: false }),
    ).toEqual({ targets: ['a', 'b', 'c'], visible: false })
  })

  it('a plain checkbox click is only that patch, selection or not', () => {
    const m = createSelectionModel()
    m.setSelection(['a', 'b'])
    expect(
      m.handleCheckboxClick('c', { shift: false, ctrl: false, nextVisible: true }),
    ).toEqual({ targets: ['c'], visible: true })
  })

  it('reports adds and removes to onChange, not the whole set', () => {
    const seen: Array<[string[], string[]]> = []
    const m = createSelectionModel({
      onChange: (prev, curr) => seen.push([[...prev].sort(), [...curr].sort()]),
    })
    m.setSelection(['a', 'b'])
    m.setSelection(['b', 'c'])
    expect(seen).toEqual([
      [[], ['a', 'b']],
      [
        ['a', 'b'],
        ['b', 'c'],
      ],
    ])
  })
})
