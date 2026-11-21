import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import type { Construction } from '../src/schema/cfdproj'
import {
  ORIENTATION_BACK_COLOR,
  ORIENTATION_FRONT_COLOR,
  orientationContext,
  orientationHelperTabAllows,
  orientationHelperVisible,
  orientationMetrics,
} from '../src/geometry/orientationHelperVisual'

function surface(kind: string, size: { x: number; y: number; z: number }, extra?: Record<string, unknown>) {
  return {
    uid: 'm_1',
    kind,
    meta: { name: kind },
    geometry: {
      size,
      location: { x: 0, y: 0, z: 0 },
      quaternion: { x: 0, y: 0, z: 0, w: 1 },
      scale: { x: 1, y: 1, z: 1 },
      faces: { front: {}, back: {}, left: {}, right: {}, top: {}, bottom: {} },
    },
    ...extra,
  } as unknown as Construction
}

/** Rounds away the -0 that a quaternion multiply leaves behind. */
const axes = (v: THREE.Vector3) => v.toArray().map((n) => Math.round(n) || 0)

function rootAt(location = { x: 0, y: 0, z: 0 }): THREE.Object3D {
  const root = new THREE.Object3D()
  root.position.set(location.x, location.y, location.z)
  return root
}

describe('orientationMetrics', () => {
  it('matches the legacy arrow length for a 1 m wall', () => {
    // Measured in the legacy at :8005 on a default wall: tip at 0.35 m.
    expect(orientationMetrics(1).arrowLength).toBeCloseTo(0.35, 9)
  })

  it('matches the legacy arrow length for a 5 m floor, where the clamp binds', () => {
    // Measured in the legacy on a default 5x5 floor: tip at 1.4 m, not 1.75.
    expect(orientationMetrics(5).arrowLength).toBeCloseTo(1.4, 9)
  })

  it('never shrinks below 180 mm, so a thin sheet still shows a direction', () => {
    expect(orientationMetrics(0.05).arrowLength).toBeCloseTo(0.18, 9)
  })
})

describe('orientationContext', () => {
  it('puts a wall arrow on the front face, normal along -Z', () => {
    const ctx = orientationContext(surface('wall', { x: 1, y: 1, z: 0 }), rootAt())
    expect(ctx).toBeTruthy()
    expect(ctx!.faceId).toBe('front')
    // Legacy reference for the same model: centre (0.5, 0.5, 0), green tip at
    // (0.5, 0.5, +0.35) — so the green arrow runs along -normal.
    expect(ctx!.center.toArray()).toEqual([0.5, 0.5, 0])
    expect(axes(ctx!.normal)).toEqual([0, 0, -1])
    expect(ctx!.extent).toBe(1)
  })

  it('puts a floor arrow on the top face, normal along +Y', () => {
    const floor = surface('floor', { x: 5, y: 0, z: 5 }, {
      cooling: { defaultBoundaryType: 'wall' },
    })
    const ctx = orientationContext(floor, rootAt())
    expect(ctx!.faceId).toBe('top')
    // Legacy reference: centre (2.5, 0, 2.5), red tip at (2.5, +1.4, 2.5).
    expect(ctx!.center.toArray()).toEqual([2.5, 0, 2.5])
    expect(axes(ctx!.normal)).toEqual([0, 1, 0])
    expect(ctx!.extent).toBe(5)
  })

  it('flips with reverseSolidNormal, which is the only readout that flag has', () => {
    const wall = surface('wall', { x: 1, y: 1, z: 0 })
    ;(wall.geometry!.faces as unknown as Record<string, Record<string, unknown>>).front!
      .reverseSolidNormal = true
    const ctx = orientationContext(wall, rootAt())
    expect(axes(ctx!.normal)).toEqual([0, 0, 1])
  })

  it('follows the render root, so a scene-level transform is not applied twice', () => {
    const root = rootAt({ x: 3, y: 0, z: 0 })
    // A 90 degrees yaw on the root: the front normal swings from -Z to -X.
    root.quaternion.setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2)
    const ctx = orientationContext(surface('wall', { x: 1, y: 1, z: 0 }), root)
    expect(axes(ctx!.normal)).toEqual([-1, 0, 0])
    expect(ctx!.center.toArray().map((v) => Math.round(v * 100) / 100)).toEqual([3, 0.5, -0.5])
  })

  it('has nothing to say about a baffle, matching the legacy', () => {
    // Verified in the running legacy at :8005: selecting a default baffle
    // leaves no isWallFloorOrientationHelper in the scene.
    expect(orientationContext(surface('baffle', { x: 1, y: 1, z: 0 }), rootAt())).toBeNull()
  })

  it('has nothing to say about a wallBaffle floor, which is wetted on both sides', () => {
    const floor = surface('floor', { x: 5, y: 0, z: 5 }, {
      cooling: { defaultBoundaryType: 'wallBaffle' },
    })
    expect(orientationContext(floor, rootAt())).toBeNull()
  })

  it('needs a render root — an unbuilt model has no world position yet', () => {
    expect(orientationContext(surface('wall', { x: 1, y: 1, z: 0 }), null)).toBeNull()
  })
})

describe('orientationHelperVisible', () => {
  const wall = surface('wall', { x: 1, y: 1, z: 0 })

  it('shows for a single selected wall on the geometry tab', () => {
    expect(orientationHelperVisible(wall, ['m_1'], 'geometry', false)).toBe(true)
  })

  it('is geometry-tab only, so it stays out of mesh and solver screenshots', () => {
    expect(orientationHelperTabAllows('mesh')).toBe(false)
    expect(orientationHelperVisible(wall, ['m_1'], 'mesh', false)).toBe(false)
    expect(orientationHelperVisible(wall, ['m_1'], 'solver', false)).toBe(false)
  })

  it('goes away in STL preview, where the surfaces on screen are the export', () => {
    expect(orientationHelperVisible(wall, ['m_1'], 'geometry', true)).toBe(false)
  })

  it('needs exactly one selected model', () => {
    expect(orientationHelperVisible(wall, [], 'geometry', false)).toBe(false)
    expect(orientationHelperVisible(wall, ['m_1', 'm_2'], 'geometry', false)).toBe(false)
  })

  it('draws for the selected model, not some other eligible one', () => {
    expect(orientationHelperVisible(wall, ['m_9'], 'geometry', false)).toBe(false)
  })
})

describe('the arrow colours', () => {
  it('are the legacy pair, green away from the solid and red along it', () => {
    // Read off the running legacy helper: #36d47a on -normal, #e06c75 on +normal.
    expect(ORIENTATION_BACK_COLOR).toBe(0x36d47a)
    expect(ORIENTATION_FRONT_COLOR).toBe(0xe06c75)
  })
})
