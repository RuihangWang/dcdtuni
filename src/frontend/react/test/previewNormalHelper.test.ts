import * as THREE from 'three'
import { afterEach, describe, expect, it } from 'vitest'
import {
  buildNormalHelper,
  normalHelperSpec,
  PREVIEW_NORMAL_COLOR,
  updatePreviewNormalHelper,
} from '../src/geometry/previewNormalHelper'
import { resetPreviewForTest, selectPreviewRegion, setPreviewActive } from '../src/io/stlPreview'

/**
 * The yellow arrow that says which way a solid's normals point.
 *
 * STL preview mode exists to answer one question — will this solid face the
 * fluid the right way — and the arrow IS the answer. The legacy draws it on the
 * selected region: an `ArrowHelper` in 0xfff27a from the region's centre along
 * its average normal, with a matching tip sphere
 * (`updateStlPreviewNormalHelper`, STL_generate.html:7488). React drew nothing,
 * so a reversed solid looked exactly like a correct one.
 *
 * The sizes are the legacy's arithmetic, clamps included, because an arrow
 * scaled off a 10 m wall and one scaled off a 40 mm vent have to both be
 * visible and not swallow the thing they point at.
 */

/** A unit square in the XZ plane, normals up. */
function plate(size = 1): THREE.BufferGeometry {
  const g = new THREE.PlaneGeometry(size, size)
  g.rotateX(-Math.PI / 2)
  g.computeBoundingBox()
  return g
}

const UP = new THREE.Vector3(0, 1, 0)

describe('normalHelperSpec', () => {
  it('scales the arrow off the region, clamped to the legacy band', () => {
    // 0.45 of the largest dimension, never below 0.15 and never above 1.2.
    expect(normalHelperSpec(plate(2), UP).arrowLength).toBeCloseTo(0.9, 6)
    expect(normalHelperSpec(plate(0.05), UP).arrowLength).toBeCloseTo(0.15, 6)
    expect(normalHelperSpec(plate(20), UP).arrowLength).toBeCloseTo(1.2, 6)
  })

  it('puts the tip sphere at the far end, sized with the arrow', () => {
    const spec = normalHelperSpec(plate(2), UP)
    expect(spec.tipRadius).toBeCloseTo(Math.min(0.08, spec.arrowLength * 0.14), 6)
    expect(spec.tip.y).toBeCloseTo(spec.center.y + spec.arrowLength, 6)
  })

  it('starts from the region’s centre, not the origin', () => {
    const g = plate(1)
    g.translate(3, 2, 1)
    g.computeBoundingBox()
    expect(normalHelperSpec(g, UP).center.toArray()).toEqual([3, 2, 1])
  })

  it('falls back to Y-up for a degenerate normal', () => {
    // A normal that cancels out is not a reason to draw nothing: the legacy
    // points up instead (`:7498`, changed to Y-up in 2026-04).
    expect(normalHelperSpec(plate(1), new THREE.Vector3(0, 0, 0)).normal.toArray()).toEqual([
      0, 1, 0,
    ])
  })

  it('normalises whatever it is handed', () => {
    const spec = normalHelperSpec(plate(1), new THREE.Vector3(0, 5, 0))
    expect(spec.normal.length()).toBeCloseTo(1, 6)
  })
})

describe('buildNormalHelper', () => {
  it('is an arrow and a tip sphere, in the legacy’s yellow', () => {
    const group = buildNormalHelper(normalHelperSpec(plate(2), UP))
    expect(group.userData.isPreviewNormalHelper).toBe(true)
    const arrow = group.children.find((o) => (o as THREE.ArrowHelper).isObject3D && o.type === 'ArrowHelper')
    const sphere = group.children.find((o) => (o as THREE.Mesh).isMesh)
    expect(arrow).toBeDefined()
    expect(sphere).toBeDefined()
    const mat = (sphere as THREE.Mesh).material as THREE.MeshPhongMaterial
    expect(`#${mat.color.getHexString()}`).toBe(PREVIEW_NORMAL_COLOR)
  })
})

describe('updatePreviewNormalHelper', () => {
  let scene: THREE.Scene

  function sceneWithRegion(modelId: string, regionName: string): THREE.Mesh {
    scene = new THREE.Scene()
    const group = new THREE.Group()
    const mesh = new THREE.Mesh(plate(2), new THREE.MeshBasicMaterial())
    mesh.userData = { isPreviewRegion: true, modelId, regionName }
    group.add(mesh)
    scene.add(group)
    return mesh
  }

  function helpers(): THREE.Object3D[] {
    const out: THREE.Object3D[] = []
    scene.traverse((o) => {
      if ((o.userData as { isPreviewNormalHelper?: boolean }).isPreviewNormalHelper) out.push(o)
    })
    return out
  }

  afterEach(() => {
    resetPreviewForTest()
  })

  it('draws one on the selected region, beside its mesh', () => {
    const mesh = sceneWithRegion('m_1', 'wall_1')
    setPreviewActive(true)
    selectPreviewRegion({ modelId: 'm_1', regionName: 'wall_1' })
    updatePreviewNormalHelper(scene)
    expect(helpers()).toHaveLength(1)
    // Parented to the region's own group, so it inherits the same transform:
    // the preview groups carry a world-to-local matrix, and a helper hung off
    // the scene root would sit wherever that matrix is not.
    expect(helpers()[0]!.parent).toBe(mesh.parent)
  })

  it('draws nothing for a region that is not selected', () => {
    sceneWithRegion('m_1', 'wall_1')
    setPreviewActive(true)
    selectPreviewRegion({ modelId: 'm_1', regionName: 'other_region' })
    updatePreviewNormalHelper(scene)
    expect(helpers()).toHaveLength(0)
  })

  it('clears the old one instead of stacking them up', () => {
    sceneWithRegion('m_1', 'wall_1')
    setPreviewActive(true)
    selectPreviewRegion({ modelId: 'm_1', regionName: 'wall_1' })
    updatePreviewNormalHelper(scene)
    updatePreviewNormalHelper(scene)
    updatePreviewNormalHelper(scene)
    expect(helpers()).toHaveLength(1)
  })

  it('takes it away when the selection is dropped', () => {
    sceneWithRegion('m_1', 'wall_1')
    setPreviewActive(true)
    selectPreviewRegion({ modelId: 'm_1', regionName: 'wall_1' })
    updatePreviewNormalHelper(scene)
    selectPreviewRegion(null)
    updatePreviewNormalHelper(scene)
    expect(helpers()).toHaveLength(0)
  })

  it('draws nothing at all outside preview mode', () => {
    sceneWithRegion('m_1', 'wall_1')
    setPreviewActive(false)
    selectPreviewRegion({ modelId: 'm_1', regionName: 'wall_1' })
    updatePreviewNormalHelper(scene)
    expect(helpers()).toHaveLength(0)
  })
})
