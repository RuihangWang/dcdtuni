import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The structural materials: what geometry_scene.js makes of a kind's entry in
 * textures/materials.json.
 *
 * Texture tiling is decided per material, never written onto a texture other
 * meshes share: a set's textures belong to js/pbr_materials.js, and a material
 * at a different repeat gets its own clone (same image).
 */

type Scene = {
  spawnPlanarRect(group: THREE.Object3D, origin: THREE.Vector3, right: THREE.Vector3, up: THREE.Vector3,
    w: number, h: number, options: Record<string, unknown>): THREE.Mesh
  clearMaterialCache(): void
  renderPipeModel(model: Record<string, unknown>, group: THREE.Object3D): void
}
type Lib = {
  _resetForTests(): void
  registerTexture(set: string, map: 'diffuse' | 'normal' | 'roughness', t: THREE.Texture): void
  ROLES: Record<string, { tileM: number }>
}
type Manifest = {
  sets: Record<string, { stats: { meanRoughness: number } }>
  materials: Record<string, { set: string | null; baseColor: string; roughness: number; metallic: number; normalScale: number; tileM: number | null; opacity: number }>
}

const manifest = JSON.parse(readFileSync(resolve(process.cwd(), '../shared/textures/materials.json'), 'utf8')) as Manifest
let createScene: (ctx: Record<string, unknown>) => Scene
let lib: () => Lib
let pure: () => Record<string, (...a: unknown[]) => THREE.Material>

beforeAll(async () => {
  const mod = await import('../src/legacy/geometryBuilder')
  createScene = mod.createGeometryScene as unknown as typeof createScene
  lib = mod.pbrMaterials as unknown as () => Lib
  pure = mod.geometryPure as unknown as typeof pure
})

function fake(tag: string) {
  return new THREE.Texture({ width: 2, height: 2, tag, toDataURL: () => 'data:image/png;base64,AA==' } as unknown as HTMLCanvasElement)
}
/** A set's three maps "load" (the registry binds a set only when all three are in). */
function land(set: string) {
  for (const map of ['diffuse', 'normal', 'roughness'] as const) lib().registerTexture(set, map, fake(`${set}/${map}`))
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

const X = new THREE.Vector3(1, 0, 0)
const Y = new THREE.Vector3(0, 1, 0)
const O = new THREE.Vector3()
const mat = (m: THREE.Mesh) => m.material as THREE.MeshStandardMaterial
const repeatOf = (kind: string) => Math.round(1000 / manifest.materials[kind]!.tileM!) / 1000
const hex = (c: string) => new THREE.Color(c).getHex()

beforeEach(() => lib()._resetForTests())
afterEach(() => { vi.restoreAllMocks(); lib()._resetForTests() })

describe('texture repeat per material, not per shared texture', () => {
  it('meshes of one kind tile at the kind’s physical size whatever their extent', () => {
    land('floor-tile')
    const s = scene()
    const g = new THREE.Group()
    const small = s.spawnPlanarRect(g, O, X, Y, 1.2, 0.6, { objectKind: 'raised-floor' })
    const big = s.spawnPlanarRect(g, O, X, Y, 20, 12, { objectKind: 'raised-floor' })
    expect(mat(small).map!.repeat.x).toBeCloseTo(repeatOf('raised-floor'), 6)
    expect(mat(big).map!.repeat.x).toBeCloseTo(repeatOf('raised-floor'), 6)
    expect(mat(big).normalMap!.repeat.x).toBeCloseTo(repeatOf('raised-floor'), 6)
    expect(mat(big).roughnessMap!.repeat.x).toBeCloseTo(repeatOf('raised-floor'), 6)
  })

  it('a normalised-UV mesh and a physical one of the same kind do not share a texture transform', () => {
    land('floor-tile')
    const s = scene()
    const g = new THREE.Group()
    const fitted = s.spawnPlanarRect(g, O, X, Y, 0.6, 0.6, { objectKind: 'raised-floor', uvNormalize: { type: 'stretch' } })
    const phys = s.spawnPlanarRect(g, O, X, Y, 0.8, 0.6, { objectKind: 'raised-floor' })
    expect(fitted.material).not.toBe(phys.material)
    expect(mat(fitted).map!.repeat.toArray()).toEqual([1, 1])
    expect(mat(phys).map!.repeat.x).toBeCloseTo(repeatOf('raised-floor'), 6)
    // a clone, not a copy of the pixels: the image is shared
    expect(mat(phys).map!.source).toBe(mat(fitted).map!.source)
    expect(mat(fitted).map!.repeat.toArray()).toEqual([1, 1])
  })

  it('one clone per (texture, repeat): materials of different colours share it', () => {
    land('painted-steel')
    const s = scene()
    const g = new THREE.Group()
    const a = s.spawnPlanarRect(g, O, X, Y, 1, 2, { color: 0x112233, keepUserColor: true, objectKind: 'box' })
    const b = s.spawnPlanarRect(g, O, X, Y, 1, 2, { color: 0x445566, keepUserColor: true, objectKind: 'box' })
    expect(a.material).not.toBe(b.material)
    expect(mat(a).map).toBe(mat(b).map)
  })

  it('a stretch kind is never tiled: one panel per face', () => {
    land('containment-panel')
    const m = scene().spawnPlanarRect(new THREE.Group(), O, X, Y, 1.25, 2.07, { objectKind: 'baffle' })
    expect(mat(m).map!.repeat.toArray()).toEqual([1, 1])
    const uv = m.geometry.getAttribute('uv')
    let maxU = 0, maxV = 0
    for (let i = 0; i < uv.count; i++) { maxU = Math.max(maxU, uv.getX(i)); maxV = Math.max(maxV, uv.getY(i)) }
    expect([maxU, maxV]).toEqual([1, 1])
  })

  it('tile-physical meshes use their own tile size', () => {
    land('hex-mesh')
    const m = scene().spawnPlanarRect(new THREE.Group(), O, X, Y, 0.4, 0.2, { objectKind: 'server-outlet-mesh', uvNormalize: { type: 'tile-physical', tileSize: 0.1 } })
    expect(mat(m).map!.repeat.x).toBeCloseTo(10, 3)
  })

  it('a kind whose manifest UV mode is tile-per-u is fitted one map per rack unit', () => {
    land('server-bezel')
    const m = scene().spawnPlanarRect(new THREE.Group(), O, X, Y, 0.45, 0.0889, { objectKind: 'server-front', uvNormalize: { type: 'tile-per-u', uCount: 2 } })
    const uv = m.geometry.getAttribute('uv')
    let maxV = 0
    for (let i = 0; i < uv.count; i++) maxV = Math.max(maxV, uv.getY(i))
    expect(maxV).toBeCloseTo(2, 6)
    expect(mat(m).map!.repeat.toArray()).toEqual([1, 1])
  })
})

describe('structural kinds are physically based, from the manifest', () => {
  it('a rack panel is a MeshStandardMaterial with the manifest colour, roughness and metalness', () => {
    const s = scene()
    const rack = s.spawnPlanarRect(new THREE.Group(), O, X, Y, 1, 2, { color: 0x7f848e, objectKind: 'rack-side' })
    const want = manifest.materials['rack-side']!
    expect(rack.material).toBeInstanceOf(THREE.MeshStandardMaterial)
    expect(mat(rack).color.getHex()).toBe(hex(want.baseColor)) // the renderer's constant does not override it
    expect(mat(rack).roughness).toBe(want.roughness) // no map yet: the scalar is the wanted roughness
    expect(mat(rack).metalness).toBe(want.metallic)
    expect(mat(rack).map).toBeNull()
    expect(mat(rack).userData.pbr).toBeUndefined()
    expect(mat(rack).userData.materialKind).toBe('rack-side')
  })

  it('once its set lands, all three maps bind and the roughness scalar is wanted / map mean', () => {
    land('painted-steel')
    const s = scene()
    const rack = s.spawnPlanarRect(new THREE.Group(), O, X, Y, 1, 2, { objectKind: 'rack-side' })
    const want = manifest.materials['rack-side']!
    expect(mat(rack).map).not.toBeNull()
    expect(mat(rack).normalMap).not.toBeNull()
    expect(mat(rack).roughnessMap).not.toBeNull()
    expect(mat(rack).roughness).toBeCloseTo(Math.min(1, want.roughness / manifest.sets[want.set!]!.stats.meanRoughness), 10)
    expect(mat(rack).normalScale.toArray()).toEqual([want.normalScale, want.normalScale])
  })

  it('an authored colour tints a tintable kind; a renderer default does not', () => {
    const s = scene()
    const g = new THREE.Group()
    const front = manifest.materials['server-front']!
    const preset = s.spawnPlanarRect(g, O, X, Y, 0.45, 0.04, { color: '#d14b4b', objectKind: 'server-front' })
    const authored = s.spawnPlanarRect(g, O, X, Y, 0.45, 0.04, { color: '#aa3300', objectKind: 'server-front' })
    expect(mat(preset).color.getHex()).toBe(hex(front.baseColor))
    expect(mat(authored).color.getHex()).toBe(hex('#aa3300'))
    // a wall-baffle blanking range has its own material, so the hint shows
    const plain = s.spawnPlanarRect(g, O, X, Y, 0.45, 0.04, { color: '#5c6370', objectKind: 'blank-panel' })
    const baffle = s.spawnPlanarRect(g, O, X, Y, 0.45, 0.04, { color: '#5c6370', objectKind: 'blank-panel-baffle' })
    expect(mat(plain).color.getHex()).toBe(hex(manifest.materials['blank-panel']!.baseColor))
    expect(mat(baffle).color.getHex()).toBe(hex(manifest.materials['blank-panel-baffle']!.baseColor))
    expect(mat(baffle).color.getHex()).not.toBe(mat(plain).color.getHex())
  })

  it('renderer defaults that resolve to one colour share one material', () => {
    const s = scene()
    const g = new THREE.Group()
    const a = s.spawnPlanarRect(g, O, X, Y, 1, 2, { color: '#5c6370', objectKind: 'rack-front' })
    const b = s.spawnPlanarRect(g, O, X, Y, 1, 2, { color: '#707783', objectKind: 'rack-front' })
    expect(a.material).toBe(b.material)
  })

  it('a containment panel blends: its alpha is in the colour map', () => {
    land('containment-panel')
    const m = scene().spawnPlanarRect(new THREE.Group(), O, X, Y, 1.25, 2.07, { objectKind: 'baffle', doubleSide: true })
    expect(mat(m).transparent).toBe(true)
    expect(mat(m).depthWrite).toBe(false)
    expect(mat(m).side).toBe(THREE.DoubleSide)
  })

  it('a mesh without a kind gets the default satin surface in its own colour', () => {
    const m = scene().spawnPlanarRect(new THREE.Group(), O, X, Y, 1, 1, { color: '#4daafc', opacity: 0.5 })
    expect(m.material).toBeInstanceOf(THREE.MeshStandardMaterial)
    expect(mat(m).color.getHex()).toBe(hex('#4daafc'))
    expect(mat(m).transparent).toBe(true)
  })
})

describe('the perforated rack door', () => {
  it('is a lit MeshStandardMaterial whose pattern survives a clone', () => {
    const door = pure()._rackDoorMaterial!({ shape: 'hexagon', openAreaPct: 50, holeDiameterMm: 4 }, 0.48, 1.9) as THREE.MeshStandardMaterial
    const steel = manifest.materials['rack-door']!
    expect(door).toBeInstanceOf(THREE.MeshStandardMaterial)
    expect(door.color.getHex()).toBe(hex(steel.baseColor))
    expect(door.opacity).toBe(steel.opacity)
    expect(door.roughness).toBe(steel.roughness)
    expect(door.userData.renderSnapshot).toMatchObject({ kind: 'perforatedDoor', shape: 3 })
    const copy = door.clone() as THREE.MeshStandardMaterial & { isPerforatedDoorMaterial?: boolean; defines?: Record<string, string> }
    expect(copy.isPerforatedDoorMaterial).toBe(true)
    expect(copy.defines).toMatchObject({ STANDARD: '', USE_UV: '' })
    expect(copy.userData.renderSnapshot).toEqual(door.userData.renderSnapshot)
    expect(copy.customProgramCacheKey()).toBe(door.customProgramCacheKey())
    const shader = { uniforms: {} as Record<string, { value: unknown }>, fragmentShader: '#include <common>\n#include <alphatest_fragment>', vertexShader: '' }
    copy.onBeforeCompile(shader as unknown as THREE.WebGLProgramParametersWithUniforms, {} as THREE.WebGLRenderer)
    expect(shader.uniforms.uDoorShape!.value).toBe(3)
    expect(shader.fragmentShader).toContain('discard')
  })
})

describe('air ducts', () => {
  it('are galvanized MeshStandardMaterial tiled along their own length, identity unchanged', () => {
    const s = scene()
    const g = new THREE.Group()
    const duct = (uid: string, geometry: Record<string, unknown>) => ({
      uid, kind: 'pipe', meta: { name: uid, enabled: true, display: { opacity: 1 } }, geometry,
    })
    s.renderPipeModel(duct('d_round', { shape: 'round', diameterM: 0.3, waypoints: [{ x: 0, y: 2, z: 0 }, { x: 4, y: 2, z: 0 }] }), g)
    s.renderPipeModel(duct('d_rect', { shape: 'rect', widthM: 0.4, heightM: 0.3, waypoints: [{ x: 0, y: 3, z: 0 }, { x: 0, y: 3, z: 2 }] }), g)
    const meshes = g.children.filter((c) => (c as THREE.Mesh).isMesh) as THREE.Mesh[]
    expect(meshes.length).toBeGreaterThanOrEqual(4) // tube + 2 caps + a rect run
    for (const m of meshes) {
      const material = m.material as THREE.MeshStandardMaterial
      expect(material).toBeInstanceOf(THREE.MeshStandardMaterial)
      expect(material.userData.pbrRole).toBe('galvanized')
      expect(m.userData).toMatchObject({ isPipeBody: true, faceId: 'body', isBaseWall: true })
    }
    const tube = meshes.find((m) => m.geometry.type === 'TubeGeometry')!
    const uv = tube.geometry.getAttribute('uv')
    let maxU = 0
    for (let i = 0; i < uv.count; i++) maxU = Math.max(maxU, uv.getX(i))
    // tiled once, by the duct builder (4 m / tileM), not scaled again as metres
    expect(maxU).toBeCloseTo(4 / lib().ROLES.galvanized!.tileM, 1)
  })
})
