import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
// Import precedent: test/helperScale.test.ts — geometryBuilder pulls in
// shared/js/geometry_pure.js for its side effect (window.geometryPure).
import { geometryPure } from '../src/legacy/geometryBuilder'
import { registerHelper, unregisterHelper } from '../src/geometry/helperRegistry'
import { buildNormalHelper, normalHelperSpec } from '../src/geometry/previewNormalHelper'
import { buildAiBox } from '../src/postprocess/ppRenderer'

/**
 * WP-S3: viewport helpers are UNIT geometries whose scale belongs to the
 * shared registry in geometry_pure.js. `registerHelper(mesh, dataRadius)`
 * hands a mesh to `syncHelperScales(camera)` — called once per frame by the
 * Viewport render loop — which draws it at `helperDisplayRadius(dataRadius,
 * dist)`: the data radius at normal and far distances, shrinking linearly
 * once the camera comes closer than dataRadius / 0.03, with a 1.5 mm floor.
 *
 * These tests pin the React side of that contract: the converted builders
 * emit unit spheres scaled to their legacy radius, registration drives the
 * per-frame scale, dispose releases the entry, and a missing shared layer
 * degrades to "caller keeps its own scale" instead of throwing.
 */

const pure = () =>
  geometryPure() as unknown as {
    helperDisplayRadius(dataRadius: number, dist: number): number
    syncHelperScales(camera: THREE.Camera): void
  }

const cameraAt = (x: number, y: number, z: number) => {
  const camera = new THREE.PerspectiveCamera()
  camera.position.set(x, y, z)
  return camera
}

describe('the shared scale registry through helperRegistry', () => {
  it('shrinks a registered helper as the camera closes in, and stops at the data radius', () => {
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 8, 8))
    mesh.scale.setScalar(0.05)
    expect(registerHelper(mesh, 0.05)).toBe(true)
    try {
      // 1 m away: 0.03 of distance binds under the 50 mm data radius.
      pure().syncHelperScales(cameraAt(0, 0, 1))
      expect(mesh.scale.x).toBeCloseTo(0.03, 12)
      // 10 m away: the data radius itself — the historic look, unchanged.
      pure().syncHelperScales(cameraAt(0, 0, 10))
      expect(mesh.scale.x).toBeCloseTo(0.05, 12)
      // Degeneracy floor: never zero, never negative.
      pure().syncHelperScales(cameraAt(0, 0, 0))
      expect(mesh.scale.x).toBeCloseTo(0.0015, 12)
    } finally {
      unregisterHelper(mesh)
    }
    // Released: the sync no longer touches it.
    const frozen = mesh.scale.x
    pure().syncHelperScales(cameraAt(0, 0, 5))
    expect(mesh.scale.x).toBe(frozen)
  })

  it('reports false without the shared layer, so callers keep their own scale writes', () => {
    const w = window as unknown as { geometryPure?: unknown }
    const saved = w.geometryPure
    delete w.geometryPure
    try {
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 8, 8))
      expect(registerHelper(mesh, 0.05)).toBe(false)
      expect(() => unregisterHelper(mesh)).not.toThrow()
    } finally {
      w.geometryPure = saved
    }
  })
})

describe('the AI box centre sphere', () => {
  it('is a UNIT sphere at the legacy 60 mm data radius, registered until dispose', () => {
    const built = buildAiBox([0, 0, 0], [2, 4, 6])
    const sphere = built.object.children.find(
      (c) => (c.userData as { ppAiPart?: string }).ppAiPart === 'centre',
    ) as THREE.Mesh<THREE.SphereGeometry>
    expect(sphere.geometry.parameters.radius).toBe(1)
    expect(sphere.scale.x).toBeCloseTo(0.06, 12)

    // Registered: a sync from 1 m away takes the scale below the data radius
    // (the centre sits at (1, 2, 3); the camera is 1 m off it).
    pure().syncHelperScales(cameraAt(1, 2, 4))
    expect(sphere.scale.x).toBeCloseTo(pure().helperDisplayRadius(0.06, 1), 12)

    // dispose() unregisters — the sync stops touching the disposed mesh.
    built.dispose()
    const frozen = sphere.scale.x
    pure().syncHelperScales(cameraAt(1, 2, 10))
    expect(sphere.scale.x).toBe(frozen)
  })
})

describe('the preview normal helper tip', () => {
  it('is a UNIT sphere scaled to the spec tip radius', () => {
    const plate = new THREE.PlaneGeometry(2, 2)
    plate.rotateX(-Math.PI / 2)
    plate.computeBoundingBox()
    const spec = normalHelperSpec(plate, new THREE.Vector3(0, 1, 0))
    const group = buildNormalHelper(spec)
    try {
      const tip = group.children.find((o) => (o as THREE.Mesh).isMesh) as THREE.Mesh<
        THREE.SphereGeometry
      >
      expect(tip.geometry.parameters.radius).toBe(1)
      expect(tip.scale.x).toBeCloseTo(spec.tipRadius, 12)
    } finally {
      // clearPreviewNormalHelper is the production teardown; unregistering
      // here keeps the shared registry free of test debris.
      for (const child of group.children) unregisterHelper(child)
    }
  })
})
