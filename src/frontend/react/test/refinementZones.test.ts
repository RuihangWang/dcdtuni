import { beforeEach, describe, expect, it } from 'vitest'
import { clampZoneBound, displayBound, MIN_SPAN, refinementZones, renameRefinementZone, setRefinementZoneBound, setRefinementZoneVisible } from '../src/run/refinementZones'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'

function project(): CfdProject {
  return {
    version: 4,
    meta: { name: 'zones' },
    geometry: { plane: [[0, 0], [4, 0], [4, 4], [0, 4]] },
    constructions: {},
    runDefaults: {
      meshConfig: {
        refinementZones: [
          {
            id: 'rz_1',
            name: 'RefineZone_1',
            level: 2,
            visible: true,
            min: { x: 0, y: 0, z: 0 },
            max: { x: 1, y: 1, z: 1 },
          },
        ],
      },
    },
  } as unknown as CfdProject
}

describe('clampZoneBound', () => {
  it('leaves a valid pair alone', () => {
    expect(clampZoneBound(0, 1, 'min', 0.4)).toEqual({ min: 0.4, max: 1 })
    expect(clampZoneBound(0, 1, 'max', 2.5)).toEqual({ min: 0, max: 2.5 })
  })

  it('pushes the OTHER side when the typed one would invert the box', () => {
    // A negative-extent searchableBox is not an error snappyHexMesh reports: it
    // refines nothing, and the zone silently stops doing its job.
    expect(clampZoneBound(0, 1, 'min', 5)).toEqual({ min: 1 - MIN_SPAN, max: 1 })
    expect(clampZoneBound(0, 1, 'max', -5)).toEqual({ min: 0, max: MIN_SPAN })
  })

  it('keeps at least one centimetre of span', () => {
    const r = clampZoneBound(0, 1, 'min', 1)
    expect(r.max - r.min).toBeCloseTo(MIN_SPAN, 12)
  })
})

describe('displayBound', () => {
  it('rounds to millimetres for the box, as the legacy list does', () => {
    expect(displayBound(1.2345678)).toBe(1.235)
    expect(displayBound(-0.0004)).toBe(-0)
  })
})

describe('zone edits against a real project', () => {
  beforeEach(() => {
    store.loadProject(project(), null)
  })

  it('writes a typed bound through to the project', () => {
    setRefinementZoneBound('rz_1', 'max', 'x', 1.2)
    expect(refinementZones()[0]?.max?.x).toBe(1.2)
  })

  it('clamps a typed min that would pass the max', () => {
    setRefinementZoneBound('rz_1', 'min', 'y', 3)
    const zone = refinementZones()[0]
    expect(zone?.min?.y).toBeCloseTo(0.99, 12)
    expect(zone?.max?.y).toBe(1)
  })

  it('ignores a non-finite value rather than writing NaN into the mesh dict', () => {
    setRefinementZoneBound('rz_1', 'max', 'z', Number.NaN)
    expect(refinementZones()[0]?.max?.z).toBe(1)
  })

  it('renames, and refuses a blank name', () => {
    renameRefinementZone('rz_1', ' Cold aisle ')
    expect(refinementZones()[0]?.name).toBe('Cold aisle')
    renameRefinementZone('rz_1', '   ')
    expect(refinementZones()[0]?.name).toBe('Cold aisle')
  })

  it('takes an undo checkpoint for a bound but not for a visibility toggle', () => {
    // Hiding a box to see the racks behind it is not an edit to the case;
    // putting it on the undo stack would make Ctrl+Z undo the wrong thing.
    const before = store.undoDepth()
    setRefinementZoneVisible('rz_1', false)
    expect(refinementZones()[0]?.visible).toBe(false)
    expect(store.undoDepth()).toBe(before)
    setRefinementZoneBound('rz_1', 'max', 'x', 2)
    expect(store.undoDepth()).toBe(before + 1)
  })
})
