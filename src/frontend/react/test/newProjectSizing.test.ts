import { describe, expect, it } from 'vitest'

import { stateV4 } from '../src/legacy/cfdprojModules'
import { meshConfigOf, resolveMeshEngine, resolveMeshMode } from '../src/run/meshConfig'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'

// A project created from nothing starts on the recommended cell-size rule; a
// loaded file keeps explicit values, while missing fields use current defaults.
type RunDefaults = { meshConfig: Record<string, unknown>; simulationConfig: Record<string, unknown> }
const rd = (doc: unknown) => (doc as { runDefaults: RunDefaults }).runDefaults

describe('new-project mesh sizing', () => {
  it('a new document is on the rule', () => {
    const { meshConfig: mc, simulationConfig: sc } = rd(stateV4().newDocumentV4())
    expect(mc.meshBaseSize).toBe(0.25)
    expect(mc.rackRegionLevel).toBe(3)
    expect(mc.rackCellMinSize).toBe(0.0175)
    expect(mc.acuRefinementLevel).toEqual([1, 1])
    expect(mc.wallBafflesRefinementLevel).toEqual([1, 1])
    expect(mc.firstLayerMinFraction).toBe(0.3)
    expect(mc.firstLayerMax).toBe(0.005)
    expect(mc.weldTolerance).toBe(0.0012)
    // the cap is a floor: an empty room does not pull the 5M default down
    expect(mc.maxGlobalCells).toBe(5000000)
    // partition-wall levels are simulationConfig keys, as the panel's button writes them
    expect(sc.partitionWallRefinementLevel).toBeUndefined()
    expect(mc.partitionWallRefinementLevel).toBeUndefined()
    // engine, parallelism and the seed point are not sizing
    const old = rd(stateV4().getInitialStateV4()).meshConfig
    expect(mc.parallelMesh).toBe(old.parallelMesh)
    expect(mc.meshProcess).toBe(old.meshProcess)
    expect(mc.locationInMesh).toEqual(old.locationInMesh)
    expect(mc.meshEngine).toBe(old.meshEngine)
  })

  it('the editor opens and resets onto a new document', () => {
    expect(meshConfigOf(store.getProject()).meshBaseSize).toBe(0.25)
  })

  it('a loaded project missing sizing keys fills them from the product defaults', () => {
    const loaded = stateV4().normalizeStateV4({ runDefaults: { meshConfig: { meshEngine: 'dcCart' } } })
    const mc = meshConfigOf(loaded as unknown as CfdProject)
    expect(mc.meshBaseSize).toBe(0.25)
    expect(mc.acuRefinementLevel).toEqual([1, 1])
    expect(mc.rackCellMinSize).toBe(0.0175)
  })
})

it('missing mesh engine follows dcCart while explicit legacy engines keep their semantics', () => {
  expect(resolveMeshEngine(undefined)).toBe('dcCart')
  expect(resolveMeshEngine({})).toBe('dcCart')
  expect(resolveMeshMode({})).toBe('cartesianLocalized')
  for (const meshEngine of ['snappy', 'dcCart'] as const) {
    expect(resolveMeshEngine({meshEngine})).toBe(meshEngine)
    expect(resolveMeshMode({meshEngine})).toBe(meshEngine === 'dcCart' ? 'cartesianLocalized' : 'bodyFitted')
  }
  const loaded = stateV4().normalizeStateV4({runDefaults:{meshConfig:{}}})
  const config = meshConfigOf(loaded as unknown as CfdProject)
  expect(config.meshBaseSize).toBe(.25)
  expect(config.meshTimeLimitSeconds).toBe(300)
  expect(resolveMeshEngine(config)).toBe('dcCart')
})

import * as THREE from 'three'
import { createCfdrunBuild } from '../src/legacy/cfdrunBuild'
it.each([undefined, 'dcCart', 'snappy'] as const)('real shared exporter applies omitted base/engine defaults and keeps explicit %s', (engine) => {
  const project = stateV4().getInitialStateV4() as CfdProject
  project.runDefaults!.meshConfig = engine ? {meshEngine: engine} : {}
  // Match the React host's shared-module ordering, rather than re-deriving models.
  const host = window as unknown as { allModels?: () => unknown[] }
  const previousAllModels = host.allModels
  host.allModels = () => stateV4().allModels(project)
  try {
    const builder = createCfdrunBuild({state:project,globalGroup:new THREE.Group(),LENGTH_UNITS:{m:1,mm:.001},_MESH_MODES:{}})
    const actual = builder.buildCfdrunJson().meshConfig as Record<string,unknown>
    expect(actual.baseSize).toBe(.25)
    expect(actual.meshEngine).toBe(engine ?? 'dcCart')
    expect(actual.meshMode).toBe((engine ?? 'dcCart') === 'dcCart' ? 'cartesianLocalized' : 'bodyFitted')
  } finally {
    if (previousAllModels) host.allModels = previousAllModels
    else delete host.allModels
  }
})


import currentRoomSource from '../../../../case/verification/shinaide-glycol25-full-mms-20261013/Baseline.cfdproj?raw'
it('real shared exporter keeps nonempty saved-room domain finite when base size is omitted', () => {
  const raw = JSON.parse(currentRoomSource)
  expect(raw.geometry.plane.length).toBeGreaterThanOrEqual(3)
  const host = window as unknown as { allModels?: () => unknown[] }
  const previousAllModels = host.allModels
  const exportAt = (engine: 'snappy' | 'dcCart', base: number | undefined) => {
    const saved = structuredClone(raw)
    const mc = saved.runDefaults.meshConfig
    mc.meshEngine = engine
    mc.blockMeshBounds = null
    if (base === undefined) delete mc.meshBaseSize
    else mc.meshBaseSize = base
    // Through the product's own load path: a v5 file keeps time-variable
    // fields (here every server's saved power) in transient.tracks, and only
    // a live project materialises them. The bare normaliser left the hybrid
    // fan servers without power, which the shared exporter rightly refuses
    // since c30906b20 — before that it silently defaulted them to 1 kW.
    store.loadProject(stateV4().normalizeStateV4(saved) as unknown as CfdProject, 'Baseline.cfdproj')
    const project = store.getProject()!
    host.allModels = () => stateV4().allModels(project)
    const builder = createCfdrunBuild({state:project,globalGroup:new THREE.Group(),LENGTH_UNITS:{m:1,mm:.001},_MESH_MODES:{}})
    const actual = builder.buildCfdrunJson().meshConfig as Record<string,unknown>
    const domain = actual.blockMeshBounds as {min:Record<string,number>;max:Record<string,number>}
    expect([...Object.values(domain.min), ...Object.values(domain.max)].every(Number.isFinite)).toBe(true)
    expect(JSON.stringify(domain)).not.toContain('null')
    expect(actual.baseSize).toBe(base ?? .25)
    expect(actual.meshEngine).toBe(engine)
    return domain
  }
  try {
    for (const engine of ['snappy', 'dcCart'] as const) {
      const explicit = exportAt(engine, .25)
      expect(exportAt(engine, undefined)).toEqual(explicit)
      expect(exportAt(engine, .5)).not.toEqual(explicit)
      console.info('actual saved-room domain', JSON.stringify({engine, explicit025:explicit}))
    }
  } finally {
    if (previousAllModels) host.allModels = previousAllModels
    else delete host.allModels
  }
})
