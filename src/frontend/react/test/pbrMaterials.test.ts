import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { pbrMaterials } from '../src/legacy/geometryBuilder'

/**
 * The liquid-equipment PBR library (shared/js/pbr_materials.js).
 *
 * The images never load in jsdom, so these tests drive the loader's onLoad
 * callbacks by hand with canvas-like fake images — the same shape the render
 * snapshot serialises.
 */

const TEXTURES = resolve(process.cwd(), '../shared/textures')
type ManifestSet = { tintable: boolean; source: { kind: string }; stats: { meanLuminance: number; medianLuminance: number; meanRoughness: number }; maps: Record<string, { file: string; bytes: number; size: number[] }> }
const manifest = JSON.parse(readFileSync(resolve(TEXTURES, 'materials.json'), 'utf8')) as {
  sets: Record<string, ManifestSet>
  materials: Record<string, { library?: boolean; set?: string | null; role?: string; roughness?: number; metallic?: number; normalScale?: number; tileM?: number; baseColor?: string }>
}

function fakeTexture(tag: string): THREE.Texture {
  const image = { width: 4, height: 4, tag, toDataURL: () => `data:image/png;base64,${btoa(tag)}` }
  return new THREE.Texture(image as unknown as HTMLCanvasElement)
}

type LoadCall = { url: string; onLoad: (t: THREE.Texture) => void }
function captureLoads(): LoadCall[] {
  const calls: LoadCall[] = []
  vi.spyOn(THREE.TextureLoader.prototype, 'load').mockImplementation(((url: string, onLoad?: (t: THREE.Texture) => void) => {
    calls.push({ url, onLoad: onLoad ?? (() => undefined) })
    return new THREE.Texture()
  }) as unknown as THREE.TextureLoader['load'])
  return calls
}
function landAll(calls: LoadCall[], only?: (url: string) => boolean) {
  for (const c of calls) if (!only || only(c.url)) c.onLoad(fakeTexture(c.url))
}

beforeEach(() => pbrMaterials()._resetForTests())
afterEach(() => { vi.restoreAllMocks(); pbrMaterials()._resetForTests() })

describe('the library comes from materials.json', () => {
  it('every role is a manifest library material, with its numbers', () => {
    const lib = pbrMaterials()
    const roles = Object.entries(manifest.materials).filter(([, m]) => m.library).map(([k]) => k)
    expect(Object.keys(lib.ROLES).sort()).toEqual(roles.sort())
    for (const role of roles) {
      const m = manifest.materials[role]!
      expect(lib.ROLES[role]).toMatchObject({ set: m.set, roughness: m.roughness, metalness: m.metallic, normalScale: m.normalScale, tileM: m.tileM,
        color: parseInt(m.baseColor!.slice(1), 16) })
    }
  })

  it('every set carries the mean roughness measured on its encoded map', () => {
    const lib = pbrMaterials()
    expect(Object.keys(lib.SETS).sort()).toEqual(Object.keys(manifest.sets).sort())
    for (const [set, meta] of Object.entries(lib.SETS)) {
      expect(meta.meanRoughness, set).toBe(manifest.sets[set]!.stats.meanRoughness)
      expect(meta.tintable, set).toBe(manifest.sets[set]!.tintable)
    }
  })

  it('the library sets are real JPEGs on disk, small enough to ship per view', () => {
    const lib = pbrMaterials()
    for (const role of Object.keys(lib.ROLES)) {
      const set = lib.ROLES[role]!.set
      for (const map of ['diffuse', 'normal', 'roughness'] as const) {
        const path = resolve(TEXTURES, lib.mapFile(set, map))
        expect(existsSync(path), path).toBe(true)
        // A Git LFS pointer is text; a real JPEG starts FF D8 FF.
        expect([...readFileSync(path).subarray(0, 3)], `${path} is not a JPEG (LFS not smudged?)`).toEqual([0xff, 0xd8, 0xff])
      }
    }
  })
})

describe('one texture registry for every set in use', () => {
  it('loadTextureSets requests each map of every set some material draws with, once per base', () => {
    const lib = pbrMaterials()
    const calls = captureLoads()
    lib.loadTextureSets('/textures')
    lib.loadTextureSets('/textures')
    const used = new Set<string>()
    for (const m of Object.values(manifest.materials)) {
      const r = m.role ? manifest.materials[m.role]! : m
      if (r.set) used.add(r.set)
    }
    const want = [...used].flatMap((set) => ['color', 'normal', 'roughness'].map((k) => '/textures/' + manifest.sets[set]!.maps[k]!.file))
    expect(calls.map((c) => c.url).sort()).toEqual(want.sort())
  })

  it('texture() answers only once the whole set is in, and onSetReady fires once per set', () => {
    const lib = pbrMaterials()
    const calls = captureLoads()
    const ready: string[] = []
    lib.onSetReady((set) => ready.push(set))
    lib.loadTextureSets('/textures')
    landAll(calls, (u) => u.startsWith('/textures/wall-paint/') && !u.endsWith('roughness.jpg'))
    expect(lib.texture('wall-paint', 'diffuse')).toBeNull()
    expect(ready).toEqual([])
    landAll(calls, (u) => u === '/textures/wall-paint/roughness.jpg')
    expect(lib.texture('wall-paint', 'diffuse')).not.toBeNull()
    expect(lib.texture('wall-paint', 'roughness')!.colorSpace).toBe(THREE.NoColorSpace)
    expect(lib.texture('wall-paint', 'diffuse')!.name).toBe('pbr:wall-paint/color.jpg')
    expect(ready).toEqual(['wall-paint'])
  })
})

describe('pbrMaterials.material', () => {
  it('builds a fresh MeshStandardMaterial per call with the role surface', () => {
    const lib = pbrMaterials()
    const a = lib.material('stainless')
    const b = lib.material('stainless')
    expect(a).toBeInstanceOf(THREE.MeshStandardMaterial)
    expect(a).not.toBe(b)
    expect(a.metalness).toBeCloseTo(lib.ROLES.stainless!.metalness)
    expect(a.roughness).toBeCloseTo(lib.ROLES.stainless!.roughness)
    expect(a.map).toBeNull()
    expect(a.userData).toMatchObject({ pbrRole: 'stainless', pbrSet: 'brushed-stainless' })
  })

  it('keeps caller options (colour, opacity, emissive) and never attaches maps before the images load', () => {
    const lib = pbrMaterials()
    const calls = captureLoads()
    lib.loadTextureSets('/textures')
    const m = lib.material('pipe-insulation', { color: 0x2196f3, transparent: true, opacity: 0.85 })
    expect(m.color.getHex()).toBe(0x2196f3)
    expect(m.opacity).toBe(0.85)
    expect(m.map).toBeNull()
    expect(m.normalMap).toBeNull()
    expect(calls.map((c) => c.url)).toContain('/textures/pipe-insulation/normal.jpg')
  })

  it('late-binds the maps to materials built before the set landed', () => {
    const lib = pbrMaterials()
    const calls = captureLoads()
    lib.loadTextureSets('/textures')
    const early = lib.material('pipe-insulation', { color: 0xf44336 })
    const version = early.version
    landAll(calls, (u) => u.includes('/pipe-insulation/') && !u.endsWith('roughness.jpg'))
    expect(early.map).toBeNull() // incomplete set: nothing bound yet
    landAll(calls, (u) => u.endsWith('/pipe-insulation/roughness.jpg'))
    expect(early.map).not.toBeNull()
    expect(early.normalMap).not.toBeNull()
    expect(early.roughnessMap).not.toBeNull()
    expect(early.map!.colorSpace).toBe(THREE.SRGBColorSpace)
    expect(early.roughnessMap!.colorSpace).toBe(THREE.NoColorSpace)
    expect(early.version).toBeGreaterThan(version)
    // the map encodes absolute roughness, so the scalar is (wanted / mean)
    expect(early.roughness).toBeCloseTo(Math.min(1, lib.ROLES['pipe-insulation']!.roughness / lib.SETS['pipe-insulation']!.meanRoughness))
    expect(early.color.getHex()).toBe(0xf44336)
    const late = lib.material('pipe-insulation')
    expect(late.map).toBe(early.map) // one shared texture per image
  })

  it('does not queue materials when no host requested the images (jsdom, headless export)', () => {
    const lib = pbrMaterials()
    const m = lib.material('rubber')
    lib.registerTexture('rubber', 'diffuse', fakeTexture('a'))
    lib.registerTexture('rubber', 'normal', fakeTexture('b'))
    lib.registerTexture('rubber', 'roughness', fakeTexture('c'))
    expect(m.map).toBeNull()
    expect(lib.material('rubber').map).not.toBeNull()
  })

  it('keeps the scalar roughness inside [0, 1] when the role asks for more than the map mean', () => {
    const lib = pbrMaterials()
    for (const t of ['diffuse', 'normal', 'roughness'] as const) lib.registerTexture('copper', t, fakeTexture(t))
    const m = lib.material('copper', { roughness: 0.9 })
    expect(m.roughness).toBeLessThanOrEqual(1)
    expect(m.roughness).toBeGreaterThan(0)
  })
})

describe('physical UV tiling', () => {
  it('tubes tile along their length and wrap whole tiles around', () => {
    const lib = pbrMaterials()
    const g = new THREE.TubeGeometry(new THREE.LineCurve3(new THREE.Vector3(), new THREE.Vector3(2, 0, 0)), 4, 0.015, 8)
    lib.tubeUv(g, 2, 0.015, 'pipe-insulation')
    const uv = g.getAttribute('uv')
    let maxU = 0, maxV = 0
    for (let i = 0; i < uv.count; i++) { maxU = Math.max(maxU, uv.getX(i)); maxV = Math.max(maxV, uv.getY(i)) }
    expect(maxU).toBeCloseTo(2 / lib.ROLES['pipe-insulation']!.tileM)
    expect(maxV).toBeCloseTo(1)
    expect(() => lib.tubeUv(g, 2, 0.015, 'pipe-insulation')).toThrow(/already scaled/)
  })

  it('cylinders swap axes so the texture u axis follows the cylinder axis', () => {
    const lib = pbrMaterials()
    const g = new THREE.CylinderGeometry(0.02, 0.02, 1.6, 8, 1, true)
    lib.cylinderUv(g, 0.02, 1.6, 'stainless', 0.2)
    const uv = g.getAttribute('uv')
    let maxU = 0
    for (let i = 0; i < uv.count; i++) maxU = Math.max(maxU, uv.getX(i))
    expect(maxU).toBeCloseTo(8) // 1.6 m / 0.2 m along the axis
  })

  it('boxes tile each face at its own size', () => {
    const lib = pbrMaterials()
    const g = new THREE.BoxGeometry(0.6, 2.0, 1.2)
    lib.boxUv(g, 'painted-steel', 0.4)
    const uv = g.getAttribute('uv')
    const face = (k: number) => { let u = 0, v = 0; for (let i = k * 4; i < k * 4 + 4; i++) { u = Math.max(u, uv.getX(i)); v = Math.max(v, uv.getY(i)) } return [u, v] }
    expect(face(0)[0]).toBeCloseTo(3) // +x: depth 1.2 / 0.4
    expect(face(0)[1]).toBeCloseTo(5) // height 2.0 / 0.4
    expect(face(2)[0]).toBeCloseTo(1.5) // +y: width 0.6 / 0.4
    expect(face(4)[1]).toBeCloseTo(5) // +z: height
  })
})
