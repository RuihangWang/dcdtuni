/**
 * The bounding box of a non-rectangular floor, and what changing it means.
 *
 * A polygon floor's `geometry.size` is DERIVED — it is the polygon's bounding
 * box, kept in step by `syncFloorBaseFromPolygon` (STL_generate.html:3508).
 * Writing size directly, which is what the generic size boxes do, leaves the
 * box and the vertices describing different shapes: on `case_04_polygon` and
 * `case_05_triangle` the floor keeps its outline while its reported width
 * changes, and the exporter believes the box.
 *
 * So the width box on a polygon floor is a RESCALE: every vertex is moved so
 * the outline spans the new width, anchored at the polygon's minimum corner so
 * the floor grows away from its origin rather than sliding sideways. Ported
 * from `updateFloorPolygonBBox` (STL_generate.html:28916).
 */

import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'

export type PolygonPoint = [number, number]

/** The floor's outline, or null when it is a plain rectangle. */
export function floorPolygon(model: Construction | null): PolygonPoint[] | null {
  if (!model || model.kind !== 'floor') return null
  const polygon = (model.geometry as { polygon?: unknown } | undefined)?.polygon
  if (!Array.isArray(polygon) || polygon.length < 3) return null
  return polygon as PolygonPoint[]
}

export interface PolygonBBox {
  minX: number
  maxX: number
  minY: number
  maxY: number
  width: number
  depth: number
}

export function polygonBBox(polygon: PolygonPoint[]): PolygonBBox {
  const xs = polygon.map((p) => p[0])
  const ys = polygon.map((p) => p[1])
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  return { minX, maxX, minY, maxY, width: maxX - minX, depth: maxY - minY }
}

/**
 * The shoelace area of the outline.
 *
 * Absolute, so a clockwise polygon reports the same area as the same shape
 * wound anticlockwise — the winding is an export concern, not a measurement.
 */
export function polygonArea(polygon: PolygonPoint[]): number {
  if (polygon.length < 3) return 0
  let area = 0
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    area += polygon[j]![0] * polygon[i]![1] - polygon[i]![0] * polygon[j]![1]
  }
  return Math.abs(area) / 2
}

/**
 * Scale the outline so its bounding box spans `next` along one axis.
 *
 * Returns null when the request cannot be honoured — a non-positive target, or
 * a degenerate outline with no extent to scale from. Null rather than a
 * zero-scaled polygon: collapsing a floor to a line is not what "type a width"
 * asked for.
 */
export function rescalePolygon(
  polygon: PolygonPoint[],
  axis: 'W' | 'D',
  next: number,
): PolygonPoint[] | null {
  if (!Number.isFinite(next) || next <= 0) return null
  const box = polygonBBox(polygon)
  const current = axis === 'W' ? box.width : box.depth
  if (!Number.isFinite(current) || current <= 1e-9) return null
  const scale = next / current
  return polygon.map((p) =>
    axis === 'W'
      ? ([box.minX + (p[0] - box.minX) * scale, p[1]] as PolygonPoint)
      : ([p[0], box.minY + (p[1] - box.minY) * scale] as PolygonPoint),
  )
}

/**
 * Set a polygon floor's bounding-box width or depth, rescaling the outline.
 *
 * `geometry.size` is rewritten from the NEW polygon rather than from the typed
 * number, so the two can never drift: the box is always the outline's extent,
 * even if floating point puts the last vertex a nanometre short.
 */
export function updateFloorPolygonBBox(uid: string, axis: 'W' | 'D', metres: number) {
  const project = store.getProject()
  const model = (project ? store.findConstruction(project, uid)?.model : null) ?? null
  if (!floorPolygon(model)) return
  store.edit('updateFloorPolygonBBox', `${uid} ${axis}=${metres}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    const polygon = floorPolygon(target ?? null)
    if (!target || !polygon) return
    const scaled = rescalePolygon(polygon, axis, metres)
    if (!scaled) return
    const geometry = target.geometry as { polygon?: unknown; size?: Record<string, number> }
    geometry.polygon = scaled
    const box = polygonBBox(scaled)
    const size = (geometry.size ?? (geometry.size = {})) as Record<string, number>
    size.x = box.width
    size.z = box.depth
  })
}
