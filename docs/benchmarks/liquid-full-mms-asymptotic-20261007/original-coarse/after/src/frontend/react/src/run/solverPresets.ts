/**
 * The three solver presets, and what pressing one actually writes.
 *
 * A preset is not a label: it rewrites every discretisation scheme, all four
 * relaxation factors and the non-orthogonal corrector count. Storing only the
 * NAME — which is what a first pass at this panel does — leaves the previous
 * preset's schemes in `simulationConfig`, and the exported .cfdrun then solves
 * with "fast" schemes under an "accurate" badge.
 *
 * Values copied from `_SOLVER_PRESETS` (STL_generate.html:26021); they are
 * OpenFOAM scheme strings and must stay byte-identical to what the legacy app
 * exports, so the two apps' manifests still diff clean.
 */

import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'

export type PresetName = 'fast' | 'balanced' | 'accurate'

export interface SolverPreset {
  divSchemeU: string
  divSchemeT: string
  divSchemeK: string
  divSchemeEpsilon: string
  gradSchemeDefault: string
  gradSchemeU: string
  gradSchemeK: string
  gradSchemeEpsilon: string
  laplacianScheme: string
  snGradScheme: string
  nNonOrthogonalCorrectors: number
  relaxP: number
  relaxU: number
  relaxT: number
  relaxKEpsilon: number
}

export const SOLVER_PRESETS: Record<PresetName, SolverPreset> = {
  fast: {
    divSchemeU: 'bounded Gauss upwind',
    divSchemeT: 'bounded Gauss upwind',
    divSchemeK: 'bounded Gauss upwind',
    divSchemeEpsilon: 'bounded Gauss upwind',
    gradSchemeDefault: 'Gauss linear',
    gradSchemeU: '',
    gradSchemeK: '',
    gradSchemeEpsilon: '',
    laplacianScheme: 'Gauss linear limited 0.5',
    snGradScheme: 'limited 0.5',
    nNonOrthogonalCorrectors: 0,
    relaxP: 0.3,
    relaxU: 0.7,
    relaxT: 0.7,
    relaxKEpsilon: 0.7,
  },
  balanced: {
    divSchemeU: 'bounded Gauss linearUpwind grad(U)',
    divSchemeT: 'bounded Gauss linearUpwind grad(U)',
    divSchemeK: 'bounded Gauss limitedLinear 1',
    divSchemeEpsilon: 'bounded Gauss limitedLinear 1',
    gradSchemeDefault: 'Gauss linear',
    gradSchemeU: 'cellLimited Gauss linear 1',
    gradSchemeK: '',
    gradSchemeEpsilon: '',
    laplacianScheme: 'Gauss linear limited 0.667',
    snGradScheme: 'limited 0.667',
    nNonOrthogonalCorrectors: 0,
    relaxP: 0.3,
    relaxU: 0.7,
    relaxT: 0.7,
    relaxKEpsilon: 0.3,
  },
  accurate: {
    divSchemeU: 'bounded Gauss limitedCubicV 1',
    divSchemeT: 'bounded Gauss limitedCubic 1',
    divSchemeK: 'bounded Gauss linearUpwind grad(k)',
    divSchemeEpsilon: 'bounded Gauss linearUpwind grad(epsilon)',
    gradSchemeDefault: 'cellLimited Gauss linear 1',
    gradSchemeU: '',
    gradSchemeK: '',
    gradSchemeEpsilon: '',
    laplacianScheme: 'Gauss linear corrected',
    snGradScheme: 'corrected',
    nNonOrthogonalCorrectors: 1,
    relaxP: 0.3,
    relaxU: 0.5,
    relaxT: 0.5,
    relaxKEpsilon: 0.3,
  },
}

/** The corrected-scheme pair a non-orthogonal mesh needs. */
export const NON_ORTHO_SCHEMES = {
  laplacianScheme: 'Gauss linear limited corrected 0.5',
  snGradScheme: 'limited corrected 0.5',
}

export interface PresetWrite {
  solverPreset: PresetName
  schemes: Record<string, string>
  relaxationFactors: Record<string, number>
  nNonOrthogonalCorrectors: number
}

/**
 * What a preset press writes, given whether non-ortho correction is on.
 *
 * Pure, so the override can be tested: with the checkbox on, the preset's own
 * laplacian/snGrad are REPLACED and the corrector count is floored at 1 — the
 * "fast" preset asks for 0 correctors, which on a non-orthogonal mesh is the
 * setting that produces a diverged run.
 */
export function presetWrite(name: PresetName, nonOrtho: boolean): PresetWrite {
  const p = SOLVER_PRESETS[name]
  const schemes: Record<string, string> = {
    divSchemeU: p.divSchemeU,
    divSchemeT: p.divSchemeT,
    divSchemeK: p.divSchemeK,
    divSchemeEpsilon: p.divSchemeEpsilon,
    gradSchemeDefault: p.gradSchemeDefault,
    gradSchemeU: p.gradSchemeU,
    gradSchemeK: p.gradSchemeK,
    gradSchemeEpsilon: p.gradSchemeEpsilon,
    laplacianScheme: p.laplacianScheme,
    snGradScheme: p.snGradScheme,
  }
  let correctors = p.nNonOrthogonalCorrectors
  if (nonOrtho) {
    schemes.laplacianScheme = NON_ORTHO_SCHEMES.laplacianScheme
    schemes.snGradScheme = NON_ORTHO_SCHEMES.snGradScheme
    correctors = Math.max(1, correctors)
  }
  return {
    solverPreset: name,
    schemes,
    relaxationFactors: { p_rgh: p.relaxP, U: p.relaxU, T: p.relaxT, k_epsilon: p.relaxKEpsilon },
    nNonOrthogonalCorrectors: correctors,
  }
}

/**
 * What toggling the non-ortho checkbox writes, on top of the active preset.
 *
 * Switching it off restores the ACTIVE preset's pair, not a fixed default:
 * on-then-off must leave the case exactly as it was. When the active preset is
 * `custom` there is nothing to restore TO — the schemes are the user's own — so
 * nothing is written, and the corrector count comes back undefined to say so.
 * The legacy looks the name up and skips the whole block when it misses
 * (STL_generate.html:26082); falling back to `fast` here would replace a
 * hand-chosen laplacian with one the user never picked.
 */
export function nonOrthoWrite(
  on: boolean,
  activePreset: string | undefined,
): { schemes: Record<string, string>; nNonOrthogonalCorrectors?: number } {
  if (on) {
    return { schemes: { ...NON_ORTHO_SCHEMES }, nNonOrthogonalCorrectors: 1 }
  }
  const p = SOLVER_PRESETS[activePreset as PresetName]
  if (!p) return { schemes: {} }
  return {
    schemes: { laplacianScheme: p.laplacianScheme, snGradScheme: p.snGradScheme },
    nNonOrthogonalCorrectors: p.nNonOrthogonalCorrectors,
  }
}

/** The scheme keys a preset writes, and the relaxation factors it sets. */
const PRESET_SCHEME_KEYS = [
  'divSchemeU',
  'divSchemeT',
  'divSchemeK',
  'divSchemeEpsilon',
  'gradSchemeDefault',
  'gradSchemeU',
  'gradSchemeK',
  'gradSchemeEpsilon',
  'laplacianScheme',
  'snGradScheme',
] as const

/**
 * The fields that must be there for a match to mean anything.
 *
 * Without them an empty `schemes` object matches every preset — it agrees on
 * each field it does not have — and the row would badge a case with no schemes
 * at all as `fast`. These four are also the ones that still tell the presets
 * apart when 非正交修正 has overridden the laplacian pair.
 */
const DISCRIMINATING = ['divSchemeU', 'divSchemeK'] as const
const DISCRIMINATING_RELAX = ['U', 'k_epsilon'] as const

export interface DetectableConfig {
  schemes?: Record<string, string | undefined>
  relaxationFactors?: Record<string, number | undefined>
  nonOrthoCorrection?: boolean
}

/**
 * Which preset the CURRENT settings are, or `custom`.
 *
 * A preset is a shorthand for eleven values, so once one is hand-edited the
 * badge is a claim about what the solver will do. The legacy re-derives this on
 * every scheme and relaxation write (`_detectCurrentPreset`,
 * STL_generate.html:26107) and the buttons un-highlight.
 *
 * Two deliberate differences from that function:
 *
 * - **It compares against what pressing the button would write RIGHT NOW**,
 *   non-ortho override included. The legacy compares against the raw preset, so
 *   with 非正交修正 ticked — 15 of the 22 projects in `case/` have it ticked —
 *   the laplacian never matches and every preset reads `custom` after the first
 *   edit. What a preset just wrote must detect as that preset.
 * - **It compares every field the preset writes**, not the legacy's five, so
 *   hand-editing div(T) does not leave a "balanced" badge over a case that is
 *   no longer balanced. A key the project does not carry is not evidence of
 *   divergence and is skipped; the corrector count is excluded, as in the
 *   legacy, because the non-ortho override floors it at 1 and comparing it
 *   would put every ticked "fast" case in `custom` on that alone.
 */
export function detectPreset(sim: DetectableConfig): PresetName | 'custom' {
  const schemes = sim.schemes ?? {}
  const relax = sim.relaxationFactors ?? {}
  if (
    DISCRIMINATING.some((k) => schemes[k] === undefined) ||
    DISCRIMINATING_RELAX.some((k) => relax[k] === undefined)
  ) {
    return 'custom'
  }
  const nonOrtho = !!sim.nonOrthoCorrection
  for (const name of Object.keys(SOLVER_PRESETS) as PresetName[]) {
    const w = presetWrite(name, nonOrtho)
    const schemesMatch = PRESET_SCHEME_KEYS.every(
      (k) => schemes[k] === undefined || schemes[k] === w.schemes[k],
    )
    const relaxMatch = Object.entries(w.relaxationFactors).every(
      ([k, v]) => relax[k] === undefined || relax[k] === v,
    )
    if (schemesMatch && relaxMatch) return name
  }
  return 'custom'
}

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
