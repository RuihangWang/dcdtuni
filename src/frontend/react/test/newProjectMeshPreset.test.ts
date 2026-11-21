import { expect, it } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { meshSizing } from '../src/legacy/meshSizing'
import { meshConfigOf } from '../src/run/meshConfig'

type ProjectDefaults = { runDefaults: { meshConfig: Record<string, unknown>; simulationConfig: Record<string, unknown> } }

it('starts a new React project with the bounded dcCart production preset', () => {
  const doc = stateV4().getInitialStateV4() as unknown as CfdProject & ProjectDefaults
  const config = meshConfigOf(doc)
  expect(config).toMatchObject({ meshBaseSize: 0.25, meshEngine: 'dcCart', meshTimeLimitSeconds: 300,
    parallelMesh: true, meshProcess: 8, firstLayerMinFraction: 0.3, firstLayerMax: 0.005, rackCellMinSize: 0.0175 })
  expect(doc.runDefaults.simulationConfig).toMatchObject({ parallelSolve: true, solveProcess: 8 })
})

it('applying the production preset retains user local refinement and cell budget', () => {
  const doc = stateV4().getInitialStateV4() as unknown as CfdProject & ProjectDefaults
  doc.runDefaults.meshConfig.acuRefinementLevel = [2, 4]
  doc.runDefaults.meshConfig.maxGlobalCells = 24000000
  const patch = meshSizing().dcCartProductMeshConfig(doc).patch
  expect(patch.acuRefinementLevel).toBeUndefined()
  expect(patch.maxGlobalCells).toBeUndefined()
  expect(patch.meshBaseSize).toBe(0.25)
})
