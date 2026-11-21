/**
 * The 2D floor plan's view transform.
 *
 * Ported from `room_2d_editor.js:104-127` (`_rotatePoint`, `worldToCanvas`,
 * `canvasToWorld`) and `:2043` (`zoomToFit`). Kept in its own module because
 * every other part of the editor — the grid, the hit tests, the snapping, the
 * PNG export — has to agree with these four functions to the pixel, and the
 * only way to be sure of that is to test them on their own.
 *
 * Two conventions carried over from the legacy and easy to get backwards:
 *
 * - **Plan Y is world Z.** Since the 2026-04-18 Y-up change a model at world
 *   (x, y, z) draws at plan (x, z); `y` is the height and never reaches this
 *   file.
 * - **Canvas Y grows DOWNWARD**, so `worldToCanvas` subtracts. A rotation that
 *   looks counter-clockwise in world coordinates therefore appears clockwise on
 *   the canvas, which is why the rotate handle is placed with `+sin` rather
 *   than `-sin` (`room2dModels.rotateRing`).
 */

export interface View {
  offsetX: number
  offsetY: number
  scale: number
}

export interface Point {
  x: number
  y: number
}

export type Vertex2 = [number, number]

/** The wheel zoom's clamps, in pixels per metre. Legacy `_onWheel` (:1855). */
export const MIN_SCALE = 2
export const MAX_SCALE = 2000

export function clampScale(scale: number): number {
  return Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale))
}

/** Rotate a plan point about the origin. `deg` is the VIEW rotation, 0/90/180/270. */
export function rotatePoint(wx: number, wy: number, deg: number): Point {
  if (deg === 0) return { x: wx, y: wy }
  const rad = (deg * Math.PI) / 180
  const c = Math.cos(rad)
  const s = Math.sin(rad)
  return { x: c * wx - s * wy, y: s * wx + c * wy }
}

export function unrotatePoint(rx: number, ry: number, deg: number): Point {
  return rotatePoint(rx, ry, -deg)
}

export function worldToCanvas(
  view: View,
  rotation: number,
  width: number,
  height: number,
  wx: number,
  wy: number,
): Point {
  const r = rotatePoint(wx, wy, rotation)
  return {
    x: (r.x - view.offsetX) * view.scale + width / 2,
    y: height / 2 - (r.y - view.offsetY) * view.scale,
  }
}

export function canvasToWorld(
  view: View,
  rotation: number,
  width: number,
  height: number,
  cx: number,
  cy: number,
): Point {
  const rx = (cx - width / 2) / view.scale + view.offsetX
  const ry = (height / 2 - cy) / view.scale + view.offsetY
  return unrotatePoint(rx, ry, rotation)
}

/**
 * Frame a set of plan points.
 *
 * The legacy fits to the ROTATED points, so turning the view 90° re-fits rather
 * than leaving the room half off-screen, and it floors the scale at 1 px/m — a
 * degenerate span would otherwise divide by ~0 and put the whole room in one
 * pixel. Returns null when there is nothing to frame, so the caller keeps its
 * current view instead of jumping to the origin.
 */
export function fitView(
  points: Vertex2[],
  rotation: number,
  width: number,
  height: number,
): View | null {
  if (points.length === 0 || width <= 0 || height <= 0) return null
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  for (const [px, py] of points) {
    const r = rotatePoint(px, py, rotation)
    if (r.x < minX) minX = r.x
    if (r.x > maxX) maxX = r.x
    if (r.y < minY) minY = r.y
    if (r.y > maxY) maxY = r.y
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null
  const spanX = maxX - minX
  const spanY = maxY - minY
  const margin = Math.max(spanX, spanY) * 0.05
  let scale = Math.min(width / (spanX + margin), height / (spanY + margin))
  if (!(scale > 1)) scale = 1
  // A single point, or a set of coincident ones, gives a zero span and an
  // INFINITE scale — every drawn coordinate then comes out non-finite and the
  // plan renders empty. The ceiling is the wheel zoom's own upper clamp, so a
  // degenerate fit lands somewhere the user could have scrolled to anyway.
  if (scale > MAX_SCALE) scale = MAX_SCALE
  return { offsetX: (minX + maxX) / 2, offsetY: (minY + maxY) / 2, scale }
}

/** Distance from a point to a segment, all in canvas pixels. */
export function pointToSegmentDist(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): number {
  const dx = bx - ax
  const dy = by - ay
  const lenSq = dx * dx + dy * dy
  if (lenSq < 1e-10) return Math.hypot(px - ax, py - ay)
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
}

/**
 * How many metres the annotation scale bar should span.
 *
 * The legacy ladder (`room_2d_editor.js:1197`): start at 5 m, grow while the bar
 * would be under 50 px, then shrink while it would be over 400 px. Order
 * matters — the grow steps run first, so a very zoomed-in view lands on 1 m and
 * a very zoomed-out one on 20 m.
 */
export function scaleBarMetres(scale: number): number {
  let metres = 5
  if (scale * metres < 50) metres = 10
  if (scale * metres < 50) metres = 20
  if (scale * metres > 400) metres = 2
  if (scale * metres > 400) metres = 1
  return metres
}

/** Total edge length of a closed ring. */
export function perimeter(vertices: Vertex2[]): number {
  let sum = 0
  for (let i = 0; i < vertices.length; i += 1) {
    const a = vertices[i]!
    const b = vertices[(i + 1) % vertices.length]!
    sum += Math.hypot(b[0] - a[0], b[1] - a[1])
  }
  return sum
}

/** The angle of edge `i` in degrees, measured CCW from plan +X. */
export function edgeAngleDeg(vertices: Vertex2[], index: number): number {
  const a = vertices[index]!
  const b = vertices[(index + 1) % vertices.length]!
  return (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI
}
