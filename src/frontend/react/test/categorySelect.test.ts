import { describe, expect, it } from 'vitest'
import { categoryClick, modelsInCategories } from '../src/interaction/categorySelect'
import type { CfdProject } from '../src/schema/cfdproj'

const PRESENT = ['racks', 'acus', 'walls', 'floors']

describe('categoryClick', () => {
  it('selects the one category on a plain click and becomes the anchor', () => {
    expect(categoryClick(PRESENT, null, 'acus', false)).toEqual({
      categories: ['acus'],
      anchor: 'acus',
    })
  })

  it('extends from the anchor on shift, in either direction', () => {
    expect(categoryClick(PRESENT, 'racks', 'walls', true).categories).toEqual([
      'racks',
      'acus',
      'walls',
    ])
    expect(categoryClick(PRESENT, 'walls', 'racks', true).categories).toEqual([
      'racks',
      'acus',
      'walls',
    ])
  })

  it('keeps the anchor where it was, so a range can be grown and shrunk', () => {
    const first = categoryClick(PRESENT, 'racks', 'floors', true)
    expect(first.anchor).toBe('racks')
    expect(categoryClick(PRESENT, first.anchor, 'acus', true).categories).toEqual([
      'racks',
      'acus',
    ])
  })

  it('skips a category with no models, because the tree does not draw it', () => {
    // A range across an empty category would select nothing for it and read as
    // a highlight on a row that is not there.
    expect(categoryClick(['racks', 'floors'], 'racks', 'floors', true).categories).toEqual([
      'racks',
      'floors',
    ])
  })

  it('falls back to a single selection when the anchor has gone', () => {
    expect(categoryClick(PRESENT, 'boxes', 'walls', true)).toEqual({
      categories: ['walls'],
      anchor: 'walls',
    })
  })
})

describe('modelsInCategories', () => {
  const project: CfdProject = {
    constructions: {
      racks: {
        r1: { uid: 'r1', meta: { name: 'Rack_1' } },
        r2: { uid: 'r2', meta: { name: 'Rack_2' } },
      },
      acus: { a1: { uid: 'a1', meta: { name: 'ACU_1' } } },
      walls: { w1: { uid: 'w1', meta: { name: 'Wall_1' } } },
    },
  }

  it('returns every model of the chosen categories in project order', () => {
    expect(modelsInCategories(project, ['racks', 'walls'])).toEqual(['r1', 'r2', 'w1'])
  })

  it('is empty without a project or a category', () => {
    expect(modelsInCategories(null, ['racks'])).toEqual([])
    expect(modelsInCategories(project, [])).toEqual([])
  })
})
