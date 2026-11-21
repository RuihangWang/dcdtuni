import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { SolverPanel } from '../src/components/SolverPanel'
import { reviveKinds } from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import {
  applySolverPreset,
  detectPreset,
  OFFERED_PRESETS,
  presetWrite,
  setNonOrthoCorrection,
  setSolverScheme,
  SOLVER_PRESETS,
  type PresetName,
} from '../src/run/solverPresets'
import { solverConfigOf } from '../src/run/solverConfig'
import { t } from '../src/i18n'
import type { CfdProject } from '../src/schema/cfdproj'

/**
 * The preset row tells the truth after the advanced settings are hand-edited.
 *
 * A preset is a shorthand for eleven numbers and strings. Once any of them is
 * changed by hand the badge is a claim about what the solver will do, and the
 * legacy re-derives it on every scheme and relaxation write
 * (`_detectCurrentPreset`, STL_generate.html:26107) so the buttons un-highlight
 * and the row reads `custom`. React stored the name when a preset button was
 * pressed and never looked at it again: the case exported with hand-edited
 * schemes under an "accurate" badge.
 *
 * ## Two deliberate divergences from `_detectCurrentPreset`
 *
 * 1. **Non-ortho aware.** The legacy compares against the RAW preset, but with
 *    非正交修正 ticked a preset press writes the corrected laplacian/snGrad
 *    pair instead of the preset's own. So in the legacy, with that box ticked,
 *    pressing 平衡 and then touching anything at all reads `custom` — and 15 of
 *    the 22 projects in `case/` have it ticked. Here the comparison is against
 *    what pressing that button would write RIGHT NOW, which keeps the invariant
 *    that matters: what a preset just wrote detects as that preset.
 * 2. **Every field it writes, not five.** The legacy compares divSchemeU,
 *    divSchemeK, laplacianScheme, relax U and relax k/ε only, so hand-editing
 *    div(T) leaves an untouched "balanced" badge over a case that is no longer
 *    balanced. The corrector count stays out of it, as in the legacy: the
 *    non-ortho override floors it at 1, and comparing it would put every
 *    "fast" case with that box ticked into `custom` on this rule alone.
 */

const PROJECT = resolve(process.cwd(), '../../../case/Base_leakage/Base_leakage.cfdproj')
const NAMES: PresetName[] = ['fast']

/** The simulationConfig shape `detectPreset` reads. */
function simFor(name: PresetName, nonOrtho: boolean) {
  const w = presetWrite(name, nonOrtho)
  return {
    nonOrthoCorrection: nonOrtho,
    schemes: { ...w.schemes },
    relaxationFactors: { ...w.relaxationFactors },
    nNonOrthogonalCorrectors: w.nNonOrthogonalCorrectors,
  }
}

describe('detectPreset', () => {
  it('names what a preset press just wrote', () => {
    for (const name of NAMES) expect(detectPreset(simFor(name, false))).toBe(name)
  })

  it('still does with 非正交修正 on, where the legacy would say custom', () => {
    // The override replaces laplacian and snGrad for EVERY preset, so those two
    // stop discriminating — div(U), div(k), relax U and relax k/ε still do.
    for (const name of NAMES) expect(detectPreset(simFor(name, true))).toBe(name)
  })

  it('falls to custom on a hand-edited relaxation factor, and back again', () => {
    const sim = simFor('fast', false)
    sim.relaxationFactors.U = 0.65
    expect(detectPreset(sim)).toBe('custom')
    sim.relaxationFactors.U = SOLVER_PRESETS.fast.relaxU
    expect(detectPreset(sim)).toBe('fast')
  })

  it('falls to custom on div(T) too — a field the legacy does not compare', () => {
    const sim = simFor('fast', false)
    sim.schemes.divSchemeT = 'bounded Gauss limitedLinear 1'
    expect(detectPreset(sim)).toBe('custom')
  })

  it('does not treat a key the project never carried as a divergence', () => {
    // An older project may not have every scheme key. A missing key is no
    // evidence either way; branding it custom would be a badge that never
    // matches anything.
    const sim = simFor('fast', false)
    delete (sim.schemes as Record<string, string>).gradSchemeK
    expect(detectPreset(sim)).toBe('fast')
  })

  it('says custom for a config with nothing in it', () => {
    // Not a vacuous match on the first preset: an empty schemes object equals
    // every preset on the fields it does not have.
    expect(detectPreset({})).toBe('custom')
    expect(detectPreset({ schemes: {}, relaxationFactors: {} })).toBe('custom')
  })

  it('says custom for the settings of a withdrawn preset', () => {
    // 平衡 as it was: no preset in the table matches it any more.
    const sim = simFor('fast', false)
    sim.schemes.divSchemeU = 'bounded Gauss linearUpwind grad(U)'
    sim.schemes.divSchemeK = 'bounded Gauss limitedLinear 1'
    sim.relaxationFactors.k_epsilon = 0.3
    expect(detectPreset(sim)).toBe('custom')
  })
})

describe('setSolverScheme', () => {
  beforeEach(() => {
    store.loadProject(
      reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject),
      '/cases/Base_leakage.cfdproj',
    )
    applySolverPreset('fast')
  })

  const sim = () => solverConfigOf(store.getProject())

  it('writes the value and re-derives the name', () => {
    expect(sim().solverPreset).toBe('fast')
    setSolverScheme('relaxationFactors', 'U', 0.65)
    expect(sim().relaxationFactors?.U).toBe(0.65)
    expect(sim().solverPreset).toBe('custom')
  })

  it('finds its way back when the numbers are restored', () => {
    setSolverScheme('schemes', 'divSchemeU', 'bounded Gauss linearUpwind grad(U)')
    expect(sim().solverPreset).toBe('custom')
    setSolverScheme('schemes', 'divSchemeU', SOLVER_PRESETS.fast.divSchemeU)
    expect(sim().solverPreset).toBe('fast')
  })

  it('is ONE undo step, name and value together', () => {
    const depth = store.undoDepth()
    setSolverScheme('relaxationFactors', 'U', 0.65)
    expect(store.undoDepth()).toBe(depth + 1)
    store.undo()
    expect(sim().relaxationFactors?.U).toBe(SOLVER_PRESETS.fast.relaxU)
    expect(sim().solverPreset).toBe('fast')
  })

  it('leaves a custom setup alone when 非正交修正 is unticked', () => {
    // `nonOrthoWrite` fell back to FAST's laplacian for any preset name it did
    // not know, which 'custom' now is — so unticking the box would have
    // overwritten the user's own scheme with one they never chose. The legacy
    // looks the name up and does nothing when it misses (:26082).
    setNonOrthoCorrection(true)
    setSolverScheme('schemes', 'laplacianScheme', 'Gauss linear corrected')
    expect(sim().solverPreset).toBe('custom')
    setNonOrthoCorrection(false)
    expect(sim().schemes?.laplacianScheme).toBe('Gauss linear corrected')
  })
})

describe('the preset row', () => {
  let host: HTMLDivElement
  let root: Root

  const render = () =>
    flushSync(() => root.render(<SolverPanel project={store.getProject()} />))

  function primaryPresets(): string[] {
    return NAMES.filter((n) => document.getElementById(`preset-${n}`)?.className.includes('primary'))
  }

  /** Type into a NumberField and commit it — `focusout`, which React sees. */
  function commit(id: string, value: string) {
    const el = document.getElementById(id) as HTMLInputElement
    expect(el, id).not.toBeNull()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    flushSync(() => el.dispatchEvent(new FocusEvent('focusin', { bubbles: true })))
    setter.call(el, value)
    flushSync(() => el.dispatchEvent(new Event('input', { bubbles: true })))
    // `blur` does not bubble and React 18 delegates at the root, so onBlur is
    // driven by `focusout`.
    flushSync(() => el.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
  }

  beforeEach(() => {
    store.loadProject(
      reviveKinds(JSON.parse(readFileSync(PROJECT, 'utf8')) as CfdProject),
      '/cases/Base_leakage.cfdproj',
    )
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    render()
    // Open 高级设置, which holds the scheme and relaxation boxes. There is more
    // than one collapse header on this panel, so it is found by its label.
    const header = [...host.querySelectorAll('.collapse-header')].find((el) =>
      el.textContent?.includes(t('label.advanced_settings')),
    )
    expect(header, 'advanced settings header').toBeDefined()
    flushSync(() => header!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  })

  afterEach(() => {
    flushSync(() => root.unmount())
    host.remove()
  })

  it('un-highlights when a relaxation factor no longer matches', () => {
    flushSync(() =>
      document.getElementById('preset-fast')!.dispatchEvent(new MouseEvent('click', { bubbles: true })),
    )
    render()
    expect(primaryPresets()).toEqual(['fast'])

    commit('slv-relax-u', '0.65')
    render()
    expect(solverConfigOf(store.getProject()).solverPreset).toBe('custom')
    expect(primaryPresets()).toEqual([])
  })

  it('offers 快速 only: 平衡 and 高精度 are withdrawn (owner 2026-11-12)', () => {
    expect(OFFERED_PRESETS).toEqual(['fast'])
    expect(document.getElementById('preset-fast')).not.toBeNull()
    expect(document.getElementById('preset-balanced')).toBeNull()
    expect(document.getElementById('preset-accurate')).toBeNull()
  })
})
