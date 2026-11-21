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
const owned = resolve(root, '.codex-tmp/box-cdu-material-original-baseline-20261006')
const enabled = process.env.DCDTUNI_CAPTURE_BOX_MATERIAL_ORIGINAL === '1'
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
function projectFor(sample: Sample) {
  const raw = stateV4().getInitialStateV4() as unknown as CfdProject
  raw.uid = 'box-material-original-baseline'
  raw.constructions = { [sample.bucket]: { [sample.uid]: structuredClone(sample.model) } } as CfdProject['constructions']
  return stateV4().normalizeStateV4(raw) as unknown as CfdProject
}

describe.skipIf(!enabled)('explicit original BOX/CDU material producer capture', () => {
  it('captures original materials/resources/true snapshots once without touching structural goldens', () => {
    const binding = JSON.parse(readFileSync(resolve(owned, 'producer-before.json'), 'utf8')) as { files: Array<{ path: string; sha256: string }> }
    const verify = () => binding.files.forEach(x => expect(sha(readFileSync(resolve(root, x.path))), x.path).toBe(x.sha256))
    verify()
    const samples = JSON.parse(readFileSync(resolve(owned, 'actual-inputs.json'), 'utf8')).cases as Sample[]
    const library = pbrMaterials()
    const globals = window as unknown as { pbrMaterials?: typeof library }
    const outputs: unknown[] = []
    const calls: Array<{ url: string; texture: THREE.Texture; onLoad: (t: THREE.Texture) => void }> = []
    vi.useFakeTimers()
    const context = new Proxy({}, { get(_target, key) {
      if (key === 'createLinearGradient' || key === 'createRadialGradient') return () => ({ addColorStop() {} })
      if (key === 'getImageData') return () => ({ data: new Uint8ClampedArray(4) })
      return () => undefined
    } })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => context as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(() => png)
    vi.spyOn(THREE.TextureLoader.prototype, 'load').mockImplementation(((url: string, onLoad?: (t: THREE.Texture) => void) => {
      const tex = new THREE.Texture(image(url) as unknown as HTMLCanvasElement)
      tex.name = url
      calls.push({ url, texture: tex, onLoad: onLoad ?? (() => undefined) })
      return tex
    }) as THREE.TextureLoader['load'])
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
        const read = () => {
          const mats: unknown[] = []
          group.traverse(o => { const m = (o as THREE.Mesh).material; if (m) mats.push({
            userData: { modelId: o.userData.modelId ?? null, faceId: o.userData.faceId ?? null,
              refIds: o.userData.refIds ?? null, geometryPartId: o.userData.geometryPartId ?? null,
              isBaseWall: o.userData.isBaseWall ?? null, baseOpacity: o.userData.baseOpacity ?? null },
            material: Array.isArray(m) ? m.map(material) : material(m),
            uvHex: (o as THREE.Mesh).geometry?.getAttribute('uv') ? Buffer.from((o as THREE.Mesh).geometry.getAttribute('uv').array.buffer).toString('hex') : null }) })
          const camera = new THREE.PerspectiveCamera(50, 16 / 9, .05, 1000); camera.position.set(9, 6, 8)
          const snapshot = buildSceneSnapshot({ root: group, project, camera, target: new THREE.Vector3(3, 1, 2),
            selectedUids: [sample.uid], timeSeconds: 7.25, helpers: host.registries.renderHelpers })
          return { materials: mats, snapshot }
        }
        const creation = read()
        let afterLateBinding: ReturnType<typeof read> | null = null
        if (late) {
          calls.filter(x => x.url.includes('/painted-steel/')).forEach(c => c.onLoad(c.texture))
          afterLateBinding = read()
        }
        outputs.push({ label, actual: !sample.synthetic && !direct, sample, mode,
          requestedResourceRefs: calls.map(c => c.url), creation, afterLateBinding,
          normalizedInputSha256: sha(before), normalizedAfterSha256: sha(JSON.stringify(project)) })
      } finally { builder.clear(); vi.clearAllTimers(); globals.pbrMaterials = library }
    }
    try {
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
          (api.spawnRenderable as Spawn)(group, new THREE.PlaneGeometry(2, 1), { ...options, objectKind: kind,
            skipEdges: true, userData: { modelId: base.uid, exportName: 'controlledPlane' } })
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
      verify()
      const target = resolve(root, 'src/frontend/react/test/fixtures/boxMaterialRecipeBaseline.json')
      writeFileSync(target, JSON.stringify({ schemaVersion: 1, scope: 'Original producer output; actual three records and labeled controlled materials. Fake 2D canvas/image sentinel, no GPU or real raster acceptance. Structural golden untouched.',
        producerSources: binding.files, cases: outputs }, null, 2) + '\n', { flag: 'wx' })
      writeFileSync(resolve(owned, 'capture-result.json'), JSON.stringify({ cases: outputs.length, target,
        sha256: sha(readFileSync(target)), structuralGoldenSha256: sha(readFileSync(resolve(root, 'src/frontend/react/test/fixtures/boxGeometryProductParity.json'))) }, null, 2) + '\n')
    } finally { globals.pbrMaterials = library; library._resetForTests(); setLiquidModeActive(false); vi.restoreAllMocks(); vi.useRealTimers() }
  })
})
