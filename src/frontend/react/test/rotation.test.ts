import { describe, expect, it } from 'vitest'
import { eulerDegToQuaternion, quaternionToEulerDeg } from '../src/projectStore'

/**
 * Rotation is the one place in the inspector where the number a user types and
 * the number stored in the file are different things. If the round trip is not
 * exact, opening a project and clicking away shifts every rotated model.
 */

const round4 = (v: number) => Math.round(v * 1e4) / 1e4

describe('euler <-> quaternion', () => {
  it('round-trips the yaw angles these models actually use', () => {
    for (const y of [0, 45, 90, 135, 180, -90, -45]) {
      const back = quaternionToEulerDeg(eulerDegToQuaternion({ x: 0, y, z: 0 }))
      expect(round4(back.y)).toBe(y === 180 ? 180 : y)
      expect(round4(back.x)).toBe(0)
      expect(round4(back.z)).toBe(0)
    }
  })

  it('round-trips a combined rotation', () => {
    const deg = { x: 20, y: -35, z: 15 }
    const back = quaternionToEulerDeg(eulerDegToQuaternion(deg))
    expect(round4(back.x)).toBeCloseTo(deg.x, 3)
    expect(round4(back.y)).toBeCloseTo(deg.y, 3)
    expect(round4(back.z)).toBeCloseTo(deg.z, 3)
  })

  it('produces the identity quaternion for no rotation', () => {
    expect(eulerDegToQuaternion({ x: 0, y: 0, z: 0 })).toEqual({ x: 0, y: 0, z: 0, w: 1 })
  })

  it('reads a stored -180 yaw as a whole number, not float dust', () => {
    // Real projects carry {x:0,y:0,z:0,w:-1} on baffles; naively that decodes to
    // 179.99999999999997 and the inspector shows a number nobody typed.
    const deg = quaternionToEulerDeg({ x: 0, y: 0, z: 0, w: -1 })
    expect(Number.isInteger(deg.x) && Number.isInteger(deg.y) && Number.isInteger(deg.z)).toBe(true)
  })

  it('stays finite at the gimbal-lock pitch instead of returning NaN', () => {
    const deg = quaternionToEulerDeg(eulerDegToQuaternion({ x: 90, y: 30, z: 0 }))
    expect(Number.isFinite(deg.x)).toBe(true)
    expect(Number.isFinite(deg.y)).toBe(true)
    expect(Number.isFinite(deg.z)).toBe(true)
    expect(round4(deg.x)).toBeCloseTo(90, 3)
  })
})
