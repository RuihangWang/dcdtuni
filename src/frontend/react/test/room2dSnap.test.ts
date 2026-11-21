import { describe, expect, it } from 'vitest'
import {
  applySnap,
  boxSelect,
  breakEdge,
  deleteVertices,
  snapModelAnchor,
  snapToGrid,
  type SnapBox,
  type SnapContext,
  type SnapModes,
} from '../src/room2d/room2dSnap'
import { worldToCanvas, type Vertex2, type View } from '../src/room2d/room2dView'

const OFF: SnapModes = { grid: false, vertex: false, edge: false }
const SCALE = 40

function ctx(patch: Partial<SnapContext> = {}): SnapContext {
  return {
    modes: OFF,
    gridSpacing: 0.1,
    scale: SCALE,
    boxes: [],
    vertices: [],
    dragVertex: -1,
    ...patch,
  }
}

const RACK: SnapBox = { uid: 'r', x: 2, y: 3, w: 0.6, d: 1.2 }

describe('snapToGrid', () => {
  it('rounds to the nearest multiple', () => {
    expect(snapToGrid(1.24, 0.1)).toBeCloseTo(1.2, 12)
    expect(snapToGrid(-1.26, 0.1)).toBeCloseTo(-1.3, 12)
  })

  it('leaves the value alone rather than dividing by a zero spacing', () => {
    expect(snapToGrid(1.234, 0)).toBe(1.234)
  })
})

describe('applySnap', () => {
  it('does nothing with every mode off', () => {
    expect(applySnap(1.234, 5.678, ctx())).toEqual([1.234, 5.678])
  })

  it('snaps to the grid', () => {
    const [x, y] = applySnap(1.234, 5.678, ctx({ modes: { ...OFF, grid: true } }))
    expect(x).toBeCloseTo(1.2, 12)
    expect(y).toBeCloseTo(5.7, 12)
  })

  it('snaps to a model corner within 10px of the cursor', () => {
    // 10px at 40px/m is 0.25m. A point 0.1m away lands on the corner; one 0.4m
    // away is left alone.
    const near = applySnap(2.1, 3.0, ctx({ modes: { ...OFF, vertex: true }, boxes: [RACK] }))
    expect(near).toEqual([2, 3])
    const far = applySnap(2.35, 3.5, ctx({ modes: { ...OFF, vertex: true }, boxes: [RACK] }))
    expect(far).toEqual([2.35, 3.5])
  })

  it('never snaps a dragged vertex to itself', () => {
    const vertices: Vertex2[] = [
      [0, 0],
      [4, 0],
    ]
    const moved = applySnap(
      4.02,
      0.02,
      ctx({ modes: { ...OFF, vertex: true }, vertices, dragVertex: 1 }),
    )
    expect(moved).toEqual([4.02, 0.02])
  })

  it('aligns one axis at a time to a model edge', () => {
    // Edge align must not drag the other axis with it: a vertex placed level
    // with a rack's left face keeps whatever depth it was given.
    const [x, y] = applySnap(2.05, 9, ctx({ modes: { ...OFF, edge: true }, boxes: [RACK] }))
    expect(x).toBe(2)
    expect(y).toBe(9)
  })

  it('locks the new edge horizontal under shift', () => {
    const [x, y] = applySnap(
      5,
      0.3,
      ctx({ shiftHeld: true, constrainFrom: [0, 0] }),
    )
    expect(x).toBe(5)
    expect(y).toBe(0)
  })

  it('locks it vertical under shift when the cursor is mostly vertical', () => {
    const [x, y] = applySnap(0.3, 5, ctx({ shiftHeld: true, constrainFrom: [0, 0] }))
    expect(x).toBe(0)
    expect(y).toBe(5)
  })

  it('applies the shift constraint AFTER the grid, so the lock always wins', () => {
    const [, y] = applySnap(
      5,
      0.34,
      ctx({ modes: { ...OFF, grid: true }, shiftHeld: true, constrainFrom: [0, 0] }),
    )
    expect(y).toBe(0)
  })
})

describe('snapModelAnchor', () => {
  const other: SnapBox = { uid: 'other', x: 5, y: 0, w: 0.6, d: 1.2 }
  const size = { w: 0.6, d: 1.2 }

  it('brings the dragged model corner-to-corner', () => {
    // Dropping a rack 0.05m short of the neighbour's left face lands it exactly
    // there — corner to corner, not overlapping by a fraction.
    const [x, y] = snapModelAnchor(4.35, 0.05, {
      modes: { ...OFF, vertex: true },
      gridSpacing: 0.1,
      scale: SCALE,
      boxes: [other],
      size,
    })
    expect(x).toBeCloseTo(4.4, 9)
    expect(y).toBeCloseTo(0, 9)
  })

  it('aligns faces without pulling the other axis', () => {
    const [x, y] = snapModelAnchor(5.05, 7, {
      modes: { ...OFF, edge: true },
      gridSpacing: 0.1,
      scale: SCALE,
      boxes: [other],
      size,
    })
    expect(x).toBeCloseTo(5, 9)
    expect(y).toBe(7)
  })

  it('picks the CLOSEST edge, not the last one in the list', () => {
    // Two candidates within tolerance: the far one must not win just because it
    // is later in the list.
    const boxes: SnapBox[] = [
      { uid: 'far', x: 5.15, y: 0, w: 1, d: 1 },
      { uid: 'near', x: 5.02, y: 0, w: 1, d: 1 },
      { uid: 'farther', x: 5.16, y: 0, w: 1, d: 1 },
    ]
    const [x] = snapModelAnchor(5, 20, {
      modes: { ...OFF, edge: true },
      gridSpacing: 0.1,
      scale: SCALE,
      boxes,
      size,
    })
    expect(x).toBeCloseTo(5.02, 9)
  })

  it('leaves the anchor alone with nothing in range', () => {
    const [x, y] = snapModelAnchor(1, 9, {
      modes: { ...OFF, vertex: true, edge: true },
      gridSpacing: 0.1,
      scale: SCALE,
      boxes: [other],
      size,
    })
    expect([x, y]).toEqual([1, 9])
  })
})

describe('boxSelect', () => {
  const view: View = { offsetX: 0, offsetY: 0, scale: 40 }
  const toCanvas = (x: number, y: number) => worldToCanvas(view, 0, 800, 600, x, y)
  const room: Vertex2[] = [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ]

  it('catches the vertices inside the band', () => {
    const a = toCanvas(-0.5, -0.5)
    const b = toCanvas(4.5, 0.5)
    const picked = boxSelect(room, true, toCanvas, a, b)
    expect(picked.vertices).toEqual([0, 1])
  })

  it('claims an edge only when BOTH ends are inside', () => {
    const a = toCanvas(-0.5, -0.5)
    const b = toCanvas(4.5, 0.5)
    expect(boxSelect(room, true, toCanvas, a, b).edges).toEqual([0])
    const half = boxSelect(room, true, toCanvas, a, toCanvas(2, 0.5))
    expect(half.edges).toEqual([])
  })

  it('offers no edges on an open chain, where the closing edge does not exist', () => {
    const a = toCanvas(-1, -1)
    const b = toCanvas(5, 4)
    expect(boxSelect(room, false, toCanvas, a, b).edges).toEqual([])
    expect(boxSelect(room, false, toCanvas, a, b).vertices).toEqual([0, 1, 2, 3])
  })
})

describe('breakEdge', () => {
  const room: Vertex2[] = [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ]

  it('keeps every vertex and moves the gap to the ends', () => {
    // Deleting edge 1 (from vertex 2 to vertex 3) must leave both of them; the
    // ring is opened, not shortened.
    const opened = breakEdge(room, 1)
    expect(opened).toHaveLength(4)
    expect(opened[0]).toEqual([4, 3])
    expect(opened[3]).toEqual([4, 0])
  })

  it('leaves the ring alone for an index that is not an edge', () => {
    expect(breakEdge(room, 9)).toBe(room)
  })
})

describe('deleteVertices', () => {
  const room: Vertex2[] = [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ]

  it('removes several at once without index drift', () => {
    expect(deleteVertices(room, [0, 2])).toEqual([
      [4, 0],
      [0, 3],
    ])
  })

  it('empties the chain rather than leaving one orphan point', () => {
    expect(deleteVertices(room, [0, 1, 2])).toEqual([])
  })
})
