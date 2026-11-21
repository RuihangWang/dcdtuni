import { beforeEach, describe, expect, it } from 'vitest'
import {
  display2k,
  fromDisplay,
  isTemperatureField,
  k2display,
  setTempUnit,
  tempUnit,
  tempUnitLabel,
  toDisplay,
} from '../src/postprocess/ppUnits'

describe('temperature units', () => {
  beforeEach(() => setTempUnit('C'))

  it('defaults to Celsius, as the legacy panel does', () => {
    expect(tempUnit()).toBe('C')
    expect(tempUnitLabel()).toBe('°C')
  })

  it('round-trips through every unit', () => {
    for (const u of ['C', 'K', 'F'] as const) {
      setTempUnit(u)
      expect(display2k(k2display(300))).toBeCloseTo(300, 6)
    }
  })

  it('converts the freezing point correctly in each unit', () => {
    setTempUnit('C')
    expect(k2display(273.15)).toBeCloseTo(0, 6)
    setTempUnit('F')
    expect(k2display(273.15)).toBeCloseTo(32, 6)
    setTempUnit('K')
    expect(k2display(273.15)).toBe(273.15)
  })

  it('uses an explicit unit without changing the global selection', () => {
    setTempUnit('F')
    expect(k2display(293.15, 'C')).toBeCloseTo(20, 10)
    expect(display2k(20, 'C')).toBeCloseTo(293.15, 10)
    expect(tempUnitLabel('K')).toBe('K')
    expect(tempUnit()).toBe('F')
    expect(k2display(273.15)).toBeCloseTo(32, 10)
  })

  it('converts ONLY the temperature field', () => {
    // Velocity in m/s and turbulent viscosity are not temperatures. Subtracting
    // 273.15 from a velocity would put the colour scale in a different world.
    expect(isTemperatureField('T')).toBe(true)
    expect(isTemperatureField('U')).toBe(false)
    expect(isTemperatureField('alphat')).toBe(false)
    expect(toDisplay('U', 3.2)).toBe(3.2)
    expect(fromDisplay('U', 3.2)).toBe(3.2)
    expect(toDisplay('T', 300)).toBeCloseTo(26.85, 6)
  })

  it('is the identity in Kelvin, so the shader limits are untouched', () => {
    setTempUnit('K')
    expect(toDisplay('T', 300)).toBe(300)
    expect(fromDisplay('T', 300)).toBe(300)
  })
})
