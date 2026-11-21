import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createSceneBuilder, emptyRegistries } from '../src/interaction/sceneBuilder'
import { setLiquidModeActive } from '../src/liquid/liquidModeState'
import { pbrMaterials } from '../src/legacy/geometryBuilder'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import type { CfdProject } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'

/**
 * Rack manifolds (geometry_pure), the React liquid-equipment detail and the
 * CDU / cold-plate bodies (geometry_scene) take their surfaces from the shared
 * PBR library, keep every identity tag they had, and still export to the
 * Omniverse render snapshot once the (fake) images have landed.
 */

function fakeTexture(tag: string): THREE.Texture {
  const image = { width: 2, height: 2, toDataURL: () => `data:image/png;base64,${btoa(tag)}` }
  return new THREE.Texture(image as unknown as HTMLCanvasElement)
}
function landEverySet() {
  const lib = pbrMaterials()
  for (const set of Object.keys(lib.SETS)) {
    for (const map of ['diffuse', 'normal', 'roughness'] as const) lib.registerTexture(set, map, fakeTexture(`${set}/${map}`))
  }
}

function shinaide(): CfdProject {
  const raw = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/verification/shinaide-legacy-projects/Shinaide_liquid.json'), 'utf8'))
  return stateV4().normalizeStateV4(raw) as unknown as CfdProject
}

beforeEach(() => pbrMaterials()._resetForTests())
afterEach(() => { pbrMaterials()._resetForTests(); setLiquidModeActive(false) })

describe('liquid equipment surfaces', () => {
  it('manifolds and CDU/rack detail are textured MeshStandardMaterial with identities intact, and export to OV', () => {
    landEverySet()
    const project = shinaide()
    const globalGroup = new THREE.Group()
    const wrapperGroup = new THREE.Group()
    wrapperGroup.add(globalGroup)
    const registries = emptyRegistries()
    setLiquidModeActive(true)
    const builder = createSceneBuilder({ globalGroup, wrapperGroup, registries, selectedUid: () => null, project: () => project, webglAvailable: false })
    try {
      builder.rebuild()
      const manifolds: THREE.Mesh[] = []
      const details: THREE.Mesh[] = []
      globalGroup.traverse((o) => {
        if (!(o as THREE.Mesh).isMesh) return
        if (o.userData.isLiquidManifold) manifolds.push(o as THREE.Mesh)
        if (o.userData.liquidVisualDetail) details.push(o as THREE.Mesh)
      })
      expect(manifolds.length).toBeGreaterThan(0)
      expect(details.length).toBeGreaterThan(0)

      const headers = manifolds.filter((m) => m.userData.pipeType)
      expect(headers.length).toBeGreaterThan(0)
      for (const m of headers) {
        const mat = m.material as THREE.MeshStandardMaterial
        expect(mat).toBeInstanceOf(THREE.MeshStandardMaterial)
        expect(mat.userData.pbrRole).toBe('stainless')
        expect(mat.map && mat.normalMap && mat.roughnessMap).toBeTruthy()
        expect(m.userData).toMatchObject({ isLiquidManifold: true, modelId: expect.any(String) })
        expect(mat.color.getHex()).toBe(m.userData.pipeType === 'supply' ? 0x2196f3 : 0xf44336)
      }
      const stubs = manifolds.filter((m) => m.userData.stubType)
      for (const m of stubs) expect((m.material as THREE.MeshStandardMaterial).userData.pbrRole).toBe('rubber')
      if (stubs.length > 1) expect(stubs[0]!.material).not.toBe(stubs[1]!.material)

      const roles = new Set(details.map((m) => (m.material as THREE.MeshStandardMaterial).userData.pbrRole))
      expect(roles.has('stainless')).toBe(true)
      expect(roles.has('painted-steel')).toBe(true)
      const cabinet = details.find((m) => m.name === 'generic-cdu-cabinet')!
      expect((cabinet.material as THREE.MeshStandardMaterial).userData.pbrRole).toBe('painted-steel')
      expect(cabinet.userData).toMatchObject({ liquidVisualDetail: true, exportExcluded: true, selectable: true, modelId: expect.any(String) })
      const screen = details.find((m) => m.name === 'generic-cdu-hmi-screen')!
      expect((screen.material as THREE.MeshStandardMaterial).map).toBeNull()

      const packet = buildSceneSnapshot({ root: globalGroup, project, helpers: registries.renderHelpers })
      // Details remain display-only even after getting their model's pick
      // owner. They are absent from the structural/STL registry, but their
      // standard PBR surfaces and exact model identity reach OV.
      for (const detail of details) expect(registries.renderMeshes).not.toContain(detail)
      const cabinetPrimitives = packet.primitives.filter((p) => p.name === cabinet.name)
      expect(cabinetPrimitives.length).toBeGreaterThan(0)
      for (const p of cabinetPrimitives) {
        expect(p.modelUid).toBe(cabinet.userData.modelId)
        expect(p.material.map && p.material.normalMap && p.material.roughnessMap).toBeTruthy()
        expect(p.positions.every(Number.isFinite)).toBe(true)
        expect(p.uvs?.every(Number.isFinite)).toBe(true)
      }
      const header = packet.primitives.find((p) => p.material.roughnessMap && p.material.metalness === 0.6)
      expect(header, 'a textured manifold primitive').toBeDefined()
      expect(header!.material.map).toBeDefined()
      expect(header!.material.normalMap).toBeDefined()
      expect(header!.uvs).toBeDefined()
      expect(header!.material.roughness).toBeLessThanOrEqual(1)
      // every texture referenced resolves to an embedded PNG
      for (const p of packet.primitives) {
        for (const key of ['map', 'normalMap', 'roughnessMap'] as const) {
          const id = p.material[key]
          if (id) expect(packet.textures[id]).toMatch(/^data:image\/png;base64,/)
        }
      }
    } finally {
      builder.clear()
    }
  })
})

describe('CDU and cold-plate bodies', () => {
  function sceneApi() {
    const globalGroup = new THREE.Group()
    const wrapperGroup = new THREE.Group()
    wrapperGroup.add(globalGroup)
    const builder = createSceneBuilder({
      globalGroup, wrapperGroup, registries: emptyRegistries(), selectedUid: () => null,
      project: () => null, webglAvailable: false,
    })
    return builder.api as unknown as {
      createMaterialForType(kind: string, options: Record<string, unknown>): THREE.Material
    }
  }

  it('a CDU face is powder-coated steel in the CDU colour', () => {
    landEverySet()
    const m = sceneApi().createMaterialForType('cdu-body', { color: '#007a99', opacity: 1 }) as THREE.MeshStandardMaterial
    expect(m).toBeInstanceOf(THREE.MeshStandardMaterial)
    expect(m.userData.pbrRole).toBe('painted-steel')
    expect(m.color.getHexString()).toBe('007a99')
    expect(m.map).not.toBeNull()
    expect(m.polygonOffset).toBe(true)
  })

  it('a cold plate is copper: copper-coloured before the map lands, hue from the map after', () => {
    const before = sceneApi().createMaterialForType('cold-plate', { color: '#ff00ff', opacity: 1 }) as THREE.MeshStandardMaterial
    expect(before.userData.pbrRole).toBe('copper')
    expect(before.color.getHex()).toBe(0xb87333)
    landEverySet()
    const after = sceneApi().createMaterialForType('cold-plate', { color: '#ff00ff', opacity: 1 }) as THREE.MeshStandardMaterial
    expect(after.color.getHex()).toBe(0xffffff)
    expect(after.map).not.toBeNull()
    expect(after.metalness).toBeGreaterThan(0.8)
  })
})
