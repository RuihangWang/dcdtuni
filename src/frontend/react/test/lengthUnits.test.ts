import { describe, expect, it } from 'vitest'
import { fromDisplay, LENGTH_UNITS, stepFor, toDisplay, unitOf } from '../src/lengthUnits'
import type { CfdProject } from '../src/schema/cfdproj'

const withUnit = (inputUnit?: string) => ({ meta: { inputUnit } }) as unknown as CfdProject

describe('unitOf', () => {
  it('defaults to metres for a project that never set one', () => {
    expect(unitOf(null)).toBe('m')
    expect(unitOf(withUnit(undefined))).toBe('m')
  })

  it('ignores a unit it does not know rather than trusting the file', () => {
    // A bad value here would scale every geometry box by NaN.
    expect(unitOf(withUnit('furlong'))).toBe('m')
  })

  it('reads mm and cm', () => {
    expect(unitOf(withUnit('mm'))).toBe('mm')
    expect(unitOf(withUnit('cm'))).toBe('cm')
  })
})

describe('conversion', () => {
  it('round-trips through the display value', () => {
    for (const unit of ['mm', 'cm', 'm'] as const) {
      expect(fromDisplay(toDisplay(0.6, unit), unit)).toBeCloseTo(0.6, 12)
    }
  })

  it('shows 600 for 0.6 m in mm', () => {
    expect(toDisplay(0.6, 'mm')).toBeCloseTo(600, 9)
    expect(fromDisplay(600, 'mm')).toBeCloseTo(0.6, 12)
  })

  it('keeps metres unchanged', () => {
    expect(toDisplay(2.5, 'm')).toBe(2.5)
  })
})

describe('stepFor', () => {
  it('is a sensible nudge in each unit, not one number scaled', () => {
    // 0.05 m is a nudge; 0.05 mm is not.
    expect(stepFor('m')).toBe(LENGTH_UNITS.m.step)
    expect(stepFor('mm')).toBeGreaterThan(LENGTH_UNITS.m.step)
  })
})
