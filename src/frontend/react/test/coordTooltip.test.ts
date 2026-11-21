import { describe, expect, it } from 'vitest'
import {
  COORD_TOOLTIP_OFFSET,
  coordDisplay,
  coordPlacement,
  nextCoordField,
  parseCoordEntry,
} from '../src/room2d/coordTooltip'
import type { Point, Vertex2 } from '../src/room2d/room2dView'

/** 40 px per metre with the origin at the canvas corner, enough to test the close test. */
const toCanvas = (x: number, y: number): Point => ({ x: x * 40, y: y * 40 })

describe('coordDisplay', () => {
  it('follows the pointer to one decimal, in the project unit', () => {
    expect(coordDisplay([1.234, 5.678], 'm')).toEqual({ x: '1.2', y: '5.7', z: '0.0' })
  })

  it('converts to millimetres when the project is authored in them', () => {
    expect(coordDisplay([1.234, 0], 'mm').x).toBe('1234.0')
  })
})

describe('parseCoordEntry', () => {
  it('reads a three-decimal entry back as metres', () => {
    expect(parseCoordEntry('12.345', '6.789', 'm')).toEqual([12.345, 6.789])
  })

  it('converts from the display unit', () => {
    const p = parseCoordEntry('1234', '500', 'mm')
    expect(p![0]).toBeCloseTo(1.234, 10)
    expect(p![1]).toBeCloseTo(0.5, 10)
  })

  it('refuses a blank or non-numeric box rather than placing a vertex on the axis', () => {
    expect(parseCoordEntry('', '2', 'm')).toBeNull()
    expect(parseCoordEntry('2', 'abc', 'm')).toBeNull()
  })
})

describe('coordPlacement', () => {
  const ring: Vertex2[] = [
    [0, 0],
    [4, 0],
    [4, 3],
  ]

  it('appends a point away from the start', () => {
    expect(coordPlacement(ring, [0, 3], toCanvas, 15)).toEqual({ kind: 'append', at: [0, 3] })
  })

  it('closes the ring when the typed point lands on the first vertex', () => {
    expect(coordPlacement(ring, [0.1, 0.1], toCanvas, 15)).toEqual({ kind: 'close' })
  })

  it('cannot close with fewer than three vertices', () => {
    expect(coordPlacement([[0, 0]], [0, 0], toCanvas, 15)).toEqual({
      kind: 'append',
      at: [0, 0],
    })
  })
})

describe('nextCoordField', () => {
  it('cycles X and Y, skipping the read-only Z', () => {
    expect(nextCoordField('x')).toBe('y')
    expect(nextCoordField('y')).toBe('x')
  })
})

describe('COORD_TOOLTIP_OFFSET', () => {
  it('sits clear of the cursor cross-hair', () => {
    expect(COORD_TOOLTIP_OFFSET).toBe(20)
  })
})
