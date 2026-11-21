import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

/**
 * Every surface's images are requested through ONE shared entry point.
 *
 * The photographic surfaces (a server's rear, the exhaust mesh on its patches,
 * the rack rails, the grilles) used to be loose pictures in textures/MISC that
 * each editor listed itself — the legacy page in `_preloadImageTextures`,
 * React in `loadPhotoTextureSet` — and a test kept the two lists equal. They
 * are texture sets in textures/materials.json now: both entry points request
 * exactly the sets the manifest's materials draw with, each image once.
 */

const REPO = resolve(process.cwd(), '../../..')
const manifest = JSON.parse(readFileSync(resolve(REPO, 'src/frontend/shared/textures/materials.json'), 'utf8')) as {
  sets: Record<string, { maps: Record<string, { file: string }> }>
  materials: Record<string, { set?: string | null; role?: string }>
}
function setsInUse() {
  const used = new Set<string>()
  for (const m of Object.values(manifest.materials)) {
    const r = m.role ? manifest.materials[m.role]! : m
    if (r.set) used.add(r.set)
  }
  return [...used]
}

type Scene = {
  loadPbrTextureSet(base: string): void
  loadPhotoTextureSet(base: string): void
}

let createScene: (ctx: Record<string, unknown>) => Scene

beforeAll(async () => {
  const mod = await import('../../src/legacy/geometryBuilder')
  createScene = mod.createGeometryScene as unknown as typeof createScene
})

function scene(): Scene {
  const globalGroup = new THREE.Group()
  const wrapperGroup = new THREE.Group()
  wrapperGroup.add(globalGroup)
  let renderMeshes: THREE.Object3D[] = []
  let renderRoots: THREE.Object3D[] = []
  let renderHelpers: THREE.Object3D[] = []
  return createScene({
    get state() {
      return { constructions: {} }
    },
    get uiState() {
      return { selection: { modelId: null, type: 'project' } }
    },
    get webglAvailable() {
      return false
    },
    get renderMeshes() {
      return renderMeshes
    },
    set renderMeshes(v: THREE.Object3D[]) {
      renderMeshes = v
    },
    get renderRoots() {
      return renderRoots
    },
    set renderRoots(v: THREE.Object3D[]) {
      renderRoots = v
    },
    get renderHelpers() {
      return renderHelpers
    },
    set renderHelpers(v: THREE.Object3D[]) {
      renderHelpers = v
    },
    globalGroup,
    wrapperGroup,
    geometryCache: new Map(),
    _renderRootMap: new Map(),
    _modelMap: new Map(),
    allModels: () => [],
  })
}

afterEach(async () => {
  vi.restoreAllMocks()
  const { pbrMaterials } = await import('../../src/legacy/geometryBuilder')
  pbrMaterials()._resetForTests()
})

describe('loadPhotoTextureSet / loadPbrTextureSet', () => {
  it('request every map of every set the manifest draws with, under the given base, each once', async () => {
    const { pbrMaterials } = await import('../../src/legacy/geometryBuilder')
    pbrMaterials()._resetForTests()
    const load = vi.spyOn(THREE.TextureLoader.prototype, 'load').mockImplementation(
      () => new THREE.Texture(),
    )
    const s = scene()
    await s.loadPhotoTextureSet('/textures')
    await s.loadPbrTextureSet('/textures')
    const urls = load.mock.calls.map((c) => c[0] as string).sort()
    const want = setsInUse().flatMap((set) => ['color', 'normal', 'roughness'].map((k) => '/textures/' + manifest.sets[set]!.maps[k]!.file))
    expect(urls).toEqual(want.sort())
    expect(urls.some((u) => u.includes('/MISC/'))).toBe(false)
  })

  it('the legacy page preloads through the shared function and names no image file itself', () => {
    const page = readFileSync(resolve(REPO, 'src/frontend/web/STL_generate.html'), 'utf8')
    const body = page.slice(page.indexOf('function _preloadImageTextures()'))
    const fn = body.slice(0, body.indexOf('\n    }\n') + 6)
    expect(fn).toContain("_geomScene_().loadPhotoTextureSet('textures')")
    expect(fn).not.toMatch(/\.(jpe?g|png)/)
  })
})
