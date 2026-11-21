import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { inflateSync } from 'node:zlib'
import * as THREE from 'three'
import { describe, expect, it, vi } from 'vitest'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import { crc32, encodePngRgba } from '../src/renderer/pngEncode'
import { geometryPure } from '../src/legacy/geometryBuilder'
import { createSceneBuilder, emptyRegistries } from '../src/interaction/sceneBuilder'
import { stateV4 } from '../src/legacy/cfdprojModules'
import type { CfdProject } from '../src/schema/cfdproj'

/**
 * The Omniverse render snapshot: textures deduplicated by image, preset PBR
 * values carried through, and the perforated rack door exported instead of
 * refused.
 */

const project = { uid: 'project-uid', name: 'pbr' }

function quad(uvScale = 1) {
  const g = new THREE.PlaneGeometry(1, 1)
  if (uvScale !== 1) {
    const uv = g.getAttribute('uv')
    for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) * uvScale, uv.getY(i) * uvScale)
  }
  return g
}
function rootWith(...meshes: THREE.Mesh[]) {
  const root = new THREE.Group()
  const owner = new THREE.Group()
  owner.userData.modelId = 'rack-uid'
  root.add(owner)
  meshes.forEach((m) => owner.add(m))
  return root
}
function image(data: string, src?: string) {
  const toDataURL = vi.fn(() => data)
  return { img: { width: 2, height: 2, toDataURL, ...(src ? { src } : {}) }, toDataURL }
}
function texture(img: object, colorSpace: THREE.ColorSpace = THREE.SRGBColorSpace) {
  const t = new THREE.Texture(img as unknown as HTMLCanvasElement)
  t.colorSpace = colorSpace
  t.wrapS = t.wrapT = THREE.RepeatWrapping
  return t
}
const PNG_A = 'data:image/png;base64,iVBORw0KGgoA'
const PNG_B = 'data:image/png;base64,iVBORw0KGgoB'

describe('texture dedup by image, not by texture object', () => {
  it('clones at different repeats (one Texture.source) serialise once', () => {
    const { img, toDataURL } = image(PNG_A)
    const base = texture(img)
    const tiled = base.clone()
    tiled.repeat.set(4, 4)
    const a = new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: base }))
    const b = new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: tiled }))
    const packet = buildSceneSnapshot({ root: rootWith(a, b), project })
    expect(Object.keys(packet.textures)).toHaveLength(1)
    expect(toDataURL).toHaveBeenCalledTimes(1)
    expect(packet.primitives[0]!.material.map).toBe(packet.primitives[1]!.material.map)
    // the per-material repeat is baked into each primitive's own UVs
    expect(Math.max(...packet.primitives[0]!.uvs!)).toBeCloseTo(1)
    expect(Math.max(...packet.primitives[1]!.uvs!)).toBeCloseTo(4)
  })

  it('two loads of one URL serialise once', () => {
    const one = image(PNG_A, 'http://host/textures/rack-top/color.jpg')
    const two = image(PNG_A, 'http://host/textures/rack-top/color.jpg')
    const a = new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: texture(one.img) }))
    const b = new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: texture(two.img) }))
    const packet = buildSceneSnapshot({ root: rootWith(a, b), project })
    expect(Object.keys(packet.textures)).toHaveLength(1)
    expect(one.toDataURL.mock.calls.length + two.toDataURL.mock.calls.length).toBe(1)
  })

  it('byte-identical images from different files share one entry; different images do not', () => {
    const a = new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: texture(image(PNG_A, 'http://h/floor-vent/color.jpg').img) }))
    const b = new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: texture(image(PNG_A, 'http://h/acu-return/color.jpg').img) }))
    const c = new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: texture(image(PNG_B).img) }))
    const packet = buildSceneSnapshot({ root: rootWith(a, b, c), project })
    expect(Object.keys(packet.textures)).toHaveLength(2)
    const [pa, pb, pc] = packet.primitives
    expect(pa!.material.map).toBe(pb!.material.map)
    expect(pc!.material.map).not.toBe(pa!.material.map)
    expect(packet.textures[pc!.material.map!]).toBe(PNG_B)
  })

  it('a loaded JPEG travels as JPEG, a PNG source as PNG, and a resend reuses the encoding', () => {
    const encode = vi.fn((mime: string) => `data:${mime};base64,${mime === 'image/jpeg' ? '/9j/' : 'iVBORw0KGgo='}`)
    const realCreate = document.createElement.bind(document)
    const spy = vi.spyOn(document, 'createElement').mockImplementation(((tag: string) => tag === 'canvas'
      ? { width: 0, height: 0, getContext: () => ({ drawImage() {} }), toDataURL: encode }
      : realCreate(tag)) as typeof document.createElement)
    try {
      const jpg = { width: 2, height: 2, src: 'http://h/textures/rack-top/color.jpg?v=2' }
      const png = { width: 2, height: 2, src: 'http://h/textures/MISC/ACU%20Return%203%20Panel.png' }
      const a = new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: texture(jpg) }))
      const b = new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: texture(png) }))
      const first = buildSceneSnapshot({ root: rootWith(a, b), project })
      expect(first.textures[first.primitives[0]!.material.map!]).toMatch(/^data:image\/jpeg;base64,/)
      expect(first.textures[first.primitives[1]!.material.map!]).toMatch(/^data:image\/png;base64,/)
      expect(encode.mock.calls.map(c => c[0])).toEqual(['image/jpeg', 'image/png'])
      buildSceneSnapshot({ root: rootWith(a, b), project })
      expect(encode).toHaveBeenCalledTimes(2)
    } finally { spy.mockRestore() }
  })

  it('one image used as colour AND data map keeps both colour spaces on the material', () => {
    const { img } = image(PNG_A)
    const color = texture(img, THREE.SRGBColorSpace)
    const normal = color.clone()
    normal.colorSpace = THREE.NoColorSpace
    const m = new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: color, normalMap: normal }))
    const packet = buildSceneSnapshot({ root: rootWith(m), project })
    expect(packet.primitives[0]!.material).toMatchObject({ mapColorSpace: 'srgb', normalColorSpace: 'linear' })
    expect(Object.keys(packet.textures)).toHaveLength(1)
  })
})

describe('PBR values', () => {
  it('a Phong material ignores any userData hint: the scene has no Phong surfaces left to hint for', () => {
    const phong = new THREE.MeshPhongMaterial({ color: 0x3a3d42, shininess: 30 })
    phong.userData.pbr = { roughness: 0.65, metalness: 0.3 }
    const packet = buildSceneSnapshot({ root: rootWith(new THREE.Mesh(quad(), phong)), project })
    expect(packet.primitives[0]!.material.roughness).toBeCloseTo(0.25)
    expect(packet.warnings.some((w) => w.startsWith('Phong shininess'))).toBe(true)
  })

  it('a Phong material converts shininess, and says so', () => {
    const packet = buildSceneSnapshot({ root: rootWith(new THREE.Mesh(quad(), new THREE.MeshPhongMaterial({ shininess: 30 }))), project })
    expect(packet.primitives[0]!.material.roughness).toBeCloseTo(0.25)
    expect(packet.primitives[0]!.material.metalness).toBe(0)
    expect(packet.warnings.some((w) => w.startsWith('Phong shininess'))).toBe(true)
  })

  it('a standard material keeps its own scalars (its roughness map multiplies them)', () => {
    const std = new THREE.MeshStandardMaterial({ roughness: 1, metalness: 0.8 })
    std.userData.pbr = { roughness: 0.34, metalness: 0.8 }
    const packet = buildSceneSnapshot({ root: rootWith(new THREE.Mesh(quad(), std)), project })
    expect(packet.primitives[0]!.material).toMatchObject({ roughness: 1, metalness: 0.8 })
  })
})

describe('perforated rack door', () => {
  function door() {
    const make = geometryPure()._rackDoorMaterial as (perforation: object, w: number, h: number) => THREE.MeshStandardMaterial
    const mat = make({ shape: 'hexagon', openAreaPct: 50, holeDiameterMm: 5 }, 0.6, 2.0)
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 2.0), mat)
    mesh.userData = { modelId: 'rack-uid', isDoor: true, doorSide: 'inlet', exportName: 'rack_inlet_door' }
    return { mat, mesh }
  }

  it('exports as an alpha-tested, textured standard material with the shader pattern, pitch and steel', () => {
    const { mat, mesh } = door()
    const spec = mat.userData.renderSnapshot as { cells: [number, number]; opacity: number; roughness: number; metalness: number }
    const packet = buildSceneSnapshot({ root: rootWith(mesh), project })
    const p = packet.primitives[0]!
    expect(p.material).toMatchObject({ opacity: spec.opacity, alphaTest: spec.opacity * 0.5, doubleSided: true, unlit: false,
      roughness: spec.roughness, metalness: spec.metalness, wrapS: 'repeat', wrapT: 'repeat', mapColorSpace: 'srgb' })
    // the steel's colour, linear, as the editor draws it
    expect(p.material.color).toEqual(mat.color.toArray())
    expect(p.material.map).toBeDefined()
    const us = p.uvs!.filter((_, i) => i % 2 === 0)
    const vs = p.uvs!.filter((_, i) => i % 2 === 1)
    expect(Math.max(...us)).toBeCloseTo(spec.cells[0], 4)
    expect(Math.max(...vs)).toBeCloseTo(spec.cells[1] / 2, 4)
    // the tile is a real RGBA PNG with see-through holes and opaque metal
    const bytes = Buffer.from(packet.textures[p.material.map!]!.split(',')[1]!, 'base64')
    expect([...bytes.subarray(0, 8)]).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
    expect(bytes.readUInt32BE(16)).toBe(32)
    expect(bytes.readUInt32BE(20)).toBe(64)
    const idatLen = bytes.readUInt32BE(33)
    const raw = inflateSync(bytes.subarray(41, 41 + idatLen))
    const alphas: number[] = []
    for (let y = 0; y < 64; y++) for (let x = 0; x < 32; x++) alphas.push(raw[y * 129 + 1 + x * 4 + 3]!)
    expect(alphas.filter((a) => a === 0).length / alphas.length).toBeGreaterThan(0.3) // ~50 % open
    expect(alphas.filter((a) => a === 255).length / alphas.length).toBeGreaterThan(0.3)
    expect(packet.primitives[0]!.modelUid).toBe('rack-uid')
  })

  it('still refuses a shader it does not know', () => {
    const odd = new THREE.Mesh(quad(), new THREE.ShaderMaterial())
    expect(() => buildSceneSnapshot({ root: rootWith(odd), project })).toThrow(/Unsupported render material ShaderMaterial/)
  })

  it('the DMAL testbed, whose racks render doors, now converts', () => {
    const raw = JSON.parse(readFileSync(resolve(process.cwd(), '../../../case/DAML-Testbed/DMAL_Testbed-patched.cfdproj'), 'utf8'))
    const proj = stateV4().normalizeStateV4(raw) as unknown as CfdProject
    const globalGroup = new THREE.Group()
    const wrapperGroup = new THREE.Group()
    wrapperGroup.add(globalGroup)
    const registries = emptyRegistries()
    const builder = createSceneBuilder({ globalGroup, wrapperGroup, registries, selectedUid: () => null, project: () => proj, webglAvailable: false })
    try {
      builder.rebuild()
      let doors = 0
      globalGroup.traverse((o) => { if (o.userData.isDoor) doors++ })
      expect(doors).toBe(3)
      const packet = buildSceneSnapshot({ root: globalGroup, project: proj, helpers: registries.renderHelpers })
      const doorPrims = packet.primitives.filter((p) => p.material.alphaTest && p.material.map)
      expect(doorPrims).toHaveLength(3)
      // circle and diamond holes: two distinct tiles, and the two circle doors share one
      expect(new Set(doorPrims.map((p) => p.material.map)).size).toBe(2)
    } finally {
      builder.clear()
    }
  })
})

describe('encodePngRgba', () => {
  it('writes valid chunk CRCs and a zlib stream that inflates to the filtered scanlines', () => {
    const rgba = new Uint8Array(3 * 2 * 4).map((_, i) => i * 7)
    const png = encodePngRgba(3, 2, rgba)
    let o = 8
    const types: string[] = []
    while (o < png.length) {
      const len = new DataView(png.buffer, png.byteOffset).getUint32(o)
      const type = String.fromCharCode(...png.subarray(o + 4, o + 8))
      const crc = new DataView(png.buffer, png.byteOffset).getUint32(o + 8 + len)
      expect(crc32(png, o + 4, o + 8 + len), type).toBe(crc)
      if (type === 'IDAT') {
        const raw = inflateSync(Buffer.from(png.subarray(o + 8, o + 8 + len)))
        expect([...raw]).toEqual([0, ...rgba.subarray(0, 12), 0, ...rgba.subarray(12, 24)])
      }
      types.push(type)
      o += 12 + len
    }
    expect(types).toEqual(['IHDR', 'IDAT', 'IEND'])
  })
})
