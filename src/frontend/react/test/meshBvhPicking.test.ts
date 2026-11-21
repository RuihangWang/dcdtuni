import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { installMeshBvh } from '../src/legacy/firstPerson'

describe('first person collision and editor picking share Three.js', () => {
  it('builds and disposes a real collider tree while ordinary picking remains valid', () => {
    installMeshBvh()
    const collider = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial())
    const ordinary = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial())
    ordinary.position.x = 2
    ordinary.updateMatrixWorld()
    collider.geometry.computeBoundsTree()
    expect(collider.geometry.boundsTree).toBeDefined()
    expect(ordinary.geometry.boundsTree).toBeUndefined()

    const ray = new THREE.Raycaster(new THREE.Vector3(0, 0, 3), new THREE.Vector3(0, 0, -1))
    const colliderHits = ray.intersectObject(collider)
    expect(colliderHits[0]?.distance).toBeCloseTo(2.5)
    ray.set(new THREE.Vector3(2, 0, 3), new THREE.Vector3(0, 0, -1))
    const ordinaryHits = ray.intersectObject(ordinary)
    expect(ordinaryHits[0]?.distance).toBeCloseTo(2.5)
    expect(colliderHits[0]?.object).toBe(collider)
    expect(ordinaryHits[0]?.object).toBe(ordinary)

    collider.geometry.disposeBoundsTree()
    expect(collider.geometry.boundsTree).toBeNull()
    collider.geometry.dispose()
    ordinary.geometry.dispose()
  })

  it('repeated installation keeps one raycast wrapper', () => {
    installMeshBvh()
    const installed = THREE.Mesh.prototype.raycast
    installMeshBvh()
    expect(THREE.Mesh.prototype.raycast).toBe(installed)
    expect(document.querySelector('script[data-legacy-src*="three-mesh-bvh"]')).toBeNull()
  })
})
