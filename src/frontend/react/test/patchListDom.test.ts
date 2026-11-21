import { beforeEach, describe, expect, it } from 'vitest'
import {
  applyFilterToDOM,
  diffSets,
  getRowNames,
  getVisibleOrderedNames,
  markRowSelected,
  rowFor,
} from '../src/geometry/patchListDom'

/**
 * The tree these build is the shape both panels render: a kind group wrapping a
 * model group wrapping the rows. The nesting is the point — the ancestor
 * collapse has to survive TWO levels, and a one-level fixture would pass with
 * the walk missing.
 */
function tree(spec: Record<string, Record<string, string[]>>): HTMLElement {
  const container = document.createElement('div')
  for (const [kind, byModel] of Object.entries(spec)) {
    const kindEl = document.createElement('div')
    kindEl.className = 'pp-kind'
    kindEl.dataset.kind = kind
    for (const [model, names] of Object.entries(byModel)) {
      const modelEl = document.createElement('div')
      modelEl.className = 'pp-model-body'
      modelEl.dataset.model = model
      for (const name of names) {
        const row = document.createElement('div')
        row.className = 'pp-patch-row'
        row.dataset.patch = name
        modelEl.appendChild(row)
      }
      kindEl.appendChild(modelEl)
    }
    container.appendChild(kindEl)
  }
  document.body.appendChild(container)
  return container
}

let container: HTMLElement

beforeEach(() => {
  document.body.innerHTML = ''
  container = tree({
    rack: { Rack_1: ['Rack_1_inlet', 'Rack_1_outlet'], Rack_2: ['Rack_2_inlet'] },
    wall: { Room: ['wall_north', 'wall_south'] },
  })
})

describe('applyFilterToDOM', () => {
  it('hides the rows the predicate rejects and counts the rest', () => {
    const result = applyFilterToDOM(container, (n) => n.startsWith('Rack_1'))
    expect(result).toEqual({ matched: 2, total: 5 })
    expect(rowFor(container, 'Rack_1_inlet')!.classList.contains('filter-hidden')).toBe(false)
    expect(rowFor(container, 'wall_north')!.classList.contains('filter-hidden')).toBe(true)
  })

  it('collapses a group whose whole subtree was filtered out', () => {
    applyFilterToDOM(container, (n) => n.startsWith('Rack_1'))
    const wall = container.querySelector('[data-kind="wall"]')!
    const rack = container.querySelector('[data-kind="rack"]')!
    expect(wall.classList.contains('filter-hidden-group')).toBe(true)
    // The RACK kind survives even though one of its two models is empty.
    expect(rack.classList.contains('filter-hidden-group')).toBe(false)
    expect(
      container.querySelector('[data-model="Rack_2"]')!.classList.contains('filter-hidden-group'),
    ).toBe(true)
  })

  it('re-shows a group when the filter is widened again', () => {
    applyFilterToDOM(container, (n) => n.startsWith('Rack_1'))
    applyFilterToDOM(container, () => true)
    expect(
      container.querySelector('[data-kind="wall"]')!.classList.contains('filter-hidden-group'),
    ).toBe(false)
    expect(getVisibleOrderedNames(container)).toHaveLength(5)
  })

  it('reports the counts to the filter bar', () => {
    const seen: Array<[number, number]> = []
    applyFilterToDOM(container, (n) => n.includes('inlet'), {
      updateCount: (m, total) => seen.push([m, total]),
    })
    expect(seen).toEqual([[2, 5]])
  })
})

describe('getVisibleOrderedNames', () => {
  it('is DOM order, filtered rows excluded', () => {
    expect(getRowNames(container)).toEqual([
      'Rack_1_inlet',
      'Rack_1_outlet',
      'Rack_2_inlet',
      'wall_north',
      'wall_south',
    ])
    applyFilterToDOM(container, (n) => n.endsWith('_inlet') || n === 'wall_south')
    // A shift-range over these must not step through Rack_1_outlet: it is not
    // on screen, and selecting it would be invisible.
    expect(getVisibleOrderedNames(container)).toEqual([
      'Rack_1_inlet',
      'Rack_2_inlet',
      'wall_south',
    ])
  })
})

describe('diffSets', () => {
  it('reports only what changed, in both directions', () => {
    const added: string[] = []
    const removed: string[] = []
    diffSets(new Set(['a', 'b']), new Set(['b', 'c']), {
      added: (n) => added.push(n),
      removed: (n) => removed.push(n),
    })
    expect(added).toEqual(['c'])
    expect(removed).toEqual(['a'])
  })
})

describe('markRowSelected', () => {
  it('adds and removes the class without disturbing the filter class', () => {
    applyFilterToDOM(container, () => false)
    markRowSelected(container, 'Rack_1_inlet', true)
    const row = rowFor(container, 'Rack_1_inlet')!
    expect(row.classList.contains('row-selected')).toBe(true)
    expect(row.classList.contains('filter-hidden')).toBe(true)
    markRowSelected(container, 'Rack_1_inlet', false)
    expect(row.classList.contains('row-selected')).toBe(false)
  })

  it('is a no-op for a name with no row', () => {
    expect(() => markRowSelected(container, 'nope', true)).not.toThrow()
    expect(rowFor(container, 'nope')).toBeNull()
  })
})
