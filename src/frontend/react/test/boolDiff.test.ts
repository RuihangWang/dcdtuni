import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { canBeTarget, footprintOnFace, primaryFaceId, rectToPoly } from '../src/geometry/boolDiff'
import type { Construction } from '../src/schema/cfdproj'

const IDENTITY = new THREE.Matrix4()
const FACE = { w: 10, h: 6 }

function box(min: [number, number, number], max: [number, number, number]) {
  return { min: { x: min[0], y: min[1], z: min[2] }, max: { x: max[0], y: max[1], z: max[2] } }
}

describe('footprintOnFace', () => {
  it('projects the tool box onto the face', () => {
    expect(footprintOnFace(box([2, 1, 0], [4, 3, 1]), IDENTITY, FACE)).toEqual({
      x: 2,
      y: 1,
      w: 2,
      h: 2,
    })
  })

  it('clamps to the face, so an overhanging tool cuts only what is over it', () => {
    expect(footprintOnFace(box([-3, -2, 0], [4, 3, 1]), IDENTITY, FACE)).toEqual({
      x: 0,
      y: 0,
      w: 4,
      h: 3,
    })
  })

  it('returns null when the tool misses the face entirely', () => {
    // A zero-area cutout would leave a degenerate polygon in the project.
    expect(footprintOnFace(box([20, 20, 0], [21, 21, 1]), IDENTITY, FACE)).toBeNull()
  })

  it('projects ALL EIGHT corners, so a rotated face still gets the right box', () => {
    // With a 45 degree rotation the min/max corner pair is no longer the
    // min/max pair after the transform; taking only two corners would give a
    // cutout narrower than the tool.
    // Placed well inside the face, so the CLAMP does not hide the effect being
    // tested: a box straddling the face origin would be cut in half by it.
    const rot = new THREE.Matrix4()
      .makeRotationZ(Math.PI / 4)
      .premultiply(new THREE.Matrix4().makeTranslation(50, 50, 0))
    const rect = footprintOnFace(box([-1, -1, 0], [1, 1, 1]), rot, { w: 100, h: 100 })!
    expect(rect.w).toBeCloseTo(Math.SQRT2 * 2, 6)
    expect(rect.h).toBeCloseTo(Math.SQRT2 * 2, 6)
  })

  it('handles a face matrix that translates', () => {
    const move = new THREE.Matrix4().makeTranslation(-5, -1, 0)
    expect(footprintOnFace(box([6, 2, 0], [8, 4, 1]), move, FACE)).toEqual({
      x: 1,
      y: 1,
      w: 2,
      h: 2,
    })
  })
})

describe('rectToPoly', () => {
  it('emits a CLOSED ring in the builder’s nesting', () => {
    const poly = rectToPoly(1, 2, 3, 4)
    const ring = poly[0]![0]!
    expect(ring[0]).toEqual([1, 2])
    expect(ring[ring.length - 1]).toEqual(ring[0])
    expect(ring).toHaveLength(5)
  })
})

describe('target rules', () => {
  const of = (kind: string) => ({ kind } as unknown as Construction)

  it('only planar kinds can be cut', () => {
    expect(canBeTarget(of('floor'))).toBe(true)
    expect(canBeTarget(of('baffle'))).toBe(true)
    // A cutout on a solid box has no meaning.
    expect(canBeTarget(of('rack'))).toBe(false)
    expect(canBeTarget(null)).toBe(false)
  })

  it('cuts a floor from above and a baffle head-on', () => {
    expect(primaryFaceId(of('floor'))).toBe('top')
    expect(primaryFaceId(of('baffle'))).toBe('front')
  })
})

describe('footprintOnFace — the tool must actually reach the face plane', () => {
  // DMAL_Testbed: the table legs (P1..P4) stand 0.43-1.84 m in front of
  // Wall_1 and still cut holes in it; the ACU sits 0.85 m above the floor and
  // still cut the floor. A silhouette is not contact.
  it('returns null for a box that hovers in front of the face', () => {
    expect(footprintOnFace(box([2, 1, 0.43], [4, 3, 1.43]), IDENTITY, FACE)).toBeNull()
  })

  it('returns null for a box that lies entirely behind the face', () => {
    expect(footprintOnFace(box([2, 1, -1.5], [4, 3, -0.5]), IDENTITY, FACE)).toBeNull()
  })

  it('still cuts a box that merely touches the plane (within 1 cm)', () => {
    expect(footprintOnFace(box([2, 1, 0.005], [4, 3, 1]), IDENTITY, FACE)).toEqual({
      x: 2, y: 1, w: 2, h: 2,
    })
    expect(footprintOnFace(box([2, 1, -1], [4, 3, -0.005]), IDENTITY, FACE)).toEqual({
      x: 2, y: 1, w: 2, h: 2,
    })
  })
})
