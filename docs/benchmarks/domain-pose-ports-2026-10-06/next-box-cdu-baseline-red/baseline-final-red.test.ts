import { createHash } from 'node:crypto'
import * as THREE from 'three'
import { afterEach, describe, expect, it } from 'vitest'
import golden from './fixtures/boxGeometryProductParity.json'
import { DomainProject } from '../src/domain'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import { setLiquidModeActive } from '../src/liquid/liquidModeState'
import type { CfdProject, Construction } from '../src/schema/cfdproj'

interface NumericPart {
  faceId: string
  exportName: string
  refIds: string[]
  wallIndex?: number
  isBaseWall: boolean
  positions: Float32Array
  normals: Float32Array
  uvs: Float32Array
  indices: Uint16Array | Uint32Array
}
interface NumericProduct {
  schema: 'dcdtuni.geometry-product/v1'
  modelUid: string
  kind: 'box' | 'cdu'
  parts: NumericPart[]
}
type Sample = typeof golden.cases[number]
type ProductEntity = ReturnType<DomainProject['topLevel']>[number] & { geometryProduct(): NumericProduct }
const builders: Array<ReturnType<typeof createSceneBuilder>> = []
function bytes(array: ArrayBufferView) {
  return Buffer.from(array.buffer, array.byteOffset, array.byteLength).toString('hex')
}
function projectFor(sample: Sample) {
  const raw = stateV4().getInitialStateV4() as unknown as CfdProject
  raw.uid = 'geometry-product-baseline'
  raw.meta = { ...raw.meta, projectName: sample.name }
  raw.constructions = { [sample.bucket]: { [sample.uid]: structuredClone(sample.model) } } as CfdProject['constructions']
  return stateV4().normalizeStateV4(raw) as unknown as CfdProject
}
function entityFor(sample: Sample) {
  // Domain consumes the persisted record, without the legacy normalizer's
  // added kind. The registered bucket definition must select the product.
  const project = { schemaVersion: 5, uid: 'geometry-product-domain',
    constructions: { [sample.bucket]: { [sample.uid]: structuredClone(sample.model) } } } as unknown as CfdProject
  return new DomainProject(project).topLevel().find(entity => entity.uid === sample.uid)! as ProductEntity
}
function actualCarrier(sample: Sample) {
  const project = projectFor(sample), normalizedBefore = JSON.stringify(project)
  setLiquidModeActive(false)
  const globalGroup = new THREE.Group(), wrapperGroup = new THREE.Group()
  wrapperGroup.add(globalGroup)
  const host: SceneHost = { globalGroup, wrapperGroup, registries: emptyRegistries(),
    project: () => project, selectedUid: () => null, webglAvailable: false, previewMode: () => false }
  const builder = createSceneBuilder(host)
  builders.push(builder)
  builder.rebuildModels([sample.uid])
  globalGroup.updateMatrixWorld(true)
  const parts = host.registries.renderMeshes.map(object => {
    const mesh = object as THREE.Mesh
    const attribute = (name: string) => {
      const a = mesh.geometry.getAttribute(name) as THREE.BufferAttribute | undefined
      return a ? { itemSize: a.itemSize, normalized: a.normalized,
        arrayType: a.array.constructor.name, bytesLE: bytes(a.array), values: Array.from(a.array) } : null
    }
    return {
      modelUid: mesh.userData.modelId, faceId: mesh.userData.faceId,
      exportName: mesh.userData.exportName, refIds: mesh.userData.refIds ?? [],
      wallIndex: mesh.userData.wallIndex ?? null, isBaseWall: mesh.userData.isBaseWall,
      position: attribute('position'), normal: attribute('normal'), uv: attribute('uv'),
      index: mesh.geometry.index ? { arrayType: mesh.geometry.index.array.constructor.name,
        bytesLE: bytes(mesh.geometry.index.array), values: Array.from(mesh.geometry.index.array) } : null,
      matrixWorld: mesh.matrixWorld.toArray(),
    }
  })
  const stl = builder.api.buildGroupedStlMap([sample.uid], true)
  const camera = new THREE.PerspectiveCamera(50, 16 / 9, .05, 1000)
  camera.position.set(9, 6, 8)
  const snapshot = buildSceneSnapshot({ root: globalGroup, project, camera,
    target: new THREE.Vector3(3, 1, 2), selectedUids: [sample.uid], timeSeconds: 7.25,
    helpers: host.registries.renderHelpers })
  return { parts, stl, snapshot,
    normalizedInputSHA256: createHash('sha256').update(normalizedBefore).digest('hex'),
    normalizedAfterSHA256: createHash('sha256').update(JSON.stringify(project)).digest('hex') }
}
afterEach(() => {
  builders.splice(0).forEach(builder => builder.clear())
  setLiquidModeActive(false)
})

describe('original actual BOX/CDU carrier remains the numerical/export oracle', () => {
  for (const sample of golden.cases) {
    it(sample.name + ' keeps all F32 bytes, index order, complete STL and snapshot', () => {
      expect(actualCarrier(sample)).toEqual(sample.expected)
    })
  }
})

describe('BOX/CDU domain geometry owns plain structural products', () => {
  for (const sample of golden.cases) {
    it(sample.name + ' exposes complete owned buffers without mutating JSON', () => {
      const entity = entityFor(sample)
      const before = JSON.stringify(entity.record)
      expect(entity.uid).toBe(sample.uid)
      expect(entity.record).not.toHaveProperty('kind')
      expect(typeof entity.geometryProduct).toBe('function')
      const product = entity.geometryProduct()
      expect(product.schema).toBe('dcdtuni.geometry-product/v1')
      expect(product.modelUid).toBe(sample.uid)
      expect(product.kind).toBe(sample.bucket === 'boxes' ? 'box' : 'cdu')
      expect(product.parts).toHaveLength(sample.expected.parts.length)
      product.parts.forEach((part, i) => {
        const original = sample.expected.parts[i]!
        expect([part.faceId, part.exportName, part.refIds, part.wallIndex ?? null, part.isBaseWall]).toEqual(
          [original.faceId, original.exportName, original.refIds, original.wallIndex, original.isBaseWall])
        for (const [actual, attr] of [
          [part.positions, original.position], [part.normals, original.normal], [part.uvs, original.uv],
        ] as const) {
          expect(actual).toBeInstanceOf(Float32Array)
          expect(bytes(actual)).toBe(attr!.bytesLE)
        }
        expect(part.indices.constructor.name).toBe(original.index!.arrayType)
        expect(bytes(part.indices)).toBe(original.index!.bytesLE)
      })
      expect(JSON.stringify(entity.record)).toBe(before)
      const again = entity.geometryProduct()
      expect(again).not.toBe(product)
      const first = product.parts[0]!, second = again.parts[0]!
      expect(first.positions).not.toBe(second.positions)
      const value = second.positions[0]
      first.positions[0] = 123456
      expect(second.positions[0]).toBe(value)
      expect(JSON.stringify(entity.record)).toBe(before)
    })
  }

  it.each(['acus', 'racks', 'walls'])('does not silently BOX-convert unsupported %s', bucket => {
    const record = structuredClone(golden.cases[1]!.model)
    const raw = { schemaVersion: 5, uid: 'unsupported-product',
      constructions: { [bucket]: { [record.uid]: record } } } as unknown as CfdProject
    const entity = new DomainProject(raw).topLevel()[0]! as ProductEntity
    expect(typeof entity.geometryProduct).toBe('function')
    expect(() => entity.geometryProduct()).toThrow(/unsupported.*geometry|geometry.*unsupported/i)
  })
})
