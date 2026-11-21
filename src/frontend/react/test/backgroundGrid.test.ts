import { describe, expect, it } from 'vitest'
import {
  GRID_CENTER_COLOR,
  GRID_LINE_COLOR,
  GRID_MAX_DIVISIONS,
  GRID_OPACITY,
  GRID_RENDER_RANGE,
  GRID_Y_OFFSET,
  buildBackgroundGrid,
  clampGridSpacing,
  disposeBackgroundGrid,
  gridDivisions,
  gridExtent,
} from '../src/geometry/backgroundGrid'

describe('clampGridSpacing', () => {
  it('holds the legacy 10 mm - 1 m range', () => {
    expect(clampGridSpacing(0.001)).toBe(0.01)
    expect(clampGridSpacing(5)).toBe(1)
    expect(clampGridSpacing(0.6)).toBe(0.6)
  })

  it('falls back to 100 mm rather than dividing by zero', () => {
    expect(clampGridSpacing(0)).toBe(0.1)
    expect(clampGridSpacing(Number.NaN)).toBe(0.1)
  })
})

describe('gridDivisions', () => {
  it('is always even, so a line falls exactly on the origin', () => {
    for (const spacing of [0.01, 0.05, 0.1, 0.3, 0.6, 0.7, 1]) {
      expect(gridDivisions(spacing) % 2).toBe(0)
    }
  })

  it('caps the line count for the single draw call', () => {
    // Below the crossover the cap binds; above it the 2 km render range does.
    expect(gridDivisions(0.1)).toBe(GRID_MAX_DIVISIONS)
    expect(gridDivisions(0.6)).toBe(Math.ceil(GRID_RENDER_RANGE / 0.6 / 2) * 2)
  })
})

describe('gridExtent', () => {
  it('reaches past the largest room in the dataset', () => {
    // case_14 is 41.3 m across; the port's fixed 40 m grid stopped inside it.
    expect(gridExtent(0.6)).toBeGreaterThan(41.3)
    expect(gridExtent(0.1)).toBeGreaterThan(41.3)
  })

  it('is an exact multiple of the spacing, so the pitch is what was asked for', () => {
    const spacing = 0.6
    expect(gridExtent(spacing) / spacing).toBeCloseTo(gridDivisions(spacing), 9)
  })
})

describe('buildBackgroundGrid', () => {
  it('uses the legacy colours and opacity, not the washed-out port values', () => {
    const grid = buildBackgroundGrid(0.6)
    const material = Array.isArray(grid.material) ? grid.material[0] : grid.material
    expect(material.opacity).toBe(GRID_OPACITY)
    expect(material.transparent).toBe(true)
    // The old values were 0x2e2e42 at 0.25, a fifth of the measured contrast.
    const colors = grid.geometry.getAttribute('color')
    expect(colors).toBeTruthy()
    expect(GRID_LINE_COLOR).toBe(0x5a6b7d)
    expect(GRID_CENTER_COLOR).toBe(0x8ba3b8)
    disposeBackgroundGrid(grid)
  })

  it('never blocks or z-fights with a floor sheet at y=0', () => {
    const grid = buildBackgroundGrid(0.6)
    const material = Array.isArray(grid.material) ? grid.material[0] : grid.material
    expect(material.depthWrite).toBe(false)
    expect(material.depthTest).toBe(true)
    expect(grid.renderOrder).toBe(-1)
    expect(grid.position.y).toBe(GRID_Y_OFFSET)
    disposeBackgroundGrid(grid)
  })

  it('draws the requested number of lines', () => {
    const grid = buildBackgroundGrid(0.6)
    const divisions = gridDivisions(0.6)
    // Two line families, one segment each, two vertices per segment.
    expect(grid.geometry.getAttribute('position').count).toBe((divisions + 1) * 4)
    disposeBackgroundGrid(grid)
  })
})
