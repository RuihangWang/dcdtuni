import { describe, expect, it } from 'vitest'
import { nonOrthoWrite, OFFERED_PRESETS, presetWrite, SOLVER_PRESETS } from '../src/run/solverPresets'

describe('the preset table', () => {
  it('holds 快速 only: 平衡 and 高精度 were withdrawn on 2026-11-12', () => {
    expect(Object.keys(SOLVER_PRESETS)).toEqual(['fast'])
    expect([...OFFERED_PRESETS]).toEqual(['fast'])
  })
})

describe('presetWrite', () => {
  it('writes the schemes and relaxation factors, not just the name', () => {
    const w = presetWrite('fast', false)
    expect(w.solverPreset).toBe('fast')
    expect(w.schemes.divSchemeU).toBe('bounded Gauss upwind')
    expect(w.relaxationFactors).toEqual({ p_rgh: 0.3, U: 0.7, T: 0.95, k_epsilon: 0.7 })
    expect(w.nNonOrthogonalCorrectors).toBe(0)
  })

  it('carries every scheme key the legacy preset defines', () => {
    const w = presetWrite('fast', false)
    expect(Object.keys(w.schemes).sort()).toEqual([
      'divSchemeEpsilon', 'divSchemeK', 'divSchemeT', 'divSchemeU',
      'gradSchemeDefault', 'gradSchemeEpsilon', 'gradSchemeK', 'gradSchemeU',
      'laplacianScheme', 'snGradScheme',
    ])
  })

  it('overrides the laplacian pair and floors the correctors when non-ortho is on', () => {
    // "fast" asks for 0 correctors, which on a non-orthogonal mesh is the
    // setting that diverges.
    expect(SOLVER_PRESETS.fast.nNonOrthogonalCorrectors).toBe(0)
    const w = presetWrite('fast', true)
    expect(w.nNonOrthogonalCorrectors).toBe(1)
    expect(w.schemes.laplacianScheme).toBe('Gauss linear limited corrected 0.5')
    expect(w.schemes.snGradScheme).toBe('limited corrected 0.5')
  })

  it('leaves the preset pair alone when non-ortho is off', () => {
    expect(presetWrite('fast', false).schemes.snGradScheme).toBe('limited 0.5')
  })
})

describe('nonOrthoWrite', () => {
  it('restores the ACTIVE preset when switched off, not a fixed default', () => {
    expect(nonOrthoWrite(false, 'fast')).toEqual({
      schemes: { laplacianScheme: 'Gauss linear limited 0.5', snGradScheme: 'limited 0.5' },
      nNonOrthogonalCorrectors: 0,
    })
  })

  it('writes NOTHING for a preset name it does not know', () => {
    // This used to fall back to `fast`. A hand edit sets `custom` (see
    // `detectPreset`), and the fallback would replace the user's own laplacian
    // with one they never chose the moment they unticked the box. The legacy
    // skips the block when the lookup misses (STL_generate.html:26082). The
    // withdrawn names are unknown too: a project saved with one is rewritten
    // on load, so none reaches this function from a file.
    expect(nonOrthoWrite(false, 'custom')).toEqual({ schemes: {} })
    expect(nonOrthoWrite(false, 'balanced')).toEqual({ schemes: {} })
    expect(nonOrthoWrite(false, 'nonsense').schemes.snGradScheme).toBeUndefined()
    expect(nonOrthoWrite(false, 'nonsense').nNonOrthogonalCorrectors).toBeUndefined()
  })

  it('is a round trip: on then off leaves the case as it was', () => {
    const before = presetWrite('fast', false)
    const off = nonOrthoWrite(false, 'fast')
    expect(off.schemes.laplacianScheme).toBe(before.schemes.laplacianScheme)
    expect(off.nNonOrthogonalCorrectors).toBe(before.nNonOrthogonalCorrectors)
  })
})
