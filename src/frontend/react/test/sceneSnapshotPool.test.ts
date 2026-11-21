import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { describe, expect, it, vi } from 'vitest'
import { buildSceneSnapshot } from '../src/renderer/sceneSnapshot'
import { validateSceneSnapshot } from '../src/renderer/validateScene'
import { materialLibrary } from '../src/legacy/materialManifest'

/**
 * render-scene v2: a texture that IS a pool file travels as a reference,
 * `pool:<file>#<sha256>`, and Kit binds the pool's own file; only pictures
 * that are not pool files (a project's embedded data URL, a canvas, the rack
 * door tile) still travel as bytes.
 */

const pool = resolve(process.cwd(), '../shared/textures')
const manifest = JSON.parse(readFileSync(resolve(pool, 'materials.json'), 'utf8')) as {
  sets: Record<string, { maps: Record<string, { file: string; sha256: string }> }>
  photos: Record<string, { image: { file: string; sha256: string } }>
}
const project = { uid: 'pool-project', name: 'pool' }

function quad() { return new THREE.PlaneGeometry(1, 1) }
function rootWith(...meshes: THREE.Mesh[]) {
  const root = new THREE.Group()
  const owner = new THREE.Group()
  owner.userData.modelId = 'model-uid'
  root.add(owner)
  meshes.forEach(m => owner.add(m))
  return root
}
function image(src?: string) {
  const toDataURL = vi.fn(() => 'data:image/png;base64,iVBORw0KGgo=')
  return { img: { width: 2, height: 2, toDataURL, ...(src ? { src } : {}) }, toDataURL }
}
function texture(img: object, colorSpace: THREE.ColorSpace = THREE.SRGBColorSpace) {
  const t = new THREE.Texture(img as unknown as HTMLCanvasElement)
  t.colorSpace = colorSpace
  t.wrapS = t.wrapT = THREE.RepeatWrapping
  return t
}
const steel = manifest.sets['painted-steel']!.maps

describe('pool textures travel as references', () => {
  it('a set loaded from the pool becomes three references and no bytes', () => {
    const color = image('/textures/' + steel.color!.file)
    const normal = image('/textures/' + steel.normal!.file)
    const rough = image('/textures/' + steel.roughness!.file)
    const mat = new THREE.MeshStandardMaterial({
      map: texture(color.img), normalMap: texture(normal.img, THREE.NoColorSpace),
      roughnessMap: texture(rough.img, THREE.NoColorSpace),
    })
    const packet = buildSceneSnapshot({ root: rootWith(new THREE.Mesh(quad(), mat)), project })
    expect(packet.schema).toBe('dcdtuni.render-scene/v2')
    const m = packet.primitives[0]!.material
    expect(packet.textures[m.map!]).toBe(`pool:${steel.color!.file}#${steel.color!.sha256}`)
    expect(packet.textures[m.normalMap!]).toBe(`pool:${steel.normal!.file}#${steel.normal!.sha256}`)
    expect(packet.textures[m.roughnessMap!]).toBe(`pool:${steel.roughness!.file}#${steel.roughness!.sha256}`)
    for (const t of [color, normal, rough]) expect(t.toDataURL).not.toHaveBeenCalled()
  })

  it('the reference carries the sha256 of the actual pool file (= its LFS oid)', () => {
    const file = steel.color!.file
    const sha = createHash('sha256').update(readFileSync(resolve(pool, file))).digest('hex')
    expect(materialLibrary.poolRef('/textures/' + file)).toBe(`pool:${file}#${sha}`)
  })

  it('absolute, /api/ and query-carrying URLs of a pool file are the same reference', () => {
    const file = manifest.photos['electric-box-lamps']!.image.file
    const urls = [`http://10.0.0.5:8441/textures/${file}?v=3`, `/api/textures/${file}`, `textures/${file}`]
    const meshes = urls.map(u => new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: texture(image(u).img) })))
    const packet = buildSceneSnapshot({ root: rootWith(...meshes), project })
    expect(Object.values(packet.textures)).toEqual([materialLibrary.poolRef('/textures/' + file)])
    expect(new Set(packet.primitives.map(p => p.material.map)).size).toBe(1)
  })

  it('a served URL the manifest does not list, a data URL and a canvas still travel as bytes', () => {
    const unlisted = image('/textures/somewhere/else.png')
    const embedded = image()          // a project's textureDataUrl: no src the snapshot can name
    const canvas = { width: 2, height: 2, toDataURL: vi.fn(() => 'data:image/png;base64,iVBORw0KGgoC') }
    const meshes = [unlisted.img, embedded.img, canvas].map(img =>
      new THREE.Mesh(quad(), new THREE.MeshStandardMaterial({ map: texture(img) })))
    const packet = buildSceneSnapshot({ root: rootWith(...meshes), project })
    const values = packet.primitives.map(p => packet.textures[p.material.map!])
    values.forEach(v => expect(v).toMatch(/^data:image\/png;base64,/))
  })
})

describe('render-scene schema v2', () => {
  function packet(schema: string, value: string) {
    return {
      schema, project, coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' },
      primitives: [{
        id: 'a/0', modelUid: 'model-uid', name: 'a', kind: 'triangles',
        positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], indices: [0, 1, 2], uvs: [0, 0, 1, 0, 0, 1],
        matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], visible: true,
        material: { color: [1, 1, 1], emissive: [0, 0, 0], opacity: 1, roughness: 1, metalness: 0,
          doubleSided: false, unlit: false, map: 't' },
      }],
      textures: { t: value }, view: { selectedUids: [], timeSeconds: null }, warnings: [],
    }
  }
  const ref = `pool:${steel.color!.file}#${steel.color!.sha256}`

  it('accepts a pool reference in v2 and still accepts v1 packets of data URIs', () => {
    expect(() => validateSceneSnapshot(packet('dcdtuni.render-scene/v2', ref))).not.toThrow()
    expect(() => validateSceneSnapshot(packet('dcdtuni.render-scene/v2', 'data:image/jpeg;base64,/9j/'))).not.toThrow()
    expect(() => validateSceneSnapshot(packet('dcdtuni.render-scene/v1', 'data:image/png;base64,iVBO'))).not.toThrow()
  })

  it('refuses a reference to a file or version this editor\'s manifest does not have', () => {
    const otherSha = `pool:${steel.color!.file}#${'0'.repeat(64)}`
    expect(() => validateSceneSnapshot(packet('dcdtuni.render-scene/v2', otherSha))).toThrow(/texture pool/)
    const unlisted = `pool:painted-steel/missing.jpg#${steel.color!.sha256}`
    expect(() => validateSceneSnapshot(packet('dcdtuni.render-scene/v2', unlisted))).toThrow(/texture pool/)
  })

  it('refuses a pool reference in a v1 packet and malformed references', () => {
    expect(() => validateSceneSnapshot(packet('dcdtuni.render-scene/v1', ref))).toThrow(/Render scene schema/)
    for (const bad of [`pool:${steel.color!.file}`, `pool:../x.jpg#${'0'.repeat(64)}`,
      `pool:${steel.color!.file}#${'0'.repeat(63)}`, '/textures/painted-steel/color.jpg']) {
      expect(() => validateSceneSnapshot(packet('dcdtuni.render-scene/v2', bad)), bad).toThrow(/Render scene schema/)
    }
  })
})
