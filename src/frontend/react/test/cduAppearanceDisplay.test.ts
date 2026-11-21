import { createHash } from 'node:crypto'
import * as THREE from 'three'
import { afterEach, describe, expect, it, vi } from 'vitest'
import fixture from './fixtures/closedCabinetShinaide.json'
import { type CfdProject, type Construction } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { geometryPure, pbrMaterials } from '../src/legacy/geometryBuilder'
import { buildLiquidEquipmentVisual } from '../src/geometry/liquidEquipmentVisuals'
import { createCduDisplayTexture } from '../src/geometry/equipmentDisplayTexture'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { setLiquidModeActive } from '../src/liquid/liquidModeState'
import { paintSelectionHighlights } from '../src/interaction/selectionHighlight'
import { NO_VIEW_FADES } from '../src/geometry/viewFades'
import { applyOpacityToMeshes } from '../src/geometry/modelOpacity'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'

// This canvas records the drawing contract. It is not a browser pixel/legibility proof.
function canvasDrawing() {
  const labels = new WeakMap<HTMLCanvasElement, string[]>()
  vi.stubGlobal('CanvasRenderingContext2D', class {})
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    const text: string[] = []
    labels.set(this, text)
    const noop = () => {}
    return {
      fillRect: noop, clearRect: noop, strokeRect: noop, beginPath: noop, closePath: noop,
      moveTo: noop, lineTo: noop, arc: noop, ellipse: noop, stroke: noop, fill: noop,
      save: noop, restore: noop, translate: noop, rotate: noop, scale: noop, drawImage: noop,
      fillText: (value: string) => text.push(value), strokeText: noop,
      measureText: (value: string) => ({ width: value.length * 8 }),
      createLinearGradient: () => ({ addColorStop: noop }),
      getImageData: () => ({ data: new Uint8ClampedArray(16) }), putImageData: noop,
    } as unknown as CanvasRenderingContext2D
  } as unknown as typeof HTMLCanvasElement.prototype.getContext)
  vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,aG1p')
  return labels
}
function project() {
  const raw = stateV4().getInitialStateV4() as unknown as CfdProject
  raw.constructions!.cdus = { m_30: structuredClone(fixture.models.find(model => model.uid === 'm_30')!) }
  return stateV4().normalizeStateV4(raw) as unknown as CfdProject
}
function cdu() {
  return (stateV4().allModels(project()) as Construction[]).find(model => model.uid === 'm_30')! as Construction
}
function screen(group: THREE.Object3D) {
  return group.getObjectByName('generic-cdu-hmi-screen') as THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>
}
const builders: Array<ReturnType<typeof createSceneBuilder>> = []
const groups: THREE.Object3D[] = []
function scene(p: CfdProject, liquid: boolean) {
  setLiquidModeActive(liquid)
  const globalGroup = new THREE.Group(), wrapperGroup = new THREE.Group()
  wrapperGroup.add(globalGroup)
  const host: SceneHost = {
    globalGroup, wrapperGroup, registries: emptyRegistries(),
    project: () => p, selectedUid: () => null, webglAvailable: false,
  }
  const builder = createSceneBuilder(host)
  builders.push(builder)
  builder.rebuild()
  return { host, builder }
}
function visual() {
  const group = buildLiquidEquipmentVisual(cdu())!
  groups.push(group)
  group.updateMatrixWorld(true)
  return group
}
function landPaint() {
  const lib = pbrMaterials(), textures: THREE.Texture[] = []
  for (const type of ['diffuse', 'normal', 'roughness'] as const) {
    const texture = new THREE.Texture({ width: 2, height: 2, toDataURL: () => 'data:image/png;base64,cGJy' } as unknown as HTMLCanvasElement)
    textures.push(texture)
    lib.registerTexture(lib.ROLES['painted-steel']!.set, type, texture)
  }
  return textures
}
afterEach(() => {
  for (const builder of builders.splice(0)) builder.clear()
  for (const group of groups.splice(0)) geometryPure().disposeRenderObject(group)
  setLiquidModeActive(false)
  pbrMaterials()._resetForTests()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('CDU display appearance', () => {
  it('keeps owned CDU geometry and STL usable when the optional PBR library is missing', () => {
    const p = project(), before = JSON.stringify(p), { host, builder } = scene(p, false)
    const take = () => ({
      meshes: host.registries.renderMeshes.map(object => {
        const mesh = object as THREE.Mesh
        return { modelId: mesh.userData.modelId, exportName: mesh.userData.exportName,
          position: Array.from(mesh.geometry.getAttribute('position').array),
          normal: Array.from(mesh.geometry.getAttribute('normal').array),
          index: mesh.geometry.index ? Array.from(mesh.geometry.index.array) : null }
      }),
      stl: builder.api.buildGroupedStlMap(['m_30'], true),
    })
    const structural = take(), library = pbrMaterials()
    const globals = window as unknown as { pbrMaterials?: typeof library }
    expect(screen(host.globalGroup)).toBeDefined()
    try {
      globals.pbrMaterials = undefined
      expect(() => builder.rebuild()).not.toThrow()
      expect(take()).toEqual(structural)
      expect(screen(host.globalGroup)).toBeUndefined()
      const packet = buildSceneSnapshot({ root: host.globalGroup, project: p, helpers: host.registries.renderHelpers })
      expect(packet.primitives.length).toBeGreaterThan(0)
      expect(packet.primitives.every(primitive => primitive.modelUid === 'm_30')).toBe(true)
      expect(JSON.stringify(p)).toBe(before)
    } finally { globals.pbrMaterials = library }
    builder.rebuild()
    expect(screen(host.globalGroup)).toBeDefined()
    expect(take()).toEqual(structural)
    expect(JSON.stringify(p)).toBe(before)
  })

  it('draws an honest static service schematic on an unlit sRGB HMI', () => {
    const labels = canvasDrawing(), group = visual(), hmi = screen(group)
    expect(hmi.material).toBeInstanceOf(THREE.MeshBasicMaterial)
    expect(hmi.material.toneMapped).toBe(false)
    expect(hmi.material.map).toBeInstanceOf(THREE.CanvasTexture)
    expect(hmi.material.map!.colorSpace).toBe(THREE.SRGBColorSpace)
    expect(labels.get(hmi.material.map!.image as HTMLCanvasElement)).toEqual(['CDU', 'PRIMARY', 'SECONDARY', 'SERVICE'])
    expect(hmi.userData).toMatchObject({ modelId: 'm_30', selectable: true, liquidVisualDetail: true, exportExcluded: true })
    expect(group.getObjectByName('generic-cdu-hmi-line-0')).toBeUndefined()
  })

  it('softens only CDU cabinet paint and keeps other shared PBR surfaces unchanged', () => {
    landPaint()
    const group = visual()
    for (const name of ['generic-cdu-cabinet', 'generic-cdu-service-door']) {
      const mat = (group.getObjectByName(name) as THREE.Mesh).material as THREE.MeshStandardMaterial
      expect(mat.metalness).toBeGreaterThanOrEqual(.05)
      expect(mat.metalness).toBeLessThanOrEqual(.1)
      expect(mat.normalScale.toArray()).toEqual([.16, .16])
      expect(mat.userData.pbrNormalScale).toBe(.16)
      expect(mat.userData.pbrRole).toBe('painted-steel')
    }
    const marker = group.getObjectByName('generic-cdu-supply-marker') as THREE.Mesh
    expect((marker.material as THREE.MeshStandardMaterial).userData.pbrNormalScale).toBe(.6)
    expect(pbrMaterials().ROLES['painted-steel']!.normalScale).toBe(.6)
  })

  it('retains the HMI map after whole-cabinet highlight and shares no owned display texture', () => {
    canvasDrawing()
    const group = visual(), other = visual(), hmi = screen(group), original = hmi.material, map = original.map
    expect(map).toBeTruthy()
    expect(map).not.toBe(screen(other).material.map)
    const roots: Array<[string, THREE.Object3D]> = [['m_30', group]]
    paintSelectionHighlights(roots, { uid: 'm_30', node: null, uids: new Set(['m_30']) }, () => () => null, NO_VIEW_FADES, () => 'cdu')
    expect(hmi.material).not.toBe(original)
    expect(hmi.material.color.getHex()).toBe(0x66ead0)
    expect(hmi.material.map).toBeNull()
    paintSelectionHighlights(roots, { uid: null, node: null, uids: new Set() }, () => () => null, NO_VIEW_FADES, () => 'cdu')
    expect(hmi.material).toBe(original)
    expect(hmi.material.map).toBe(map)
  })

  it.each([false, true])('clears owned HMI resources once with selected=%s without disposing cached paint', selected => {
    canvasDrawing()
    const cached = landPaint(), cachedEvents = cached.map(() => vi.fn())
    cached.forEach((texture, index) => texture.addEventListener('dispose', cachedEvents[index]!))
    const { host, builder } = scene(project(), true), hmi = screen(host.globalGroup), original = hmi.material
    expect(original.map).toBeTruthy()
    const textureEvent = vi.fn(), materialEvent = vi.fn()
    original.map!.addEventListener('dispose', textureEvent)
    original.addEventListener('dispose', materialEvent)
    if (selected) paintSelectionHighlights(host.registries.renderRootMap, {
      uid: 'm_30', node: null, uids: new Set(['m_30']),
    }, () => () => null, NO_VIEW_FADES, () => 'cdu')
    builder.clear()
    expect(textureEvent).toHaveBeenCalledOnce()
    expect(materialEvent).toHaveBeenCalledOnce()
    for (const event of cachedEvents) expect(event).not.toHaveBeenCalled()
  })

  it.each([
    { name: 'selected', selected: true, opacity: false },
    { name: 'opacity-owned', selected: false, opacity: true },
    { name: 'opacity-owned then selected', selected: true, opacity: true },
  ])('releases every private CDU detail material on clear: $name', ({ selected, opacity }) => {
    canvasDrawing()
    const cached: THREE.Texture[] = []
    for (const set of Object.keys(pbrMaterials().SETS)) {
      for (const type of ['diffuse', 'normal', 'roughness'] as const) {
        const texture = new THREE.Texture({ width: 2, height: 2, toDataURL: () => 'data:image/png;base64,cGJy' } as unknown as HTMLCanvasElement)
        cached.push(texture)
        pbrMaterials().registerTexture(set, type, texture)
      }
    }
    const cacheEvents = cached.map(() => vi.fn())
    cached.forEach((texture, index) => texture.addEventListener('dispose', cacheEvents[index]!))
    const { host, builder } = scene(project(), true)
    const root = host.registries.renderRootMap.get('m_30')!
    const detail: THREE.Mesh[] = []
    root.traverse(object => {
      if ((object as THREE.Mesh).isMesh && object.userData.liquidVisualDetail) detail.push(object as THREE.Mesh)
    })
    expect(detail.length).toBeGreaterThan(20)
    const originals = detail.map(mesh => mesh.material as THREE.Material)
    const allOwned = new Map<THREE.Material, ReturnType<typeof vi.fn>>()
    const track = (material: THREE.Material) => {
      if (!allOwned.has(material)) {
        const event = vi.fn()
        material.addEventListener('dispose', event)
        allOwned.set(material, event)
      }
    }
    originals.forEach(track)
    if (opacity) applyOpacityToMeshes(root, 37)
    detail.forEach(mesh => track(mesh.material as THREE.Material))
    if (selected) paintSelectionHighlights(host.registries.renderRootMap, {
      uid: 'm_30', node: null, uids: new Set(['m_30']),
    }, () => () => null, NO_VIEW_FADES, () => 'cdu')
    detail.forEach(mesh => track(mesh.material as THREE.Material))
    builder.clear()
    console.info('CDU_PRIVATE_MATERIAL_DISPOSAL', {
      selected, opacity, owned: allOwned.size,
      disposed: [...allOwned.values()].filter(event => event.mock.calls.length === 1).length,
    })
    for (const [material, event] of allOwned) expect(event, material.type + '/' + material.uuid).toHaveBeenCalledOnce()
    for (const event of cacheEvents) expect(event).not.toHaveBeenCalled()
  })

  it('provides a dark nontextured HMI when a 2D canvas is unavailable', () => {
    vi.stubGlobal('CanvasRenderingContext2D', undefined)
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    const hmi = screen(visual())
    expect(hmi.material).toBeInstanceOf(THREE.MeshBasicMaterial)
    expect(hmi.material.map).toBeNull()
    expect(hmi.material.color.getHex()).toBe(0x123138)
    expect(getContext).not.toHaveBeenCalled()
  })

  it('does not fabricate a display texture on the server without a document', () => {
    vi.stubGlobal('document', undefined)
    expect(createCduDisplayTexture()).toBeNull()
  })

  it.each(['null', 'throw'])('falls back honestly when an available canvas context returns %s', failure => {
    vi.stubGlobal('CanvasRenderingContext2D', class {})
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => {
      if (failure === 'throw') throw new Error('No canvas backend')
      return null
    })
    const hmi = screen(visual())
    expect(hmi.material.map).toBeNull()
    expect(hmi.material.color.getHex()).toBe(0x123138)
    expect(getContext).toHaveBeenCalledOnce()
  })

  it('carries the static screen through the actual supported OV snapshot without shader-only maps', () => {
    canvasDrawing()
    const p = project(), { host } = scene(p, true)
    const packet = buildSceneSnapshot({ root: host.globalGroup, project: p, helpers: host.registries.renderHelpers })
    const screens = packet.primitives.filter(primitive => primitive.name === 'generic-cdu-hmi-screen')
    expect(screens.length).toBeGreaterThan(0)
    for (const primitive of screens) {
      expect(primitive.modelUid).toBe('m_30')
      expect(primitive.material.unlit).toBe(true)
      expect(primitive.material.mapColorSpace).toBe('srgb')
      expect(packet.textures[primitive.material.map!]).toMatch(/^data:image\/png;base64,/)
      expect(primitive.uvs?.every(Number.isFinite)).toBe(true)
    }
  })

  it('keeps the real SceneBuilder structural buffers, grouped STL, ports and source JSON exact', () => {
    const p = project(), before = JSON.stringify(p), model = (stateV4().allModels(p) as Construction[]).find(item => item.uid === 'm_30')! as Construction
    const ports = () => ['supply', 'return'].map(port => {
      const frame = geometryPure().liquidEquipmentPortFrame(model, port, 'top')!
      return [frame.position.toArray(), frame.direction.toArray()]
    })
    const originalPorts = ports()
    const take = (host: SceneHost, builder: ReturnType<typeof createSceneBuilder>) => ({
      buffers: host.registries.renderMeshes.map(object => {
        const mesh = object as THREE.Mesh
        return {
        exportName: mesh.userData.exportName, faceId: mesh.userData.faceId,
        matrix: mesh.matrixWorld.toArray(), index: mesh.geometry.index ? Array.from(mesh.geometry.index.array) : null,
        attributes: Object.fromEntries(Object.entries(mesh.geometry.attributes).map(([name, attribute]) => [
          name, { itemSize: attribute.itemSize, normalized: attribute.normalized, data: Array.from(attribute.array) },
        ])),
      } }),
      stl: builder.api.buildGroupedStlMap(['m_30'], true),
    })
    const air = scene(p, false), baseline = take(air.host, air.builder)
    const liquid = scene(p, true), actual = take(liquid.host, liquid.builder)
    expect(baseline.buffers.length).toBeGreaterThan(0)
    expect(Object.keys(baseline.stl).length).toBeGreaterThan(0)
    expect(actual).toEqual(baseline)
    expect(ports()).toEqual(originalPorts)
    expect(JSON.stringify(p)).toBe(before)
    const digest = createHash('sha256').update(JSON.stringify(actual)).digest('hex')
    // Captured from the real pre-change SceneBuilder/STL exporter, not a
    // parallel reimplementation of box geometry.
    expect(digest).toBe('cc709a57cd2bbdd7e66257709aa5d0a86ed6b6bd1aee5b0032043b22a97fd48d')
    console.info('CDU_STRUCTURAL_EXPORT_SHA256', digest)
    const bounds = new THREE.Box3().setFromObject(liquid.host.globalGroup)
    expect([bounds.min.toArray(), bounds.max.toArray()]).toEqual([
      [6.28893749713898, .8, 4.000972474864126],
      [6.892537522315982, 2.8469999947547917, 5.219872522354127],
    ])
    console.info('CDU_VISUAL_BOUNDS', JSON.stringify([bounds.min.toArray(), bounds.max.toArray()]))
    const hmi = screen(liquid.host.globalGroup)
    const screenDigest = createHash('sha256').update(JSON.stringify({
      attributes: Object.fromEntries(Object.entries(hmi.geometry.attributes).map(([name, attr]) => [name, Array.from(attr.array)])),
      index: hmi.geometry.index ? Array.from(hmi.geometry.index.array) : null, matrix: hmi.matrixWorld.toArray(),
    })).digest('hex')
    expect(screenDigest).toBe('547c0bc2c082393f16b9fc55410d6b868df3c213d4791128b9ad9e2a28c556b3')
    console.info('CDU_HMI_GEOMETRY_SHA256', screenDigest)
  })
})
