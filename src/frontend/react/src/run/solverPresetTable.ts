/**
 * The solver preset, and what pressing it actually writes.
 *
 * A preset is not a label: it rewrites every discretisation scheme, all four
 * relaxation factors and the non-orthogonal corrector count. Storing only the
 * NAME leaves the previous schemes in `simulationConfig`, and the exported
 * .cfdrun then solves with schemes the badge does not describe.
 *
 * There is ONE preset, `fast` (first-order upwind), the set both solver engines
 * use. `balanced` and `accurate` were withdrawn on 2026-11-12 (owner): a
 * project saved with either is rewritten to `fast` when it is loaded
 * (`normalizeWithdrawnPreset`, called by the store's load like the retired
 * mesh engine), and the load says so in the operation log.
 *
 * `fast` relaxes T at 0.95 since 2026-10-30 (owner): T is a linear scalar
 * equation, and at 0.7 the heat took a thousand iterations to reach the returns
 * of a large room (Shinaide 691 -> 348 iterations, the energy balance closed).
 *
 * This module is pure (no store): the store imports it for the load-time
 * rewrite, and `solverPresets.ts` adds the edits that go through the store.
 */

export type PresetName = 'fast'

/** The presets the panel offers. */
export const OFFERED_PRESETS: readonly PresetName[] = ['fast']

/** Names a saved project may still carry; the load rewrites them to `fast`. */
export const WITHDRAWN_PRESETS: readonly string[] = ['balanced', 'accurate']

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
    relaxT: 0.95,
    relaxKEpsilon: 0.7,
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

/**
 * Rewrite a project saved with a withdrawn preset to `fast`, in place.
 *
 * A load-time migration, not an edit: the caller logs it and the project stays
 * clean. Everything a preset press writes is rewritten (schemes, relaxation
 * factors, corrector count), with 非正交修正 honoured as a press would. Returns
 * the withdrawn name when it changed the project, else null. A `custom`
 * project (hand-edited schemes) is the user's own and is left alone.
 */
export function normalizeWithdrawnPreset(project: unknown): string | null {
  const sim = (project as { runDefaults?: { simulationConfig?: Record<string, unknown> } } | null | undefined)
    ?.runDefaults?.simulationConfig
  const name = sim?.solverPreset
  if (!sim || typeof name !== 'string' || !WITHDRAWN_PRESETS.includes(name)) return null
  const w = presetWrite('fast', !!sim.nonOrthoCorrection)
  sim.solverPreset = w.solverPreset
  sim.schemes = w.schemes
  sim.relaxationFactors = w.relaxationFactors
  sim.nNonOrthogonalCorrectors = w.nNonOrthogonalCorrectors
  return name
}
