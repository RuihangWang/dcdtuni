import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { batchedRenderPass, buildStaticBatch } from '../src/geometry/staticBatch'

/** A room of `n` boxes sharing one material, each with its own edge set. */
function room(n = 4) {
  const scene = new THREE.Scene()
  const parent = new THREE.Group()
  scene.add(parent)
  const material = new THREE.MeshStandardMaterial({ color: 0x808080 })
  const geometry = new THREE.BoxGeometry(1, 1, 1)
  const roots: THREE.Group[] = []
  const meshes: THREE.Mesh[] = []
  const edges: THREE.LineSegments[] = []
  for (let i = 0; i < n; i++) {
    const root = new THREE.Group()
    root.position.set(i * 2, 0, 0)
    const mesh = new THREE.Mesh(geometry, material)
    mesh.userData.modelId = `m${i}`
    const edge = new THREE.LineSegments(new THREE.EdgesGeometry(geometry),
      new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.5 }))
    root.add(mesh, edge)
    parent.add(root)
    roots.push(root)
    meshes.push(mesh)
    edges.push(edge)
  }
  scene.updateMatrixWorld(true)
  return { scene, parent, roots, meshes, edges, material }
}

function drawn(object: THREE.Object3D, camera: THREE.Camera): boolean {
  const frustum = new THREE.Frustum().setFromProjectionMatrix(
    new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse))
  return frustum.intersectsObject(object)
}

describe('static batch', () => {
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100)
  camera.position.set(3, 4, 12)
  camera.lookAt(3, 0, 0)
  camera.updateMatrixWorld(true)

  it('draws every mesh and edge set through one draw per look, the originals stay pickable', () => {
    const { parent, roots, meshes, edges } = room()
    const batch = buildStaticBatch(roots, parent)!
    expect(batch.draws).toBe(2)  // one BatchedMesh, one merged edge set
    expect(batch.meshes).toBe(4)
    expect(batch.edges).toBe(4)
    for (const o of [...meshes, ...edges]) {
      expect(o.visible).toBe(true)
      expect(drawn(o, camera)).toBe(false)  // out of the draw list only
    }
    const ray = new THREE.Raycaster(new THREE.Vector3(2, 0, 10), new THREE.Vector3(0, 0, -1))
    expect(ray.intersectObjects(meshes, false)[0]?.object).toBe(meshes[1])
    expect(parent.children.some(c => c.userData.isStaticBatch)).toBe(true)
    batch.dispose()
    for (const o of [...meshes, ...edges]) expect(drawn(o, camera)).toBe(true)
    expect(parent.children.some(c => c.userData.isStaticBatch)).toBe(false)
  })

  it('hands a mesh back while its material is swapped, and takes it again when restored', () => {
    const { parent, roots, meshes, material } = room()
    const batch = buildStaticBatch(roots, parent)!
    const highlight = material.clone()
    meshes[2]!.material = highlight  // what the selection highlight does
    batch.sync()
    expect(batch.meshes).toBe(3)
    expect(drawn(meshes[2]!, camera)).toBe(true)
    meshes[2]!.material = material
    batch.sync()
    expect(batch.meshes).toBe(4)
    expect(drawn(meshes[2]!, camera)).toBe(false)
    batch.dispose()
  })

  it('follows a hidden model, a moved model and a rebuilt (detached) one', () => {
    const { parent, roots, meshes, edges } = room()
    const batch = buildStaticBatch(roots, parent)!
    roots[0]!.visible = false
    batch.sync()
    expect(batch.meshes).toBe(3)
    roots[0]!.visible = true
    batch.sync()
    expect(batch.meshes).toBe(4)
    expect(batch.edges).toBe(3)  // edges, once out, draw themselves until the next build
    expect(drawn(roots[0]!.children[1]!, camera)).toBe(true)
    // A moved mesh moves its instance; its edges leave the merged set for good.
    roots[1]!.position.y = 3
    roots[1]!.updateMatrixWorld(true)
    batch.sync()
    const batched = parent.children.find(c => c.userData.isStaticBatch)!.children
      .find(c => (c as THREE.BatchedMesh).isBatchedMesh) as THREE.BatchedMesh
    const m = new THREE.Matrix4()
    batched.getMatrixAt(1, m)
    expect(new THREE.Vector3().setFromMatrixPosition(m).y).toBeCloseTo(3)
    expect(batch.meshes).toBe(4)
    expect(batch.edges).toBe(2)
    expect(drawn(edges[1]!, camera)).toBe(true)
    // A rebuild removes the roots: nothing of them may stay drawn by the batch.
    parent.remove(roots[3]!)
    batch.sync()
    expect(batch.meshes).toBe(3)
    expect(drawn(meshes[3]!, camera)).toBe(true)
    batch.dispose()
  })

  it('keeps a batched original\'s matrices itself: they follow its parent and its own transform', () => {
    const { parent, roots, meshes } = room()
    const batch = buildStaticBatch(roots, parent)!
    const batched = parent.children.find(c => c.userData.isStaticBatch)!.children
      .find(c => (c as THREE.BatchedMesh).isBatchedMesh) as THREE.BatchedMesh
    const at = (i: number) => { const m = new THREE.Matrix4(); batched.getMatrixAt(i, m); return new THREE.Vector3().setFromMatrixPosition(m) }
    expect(meshes[2]!.matrixAutoUpdate).toBe(false)              // three no longer recomputes it every frame
    // the model moves (a gizmo drag moves the model's root); the scene renders, then the batch syncs
    roots[2]!.position.z = 5
    parent.parent!.updateMatrixWorld()
    batch.sync()
    expect(at(2).z).toBeCloseTo(5)
    expect(new THREE.Vector3().setFromMatrixPosition(meshes[2]!.matrixWorld).z).toBeCloseTo(5)
    const ray = new THREE.Raycaster(new THREE.Vector3(4, 0, 15), new THREE.Vector3(0, 0, -1))
    expect(ray.intersectObjects(meshes, false)[0]?.object).toBe(meshes[2])   // picked where it now is
    // its own transform changes (no setter sees that: the turn of full checks finds it within 30 syncs)
    meshes[0]!.position.y = 2
    for (let i = 0; i < 30; i++) batch.sync()
    expect(at(0).y).toBeCloseTo(2)
    // released (its material swapped) it updates as any object again
    meshes[3]!.material = new THREE.MeshStandardMaterial({ color: 0xff0000 })
    batch.sync()
    expect(meshes[3]!.matrixAutoUpdate).toBe(true)
    batch.dispose()
    expect(meshes.every(m => m.matrixAutoUpdate && m.matrixWorldAutoUpdate)).toBe(true)
  })

  it('reads a batched member as hidden only inside the viewport\'s render', () => {
    const { parent, roots, meshes } = room()
    const batch = buildStaticBatch(roots, parent)!
    expect(meshes[0]!.visible).toBe(true)
    expect(batchedRenderPass(() => meshes.map((m) => m.visible))).toEqual([false, false, false, false])
    meshes[1]!.material = new THREE.MeshStandardMaterial({ color: 0xff0000 })   // released: draws itself
    batch.sync()
    expect(batchedRenderPass(() => meshes.map((m) => m.visible))).toEqual([false, true, false, false])
    meshes[2]!.visible = false                                                      // the editor hides one
    batch.sync()
    expect(meshes[2]!.visible).toBe(false)
    batch.dispose()
    expect(batchedRenderPass(() => meshes.map((m) => m.visible))).toEqual([true, true, false, true])
  })

  it('merges U-slot guide strips (Line, not LineSegments) as segments', () => {
    const { parent, roots } = room(2)
    for (const root of roots) {
      const guide = new THREE.Line(new THREE.BufferGeometry().setFromPoints(
        [new THREE.Vector3(0, 0, 0), new THREE.Vector3(1, 0, 0), new THREE.Vector3(1, 1, 0)]),
        new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.15 }))
      guide.userData.isSlotBoundaryGuide = true
      root.add(guide)
    }
    parent.updateMatrixWorld(true)
    const batch = buildStaticBatch(roots, parent)!
    expect(batch.edges).toBe(4)
    const merged = parent.children.find(c => c.userData.isStaticBatch)!.children
      .filter(c => (c as THREE.LineSegments).isLineSegments) as THREE.LineSegments[]
    const guides = merged.find(l => (l.material as THREE.LineBasicMaterial).opacity === 0.15)!
    expect(guides.geometry.getAttribute('position').count).toBe(2 * 2 * 2)  // 2 strips x 2 segments
    batch.dispose()
  })

  it('hands meshes back when shadows re-mark their cast/receive flags', () => {
    const { parent, roots, meshes } = room()
    const batch = buildStaticBatch(roots, parent)!
    for (const mesh of meshes) { mesh.castShadow = true; mesh.receiveShadow = true }  // shadows on
    batch.sync()
    expect(batch.meshes).toBe(0)
    expect(meshes.every(m => drawn(m, camera))).toBe(true)  // they draw (and cast) themselves
    batch.dispose()
    const rebuilt = buildStaticBatch(roots, parent)!
    const batched = parent.children.find(c => c.userData.isStaticBatch)!.children
      .find(c => (c as THREE.BatchedMesh).isBatchedMesh)!
    expect([rebuilt.meshes, batched.castShadow, batched.receiveShadow]).toEqual([4, true, true])
    rebuilt.dispose()
  })

  it('leaves transparent meshes and helpers to draw themselves', () => {
    const { parent, roots, meshes } = room(3)
    const glass = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshStandardMaterial({ transparent: true, opacity: 0.4 }))
    const handle = new THREE.Mesh(new THREE.SphereGeometry(0.1), meshes[0]!.material as THREE.Material)
    handle.userData.isResizeHandle = true
    roots[0]!.add(glass, handle)
    parent.updateMatrixWorld(true)
    const batch = buildStaticBatch(roots, parent)!
    expect(batch.meshes).toBe(3)
    expect(drawn(glass, camera)).toBe(true)
    expect(drawn(handle, camera)).toBe(true)
    batch.dispose()
  })
  it('batches a faded (transparent) room, sorted per instance, and leaves a ghost on its layer alone', () => {
    const { parent, roots, meshes, material } = room(4)
    material.transparent = true
    material.opacity = 0.15
    material.depthWrite = false
    for (const m of meshes) m.renderOrder = 3
    meshes[3]!.layers.set(2)                                      // a ghost: not on the camera's layer
    const batch = buildStaticBatch(roots, parent)!
    expect(batch.meshes).toBe(3)
    const batched = parent.children.find(c => c.userData.isStaticBatch)!.children
      .find(c => (c as THREE.BatchedMesh).isBatchedMesh) as THREE.BatchedMesh
    expect(batched.sortObjects).toBe(true)
    expect(batched.renderOrder).toBe(3)
    expect(meshes[3]!.layers.mask).toBe(1 << 2)
    batch.dispose()
  })

  it('batches the selection highlight\'s look-alike clones again, with its own copy of their material', () => {
    const { parent, roots, meshes, material } = room()
    const before = buildStaticBatch(roots, parent)!
    // Select everything: each mesh gets its own tinted clone, as paintMeshHighlight does.
    for (const mesh of meshes) {
      const clone = material.clone()
      clone.emissive.setHex(0x224422)
      mesh.material = clone
    }
    before.sync()
    expect([before.meshes, before.releasedMeshes]).toEqual([0, 4])
    before.dispose()
    const batch = buildStaticBatch(roots, parent)!
    expect([batch.meshes, batch.draws]).toEqual([4, 2])
    const batched = parent.children.find(c => c.userData.isStaticBatch)!.children
      .find(c => (c as THREE.BatchedMesh).isBatchedMesh) as THREE.BatchedMesh
    const copy = batched.material as THREE.MeshStandardMaterial
    expect(meshes.some(m => m.material === copy)).toBe(false)
    expect(copy.emissive.getHex()).toBe(0x224422)
    // One clone's look changes in place (the active model brightens): it leaves.
    ;(meshes[1]!.material as THREE.MeshStandardMaterial).emissiveIntensity = 2
    batch.sync()
    expect([batch.meshes, batch.releasedMeshes]).toEqual([3, 1])
    expect(drawn(meshes[1]!, camera)).toBe(true)
    let disposed = false
    copy.addEventListener('dispose', () => { disposed = true })
    batch.dispose()
    expect(disposed).toBe(true)
  })

  it('keeps meshes of different looks apart, and draws a shared material as itself', () => {
    const { parent, roots, meshes, material } = room()
    const red = material.clone()
    red.color.setHex(0xff0000)
    meshes[0]!.material = red
    const batch = buildStaticBatch(roots, parent)!
    expect(batch.meshes).toBe(3)  // the red one alone is a group of one: left to draw itself
    const batched = parent.children.find(c => c.userData.isStaticBatch)!.children
      .find(c => (c as THREE.BatchedMesh).isBatchedMesh) as THREE.BatchedMesh
    expect(batched.material).toBe(material)
    expect(batched.userData.ownsMaterial).toBeUndefined()
    batch.dispose()
  })

  it('groups a material with a shader hook by object, not by look', () => {
    const { parent, roots, meshes, material } = room(2)
    const hooked = material.clone()
    hooked.onBeforeCompile = () => {}
    meshes[0]!.material = hooked
    meshes[1]!.material = hooked.clone()
    const batch = buildStaticBatch(roots, parent)!
    expect(batch?.meshes ?? 0).toBe(0)
    batch?.dispose()
  })
})
