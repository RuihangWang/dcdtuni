import { beforeEach, describe, expect, it } from 'vitest'

import * as store from '../src/projectStore'
import { automation } from '../src/automation/contract'
import {
  boxesOverlap,
  placeDuplicateWithoutOverlap,
  SEARCH_DIRECTIONS,
  SEARCH_RINGS,
} from '../src/geometry/duplicatePlacement'

/**
 * Two failures this prevents, both invisible until the mesh: a copy sitting
 * inside its neighbour in a dense room, and a copy kept outside the project
 * bounds instead of being refused.
 */

const box = (x: number, y: number, z: number, w = 0.6, h = 2, d = 1) => ({
  location: { x, y, z },
  size: { x: w, y: h, z: d },
})

describe('boxesOverlap', () => {
  it('sees a shared volume', () => {
    expect(boxesOverlap(box(0, 0, 0), box(0.3, 0, 0))).toBe(true)
  })

  it('does not count touching faces as overlap', () => {
    expect(boxesOverlap(box(0, 0, 0), box(0.6, 0, 0))).toBe(false)
  })

  it('needs every axis to overlap, not just one', () => {
    expect(boxesOverlap(box(0, 0, 0), box(0.3, 5, 0))).toBe(false)
  })
})

describe('placeDuplicateWithoutOverlap', () => {
  it('leaves the copy where it is when that spot is already free', () => {
    expect(placeDuplicateWithoutOverlap(box(4, 0, 0), [box(0, 0, 0)])).toEqual({ x: 4, y: 0, z: 0 })
  })

  it('steps the copy clear of the neighbour it landed in', () => {
    const found = placeDuplicateWithoutOverlap(box(0.3, 0, 0), [box(0, 0, 0)])
    expect(found).not.toBeNull()
    expect(boxesOverlap({ location: found!, size: { x: 0.6, y: 2, z: 1 } }, box(0, 0, 0))).toBe(false)
  })

  it('refuses rather than placing a copy outside the project bounds', () => {
    // A one-metre-wide corridor with a 0.6 m model already in it: every ring
    // step leaves the box, so there is nowhere legal to put a second one.
    const bounds = {
      xMin: { enabled: true, value: 0 },
      xMax: { enabled: true, value: 1 },
      yMin: { enabled: true, value: 0 },
      yMax: { enabled: true, value: 2 },
      zMin: { enabled: true, value: 0 },
      zMax: { enabled: true, value: 1 },
    }
    expect(placeDuplicateWithoutOverlap(box(0.2, 0, 0), [box(0.2, 0, 0)], bounds)).toBeNull()
  })

  it('keeps the copy inside enabled bounds when it does find room', () => {
    const bounds = {
      xMin: { enabled: true, value: 0 },
      xMax: { enabled: true, value: 10 },
    }
    const found = placeDuplicateWithoutOverlap(box(0, 0, 0), [box(0, 0, 0)], bounds)
    expect(found).not.toBeNull()
    expect(found!.x).toBeGreaterThanOrEqual(0)
    expect(found!.x + 0.6).toBeLessThanOrEqual(10 + 1e-5)
  })

  it('gives up after twelve rings rather than searching forever', () => {
    // A wall of neighbours along every direction the search tries.
    const others = []
    for (const dir of SEARCH_DIRECTIONS) {
      for (let ring = 0; ring <= SEARCH_RINGS; ring += 1) {
        others.push(
          box(dir.x * 0.648 * ring, dir.y * 2.16 * ring, dir.z * 1.08 * ring),
        )
      }
    }
    expect(placeDuplicateWithoutOverlap(box(0, 0, 0), others)).toBeNull()
  })

  it('steps a zero-thickness sheet by the 0.12 m floor, not by nothing', () => {
    const sheet = { location: { x: 0, y: 0, z: 0 }, size: { x: 4, y: 0, z: 3 } }
    const found = placeDuplicateWithoutOverlap(sheet, [sheet])
    expect(found).not.toBeNull()
    expect(found).not.toEqual(sheet.location)
  })
})

// ---------------------------------------------------------------------------
// Wired into the store — the part that was missing
// ---------------------------------------------------------------------------


describe('duplicating a model through the store', () => {
  beforeEach(() => {
    automation.newProject()
  })

  it('does not land the copy inside its neighbour', () => {
    // The old one-line offset shifted the copy by its own width, which on a row
    // of racks at that exact pitch puts it precisely on top of the next one.
    const a = automation.createDefaultRackModel()!
    automation.placeModel(a, { name: 'R1', location: { x: 0, y: 0, z: 0 }, size: { W: 0.6, D: 1.2, H: 2 } })
    const b = automation.createDefaultRackModel()!
    automation.placeModel(b, { name: 'R2', location: { x: 0.6, y: 0, z: 0 }, size: { W: 0.6, D: 1.2, H: 2 } })

    const copy = store.duplicateModel(a)
    expect(copy).toBeTruthy()

    const project = store.getProject()!
    const at = (uid: string) =>
      (store.findConstruction(project, uid)!.model.geometry as { location: { x: number; y: number; z: number } }).location
    const copyLoc = at(copy!)
    // Not on top of R2, and not on top of R1 either.
    expect(copyLoc).not.toEqual(at(b))
    expect(copyLoc).not.toEqual(at(a))
  })

  it('refuses rather than placing a copy outside the project bounds', () => {
    const uid = automation.createDefaultRackModel()!
    automation.placeModel(uid, { name: 'R1', location: { x: 0, y: 0, z: 0 }, size: { W: 0.6, D: 1.2, H: 2 } })
    // A box exactly the size of the model: nowhere for a second one to go.
    for (const [key, value] of [
      ['xMin', 0], ['xMax', 0.6], ['yMin', 0], ['yMax', 2], ['zMin', 0], ['zMax', 1.2],
    ] as const) {
      store.setProjectBound(key, 'enabled', true)
      store.setProjectBound(key, 'value', value)
    }
    expect(store.duplicateModel(uid)).toBeNull()
  })
})
