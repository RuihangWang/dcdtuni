import { describe, expect, it } from 'vitest'
import { coreWarning, physicalCores } from '../src/run/coreCount'
import { t } from '../src/i18n'

/**
 * The threshold is HALF the reported core count, not the whole of it. The port
 * had `procs > cpuCount`, which on this repo's 48-logical-core host could not
 * fire below 49 — twice the number that matters for an OpenFOAM decomposition.
 */

describe('coreWarning', () => {
  it('warns above the physical count, not the logical one', () => {
    expect(physicalCores(48)).toBe(24)
    expect(coreWarning(24, 48)).toBeNull()
    expect(coreWarning(25, 48)).toEqual({ n: 25, physical: 24, total: 48, recommended: 24 })
    // The old rule would have stayed silent here, which is the bug.
    expect(coreWarning(48, 48)).not.toBeNull()
  })

  it('says nothing when the server never reported a core count', () => {
    // Half of zero is zero, so an unguarded rule would warn on every project
    // before the first /api/server-info comes back.
    expect(coreWarning(16, undefined)).toBeNull()
    expect(coreWarning(16, 0)).toBeNull()
  })

  it('fills every placeholder the shared message expects', () => {
    const warning = coreWarning(40, 48)!
    const text = t('status.cores_warning', { ...warning })
    expect(text).not.toContain('{')
    expect(text).toContain('40')
    expect(text).toContain('24')
    expect(text).toContain('48')
  })
})
