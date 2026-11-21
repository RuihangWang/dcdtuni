/**
 * Where a duplicated model lands.
 *
 * A flat "one width to the right" offset is fine in an empty room and wrong in
 * a real one: in `case_10_dense_A` the racks stand a few centimetres apart, so
 * the copy appears INSIDE its neighbour, and a copy made at the room edge is
 * kept outside the meshing domain instead of being refused.
 *
 * The legacy searches for a free spot instead — ten directions, twelve rings,
 * stepping by the model's own size plus 8% (`placeDuplicatedModelWithoutOverlap`,
 * STL_generate.html:5511) — and then tests the result against the project bounds
 * (`window.duplicateModel`, :13845). If nothing fits it REMOVES the copy, pops
 * the undo checkpoint and says so, rather than leaving a model outside the
 * domain.
 *
 * Kept as pure arithmetic over axis-aligned boxes. The legacy asks the SCENE for
 * each model's world bounds, which a rotated model makes slightly larger than
 * this; the difference only ever makes this search more willing to place a copy
 * where the legacy would step once further, and the bounds test that follows is
 * the same one either way.
 */

import { withinProjectBounds, type ProjectBounds } from './faceDragMath'

export interface Box {
  location: { x: number; y: number; z: number }
  size: { x: number; y: number; z: number }
}

/** The ten directions the legacy tries, in its order — X and Y first, Z last. */
export const SEARCH_DIRECTIONS = [
  { x: 1, y: 0, z: 0 },
  { x: 0, y: 1, z: 0 },
  { x: 1, y: 1, z: 0 },
  { x: -1, y: 0, z: 0 },
  { x: 0, y: -1, z: 0 },
  { x: -1, y: 1, z: 0 },
  { x: 1, y: -1, z: 0 },
  { x: -1, y: -1, z: 0 },
  { x: 0, y: 0, z: 1 },
  { x: 0, y: 0, z: -1 },
] as const

export const SEARCH_RINGS = 12

/**
 * A wall, a baffle and a floor have NO extent on one axis, and a plane shares no
 * volume with anything — so a copy of one would count as clear of the original
 * it is sitting exactly on top of. Treated as a thin slab instead, which is what
 * "the duplicate is hidden inside it" means to the person looking at it.
 */
const MIN_EXTENT = 1e-3

/** Do two axis-aligned boxes share any volume? Touching faces do not count. */
export function boxesOverlap(a: Box, b: Box, tolerance = 1e-6): boolean {
  for (const axis of ['x', 'y', 'z'] as const) {
    const aMin = a.location[axis]
    const aMax = aMin + Math.max(a.size[axis], MIN_EXTENT)
    const bMin = b.location[axis]
    const bMax = bMin + Math.max(b.size[axis], MIN_EXTENT)
    if (aMax <= bMin + tolerance || bMax <= aMin + tolerance) return false
  }
  return true
}

/**
 * A free anchor for a copy, or null when twelve rings found nothing.
 *
 * `null` is not "put it back where it was": the caller deletes the copy, which
 * is what stops a duplicate from being silently stacked on its original.
 */
export function placeDuplicateWithoutOverlap(
  copy: Box,
  others: Box[],
  bounds?: ProjectBounds,
): { x: number; y: number; z: number } | null {
  const fits = (location: { x: number; y: number; z: number }) => {
    const candidate: Box = { location, size: copy.size }
    if (!withinProjectBounds(location, copy.size, bounds)) return false
    return !others.some((other) => boxesOverlap(candidate, other))
  }

  const origin = { ...copy.location }
  if (fits(origin)) return origin

  // The step is the model's own extent plus 8%, so a ring lands the copy clear
  // of the original with a visible gap rather than flush against it. The 0.12 m
  // floor keeps a zero-thickness sheet from stepping by nothing at all.
  const step = {
    x: Math.max(copy.size.x, 0.12) * 1.08,
    y: Math.max(copy.size.y, 0.12) * 1.08,
    z: Math.max(copy.size.z, 0.12) * 1.08,
  }

  for (let ring = 1; ring <= SEARCH_RINGS; ring += 1) {
    for (const dir of SEARCH_DIRECTIONS) {
      const location = {
        x: origin.x + dir.x * step.x * ring,
        y: origin.y + dir.y * step.y * ring,
        z: origin.z + dir.z * step.z * ring,
      }
      if (fits(location)) return location
    }
  }
  return null
}
