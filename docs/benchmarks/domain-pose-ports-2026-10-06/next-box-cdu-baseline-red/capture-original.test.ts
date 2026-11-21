import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import * as THREE from 'three'
import { describe, it, expect, afterEach } from 'vitest'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { stateV4 } from '../src/legacy/cfdprojModules'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import { setLiquidModeActive } from '../src/liquid/liquidModeState'

const root = resolve(process.cwd(), '../../..')
const owned = resolve(root, '.codex-tmp/domain-box-cdu-baseline-20261006')
const enabled = process.env.DCDTUNI_CAPTURE_BOX_GOLDEN === '1'
const builders: Array<ReturnType<typeof createSceneBuilder>> = []
afterEach(() => { builders.splice(0).forEach(builder => builder.clear()); setLiquidModeActive(false) })

function bytes(array: ArrayBufferView) {
  return Buffer.from(array.buffer, array.byteOffset, array.byteLength).toString('hex')
}
describe.skipIf(!enabled)('explicit original-producer capture, never a golden-update fallback', () => {
  it('captures actual SceneBuilder buffers, complete STL and supported snapshot before the producer changes', () => {
    const sources = JSON.parse(readFileSync(resolve(owned, 'producer-before.json'), 'utf8')).files as Array<{ path: string; sha256: string }>
    for (const source of sources) {
      expect(createHash('sha256').update(readFileSync(resolve(root, source.path))).digest('hex'), source.path).toBe(source.sha256)
    }
    const input = JSON.parse(readFileSync(resolve(owned, 'cases-before.json'), 'utf8')) as {
      cases: Array<{ name: string; bucket: string; uid: string; model: Construction; source: unknown; synthetic: boolean }>
    }
    const output = input.cases.map(sample => {
      const raw = stateV4().getInitialStateV4() as unknown as CfdProject
      raw.uid = 'geometry-product-baseline'
      raw.meta = { ...raw.meta, projectName: sample.name }
      raw.constructions = { [sample.bucket]: { [sample.uid]: structuredClone(sample.model) } } as CfdProject['constructions']
      const project = stateV4().normalizeStateV4(raw) as unknown as CfdProject
      const normalizedBefore = JSON.stringify(project)
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
      expect(parts.length).toBeGreaterThan(0)
      const stl = builder.api.buildGroupedStlMap([sample.uid], true)
      const camera = new THREE.PerspectiveCamera(50, 16 / 9, .05, 1000)
      camera.position.set(9, 6, 8)
      const snapshot = buildSceneSnapshot({ root: globalGroup, project, camera,
        target: new THREE.Vector3(3, 1, 2), selectedUids: [sample.uid], timeSeconds: 7.25,
        helpers: host.registries.renderHelpers })
      const result = { ...sample, expected: { parts, stl, snapshot,
        normalizedInputSHA256: createHash('sha256').update(normalizedBefore).digest('hex'),
        normalizedAfterSHA256: createHash('sha256').update(JSON.stringify(project)).digest('hex') } }
      builder.clear()
      return result
    })
    const target = resolve(root, 'src/frontend/react/test/fixtures/boxGeometryProductParity.json')
    writeFileSync(target, JSON.stringify({ schemaVersion: 1,
      scope: 'Original actual SceneBuilder per-model harness, no WebGL; synthetic mutations labeled. Not full business/browser/Kit acceptance.',
      producerSources: sources, cases: output }, null, 2) + '\n')
    writeFileSync(resolve(owned, 'capture-result.json'), JSON.stringify({ capturedCases: output.length,
      target, sha256: createHash('sha256').update(readFileSync(target)).digest('hex'),
      totalParts: output.reduce((n, c) => n + c.expected.parts.length, 0) }, null, 2) + '\n')
  })
})
