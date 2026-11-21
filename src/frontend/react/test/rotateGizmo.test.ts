import { describe, expect, it } from 'vitest'
import {
  angleOnPlane,
  handlePosition,
  normaliseDelta,
  rotateAnchor,
  rotateQuaternion,
  snapDelta,
} from '../src/interaction/rotateGizmo'
import * as THREE from 'three'

const DEG = Math.PI / 180

describe('normaliseDelta', () => {
  it('brings a wrapped difference back into (-pi, pi]', () => {
    // Dragging past the ring's seam gives a raw difference near 2pi; used as-is
    // the model spins a full turn in one frame.
    expect(normaliseDelta(1.9 * Math.PI)).toBeCloseTo(-0.1 * Math.PI, 12)
    expect(normaliseDelta(-1.9 * Math.PI)).toBeCloseTo(0.1 * Math.PI, 12)
  })

  it('leaves a small delta alone', () => {
    expect(normaliseDelta(0.3)).toBeCloseTo(0.3, 12)
  })
})

describe('snapDelta', () => {
  it('does nothing with both snaps off', () => {
    expect(snapDelta(0.12345, false, false)).toBe(0.12345)
  })

  it('quantises to whole degrees with 旋转吸附 on', () => {
    expect(snapDelta(37.4 * DEG, false, true) / DEG).toBeCloseTo(37, 9)
    expect(snapDelta(37.6 * DEG, false, true) / DEG).toBeCloseTo(38, 9)
  })

  it('quantises to 15° with shift', () => {
    expect(snapDelta(20 * DEG, true, false) / DEG).toBeCloseTo(15, 9)
    expect(snapDelta(23 * DEG, true, false) / DEG).toBeCloseTo(30, 9)
  })

  it('applies shift FIRST, so the degree snap cannot move a 15° step', () => {
    // The other order would round the coarse step off its own multiple.
    expect(snapDelta(22.4 * DEG, true, true) / DEG).toBeCloseTo(15, 6)
  })
})

describe('angleOnPlane', () => {
  const c = { x: 1, y: 2, z: 3 }

  it('is zero on each axis’ own reference direction', () => {
    expect(angleOnPlane({ x: 1, y: 3, z: 3 }, c, 'x')).toBeCloseTo(0, 12)
    expect(angleOnPlane({ x: 1, y: 2, z: 4 }, c, 'y')).toBeCloseTo(0, 12)
    expect(angleOnPlane({ x: 2, y: 2, z: 3 }, c, 'z')).toBeCloseTo(0, 12)
  })

  it('increases anticlockwise in its own plane', () => {
    expect(angleOnPlane({ x: 1, y: 2, z: 4 }, c, 'x')).toBeCloseTo(Math.PI / 2, 12)
  })
})

describe('rotateAnchor', () => {
  it('leaves an anchor at the centre where it is', () => {
    const c = { x: 1, y: 2, z: 3 }
    expect(rotateAnchor(c, c, 'y', 1.234)).toEqual({ x: 1, y: 2, z: 3 })
  })

  it('carries a corner anchor a quarter turn about Y', () => {
    // A rack's anchor is a CORNER: turning about the anchor would swing the
    // rack across the room, so the gizmo turns about the OBB centre and moves
    // the anchor to match.
    const out = rotateAnchor({ x: 2, y: 0, z: 0 }, { x: 0, y: 0, z: 0 }, 'y', Math.PI / 2)
    expect(out.x).toBeCloseTo(0, 12)
    expect(out.z).toBeCloseTo(-2, 12)
  })

  it('keeps the axis component fixed', () => {
    expect(rotateAnchor({ x: 2, y: 7, z: 0 }, { x: 0, y: 0, z: 0 }, 'y', 0.7).y).toBe(7)
    expect(rotateAnchor({ x: 7, y: 2, z: 0 }, { x: 0, y: 0, z: 0 }, 'x', 0.7).x).toBe(7)
    expect(rotateAnchor({ x: 2, y: 0, z: 7 }, { x: 0, y: 0, z: 0 }, 'z', 0.7).z).toBe(7)
  })

  it('is reversible', () => {
    const a = { x: 3, y: -1, z: 2 }
    const c = { x: 1, y: 1, z: 1 }
    const there = rotateAnchor(a, c, 'z', 0.9)
    const back = rotateAnchor(there, c, 'z', -0.9)
    expect(back.x).toBeCloseTo(a.x, 12)
    expect(back.y).toBeCloseTo(a.y, 12)
  })
})

describe('rotateQuaternion', () => {
  const I = { x: 0, y: 0, z: 0, w: 1 }

  it('turns about the WORLD axis, pre-multiplying', () => {
    const q = rotateQuaternion(I, 'y', Math.PI / 2)
    expect(q.y).toBeCloseTo(Math.SQRT1_2, 12)
    expect(q.w).toBeCloseTo(Math.SQRT1_2, 12)
  })

  it('agrees with the toolbar buttons for the same angle', () => {
    // A 90° drag and the 90° button must leave a model in the same place, or
    // the two ways of rotating disagree.
    const drag = rotateQuaternion(I, 'z', Math.PI / 2)
    const button = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), Math.PI / 2)
    expect(drag.z).toBeCloseTo(button.z, 12)
    expect(drag.w).toBeCloseTo(button.w, 12)
  })
})

describe('handlePosition', () => {
  it('sits on the ring for every axis', () => {
    const c = new THREE.Vector3(1, 2, 3)
    for (const axis of ['x', 'y', 'z'] as const) {
      const p = handlePosition(c, 2, 0.7, axis)
      expect(p.distanceTo(c)).toBeCloseTo(2, 12)
    }
  })
})
