/**
 * The arithmetic behind the two post-processing drag handles.
 *
 * Kept apart from the listeners so the part that can actually be wrong — where
 * a box face lands after a three-metre pull, how far a plane slides when the
 * cursor moves across its normal — is testable without a canvas, a camera or a
 * pointer device.
 *
 * Everything here works in WORLD metres. The screen-to-world step belongs to
 * the ray/plane intersection in `ppViewportDrag`, and mixing the two is how a
 * drag ends up scaling with the zoom level.
 */

export type Vec3 = [number, number, number]

export interface AiBoxBounds {
  min: Vec3
  max: Vec3
}

/**
 * A face of the inference box, in the order the six pick planes are built.
 *
 * The same order as the legacy `BM_FACE_INFO` (STL_generate.html:26729) — -X,
 * +X, -Y, +Y, -Z, +Z — because a plane's INDEX is its identity: the raycast
 * reports which object was hit and nothing more, so a different order here
 * would resize the opposite face from the one under the cursor.
 */
export const AI_BOX_FACES: { name: string; axis: 0 | 1 | 2; isMin: boolean }[] = [
  { name: '-X', axis: 0, isMin: true },
  { name: '+X', axis: 0, isMin: false },
  { name: '-Y', axis: 1, isMin: true },
  { name: '+Y', axis: 1, isMin: false },
  { name: '-Z', axis: 2, isMin: true },
  { name: '+Z', axis: 2, isMin: false },
]

/** The cursor a hovered face shows, matching the legacy hover (`:20731`). */
export function aiBoxFaceCursor(faceIdx: number): string {
  const face = AI_BOX_FACES[faceIdx]
  if (!face) return 'grab'
  return face.axis === 0 ? 'ew-resize' : face.axis === 1 ? 'ns-resize' : 'row-resize'
}

/**
 * The thinnest the box may get, in metres.
 *
 * A face dragged past its partner would invert the box, and the server reads an
 * inverted box as an EMPTY region — an inference that returns nothing and reads
 * as a model failure rather than as a mis-drag. The legacy clamps at the same
 * 0.01 (`updatePPAIBoxDrag`, STL_generate.html:20823).
 */
const MIN_EXTENT = 0.01

function clone(box: AiBoxBounds): AiBoxBounds {
  return { min: [...box.min] as Vec3, max: [...box.max] as Vec3 }
}

/** Slide the whole box by a world-space delta; its size is untouched. */
export function moveAiBox(start: AiBoxBounds, delta: Vec3): AiBoxBounds {
  const next = clone(start)
  for (let i = 0; i < 3; i++) {
    next.min[i] = (start.min[i] as number) + (delta[i] as number)
    next.max[i] = (start.max[i] as number) + (delta[i] as number)
  }
  return next
}

/**
 * Move ONE face along its own axis, leaving the other five where they are.
 *
 * Only the dragged face's component of the delta counts. A face pulled while
 * the cursor also travels sideways must not drift the box across the room —
 * that is what the centre sphere is for.
 */
export function resizeAiBox(start: AiBoxBounds, faceIdx: number, delta: Vec3): AiBoxBounds {
  const face = AI_BOX_FACES[faceIdx]
  const next = clone(start)
  if (!face) return next
  const axis = face.axis
  const d = delta[axis] as number
  if (face.isMin) {
    next.min[axis] = Math.min((start.min[axis] as number) + d, (start.max[axis] as number) - MIN_EXTENT)
  } else {
    next.max[axis] = Math.max((start.max[axis] as number) + d, (start.min[axis] as number) + MIN_EXTENT)
  }
  return next
}

/**
 * Project a drag onto a plane's normal.
 *
 * The pointer moves in the camera's plane, but a cut plane has exactly one
 * degree of freedom: how far along its own normal it sits. Taking the delta's
 * component on the normal is what makes a sideways sweep move the plane by
 * nothing instead of by however far the cursor happened to travel.
 *
 * A zero-length normal returns the start unchanged — a plane with no normal has
 * no direction to slide in, and normalising it would produce NaN coordinates
 * that silently poison the ring's transform.
 */
export function slideAlongNormal(start: Vec3, normal: Vec3, delta: Vec3): Vec3 {
  const len = Math.hypot(normal[0], normal[1], normal[2])
  if (!(len > 0)) return [...start] as Vec3
  const nx = normal[0] / len
  const ny = normal[1] / len
  const nz = normal[2] / len
  const d = delta[0] * nx + delta[1] * ny + delta[2] * nz
  return [start[0] + nx * d, start[1] + ny * d, start[2] + nz * d]
}
