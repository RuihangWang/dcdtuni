import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import * as store from '../src/projectStore'
import {
  applyWallRegionOpacityState,
  commitWallRegionOpacity,
  previewWallRegionOpacity,
  primaryFaceId,
  resetWallOpacityPreview,
  setWallRegionName,
  wallOpacityPreviewPending,
  wallRegionCount,
  wallRegionName,
  wallRegionOpacityPercent,
  wallRegionSolidName,
} from '../src/geometry/wallRegion'

/** A floor whose top face is split by two vent tiles into three regions. */
function project(): CfdProject {
  return {
    name: 'T',
    geometry: { height: 3, plane: [] },
    constructions: {
      floors: {
        m_1: {
          uid: 'm_1',
          meta: { name: 'Floor_1', enabled: true },
          geometry: {
            location: { x: 0, y: 0, z: 0 },
            size: { x: 5, y: 0, z: 5 },
            faces: {
              top: {
                displayName: 'Top',
                prefixEnabled: true,
                opacity: 0.6,
                wallNames: ['wall_1', 'named_region'],
                wallOpacities: [],
                patches: [{ id: 'p_1' }, { id: 'p_2' }],
              },
            },
          },
          cooling: { defaultBoundaryType: 'perforated' },
        },
      },
    },
  } as unknown as CfdProject
}

function floor(): Construction {
  return store.getProject()!.constructions!.floors!.m_1!
}

function topFace(): { wallNames?: string[]; wallOpacities?: number[] } {
  return (floor().geometry as { faces: Record<string, { wallNames?: string[]; wallOpacities?: number[] }> })
    .faces.top!
}

beforeEach(() => {
  store.loadProject(project(), '/tmp/T.cfdproj')
  resetWallOpacityPreview()
})

describe('reading', () => {
  it('names an unset region wall_<n+1> without writing it into the project', () => {
    // `ensureWallName` materialises the default on READ, which is how an
    // untouched project comes back from a save with wallNames filled in.
    expect(wallRegionName(floor(), 'top', 2)).toBe('wall_3')
    expect(topFace().wallNames).toEqual(['wall_1', 'named_region'])
  })

  it('reads a stored name', () => {
    expect(wallRegionName(floor(), 'top', 1)).toBe('named_region')
  })

  it('falls back to the FACE opacity when the region has none', () => {
    expect(wallRegionOpacityPercent(floor(), 'top', 0)).toBe(60)
  })

  it('counts regions from the longer of the two parallel arrays', () => {
    expect(wallRegionCount(floor(), 'top')).toBe(2)
  })

  it('puts a floor s regions on the top face and everything else on the front', () => {
    expect(primaryFaceId(floor())).toBe('top')
    expect(primaryFaceId({ uid: 'x', meta: { name: 'B' }, kind: 'baffle' } as Construction)).toBe('front')
  })
})

describe('wallRegionSolidName', () => {
  it('is the model-scoped part name the builder tags the mesh with', () => {
    // NOT `wallNames[index]`: renaming a region does not rename the solid, and
    // the panel must not claim it does. A floor omits the synthetic face name.
    expect(wallRegionSolidName(floor(), 'top', 0)).toBe('Floor_1_part1')
    expect(wallRegionSolidName(floor(), 'top', 1)).toBe('Floor_1_part2')
  })
})

describe('setWallRegionName', () => {
  it('writes the name and is undoable', () => {
    setWallRegionName('m_1', 'top', 0, 'north_strip')
    expect(topFace().wallNames?.[0]).toBe('north_strip')
    store.undo()
    expect(topFace().wallNames?.[0]).toBe('wall_1')
  })

  it('falls back to wall_<n+1> for a blank name rather than storing nothing', () => {
    setWallRegionName('m_1', 'top', 1, '   ')
    expect(topFace().wallNames?.[1]).toBe('wall_2')
  })

  it('creates the array for a face that has never been named', () => {
    setWallRegionName('m_1', 'top', 3, 'far_corner')
    expect(topFace().wallNames?.[3]).toBe('far_corner')
  })
})

describe('opacity', () => {
  it('writes one region without touching its neighbours', () => {
    commitWallRegionOpacity('m_1', 'top', 1, 25)
    expect(topFace().wallOpacities?.[1]).toBeCloseTo(0.25, 10)
    // Earlier slots are filled from the face rather than left as holes: a hole
    // serialises as null and reads back as "not finite" on the next load.
    expect(topFace().wallOpacities?.[0]).toBeCloseTo(0.6, 10)
  })

  it('rejects a value that is not a number', () => {
    expect(applyWallRegionOpacityState(floor(), 'top', 0, Number.NaN)).toBe(false)
  })

  it('takes ONE checkpoint for a whole drag', () => {
    // The slider fires on every pointer move; one edit per frame would make a
    // single drag eighty presses of Ctrl+Z to undo.
    previewWallRegionOpacity('m_1', 'top', 0, 90)
    expect(wallOpacityPreviewPending()).toBe(true)
    previewWallRegionOpacity('m_1', 'top', 0, 60)
    previewWallRegionOpacity('m_1', 'top', 0, 30)
    commitWallRegionOpacity('m_1', 'top', 0, 30)
    expect(wallOpacityPreviewPending()).toBe(false)
    expect(topFace().wallOpacities?.[0]).toBeCloseTo(0.3, 10)
    store.undo()
    expect(topFace().wallOpacities?.[0]).toBeUndefined()
  })

  it('commits without a preview as a plain undoable edit', () => {
    commitWallRegionOpacity('m_1', 'top', 0, 40)
    store.undo()
    expect(topFace().wallOpacities?.[0]).toBeUndefined()
  })
})
