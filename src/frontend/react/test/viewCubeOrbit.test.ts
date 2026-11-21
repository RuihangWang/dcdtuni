import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { DEFAULT_LIMITS, orbitAngles, orbitCameraByPixels } from '../src/interaction/viewCubeOrbit'

describe('orbitAngles', () => {
  it('turns by 2*PI when dragged one viewport height', () => {
    const { theta } = orbitAngles(0, Math.PI / 2, 900, 0, 900)
    expect(theta).toBeCloseTo(-2 * Math.PI, 10)
  })

  it('scales both axes by the HEIGHT, so a wide window does not turn faster', () => {
    const wide = orbitAngles(0, Math.PI / 2, 100, 0, 900)
    const narrow = orbitAngles(0, Math.PI / 2, 100, 0, 900)
    expect(wide.theta).toBeCloseTo(narrow.theta, 12)
    // The same pixel count vertically turns by the same angle.
    const down = orbitAngles(0, Math.PI / 2, 0, 100, 900)
    expect(Math.abs(down.phi - Math.PI / 2)).toBeCloseTo(Math.abs(wide.theta), 12)
  })

  it('applies the controls rotateSpeed', () => {
    const slow = orbitAngles(0, 1, 90, 0, 900, { ...DEFAULT_LIMITS, rotateSpeed: 0.5 })
    const fast = orbitAngles(0, 1, 90, 0, 900, { ...DEFAULT_LIMITS, rotateSpeed: 1 })
    expect(slow.theta).toBeCloseTo(fast.theta / 2, 12)
  })

  it('never lets the polar angle reach 0 or PI', () => {
    // Straight up would put the camera on its own up vector; the view degenerates.
    expect(orbitAngles(0, 0.01, 0, -10000, 900).phi).toBeGreaterThan(0)
    expect(orbitAngles(0, 3.13, 0, 10000, 900).phi).toBeLessThan(Math.PI)
  })

  it('honours azimuth and polar limits', () => {
    const limits = {
      ...DEFAULT_LIMITS,
      minAzimuthAngle: -0.1,
      maxAzimuthAngle: 0.1,
      minPolarAngle: 1,
      maxPolarAngle: 2,
    }
    expect(orbitAngles(0, 1.5, -900, 0, 900, limits).theta).toBeCloseTo(0.1, 12)
    expect(orbitAngles(0, 1.5, 0, 900, 900, limits).phi).toBeCloseTo(1, 12)
  })

  it('treats a zero height as one pixel rather than dividing by zero', () => {
    expect(Number.isFinite(orbitAngles(0, 1, 3, 0, 0).theta)).toBe(true)
  })
})

describe('orbitCameraByPixels', () => {
  function rig() {
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100)
    camera.position.set(0, 0, 10)
    const controls = {
      target: new THREE.Vector3(0, 0, 0),
      rotateSpeed: 1,
      minAzimuthAngle: -Infinity,
      maxAzimuthAngle: Infinity,
      minPolarAngle: 0,
      maxPolarAngle: Math.PI,
      updated: 0,
      update() {
        this.updated += 1
      },
    }
    return { camera, controls: controls as unknown as never, raw: controls }
  }

  it('keeps the distance to the target', () => {
    const { camera, controls, raw } = rig()
    orbitCameraByPixels(camera, controls, 120, 40, 900)
    expect(camera.position.distanceTo(raw.target)).toBeCloseTo(10, 8)
    expect(raw.updated).toBe(1)
  })

  it('a quarter-height horizontal drag turns 90 degrees about the target', () => {
    const { camera, controls } = rig()
    orbitCameraByPixels(camera, controls, 900 / 4, 0, 900)
    expect(camera.position.x).toBeCloseTo(-10, 6)
    expect(camera.position.z).toBeCloseTo(0, 6)
  })

  it('puts the up vector back to world up, so a top view does not orbit rolled', () => {
    const { camera, controls } = rig()
    camera.up.set(0, 0, -1)
    orbitCameraByPixels(camera, controls, 10, 0, 900)
    expect([camera.up.x, camera.up.y, camera.up.z]).toEqual([0, 1, 0])
  })
})
