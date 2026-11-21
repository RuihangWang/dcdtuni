/**
 * What it means to MOVE a model — the one rule both move paths share.
 *
 * `geometry.location` is not always where a model is drawn. A pipe segment IS
 * its `geometry.waypoints`: the renderer sweeps a tube along those points and
 * never reads the anchor, and the segment's valves carry their own world
 * positions on top of that. Translating such a model by its anchor alone leaves
 * it drawn exactly where it was.
 *
 * That is not hypothetical — it is the defect this module exists to close. The
 * legacy editor moves the waypoints and the valves in BOTH of its group-move
 * paths (`_onMoveGizmoChange`, STL_generate.html:15902, and
 * `setMultiMoveMinPoint`, :10950), while the port did it only in the typed
 * minimum-point box. The same gesture therefore worked one way and not the
 * other: type a number and the pipes follow, drag the gizmo and they stay
 * behind. Both callers now go through here, so there is only ever one answer to
 * "what does moving this model mean".
 */

import type { Construction } from '../schema/cfdproj'

export interface Vec3 {
  x: number
  y: number
  z: number
}

/**
 * Where a co-moved point lands, given the drag's delta.
 *
 * Measured from the point's OWN start, which is the whole of the rigid-move
 * rule: every point shifts by the SAME delta from where it began, so the group
 * keeps its shape.
 */
export function followDelta(start: Vec3, delta: Vec3): Vec3 {
  return { x: start.x + delta.x, y: start.y + delta.y, z: start.z + delta.z }
}

/**
 * Only a pipe segment's waypoints move with it.
 *
 * The legacy gates on `kind === 'pipe_segment'` in both paths, and `kind` is
 * restored after every project clone (`reviveKinds`), so it is safe to read
 * here even on a freshly cloned draft.
 */
function isPipeSegment(model: Construction): boolean {
  return String((model as { kind?: unknown }).kind ?? '') === 'pipe_segment'
}

/**
 * The LIVE point objects that together are a model's placement.
 *
 * Live, not copies: the callers mutate what they get back. The order is stable
 * for a given model — anchor, then waypoints, then valve positions — which is
 * what lets a drag snapshot the array once and index into it every frame.
 */
export function placementPoints(model: Construction | null | undefined): Vec3[] {
  if (!model) return []
  const geometry = model.geometry as
    | { location?: Vec3; waypoints?: (Vec3 | null | undefined)[] }
    | undefined
  const points: Vec3[] = []
  if (geometry?.location) points.push(geometry.location)
  if (!isPipeSegment(model)) return points
  for (const waypoint of geometry?.waypoints ?? []) if (waypoint) points.push(waypoint)
  const valves = (model as unknown as { cooling?: { valves?: { position?: Vec3 }[] } }).cooling
    ?.valves
  for (const valve of valves ?? []) if (valve?.position) points.push(valve.position)
  return points
}

/** A copy of those points, for a drag to measure its delta from. */
export function capturePlacement(model: Construction | null | undefined): Vec3[] {
  return placementPoints(model).map((p) => ({ x: p.x, y: p.y, z: p.z }))
}

/**
 * Put every point back at `start + delta`.
 *
 * From the START rather than from where the point is now, because that is what
 * keeps a group rigid: reading the current position and adding the frame's
 * delta would compound it sixty times a second and the group would fly apart.
 *
 * Points with no entry in `start` are left alone — the same guard the legacy
 * has (`if (wpStart[i])`), for a model whose waypoint list changed under the
 * drag.
 */
export function applyPlacement(
  model: Construction | null | undefined,
  start: readonly Vec3[],
  delta: Vec3,
): void {
  const points = placementPoints(model)
  for (let i = 0; i < points.length; i++) {
    const from = start[i]
    if (!from) continue
    const next = followDelta(from, delta)
    points[i]!.x = next.x
    points[i]!.y = next.y
    points[i]!.z = next.z
  }
}

/** Translate a model by `delta`, with no snapshot to measure from. */
export function translateModel(model: Construction | null | undefined, delta: Vec3): void {
  for (const point of placementPoints(model)) {
    point.x += delta.x
    point.y += delta.y
    point.z += delta.z
  }
}

/**
 * Whether a moving group holds anything the pipe renderer draws.
 *
 * Pipes are not built by the geometry builder — they live in their own group
 * and are redrawn from the project — so a drag that moved one has to ask for
 * that redraw. Asking on every drag would rebuild every tube in the room sixty
 * times a second for a group of racks.
 */
export function hasPipeGeometry(models: readonly (Construction | null | undefined)[]): boolean {
  return models.some((m) => !!m && isPipeSegment(m))
}
