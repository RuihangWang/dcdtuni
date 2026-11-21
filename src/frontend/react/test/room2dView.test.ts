import { describe, expect, it } from 'vitest'
import {
  canvasToWorld,
  edgeAngleDeg,
  fitView,
  perimeter,
  pointToSegmentDist,
  rotatePoint,
  scaleBarMetres,
  MAX_SCALE,
  worldToCanvas,
  type Vertex2,
  type View,
} from '../src/room2d/room2dView'

const VIEW: View = { offsetX: 0, offsetY: 0, scale: 40 }

describe('rotatePoint', () => {
  it('leaves a point alone at 0 degrees', () => {
    expect(rotatePoint(3, 4, 0)).toEqual({ x: 3, y: 4 })
  })

  it('turns +X onto +Y at 90 degrees', () => {
    const p = rotatePoint(1, 0, 90)
    expect(p.x).toBeCloseTo(0, 12)
    expect(p.y).toBeCloseTo(1, 12)
  })

  it('is its own inverse over four quarter turns', () => {
    let p = { x: 2.5, y: -1.25 }
    for (let i = 0; i < 4; i += 1) p = rotatePoint(p.x, p.y, 90)
    expect(p.x).toBeCloseTo(2.5, 12)
    expect(p.y).toBeCloseTo(-1.25, 12)
  })
})

describe('worldToCanvas / canvasToWorld', () => {
  it('puts the view centre at the canvas centre', () => {
    expect(worldToCanvas(VIEW, 0, 800, 600, 0, 0)).toEqual({ x: 400, y: 300 })
  })

  it('flips plan Y, because canvas Y grows downward', () => {
    // A rack one metre further from the viewer draws HIGHER on screen. Getting
    // this backwards mirrors the whole plan.
    expect(worldToCanvas(VIEW, 0, 800, 600, 0, 1).y).toBe(300 - 40)
  })

  it('round-trips through canvasToWorld at every view rotation', () => {
    for (const rot of [0, 90, 180, 270]) {
      const view: View = { offsetX: 3, offsetY: -2, scale: 17.5 }
      const back = canvasToWorld(view, rot, 800, 600, 123, 456)
      const fwd = worldToCanvas(view, rot, 800, 600, back.x, back.y)
      expect(fwd.x).toBeCloseTo(123, 9)
      expect(fwd.y).toBeCloseTo(456, 9)
    }
  })
})

describe('fitView', () => {
  it('centres on the footprint and scales it to the canvas', () => {
    const room: Vertex2[] = [
      [0, 0],
      [10, 0],
      [10, 6],
      [0, 6],
    ]
    const fit = fitView(room, 0, 800, 600)!
    expect(fit.offsetX).toBeCloseTo(5, 12)
    expect(fit.offsetY).toBeCloseTo(3, 12)
    // 5% margin comes off the LARGER span, so the limiting axis is X: 800 / 10.5.
    expect(fit.scale).toBeCloseTo(800 / 10.5, 9)
  })

  it('re-fits against the rotated footprint', () => {
    // A hall that is wide in X becomes deep in Z at 90 degrees; fitting the
    // unrotated points would leave half of it off screen.
    const hall: Vertex2[] = [
      [0, 0],
      [30, 0],
      [30, 5],
      [0, 5],
    ]
    const flat = fitView(hall, 0, 800, 600)!
    const turned = fitView(hall, 90, 800, 600)!
    expect(turned.scale).not.toBeCloseTo(flat.scale, 3)
    expect(turned.offsetX).toBeCloseTo(-2.5, 12)
    expect(turned.offsetY).toBeCloseTo(15, 12)
  })

  it('caps a zero-span fit instead of returning an infinite scale', () => {
    // One point divides by a zero span. Left alone the scale is Infinity and
    // every drawn coordinate is non-finite, so the plan renders blank.
    const fit = fitView([[2, 2]], 0, 800, 600)!
    expect(Number.isFinite(fit.scale)).toBe(true)
    expect(fit.scale).toBe(MAX_SCALE)
    expect(fit.offsetX).toBe(2)
  })

  it('returns null with nothing to frame, so the caller keeps its view', () => {
    expect(fitView([], 0, 800, 600)).toBeNull()
  })
})

describe('pointToSegmentDist', () => {
  it('measures perpendicular distance inside the segment', () => {
    expect(pointToSegmentDist(5, 3, 0, 0, 10, 0)).toBeCloseTo(3, 12)
  })

  it('clamps to the endpoints outside it', () => {
    expect(pointToSegmentDist(-4, 3, 0, 0, 10, 0)).toBeCloseTo(5, 12)
  })

  it('handles a degenerate segment without dividing by zero', () => {
    expect(pointToSegmentDist(3, 4, 1, 1, 1, 1)).toBeCloseTo(Math.hypot(2, 3), 12)
  })
})

describe('scaleBarMetres', () => {
  it('grows the bar when it would be shorter than 50px', () => {
    expect(scaleBarMetres(4)).toBe(20)
    expect(scaleBarMetres(9)).toBe(10)
  })

  it('shrinks it when it would run past 400px', () => {
    expect(scaleBarMetres(100)).toBe(2)
    expect(scaleBarMetres(300)).toBe(1)
  })

  it('leaves 5m alone in the middle of the range', () => {
    expect(scaleBarMetres(40)).toBe(5)
  })
})

describe('perimeter and edgeAngleDeg', () => {
  const rect: Vertex2[] = [
    [0, 0],
    [10, 0],
    [10, 6],
    [0, 6],
  ]

  it('sums the closed ring, not the open chain', () => {
    expect(perimeter(rect)).toBeCloseTo(32, 12)
  })

  it('reports edge angles CCW from plan +X', () => {
    expect(edgeAngleDeg(rect, 0)).toBeCloseTo(0, 12)
    expect(edgeAngleDeg(rect, 1)).toBeCloseTo(90, 12)
    expect(edgeAngleDeg(rect, 3)).toBeCloseTo(-90, 12)
  })
})
