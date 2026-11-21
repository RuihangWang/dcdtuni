import { describe, expect, it } from 'vitest'
import { sheetDeadAxis, sheetSizeSpec } from '../src/geometry/sheetSizeFields'

/**
 * The box that must not exist is the point of this. The shared renderer builds
 * a wall, a baffle and a rectangular floor from TWO size components, so the
 * third is written to the .cfdproj and never drawn or exported — an editable
 * field that does nothing.
 */

describe('sheetSizeSpec', () => {
  it('gives a rectangular floor its two in-plane axes, X and Z', () => {
    const spec = sheetSizeSpec('floor')
    expect(spec?.fields.map((f) => f.axis)).toEqual(['x', 'z'])
    expect(spec?.fields.map((f) => f.legacyKey)).toEqual(['W', 'D'])
    expect(spec?.hint).toBe('hint.floor_zero_thickness')
    expect(spec?.title).toBe('inspector.floor_naming')
  })

  it('gives a wall its width and height', () => {
    const spec = sheetSizeSpec('wall')
    expect(spec?.fields.map((f) => f.axis)).toEqual(['x', 'y'])
    expect(spec?.fields.map((f) => f.label)).toEqual(['label.width_w', 'label.height_h'])
    expect(spec?.hint).toBe('hint.wall_zero_thickness')
  })

  it('gives a baffle the same pair with its own hint', () => {
    const spec = sheetSizeSpec('baffle')
    expect(spec?.fields.map((f) => f.axis)).toEqual(['x', 'y'])
    expect(spec?.hint).toBe('hint.baffle_zero_thickness')
  })

  it('has nothing to say about a solid body', () => {
    expect(sheetSizeSpec('box')).toBeNull()
    expect(sheetSizeSpec('rack')).toBeNull()
    expect(sheetSizeSpec(undefined)).toBeNull()
  })
})

describe('sheetDeadAxis', () => {
  it('names the axis a floor has no thickness on', () => {
    expect(sheetDeadAxis('floor')).toBe('y')
  })

  it('names the axis a wall and a baffle have no thickness on', () => {
    expect(sheetDeadAxis('wall')).toBe('z')
    expect(sheetDeadAxis('baffle')).toBe('z')
  })

  it('names none for a body with three real dimensions', () => {
    expect(sheetDeadAxis('box')).toBeNull()
  })
})
