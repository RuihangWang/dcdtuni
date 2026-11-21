import { describe, expect, it } from 'vitest'
import {
  constrainSeed,
  FACE_DEFS,
  formatBounds,
  gridVertices,
  moveBox,
  pickSeedColor,
  planGrid,
  resizeBox,
  type Bounds3,
} from '../src/run/meshVisuals'

const box: Bounds3 = { min: { x: 0, y: 0, z: 0 }, max: { x: 2, y: 1, z: 4 } }

describe('planGrid', () => {
  it('divides the block into whole cells and recomputes the spacing', () => {
    const plan = planGrid(box, 0.3)
    // 2 / 0.3 = 6.67 -> 7 cells, so the spacing is 2/7, NOT 0.3. Using 0.3 as
    // the spacing would leave the last line 0.1 m short of the far face.
    expect(plan.nx).toBe(7)
    expect(plan.sx).toBeCloseTo(2 / 7, 12)
    expect(plan.nx * plan.sx).toBeCloseTo(2, 12)
  })

  it('never returns zero cells for a thin block', () => {
    const thin: Bounds3 = { min: { x: 0, y: 0, z: 0 }, max: { x: 0.01, y: 1, z: 1 } }
    expect(planGrid(thin, 1).nx).toBe(1)
  })

  it('floors the base size at 1 cm, so a zero cannot divide', () => {
    expect(Number.isFinite(planGrid(box, 0).lines)).toBe(true)
  })

  it('counts the lines of all three families', () => {
    const plan = planGrid(box, 1)
    // 2x1x4 at 1 m: 2,1,4 cells -> (3*2)+(2*5)+(3*5)
    expect(plan.lines).toBe((plan.nx + 1) * (plan.ny + 1) + (plan.ny + 1) * (plan.nz + 1) + (plan.nx + 1) * (plan.nz + 1))
  })
})

describe('gridVertices', () => {
  it('emits two endpoints per line', () => {
    const plan = planGrid(box, 1)
    expect(gridVertices(box, plan).length).toBe(plan.lines * 6)
  })

  it('spans the block exactly', () => {
    const plan = planGrid(box, 1)
    const v = gridVertices(box, plan)
    const xs = v.filter((_, i) => i % 3 === 0)
    expect(Math.min(...xs)).toBeCloseTo(box.min.x, 12)
    expect(Math.max(...xs)).toBeCloseTo(box.max.x, 12)
  })
})

describe('resizeBox', () => {
  it('moves the face the drag grabbed', () => {
    const out = resizeBox(box, 1, 0.5) // +X
    expect(out.max.x).toBeCloseTo(2.5, 12)
    expect(out.min.x).toBe(0)
  })

  it('stops at a 1 cm box instead of inverting', () => {
    const out = resizeBox(box, 0, 5) // -X dragged far past +X
    expect(out.min.x).toBeCloseTo(1.99, 12)
    expect(out.max.x - out.min.x).toBeGreaterThan(0)
  })

  it('leaves the other two axes alone', () => {
    const out = resizeBox(box, 3, -0.4) // +Y
    expect(out.min).toEqual(box.min)
    expect(out.max.x).toBe(box.max.x)
    expect(out.max.z).toBe(box.max.z)
  })

  it('has one entry per face, min then max per axis', () => {
    expect(FACE_DEFS.map((f) => `${f.axis}${f.isMin ? '-' : '+'}`)).toEqual([
      'x-', 'x+', 'y-', 'y+', 'z-', 'z+',
    ])
  })
})

describe('moveBox', () => {
  it('translates both corners, keeping the size', () => {
    const out = moveBox(box, { x: 1, y: -2, z: 0.5 })
    expect(out.min).toEqual({ x: 1, y: -2, z: 0.5 })
    expect(out.max.x - out.min.x).toBeCloseTo(2, 12)
  })
})

describe('constrainSeed', () => {
  const start = { x: 1, y: 2, z: 3 }
  const target = { x: 9.876, y: 8.765, z: 7.654 }

  it('rounds a free drag to a centimetre', () => {
    expect(constrainSeed(start, target, { x: true, y: true, z: true })).toEqual({
      x: 9.88,
      y: 8.77,
      z: 7.65,
    })
  })

  it('keeps a locked axis at its value from the drag START', () => {
    // Not the current position: reading "current" would let a locked axis creep
    // by whatever it had already moved before the box was unchecked.
    expect(constrainSeed(start, target, { x: false, y: true, z: false })).toEqual({
      x: 1,
      y: 8.77,
      z: 3,
    })
  })
})

describe('pickSeedColor', () => {
  it('avoids a colour already in use', () => {
    const first = pickSeedColor([])
    const second = pickSeedColor([{ id: 1, x: 0, y: 0, z: 0, color: first }])
    expect(second).not.toBe(first)
  })
})

describe('formatBounds', () => {
  it('prints four decimals per axis', () => {
    expect(formatBounds(box)).toBe('X: [0.0000, 2.0000]  Y: [0.0000, 1.0000]  Z: [0.0000, 4.0000]')
  })
})
