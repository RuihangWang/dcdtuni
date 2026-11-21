import { describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import type { ModelGroup } from '../src/geometry/modelGroups'
import {
  buildTreeCategories,
  nextAnchor,
  rangeSelect,
  visualModelOrder,
} from '../src/interaction/treeVisualOrder'

function model(uid: string, name = uid): Construction {
  return { uid, meta: { name }, geometry: {} } as unknown as Construction
}

function bucket(...models: Construction[]): Record<string, Construction> {
  return Object.fromEntries(models.map((m) => [m.uid, m]))
}

/** Racks out of name order, so a sorted view differs from insertion order. */
const PROJECT: CfdProject = {
  constructions: {
    racks: bucket(model('r10', 'Rack_10'), model('r2', 'Rack_2'), model('r1', 'Rack_1')),
    acus: bucket(model('a1', 'ACU_1'), model('a2', 'ACU_2')),
  },
}

const group = (id: string, name: string, memberIds: string[]): ModelGroup =>
  ({ id, name, memberIds }) as ModelGroup

describe('buildTreeCategories', () => {
  it('drops categories with nothing in them', () => {
    // A range must not step through a category that is not on screen.
    expect(buildTreeCategories(PROJECT, [], 'name-asc').map((c) => c.bucket)).toEqual([
      'racks',
      'acus',
    ])
  })

  it('sorts the rows the way the tree draws them', () => {
    const [racks] = buildTreeCategories(PROJECT, [], 'name-asc')
    expect(racks!.models.map((m) => m.uid)).toEqual(['r1', 'r2', 'r10'])
  })

  it('leaves insertion order alone in manual mode', () => {
    const [racks] = buildTreeCategories(PROJECT, [], 'manual')
    expect(racks!.models.map((m) => m.uid)).toEqual(['r10', 'r2', 'r1'])
  })

  it('files a group under the category of its first member and hides its rows from the list', () => {
    const cats = buildTreeCategories(PROJECT, [group('g1', 'A', ['r2', 'r10'])], 'name-asc')
    const racks = cats.find((c) => c.bucket === 'racks')!
    expect(racks.groups.map((g) => g.group.id)).toEqual(['g1'])
    expect(racks.groups[0]!.members.map((m) => m.uid)).toEqual(['r2', 'r10'])
    expect(racks.models.map((m) => m.uid)).toEqual(['r1'])
    // The badge counts every model in the category, grouped or not.
    expect(racks.count).toBe(3)
  })

  it('skips a folder whose models were all deleted', () => {
    // It draws nothing, so it must not occupy a position a range steps through.
    const cats = buildTreeCategories(PROJECT, [group('g1', 'A', ['gone'])], 'name-asc')
    expect(cats.find((c) => c.bucket === 'racks')!.groups).toEqual([])
  })

  it('returns nothing without a project', () => {
    expect(buildTreeCategories(null, [], 'name-asc')).toEqual([])
  })
})

describe('visualModelOrder', () => {
  it('stacks grouped rows above ungrouped ones, category by category', () => {
    const cats = buildTreeCategories(PROJECT, [group('g1', 'A', ['r2', 'r10'])], 'name-asc')
    expect(visualModelOrder(cats)).toEqual(['r2', 'r10', 'r1', 'a1', 'a2'])
  })

  it('follows the SORT, which is what the legacy range select does not', () => {
    // getVisualModelOrder (STL_generate.html:12791) rebuilds the order without
    // sortModelsForTree, so under the default name-asc its range covers the
    // models between two insertion-order positions instead of the rows the user
    // sees between the two they clicked.
    expect(visualModelOrder(buildTreeCategories(PROJECT, [], 'name-asc'))).toEqual([
      'r1',
      'r2',
      'r10',
      'a1',
      'a2',
    ])
    expect(visualModelOrder(buildTreeCategories(PROJECT, [], 'manual'))).toEqual([
      'r10',
      'r2',
      'r1',
      'a1',
      'a2',
    ])
  })
})

const ORDER = ['r1', 'r2', 'r10', 'a1', 'a2']

describe('rangeSelect', () => {
  it('takes every row between the anchor and the click', () => {
    expect(rangeSelect(ORDER, 'r1', 'r10')).toEqual(['r1', 'r2', 'r10'])
  })

  it('reads the same range upwards', () => {
    expect(rangeSelect(ORDER, 'r10', 'r1')).toEqual(['r1', 'r2', 'r10'])
  })

  it('crosses category boundaries, as the legacy flat order does', () => {
    expect(rangeSelect(ORDER, 'r2', 'a1')).toEqual(['r2', 'r10', 'a1'])
  })

  it('is an ordinary click with no anchor to extend from', () => {
    expect(rangeSelect(ORDER, null, 'a1')).toEqual(['a1'])
  })

  it('ignores an anchor the tree no longer draws', () => {
    expect(rangeSelect(ORDER, 'deleted', 'a1')).toEqual(['a1'])
  })

  it('selects the single row when anchor and click are the same', () => {
    expect(rangeSelect(ORDER, 'r2', 'r2')).toEqual(['r2'])
  })

  it('selects nothing for a row that is not on screen', () => {
    expect(rangeSelect(ORDER, 'r1', 'hidden')).toEqual([])
  })
})

describe('nextAnchor', () => {
  it('moves to the row clicked without a modifier', () => {
    expect(nextAnchor('r1', 'r2', 'a1', false)).toBe('a1')
  })

  it('stays put under a modifier, so a range can grow and shrink', () => {
    // The bug this guards: an anchor that follows the shift-click walks the
    // range down the tree instead of extending it.
    expect(nextAnchor('r1', 'r2', 'r10', true)).toBe('r1')
  })

  it('falls back to the active model when the tree has no anchor yet', () => {
    // A selection made in the VIEWPORT never went through the tree, and a
    // shift-click straight after it still has to extend from it.
    expect(nextAnchor(null, 'r2', 'r10', true)).toBe('r2')
  })

  it('falls back to the clicked row when nothing is selected at all', () => {
    expect(nextAnchor(null, null, 'r10', true)).toBe('r10')
  })
})
