import * as THREE from 'three'

/**
 * Three draws a transparent DoubleSide material in two passes (back faces, then front) unless
 * `forceSinglePass` is set, and flags the material for a program re-derivation in each pass: every frame,
 * for every such material. Alibaba-hongchou's 399 perforated rack doors (each its own material) and its
 * polycarbonate panels re-derived their programs every frame: a quarter of the frame.
 *
 * The two passes only matter for a surface whose own back faces can lie behind its front faces. A flat
 * sheet has none, so for a planar mesh one pass draws exactly the same pixels; those are set to one pass
 * here. A translucent volume (a glass box, a curved panel) keeps both passes.
 */
export function singlePassSheets(roots: Iterable<THREE.Object3D | null | undefined>): number {
  let changed = 0
  for (const root of roots) {
    root?.traverse((o) => {
      const mesh = o as THREE.Mesh
      if (!mesh.isMesh) return
      const material = mesh.material
      if (!material || Array.isArray(material)) return
      if (!material.transparent || material.side !== THREE.DoubleSide || material.forceSinglePass) return
      if (!isPlanar(mesh.geometry)) return
      material.forceSinglePass = true
      changed += 1
    })
  }
  return changed
}

const n0 = new THREE.Vector3()
const n = new THREE.Vector3()

/** Every vertex normal parallel to the first (a sheet seen from either side), or no normals and coplanar points. */
export function isPlanar(geometry: THREE.BufferGeometry | undefined): boolean {
  const normal = geometry?.getAttribute('normal')
  const position = geometry?.getAttribute('position')
  if (!position || position.count < 3) return false
  if (normal && normal.count === position.count) {
    n0.fromBufferAttribute(normal, 0)
    if (n0.lengthSq() < 1e-12) return false
    n0.normalize()
    for (let i = 1; i < normal.count; i++) {
      n.fromBufferAttribute(normal, i)
      const len = n.length()
      if (len < 1e-12 || Math.abs(n.dot(n0)) < 0.9999 * len) return false
    }
    // parallel normals: also one plane, not a stack of parallel faces (a thin box's two big faces)
  } else {
    const a = new THREE.Vector3().fromBufferAttribute(position, 0)
    const b = new THREE.Vector3().fromBufferAttribute(position, 1)
    const c = new THREE.Vector3()
    let found = false
    for (let i = 2; i < position.count && !found; i++) {
      c.fromBufferAttribute(position, i)
      n0.subVectors(b, a).cross(n.subVectors(c, a))
      found = n0.lengthSq() > 1e-18
    }
    if (!found) return false
    n0.normalize()
  }
  const p0 = new THREE.Vector3().fromBufferAttribute(position, 0)
  const d0 = n0.dot(p0)
  geometry!.computeBoundingSphere()
  const tol = 1e-5 * Math.max(1e-3, geometry!.boundingSphere?.radius ?? 1)
  const p = new THREE.Vector3()
  for (let i = 1; i < position.count; i++) {
    p.fromBufferAttribute(position, i)
    if (Math.abs(n0.dot(p) - d0) > tol) return false
  }
  return true
}
