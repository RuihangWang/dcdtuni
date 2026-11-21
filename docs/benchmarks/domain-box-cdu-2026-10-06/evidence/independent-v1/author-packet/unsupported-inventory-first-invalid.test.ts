import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import vm from 'node:vm'
import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { createSceneBuilder, emptyRegistries, type SceneHost } from '../src/interaction/sceneBuilder'
import { BoxEntity, DomainProject, PduEntity } from '../src/domain'
import { geometryProductKernel } from '../src/domain/geometryProductKernel'
import type { CfdProject } from '../src/schema/cfdproj'
import golden from './fixtures/boxGeometryProductParity.json'

const root = resolve(process.cwd(), '../../..')
const shared = resolve(root, 'src/frontend/shared')
function read(path: string) { return readFileSync(resolve(shared, path), 'utf8') }
function bytes(array: ArrayBufferView) {
  return Buffer.from(array.buffer, array.byteOffset, array.byteLength).toString('hex')
}
function classic() {
  const context = vm.createContext({})
  // A real classic-script global has window === globalThis. No renderer or DOM.
  vm.runInContext('globalThis.window = globalThis', context)
  for (const file of ['vendor/polygon-clipping.umd.js', 'vendor/earcut.umd.js',
    'js/stl_naming.js', 'js/box_geometry_product.js']) vm.runInContext(read(file), context)
  return context
}
function model(index = 4) { return structuredClone(golden.cases[index]!.model) }
function product(index = 4) {
  const sample = golden.cases[index]!
  return geometryProductKernel.createProduct(model(index), sample.bucket === 'cdus' ? 'cdu' : 'box')
}

describe('one pure structural kernel across classic, React and CommonJS', () => {
  it('preserves the installed Earcut numerical body and distribution license', () => {
    const installed = readFileSync(resolve(process.cwd(), 'node_modules/three/src/extras/lib/earcut.js'), 'utf8')
    const numerical = installed.replace('export default function earcut', 'function earcut')
      .replace('export function deviation', 'function deviation')
      .replace('export function flatten', 'function flatten')
    const wrapped = read('vendor/earcut.umd.js')
    expect(wrapped).toContain(numerical)
    expect(wrapped).toContain('Permission to use, copy, modify, and/or distribute')
    expect(read('vendor/earcut.LICENSE')).toContain('Copyright (c) 2024, Mapbox')
  })

  it('runs the classic loader without THREE, document or a renderer fallback', () => {
    const context = classic()
    expect(vm.runInContext('typeof THREE + "/" + typeof document', context)).toBe('undefined/undefined')
    context.input = model()
    const value = vm.runInContext('boxGeometryProduct.createProduct(input, "box")', context) as ReturnType<typeof product>
    const expected = golden.cases[4]!.expected.parts
    expect(value.parts).toHaveLength(expected.length)
    value.parts.forEach((part, i) => {
      expect(bytes(part.positions)).toBe(expected[i]!.position!.bytesLE)
      expect(bytes(part.normals)).toBe(expected[i]!.normal!.bytesLE)
      expect(bytes(part.uvs)).toBe(expected[i]!.uv!.bytesLE)
      expect(bytes(part.indices)).toBe(expected[i]!.index!.bytesLE)
    })
  })

  it('fails clearly when classic numerical dependencies were not loaded', () => {
    const context = vm.createContext({})
    expect(() => vm.runInContext(read('js/box_geometry_product.js'), context))
      .toThrow(/needs polygon-clipping, earcut and stl_naming loaded first/)
    expect(context.boxGeometryProduct).toBeUndefined()
  })

  it('standalone CommonJS uses the same buffers without renderer globals', () => {
    const script = [
      "const assert = require('node:assert/strict');",
      'const core = require(' + JSON.stringify(resolve(shared, 'js/box_geometry_product.js')) + ');',
      'const pure = require(' + JSON.stringify(resolve(shared, 'js/geometry_pure.js')) + ');',
      'const sample = ' + JSON.stringify(golden.cases[4]) + ';',
      "assert.equal(typeof global.THREE, 'undefined'); assert.equal(typeof global.window, 'undefined');",
      "assert.equal(typeof global.document, 'undefined');",
      "const p = core.createProduct(sample.model, 'box');",
      "p.parts.forEach((x,i) => assert.equal(Buffer.from(x.positions.buffer).toString('hex'), sample.expected.parts[i].position.bytesLE));",
      "assert.deepEqual(pure.circleToPoly(1,2,3), core.circleToPoly(1,2,3));",
      "assert.throws(()=>core.createProduct(sample.model,'acu'), /Unsupported geometry product kind/);",
      "process.stdout.write('pure-kernel-ok');",
    ].join('\n')
    const result = spawnSync(process.execPath, ['-e', script], { cwd: root, encoding: 'utf8', timeout: 5000 })
    expect(result.error).toBeUndefined()
    expect(result.status, result.stderr).toBe(0)
    expect(result.stdout).toBe('pure-kernel-ok')
  })

  it('both actual entry points load the shared numerical producer before consuming it', () => {
    const html = readFileSync(resolve(root, 'src/frontend/web/STL_generate.html'), 'utf8')
    const react = readFileSync(resolve(process.cwd(), 'src/legacy/geometryBuilder.ts'), 'utf8')
    for (const source of [html, react]) {
      expect(source.indexOf('vendor/earcut.umd.js')).toBeGreaterThan(source.indexOf('vendor/polygon-clipping.umd.js'))
      expect(source.indexOf('js/box_geometry_product.js')).toBeGreaterThan(source.indexOf('js/stl_naming.js'))
      expect(source.indexOf('js/geometry_pure.js', source.indexOf('js/box_geometry_product.js')))
        .toBeGreaterThan(source.indexOf('js/box_geometry_product.js'))
    }
  })
})

describe('owned plain buffers and explicitly scoped material source recipes', () => {
  it('does not alias any mutable buffer or source recipe between calls or to JSON', () => {
    const input = model()
    const before = JSON.stringify(input)
    const a = geometryProductKernel.createProduct(input, 'box')
    const b = geometryProductKernel.createProduct(input, 'box')
    for (const key of ['positions', 'normals', 'uvs', 'indices'] as const) {
      expect(a.parts[0]![key]).not.toBe(b.parts[0]![key])
      const value = b.parts[0]![key][0]
      a.parts[0]![key][0] = 999
      expect(b.parts[0]![key][0]).toBe(value)
    }
    a.parts[0]!.refIds.push('foreign')
    a.parts[0]!.materialRecipe.source.patchIds.push('foreign')
    a.parts[0]!.materialRecipe.color = '#000'
    expect(b.parts[0]!.refIds).not.toContain('foreign')
    expect(b.parts[0]!.materialRecipe.source.patchIds).not.toContain('foreign')
    expect(b.parts[0]!.materialRecipe.color).not.toBe('#000')
    expect(JSON.stringify(input)).toBe(before)
  })

  it('keeps patch/wall roles and raw UV as source descriptors, not renderer textures', () => {
    const p = product()
    expect(p.parts.some(part => !part.isBaseWall)).toBe(true)
    for (const part of p.parts) {
      expect(part.materialRecipe).toEqual({
        role: part.isBaseWall ? 'structural-wall' : 'structural-patch',
        uvMode: 'raw', color: expect.any(String), opacity: expect.any(Number),
        source: { ownerUid: p.modelUid, faceId: part.faceId, patchIds: part.refIds },
      })
      expect(part.partId.startsWith(p.modelUid + '/' + part.faceId + '/')).toBe(true)
      expect(part.materialRecipe).not.toHaveProperty('texture')
      expect(part.materialRecipe).not.toHaveProperty('pbrMaterial')
    }
  })

  it('reads legacy opacity defaults without materializing wallOpacities in project JSON', () => {
    const input = model(9), face = input.geometry.faces.front
    Reflect.deleteProperty(face, 'wallOpacities')
    face.opacity = .37
    const before = JSON.stringify(input)
    const p = geometryProductKernel.createProduct(input, 'box')
    expect(p.parts.filter(part => part.faceId === 'front').every(part => part.materialRecipe.opacity === .37)).toBe(true)
    expect(JSON.stringify(input)).toBe(before)
    expect(face).not.toHaveProperty('wallOpacities')
  })

  it('keeps CDU face palette and owned structural recipe apart from display details', () => {
    const p = product(2)
    const colors: Record<string, string> = { front: '#007a99', back: '#009ab8', left: '#008aaa',
      right: '#008aaa', top: '#00b4dc', bottom: '#005e77' }
    for (const part of p.parts.filter(x => x.isBaseWall)) expect(part.materialRecipe.color).toBe(colors[part.faceId])
    expect(p.parts.every(part => !/hmi|pipe|door-detail/i.test(part.partId))).toBe(true)
  })

  it('explicit PDU assets keep physical BOX products while display names never assign PDU type', () => {
    const record = model(1)
    const ordinary = new DomainProject({ schemaVersion: 5, constructions: { boxes: { [record.uid]: record } } } as unknown as CfdProject).topLevel()[0]!
    expect(ordinary).toBeInstanceOf(BoxEntity)
    expect(ordinary).not.toBeInstanceOf(PduEntity)
    const named = { ...record, meta: { ...record.meta, name: 'PDU_only_a_display_name' } }
    expect(new DomainProject({ schemaVersion: 5, constructions: { boxes: { [record.uid]: named } } } as unknown as CfdProject).topLevel()[0]).not.toBeInstanceOf(PduEntity)
    const declared = { ...record, assetDefinition: { type: 'pdu' } }
    const entity = new DomainProject({ schemaVersion: 5, constructions: { boxes: { [record.uid]: declared } } } as unknown as CfdProject).topLevel()[0]!
    expect(entity).toBeInstanceOf(PduEntity)
    expect((entity as PduEntity).geometryProduct().kind).toBe('box')
    expect(bytes((entity as PduEntity).geometryProduct().parts[0]!.positions)).toBe(golden.cases[1]!.expected.parts[0]!.position!.bytesLE)
  })
})

describe('legacy non-BOX callers retain their established structural carrier', () => {
  it.each([
    ['acu', 'acus'], ['cold_plate', 'boxes'], ['custom', 'customMeshes'],
  ])('renders %s without asking the narrower domain product to convert it', (kind, bucket) => {
    const raw = stateV4().getInitialStateV4() as unknown as CfdProject
    const record = { ...model(1), kind }
    // Persisted BOX subtypes carry this marker; a bare transient kind is
    // intentionally canonicalized to BOX by the established loader.
    if (kind === 'cold_plate') Object.assign(record.meta, { legacyKind: kind })
    raw.constructions = { [bucket]: { [record.uid]: record } } as CfdProject['constructions']
    const project = stateV4().normalizeStateV4(raw) as unknown as CfdProject
    expect(stateV4().allModels(project)[0]).toMatchObject({ kind })
    const globalGroup = new THREE.Group(), wrapperGroup = new THREE.Group()
    wrapperGroup.add(globalGroup)
    const host: SceneHost = { globalGroup, wrapperGroup, registries: emptyRegistries(),
      project: () => project, selectedUid: () => null, webglAvailable: false }
    const runtime = globalThis as typeof globalThis & { boxGeometryProduct: typeof geometryProductKernel }
    const original = runtime.boxGeometryProduct
    const noBoxConversion = vi.fn(() => { throw new Error('unsupported carrier entered BOX product') })
    runtime.boxGeometryProduct = { ...original, createProduct: noBoxConversion }
    const builder = createSceneBuilder(host)
    try {
      builder.rebuildModels([record.uid])
      expect(host.registries.renderMeshes.length).toBeGreaterThanOrEqual(6)
      expect(host.registries.renderMeshes.every(mesh => mesh.userData.exportName && mesh.userData.modelId === record.uid)).toBe(true)
      expect(noBoxConversion).not.toHaveBeenCalled()
      expect(() => geometryProductKernel.createProduct(record, kind)).toThrow(/Unsupported geometry product kind/)
    } finally {
      builder.clear()
      runtime.boxGeometryProduct = original
    }
  })
})

describe('base entity geometry-product contract rejects unsupported source kinds', () => {
  it.each(['sensors', 'futureDevices'])('returns an explicit unsupported error for %s', bucket => {
    const record = model()
    const entity = new DomainProject({ schemaVersion: 5,
      constructions: { [bucket]: { [record.uid]: record } } } as unknown as CfdProject).topLevel()[0]!
    expect(typeof entity.geometryProduct).toBe('function')
    expect(() => entity.geometryProduct()).toThrow(/unsupported.*geometry|geometry.*unsupported/i)
  })
})
