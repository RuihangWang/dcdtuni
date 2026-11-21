import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import vm from 'node:vm'
import { describe, expect, it } from 'vitest'
import { materialLibrary } from '../src/legacy/materialManifest'

/**
 * js/material_library.js — the one reader of textures/materials.json that both
 * editors use. The rules it applies are the manifest's `colorRules`.
 */

const SHARED = resolve(process.cwd(), '../shared')
const manifest = JSON.parse(readFileSync(resolve(SHARED, 'textures/materials.json'), 'utf8'))

describe('material_library.js', () => {
  it('is installed from the manifest before any renderer asks', () => {
    expect(materialLibrary.isReady()).toBe(true)
    expect(materialLibrary.manifest().schema).toBe('dcdtuni.material-library/v1')
  })

  it('resolves a role kind to its library material, keeping the kind identity', () => {
    const cdu = materialLibrary.material('cdu-body')!
    expect(cdu.role).toBe('painted-steel')
    expect(cdu.set).toBe('painted-steel')
    expect(cdu.roughness).toBe(manifest.materials['painted-steel'].roughness)
    expect(materialLibrary.material('cold-plate')!.hueInMap).toBe(true)
    expect(materialLibrary.material('no-such-kind')).toBeNull()
  })

  it('a preset kind ignores the renderer colour; an authored overlay colour wins on a tintable one', () => {
    const wall = manifest.materials.wall
    expect(materialLibrary.colorFor('wall', '#7f848e', false)).toBe(wall.baseColor)
    expect(materialLibrary.colorFor('wall', '#123456', true)).toBe('#123456')
  })

  it('a model kind shows an authored colour, but not a renderer default listed in factoryColors', () => {
    const front = manifest.materials['server-front']
    expect(materialLibrary.colorFor('server-front', '#D14B4B', false)).toBe(front.baseColor)
    expect(materialLibrary.colorFor('server-front', 0x4488ff, false)).toBe(front.baseColor)
    expect(materialLibrary.colorFor('server-front', '#aa3300', false)).toBe('#aa3300')
    expect(materialLibrary.colorFor('server-front', undefined, false)).toBe(front.baseColor)
    // a blanking panel's frame colour is a renderer constant: the manifest decides
    expect(materialLibrary.colorFor('blank-panel', '#5c6370', false)).toBe(manifest.materials['blank-panel'].baseColor)
  })

  it('a non-tintable material always shows its base colour: the map carries the hue', () => {
    expect(materialLibrary.colorFor('face-image', '#ff0000', true)).toBe('#ffffff')
  })

  it('a bound absolute roughness map gets the scalar wanted / mean, clamped to 1', () => {
    const rack = manifest.materials['rack-side']
    const mean = manifest.sets[rack.set].stats.meanRoughness
    expect(materialLibrary.roughnessScalar('rack-side', true)).toBeCloseTo(Math.min(1, rack.roughness / mean), 6)
    expect(materialLibrary.roughnessScalar('rack-side', false)).toBe(rack.roughness)
    expect(materialLibrary.roughnessScalar('ceiling', true)).toBeLessThanOrEqual(1)
  })

  it('lists exactly the sets some material draws with', () => {
    const used = new Set<string>()
    for (const e of Object.values(manifest.materials) as Array<{ set?: string | null; role?: string }>) {
      const r = e.role ? manifest.materials[e.role] : e
      if (r.set) used.add(r.set)
    }
    expect(materialLibrary.setsInUse().sort()).toEqual([...used].sort())
  })

  it('names a pool file by reference and sha256, and nothing outside the pool', () => {
    const rel = manifest.photos['electric-box-lamps'].image
    expect(materialLibrary.poolRef('/textures/electric-box/panel_lamps.jpg')).toBe(`pool:${rel.file}#${rel.sha256}`)
    expect(materialLibrary.poolRef('textures/electric-box/panel_lamps.jpg')).toBe(`pool:${rel.file}#${rel.sha256}`)
    expect(materialLibrary.poolRef('/textures/../secrets.jpg')).toBeNull()
    expect(materialLibrary.poolRef('/textures/not-in-manifest.jpg')).toBeNull()
    expect(materialLibrary.poolRef('data:image/png;base64,AA==')).toBeNull()
  })

  it('gives the patch presets and their served URLs from the manifest', () => {
    const presets = materialLibrary.patchPresets()
    expect(presets.map((p) => p.id)).toEqual(manifest.patchPresets.map((p: { id: string }) => p.id))
    expect(materialLibrary.textureUrl('/textures', presets[0]!.image.file)).toBe('/textures/' + presets[0]!.image.file)
  })

  it('a bare classic-script context can install the manifest itself (the legacy page fetches it)', () => {
    const c = vm.createContext({})
    vm.runInContext(readFileSync(resolve(SHARED, 'js/material_library.js'), 'utf8'), c)
    expect(vm.runInContext('materialLibrary.isReady()', c)).toBe(false)
    c.__m = manifest
    expect(vm.runInContext('materialLibrary.install(__m).material("wall").baseColor', c)).toBe(manifest.materials.wall.baseColor)
  })

  it('CommonJS consumers get the manifest installed from the pool', () => {
    const lib = resolve(SHARED, 'js/material_library.js')
    const r = spawnSync(process.execPath, ['-e', `const l=require(${JSON.stringify(lib)});process.stdout.write(String(l.isReady())+':'+l.material('wall').set)`], { encoding: 'utf8', timeout: 5000 })
    expect(r.status, r.stderr).toBe(0)
    expect(r.stdout).toBe('true:' + manifest.materials.wall.set)
  })
})
