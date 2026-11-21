/**
 * Snapping and box-selection for the 2D floor plan.
 *
 * Ported from `room_2d_editor.js:1214` (`_applySnap`), `:1668-1725` (the model
 * drag's own vertex/edge snap) and `:1734-1760` (box select). Pure arithmetic,
 * so it is tested rather than clicked at.
 *
 * The three snap modes are MUTUALLY EXCLUSIVE in the toolbar (`toggleGridSnap`
 * and friends, :2103-2130) but not here: this applies whatever it is handed, so
 * a caller that turns two on gets both, and the exclusion stays a UI rule in one
 * place instead of being enforced twice with two chances to disagree.
 *
 * Every tolerance is in PIXELS divided by the view scale, which is what makes a
 * snap feel the same at every zoom. Passing metres instead would make snapping
 * either unusable when zoomed out or impossible to escape when zoomed in.
 */

import type { Vertex2 } from './room2dView'

export interface SnapModes {
  grid: boolean
  vertex: boolean
  edge: boolean
}

/** A model's plan box as the snapper sees it: the RAW anchor + size, unrotated. */
export interface SnapBox {
  uid: string
  x: number
  y: number
  w: number
  d: number
}

export interface SnapContext {
  modes: SnapModes
  gridSpacing: number
  /** px per metre — the tolerances are pixel distances divided by this. */
  scale: number
  boxes: SnapBox[]
  vertices: Vertex2[]
  /** The vertex being dragged, which must not snap to itself. -1 when none. */
  dragVertex?: number
  shiftHeld?: boolean
  /** Draw mode only: the shift/edge constraint runs against the last placed vertex. */
  constrainFrom?: Vertex2 | null
}

export const VERTEX_SNAP_PX = 10
export const EDGE_SNAP_PX = 8
export const COLLINEAR_PX = 5

/** The four plan corners of a snap box. */
export function boxCorners(b: SnapBox): Vertex2[] {
  return [
    [b.x, b.y],
    [b.x + b.w, b.y],
    [b.x + b.w, b.y + b.d],
    [b.x, b.y + b.d],
  ]
}

export function snapToGrid(value: number, spacing: number): number {
  if (!(spacing > 0)) return value
  return Math.round(value / spacing) * spacing
}

/**
 * Snap a plan point.
 *
 * Order is the legacy's and it matters: grid first, then vertex proximity (which
 * REPLACES the point outright), then edge alignment (which moves one axis at a
 * time), then the shift/collinear constraint last so it always wins.
 */
export function applySnap(wx: number, wy: number, ctx: SnapContext): Vertex2 {
  let x = wx
  let y = wy
  const { modes, scale } = ctx

  if (modes.grid) {
    x = snapToGrid(x, ctx.gridSpacing)
    y = snapToGrid(y, ctx.gridSpacing)
  }

  if (modes.vertex) {
    let best = VERTEX_SNAP_PX / scale
    ctx.vertices.forEach((v, i) => {
      if (i === ctx.dragVertex) return
      const d = Math.hypot(x - v[0], y - v[1])
      if (d < best) {
        best = d
        x = v[0]
        y = v[1]
      }
    })
    for (const b of ctx.boxes) {
      for (const [cx, cy] of boxCorners(b)) {
        const d = Math.hypot(x - cx, y - cy)
        if (d < best) {
          best = d
          x = cx
          y = cy
        }
      }
    }
  }

  if (modes.edge) {
    const tol = EDGE_SNAP_PX / scale
    for (const b of ctx.boxes) {
      for (const ex of [b.x, b.x + b.w]) if (Math.abs(x - ex) < tol) x = ex
      for (const ey of [b.y, b.y + b.d]) if (Math.abs(y - ey) < tol) y = ey
    }
  }

  const prev = ctx.constrainFrom
  if (prev) {
    if (ctx.shiftHeld) {
      // Shift locks the new edge to horizontal or vertical, whichever the
      // cursor is already closer to.
      const dx = Math.abs(x - prev[0])
      const dy = Math.abs(y - prev[1])
      if (dx >= dy) y = prev[1]
      else x = prev[0]
    } else if (modes.edge) {
      const tol = COLLINEAR_PX / scale
      const dx = Math.abs(x - prev[0])
      const dy = Math.abs(y - prev[1])
      if (dx < tol && dy > tol) x = prev[0]
      if (dy < tol && dx > tol) y = prev[1]
    }
  }

  return [x, y]
}

export interface ModelDragContext {
  modes: SnapModes
  gridSpacing: number
  scale: number
  /** Every OTHER model's box; the dragged one must not snap to itself. */
  boxes: SnapBox[]
  /** The dragged model's plan footprint size. */
  size: { w: number; d: number }
}

/**
 * Snap a dragged model's anchor.
 *
 * Different from `applySnap` in the two ways that matter: vertex snapping
 * matches the model's OWN four corners against every other model's, and edge
 * alignment picks the single closest candidate per axis instead of letting the
 * last model in the list win.
 */
export function snapModelAnchor(nx: number, ny: number, ctx: ModelDragContext): Vertex2 {
  let x = nx
  let y = ny
  const { modes, scale } = ctx

  if (modes.grid) {
    x = snapToGrid(x, ctx.gridSpacing)
    y = snapToGrid(y, ctx.gridSpacing)
  }

  if (modes.vertex) {
    const mine = boxCorners({ uid: '', x, y, w: ctx.size.w, d: ctx.size.d })
    const radius = VERTEX_SNAP_PX / scale
    let best = radius
    let bestDx = 0
    let bestDy = 0
    for (const other of ctx.boxes) {
      for (const [ox, oy] of boxCorners(other)) {
        for (const [mx, my] of mine) {
          const d = Math.hypot(mx - ox, my - oy)
          if (d < best) {
            best = d
            bestDx = ox - mx
            bestDy = oy - my
          }
        }
      }
    }
    if (best < radius) {
      x += bestDx
      y += bestDy
    }
  }

  if (modes.edge) {
    const tol = EDGE_SNAP_PX / scale
    let bestX: number | null = null
    let bestY: number | null = null
    let distX = tol
    let distY = tol
    const mineX = [x, x + ctx.size.w]
    const mineY = [y, y + ctx.size.d]
    for (const other of ctx.boxes) {
      for (const oex of [other.x, other.x + other.w]) {
        for (const mex of mineX) {
          const d = Math.abs(mex - oex)
          if (d < distX) {
            distX = d
            bestX = oex - mex
          }
        }
      }
      for (const oey of [other.y, other.y + other.d]) {
        for (const mey of mineY) {
          const d = Math.abs(mey - oey)
          if (d < distY) {
            distY = d
            bestY = oey - mey
          }
        }
      }
    }
    if (bestX !== null) x += bestX
    if (bestY !== null) y += bestY
  }

  return [x, y]
}

export interface BoxSelection {
  vertices: number[]
  edges: number[]
}

/**
 * Which vertices and edges a rubber band caught.
 *
 * An edge counts only when BOTH its endpoints are inside — a band that merely
 * crosses an edge is selecting the vertices at its ends, not the wall. Edges
 * are only offered on a closed ring, since an open chain's last "edge" does not
 * exist.
 */
export function boxSelect(
  vertices: Vertex2[],
  closed: boolean,
  toCanvas: (x: number, y: number) => { x: number; y: number },
  a: { x: number; y: number },
  b: { x: number; y: number },
): BoxSelection {
  const x1 = Math.min(a.x, b.x)
  const x2 = Math.max(a.x, b.x)
  const y1 = Math.min(a.y, b.y)
  const y2 = Math.max(a.y, b.y)
  const inside = (p: { x: number; y: number }) => p.x >= x1 && p.x <= x2 && p.y >= y1 && p.y <= y2

  const picked: number[] = []
  const screen = vertices.map((v) => toCanvas(v[0], v[1]))
  screen.forEach((p, i) => {
    if (inside(p)) picked.push(i)
  })

  const edges: number[] = []
  if (closed) {
    for (let i = 0; i < vertices.length; i += 1) {
      const j = (i + 1) % vertices.length
      if (inside(screen[i]!) && inside(screen[j]!)) edges.push(i)
    }
  }
  return { vertices: picked, edges }
}

/**
 * Delete an edge by re-rolling the ring so the gap falls at the ends.
 *
 * Both endpoints survive — the ring is opened, not shortened. The legacy
 * rotates the array so the break lands between the last and first vertex
 * (:1897), which is the only place an open chain can carry a gap.
 */
export function breakEdge(vertices: Vertex2[], edge: number): Vertex2[] {
  if (edge < 0 || edge >= vertices.length) return vertices
  const out: Vertex2[] = []
  for (let k = 0; k < vertices.length; k += 1) {
    out.push(vertices[(edge + 1 + k) % vertices.length]!)
  }
  return out
}

/** Remove a set of vertices. Highest index first, so the earlier ones keep theirs. */
export function deleteVertices(vertices: Vertex2[], indices: Iterable<number>): Vertex2[] {
  const drop = new Set(indices)
  const kept = vertices.filter((_, i) => !drop.has(i))
  // Under two vertices there is no chain left to edit, and the legacy empties
  // the list rather than leaving one orphan point behind.
  return kept.length < 2 ? [] : kept
}
