import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { afterEach, describe, expect, it } from 'vitest'
import golden from './fixtures/boxGeometryProductParity.json'
import { DomainProject } from '../src/domain'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { buildSceneSnapshot, RENDER_SCENE_SCHEMA } from '../src/renderer/sceneSnapshot'
import { setLiquidModeActive } from '../src/liquid/liquidModeState'
import type { CfdProject } from '../src/schema/cfdproj'

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
// New display intent is asserted independently; frozen structural/export bytes remain exact.
const expectedDefaultGrid = {
  visible: true, spacingM: 0.1, divisions: 4000, extentM: 400,
  centerColor: [0.25818285291079235, 0.36625259558833256, 0.4793201830913402],
  lineColor: [0.10224173307914941, 0.14702726648767014, 0.20507873637973145],
  opacity: 0.6,
  matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -0.005, 0, 1],
}
function bytes(array: ArrayBufferView) {
  return Buffer.from(array.buffer, array.byteOffset, array.byteLength).toString('hex')
}
function projectFor(sample: Sample) {
  const raw = stateV4().getInitialStateV4() as unknown as CfdProject
  raw.uid = 'geometry-product-baseline'
  // The frozen oracle hashes the WHOLE normalised project, run defaults
  // included. They were recorded when a new project's steady endTime was 500;
  // that default became 1500 on 2026-10-22 (the return-air verdict needs 1000+
  // iterations), which is not geometry, so the input keeps the recorded value.
  ;(raw.runDefaults!.simulationConfig as { endTime?: number }).endTime = 500
  // The same for the 2026-10-27 defaults (residual thresholds 1e-3, the
  // return-air stop tolerance): solver settings, not geometry or material.
  {
    const sim = raw.runDefaults!.simulationConfig as Record<string, unknown>
    sim.residualControl = { p_rgh: 1e-4, U: 1e-5, T: 1e-5, k_epsilon: 1e-4 }
    delete sim.stopToleranceK
    // and the 2026-10-30 fast preset's T relaxation
    ;(sim.relaxationFactors as Record<string, number>).T = 0.7
    // and the non-orthogonal correction now on by default
    sim.nonOrthoCorrection = false
    sim.nNonOrthogonalCorrectors = 0
    Object.assign(sim.schemes as object, { laplacianScheme: 'Gauss linear limited 0.5', snGradScheme: 'limited 0.5' })
  }
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
// Rendering uploads colour to WebGL uniform3f and USD Color3f. Compare exact
// Float32 colour bits only; geometry, transforms and all other numbers stay exact.
function displayColourPrecision<T extends { material: { color: number[]; emissive: number[] } }>(primitives: T[]): T[] {
  return primitives.map(primitive => ({ ...primitive, material: { ...primitive.material,
    color: primitive.material.color.map(Math.fround), emissive: primitive.material.emissive.map(Math.fround) } } as T))
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
  const originalIds = new Set(sample.expected.snapshot.primitives.map(primitive => primitive.id))
  const detailSources = new Map<string, THREE.Mesh>()
  // Display-only skins never enter renderMeshes: the CDU's liquid detail and,
  // since dc54654ed (owner draft POWER-CABINET-UPS-VISUAL 2026-10-12), the
  // PDU/UPS power-cabinet detail on a BOX named UPS*.
  const visit = (object: THREE.Object3D, path: string) => {
    if ((object as THREE.Mesh).isMesh && (object.userData.liquidVisualDetail || object.userData.equipmentVisualDetail)) {
      const mesh = object as THREE.Mesh
      expect(mesh.userData).toMatchObject({ modelId: sample.uid, selectable: true, exportExcluded: true })
      expect(host.registries.renderMeshes).not.toContain(mesh)
      expect(Array.isArray(mesh.material)).toBe(false)
      expect((mesh as THREE.InstancedMesh).isInstancedMesh).not.toBe(true)
      detailSources.set(`${sample.uid}/${path}/0/0`, mesh)
    }
    object.children.forEach((child, index) => visit(child, `${path}.${index}`))
  }
  visit(globalGroup, 'root')
  const added = snapshot.primitives.filter(primitive => !originalIds.has(primitive.id))
  // Unknown additions, stale UIDs, missing details and reordered physical carriers fail.
  expect(snapshot.primitives.map(primitive => primitive.id)).toEqual([...originalIds, ...detailSources.keys()])
  expect(added.map(primitive => primitive.id)).toEqual([...detailSources.keys()])
  if (sample.bucket === 'cdus') {
    expect(added).toHaveLength(28)
    for (const name of ['generic-cdu-cabinet', 'generic-cdu-service-door', 'generic-cdu-hmi-screen',
      'generic-cdu-door-handle', 'generic-cdu-supply-stub', 'generic-cdu-return-stub']) {
      expect(added.filter(primitive => primitive.name === name)).toHaveLength(1)
    }
  } else if (sample.bucket === 'boxes' && /^\s*ups/i.test(String(sample.model.meta?.name ?? '')) &&
    // The skin never covers an editable CFD patch (pduEquipmentVisuals.ts).
    !Object.values(sample.model.geometry?.faces ?? {}).some(face => ((face as { patches?: unknown[] }).patches ?? []).length > 0)) {
    expect(added).toHaveLength(71)
    for (const primitive of added) expect(primitive.name).toMatch(/^generic-ups-/)
  } else expect(added).toHaveLength(0)
  for (const primitive of added) {
    const mesh = detailSources.get(primitive.id)!, geometry = mesh.geometry
    const material = mesh.material as THREE.MeshStandardMaterial & THREE.MeshBasicMaterial
    expect(primitive.modelUid).toBe(sample.uid)
    expect(primitive.name).toBe(mesh.name)
    expect(primitive.kind).toBe('triangles')
    expect(primitive.positions).toEqual(Array.from(geometry.getAttribute('position').array))
    expect(primitive.normals).toEqual(Array.from(geometry.getAttribute('normal').array))
    expect(material.side).not.toBe(THREE.BackSide)
    expect(primitive.indices).toEqual(geometry.index ? Array.from(geometry.index.array)
      : Array.from({ length: geometry.getAttribute('position').count }, (_, index) => index))
    expect(primitive.matrix).toEqual(globalGroup.matrixWorld.clone().invert().multiply(mesh.matrixWorld).toArray())
    const uv = Array.from(geometry.getAttribute('uv').array)
    const texture = material.map ?? material.normalMap ?? material.roughnessMap ?? material.metalnessMap
    if (texture) {
      if (texture.matrixAutoUpdate) texture.updateMatrix()
      for (let index = 0; index < uv.length; index += 2) {
        const transformed = new THREE.Vector2(uv[index], uv[index + 1]).applyMatrix3(texture.matrix)
        uv[index] = transformed.x; uv[index + 1] = texture.flipY ? transformed.y : 1 - transformed.y
      }
    }
    expect(primitive.uvs).toEqual(uv)
    expect(primitive.material.color.map(Math.fround)).toEqual(material.color.toArray().map(Math.fround))
    expect(primitive.material.opacity).toBe(material.opacity)
    if (mesh.name === 'generic-cdu-cabinet') {
      expect(primitive.material).toMatchObject({ roughness: 0.64, metalness: 0.08, unlit: false })
    }
    if (mesh.name === 'generic-cdu-hmi-screen') expect(primitive.material.unlit).toBe(true)
  }
  // The original snapshot remains the frozen structural carrier oracle after
  // every additional display primitive has passed the independent source checks.
  snapshot.primitives = displayColourPrecision(snapshot.primitives.filter(primitive => originalIds.has(primitive.id)))
  return { parts, stl, snapshot,
    normalizedInputSHA256: createHash('sha256').update(normalizedBefore).digest('hex'),
    normalizedAfterSHA256: createHash('sha256').update(JSON.stringify(project)).digest('hex') }
}
afterEach(() => {
  builders.splice(0).forEach(builder => builder.clear())
  setLiquidModeActive(false)
})

describe('original actual BOX/CDU carrier remains the numerical/export oracle', () => {
  it('retains the original frozen geometry/export fixture bytes', () => {
    const fixture = readFileSync(resolve(process.cwd(), 'test/fixtures/boxGeometryProductParity.json'))
    expect(createHash('sha256').update(fixture).digest('hex')).toBe('f37b50770d81ca7b9fae0b1f6fe3773d773280e98f712ac9e8ec13dcffd528ab')
  })
  for (const sample of golden.cases) {
    it(sample.name + ' keeps all F32 bytes, STL and structural snapshot while verifying owned display additions', () => {
      const expected = structuredClone(sample.expected)
      expected.snapshot.primitives = displayColourPrecision(expected.snapshot.primitives)
      // The frozen packets are render-scene v1. v2 only adds pool texture
      // references; these texture-free carriers (no WebGL) differ in the version
      // string alone, so the frozen bytes stay and the version is the current one.
      expect(expected.snapshot.textures).toEqual({})
      expect(actualCarrier(sample)).toEqual({ ...expected, snapshot: { ...expected.snapshot,
        schema: RENDER_SCENE_SCHEMA,
        view: { ...expected.snapshot.view, backgroundGrid: expectedDefaultGrid } } })
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
