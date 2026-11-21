import { describe, expect, it } from 'vitest'
import {
  OPEN_ALL,
  batchFoldKeys,
  foldAll,
  foldKeys,
  isFolded,
  rootClickAction,
  rootFolded,
  toggleFold,
} from '../src/interaction/treeFold'

/**
 * The root row of the feature tree does two things the React port had not
 * wired: its caret folds or opens EVERY branch, and a click on it selects every
 * model. The caret was a static glyph and the click dropped the selection.
 */

describe('isFolded', () => {
  it('a branch with no entry uses its own default', () => {
    expect(isFolded(OPEN_ALL, 'model:m_1')).toBe(false)
    expect(isFolded(OPEN_ALL, 'face:m_1:front', true)).toBe(true)
  })

  it('a per-branch entry wins over the default', () => {
    const s = toggleFold(OPEN_ALL, 'model:m_1')
    expect(isFolded(s, 'model:m_1')).toBe(true)
    expect(isFolded(toggleFold(s, 'model:m_1'), 'model:m_1')).toBe(false)
  })

  it('`open` is the current state when the caller knows it — a folded-by-default branch toggles open', () => {
    const s = toggleFold(OPEN_ALL, 'face:m_1:front', false)
    expect(isFolded(s, 'face:m_1:front', true)).toBe(false)
  })
})

describe('foldAll', () => {
  it('folds every branch, overriding the defaults and the entries', () => {
    const s = foldAll(toggleFold(OPEN_ALL, 'model:m_1', true), true)
    expect(isFolded(s, 'model:m_1')).toBe(true)
    expect(isFolded(s, 'cat:racks')).toBe(true)
    expect(rootFolded(s)).toBe(true)
  })

  it('opens every branch, including the ones folded by default', () => {
    const s = foldAll(OPEN_ALL, false)
    expect(isFolded(s, 'face:m_1:front', true)).toBe(false)
    expect(rootFolded(s)).toBe(false)
  })

  it('a branch folded by hand after "open all" stays folded', () => {
    const s = toggleFold(foldAll(OPEN_ALL, false), 'model:m_1')
    expect(isFolded(s, 'model:m_1')).toBe(true)
    expect(isFolded(s, 'model:m_2')).toBe(false)
  })
})

describe('rootClickAction', () => {
  it('selects all when anything is unselected', () => {
    expect(rootClickAction(['a', 'b'], new Set())).toBe('selectAll')
    expect(rootClickAction(['a', 'b'], new Set(['a']))).toBe('selectAll')
  })

  it('deselects when everything is already selected — the same click is the way back', () => {
    expect(rootClickAction(['a', 'b'], new Set(['a', 'b']))).toBe('deselect')
  })

  it('an empty project has nothing to select', () => {
    expect(rootClickAction([], new Set())).toBe('deselect')
  })
})

describe('batchFoldKeys', () => {
  const two = new Set(['m_1', 'm_2'])

  it('without shift it is the one caret', () => {
    expect(batchFoldKeys('model:m_1', false, two)).toEqual(['model:m_1'])
  })

  it('shift on a selected model folds that branch on every selected model', () => {
    expect(batchFoldKeys('model:m_1', true, two)).toEqual(['model:m_1', 'model:m_2'])
    expect(batchFoldKeys('face:m_2:front', true, two)).toEqual(['face:m_1:front', 'face:m_2:front'])
    expect(batchFoldKeys('rack-equipment:m_1', true, two)).toEqual([
      'rack-equipment:m_1',
      'rack-equipment:m_2',
    ])
  })

  it('shift on a model that is NOT in the selection folds only itself', () => {
    expect(batchFoldKeys('model:m_9', true, two)).toEqual(['model:m_9'])
  })

  it('shift with a single selection is a plain fold', () => {
    expect(batchFoldKeys('model:m_1', true, new Set(['m_1']))).toEqual(['model:m_1'])
  })

  it('a category caret batches over the selected categories, not the models', () => {
    expect(batchFoldKeys('cat:racks', true, two, ['racks', 'acus'])).toEqual(['cat:racks', 'cat:acus'])
    expect(batchFoldKeys('cat:walls', true, two, ['racks', 'acus'])).toEqual(['cat:walls'])
  })

  it('foldKeys sets every key to the ONE state of the gesture', () => {
    const s = foldKeys(toggleFold(OPEN_ALL, 'model:m_2', true), ['model:m_1', 'model:m_2'], false)
    expect(isFolded(s, 'model:m_1')).toBe(false)
    expect(isFolded(s, 'model:m_2')).toBe(false)
  })
})
