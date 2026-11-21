import { createHash } from 'node:crypto'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { buildSceneSnapshot, type RenderMaterial } from '../src/renderer/sceneSnapshot'
import { materialLibrary } from '../src/legacy/materialManifest'

/**
 * The cross-language contract between the editor's render snapshot and Kit.
 *
 * Each case makes one face through the real geometry builder
 * (spawnPlanarRect -> createMaterialForType) with every texture set "loaded"
 * from its pool URL, snapshots it, and records the material (texture fields as
 * their pool references) and the baked UVs. The record is the committed golden
 * test/fixtures/renderMaterialByKind.json; the Kit tests
 * (cfd_demo/tests/test_material_parity.py) require
 * dc.cfd_bridge.material_library to produce the same material and UVs for the
 * same case, and the Kit-native path to author the same USD as the display
 * bridge from it.
 *
 * After a change to textures/materials.json or to the builder, regenerate:
 *   UPDATE_RENDER_MATERIAL_GOLDEN=1 npx vitest run test/renderMaterialParity.test.ts
 * then run the Kit tests; they fail until the Python reader agrees.
 */

const golden = resolve(process.cwd(), 'test/fixtures/renderMaterialByKind.json')
const manifestFile = resolve(process.cwd(), '../shared/textures/materials.json')
const manifestSha256 = createHash('sha256').update(readFileSync(manifestFile)).digest('hex')
const update = process.env.UPDATE_RENDER_MATERIAL_GOLDEN === '1'

type Options = { color?: string; keepUserColor?: boolean; opacity?: number; doubleSide?: boolean;
  uvNormalize?: { type: string; uCount?: number; tileSize?: number } }
type Case = { name: string; kind: string; picture?: string; options: Options; w: number; h: number }

const PICTURE = '/textures/electric-box/panel_lamps.jpg'
const CASES: Case[] = [
  { name: 'rack-side', kind: 'rack-side', options: { color: '#707783', doubleSide: true }, w: 1.2, h: 2.0 },
  { name: 'rack-top', kind: 'rack-top', options: { color: '#9ba0a9', doubleSide: true }, w: 0.6, h: 1.2 },
  { name: 'rack-bottom', kind: 'rack-bottom', options: { color: '#4b5263', doubleSide: true }, w: 0.6, h: 1.2 },
  { name: 'rack-front', kind: 'rack-front', options: { color: '#5c6370', doubleSide: true }, w: 0.6, h: 0.1 },
  { name: 'rack-rail', kind: 'rack-rail', options: { color: '#5c6370', doubleSide: true, uvNormalize: { type: 'tile-per-u', uCount: 42 } }, w: 0.05, h: 1.867 },
  { name: 'server-front factory', kind: 'server-front', options: { color: '#4488ff', doubleSide: true, uvNormalize: { type: 'tile-per-u', uCount: 2 } }, w: 0.48, h: 0.0889 },
  { name: 'server-front authored', kind: 'server-front', options: { color: '#aa5500', doubleSide: true, uvNormalize: { type: 'tile-per-u', uCount: 1 } }, w: 0.48, h: 0.0445 },
  { name: 'server-rear-socket', kind: 'server-rear-socket', options: { color: '#44ff88', doubleSide: true, uvNormalize: { type: 'tile-per-u', uCount: 2 } }, w: 0.48, h: 0.0889 },
  { name: 'server-outlet-mesh', kind: 'server-outlet-mesh', options: { keepUserColor: false, doubleSide: true, uvNormalize: { type: 'tile-physical', tileSize: 0.1 } }, w: 0.2, h: 0.05 },
  { name: 'server-chassis', kind: 'server-chassis', options: { color: '#707783', doubleSide: true }, w: 0.48, h: 0.9 },
  { name: 'server-chassis patch', kind: 'server-chassis', options: { color: '#ffcc33', keepUserColor: true, doubleSide: true }, w: 0.1, h: 0.1 },
  { name: 'blank-panel', kind: 'blank-panel', options: { color: '#5c6370' }, w: 0.48, h: 0.4 },
  { name: 'blank-panel-baffle', kind: 'blank-panel-baffle', options: { color: '#5c6370' }, w: 0.48, h: 0.4 },
  { name: 'floor', kind: 'floor', options: { doubleSide: true }, w: 4.4, h: 6.0 },
  { name: 'raised-floor', kind: 'raised-floor', options: { doubleSide: true }, w: 4.4, h: 6.0 },
  { name: 'floor-vent', kind: 'floor-vent', options: { keepUserColor: false, doubleSide: true }, w: 0.6, h: 0.6 },
  { name: 'ceiling', kind: 'ceiling', options: { doubleSide: true }, w: 4.4, h: 6.0 },
  { name: 'wall', kind: 'wall', options: { doubleSide: true }, w: 6.0, h: 3.6 },
  { name: 'wall translucent', kind: 'wall', options: { doubleSide: true, opacity: 0.5 }, w: 6.0, h: 3.6 },
  { name: 'wall patch', kind: 'wall', options: { color: '#ff8800', keepUserColor: true, doubleSide: true }, w: 0.5, h: 0.5 },
  { name: 'baffle', kind: 'baffle', options: { color: '#ffffff', doubleSide: true }, w: 1.2, h: 2.0 },
  { name: 'baffle-perforated', kind: 'baffle-perforated', options: { doubleSide: true }, w: 1.2, h: 2.0 },
  { name: 'baffle-grille', kind: 'baffle-grille', options: { color: '#ffffff', keepUserColor: false, doubleSide: true }, w: 0.6, h: 0.6 },
  { name: 'box factory', kind: 'box', options: { color: '#5c6370' }, w: 1.0, h: 2.0 },
  { name: 'box patch', kind: 'box', options: { color: '#3366cc', keepUserColor: true }, w: 0.4, h: 0.3 },
  { name: 'acu-body', kind: 'acu-body', options: { color: '#7f848e' }, w: 1.0, h: 2.0 },
  { name: 'acu-supply', kind: 'acu-supply', options: { color: '#3366cc', keepUserColor: true }, w: 0.8, h: 0.4 },
  { name: 'acu-return', kind: 'acu-return', options: { color: '#cc6633', keepUserColor: true }, w: 0.8, h: 0.4 },
  { name: 'acu-side', kind: 'acu-side', options: { color: '#9ba0a9', doubleSide: true }, w: 1.0, h: 0.3 },
  { name: 'face-image', kind: 'face-image', picture: PICTURE, options: { color: '#ffffff', uvNormalize: { type: 'stretch' } }, w: 0.5, h: 0.7 },
  { name: 'default', kind: 'default', options: { color: '#000000', opacity: 0.15, doubleSide: true }, w: 0.5, h: 0.0025 },
  { name: 'custom', kind: 'custom', options: { color: '#336699' }, w: 1.0, h: 1.0 },
  { name: 'cdu-body', kind: 'cdu-body', options: { color: '#007a99' }, w: 0.8, h: 1.8 },
  { name: 'air-duct', kind: 'air-duct', options: {}, w: 2.0, h: 0.4 },
  { name: 'cold-plate', kind: 'cold-plate', options: { color: '#123456' }, w: 0.2, h: 0.1 },
]

type Scene = {
  spawnPlanarRect(group: THREE.Object3D, origin: THREE.Vector3, right: THREE.Vector3, up: THREE.Vector3,
    w: number, h: number, options: Record<string, unknown>): THREE.Mesh
  clearMaterialCache(): void
}
type Lib = { _resetForTests(): void; registerTexture(set: string, map: 'diffuse' | 'normal' | 'roughness', t: THREE.Texture): void }
let createScene: (ctx: Record<string, unknown>) => Scene
let lib: () => Lib

function image(src: string) {
  return { width: 2, height: 2, src, toDataURL: () => { throw new Error(`${src} is a pool file: it must not be serialised`) } }
}
function landAll() {
  const keys = { diffuse: 'color', normal: 'normal', roughness: 'roughness' } as const
  for (const set of materialLibrary.setNames()) {
    for (const [map, key] of Object.entries(keys)) {
      const t = new THREE.Texture(image('/textures/' + materialLibrary.mapFile(set, key)) as unknown as HTMLCanvasElement)
      lib().registerTexture(set, map as 'diffuse', t)
    }
  }
}
function scene(): Scene {
  let renderMeshes: THREE.Object3D[] = []
  let renderRoots: THREE.Object3D[] = []
  let renderHelpers: THREE.Object3D[] = []
  const globalGroup = new THREE.Group()
  const wrapperGroup = new THREE.Group()
  wrapperGroup.add(globalGroup)
  return createScene({
    get state() { return { constructions: {} } },
    get uiState() { return { selection: { modelId: null, type: 'project' } } },
    get webglAvailable() { return true },
    get renderMeshes() { return renderMeshes }, set renderMeshes(v: THREE.Object3D[]) { renderMeshes = v },
    get renderRoots() { return renderRoots }, set renderRoots(v: THREE.Object3D[]) { renderRoots = v },
    get renderHelpers() { return renderHelpers }, set renderHelpers(v: THREE.Object3D[]) { renderHelpers = v },
    globalGroup, wrapperGroup,
    geometryCache: new Map(), _renderRootMap: new Map(), _modelMap: new Map(),
    allModels: () => [],
  })
}

beforeAll(async () => {
  const mod = await import('../src/legacy/geometryBuilder')
  createScene = mod.createGeometryScene as unknown as typeof createScene
  lib = mod.pbrMaterials as unknown as () => Lib
  lib()._resetForTests()
  landAll()
  // A face picture loads through TextureLoader; hand back a loaded image of that URL.
  vi.spyOn(THREE.TextureLoader.prototype, 'load').mockImplementation(((url: string) => {
    const t = new THREE.Texture(image(url) as unknown as HTMLCanvasElement)
    return t
  }) as unknown as THREE.TextureLoader['load'])
})
afterAll(() => { vi.restoreAllMocks(); lib()._resetForTests() })

function record(c: Case) {
  const s = scene()
  const owner = new THREE.Group()
  owner.userData.modelId = 'model-uid'
  const root = new THREE.Group()
  root.add(owner)
  const kind = c.kind === 'face-image' ? 'face-image:' + c.picture : c.kind
  s.spawnPlanarRect(owner, new THREE.Vector3(), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0),
    c.w, c.h, { ...c.options, objectKind: kind, skipEdges: true })
  const packet = buildSceneSnapshot({ root, project: { uid: 'parity', name: 'parity' } })
  expect(packet.primitives).toHaveLength(1)
  const prim = packet.primitives[0]!
  const material: Record<string, unknown> = { ...prim.material }
  for (const key of ['map', 'normalMap', 'roughnessMap', 'metalnessMap'] as const) {
    const id = (prim.material as RenderMaterial)[key]
    if (id) material[key] = packet.textures[id]
  }
  return { ...c, material, positions: prim.positions, uvs: prim.uvs ?? null }
}

/** `actual` with every number within 1e-12 of `expected` replaced by it. */
function near(actual: unknown, expected: unknown): unknown {
  if (typeof actual === 'number' && typeof expected === 'number') return Math.abs(actual - expected) <= 1e-12 ? expected : actual
  if (Array.isArray(actual) && Array.isArray(expected)) return actual.map((v, i) => near(v, expected[i]))
  if (actual && expected && typeof actual === 'object' && typeof expected === 'object')
    return Object.fromEntries(Object.entries(actual).map(([k, v]) => [k, near(v, (expected as Record<string, unknown>)[k])]))
  return actual
}

describe('render material per kind: the golden Kit is held to', () => {
  const actual = () => ({ manifestSha256, cases: CASES.map(record) })

  it('every pool texture in it is a reference, never bytes', () => {
    for (const c of actual().cases) {
      for (const key of ['map', 'normalMap', 'roughnessMap'] as const) {
        const value = c.material[key]
        if (value !== undefined) expect(value, `${c.name} ${key}`).toMatch(/^pool:/)
      }
    }
  })

  it('matches test/fixtures/renderMaterialByKind.json', () => {
    const now = actual()
    if (update || !existsSync(golden)) {
      writeFileSync(golden, JSON.stringify(now, null, 1) + '\n')
    }
    const frozen = JSON.parse(readFileSync(golden, 'utf8'))
    expect(frozen.manifestSha256, 'textures/materials.json changed: regenerate with UPDATE_RENDER_MATERIAL_GOLDEN=1').toBe(manifestSha256)
    // Numbers to 1e-12, as the Kit test compares (test_material_parity.py):
    // the sRGB->linear colours come from Math.pow, whose last bit differs
    // between V8 versions; everything else must match exactly.
    expect(near(JSON.parse(JSON.stringify(now.cases)), frozen.cases)).toEqual(frozen.cases)
  })
})
