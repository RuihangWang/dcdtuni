import { createHash } from 'node:crypto'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import Ajv2020 from 'ajv/dist/2020'
import { describe, expect, it } from 'vitest'

/**
 * The material and texture pool manifest, src/frontend/shared/textures/materials.json.
 *
 * It is the ONE source of every surface: both editors build their materials
 * from it, the Omniverse bridge and the Kit-native path read the same file,
 * and tools/gen_pbr_textures.py writes its measured fields. These checks keep
 * the manifest honest about the pool (every file exists, is a real image of
 * the size it claims, hashes to its recorded sha256 — which is also its Git
 * LFS oid) and keep the code honest about the manifest (every kind a renderer
 * emits has a material, and no parameter table survives in the JavaScript).
 */

const REPO = resolve(process.cwd(), '../../..')
const SHARED = resolve(REPO, 'src/frontend/shared')
const POOL = resolve(SHARED, 'textures')
const MANIFEST_PATH = resolve(POOL, 'materials.json')

type FileRef = { file: string; format: 'jpeg' | 'png'; size: [number, number]; bytes: number; sha256: string }
type SetEntry = {
  label: string
  source: { kind: 'generated' | 'photo' | 'project'; seed?: number; recipe?: string; size?: [number, number] }
  tintable: boolean
  alphaInColor?: boolean
  maps: Record<'color' | 'normal' | 'roughness', FileRef>
  stats: { meanLuminance: number; medianLuminance: number; minLuminance: number; meanRoughness: number; meanAlpha?: number }
}
type Material = {
  label: string
  role?: string
  hueInMap?: boolean
  library?: boolean
  set?: string | null
  baseColor?: string
  colorSource?: 'preset' | 'model'
  factoryColors?: string[]
  tintable?: boolean
  roughness?: number
  metallic?: number
  normalScale?: number
  tileM?: number | null
  uv?: 'physical' | 'stretch' | 'tile-per-u'
  wrap?: 'repeat' | 'clamp'
  doubleSide?: boolean
  opacity?: number
}
type Manifest = {
  schema: string
  sets: Record<string, SetEntry>
  photos: Record<string, { image: FileRef }>
  patchPresets: Array<{ id: string; roles: string[]; image: FileRef }>
  materials: Record<string, Material>
  lighting: Record<string, unknown>
}

function manifest(): Manifest {
  return JSON.parse(readFileSync(MANIFEST_PATH, 'utf8')) as Manifest
}

function allFileRefs(m: Manifest): FileRef[] {
  return [
    ...Object.values(m.sets).flatMap((s) => Object.values(s.maps)),
    ...Object.values(m.photos).map((p) => p.image),
    ...m.patchPresets.map((p) => p.image),
  ]
}

/** Width and height from a JPEG SOFn or a PNG IHDR; throws on anything else (an LFS pointer, say). */
function imageSize(bytes: Buffer, name: string): { format: 'jpeg' | 'png'; size: [number, number] } {
  if (bytes.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    return { format: 'png', size: [bytes.readUInt32BE(16), bytes.readUInt32BE(20)] }
  }
  if (bytes[0] === 0xff && bytes[1] === 0xd8) {
    let i = 2
    while (i < bytes.length) {
      if (bytes[i] !== 0xff) { i++; continue }
      const marker = bytes[i + 1]!
      if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
        return { format: 'jpeg', size: [bytes.readUInt16BE(i + 7), bytes.readUInt16BE(i + 5)] }
      }
      i += 2 + bytes.readUInt16BE(i + 2)
    }
  }
  const head = bytes.subarray(0, 40).toString('latin1')
  throw new Error(`${name} is not a JPEG or PNG${head.startsWith('version https://git-lfs') ? ' (an unsmudged Git LFS pointer: run git lfs pull)' : ''}`)
}

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    return statSync(p).isDirectory() ? walk(p) : [p]
  })
}

/** Every objectKind the shared renderers emit, after the 2026-10-21 material redesign. */
const RENDERED_KINDS = [
  'rack-side', 'rack-top', 'rack-bottom', 'rack-front', 'rack-rail', 'rack-door',
  'server-front', 'server-rear-socket', 'server-outlet-mesh', 'server-chassis', 'blank-panel', 'blank-panel-baffle',
  'floor', 'raised-floor', 'floor-vent', 'ceiling', 'wall', 'partition-wall',
  'baffle', 'baffle-perforated', 'baffle-grille',
  'box', 'acu-body', 'acu-supply', 'acu-return', 'acu-side', 'custom', 'face-image', 'default',
  'cdu-body', 'cold-plate', 'air-duct',
]
/** The liquid-equipment library roles js/pbr_materials.js serves. */
const LIBRARY_ROLES = ['pipe-insulation', 'stainless', 'galvanized', 'painted-steel', 'copper', 'brass', 'rubber']

describe('materials.json is the pool manifest', () => {
  it('validates against shared/schema/material-library.schema.json', () => {
    const schema = JSON.parse(readFileSync(resolve(SHARED, 'schema/material-library.schema.json'), 'utf8'))
    const validate = new Ajv2020({ strict: true, allErrors: true }).compile(schema)
    const ok = validate(manifest())
    expect(ok, JSON.stringify(validate.errors, null, 1)).toBe(true)
  })

  it('every referenced file exists, is the image it claims, and hashes to its recorded sha256 (= its LFS oid)', () => {
    for (const ref of allFileRefs(manifest())) {
      const path = resolve(POOL, ref.file)
      expect(existsSync(path), ref.file).toBe(true)
      const bytes = readFileSync(path)
      const { format, size } = imageSize(bytes, ref.file)
      expect(format, ref.file).toBe(ref.format)
      expect(size, ref.file).toEqual(ref.size)
      expect(Math.max(...size), `${ref.file} is larger than the 1024 px ceiling`).toBeLessThanOrEqual(1024)
      expect(bytes.length, ref.file).toBe(ref.bytes)
      expect(createHash('sha256').update(bytes).digest('hex'), ref.file).toBe(ref.sha256)
    }
  })

  it('the pool holds no image the manifest does not reference (no dead or duplicate files)', () => {
    const referenced = new Set(allFileRefs(manifest()).map((r) => r.file))
    const images = walk(POOL).map((p) => relative(POOL, p)).filter((p) => /\.(jpe?g|png|webp|ktx2|exr|hdr)$/i.test(p))
    expect(images.filter((p) => !referenced.has(p.split('\\').join('/'))).sort()).toEqual([])
    const bySha = new Map<string, string>()
    for (const ref of allFileRefs(manifest())) {
      const prior = bySha.get(ref.sha256)
      if (prior && prior !== ref.file) throw new Error(`${ref.file} is byte-identical to ${prior}`)
      bySha.set(ref.sha256, ref.file)
    }
  })

  it('a tintable set is high-key, so the material colour decides the hue', () => {
    // The MEDIAN is the surface itself: a perforated sheet's dark holes pull
    // the mean down without darkening the metal the colour multiplies.
    for (const [name, set] of Object.entries(manifest().sets)) {
      if (set.tintable) expect(set.stats.medianLuminance, name).toBeGreaterThanOrEqual(0.8)
    }
  })

  it('generated sets record their recipe and seed; photo sets record where they came from', () => {
    for (const [name, set] of Object.entries(manifest().sets)) {
      if (set.source.kind === 'generated') {
        expect(typeof set.source.seed, name).toBe('number')
        expect(set.source.size, name).toEqual(set.maps.color.size)
      } else expect(set.source.kind, name).toBe('photo')
    }
  })
})

describe('every rendered kind has a material', () => {
  const m = manifest()

  it.each(RENDERED_KINDS)('%s', (kind) => {
    const entry = m.materials[kind]
    expect(entry, kind).toBeDefined()
    const resolved = entry!.role ? m.materials[entry!.role] : entry
    expect(resolved, `${kind} -> ${entry!.role}`).toBeDefined()
    if (resolved!.set) expect(m.sets[resolved!.set], `${kind} set ${resolved!.set}`).toBeDefined()
  })

  it.each(LIBRARY_ROLES)('library role %s', (role) => {
    const entry = m.materials[role]
    expect(entry?.library, role).toBe(true)
    expect(m.sets[entry!.set!], role).toBeDefined()
  })

  it('the shared renderers emit no objectKind literal the manifest lacks', () => {
    const files = ['geometry_scene.js', 'geometry_pure.js', 'box_material_recipe.js', 'baffle_material_recipe.js']
    const emitted = new Set<string>()
    for (const f of files) {
      const text = readFileSync(resolve(SHARED, 'js', f), 'utf8')
      for (const match of text.matchAll(/objectKind\s*[:=]\s*'([a-z][a-z-]*)'/g)) emitted.add(match[1]!)
    }
    expect(emitted.size).toBeGreaterThan(3)
    for (const kind of emitted) expect(m.materials[kind], kind).toBeDefined()
    for (const kind of emitted) expect(RENDERED_KINDS, kind).toContain(kind)
  })

  it('every material that names a set agrees with it on tinting', () => {
    for (const [kind, entry] of Object.entries(m.materials)) {
      if (entry.role || !entry.set) continue
      if (entry.tintable) expect(m.sets[entry.set]!.tintable, kind).toBe(true)
      if (entry.uv === 'physical') expect(entry.tileM, kind).toBeGreaterThan(0)
    }
  })
})

describe('saved projects keep working', () => {
  it('every /textures/ URL and texturePreset a case project stores resolves through the manifest', () => {
    const m = manifest()
    const known = new Set(allFileRefs(m).map((r) => '/textures/' + r.file))
    const presets = new Set(m.patchPresets.map((p) => p.id))
    const caseDir = resolve(REPO, 'case')
    // Projects live at case/<dir>/<name>.cfdproj; deeper trees are logs and meshes.
    const projects = existsSync(caseDir) ? readdirSync(caseDir, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .flatMap((d) => readdirSync(join(caseDir, d.name)).filter((n) => n.endsWith('.cfdproj')).map((n) => join(caseDir, d.name, n)))
      : []
    expect(projects.length).toBeGreaterThan(3)
    for (const p of projects) {
      const text = readFileSync(p, 'utf8')
      for (const match of text.matchAll(/"(\/textures\/[^"]+)"/g)) expect(known.has(match[1]!), `${p}: ${match[1]}`).toBe(true)
      for (const match of text.matchAll(/"texturePreset":\s*"([^"]+)"/g)) expect(presets.has(match[1]!), `${p}: ${match[1]}`).toBe(true)
    }
  })
})

describe('no parameter table survives in the code', () => {
  const read = (p: string) => readFileSync(resolve(REPO, p), 'utf8')

  it('geometry_scene.js builds every structural material from the manifest', () => {
    const text = read('src/frontend/shared/js/geometry_scene.js')
    for (const banned of ['TEXTURE_TILE_SIZES', '_materialPresets', 'externalTextureRegistry', 'LIBRARY_KINDS',
      '_withPbrHint', 'MeshPhongMaterial', 'tintColor', "'/textures/", '_generateProceduralTexture']) {
      expect(text.includes(banned), banned).toBe(false)
    }
  })

  it('geometry_pure.js has no random procedural textures and no unlit door shader', () => {
    const text = read('src/frontend/shared/js/geometry_pure.js')
    for (const banned of ['_genBrushedMetal', '_genPerforatedMetal', '_genBlankPanel', '_genFloorTiles', '_genConcrete',
      '_generateProceduralTexture', '_heightmapToNormalMap', "color: '#7c9a7c'"]) {
      expect(text.includes(banned), banned).toBe(false)
    }
    // The one shader left is the viewport's point marker (an overlay, not a
    // model material): no model surface may be drawn by a shader of its own.
    const shaders = text.split('new THREE.ShaderMaterial').slice(1)
    expect(shaders).toHaveLength(1)
    const owner = text.slice(text.lastIndexOf('function ', text.indexOf('new THREE.ShaderMaterial')))
    expect(owner.startsWith('function createMarkerMaterial(')).toBe(true)
  })

  it('the recipes and the PBR library carry no numbers of their own', () => {
    for (const p of ['src/frontend/shared/js/box_material_recipe.js', 'src/frontend/shared/js/baffle_material_recipe.js',
      'src/frontend/shared/js/pbr_materials.js']) {
      const text = read(p)
      expect(text, p).not.toMatch(/(roughness|metalness|meanRoughness|normalScale|tileM)\s*:\s*0?\.\d/)
      expect(text, p).not.toMatch(/shininess|specular|tintColor/)
    }
    expect(read('src/frontend/shared/js/patch_textures.js')).not.toContain('/textures/grilles/')
  })

  it('both editors light the scene from the manifest, not from literals', () => {
    const legacy = read('src/frontend/web/STL_generate.html')
    expect(legacy).not.toMatch(/new THREE\.DirectionalLight\(0xFFF7E6/)
    expect(legacy).not.toMatch(/toneMappingExposure\s*=\s*2\.0/)
    expect(legacy).not.toContain('textures/MISC/')
    const viewport = read('src/frontend/react/src/components/Viewport.tsx')
    expect(viewport).not.toMatch(/addCameraLight\(0x/)
    expect(viewport).not.toMatch(/toneMappingExposure\s*=\s*2\.0/)
  })
})
