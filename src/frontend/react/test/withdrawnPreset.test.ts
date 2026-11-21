import { afterEach, expect, it } from 'vitest'

import { stateV4 } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import { solverConfigOf } from '../src/run/solverConfig'
import { NON_ORTHO_SCHEMES, normalizeWithdrawnPreset, presetWrite } from '../src/run/solverPresets'
import { t } from '../src/i18n'
import type { CfdProject } from '../src/schema/cfdproj'

// The 平衡 and 高精度 solver presets were withdrawn on 2026-11-12 (owner): one set
// of schemes, 快速, for both solver engines. A project saved with either is
// rewritten when it is loaded — by the editor and by the headless compiler the
// scripted API uses, which share the store's load — and the load says so.

/** A project as the editor saved it under one of the three old presets. */
const savedWith = (preset: string, nonOrtho = false) => {
  const project = stateV4().getInitialStateV4() as CfdProject
  const sim = project.runDefaults!.simulationConfig as Record<string, unknown>
  sim.solverPreset = preset
  sim.nonOrthoCorrection = nonOrtho
  if (preset === 'balanced') {
    sim.schemes = {
      divSchemeU: 'bounded Gauss linearUpwind grad(U)', divSchemeT: 'bounded Gauss linearUpwind grad(U)',
      divSchemeK: 'bounded Gauss limitedLinear 1', divSchemeEpsilon: 'bounded Gauss limitedLinear 1',
      gradSchemeDefault: 'Gauss linear', gradSchemeU: 'cellLimited Gauss linear 1', gradSchemeK: '',
      gradSchemeEpsilon: '', laplacianScheme: 'Gauss linear limited 0.667', snGradScheme: 'limited 0.667',
    }
    sim.relaxationFactors = { p_rgh: 0.3, U: 0.7, T: 0.7, k_epsilon: 0.3 }
    sim.nNonOrthogonalCorrectors = 0
  } else if (preset === 'accurate') {
    sim.schemes = {
      divSchemeU: 'bounded Gauss limitedCubicV 1', divSchemeT: 'bounded Gauss limitedCubic 1',
      divSchemeK: 'bounded Gauss linearUpwind grad(k)', divSchemeEpsilon: 'bounded Gauss linearUpwind grad(epsilon)',
      gradSchemeDefault: 'cellLimited Gauss linear 1', gradSchemeU: '', gradSchemeK: '', gradSchemeEpsilon: '',
      laplacianScheme: 'Gauss linear corrected', snGradScheme: 'corrected',
    }
    sim.relaxationFactors = { p_rgh: 0.3, U: 0.5, T: 0.5, k_epsilon: 0.3 }
    sim.nNonOrthogonalCorrectors = 1
  } else if (preset === 'custom') {
    sim.schemes = { ...presetWrite('fast', false).schemes, divSchemeU: 'bounded Gauss linearUpwind grad(U)' }
    sim.relaxationFactors = { ...presetWrite('fast', false).relaxationFactors }
  }
  return JSON.parse(JSON.stringify(project)) as CfdProject
}

const withdrawnLine = () => store.getLog().some((entry) => entry.detail === t('log.preset_withdrawn'))

afterEach(() => {
  store.loadProject(stateV4().getInitialStateV4() as CfdProject, null)
})

it.each(['balanced', 'accurate'])('a project saved with %s loads on 快速, everything a press writes', (name) => {
  store.loadProject(savedWith(name), '/cases/old.cfdproj')
  const sim = solverConfigOf(store.getProject())
  const fast = presetWrite('fast', false)
  expect(sim.solverPreset).toBe('fast')
  expect(sim.schemes).toEqual(fast.schemes)
  expect(sim.relaxationFactors).toEqual(fast.relaxationFactors)
  expect(sim.nNonOrthogonalCorrectors).toBe(fast.nNonOrthogonalCorrectors)
  expect(withdrawnLine()).toBe(true)
  expect(t('log.preset_withdrawn')).toMatch(/2026-11-12/)
  // A migration, not an edit: nothing to undo, and the project is not dirty.
  expect(store.canUndo()).toBe(false)
  expect(store.isDirty()).toBe(false)
})

it('keeps 非正交修正 as a press of 快速 would', () => {
  store.loadProject(savedWith('balanced', true), null)
  const sim = solverConfigOf(store.getProject())
  expect(sim.schemes?.divSchemeU).toBe('bounded Gauss upwind')
  expect(sim.schemes?.laplacianScheme).toBe(NON_ORTHO_SCHEMES.laplacianScheme)
  expect(sim.schemes?.snGradScheme).toBe(NON_ORTHO_SCHEMES.snGradScheme)
  expect(sim.nNonOrthogonalCorrectors).toBe(1)
})

it('leaves 快速 and hand-edited (custom) projects as they are, without the log line', () => {
  for (const name of ['fast', 'custom']) {
    const saved = savedWith(name)
    const before = JSON.parse(JSON.stringify(saved.runDefaults!.simulationConfig))
    store.loadProject(saved, null)
    const sim = solverConfigOf(store.getProject()) as unknown as Record<string, unknown>
    expect(sim.solverPreset).toBe(before.solverPreset)
    expect(sim.schemes).toEqual(before.schemes)
    expect(withdrawnLine()).toBe(false)
  }
  expect(normalizeWithdrawnPreset(null)).toBeNull()
  expect(normalizeWithdrawnPreset({})).toBeNull()
})
