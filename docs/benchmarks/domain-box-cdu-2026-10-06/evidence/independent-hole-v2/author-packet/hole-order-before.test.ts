import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { geometryProductKernel } from '../src/domain/geometryProductKernel'

function bytes(array: ArrayBufferView) {
  return Buffer.from(array.buffer, array.byteOffset, array.byteLength).toString('hex')
}
const outerClockwise = [[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]
const holeClockwise = [[.5, .5], [.5, 1.5], [1.5, 1.5], [1.5, .5], [.5, .5]]
function pathInto(path: THREE.Path, ring: number[][]) {
  ring.forEach((p, i) => i === 0 ? path.moveTo(p[0]!, p[1]!) : path.lineTo(p[0]!, p[1]!))
}
function originalInstalledShape(outer: number[][]) {
  // The unchanged _buildSingleBoxFace carrier uses these exact APIs: Shape
  // paths -> installed ShapeGeometry(32) -> layout matrix -> F32 attributes.
  const shape = new THREE.Shape()
  pathInto(shape, outer)
  const hole = new THREE.Path()
  pathInto(hole, holeClockwise)
  shape.holes.push(hole)
  const geometry = new THREE.ShapeGeometry(shape, 32)
  const layout = new THREE.Matrix4().makeBasis(
    new THREE.Vector3(-1, 0, 0), new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, -1),
  ).setPosition(2, 0, 0)
  geometry.applyMatrix4(layout)
  return geometry
}
describe('supplied polygon context preserves installed ShapeGeometry hole order', () => {
  it.each([true, false])('keeps every structural buffer/index for clockwiseOuter=%s and clockwise hole', clockwise => {
    const outer = clockwise ? outerClockwise : outerClockwise.slice().reverse()
    const source = { uid: 'orientation-context', meta: { name: 'orientation-context' },
      geometry: { size: { x: 2, y: 2, z: 1 },
        faces: { front: { id: 'front', prefixEnabled: true, patches: [] } } } }
    const context = { faceIslands: { front: [{ poly: [[outer, holeClockwise]],
      refIds: ['hole-order'], name: 'hole-order', color: '#fff' }] } }
    const product = geometryProductKernel.createProduct(source, 'box', context)
    const patch = product.parts.find(part => !part.isBaseWall)!
    const geometry = originalInstalledShape(outer)
    try {
      for (const [array, attribute] of [
        [patch.positions, 'position'], [patch.normals, 'normal'], [patch.uvs, 'uv'],
      ] as const) {
        expect(bytes(array)).toBe(bytes(geometry.getAttribute(attribute).array))
      }
      expect(patch.indices.constructor.name).toBe(geometry.index!.array.constructor.name)
      expect(bytes(patch.indices)).toBe(bytes(geometry.index!.array))
    } finally {
      geometry.dispose()
    }
  })
})
