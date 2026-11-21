import { describe, expect, it } from 'vitest'
import { formatColorbarTick, scalarFieldUnit } from '../src/postprocess/ppUnits'

describe('readable scientific field legends', () => {
  it('keeps the real millimetre-per-second velocity bounds distinct', () => {
    const min = 0
    const max = 0.005793154092885289
    expect(formatColorbarTick(min, min, max)).toBe('0')
    expect(Number(formatColorbarTick(max, min, max))).toBeCloseTo(max, 5)
    expect(formatColorbarTick(max, min, max)).not.toBe(formatColorbarTick(min, min, max))
  })

  it('preserves signs and near-zero turbulent values', () => {
    const min = -1.2e-12
    const max = 3.4e-12
    expect(Number(formatColorbarTick(min, min, max))).toBeCloseTo(min, 14)
    expect(Number(formatColorbarTick(max, min, max))).toBeCloseTo(max, 14)
    expect(formatColorbarTick(min, min, max)).not.toBe('0.0')
  })

  it('distinguishes narrow limits at a large offset', () => {
    const min = 1e8
    const max = min + 0.01
    expect(formatColorbarTick(min, min, max)).not.toBe(formatColorbarTick(max, min, max))
    expect(Math.abs(Number(formatColorbarTick(max, min, max)) - max)).toBeLessThan(0.001)
  })

  it('keeps conventional temperature ticks compact and rejects nonfinite labels', () => {
    expect(formatColorbarTick(32, 22, 32)).toBe('32.0')
    expect(formatColorbarTick(22, 22, 32)).toBe('22.0')
    expect(formatColorbarTick(NaN, 0, 1)).toBe('—')
    expect(formatColorbarTick(Infinity, 0, 1)).toBe('—')
  })

  it('names the actual velocity and kinematic pressure units', () => {
    for (const field of ['U', 'Umag', 'Ux', 'Uy', 'Uz']) expect(scalarFieldUnit(field)).toBe('m/s')
    for (const field of ['p', 'p_rgh']) expect(scalarFieldUnit(field)).toBe('m²/s²')
    expect(scalarFieldUnit('nut')).toBe('m²/s')
    expect(scalarFieldUnit('epsilon')).toBe('m²/s³')
    expect(scalarFieldUnit('unknown')).toBe('')
  })
})
