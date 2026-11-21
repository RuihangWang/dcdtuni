import { describe, expect, it } from 'vitest'
import type { Construction } from '../src/schema/cfdproj'
import { patchAnchorWorld, worldToPatchAnchor } from '../src/geometry/patchAnchor'

function acu(quaternion = { x: 0, y: 0, z: 0, w: 1 }): Construction {
  return {
    uid: 'm_20',
    kind: 'acu',
    meta: { name: 'AC2' },
    geometry: {
      location: { x: 2.45354, y: 2.07, z: 0 },
      size: { x: 0.2, y: 0.3, z: 1 },
      quaternion,
      scale: { x: 1, y: 1, z: 1 },
    },
  } as unknown as Construction
}

describe('patchAnchorWorld', () => {
  it('maps a rect corner on the RIGHT face into the room (DMAL AC2 supply)', () => {
    // right face: origin (W,0,D), right = -Z, up = +Y. x=0,y=0 -> local (W,0,D).
    const p = patchAnchorWorld(acu(), 'right', { type: 'rect', x: 0, y: 0, w: 1, h: 0.15 })!
    expect(p.x).toBeCloseTo(2.45354 + 0.2, 9)
    expect(p.y).toBeCloseTo(2.07, 9)
    expect(p.z).toBeCloseTo(1, 9)
  })

  it('maps a circle centre on the TOP face', () => {
    const p = patchAnchorWorld(acu(), 'top', { type: 'circle', cx: 0.1, cy: 0.4, r: 0.05 })!
    // top: origin (0,H,D), right +X, up -Z
    expect([p.x, p.y, p.z].map((v) => +v.toFixed(9))).toEqual([2.55354, 2.37, 0.6])
  })

  it('follows the model rotation', () => {
    // 90 deg yaw about Y: local +X -> world -Z, local +Z -> world +X.
    const q = { x: 0, y: Math.SQRT1_2, z: 0, w: Math.SQRT1_2 }
    const p = patchAnchorWorld(acu(q), 'right', { type: 'rect', x: 0, y: 0 })!
    // local (0.2, 0, 1) -> rotated (1, 0, -0.2) + location
    expect(p.x).toBeCloseTo(2.45354 + 1, 9)
    expect(p.y).toBeCloseTo(2.07, 9)
    expect(p.z).toBeCloseTo(-0.2, 9)
  })

  it('has no world anchor for kinds whose patches are not on a face layout', () => {
    const wall = { ...acu(), kind: 'partitionWall' } as unknown as Construction
    expect(patchAnchorWorld(wall, 'front', { type: 'rect', x: 0, y: 0 })).toBeNull()
  })
})

describe('worldToPatchAnchor', () => {
  it('round-trips through the rotated model and drops the off-plane part', () => {
    const q = { x: 0, y: Math.SQRT1_2, z: 0, w: Math.SQRT1_2 }
    const m = acu(q)
    const patch = { type: 'rect', x: 0.35, y: 0.12 }
    const w = patchAnchorWorld(m, 'right', patch)!
    // Push the point off the face along its normal; it must still map back.
    const uv = worldToPatchAnchor(m, 'right', { x: w.x, y: w.y, z: w.z - 0.5 })!
    expect(uv.u).toBeCloseTo(0.35, 9)
    expect(uv.v).toBeCloseTo(0.12, 9)
  })

  it('honours a non-unit scale', () => {
    const m = acu()
    ;(m.geometry as { scale: unknown }).scale = { x: 2, y: 1, z: 3 }
    const patch = { type: 'circle', cx: 0.4, cy: 0.2 }
    const w = patchAnchorWorld(m, 'front', patch)!
    const uv = worldToPatchAnchor(m, 'front', w)!
    expect(uv.u).toBeCloseTo(0.4, 9)
    expect(uv.v).toBeCloseTo(0.2, 9)
  })
})
