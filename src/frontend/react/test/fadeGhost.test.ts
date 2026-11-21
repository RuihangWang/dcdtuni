import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { buildFadeGhost } from '../src/geometry/fadeGhost'

/**
 * The faded geometry as one ghost mesh (GTC: 7,720 transparent draws at 3 FPS).
 * It must stand in for exactly what was shown -- every shown triangle and edge
 * in its world place and material colour -- and give the scene back untouched.
 */
function scene() {
  const parent = new THREE.Group()
  parent.position.set(-5, 0, -2) // the room-centring offset globalGroup carries
  const rackA = new THREE.Group()
  rackA.position.set(1, 0, 0)
  const box = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1), new THREE.MeshStandardMaterial({ color: '#ff0000' }))
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(box.geometry), new THREE.LineBasicMaterial({ color: '#00ff00' }))
  const handle = new THREE.Mesh(new THREE.SphereGeometry(0.1), new THREE.MeshBasicMaterial())
  handle.userData.isAnchorMarker = true
  const hiddenPart = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial())
  hiddenPart.visible = false
  rackA.add(box, edges, handle, hiddenPart)
  const rackB = new THREE.Group()
  rackB.add(new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial({ color: '#0000ff' })))
  const preview = new THREE.Group()
  preview.name = 'meshPreview'
  preview.add(new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial()))
  const userHidden = new THREE.Group()
  userHidden.visible = false
  userHidden.add(new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial()))
  parent.add(rackA, rackB, preview, userHidden)
  return { parent, rackA, rackB, preview, userHidden, box }
}

describe('buildFadeGhost', () => {
  it('merges the shown meshes and edges, skipping helpers, hidden parts, the preview and hidden roots', () => {
    const { parent, rackA, rackB, preview, userHidden } = scene()
    const ghost = buildFadeGhost([rackA, rackB, preview, userHidden], parent, 0.15)!
    expect(ghost.triangles).toBe(12 + 2) // the box and rack B's plane
    expect(ghost.segments).toBe(12)
    expect(ghost.hidden).toEqual([rackA, rackB])
    expect([rackA.visible, rackB.visible, preview.visible, userHidden.visible]).toEqual([false, false, true, false])
    expect(ghost.group.parent).toBe(parent)
    const surface = ghost.group.getObjectByName('fadeGhostSurface') as THREE.Mesh
    const material = surface.material as THREE.MeshStandardMaterial
    expect([material.transparent, material.opacity, material.depthWrite, material.vertexColors]).toEqual([true, 0.15, false, true])
  })

  it('puts every vertex where the original was and colours it by its material', () => {
    const { parent, rackA, rackB, box } = scene()
    parent.updateMatrixWorld(true)
    const expected = box.geometry.getAttribute('position').clone().applyMatrix4(box.matrixWorld)
    const ghost = buildFadeGhost([rackA, rackB], parent, 0.3)!
    const surface = ghost.group.getObjectByName('fadeGhostSurface') as THREE.Mesh
    const pos = surface.geometry.getAttribute('position')
    const col = surface.geometry.getAttribute('color')
    const world = new THREE.Vector3()
    for (let k = 0; k < expected.count; k++) {
      world.fromBufferAttribute(pos, k).applyMatrix4(parent.matrixWorld)
      expect(world.distanceTo(new THREE.Vector3().fromBufferAttribute(expected, k))).toBeLessThan(1e-6)
      expect([col.getX(k), col.getY(k), col.getZ(k)]).toEqual([1, 0, 0])
    }
  })

  it('gives the roots back on dispose, and only those it hid', () => {
    const { parent, rackA, rackB, userHidden } = scene()
    const ghost = buildFadeGhost([rackA, rackB, userHidden], parent, 0.5)!
    ghost.setOpacity(0)
    expect(ghost.group.visible).toBe(false)
    ghost.dispose()
    expect([rackA.visible, rackB.visible, userHidden.visible]).toEqual([true, true, false])
    expect(parent.getObjectByName('fadeGhost')).toBeUndefined()
    expect(rackA.userData.hiddenByFadeGhost).toBeUndefined()
  })

  it('returns null with nothing to merge', () => {
    const parent = new THREE.Group()
    expect(buildFadeGhost([], parent, 0.2)).toBeNull()
  })
})
