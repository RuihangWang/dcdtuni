import * as THREE from 'three'
import { afterEach, describe, expect, it, vi } from 'vitest'
import fixture from './fixtures/cduRingRearAuthoredPorts.json'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { geometryPure, pbrMaterials } from '../src/legacy/geometryBuilder'
import { buildLiquidEquipmentVisual } from '../src/geometry/liquidEquipmentVisuals'
import { applyModelOpacityState, applyOpacityToMeshes } from '../src/geometry/modelOpacity'
import { paintSelectionHighlights } from '../src/interaction/selectionHighlight'
import { NO_VIEW_FADES, reapplyViewFades } from '../src/geometry/viewFades'
import { buildStlFiles } from '../src/io/stlExport'
import * as visuals from '../src/geometry/liquidEquipmentVisuals'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
const facesOf = (model: Construction) => model.geometry!.faces as Record<string, { opacity?: number; patches?: unknown[] }>
const builders: ReturnType<typeof createSceneBuilder>[] = []
function meshes(root: THREE.Object3D) { const out: THREE.Mesh[] = []; root.traverse(o => { if ((o as THREE.Mesh).isMesh) out.push(o as THREE.Mesh) }); return out }
function scene(edit?: (model: Construction) => void, persisted = fixture.model) {
 const raw = stateV4().getInitialStateV4() as unknown as CfdProject
 raw.constructions!.cdus = { m_30: structuredClone(persisted) as unknown as Construction }
 edit?.(raw.constructions!.cdus.m_30!)
 store.loadProject(raw, null)
 const project = store.getProject()!, globalGroup = new THREE.Group(), wrapperGroup = new THREE.Group(); wrapperGroup.add(globalGroup)
 const host: SceneHost = { globalGroup, wrapperGroup, registries: emptyRegistries(), project: () => project, selectedUid: () => null, webglAvailable: false }
 const builder = createSceneBuilder(host); builders.push(builder); builder.rebuild()
 return { builder, host, project, model: host.registries.modelMap.get('m_30')!, root: () => host.registries.renderRootMap.get('m_30')! }
}
afterEach(() => { for (const builder of builders.splice(0)) builder.clear(); pbrMaterials()._resetForTests(); vi.restoreAllMocks() })
const materials = (mesh: THREE.Mesh) => Array.isArray(mesh.material) ? mesh.material : [mesh.material]
const detail = (root: THREE.Object3D) => meshes(root).filter(m => m.userData.liquidVisualDetail)
describe('CDU saved display opacity and authored geometry', () => {
 it('keeps live commit, rebuild, saved reload, highlight restore, and view-fade state', () => {
  const { builder, model, project, root } = scene()
  applyOpacityToMeshes(root(), 27); expect(applyModelOpacityState(model, 27)).toBe(true); builder.rebuild()
  expect(detail(root())).toHaveLength(28)
  for (const mesh of detail(root())) for (const mat of materials(mesh)) { expect(mat.opacity).toBe(.27); expect(mat.transparent).toBe(true); expect(mat.depthWrite).toBe(false) }
  const originals = detail(root()).map(mesh => mesh.material)
  paintSelectionHighlights([['m_30', root()]], { uid: 'm_30', node: null, uids: new Set(['m_30']) }, () => () => null, NO_VIEW_FADES, () => 'cdu')
  paintSelectionHighlights([['m_30', root()]], { uid: null, node: null, uids: new Set() }, () => () => null, NO_VIEW_FADES, () => 'cdu')
  detail(root()).forEach((mesh, i) => expect(mesh.material).toBe(originals[i]))
  reapplyViewFades([root()], { model: 1, preview: .15 }); for (const mesh of detail(root())) for (const mat of materials(mesh)) expect(mat.opacity).toBe(.15)
  const saved = JSON.parse(JSON.stringify(project))
  const other = scene(undefined, saved.constructions.cdus.m_30)
  for (const mesh of detail(other.root())) for (const mat of materials(mesh)) expect(mat.opacity).toBe(.27)
 })
 it('caps saved model opacity even if old face values remain one, including zero', () => {
  for (const opacity of [.24, 0, 1]) {
   const { root } = scene(model => { model.meta!.display = { ...model.meta!.display, opacity } })
   for (const mesh of detail(root())) for (const mat of materials(mesh)) { expect(mat.opacity).toBe(opacity); expect(mat.depthWrite).toBe(opacity === 1) }
  }
 })
 it('keeps independently faded cabinet faces and attached front/rear hardware distinct', () => {
  const values = { front: .17, back: .63, left: .4, right: .48, top: .55, bottom: .31 }
  const { model, root, project } = scene(m => { for (const [id, value] of Object.entries(values)) facesOf(m)[id]!.opacity = value })
  const before = JSON.stringify(project), cabinet = root().getObjectByName('generic-cdu-cabinet') as THREE.Mesh
  const w = model.geometry!.size!.x, h = model.geometry!.size!.y, d = model.geometry!.size!.z
  const rays: [keyof typeof values, THREE.Vector3, THREE.Vector3][] = [
   ['front', new THREE.Vector3(w / 2, h / 2, -.5), new THREE.Vector3(0, 0, 1)],
   ['back', new THREE.Vector3(w / 2, h / 2, d + .5), new THREE.Vector3(0, 0, -1)],
   ['left', new THREE.Vector3(-.5, h / 2, d / 2), new THREE.Vector3(1, 0, 0)],
   ['right', new THREE.Vector3(w + .5, h / 2, d / 2), new THREE.Vector3(-1, 0, 0)],
   ['top', new THREE.Vector3(w / 2, h + .5, d / 2), new THREE.Vector3(0, -1, 0)],
   ['bottom', new THREE.Vector3(w / 2, -.5, d / 2), new THREE.Vector3(0, 1, 0)],
  ]
  root().updateWorldMatrix(true, true)
  for (const [id, start, direction] of rays) {
   const ray = new THREE.Raycaster(root().localToWorld(start), direction.transformDirection(root().matrixWorld))
   const hit = ray.intersectObject(cabinet, false)[0]!
   expect(hit, id).toBeTruthy(); expect(materials(cabinet)[hit.face!.materialIndex]!.opacity, id).toBe(values[id])
  }
  expect(materials(root().getObjectByName('generic-cdu-service-door') as THREE.Mesh)[0]!.opacity).toBe(.17)
  expect(materials(root().getObjectByName('generic-cdu-hmi-screen') as THREE.Mesh)[0]!.opacity).toBe(.17)
  expect(materials(root().getObjectByName('generic-cdu-supply-stub') as THREE.Mesh)[0]!.opacity).toBe(.63)
  expect(JSON.stringify(project)).toBe(before)
  const packet = buildSceneSnapshot({ root: root(), project })
  expect(new Set(packet.primitives.filter(p => p.name === cabinet.name).map(p => p.material.opacity))).toEqual(new Set(Object.values(values)))
 })
 it('disposes all distinct-face PBR originals and preview/highlight clones once', () => {
  const tracked = new Map<THREE.Material, ReturnType<typeof vi.fn>>()
  const track = <T extends THREE.Material>(mat: T) => { if (!tracked.has(mat)) { const event = vi.fn(); mat.addEventListener('dispose', event); tracked.set(mat, event) }; return mat }
  const lib = pbrMaterials(), original = lib.material.bind(lib)
  vi.spyOn(lib, 'material').mockImplementation((...args) => track(original(...args)))
  const { builder, root } = scene(m => { facesOf(m).front!.opacity = .2 })
  detail(root()).forEach(mesh => materials(mesh).forEach(track))
  applyOpacityToMeshes(root(), 37); detail(root()).forEach(mesh => materials(mesh).forEach(track))
  paintSelectionHighlights([['m_30', root()]], { uid: 'm_30', node: null, uids: new Set(['m_30']) }, () => () => null, NO_VIEW_FADES, () => 'cdu')
  detail(root()).forEach(mesh => materials(mesh).forEach(track)); builder.clear()
  for (const event of tracked.values()) expect(event).toHaveBeenCalledOnce()
 })
 it('preserves structural buffers, STL and saved source when plain closed CDU detail is toggled', () => {
  const { builder, host, project, root } = scene(), saved = JSON.stringify(project), stl = buildStlFiles(builder, project)
  const buffers = () => host.registries.renderMeshes.map((o: THREE.Object3D) => { const mesh = o as THREE.Mesh; return Array.from(mesh.geometry.getAttribute('position').array) })
  const before = buffers(); expect(detail(root())).toHaveLength(28)
  const bypass = vi.spyOn(visuals, 'buildLiquidEquipmentVisual').mockReturnValue(null)
  builder.rebuild(); expect(buffers()).toEqual(before); expect(buildStlFiles(builder, project)).toEqual(stl); expect(JSON.stringify(project)).toBe(saved)
  bypass.mockRestore(); builder.rebuild(); expect(buffers()).toEqual(before)
 })
 it('uses the actual admitted 100mm ports, ends fittings inward and keeps legacy fallback', () => {
  const { model, root } = scene(), inverse = root().matrixWorld.clone().invert(), before = JSON.stringify(model)
  for (const id of ['supply', 'return']) {
   const stub = root().getObjectByName(`generic-cdu-${id}-stub`) as THREE.Mesh
   const bounds = new THREE.Box3().setFromObject(stub).applyMatrix4(inverse)
   expect(bounds.max.x - bounds.min.x).toBeCloseTo(.1, 6); expect(bounds.max.z).toBeLessThanOrEqual(1.2 + 1e-7)
  }
  expect(JSON.stringify(model)).toBe(before)
  const old = structuredClone(model); old.kind = 'cdu'; delete (old.cooling as { ports?: unknown }).ports
  const visual = buildLiquidEquipmentVisual(old)!, stub = visual.getObjectByName('generic-cdu-supply-stub') as THREE.Mesh
  const bounds = new THREE.Box3().setFromObject(stub); expect(bounds.max.x - bounds.min.x).toBeCloseTo(.03, 6); geometryPure().disposeRenderObject(visual)
 })
 it.each(['patch', 'cutout', 'import'])('retains structural ownership instead of painting over an authored %s', type => {
  const { model } = scene()
  if (type === 'patch') facesOf(model).front!.patches!.push({ id: 'authored', type: 'rect', x: .1, y: .1, w: .1, h: .1 } as never)
  else if (type === 'cutout') model.geometry!.cutouts = [{}] as never
  else model.customSTLBase64 = 'authored-import-data'
  const before = JSON.stringify(model); expect(buildLiquidEquipmentVisual(model)).toBeNull(); expect(JSON.stringify(model)).toBe(before)
 })
 it('binds visible per-face PBR materials when shared textures arrive after construction', () => {
  // Only the image transport is controlled; the library's loader callbacks,
  // SceneBuilder and material registration are the production implementations.
  const arrivals: Array<(texture: THREE.Texture<HTMLImageElement>) => void> = []
  vi.spyOn(THREE.TextureLoader.prototype, 'load').mockImplementation((_url, onLoad) => {
   arrivals.push(onLoad!); return new THREE.Texture()
  })
  const lib = pbrMaterials(); lib.loadTextureSets('/controlled-cdu-texture-transport')
  const { root } = scene(model => { facesOf(model).front!.opacity = .2 })
  const cabinet = root().getObjectByName('generic-cdu-cabinet') as THREE.Mesh
  expect(materials(cabinet)).toHaveLength(6)
  expect(materials(cabinet).every(mat => (mat as THREE.MeshStandardMaterial).map === null)).toBe(true)
  arrivals.forEach(loaded => loaded(new THREE.Texture({ width: 2, height: 2 } as unknown as HTMLImageElement)))
  expect(lib.isSetReady('painted-steel')).toBe(true)
  for (const mat of materials(cabinet)) {
   const surface = mat as THREE.MeshStandardMaterial
   expect(surface.map).toBeTruthy(); expect(surface.normalMap).toBeTruthy(); expect(surface.roughnessMap).toBeTruthy()
   expect(surface.normalScale.toArray()).toEqual([.16, .16])
  }
 })

 it.each(['success', 'error'])('unregisters disposed CDU PBR targets before late texture %s', outcome => {
  const arrivals: Array<{ loaded: (texture: THREE.Texture<HTMLImageElement>) => void; failed: (error: unknown) => void }> = []
  vi.spyOn(THREE.TextureLoader.prototype, 'load').mockImplementation((_url, onLoad, _progress, onError) => {
   arrivals.push({ loaded: onLoad!, failed: onError! }); return new THREE.Texture()
  })
  const lib = pbrMaterials(), watched = new Map<THREE.Material, ReturnType<typeof vi.fn>>()
  const create = lib.material.bind(lib)
  vi.spyOn(lib, 'material').mockImplementation((...args) => {
   const mat = create(...args), disposed = vi.fn()
   mat.addEventListener('dispose', disposed); watched.set(mat, disposed); return mat
  })
  lib.loadTextureSets('/controlled-dispose-before-arrival')
  const { builder } = scene(model => { facesOf(model).front!.opacity = .2 })
  builder.clear()
  for (const disposed of watched.values()) expect(disposed).toHaveBeenCalledOnce()
  const versions = new Map([...watched.keys()].map(mat => [mat, mat.version]))
  const cachedDisposals: Array<ReturnType<typeof vi.fn>> = []
  const warnings = vi.spyOn(console, 'warn').mockImplementation(() => {})
  for (const arrival of arrivals) {
   if (outcome === 'error') arrival.failed(new Error('Controlled image transport failure'))
   else {
    const texture = new THREE.Texture({ width: 2, height: 2 } as unknown as HTMLImageElement), disposed = vi.fn()
    texture.addEventListener('dispose', disposed); cachedDisposals.push(disposed); arrival.loaded(texture)
   }
  }
  for (const [mat, disposed] of watched) {
   expect(mat.version, 'a disposed target must have left the pending bind queue').toBe(versions.get(mat))
   expect((mat as THREE.MeshStandardMaterial).map).toBeNull()
   expect((mat as THREE.MeshStandardMaterial).normalMap).toBeNull()
   expect((mat as THREE.MeshStandardMaterial).roughnessMap).toBeNull()
   expect(disposed).toHaveBeenCalledOnce()
  }
  for (const disposed of cachedDisposals) expect(disposed).not.toHaveBeenCalled()
  expect(warnings.mock.calls.length).toBe(outcome === 'error' ? arrivals.length : 0)
 })

 it('removes only disposed PBR leases while another CDU still receives late shared maps', () => {
  const arrivals: Array<(texture: THREE.Texture<HTMLImageElement>) => void> = []
  vi.spyOn(THREE.TextureLoader.prototype, 'load').mockImplementation((_url, onLoad) => {
   arrivals.push(onLoad!); return new THREE.Texture()
  })
  const lib = pbrMaterials(); lib.loadTextureSets('/controlled-shared-queue')
  const removed = scene(model => { facesOf(model).front!.opacity = .2 })
  const oldFaces = materials(removed.root().getObjectByName('generic-cdu-cabinet') as THREE.Mesh)
  const live = scene(model => { facesOf(model).front!.opacity = .4 })
  const liveFaces = materials(live.root().getObjectByName('generic-cdu-cabinet') as THREE.Mesh)
  const liveDisposals = liveFaces.map(() => vi.fn())
  liveFaces.forEach((mat, index) => mat.addEventListener('dispose', liveDisposals[index]!))
  removed.builder.clear()
  const oldVersions = oldFaces.map(mat => mat.version)
  arrivals.forEach(loaded => loaded(new THREE.Texture({ width: 2, height: 2 } as unknown as HTMLImageElement)))
  oldFaces.forEach((mat, index) => { expect(mat.version).toBe(oldVersions[index]); expect((mat as THREE.MeshStandardMaterial).map).toBeNull() })
  liveFaces.forEach(mat => { expect((mat as THREE.MeshStandardMaterial).map).toBeTruthy(); expect((mat as THREE.MeshStandardMaterial).normalMap).toBeTruthy() })
  liveDisposals.forEach(disposed => expect(disposed).not.toHaveBeenCalled())
 })

})
