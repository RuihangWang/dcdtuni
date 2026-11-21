import * as THREE from 'three'
import { expect, it } from 'vitest'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'

function sceneWith(aoMap: THREE.Texture) {
  const root = new THREE.Group()
  const material = new THREE.MeshStandardMaterial({ color: 0x707783 })
  material.aoMap = aoMap
  material.aoMapIntensity = 0.5
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), material)
  mesh.userData = { modelId: 'rack-1', cabinetInterior: 'lining' }
  root.add(mesh)
  root.updateMatrixWorld(true)
  return buildSceneSnapshot({ root, project: { uid: 'p', name: 'cabinet' } })
}

it("exports a cabinet-interior surface without its occlusion map: Kit's RTX occludes the dome itself", () => {
  const map = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1)
  map.userData.dcdtuniCabinetOcclusion = true
  const packet = sceneWith(map)
  expect(packet.primitives).toHaveLength(1)
  expect(JSON.stringify(packet.primitives[0]!.material)).not.toMatch(/ao/i)
})

it('still refuses any other ambient-occlusion map rather than drop it silently', () => {
  expect(() => sceneWith(new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1)))
    .toThrow('Unsupported material texture aoMap')
})
