import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
// Import precedent: test/faceDragAnchor.test.ts — geometryBuilder pulls in
// shared/js/geometry_pure.js for its side effect (window.geometryPure).
import { geometryPure } from '../src/legacy/geometryBuilder'

/**
 * WP-S: selection helpers must keep shrinking as the camera closes in.
 *
 * The shared rules live in js/geometry_pure.js and are consumed by BOTH
 * editors (React via src/geometry/anchorMarkers.ts + src/ui/resizeHandles.ts,
 * legacy via window.geometryPure in STL_generate.html):
 *   handleCubeSize(dist)      = clamp(dist * 0.018, 0.0015, 0.3)
 *   markerDisplayRadius(k, d) = clamp(d * k, 0.0015, 0.35)   (point markers)
 *   anchorDisplayRadius(b, d) = markerDisplayRadius('anchor', d)
 * The old world-space floors (20 mm cube side) froze the helpers at a fixed
 * size once the camera came closer than ~1.1 m. A point marker holds a
 * constant size on screen; the anchor ball sits on the corner cube, smaller
 * than its half side.
 */
const pure = () =>
  geometryPure() as unknown as {
    handleCubeSize(dist: number): number
    anchorDisplayRadius(base: number, dist: number): number
    MARKER_SIZE: Record<string, number>
    markerDisplayRadius(kind: string | number, dist: number): number
    registerMarker(mesh: THREE.Object3D, kind: string | number): void
    unregisterHelper(mesh: THREE.Object3D): void
    syncHelperScales(camera: THREE.Camera): void
  }

describe('handleCubeSize', () => {
  it('is monotonically non-decreasing in camera distance', () => {
    let prev = 0
    for (let d = 0; d <= 100; d += 0.25) {
      const s = pure().handleCubeSize(d)
      expect(s).toBeGreaterThanOrEqual(prev)
      prev = s
    }
  })

  it('keeps shrinking below the old 20 mm floor when the camera is very close', () => {
    expect(pure().handleCubeSize(0.1)).toBeLessThan(0.004) // 0.1 * 0.018 = 1.8 mm
  })

  it('is ~54 mm at a normal 3 m viewing distance', () => {
    expect(pure().handleCubeSize(3)).toBeCloseTo(0.054, 12)
  })

  it('keeps the 300 mm far cap', () => {
    expect(pure().handleCubeSize(100)).toBe(0.3)
  })

  it('never degenerates to zero size', () => {
    expect(pure().handleCubeSize(0)).toBeGreaterThan(0)
    expect(pure().handleCubeSize(1e-6)).toBeGreaterThan(0)
  })
})

describe('point markers', () => {
  it('hold a constant size on screen: k x the camera distance', () => {
    for (const kind of Object.keys(pure().MARKER_SIZE)) {
      const k = pure().MARKER_SIZE[kind]!
      expect(pure().markerDisplayRadius(kind, 5)).toBeCloseTo(k * 5, 12)
      expect(pure().markerDisplayRadius(kind, 10)).toBeCloseTo(2 * pure().markerDisplayRadius(kind, 5), 12)
    }
  })

  it('never degenerate, never exceed the far cap, and survive a bad distance', () => {
    expect(pure().markerDisplayRadius('seed', 0)).toBe(0.0015)
    expect(pure().markerDisplayRadius('seed', 1e4)).toBe(0.35)
    expect(Number.isFinite(pure().markerDisplayRadius('seed', NaN))).toBe(true)
  })

  it('the anchor ball is the anchor marker, smaller than the cube it sits on', () => {
    for (const d of [0.2, 1, 3, 11.65, 16]) {
      expect(pure().anchorDisplayRadius(0.068, d)).toBe(pure().markerDisplayRadius('anchor', d))
      expect(pure().anchorDisplayRadius(0.068, d)).toBeLessThan(0.5 * pure().handleCubeSize(d))
    }
  })

  it('are re-sized every frame by the shared registry', () => {
    const camera = new THREE.PerspectiveCamera()
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(1))
    pure().registerMarker(mesh, 'sensor')
    try {
      camera.position.set(0, 0, 4)
      pure().syncHelperScales(camera)
      expect(mesh.scale.x).toBeCloseTo(pure().markerDisplayRadius('sensor', 4), 12)
      camera.position.set(0, 0, 20)
      pure().syncHelperScales(camera)
      expect(mesh.scale.x).toBeCloseTo(pure().markerDisplayRadius('sensor', 20), 12)
    } finally {
      pure().unregisterHelper(mesh)
    }
  })
})
