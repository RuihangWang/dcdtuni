import { describe, expect, it } from 'vitest'
import { generateRoom, polygonArea, rectangleFootprint, signedArea2, type Vertex2 } from '../src/room2d/room2d'

/**
 * The winding decides which way every wall faces. A clockwise footprint whose
 * flip is missed produces a room that meshes cleanly and then solves the
 * OUTSIDE of the building, so these are worth pinning independently of the
 * model-generation round trip.
 */

const CCW: Vertex2[] = [
  [0, 0],
  [4.4, 0],
  [4.4, 6],
  [0, 6],
]
const CW: Vertex2[] = [...CCW].reverse()

describe('signedArea2', () => {
  it('is positive for a counter-clockwise ring', () => {
    // 4.4 x 6 = 26.4, twice that is 52.8 — the value case/Base_leakage carries
    expect(signedArea2(CCW)).toBeCloseTo(52.8, 6)
  })

  it('is negative for the same ring reversed', () => {
    expect(signedArea2(CW)).toBeCloseTo(-52.8, 6)
  })

  it('drives the normal flip: CW needs it, CCW does not', () => {
    expect(signedArea2(CW) < 0).toBe(true)
    expect(signedArea2(CCW) < 0).toBe(false)
  })

  it('is unchanged by where the ring starts', () => {
    const rotated: Vertex2[] = [...CCW.slice(2), ...CCW.slice(0, 2)]
    expect(signedArea2(rotated)).toBeCloseTo(signedArea2(CCW), 6)
  })
})

describe('polygonArea', () => {
  it('matches the rectangle', () => {
    expect(polygonArea(CCW)).toBeCloseTo(26.4, 6)
  })

  it('does not depend on winding', () => {
    expect(polygonArea(CW)).toBeCloseTo(polygonArea(CCW), 6)
  })

  it('handles a non-rectangular room', () => {
    // right triangle, legs 7.6 and 3.8
    const tri: Vertex2[] = [
      [0, 0],
      [7.6, 0],
      [0, 3.8],
    ]
    expect(polygonArea(tri)).toBeCloseTo((7.6 * 3.8) / 2, 6)
  })
})

describe('rectangleFootprint', () => {
  it('is counter-clockwise, so the quick path needs no flip', () => {
    expect(signedArea2(rectangleFootprint(10, 6))).toBeGreaterThan(0)
  })

  it('has the requested extent', () => {
    const r = rectangleFootprint(10, 6)
    expect(Math.max(...r.map((p) => p[0]))).toBe(10)
    expect(Math.max(...r.map((p) => p[1]))).toBe(6)
  })
})

// ---------------------------------------------------------------------------
// Wall normals must point OUT of the room, for every winding and every shape.
// ---------------------------------------------------------------------------

/** Is a point inside the polygon? Ray casting, so it works for concave rings too. */
function inside(polygon: Vertex2[], px: number, py: number): boolean {
  let hit = false
  const n = polygon.length
  for (let j = 0; j < n; j += 1) {
    const a = polygon[j]!
    const b = polygon[(j + 1) % n]!
    if (a[1] > py !== b[1] > py) {
      const xInt = ((b[0] - a[0]) * (py - a[1])) / (b[1] - a[1]) + a[0]
      if (px < xInt) hit = !hit
    }
  }
  return hit
}

/**
 * The world-space normal of a wall's `front` face.
 *
 * `getFaceLayouts` (STL_generate.html:6614) defines the front face's LOCAL
 * normal as right x up = (-1,0,0) x (0,1,0) = (0,0,-1) — not +Z, which is the
 * assumption that made the first version of this check report every wall
 * inward. The wall is then rotated about -Y by the edge angle, and
 * `reverseSolidNormal` negates the result.
 */
function wallNormal(a: Vertex2, b: Vertex2, flipped: boolean): [number, number] {
  const angle = Math.atan2(b[1] - a[1], b[0] - a[0])
  // local (0,0,1) -> (-sin, 0, cos), so local (0,0,-1) -> (+sin, 0, -cos)
  const nx = Math.sin(angle)
  const nz = -Math.cos(angle)
  return flipped ? [-nx, -nz] : [nx, nz]
}

describe('wall normals point out of the room', () => {
  const CASES: [string, Vertex2[]][] = [
    ['rectangle CCW', [[0, 0], [4.4, 0], [4.4, 6], [0, 6]]],
    ['rectangle CW', [[0, 6], [4.4, 6], [4.4, 0], [0, 0]]],
    ['right triangle CCW', [[0, 0], [7.6, 0], [0, 3.8]]],
    ['right triangle CW', [[0, 3.8], [7.6, 0], [0, 0]]],
    ['L-shaped CCW', [[0, 0], [8, 0], [8, 4], [4, 4], [4, 7], [0, 7]]],
  ]

  it.each(CASES)('%s', (_name, poly) => {
    const flipped = signedArea2(poly) < 0
    for (let i = 0; i < poly.length; i += 1) {
      const a = poly[i]!
      const b = poly[(i + 1) % poly.length]!
      const [nx, nz] = wallNormal(a, b, flipped)
      const mx = (a[0] + b[0]) / 2
      const my = (a[1] + b[1]) / 2
      // Step off the edge midpoint along the normal; it must leave the polygon.
      expect(inside(poly, mx + nx * 0.01, my + nz * 0.01)).toBe(false)
      // ...and stepping the other way must stay inside, which rules out a
      // "normal parallel to the wall" bug passing the first check by accident.
      expect(inside(poly, mx - nx * 0.01, my - nz * 0.01)).toBe(true)
    }
  })
})

/**
 * Outward-facing normals, the requirement the whole generator exists for.
 *
 * A wall whose normal points INTO the room is not a rendering nit: the mesher
 * reads the STL normal to decide which side of the surface the fluid is on, so
 * an inward wall carves the room out of the solid instead of the other way
 * round, and the case either fails to mesh or meshes the wrong volume.
 *
 * The front face's LOCAL normal is `right x up` = (-1,0,0) x (0,1,0) =
 * (0,0,-1). Not +Z — that assumption is what made an earlier check declare
 * every wall inward. The world normal is that vector under the wall's own
 * quaternion.
 */
describe('generated room normals point outward', () => {
  function normalOf(q: { x: number; y: number; z: number; w: number }) {
    const t = 2 * Math.atan2(q.y, q.w)
    const c = Math.cos(t)
    const s = Math.sin(t)
    const lx = 0
    const lz = -1
    return { x: lx * c + lz * s, z: -lx * s + lz * c }
  }

  const W = 12
  const D = 7
  const room = generateRoom(
    { geometry: { height: 3.2 } } as never,
    [
      [0, 0],
      [W, 0],
      [W, D],
      [0, D],
    ],
    3.2,
  )

  it('makes four walls, a floor and a ceiling', () => {
    expect(room.walls.length).toBe(4)
    expect(room.floor).toBeTruthy()
    expect(room.ceiling).toBeTruthy()
  })

  it('points every wall away from the room interior', () => {
    const cx = W / 2
    const cz = D / 2
    for (const { model: wall } of room.walls) {
      const g = wall.geometry as { location: { x: number; z: number }; quaternion: { x: number; y: number; z: number; w: number } }
      const n = normalOf(g.quaternion)
      // From the wall's anchor towards the room centre.
      const toCentre = { x: cx - g.location.x, z: cz - g.location.z }
      const dot = n.x * toCentre.x + n.z * toCentre.z
      // Outward means the normal points AWAY from the centre: a negative dot.
      expect(dot, `${(wall.meta as { name: string }).name} normal ${JSON.stringify(n)} vs centre`).toBeLessThan(0)
    }
  })

  it('reverses the floor so its normal points DOWN, away from the room', () => {
    const faces = (room.floor.model.geometry as { faces: Record<string, { reverseSolidNormal?: boolean }> }).faces
    expect(faces.top?.reverseSolidNormal).toBe(true)
  })

  it('leaves the ceiling unreversed so its normal points UP', () => {
    const faces = (room.ceiling.model.geometry as { faces: Record<string, { reverseSolidNormal?: boolean }> }).faces
    expect(faces.top?.reverseSolidNormal).not.toBe(true)
  })

  it('puts the floor at y=0 and the ceiling at the room height', () => {
    expect((room.floor.model.geometry as { location: { y: number } }).location.y).toBe(0)
    expect((room.ceiling.model.geometry as { location: { y: number } }).location.y).toBe(3.2)
  })

  it('gives every wall the room height and its own edge length', () => {
    const lengths = room.walls
      .map(({ model }) => (model.geometry as { size: { x: number; y: number } }).size)
      .map((s) => [s.x, s.y])
    expect(lengths).toEqual([
      [W, 3.2],
      [D, 3.2],
      [W, 3.2],
      [D, 3.2],
    ])
  })
})
