import * as THREE from 'three'
import { expect, it } from 'vitest'

import { createCfdrunBuild } from '../src/legacy/cfdrunBuild'
import { stateV4 } from '../src/legacy/cfdprojModules'
import { defaultSolverConfig, solverConfigOf } from '../src/run/solverConfig'
import type { CfdProject } from '../src/schema/cfdproj'

// 2026-10-22: the return-air verdict needs at least 1000 steady iterations
// (backend convergence.minimum_run_iterations), so a new project saves 1500;
// a monitored steady solve still stops by itself once the return air is steady.
const NEW_PROJECT_END_TIME = 1500
type Sim = { simulationConfig: Record<string, unknown> }
const sim = (doc: unknown) => (doc as { runDefaults: Sim }).runDefaults.simulationConfig

it('a new project, and the solver panel of one, start on 1500 steady iterations', () => {
  expect(sim(stateV4().getInitialStateV4()).endTime).toBe(NEW_PROJECT_END_TIME)
  expect(sim(stateV4().newDocumentV4()).endTime).toBe(NEW_PROJECT_END_TIME)
  expect(sim(stateV4().newDocumentV4()).solverType).toBe('steady')
  expect(defaultSolverConfig().endTime).toBe(NEW_PROJECT_END_TIME)
  expect(solverConfigOf(null).endTime).toBe(NEW_PROJECT_END_TIME)
})

it.each([undefined, 500])('the shared exporter writes the iterations the panel shows (saved %s)', (saved) => {
  const project = stateV4().getInitialStateV4() as CfdProject
  const sc = sim(project)
  if (saved === undefined) delete sc.endTime
  else sc.endTime = saved
  const host = window as unknown as { allModels?: () => unknown[] }
  const previousAllModels = host.allModels
  host.allModels = () => stateV4().allModels(project)
  try {
    const builder = createCfdrunBuild({state:project,globalGroup:new THREE.Group(),LENGTH_UNITS:{m:1,mm:.001},_MESH_MODES:{}})
    const blob = builder.buildCfdrunJson().simulationConfig as Record<string, unknown>
    expect(blob.endTime).toBe(solverConfigOf(project).endTime)
    expect(blob.endTime).toBe(saved ?? NEW_PROJECT_END_TIME)
  } finally {
    if (previousAllModels) host.allModels = previousAllModels
    else delete host.allModels
  }
})
