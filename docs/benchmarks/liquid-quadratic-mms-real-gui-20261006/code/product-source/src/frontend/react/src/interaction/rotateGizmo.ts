/**
 * The rotation gizmo: drag a ring to turn a model in place.
 *
 * The toolbar's 旋转吸附 existed with nothing to snap, because this port had
 * the rotate BUTTONS (90°/180°/270°) but no rotate DRAG. A button gives you
 * multiples of ninety; aligning a rack row with a wall that is not axis-aligned
 * needs the ring.
 *
 * Ported from `createRotationGizmo` / `beginRotationGizmoDrag` /
 * `updateRotationGizmoDrag` (STL_generate.html:15662, 16023, 16084). The parts
 * that carry the behaviour:
 *
 * - **Rotation is about the model's OBB CENTRE, not its anchor.** A rack's
 *   anchor is a corner, so turning about it swings the rack across the room.
 *   The anchor is therefore re-derived from the centre after each turn.
 * - **The delta is normalised into (-pi, pi]** before it is used. Without it,
 *   dragging past the ring's seam jumps by a full turn.
 * - **Two snaps, and they compose in the legacy's order**: Shift quantises to
 *   15°, then 旋转吸附 quantises to 1°.
 */

import * as THREE from 'three'

export type RotationAxis = 'x' | 'y' | 'z'

export function axisVector(axis: RotationAxis): THREE.Vector3 {
  return new THREE.Vector3(axis === 'x' ? 1 : 0, axis === 'y' ? 1 : 0, axis === 'z' ? 1 : 0)
}

/**
 * The angle of a point around the ring, in the ring's own plane.
 *
 * Each axis reads a different pair of components, and the pairs are the
 * legacy's — swapping them mirrors the drag, so a ring would turn the wrong way
 * under the pointer.
 */
export function angleOnPlane(
  point: { x: number; y: number; z: number },
  centre: { x: number; y: number; z: number },
  axis: RotationAxis,
): number {
  const dx = point.x - centre.x
  const dy = point.y - centre.y
  const dz = point.z - centre.z
  if (axis === 'x') return Math.atan2(dz, dy)
  if (axis === 'y') return Math.atan2(dx, dz)
  return Math.atan2(dy, dx)
}

/** Bring a raw difference of two angles into (-pi, pi]. */
export function normaliseDelta(delta: number): number {
  let d = delta
  while (d > Math.PI) d -= 2 * Math.PI
  while (d < -Math.PI) d += 2 * Math.PI
  return d
}

/**
 * Apply the two quantisations, in the legacy's order.
 *
 * Shift is the coarse one (15°) and 旋转吸附 the fine one (1°); applying the
 * fine one first would round a 15° step off its multiple.
 */
export function snapDelta(delta: number, shift: boolean, degreeSnap: boolean): number {
  let d = delta
  if (shift) {
    const step = Math.PI / 12
    d = Math.round(d / step) * step
  }
  if (degreeSnap) {
    const deg = Math.PI / 180
    d = Math.round(d / deg) * deg
  }
  return d
}

/**
 * Where an anchor lands when the model turns about `centre`.
 *
 * The per-axis formulas are the legacy's, kept literally: a rotation about Y in
 * this Y-up scene maps (x, z) with the OPPOSITE sign convention to the other
 * two, and "tidying" that up turns racks the wrong way.
 */
export function rotateAnchor(
  anchor: { x: number; y: number; z: number },
  centre: { x: number; y: number; z: number },
  axis: RotationAxis,
  delta: number,
): { x: number; y: number; z: number } {
  const cos = Math.cos(delta)
  const sin = Math.sin(delta)
  const { x: ax, y: ay, z: az } = anchor
  const { x: cx, y: cy, z: cz } = centre
  if (axis === 'z') {
    return {
      x: cx + (ax - cx) * cos - (ay - cy) * sin,
      y: cy + (ax - cx) * sin + (ay - cy) * cos,
      z: az,
    }
  }
  if (axis === 'x') {
    return {
      x: ax,
      y: cy + (ay - cy) * cos - (az - cz) * sin,
      z: cz + (ay - cy) * sin + (az - cz) * cos,
    }
  }
  return {
    x: cx + (ax - cx) * cos + (az - cz) * sin,
    y: ay,
    z: cz - (ax - cx) * sin + (az - cz) * cos,
  }
}

/** The model's new orientation after turning `delta` about a world axis. */
export function rotateQuaternion(
  start: { x: number; y: number; z: number; w: number },
  axis: RotationAxis,
  delta: number,
): { x: number; y: number; z: number; w: number } {
  const dq = new THREE.Quaternion().setFromAxisAngle(axisVector(axis), delta)
  const q = dq.multiply(new THREE.Quaternion(start.x, start.y, start.z, start.w))
  return { x: q.x, y: q.y, z: q.z, w: q.w }
}

/** Ring orientation: a torus lies in XY, so each axis needs its own tilt. */
export function ringRotation(axis: RotationAxis): THREE.Euler {
  if (axis === 'x') return new THREE.Euler(0, Math.PI / 2, 0)
  if (axis === 'y') return new THREE.Euler(Math.PI / 2, 0, 0)
  return new THREE.Euler(0, 0, 0)
}

/** Where a ring's drag handle sits for a given angle. */
export function handlePosition(
  centre: THREE.Vector3,
  radius: number,
  angle: number,
  axis: RotationAxis,
): THREE.Vector3 {
  const cos = Math.cos(angle) * radius
  const sin = Math.sin(angle) * radius
  if (axis === 'x') return new THREE.Vector3(centre.x, centre.y + cos, centre.z + sin)
  if (axis === 'y') return new THREE.Vector3(centre.x + sin, centre.y, centre.z + cos)
  return new THREE.Vector3(centre.x + cos, centre.y + sin, centre.z)
}
