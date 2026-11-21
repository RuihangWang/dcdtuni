import { describe, expect, it } from 'vitest'
import type { Construction } from '../src/schema/cfdproj'
import {
  allModelPatches,
  customRegionGroups,
  customRegionKey,
  solidRegionsOf,
} from '../src/geometry/customRegions'

const model = (over: Record<string, unknown>): Construction =>
  ({ uid: 'm_1', kind: 'custom', meta: { name: 'shell' }, geometry: { faces: {} }, ...over }) as unknown as Construction

const face = (patches: Record<string, unknown>[] = []) => ({ patches })

describe('solidRegionsOf', () => {
  it('reads the V4 nest', () => {
    expect(solidRegionsOf(model({ customMesh: { solidRegions: ['a', 'b'] } }))).toEqual(['a', 'b'])
  })

  it('falls back to the flat pre-V4 field a factory seed still carries', () => {
    expect(solidRegionsOf(model({ solidRegions: ['only'] }))).toEqual(['only'])
  })

  it('is empty for a model that never carried a mesh', () => {
    expect(solidRegionsOf(model({}))).toEqual([])
  })
})

describe('customRegionKey', () => {
  it('maps the whole model onto the legacy __whole__ key', () => {
    expect(customRegionKey(null)).toBe('__whole__')
    expect(customRegionKey('')).toBe('__whole__')
    expect(customRegionKey('shell_top')).toBe('shell_top')
  })
})

describe('customRegionGroups', () => {
  it('lists every solid, empty ones included', () => {
    const m = model({
      customMesh: { solidRegions: ['top', 'side'] },
      geometry: { faces: { front: face() } },
    })
    expect([...customRegionGroups(m).keys()]).toEqual(['top', 'side'])
  })

  it('files each patch under the region it names', () => {
    const m = model({
      customMesh: { solidRegions: ['top', 'side'] },
      geometry: {
        faces: {
          top: face([{ id: 'p_1', regionName: 'top' }]),
          front: face([{ id: 'p_2', regionName: 'side' }, { id: 'p_3', regionName: 'top' }]),
        },
      },
    })
    const groups = customRegionGroups(m)
    expect(groups.get('top')?.map((e) => e.patch.id)).toEqual(['p_1', 'p_3'])
    expect(groups.get('side')?.map((e) => e.patch.id)).toEqual(['p_2'])
    // The face the patch lives on travels with it: the panel reads the patch
    // back through that face, and a region spans several of them.
    expect(groups.get('top')?.map((e) => e.faceId)).toEqual(['top', 'front'])
  })

  it('gives a mesh with no named solids the single whole-model row', () => {
    const m = model({ geometry: { faces: { front: face([{ id: 'p_1' }]) } } })
    const groups = customRegionGroups(m)
    expect([...groups.keys()]).toEqual(['__whole__'])
    expect(groups.get('__whole__')?.map((e) => e.patch.id)).toEqual(['p_1'])
  })

  it('keeps a patch whose region the mesh no longer has', () => {
    // Losing the row would strand its boundary condition with no way back.
    const m = model({
      customMesh: { solidRegions: ['top'] },
      geometry: { faces: { front: face([{ id: 'p_9', regionName: 'deleted_solid' }]) } },
    })
    const groups = customRegionGroups(m)
    expect([...groups.keys()]).toEqual(['top', 'deleted_solid'])
    expect(groups.get('deleted_solid')?.map((e) => e.patch.id)).toEqual(['p_9'])
  })
})

describe('allModelPatches', () => {
  it('walks the faces in their own order', () => {
    const m = model({
      geometry: { faces: { front: face([{ id: 'p_1' }]), top: face([{ id: 'p_2' }]) } },
    })
    expect(allModelPatches(m).map((e) => `${e.faceId}/${e.patch.id}`)).toEqual([
      'front/p_1',
      'top/p_2',
    ])
  })

  it('survives a rack, whose faces are an ARRAY', () => {
    expect(allModelPatches(model({ geometry: { faces: [] } }))).toEqual([])
  })
})
