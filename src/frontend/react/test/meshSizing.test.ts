import { describe, expect, it } from 'vitest'

import { meshSizing } from '../src/legacy/meshSizing'
import { stateV4 } from '../src/legacy/cfdprojModules'

// The 6Sigma sizing rule (docs/reference/sixsigma-cell-size-rule.md) as the
// shared module applies it. Numbers here are the rule's, so a drift in the
// module is a drift from the measured reference.
function room(width: number, depth: number, base: number) {
  const doc = stateV4().getInitialStateV4() as Record<string, unknown>
  ;(doc.geometry as Record<string, unknown>).plane = [[0, 0], [0, depth], [width, depth], [width, 0]]
  const rd = doc.runDefaults as { meshConfig: Record<string, unknown> }
  rd.meshConfig.meshBaseSize = base
  rd.meshConfig.maxGlobalCells = 500000
  rd.meshConfig.rackRegionLevel = 1 // below any pitch the rule computes
  return doc as never
}

describe('sixSigmaMeshConfig', () => {
  it('pulls a coarse background down to 0.25 m and keeps a finer one', () => {
    expect(meshSizing().sixSigmaMeshConfig(room(10, 6, 0.34)).patch.meshBaseSize).toBe(0.25)
    expect(meshSizing().sixSigmaMeshConfig(room(10, 6, 0.2)).patch.meshBaseSize).toBe(0.2)
  })

  it('puts rack regions at about 1U: 31 mm from 0.25, 50 mm from 0.2', () => {
    const a = meshSizing().sixSigmaMeshConfig(room(10, 6, 0.25)).patch
    const b = meshSizing().sixSigmaMeshConfig(room(10, 6, 0.2)).patch
    expect(a.rackRegionLevel).toBe(3)
    expect(b.rackRegionLevel).toBe(2)
    expect(a.acuRefinementLevel).toEqual([2, 3])
    expect(a.rackCellMinSize).toBe(0.0175)
    expect(a.firstLayerMinFraction).toBe(0.3)
    expect(a.weldTolerance).toBe(0.0012)
  })

  it('never coarsens a level the project already asked for', () => {
    const doc = room(10, 6, 0.17) as Record<string, unknown>
    const mc = (doc.runDefaults as { meshConfig: Record<string, unknown> }).meshConfig
    mc.rackRegionLevel = 3 // Base_leakage: 21 mm for its leakage slots
    mc.acuRefinementLevel = [2, 2]
    const p = meshSizing().sixSigmaMeshConfig(doc as never).patch
    expect(p.rackRegionLevel).toBe(3) // the pitch alone would say 2 (42 mm)
    expect(p.acuRefinementLevel).toEqual([2, 3])
    expect(p.wallBafflesRefinementLevel).toEqual([2, 3])
  })

  it('budgets 1.5 x 7,100 cells per m², never under 500k', () => {
    expect(meshSizing().sixSigmaMeshConfig(room(10, 6, 0.25)).patch.maxGlobalCells).toBe(700000)
    expect(meshSizing().sixSigmaMeshConfig(room(2, 2, 0.25)).patch.maxGlobalCells).toBe(500000)
  })
})

describe('describeMeshSizing', () => {
  it('reports the cell edge per class and flags a cell under 17.5 mm', () => {
    const doc = room(10, 6, 0.25) as Record<string, unknown>
    ;(doc.runDefaults as { meshConfig: Record<string, unknown> }).meshConfig.rackRegionLevel = 3
    const d = meshSizing().describeMeshSizing(doc as never)
    expect(d.rows.find((r) => r.key === 'background')?.cellMm).toBeCloseTo(250)
    expect(d.rows.find((r) => r.key === 'rack_region')?.cellMm).toBeCloseTo(31.25)
    expect(d.budget.areaM2).toBeCloseTo(60)
    expect(d.budget.recommendedCells).toBe(700000)
  })

  it('mirrors the runner: a 45 mm fan server raises the rack level, the floor caps it', () => {
    const doc = room(10, 6, 0.2) as Record<string, unknown>
    const cons = doc.constructions as { racks: Record<string, unknown> }
    cons.racks.r1 = {
      geometry: { slotHeight: 0.045 },
      constructions: { servers: { s1: { type: 'fanServer', geometry: { slotOccupation: 1 } } } },
    }
    const mc = (doc.runDefaults as { meshConfig: Record<string, unknown> }).meshConfig
    mc.rackRegionLevel = 2
    expect(meshSizing().describeMeshSizing(doc as never).rack.level).toBe(3)
    mc.rackCellMinSize = 0.03
    const capped = meshSizing().describeMeshSizing(doc as never).rack
    expect(capped.level).toBe(2)
    expect(capped.capped).toBe(true)
  })
})
