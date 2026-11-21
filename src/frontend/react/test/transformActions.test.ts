import { describe, expect, it } from 'vitest'
import { applyToTransform, centroid, orbitAnchor } from '../src/interaction/transformActions'

const IDENTITY = { x: 0, y: 0, z: 0, w: 1 }
const ONE = { x: 1, y: 1, z: 1 }

describe('applyToTransform', () => {
  it('rotates 90° about Y', () => {
    const out = applyToTransform(IDENTITY, ONE, { kind: 'rotate', axis: 'y', degrees: 90 })
    expect(out.quaternion.y).toBeCloseTo(Math.SQRT1_2, 12)
    expect(out.quaternion.w).toBeCloseTo(Math.SQRT1_2, 12)
    expect(out.scale).toEqual(ONE)
  })

  it('PRE-multiplies, so a second rotation is about the WORLD axis', () => {
    // Post-multiplying would turn about the model's own, already-rotated axis
    // and give a different orientation — the classic gimbal-order bug.
    const yaw = applyToTransform(IDENTITY, ONE, { kind: 'rotate', axis: 'y', degrees: 90 })
    const then = applyToTransform(yaw.quaternion, ONE, { kind: 'rotate', axis: 'x', degrees: 90 })
    expect(then.quaternion.x).toBeCloseTo(0.5, 12)
    expect(then.quaternion.y).toBeCloseTo(0.5, 12)
    expect(then.quaternion.z).toBeCloseTo(0.5, 12)
    expect(then.quaternion.w).toBeCloseTo(0.5, 12)
  })

  it('mirrors as a negative SCALE and leaves the orientation alone', () => {
    const out = applyToTransform(IDENTITY, ONE, { kind: 'mirror', axis: 'x' })
    expect(out.scale).toEqual({ x: -1, y: 1, z: 1 })
    expect(out.quaternion).toEqual(IDENTITY)
  })

  it('mirrors twice back to the original', () => {
    const once = applyToTransform(IDENTITY, ONE, { kind: 'mirror', axis: 'z' })
    const twice = applyToTransform(once.quaternion, once.scale, { kind: 'mirror', axis: 'z' })
    expect(twice.scale).toEqual(ONE)
  })

  it('reset clears both orientation and mirroring', () => {
    const turned = applyToTransform(IDENTITY, ONE, { kind: 'rotate', axis: 'z', degrees: 45 })
    const mirrored = applyToTransform(turned.quaternion, turned.scale, { kind: 'mirror', axis: 'y' })
    expect(applyToTransform(mirrored.quaternion, mirrored.scale, { kind: 'reset' })).toEqual({
      quaternion: IDENTITY,
      scale: ONE,
    })
  })
})

describe('orbitAnchor', () => {
  const pivot = { x: 0, y: 0, z: 0 }

  it('carries a co-rotated model AROUND the pivot', () => {
    // Rotating each model in place would spin eight racks on the spot instead
    // of turning the row.
    const out = orbitAnchor({ x: 2, y: 0, z: 0 }, pivot, { kind: 'rotate', axis: 'y', degrees: 90 })
    expect(out.x).toBeCloseTo(0, 12)
    expect(out.z).toBeCloseTo(-2, 12)
  })

  it('leaves the pivot itself where it is', () => {
    expect(orbitAnchor(pivot, pivot, { kind: 'rotate', axis: 'y', degrees: 37 })).toEqual({
      x: 0,
      y: 0,
      z: 0,
    })
  })

  it('reflects across the pivot when mirroring', () => {
    expect(orbitAnchor({ x: 3, y: 1, z: 0 }, pivot, { kind: 'mirror', axis: 'x' })).toEqual({
      x: -3,
      y: 1,
      z: 0,
    })
  })

  it('does not move an anchor on reset', () => {
    expect(orbitAnchor({ x: 3, y: 1, z: 2 }, pivot, { kind: 'reset' })).toEqual({
      x: 3,
      y: 1,
      z: 2,
    })
  })

  it('orbits about a NON-origin pivot', () => {
    const p = { x: 5, y: 0, z: 5 }
    const out = orbitAnchor({ x: 7, y: 0, z: 5 }, p, { kind: 'rotate', axis: 'y', degrees: 180 })
    expect(out.x).toBeCloseTo(3, 12)
    expect(out.z).toBeCloseTo(5, 12)
  })
})

describe('centroid', () => {
  it('averages the waypoints — the pivot a pipe turns about', () => {
    expect(centroid([{ x: 0, y: 0, z: 0 }, { x: 2, y: 4, z: 6 }])).toEqual({ x: 1, y: 2, z: 3 })
  })

  it('returns the origin for an empty route rather than NaN', () => {
    expect(centroid([])).toEqual({ x: 0, y: 0, z: 0 })
  })
})
