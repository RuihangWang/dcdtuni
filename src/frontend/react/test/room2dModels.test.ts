import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import type { Quat, Vec3 } from '../src/schema/cfdproj'
import {
  applyQuat,
  bottomCorners,
  boundsOf,
  hasRotation,
  hitModel,
  hitRotateRing,
  modelYaw,
  obbCentre,
  planFootprint,
  rotateRing,
  toPlanModel,
  type PlanModel,
} from '../src/room2d/room2dModels'
import { worldToCanvas, type View } from '../src/room2d/room2dView'

const VIEW: View = { offsetX: 0, offsetY: 0, scale: 40 }
const W = 800
const H = 600
const toCanvas = (x: number, y: number) => worldToCanvas(VIEW, 0, W, H, x, y)

function yaw(deg: number): Quat {
  const half = ((deg * Math.PI) / 180) / 2
  return { x: 0, y: Math.sin(half), z: 0, w: Math.cos(half) }
}

function model(patch: Partial<PlanModel> = {}): PlanModel {
  return {
    uid: 'm_1',
    kind: 'rack',
    name: 'Rack_1',
    enabled: true,
    isRoomBoundary: false,
    location: { x: 0, y: 0, z: 0 },
    size: { x: 0.6, y: 2, z: 1.2 },
    quaternion: { x: 0, y: 0, z: 0, w: 1 },
    scale: { x: 1, y: 1, z: 1 },
    rotatable: false,
    movable: false,
    frontIsInlet: true,
    ...patch,
  }
}

describe('applyQuat', () => {
  it('agrees with three.js for an arbitrary rotation', () => {
    // This is the one piece of maths written out rather than imported. If it
    // drifts from three.js, the plan draws a rack somewhere the 3D view does not.
    const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(0.3, -1.1, 0.7, 'YXZ'))
    const v: Vec3 = { x: 1.5, y: -0.25, z: 3 }
    const expected = new THREE.Vector3(v.x, v.y, v.z).applyQuaternion(q)
    const got = applyQuat(v, { x: q.x, y: q.y, z: q.z, w: q.w })
    expect(got.x).toBeCloseTo(expected.x, 12)
    expect(got.y).toBeCloseTo(expected.y, 12)
    expect(got.z).toBeCloseTo(expected.z, 12)
  })

  it('normalises first, so an unnormalised quaternion does not scale the model', () => {
    const doubled: Quat = { x: 0, y: 2 * Math.sin(Math.PI / 4), z: 0, w: 2 * Math.cos(Math.PI / 4) }
    const got = applyQuat({ x: 1, y: 0, z: 0 }, doubled)
    expect(Math.hypot(got.x, got.y, got.z)).toBeCloseTo(1, 12)
  })
})

describe('modelYaw', () => {
  it('reads a 180 degree turn as 180, not 90', () => {
    // w === 0 at 180 degrees. `q.w || 1` turns (0,1,0,0) into (0,1,0,1) — a
    // 90 degree turn — and the rack draws sideways.
    expect((modelYaw({ x: 0, y: 1, z: 0, w: 0 }) * 180) / Math.PI).toBeCloseTo(180, 9)
  })

  it('matches the quaternion it came from', () => {
    for (const deg of [0, 30, 90, 135]) {
      expect((modelYaw(yaw(deg)) * 180) / Math.PI).toBeCloseTo(deg, 9)
    }
  })
})

describe('hasRotation', () => {
  it('is false for identity and true once any vector part is set', () => {
    expect(hasRotation({ x: 0, y: 0, z: 0, w: 1 })).toBe(false)
    expect(hasRotation(yaw(1))).toBe(true)
  })
})

describe('bottomCorners', () => {
  it('walks the footprint anticlockwise from the anchor', () => {
    const corners = bottomCorners(model({ location: { x: 2, y: 0, z: 3 } }))
    expect(corners).toEqual([
      [2, 3],
      [2.6, 3],
      [2.6, 4.2],
      [2, 4.2],
    ])
  })

  it('turns the quad rather than growing an axis-aligned box', () => {
    const corners = bottomCorners(model({ quaternion: yaw(90) }))
    // A +90 degree yaw sends local +X to world -Z and local +Z to world +X.
    expect(corners[1]![0]).toBeCloseTo(0, 9)
    expect(corners[1]![1]).toBeCloseTo(-0.6, 9)
    expect(corners[3]![0]).toBeCloseTo(1.2, 9)
    expect(corners[3]![1]).toBeCloseTo(0, 9)
  })

  it('applies the geometry scale', () => {
    const bounds = boundsOf(bottomCorners(model({ scale: { x: 2, y: 1, z: 3 } })))
    expect(bounds.maxX).toBeCloseTo(1.2, 12)
    expect(bounds.maxY).toBeCloseTo(3.6, 12)
  })
})

describe('obbCentre', () => {
  it('is the anchor plus half the footprint when unrotated', () => {
    expect(obbCentre(model({ location: { x: 1, y: 0, z: 1 } }))).toEqual({ x: 1.3, y: 1.6 })
  })

  it('follows the rotation, so a turn happens in place', () => {
    const c = obbCentre(model({ quaternion: yaw(180) }))
    expect(c.x).toBeCloseTo(-0.3, 9)
    expect(c.y).toBeCloseTo(-0.6, 9)
  })
})

describe('rotateRing', () => {
  it('is null unless the model carries an enabled yaw ring', () => {
    expect(rotateRing(model(), toCanvas)).toBeNull()
    expect(rotateRing(model({ rotatable: true }), toCanvas)).not.toBeNull()
  })

  it('puts the handle CLOCKWISE on canvas as the yaw grows', () => {
    // worldToCanvas flips plan Y, so a counter-clockwise world rotation reads
    // clockwise on screen. Placing the handle with -sin sent it the opposite
    // way to the rack it was attached to.
    const ring = rotateRing(model({ rotatable: true, quaternion: yaw(90) }), toCanvas)!
    expect(ring.hx - ring.centre.x).toBeCloseTo(0, 9)
    expect(ring.hy - ring.centre.y).toBeCloseTo(ring.radius, 9)
  })

  it('never shrinks the ring below 25px, so a thin ACU is still grabbable', () => {
    const ring = rotateRing(
      model({ rotatable: true, size: { x: 0.05, y: 2, z: 0.05 } }),
      toCanvas,
    )!
    expect(ring.radius).toBe(25)
  })

  it('hits on the ring itself and on the handle dot', () => {
    const m = model({ rotatable: true })
    const ring = rotateRing(m, toCanvas)!
    expect(hitRotateRing([m], ring.hx, ring.hy, toCanvas)?.model.uid).toBe('m_1')
    expect(hitRotateRing([m], ring.centre.x + ring.radius, ring.centre.y, toCanvas)).not.toBeNull()
    expect(hitRotateRing([m], ring.centre.x, ring.centre.y, toCanvas)).toBeNull()
  })
})

describe('hitModel', () => {
  const rack = model({ uid: 'm_rack', location: { x: 1, y: 0, z: 1 } })

  it('picks a model under the cursor', () => {
    const p = toCanvas(1.3, 1.6)
    expect(hitModel([rack], p.x, p.y, toCanvas)?.uid).toBe('m_rack')
  })

  it('allows 5px of slack on an unrotated model', () => {
    const edge = toCanvas(1, 1.6)
    expect(hitModel([rack], edge.x - 4, edge.y, toCanvas)?.uid).toBe('m_rack')
    expect(hitModel([rack], edge.x - 9, edge.y, toCanvas)).toBeNull()
  })

  it('ignores walls, floors and the room shell', () => {
    const wall = model({ uid: 'm_wall', kind: 'wall', location: { x: 1, y: 0, z: 1 } })
    const shell = model({ uid: 'm_shell', isRoomBoundary: true, location: { x: 1, y: 0, z: 1 } })
    const off = model({ uid: 'm_off', enabled: false, location: { x: 1, y: 0, z: 1 } })
    const p = toCanvas(1.3, 1.6)
    expect(hitModel([wall, shell, off], p.x, p.y, toCanvas)).toBeNull()
  })

  it('takes the FIRST match, so a stack resolves the same way in both apps', () => {
    const a = model({ uid: 'a', location: { x: 1, y: 0, z: 1 } })
    const b = model({ uid: 'b', location: { x: 1, y: 0, z: 1 } })
    const p = toCanvas(1.3, 1.6)
    expect(hitModel([a, b], p.x, p.y, toCanvas)?.uid).toBe('a')
  })

  it('still hits a rotated model, whose screen box is not anchor-ordered', () => {
    const turned = model({ uid: 'm_turned', quaternion: yaw(90), location: { x: 2, y: 0, z: 2 } })
    const centre = obbCentre(turned)
    const p = toCanvas(centre.x, centre.y)
    expect(hitModel([turned], p.x, p.y, toCanvas)?.uid).toBe('m_turned')
  })
})

describe('planFootprint', () => {
  it('skips the quaternion maths entirely when there is no rotation', () => {
    expect(planFootprint(model({ location: { x: 5, y: 0, z: 5 } }))).toEqual([
      [5, 5],
      [5.6, 5],
      [5.6, 6.2],
      [5, 6.2],
    ])
  })
})

describe('toPlanModel', () => {
  it('defaults the rotate ring to yaw-on, as cloneModelRotateTool does', () => {
    const flat = toPlanModel({
      uid: 'u',
      meta: { name: 'n' },
      geometry: { location: { x: 0, y: 0, z: 0 }, size: { x: 1, y: 1, z: 1 } },
      rotateTool: { enabled: true },
    } as never)
    expect(flat.rotatable).toBe(true)
  })

  it('treats an explicitly disabled yaw as no ring', () => {
    const flat = toPlanModel({
      uid: 'u',
      meta: { name: 'n' },
      geometry: { location: { x: 0, y: 0, z: 0 }, size: { x: 1, y: 1, z: 1 } },
      rotateTool: { enabled: true, axes: { yaw: false } },
    } as never)
    expect(flat.rotatable).toBe(false)
  })

  it('reads a 180 degree quaternion without the w fallback', () => {
    const flat = toPlanModel({
      uid: 'u',
      meta: { name: 'n' },
      geometry: {
        location: { x: 0, y: 0, z: 0 },
        size: { x: 1, y: 1, z: 1 },
        quaternion: { x: 0, y: 1, z: 0, w: 0 },
      },
    } as never)
    expect(flat.quaternion.w).toBe(0)
  })
})
