import { describe, expect, it } from 'vitest'

import { stateV4 } from '../src/legacy/cfdprojModules'
import { meshConfigOf } from '../src/run/meshConfig'
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
