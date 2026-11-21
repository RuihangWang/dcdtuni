import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { applySceneTransform, applySceneTransformTo, sceneWrapperGroup } from '../src/interaction/sceneTransform'

const DEG = Math.PI / 180

describe('applySceneTransformTo', () => {
  it('rotates the wrapper by the requested angle', () => {
    const g = new THREE.Group()
    applySceneTransformTo(g, { kind: 'rotate', axis: 'y', degrees: 90 })
    expect(g.rotation.y).toBeCloseTo(90 * DEG, 10)
    expect(g.rotation.x).toBeCloseTo(0, 10)
  })

  it('accumulates rotations about the object\'s OWN axes', () => {
    // `rotateX` post-multiplies, so the second turn is about the axis the first
    // one left behind. A world-axis rotation would land somewhere else, and the
    // legacy uses the local one (STL_generate.html:16223).
    const g = new THREE.Group()
    applySceneTransformTo(g, { kind: 'rotate', axis: 'y', degrees: 90 })
    applySceneTransformTo(g, { kind: 'rotate', axis: 'x', degrees: 90 })
    const expected = new THREE.Group()
    expected.rotateY(90 * DEG)
    expected.rotateX(90 * DEG)
    expect(g.quaternion.angleTo(expected.quaternion)).toBeCloseTo(0, 10)
    // ... and it is NOT the same as turning about the world X.
    const world = new THREE.Group()
    world.quaternion.premultiply(
      new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), 90 * DEG),
    )
    world.quaternion.premultiply(
      new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), 90 * DEG),
    )
    expect(g.quaternion.angleTo(world.quaternion)).toBeGreaterThan(0.1)
  })

  it('mirrors by negating one scale component, not by rotating', () => {
    // The sign in `scale` is what the STL exporter reads to flip the winding;
    // a 180° rotation would look the same on screen and export inside-out.
    const g = new THREE.Group()
    applySceneTransformTo(g, { kind: 'mirror', axis: 'x' })
    expect(g.scale.toArray()).toEqual([-1, 1, 1])
    applySceneTransformTo(g, { kind: 'mirror', axis: 'x' })
    expect(g.scale.toArray()).toEqual([1, 1, 1])
    expect(g.rotation.toArray().slice(0, 3)).toEqual([0, 0, 0])
  })

  it('resets rotation and scale together', () => {
    const g = new THREE.Group()
    applySceneTransformTo(g, { kind: 'rotate', axis: 'z', degrees: 45 })
    applySceneTransformTo(g, { kind: 'mirror', axis: 'y' })
    applySceneTransformTo(g, { kind: 'reset' })
    expect(g.rotation.toArray().slice(0, 3)).toEqual([0, 0, 0])
    expect(g.scale.toArray()).toEqual([1, 1, 1])
  })

  it('leaves the POSITION alone — the room stays where it was', () => {
    const g = new THREE.Group()
    g.position.set(1, 2, 3)
    applySceneTransformTo(g, { kind: 'reset' })
    expect(g.position.toArray()).toEqual([1, 2, 3])
  })

  it('refreshes the world matrix, so the export sees the new transform', () => {
    const g = new THREE.Group()
    const child = new THREE.Object3D()
    g.add(child)
    applySceneTransformTo(g, { kind: 'mirror', axis: 'z' })
    expect(child.matrixWorld.determinant()).toBeLessThan(0)
  })
})

describe('sceneWrapperGroup', () => {
  const w = window as unknown as { __three?: { globalGroup?: THREE.Object3D } }

  it('is null before the viewport has mounted', () => {
    delete w.__three
    expect(sceneWrapperGroup()).toBeNull()
    expect(applySceneTransform({ kind: 'reset' })).toBe(false)
  })

  it('resolves the wrapper as globalGroup\'s PARENT', () => {
    // globalGroup is kept at identity by the builders; the transform belongs on
    // the group above it, which the viewport calls `content`.
    const content = new THREE.Group()
    const globalGroup = new THREE.Group()
    content.add(globalGroup)
    w.__three = { globalGroup }
    expect(sceneWrapperGroup()).toBe(content)
    expect(applySceneTransform({ kind: 'rotate', axis: 'y', degrees: 90 })).toBe(true)
    expect(content.rotation.y).toBeCloseTo(90 * DEG, 10)
    expect(globalGroup.rotation.y).toBe(0)
    delete w.__three
  })
})
