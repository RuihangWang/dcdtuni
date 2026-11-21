import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { buildSceneSnapshot, POLYGON_OFFSET_DEPTH_M } from '../src/renderer/sceneSnapshot'

/**
 * Three layers coplanar surfaces with polygonOffset: every base face is pushed
 * back (factor 1) so a patch or an equipment skin drawn on the same plane wins
 * the depth test. USD has no polygon offset, so RTX resolved the tie arbitrarily
 * (the CDU front showed its cyan base face instead of the grey cabinet skin).
 * The snapshot turns the offset into geometry: a pushed-back face moves
 * POLYGON_OFFSET_DEPTH_M behind its own surface, a pulled-forward one in front.
 */

const project = { uid: 'offset-project', name: 'offset' }

function rootWith(...meshes: THREE.Object3D[]) {
  const root = new THREE.Group()
  const owner = new THREE.Group()
  owner.userData.modelId = 'model-uid'
  root.add(owner)
  meshes.forEach(m => owner.add(m))
  return root
}

function world(p: ReturnType<typeof buildSceneSnapshot>['primitives'][number]) {
  const m = new THREE.Matrix4().fromArray(p.matrix)
  const out: THREE.Vector3[] = []
  for (let i = 0; i < p.positions.length; i += 3) {
    out.push(new THREE.Vector3(p.positions[i], p.positions[i + 1], p.positions[i + 2]).applyMatrix4(m))
  }
  return out
}

describe('polygon offset becomes geometry in the render snapshot', () => {
  it('a pushed-back base face sits behind a coplanar skin', () => {
    const base = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshStandardMaterial({ polygonOffset: true, polygonOffsetFactor: 1 }))
    const skin = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial())
    const [b, s] = buildSceneSnapshot({ root: rootWith(base, skin), project }).primitives
    for (const v of world(b!)) expect(v.z).toBeCloseTo(-POLYGON_OFFSET_DEPTH_M, 9)
    for (const v of world(s!)) expect(v.z).toBe(0)
  })

  it('the depth is world metres under a scaled, rotated parent', () => {
    const base = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshStandardMaterial({ polygonOffset: true, polygonOffsetFactor: 1 }))
    base.scale.set(3, 3, 0.25)
    base.rotation.y = Math.PI / 2           // the face now looks along +x
    base.position.set(2, 0, 0)
    const [b] = buildSceneSnapshot({ root: rootWith(base), project }).primitives
    for (const v of world(b!)) expect(v.x).toBeCloseTo(2 - POLYGON_OFFSET_DEPTH_M, 9)
  })

  it('a pulled-forward overlay moves in front, and no offset leaves the vertices alone', () => {
    const decal = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }))
    const off = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshStandardMaterial({ polygonOffset: false, polygonOffsetFactor: 1 }))
    const [d, o] = buildSceneSnapshot({ root: rootWith(decal, off), project }).primitives
    for (const v of world(d!)) expect(v.z).toBeCloseTo(POLYGON_OFFSET_DEPTH_M, 9)
    expect(o!.positions).toEqual(Array.from(off.geometry.getAttribute('position').array as Float32Array))
  })

  it('a back-side material pushes along the side it shows', () => {
    const base = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshStandardMaterial({ side: THREE.BackSide, polygonOffset: true, polygonOffsetFactor: 1 }))
    const [b] = buildSceneSnapshot({ root: rootWith(base), project }).primitives
    for (const v of world(b!)) expect(v.z).toBeCloseTo(POLYGON_OFFSET_DEPTH_M, 9)
  })
})
