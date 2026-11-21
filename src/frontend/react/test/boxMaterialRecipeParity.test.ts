import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import * as THREE from 'three'
import { describe, it, expect, vi } from 'vitest'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { pbrMaterials } from '../src/legacy/geometryBuilder'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import { setLiquidModeActive } from '../src/liquid/liquidModeState'
import type { CfdProject, Construction } from '../src/schema/cfdproj'

const root = resolve(process.cwd(), '../../..')
// Independently asserted new display intent; frozen material/geometry fixtures stay unchanged.
const expectedDefaultGrid = {
  visible: true, spacingM: 0.1, divisions: 4000, extentM: 400,
  centerColor: [0.25818285291079235, 0.36625259558833256, 0.4793201830913402],
  lineColor: [0.10224173307914941, 0.14702726648767014, 0.20507873637973145],
  opacity: 0.6,
  matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -0.005, 0, 1],
}

// Valid constant PNG sentinel. Controlled image/canvas serialization, never GPU/raster evidence.
const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aM1kAAAAASUVORK5CYII='
type Sample = { name: string; bucket: string; uid: string; model: Construction; source: unknown; synthetic: boolean }
function sha(value: string | Buffer) { return createHash('sha256').update(value).digest('hex') }
function numBits(n: number) { const b = Buffer.alloc(8); b.writeDoubleLE(n); return b.toString('hex') }
function image(url: string) { return { width: 1, height: 1, src: url, toDataURL: () => png } }
function texture(tex: THREE.Texture | null | undefined) {
  if (!tex) return null
  const im = tex.image as { src?: string; tag?: string } | undefined
  return { name: tex.name, resource: im?.src ?? im?.tag ?? 'controlled-procedural-canvas',
    repeat: tex.repeat.toArray(), offset: tex.offset.toArray(), center: tex.center.toArray(),
    rotation: tex.rotation, wrapS: tex.wrapS, wrapT: tex.wrapT, colorSpace: tex.colorSpace,
    flipY: tex.flipY, channel: tex.channel, cached: Boolean((tex as unknown as { _isCached?: boolean })._isCached) }
}
function material(mat: THREE.Material) {
  const m = mat as THREE.MeshStandardMaterial & THREE.MeshPhongMaterial
  const numbers = { opacity: m.opacity, side: m.side, polygonOffsetFactor: m.polygonOffsetFactor,
    polygonOffsetUnits: m.polygonOffsetUnits, shininess: m.shininess ?? null,
    roughness: m.roughness ?? null, metalness: m.metalness ?? null, alphaTest: m.alphaTest }
  return { type: mat.type, color: m.color.toArray(), colorHex: m.color.getHex(),
    emissive: m.emissive?.toArray() ?? null, specular: m.specular?.toArray() ?? null,
    ...numbers, numberBits: Object.fromEntries(Object.entries(numbers).map(([k, v]) => [k, v === null ? null : numBits(v)])),
    transparent: m.transparent, depthWrite: m.depthWrite, polygonOffset: m.polygonOffset,
    normalScale: m.normalScale?.toArray() ?? null, userData: structuredClone(m.userData),
    maps: { map: texture(m.map), normal: texture(m.normalMap), roughness: texture(m.roughnessMap), metalness: texture(m.metalnessMap) } }
}
type MeshMaterialCapture = { material: ReturnType<typeof material> | ReturnType<typeof material>[];
  userData: Record<string, unknown>; uvHex: string | null }
type ReadProduct = { materials: MeshMaterialCapture[]; snapshot: ReturnType<typeof buildSceneSnapshot> }
// Real WebGL uploads RGB/specular using uniform3f; USD display RGB uses Color3f.
// All other scalar values, UV bytes, geometry and transforms stay under exact comparison.
function materialColourPrecision(value: ReturnType<typeof material>) {
  return { ...value, color: value.color.map(Math.fround),
    emissive: value.emissive?.map(Math.fround) ?? null,
    specular: value.specular?.map(Math.fround) ?? null }
}
function projectFor(sample: Sample) {
  const raw = stateV4().getInitialStateV4() as unknown as CfdProject
  raw.uid = 'box-material-original-baseline'
  // Recorded with a new project's steady endTime at 500; the default became
  // 1500 on 2026-10-22, which is not material state (boxGeometryProduct.test.ts).
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
  raw.constructions = { [sample.bucket]: { [sample.uid]: structuredClone(sample.model) } } as CfdProject['constructions']
  return stateV4().normalizeStateV4(raw) as unknown as CfdProject
}

describe.each([false, true])('actual original BOX/CDU material parity sourceExtras=%s', (sourceExtras) => {
  it('preserves the frozen original creation, resource refs, UV bytes and real snapshot output', () => {
    // Re-frozen 2026-10-21 for the textures/materials.json PBR redesign: only
    // material fields changed (_tmp/campaign_M/regen_box_fixture.py checks the
    // geometry, UV bytes, transforms, ids and input hashes are unchanged); then
    // for render-scene v2: only the snapshot version and pool textures
    // (pool:<file>#<sha256> in place of sentinel bytes) changed
    // (_tmp/campaign_M/regen_box_fixture_v2.py); then 2026-10-22: only snapshot
    // positions of polygon-offset faces, each vertex moved exactly
    // POLYGON_OFFSET_DEPTH_M in world space (_tmp/campaign_M3/regen_box_fixture_v3.py).
    // Then 2026-10-22: only requestedResourceRefs, the ACU supply/return grilles moved
    // to the CC0 photo sets acu-louvre/ and acu-perforated/ (louvre/ no longer requested).
    // Then 2026-10-23: only `cached` of the face/patch photo textures, false -> true
    // (they are now shared across rebuilds like the PBR sets; a plain text swap of
    // the five `"cached": false` entries, nothing else changed).
    const binding = [{"path": "src/frontend/react/test/fixtures/boxMaterialRecipeBaseline.json", "bytes": 587931, "sha256": "e851702ea0f88327a661b59dcd55743f77fab809c99cc7c11fa882438e53071c"}, {"path": "src/frontend/react/test/fixtures/boxMaterialRecipeSourceBaseline.json", "bytes": 255397, "sha256": "ad2afec27cd6b5db560e458d05e6d7c44a4b57bcfe765df3acd0e711685d6f7e"}, {"path": "src/frontend/react/test/fixtures/boxGeometryProductParity.json", "bytes": 487470, "sha256": "f37b50770d81ca7b9fae0b1f6fe3773d773280e98f712ac9e8ec13dcffd528ab"}]
    const verify = () => binding.forEach(x => expect(sha(readFileSync(resolve(root, x.path))), x.path).toBe(x.sha256))
    verify()
    const target = resolve(root, sourceExtras ? 'src/frontend/react/test/fixtures/boxMaterialRecipeSourceBaseline.json' : 'src/frontend/react/test/fixtures/boxMaterialRecipeBaseline.json')
    const frozen = JSON.parse(readFileSync(target, 'utf8'))
    const samples = (sourceExtras ? frozen.cases : frozen.cases.filter((x: { actual: boolean; label: string }) => x.actual && x.label.endsWith('-library-present-unloaded'))).map((x: { sample: Sample }) => x.sample) as Sample[]
    const library = pbrMaterials()
    const globals = window as unknown as { pbrMaterials?: typeof library }
    const outputs: unknown[] = []
    const checkedProducts = new WeakMap<ReadProduct, { originalIds: Set<string>; detailMaterialIndices: Set<number> }>()
    const calls: Array<{ url: string; texture: THREE.Texture; onLoad: (t: THREE.Texture) => void }> = []
    vi.useFakeTimers()
    const context = new Proxy({}, { get(_target, key) {
      if (key === 'createLinearGradient' || key === 'createRadialGradient') return () => ({ addColorStop() {} })
      if (key === 'getImageData') return (_x: number, _y: number, w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4) })
      if (key === 'createImageData') return (w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4) })
      return () => undefined
    } })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => context as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(() => png)
    vi.spyOn(THREE.TextureLoader.prototype, 'load').mockImplementation(((url: string, onLoad?: (t: THREE.Texture) => void) => {
      const tex = new THREE.Texture(image(url) as unknown as HTMLCanvasElement)
      tex.name = url
      calls.push({ url, texture: tex, onLoad: onLoad ?? (() => undefined) })
      return tex
    }) as unknown as THREE.TextureLoader['load'])
    const capture = (sample: Sample, label: string, mode: 'present' | 'missing', direct?: (api: Record<string, unknown>, group: THREE.Group) => void,
      late = false) => {
      library._resetForTests(); calls.length = 0
      globals.pbrMaterials = mode === 'present' ? library : undefined
      const project = projectFor(sample), before = JSON.stringify(project)
      setLiquidModeActive(false)
      const group = new THREE.Group(), wrapperGroup = new THREE.Group(); wrapperGroup.add(group)
      const host: SceneHost = { globalGroup: group, wrapperGroup, registries: emptyRegistries(),
        project: () => project, selectedUid: () => null, webglAvailable: true, previewMode: () => false }
      const builder = createSceneBuilder(host)
      try {
        if (direct) direct(builder.api as unknown as Record<string, unknown>, group)
        else builder.rebuildModels([sample.uid])
        group.updateMatrixWorld(true)
        group.traverse(o => {
          if ((o as THREE.Mesh).isMesh && o.userData.isBaseWall === false && o.userData.geometryPartId) {
            expect(o.renderOrder).toBe(3)
            expect(((o as THREE.Mesh).material as THREE.Material).polygonOffsetFactor).toBe(0)
          }
        })
        const read = (stage: 'creation' | 'afterLateBinding') => {
          const mats: MeshMaterialCapture[] = [], materialSources: THREE.Object3D[] = []
          group.traverse(o => { const m = (o as THREE.Mesh).material; if (m) { materialSources.push(o); mats.push({
            userData: { modelId: o.userData.modelId ?? null, faceId: o.userData.faceId ?? null,
              refIds: o.userData.refIds ?? null, geometryPartId: o.userData.geometryPartId ?? null,
              isBaseWall: o.userData.isBaseWall ?? null, baseOpacity: o.userData.baseOpacity ?? null },
            material: Array.isArray(m) ? m.map(material) : material(m),
            uvHex: (o as THREE.Mesh).geometry?.getAttribute('uv') ? Buffer.from((o as THREE.Mesh).geometry.getAttribute('uv').array.buffer).toString('hex') : null }) } })
          const camera = new THREE.PerspectiveCamera(50, 16 / 9, .05, 1000); camera.position.set(9, 6, 8)
          const snapshot = buildSceneSnapshot({ root: group, project, camera, target: new THREE.Vector3(3, 1, 2),
            selectedUids: [sample.uid], timeSeconds: 7.25, helpers: host.registries.renderHelpers })
          expect(snapshot.view.backgroundGrid).toEqual(expectedDefaultGrid)
          const reference = frozen.cases.find((entry: { label: string }) => entry.label === label)[stage]
          const originalIds = new Set<string>(reference.snapshot.primitives.map((primitive: { id: string }) => primitive.id))
          const detailSources = new Map<string, THREE.Mesh>()
          // Display-only skins never enter renderMeshes: the CDU's liquid
          // detail and, since dc54654ed (owner draft POWER-CABINET-UPS-VISUAL
          // 2026-10-12), the PDU/UPS power-cabinet detail on a BOX named UPS*.
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
          visit(group, 'root')
          const added = snapshot.primitives.filter(primitive => !originalIds.has(primitive.id))
          // Unknown additions, stale UIDs, missing details and reordered physical carriers fail.
          expect(snapshot.primitives.map(primitive => primitive.id)).toEqual([...originalIds, ...detailSources.keys()])
          expect(added.map(primitive => primitive.id)).toEqual([...detailSources.keys()])
          if (sample.bucket === 'cdus' && mode === 'present' && !direct) {
            expect(added).toHaveLength(28)
            for (const name of ['generic-cdu-cabinet', 'generic-cdu-service-door', 'generic-cdu-hmi-screen',
              'generic-cdu-door-handle', 'generic-cdu-supply-stub', 'generic-cdu-return-stub']) {
              expect(added.filter(primitive => primitive.name === name)).toHaveLength(1)
            }
          } else if (sample.bucket === 'boxes' && /^\s*ups/i.test(String(sample.model.meta?.name ?? '')) && mode === 'present' && !direct &&
            // The skin never covers an editable CFD patch (pduEquipmentVisuals.ts).
            !Object.values(sample.model.geometry?.faces ?? {}).some(face => ((face as { patches?: unknown[] }).patches ?? []).length > 0)) {
            expect(added).toHaveLength(71)
            for (const primitive of added) expect(primitive.name).toMatch(/^generic-ups-/)
          } else expect(added).toHaveLength(0)
          const detailMaterialIndices = new Set<number>()
          for (const primitive of added) {
            const mesh = detailSources.get(primitive.id)!, geometry = mesh.geometry
            const liveMaterial = mesh.material as THREE.MeshStandardMaterial & THREE.MeshBasicMaterial
            expect(primitive.modelUid).toBe(sample.uid)
            expect(primitive.name).toBe(mesh.name)
            expect(primitive.kind).toBe('triangles')
            expect(primitive.positions).toEqual(Array.from(geometry.getAttribute('position').array))
            expect(primitive.normals).toEqual(Array.from(geometry.getAttribute('normal').array))
            expect(liveMaterial.side).not.toBe(THREE.BackSide)
            expect(primitive.indices).toEqual(geometry.index ? Array.from(geometry.index.array)
              : Array.from({ length: geometry.getAttribute('position').count }, (_, index) => index))
            expect(primitive.matrix).toEqual(group.matrixWorld.clone().invert().multiply(mesh.matrixWorld).toArray())
            const uv = Array.from(geometry.getAttribute('uv').array)
            const texture = liveMaterial.map ?? liveMaterial.normalMap ?? liveMaterial.roughnessMap ?? liveMaterial.metalnessMap
            if (texture) {
              if (texture.matrixAutoUpdate) texture.updateMatrix()
              for (let index = 0; index < uv.length; index += 2) {
                const transformed = new THREE.Vector2(uv[index], uv[index + 1]).applyMatrix3(texture.matrix)
                uv[index] = transformed.x; uv[index + 1] = texture.flipY ? transformed.y : 1 - transformed.y
              }
            }
            expect(primitive.uvs).toEqual(uv)
            expect(primitive.material.color.map(Math.fround)).toEqual(liveMaterial.color.toArray().map(Math.fround))
            expect(primitive.material.opacity).toBe(liveMaterial.opacity)
            if (mesh.name === 'generic-cdu-cabinet') {
              // The loaded roughness map rescales its scalar from the authored 0.64
              // to1 via the existing painted-steel recipe; it is independently
              // required here together with its real role, refs and normal scale.
              expect(primitive.material).toMatchObject({ roughness: stage === 'afterLateBinding' ? 1 : 0.64,
                metalness: 0.08, unlit: false })
              expect(liveMaterial.userData).toMatchObject({ pbrRole: 'painted-steel', pbrSet: 'painted-steel',
                pbrRoughness: 0.64, pbrNormalScale: 0.16 })
              if (stage === 'afterLateBinding') {
                const captured = material(liveMaterial)
                expect(captured.normalScale).toEqual([0.16, 0.16])
                expect(captured.maps.map?.resource).toBe('/textures/painted-steel/color.jpg')
                expect(captured.maps.normal?.resource).toBe('/textures/painted-steel/normal.jpg')
                expect(captured.maps.roughness?.resource).toBe('/textures/painted-steel/roughness.jpg')
              }
            }
            if (mesh.name === 'generic-cdu-hmi-screen') expect(primitive.material.unlit).toBe(true)
            const indices = materialSources.map((source, index) => source === mesh ? index : -1).filter(index => index >= 0)
            expect(indices).toHaveLength(1)
            const liveMaterialIndex = indices[0]!
            expect(mats[liveMaterialIndex]!.userData.modelId).toBe(sample.uid)
            expect(mats[liveMaterialIndex]!.material).toEqual(material(mesh.material as THREE.Material))
            detailMaterialIndices.add(liveMaterialIndex)
          }
          expect(detailMaterialIndices.size).toBe(added.length)
          const product = { materials: mats, snapshot }
          checkedProducts.set(product, { originalIds, detailMaterialIndices })
          return product
        }
        const creation = read('creation')
        let afterLateBinding: ReturnType<typeof read> | null = null
        if (late) {
          calls.filter(x => x.url.includes('/painted-steel/')).forEach(c => c.onLoad(c.texture))
          afterLateBinding = read('afterLateBinding')
        }
        outputs.push({ label, actual: !sample.synthetic && !direct, sample, mode,
          requestedResourceRefs: calls.map(c => c.url), creation, afterLateBinding,
          normalizedInputSha256: sha(before), normalizedAfterSha256: sha(JSON.stringify(project)) })
      } finally { builder.clear(); vi.clearAllTimers(); globals.pbrMaterials = library }
    }
    try {
      if (sourceExtras) {
        for (const sample of samples) capture(sample, sample.name, 'present')
      } else {
      for (const sample of samples) {
        capture(sample, sample.name + '-library-present-unloaded', 'present')
        capture(sample, sample.name + '-library-missing', 'missing')
      }
      capture(samples[0]!, 'actual-LiquidSingle-CDU-late-binding-controlled-sentinel', 'present', undefined, true)
      const base = samples.find(x => x.bucket === 'boxes')!
      type Spawn = (group: THREE.Group, geom: THREE.BufferGeometry, options: Record<string, unknown>) => THREE.Mesh
      for (const [label, kind, options] of [
        ['box-preset', 'box', { color: '#789abc', opacity: 1 }],
        ['photo-override', 'face-image:/controlled/photo.png', { color: '#aabbcc', opacity: .85, doubleSide: true, uvNormalize: { type: 'stretch' } }],
        ['box-opacity-below-threshold', 'box', { color: '#789abc', opacity: .9989 }],
        ['box-opacity-at-threshold', 'box', { color: '#789abc', opacity: .999 }],
        ['box-explicit-false-overlay', 'box', { color: '#789abc', opacity: .3, transparent: false, depthWrite: false, renderOrder: 3 }],
        ['box-tile-per-u', 'box', { color: '#789abc', opacity: 1, uvNormalize: { type: 'tile-per-u', uCount: 3 } }],
        ['box-tile-physical', 'box', { color: '#789abc', opacity: 1, uvNormalize: { type: 'tile-physical', tileSize: .3 } }],
        ['cdu-present-unloaded', 'cdu-body', { color: '#007a99', opacity: 1 }],
      ] as const) {
        capture({ ...base, synthetic: true }, label, 'present', (api, group) => {
          const mesh = (api.spawnRenderable as Spawn)(group, new THREE.PlaneGeometry(2, 1), { ...options, objectKind: kind,
            skipEdges: true, userData: { modelId: base.uid, exportName: 'controlledPlane' } })
          if (label === 'box-explicit-false-overlay') {
            expect(mesh.renderOrder).toBe(3)
            expect((mesh.material as THREE.Material).polygonOffsetFactor).toBe(0)
            expect((mesh.material as THREE.Material).depthWrite).toBe(false)
            expect((mesh.material as THREE.Material).transparent).toBe(false)
          }
        })
      }
      capture({ ...base, synthetic: true }, 'box-rounded-opacity-cache-call-history', 'present', (api, group) => {
        const spawn = api.spawnRenderable as Spawn
        const a = spawn(group, new THREE.PlaneGeometry(2, 1), { objectKind: 'box', color: '#abcdef', opacity: .997,
          transparent: true, skipEdges: true, userData: { modelId: base.uid, exportName: 'cacheA' } })
        const b = spawn(group, new THREE.PlaneGeometry(2, 1), { objectKind: 'box', color: '#abcdef', opacity: 1,
          transparent: false, skipEdges: true, userData: { modelId: base.uid, exportName: 'cacheB' } })
        expect(a.material).toBe(b.material)
      })
      }
      verify()
      const expected = JSON.parse(readFileSync(target, 'utf8')).cases
      if (process.env.DCDTUNI_MATERIAL_PARITY_OUT) writeFileSync(resolve(process.env.DCDTUNI_MATERIAL_PARITY_OUT, (sourceExtras ? 'parity-extra4-' : 'parity-original16-') + (process.env.DCDTUNI_MATERIAL_PARITY_ATTEMPT || '1') + '-actual.json'), JSON.stringify({cases:outputs},null,2)+'\n', {flag:'wx'})
      const normalizeRead = (product: ReadProduct, actual: boolean) => {
        const checked = actual ? checkedProducts.get(product)! : null
        if (actual) expect(checked).toBeDefined()
        const materials = product.materials.filter((_capture, index) => !checked?.detailMaterialIndices.has(index))
          .map(capture => ({ ...capture, material: Array.isArray(capture.material)
            ? capture.material.map(materialColourPrecision) : materialColourPrecision(capture.material) }))
        const primitives = product.snapshot.primitives.filter(primitive => !checked || checked.originalIds.has(primitive.id))
          .map(primitive => ({ ...primitive, material: { ...primitive.material,
            color: primitive.material.color.map(Math.fround), emissive: primitive.material.emissive.map(Math.fround) } }))
        return { materials, snapshot: { ...product.snapshot, primitives,
          view: { ...product.snapshot.view, backgroundGrid: expectedDefaultGrid } } }
      }
      type Output = { creation: ReadProduct; afterLateBinding: ReadProduct | null; [key: string]: unknown }
      const normalizeOutput = (value: unknown, actual: boolean) => {
        const record = value as Output
        return { ...record, creation: normalizeRead(record.creation, actual),
          afterLateBinding: record.afterLateBinding ? normalizeRead(record.afterLateBinding, actual) : null }
      }
      // Every added display source passed exact owner/buffer/export exclusion checks above;
      // all original recipes/resources/UV/physical primitives now face the frozen oracle.
      expect(outputs.map(output => normalizeOutput(output, true)))
        .toEqual(expected.map((output: unknown) => normalizeOutput(output, false)))

    } finally { globals.pbrMaterials = library; library._resetForTests(); setLiquidModeActive(false); vi.restoreAllMocks(); vi.useRealTimers() }
  })
})
