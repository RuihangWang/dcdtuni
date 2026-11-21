import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import { bucketOf, reorderKeys, reorderModel } from '../src/interaction/treeReorder'

function project(): CfdProject {
  return {
    uid: 'p_reorder',
    name: 'T',
    constructions: {
      racks: {
        r1: { uid: 'r1', meta: { name: 'Rack_1' } },
        r2: { uid: 'r2', meta: { name: 'Rack_2' } },
        r3: { uid: 'r3', meta: { name: 'Rack_3' } },
      },
      acus: {
        a1: { uid: 'a1', meta: { name: 'ACU_1' } },
      },
    },
  }
}

beforeEach(() => store.loadProject(project(), null))

describe('reorderKeys', () => {
  it('drops a node in before the target', () => {
    expect(reorderKeys(['r1', 'r2', 'r3'], 'r3', 'r1', 'before')).toEqual(['r3', 'r1', 'r2'])
  })

  it('drops a node in after the target', () => {
    expect(reorderKeys(['r1', 'r2', 'r3'], 'r1', 'r3', 'after')).toEqual(['r2', 'r3', 'r1'])
  })

  it('removes the source BEFORE finding the target index', () => {
    // Otherwise dragging downwards lands one slot short: the target's index
    // still counted the source sitting above it.
    expect(reorderKeys(['r1', 'r2', 'r3'], 'r1', 'r2', 'after')).toEqual(['r2', 'r1', 'r3'])
  })

  it('refuses a drop on itself or on an unknown node', () => {
    expect(reorderKeys(['r1', 'r2'], 'r1', 'r1', 'before')).toBeNull()
    expect(reorderKeys(['r1', 'r2'], 'r1', 'nope', 'before')).toBeNull()
    expect(reorderKeys(['r1', 'r2'], 'nope', 'r1', 'before')).toBeNull()
  })

  it('keeps every key', () => {
    const out = reorderKeys(['a', 'b', 'c', 'd'], 'b', 'd', 'after')!
    expect([...out].sort()).toEqual(['a', 'b', 'c', 'd'])
  })
})

describe('reorderModel', () => {
  it('rewrites the bucket key order, which is the export order', () => {
    expect(reorderModel('r3', 'r1', 'before')).toBe(true)
    expect(Object.keys(store.getProject()!.constructions!.racks!)).toEqual(['r3', 'r1', 'r2'])
  })

  it('is one undo step, and the undo restores the old order', () => {
    reorderModel('r3', 'r1', 'before')
    expect(store.undoDepth()).toBe(1)
    store.undo()
    expect(Object.keys(store.getProject()!.constructions!.racks!)).toEqual(['r1', 'r2', 'r3'])
  })

  it('refuses a cross-bucket drop and leaves no undo entry', () => {
    // The V4 buckets are per-kind dictionaries: a rack has no position among
    // the ACUs, so this cannot be honoured — and an undo step that restores an
    // unchanged project reads as a broken undo.
    expect(reorderModel('r1', 'a1', 'before')).toBe(false)
    expect(store.undoDepth()).toBe(0)
    expect(Object.keys(store.getProject()!.constructions!.racks!)).toEqual(['r1', 'r2', 'r3'])
  })

  it('refuses a drop on itself', () => {
    expect(reorderModel('r1', 'r1', 'after')).toBe(false)
    expect(store.undoDepth()).toBe(0)
  })

  it('keeps every model, with its data', () => {
    reorderModel('r1', 'r3', 'after')
    const racks = store.getProject()!.constructions!.racks!
    expect(Object.keys(racks)).toEqual(['r2', 'r3', 'r1'])
    expect(racks.r1!.meta.name).toBe('Rack_1')
  })

  it('reports the bucket a model lives in', () => {
    expect(bucketOf(store.getProject()!, 'r2')).toBe('racks')
    expect(bucketOf(store.getProject()!, 'a1')).toBe('acus')
    expect(bucketOf(store.getProject()!, 'nope')).toBeNull()
  })
})
