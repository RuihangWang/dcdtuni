import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as THREE from 'three'
import { expect, it } from 'vitest'
import '../src/legacy/geometryBuilder'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { modelPoseKernel } from '../src/domain/modelPoseKernel'
import { createSceneBuilder, emptyRegistries } from '../src/interaction/sceneBuilder'
import type { CfdProject, Construction } from '../src/schema/cfdproj'
import { prepareLiquidTransport } from '../src/run/liquidTransportWorker'
import type { LiquidTransportInput, LiquidTransportProduct } from '../src/run/liquidTransportProtocol'

const input = { modelIds: ['pipe'], models: [{ uid: 'pipe', kind: 'pipe_segment' }],
  state: { uid: 'project', constructions: {} }, matrixElements: [] } as unknown as LiquidTransportInput

function worker() {
  let stops = 0
  const value = {
    onmessage: null as Worker['onmessage'], onerror: null as Worker['onerror'],
    onmessageerror: null as Worker['onmessageerror'],
    postMessage: () => undefined, terminate: () => { ++stops },
  }
  return { value, asWorker: value as unknown as Worker, stops: () => stops }
}

it('terminates only the owned transport worker on preparation cancellation and ignores a late result', async () => {
  const owned = worker(), controller = new AbortController()
  const pending = prepareLiquidTransport(input, controller.signal, () => owned.asWorker)
  const late = owned.value.onmessage!
  controller.abort()
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  expect(owned.stops()).toBe(1)
  late.call(owned.asWorker, new MessageEvent('message', { data: { ok: true, product: { stl: 'stale' } } }))
  expect(owned.stops()).toBe(1)
  expect(owned.value.onmessage).toBeNull()
})

it('does not start a worker for an already-cancelled preparation', () => {
  const controller = new AbortController(); controller.abort()
  expect(() => prepareLiquidTransport(input, controller.signal, () => { throw new Error('must not start') }))
    .toThrow('Run preparation cancelled')
})

it('propagates the canonical geometry refusal instead of silently rebuilding on the UI thread', async () => {
  const owned = worker(), pending = prepareLiquidTransport(input, undefined, () => owned.asWorker)
  owned.value.onmessage!.call(owned.asWorker,
    new MessageEvent('message', { data: { ok: false, error: 'Liquid transport union is not a closed oriented solid carrier' } }))
  await expect(pending).rejects.toThrow('closed oriented solid carrier')
  expect(owned.stops()).toBe(1)
})

function normalizedTransportCase() {
  const path = resolve(process.cwd(), '../../../case/LiquidSingle/LiquidSingle.cfdproj')
  const saved = readFileSync(path, 'utf8')
  const project = stateV4().normalizeStateV4(JSON.parse(saved)) as unknown as CfdProject
  const models = stateV4().allModels(project)
  const ids = models.filter(model => model.kind === 'pipe_segment' || model.kind === 'pipe_junction')
    .map(model => model.uid)
  const globalGroup = new THREE.Group(), wrapperGroup = new THREE.Group()
  wrapperGroup.add(globalGroup); wrapperGroup.updateMatrixWorld(true)
  const builder = createSceneBuilder({ globalGroup, wrapperGroup, registries: emptyRegistries(),
    project: () => project, selectedUid: () => null, webglAvailable: false })
  const globals = window as unknown as {
    allModels?: () => Construction[]
    ThreeCSG: { CSG: unknown }
    geometryScene: { buildLiquidTransportDetached(input: LiquidTransportInput, three: typeof THREE,
      csg: unknown, portFrame: (model: Construction, port: string, end: string) => unknown): LiquidTransportProduct | null
      getGroupedStlSourceCatalogue(files: Record<string, string>): {
        files: Record<string, { regions: { sourceEntityUid: string; sourceEntitySnapshot: string }[] }>
      } }
  }
  const previous = globals.allModels
  globals.allModels = () => stateV4().allModels(project)
  const input = builder.api.captureLiquidTransportInput(ids)
  const build = (value: LiquidTransportInput) => globals.geometryScene.buildLiquidTransportDetached(value, THREE,
    globals.ThreeCSG.CSG, (model, port, end) => modelPoseKernel.equipmentPortFrame(
      model as unknown as Record<string, unknown>, model.kind as string, port, end))
  return { path, saved, project, models, ids, builder, globals, input, build, close() {
    builder.clear()
    if (previous) globals.allModels = previous
    else delete globals.allModels
  } }
}

it('preserves real normalized hidden kinds, bucket aliases, STL bytes and current OO ownership through both wire clones', () => {
  const h = normalizedTransportCase(), projectBefore = JSON.stringify(h.project)
  try {
    expect(h.ids.length).toBeGreaterThan(0)
    expect(h.input.models).toEqual(h.models)
    for (const model of h.input.models) {
      expect(h.models.includes(model)).toBe(true)
      expect(Object.getOwnPropertyDescriptor(model, 'kind')?.enumerable).toBe(false)
    }
    const expectedFiles = h.builder.api.buildGroupedStlMap(h.ids, false)
    const copied = structuredClone(h.input)
    const beforeDecode = JSON.stringify(copied.models)
    expect(copied.models.every(model => !Object.hasOwn(model, 'kind'))).toBe(true)
    const buckets = copied.state.constructions as {
      pipeSegments: Record<string, Construction>; pipeJunctions: Record<string, Construction>
    }
    for (const uid of h.ids) {
      const model = copied.models.find(row => row.uid === uid)!
      expect(buckets.pipeSegments[uid] ?? buckets.pipeJunctions[uid]).toBe(model)
    }
    const product = h.build(copied)
    expect(product).not.toBeNull()
    expect(product!.stl).toBe(expectedFiles['merged_liquid_transport.stl'])
    for (const model of copied.models) {
      const original = h.models.find(row => row.uid === model.uid)!
      expect(model.kind).toBe(original.kind)
      expect(Object.getOwnPropertyDescriptor(model, 'kind')?.enumerable).toBe(false)
      expect(JSON.stringify(model)).toBe(JSON.stringify(original))
    }
    expect(JSON.stringify(copied.models)).toBe(beforeDecode)
    const reply = structuredClone(product!)
    expect(reply.regions.every(row => !Object.hasOwn(row.model, 'kind') && !h.models.includes(row.model))).toBe(true)
    const files = h.builder.api.buildGroupedStlMap(h.ids, false, { input: h.input, product: reply })
    expect(files).toEqual(expectedFiles)
    // The real recordSourceEmission rejects any object outside current allModels:
    // this export succeeds only after restorePreparedLiquidTransport rebinds them.
    expect(h.globals.geometryScene.getGroupedStlSourceCatalogue(files))
      .toEqual(h.globals.geometryScene.getGroupedStlSourceCatalogue(expectedFiles))
    expect(JSON.stringify(h.project)).toBe(projectBefore)
    expect(readFileSync(h.path, 'utf8')).toBe(h.saved)
    for (const model of h.models) expect(Object.getOwnPropertyDescriptor(model, 'kind')?.enumerable).toBe(false)
  } finally { h.close() }
})

it('refuses incomplete, duplicate, foreign or conflicting wire kinds before restoring any model', () => {
  const h = normalizedTransportCase()
  try {
    const invalid = (change: (value: LiquidTransportInput) => void) => {
      const copied = structuredClone(h.input)
      change(copied)
      expect(() => h.build(copied)).toThrow(/kind|UID/)
      expect(copied.models.slice(1).every(model => !Object.hasOwn(model, 'kind'))).toBe(true)
    }
    invalid(value => { value.modelKinds!.pop() })
    invalid(value => { value.modelKinds![1] = { ...value.modelKinds![0]! } })
    invalid(value => { value.modelKinds![0]!.uid = 'absent-wire-owner' })
    invalid(value => { value.models[1]!.uid = value.models[0]!.uid })
    invalid(value => { Object.defineProperty(value.models[0]!, 'kind', {
      value: 'conflicting-wire-kind', enumerable: false, configurable: true, writable: true,
    }) })
    invalid(value => { delete value.modelKinds })
    const plain = { ...h.input }; delete plain.modelKinds
    // The original direct OO/plain path remains compatible with its own kinds.
    expect(h.build(plain)?.contributors).toEqual([...h.ids].sort())
  } finally { h.close() }
})
