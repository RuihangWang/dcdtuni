/**
 * The solver preset's edits: what goes through the project store.
 *
 * The table, `presetWrite`, `detectPreset` and the load-time rewrite of the
 * withdrawn presets are in `solverPresetTable.ts` (pure, re-exported here).
 */

import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'
import { detectPreset, nonOrthoWrite, presetWrite, type DetectableConfig, type PresetName } from './solverPresetTable'

export * from './solverPresetTable'

function simOf(draft: CfdProject): Record<string, unknown> {
  const holder = draft as CfdProject & { runDefaults?: { simulationConfig?: Record<string, unknown> } }
  const defaults = holder.runDefaults ?? (holder.runDefaults = {})
  return defaults.simulationConfig ?? (defaults.simulationConfig = {})
}

export function applySolverPreset(name: PresetName) {
  store.edit('applySolverPreset', name, (draft) => {
    const sim = simOf(draft)
    const w = presetWrite(name, !!sim.nonOrthoCorrection)
    sim.solverPreset = w.solverPreset
    sim.schemes = w.schemes
    sim.relaxationFactors = w.relaxationFactors
    sim.nNonOrthogonalCorrectors = w.nNonOrthogonalCorrectors
  })
}

export function setNonOrthoCorrection(on: boolean) {
  store.edit('toggleNonOrthoCorrection', String(on), (draft) => {
    const sim = simOf(draft)
    sim.nonOrthoCorrection = on
    const w = nonOrthoWrite(on, sim.solverPreset as string | undefined)
    sim.schemes = { ...((sim.schemes as Record<string, string>) ?? {}), ...w.schemes }
    if (w.nNonOrthogonalCorrectors !== undefined) {
      sim.nNonOrthogonalCorrectors = w.nNonOrthogonalCorrectors
    }
  })
}

/**
 * Hand-edit one scheme or relaxation factor, and re-derive the preset name.
 *
 * The legacy's `setSolverScheme` (STL_generate.html:26092) does the same three
 * things in the same order under ONE undo checkpoint: write the field, set
 * `solverPreset = _detectCurrentPreset()`, log. Splitting the re-derivation
 * into a second edit would put a "the badge changed" step in the undo stack
 * that the user never took.
 */
export function setSolverScheme(
  group: 'schemes' | 'relaxationFactors',
  key: string,
  value: string | number,
) {
  store.edit('setSolverScheme', `${key} = ${value}`, (draft) => {
    const sim = simOf(draft)
    const bag = (sim[group] ?? (sim[group] = {})) as Record<string, unknown>
    bag[key] = value
    sim.solverPreset = detectPreset(sim as DetectableConfig)
  })
}

/**
 * The non-orthogonal corrector count, which the legacy routes through the same
 * function. `detectPreset` does not compare it, so the name only changes here
 * if the schemes were already off a preset — but the re-derivation is kept so
 * there is one path, not two with different rules.
 */
export function setNonOrthoCorrectors(count: number) {
  store.edit('setSolverScheme', `nNonOrthogonalCorrectors = ${count}`, (draft) => {
    const sim = simOf(draft)
    sim.nNonOrthogonalCorrectors = count
    sim.solverPreset = detectPreset(sim as DetectableConfig)
  })
}
