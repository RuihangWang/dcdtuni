import { beforeEach, describe, expect, it } from 'vitest'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import {
  composeFaceScopedExportName,
  faceOf,
  facePrefixName,
  facePrefixSupported,
  isFacePrefixEnabled,
  toggleFacePrefix,
} from '../src/geometry/facePrefix'
import * as store from '../src/projectStore'

/**
 * The prefix decides what a face's solids are CALLED, and a solid name is an
 * OpenFOAM patch name. So these pin the composed string, not the button.
 */

function project(): CfdProject {
  const face = (displayName: string, prefixEnabled = true) => ({
    displayName,
    prefixEnabled,
    patches: [],
  })
  return {
    uid: 'p_prefix',
    name: 'T',
    constructions: {
      boxes: {
        m_1: {
          uid: 'm_1',
          kind: 'box',
          meta: { name: 'Box_A', enabled: true },
          geometry: {
            location: { x: 0, y: 0, z: 0 },
            size: { x: 1, y: 1, z: 1 },
            faces: { front: face('Inlet Face'), top: face('top', false) },
          },
        },
      },
      walls: {
        m_2: {
          uid: 'm_2',
          kind: 'wall',
          meta: { name: 'Wall_N', enabled: true },
          geometry: {
            location: { x: 0, y: 0, z: 0 },
            size: { x: 4, y: 3, z: 0.2 },
            faces: { front: face('front') },
          },
        },
      },
    },
  } as unknown as CfdProject
}

const modelOf = (uid: string): Construction =>
  store.findConstruction(store.getProject() as CfdProject, uid)!.model

beforeEach(() => store.loadProject(project(), null))

describe('composeFaceScopedExportName', () => {
  it('prefixes with the face display name, sanitised', () => {
    // "Inlet Face" is not a legal solid name; the space becomes an underscore
    // or OpenFOAM cannot parse the patch.
    expect(composeFaceScopedExportName('front', { displayName: 'Inlet Face', prefixEnabled: true }, 'wall_1')).toBe(
      'Inlet_Face_wall_1',
    )
  })

  it('drops the prefix when the flag is off', () => {
    expect(composeFaceScopedExportName('front', { displayName: 'Inlet Face' }, 'wall_1')).toBe(
      'wall_1',
    )
  })

  it('falls back to the face id when there is no display name', () => {
    expect(composeFaceScopedExportName('back', { prefixEnabled: true }, 'wall_1')).toBe(
      'back_wall_1',
    )
  })

  it('names an unnamed region after the face', () => {
    expect(composeFaceScopedExportName('back', { prefixEnabled: true }, '')).toBe(
      'back_wall_back',
    )
  })

  it('sanitises the base name too', () => {
    expect(composeFaceScopedExportName('front', { prefixEnabled: false }, 'vent tile #2')).toBe(
      'vent_tile_2',
    )
  })
})

describe('facePrefixName', () => {
  it('prefers the display name over the face id', () => {
    expect(facePrefixName('front', { displayName: 'Supply' })).toBe('Supply')
    expect(facePrefixName('front', { label: 'Return' })).toBe('Return')
    expect(facePrefixName('front', {})).toBe('front')
  })
})

describe('isFacePrefixEnabled', () => {
  it('defaults to on, as the schema and the migration do', () => {
    expect(isFacePrefixEnabled({})).toBe(true)
    expect(isFacePrefixEnabled(undefined)).toBe(true)
    expect(isFacePrefixEnabled({ prefixEnabled: false })).toBe(false)
  })
})

describe('facePrefixSupported', () => {
  it('covers the kinds whose exporter keeps a face component', () => {
    expect(facePrefixSupported('box')).toBe(true)
    expect(facePrefixSupported('acu')).toBe(true)
    expect(facePrefixSupported('cdu')).toBe(true)
    // wall / floor / baffle export with no face name at all, so the flag has
    // nothing to switch there.
    expect(facePrefixSupported('wall')).toBe(false)
    expect(facePrefixSupported('floor')).toBe(false)
    expect(facePrefixSupported(undefined)).toBe(false)
  })
})

describe('toggleFacePrefix', () => {
  it('writes the flag onto the face', () => {
    toggleFacePrefix('m_1', 'front', false)
    expect(faceOf(modelOf('m_1'), 'front')!.prefixEnabled).toBe(false)
    toggleFacePrefix('m_1', 'front', true)
    expect(faceOf(modelOf('m_1'), 'front')!.prefixEnabled).toBe(true)
  })

  it('changes the composed name the inspector shows', () => {
    toggleFacePrefix('m_1', 'front', false)
    const face = faceOf(modelOf('m_1'), 'front')!
    expect(composeFaceScopedExportName('front', face, 'sample_region')).toBe('sample_region')
  })

  it('is undoable, because it is project data', () => {
    toggleFacePrefix('m_1', 'front', false)
    store.undo()
    expect(faceOf(modelOf('m_1'), 'front')!.prefixEnabled).toBe(true)
  })

  it('refuses the kinds whose exporter ignores it', () => {
    toggleFacePrefix('m_2', 'front', false)
    expect(faceOf(modelOf('m_2'), 'front')!.prefixEnabled).toBe(true)
  })

  it('ignores a face that does not exist', () => {
    toggleFacePrefix('m_1', 'nope', false)
    expect(faceOf(modelOf('m_1'), 'front')!.prefixEnabled).toBe(true)
  })
})
