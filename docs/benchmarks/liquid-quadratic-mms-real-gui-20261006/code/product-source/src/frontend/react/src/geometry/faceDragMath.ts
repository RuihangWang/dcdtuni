/**
 * The arithmetic of direct manipulation on a face.
 *
 * Three separate questions, all of them pure, all of them wrong in a way that
 * is invisible until a boundary lands a few millimetres off in the mesh:
 *
 *   1. Where does a pointer ray land, expressed in the FACE's own 2D
 *      coordinates — the space `patch.x/y/w/h` is stored in.
 *   2. What does dragging one edge do to a rectangle, and what does dragging a
 *      model's edge do to its size AND its anchor (shrinking from the left edge
 *      moves the anchor; shrinking from the right does not).
 *   3. Which nearby edge the drag sticks to, and when it lets go.
 *
 * Ported from `beginPatchDrag` / `updatePatchDrag` (STL_generate.html:16266,
 * :16562). The vectors arrive as plain objects rather than THREE.Vector3 so the
 * tests can state a case in numbers; the viewport converts at the boundary.
 */

export type EdgeKey = 'bottom' | 'right' | 'top' | 'left'

export interface Vec3 {
  x: number
  y: number
  z: number
}

export interface FacePoint {
  x: number
  y: number
}

/** A face's own frame, in MODEL-LOCAL space — `getFaceLayouts()[faceId]`. */
export interface FaceBasis {
  origin: Vec3
  right: Vec3
  up: Vec3
}

export interface RectPatch {
  x: number
  y: number
  w: number
  h: number
}

export interface CirclePatch {
  cx: number
  cy: number
  r: number
}

/** The smallest a dragged dimension may become, from `updatePatchDrag`. */
export const MIN_DIM = 0.01

/** How far the pointer must travel before a button press becomes a drag. */
export const DRAG_CREATE_THRESHOLD_PX = 5

const dot = (a: Vec3, b: Vec3) => a.x * b.x + a.y * b.y + a.z * b.z

export function subtract(a: Vec3, b: Vec3): Vec3 {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }
}

export function scaleAdd(base: Vec3, dir: Vec3, k: number): Vec3 {
  return { x: base.x + dir.x * k, y: base.y + dir.y * k, z: base.z + dir.z * k }
}

/** right x up — the face's outward normal, by the builder's own convention. */
export function faceNormal(basis: FaceBasis): Vec3 {
  const { right: r, up: u } = basis
  const n = {
    x: r.y * u.z - r.z * u.y,
    y: r.z * u.x - r.x * u.z,
    z: r.x * u.y - r.y * u.x,
  }
  const len = Math.hypot(n.x, n.y, n.z) || 1
  return { x: n.x / len, y: n.y / len, z: n.z / len }
}

/**
 * A model-local point, in face coordinates.
 *
 * The legacy inverts the face's basis matrix (`getPointOnBoxFace`,
 * STL_generate.html:6144). Every layout the builder makes is ORTHONORMAL, so
 * the inverse is a pair of dot products — the same number, without building a
 * 4x4 and inverting it sixty times a second.
 */
export function localToFace(local: Vec3, basis: FaceBasis): FacePoint {
  const d = subtract(local, basis.origin)
  return { x: dot(d, basis.right), y: dot(d, basis.up) }
}

/** The inverse: a face coordinate back to model-local space. */
export function faceToLocal(basis: FaceBasis, u: number, v: number): Vec3 {
  return scaleAdd(scaleAdd(basis.origin, basis.right, u), basis.up, v)
}

/**
 * A rectangle's corners in model-local space, counter-clockwise from
 * bottom-left — the order `_createResizeHandles` expects, because the edge
 * between corner 0 and corner 1 is the one it labels `bottom`.
 */
export function rectCornersLocal(
  basis: FaceBasis,
  rect: RectPatch,
): [Vec3, Vec3, Vec3, Vec3] {
  const { x, y, w, h } = rect
  return [
    faceToLocal(basis, x, y),
    faceToLocal(basis, x + w, y),
    faceToLocal(basis, x + w, y + h),
    faceToLocal(basis, x, y + h),
  ]
}

const mid = (a: Vec3, b: Vec3): Vec3 => ({
  x: (a.x + b.x) / 2,
  y: (a.y + b.y) / 2,
  z: (a.z + b.z) / 2,
})

/** Where the four edge handles sit, given the quad's corners. */
export function edgeCenters(corners: readonly Vec3[]): Record<EdgeKey, Vec3> {
  return {
    bottom: mid(corners[0]!, corners[1]!),
    right: mid(corners[1]!, corners[2]!),
    top: mid(corners[2]!, corners[3]!),
    left: mid(corners[3]!, corners[0]!),
  }
}

/** W/H/D to the model's own axes, the mapping `_baseWHD` bridges. */
export const DIM_AXIS: Record<string, 'x' | 'y' | 'z'> = { W: 'x', H: 'y', D: 'z' }

export const DIM_DIR: Record<string, Vec3> = {
  W: { x: 1, y: 0, z: 0 },
  H: { x: 0, y: 1, z: 0 },
  D: { x: 0, y: 0, z: 1 },
}

/**
 * A model FACE's quad, in model-local space.
 *
 * Deliberately NOT the face layout: the handles have to line up with the
 * dimension the drag changes (W along +X, H along +Y, D along +Z), and three of
 * the six layouts run their `right` backwards so the face reads correctly from
 * outside. Using the layout here would put the handle labelled `right` on the
 * edge that shrinks when you drag it. Mirrors the model-level branch of
 * `_computeQuadCornersWorld` (STL_generate.html:6455).
 */
export function modelFaceCornersLocal(
  size: Vec3,
  faceId: string,
  axes: { wType: string; hType: string },
): [Vec3, Vec3, Vec3, Vec3] | null {
  const base: Record<string, number> = { W: size.x, H: size.y, D: size.z }
  const w = base[axes.wType] ?? 0
  const h = base[axes.hType] ?? 0
  if (w < 1e-6 || h < 1e-6) return null
  const origin: Vec3 = { x: 0, y: 0, z: 0 }
  if (faceId === 'right') origin.x = size.x
  else if (faceId === 'back') origin.z = size.z
  else if (faceId === 'top') origin.y = size.y
  const dw = DIM_DIR[axes.wType]!
  const dh = DIM_DIR[axes.hType]!
  return [
    { ...origin },
    scaleAdd(origin, dw, w),
    scaleAdd(scaleAdd(origin, dw, w), dh, h),
    scaleAdd(origin, dh, h),
  ]
}

/**
 * A partition wall's patch quad.
 *
 * Its patches live in model-local CORNER space (`patch.x` is local X), not in
 * the mirrored front-face layout the other box kinds use, because that is where
 * the CSG cutter puts the hole. Getting this wrong draws the handles beside the
 * hole rather than on it (STL_generate.html:6430).
 */
export function partitionWallCornersLocal(
  rect: RectPatch,
  depth: number,
  faceId: string,
): [Vec3, Vec3, Vec3, Vec3] | null {
  const { x, y, w, h } = rect
  if (w < 1e-6 || h < 1e-6) return null
  const z = faceId === 'back' ? depth : 0
  return [
    { x, y, z },
    { x: x + w, y, z },
    { x: x + w, y: y + h, z },
    { x, y: y + h, z },
  ]
}

// ---------------------------------------------------------------------------
// What a drag does
// ---------------------------------------------------------------------------

/** Move a rectangle by the pointer's travel across the face. */
export function movedRect(start: RectPatch, from: FacePoint, to: FacePoint): RectPatch {
  return { ...start, x: start.x + (to.x - from.x), y: start.y + (to.y - from.y) }
}

export function movedCircle(start: CirclePatch, from: FacePoint, to: FacePoint): CirclePatch {
  return { ...start, cx: start.cx + (to.x - from.x), cy: start.cy + (to.y - from.y) }
}

/**
 * Resize a rectangle by one edge, holding the OPPOSITE edge still.
 *
 * The opposite edge is computed from the rectangle as it was when the drag
 * STARTED, not from its current value: deriving it per frame accumulates the
 * clamping `enforceSafety` does and the fixed edge creeps.
 */
export function resizedRect(start: RectPatch, edge: EdgeKey, point: FacePoint): RectPatch {
  const fixedRight = start.x + start.w
  const fixedTop = start.y + start.h
  switch (edge) {
    case 'left':
      return { ...start, x: point.x, w: fixedRight - point.x }
    case 'right':
      return { ...start, x: start.x, w: point.x - start.x }
    case 'bottom':
      return { ...start, y: point.y, h: fixedTop - point.y }
    case 'top':
      return { ...start, y: start.y, h: point.y - start.y }
  }
}

/**
 * Whether a click on a circular patch means its RING rather than its body.
 *
 * A circle has no edge handles to grab, so the ring itself is the resize
 * affordance: within a band of it the drag resizes, inside it the drag moves.
 * The band is 6% of the face's short side, capped at 40 mm and floored at
 * 10 mm, then widened by 1.4 — small enough on a big face that the middle is
 * still draggable, large enough on a small one to be hittable
 * (STL_generate.html:16487).
 */
export function isCircleRingGrab(
  point: FacePoint,
  circle: CirclePatch,
  faceW: number,
  faceH: number,
): boolean {
  const distance = Math.hypot(point.x - circle.cx, point.y - circle.cy)
  const band = Math.max(Math.min(Math.min(faceW, faceH) * 0.06, 0.04), 0.01)
  return Math.abs(distance - circle.r) <= band * 1.4
}

/** A circle patch resizes by distance from its centre, like the legacy ring. */
export function resizedCircle(centre: FacePoint, point: FacePoint): number {
  return Math.hypot(point.x - centre.x, point.y - centre.y)
}

export interface WallResizeInput {
  /** The model's W/H/D when the drag began. */
  baseStart: Record<string, number>
  /** Its anchor when the drag began. */
  anchorStart: Vec3
  /** The face's width and height directions, in WORLD space. */
  worldRight: Vec3
  worldUp: Vec3
  /** Pointer travel on the drag plane, in world space. */
  worldDelta: Vec3
  edge: EdgeKey
  wType: string
  hType: string
  /** `baffle` is pinned flat in Z and `floor` in Y, as the legacy pins them. */
  kind?: string
}

export interface WallResizeResult {
  size: Vec3
  location: Vec3
}

/**
 * One frame of a model edge resize.
 *
 * The asymmetry is the whole point: `right` and `top` grow the box away from
 * the anchor, so only the size changes. `left` and `bottom` hold the far edge
 * still, which means the anchor has to move by exactly what the size lost —
 * along the DRAG direction, not along X, because the model may be rotated.
 */
export function wallResizeStep(input: WallResizeInput): WallResizeResult {
  const { baseStart, anchorStart, worldRight, worldUp, worldDelta, edge, wType, hType } = input
  const base: Record<string, number> = { ...baseStart }
  const location: Vec3 = { ...anchorStart }
  const deltaRight = dot(worldDelta, worldRight)
  const deltaUp = dot(worldDelta, worldUp)

  if (edge === 'right') {
    base[wType] = Math.max(MIN_DIM, (baseStart[wType] ?? 0) + deltaRight)
  } else if (edge === 'left') {
    const next = Math.max(MIN_DIM, (baseStart[wType] ?? 0) - deltaRight)
    const shift = (baseStart[wType] ?? 0) - next
    base[wType] = next
    location.x = anchorStart.x + shift * worldRight.x
    location.y = anchorStart.y + shift * worldRight.y
    location.z = anchorStart.z + shift * worldRight.z
  } else if (edge === 'top') {
    base[hType] = Math.max(MIN_DIM, (baseStart[hType] ?? 0) + deltaUp)
  } else {
    const next = Math.max(MIN_DIM, (baseStart[hType] ?? 0) - deltaUp)
    const shift = (baseStart[hType] ?? 0) - next
    base[hType] = next
    location.x = anchorStart.x + shift * worldUp.x
    location.y = anchorStart.y + shift * worldUp.y
    location.z = anchorStart.z + shift * worldUp.z
  }

  const size: Vec3 = { x: base.W ?? 0, y: base.H ?? 0, z: base.D ?? 0 }
  if (input.kind === 'baffle') size.z = 0
  if (input.kind === 'floor') size.y = 0
  return { size, location }
}

// ---------------------------------------------------------------------------
// Snapping
// ---------------------------------------------------------------------------

/**
 * Where the moving edge sits, projected onto the direction it moves along.
 *
 * One number, so a snap target is one number too — comparing whole boxes would
 * need the models to be axis-aligned with each other, which a rotated rack is
 * not.
 */
export function edgeProjection(anchor: Vec3, dir: Vec3, dim: number, edge: EdgeKey): number {
  const at = dot(anchor, dir)
  return edge === 'right' || edge === 'top' ? at + dim : at
}

/** An axis-aligned box's extent along an arbitrary direction. */
export function projectBoxRange(min: Vec3, max: Vec3, dir: Vec3): [number, number] {
  let lo = Infinity
  let hi = -Infinity
  for (let ix = 0; ix < 2; ix++) {
    for (let iy = 0; iy < 2; iy++) {
      for (let iz = 0; iz < 2; iz++) {
        const p =
          (ix ? max.x : min.x) * dir.x + (iy ? max.y : min.y) * dir.y + (iz ? max.z : min.z) * dir.z
        if (p < lo) lo = p
        if (p > hi) hi = p
      }
    }
  }
  return [lo, hi]
}

/** The same, for an already-transformed corner list (a rotated model). */
export function projectPoints(points: readonly Vec3[], dir: Vec3): [number, number] {
  let lo = Infinity
  let hi = -Infinity
  for (const p of points) {
    const v = dot(p, dir)
    if (v < lo) lo = v
    if (v > hi) hi = v
  }
  return [lo, hi]
}

/**
 * The nearest snap target within the threshold, or null.
 *
 * A target equal to where the edge STARTED is skipped: the edge is already
 * there, so snapping to it would pin the drag at its own starting position and
 * the model could not be resized at all (STL_generate.html:16683).
 */
export function bestEdgeTarget(
  rawEdgeProj: number,
  targets: readonly number[],
  threshold: number,
  initEdgeProj: number | null,
): number | null {
  let best: number | null = null
  let bestDist = threshold
  for (const target of targets) {
    if (initEdgeProj !== null && Math.abs(target - initEdgeProj) < 1e-3) continue
    const dist = Math.abs(rawEdgeProj - target)
    if (dist < bestDist) {
      bestDist = dist
      best = target
    }
  }
  return best
}

/** How far past the threshold the pointer must travel to break a sticky snap. */
export const STICKY_RELEASE = 2.5

/**
 * How close the raw edge must be to ENGAGE a snap, as a fraction of the
 * threshold. The lock then HOLDS out to one threshold and drops at
 * STICKY_RELEASE, so engage/release are not the same distance.
 *
 * That gap is a Schmitt trigger on the pointer: engage and release at one
 * distance is a comparator whose input quantizes a pixel at a time, so a
 * pointer resting on the boundary toggles the snap frame by frame and the edge
 * flickers between "on the target" and "free" — a jump of up to a full
 * threshold each time. Half is wide enough that no hand tremor crosses it and
 * narrow enough that the magnet still grabs from a visible distance away.
 */
export const STICKY_ENGAGE = 0.5

/**
 * The sticky-snap state machine.
 *
 * Without it the snap is re-decided from scratch on the frames the throttle
 * lets through and dropped on the ones it does not, so the edge flickers
 * between snapped and free. Once locked the target is HELD, and only a pointer
 * that travels 2.5 thresholds away releases it.
 */
export function stickyTarget(
  current: number | null,
  rawEdgeProj: number,
  found: number | null,
  threshold: number,
): number | null {
  // A held target wins while the raw edge is still within its threshold.
  // Near a rack the target list is dense (face edges every few cm), and
  // re-picking the NEAREST target every frame flip-flops the lock between two
  // neighbours as the pointer quantizes — the edge "twitches" and the resize
  // is unusable. The lock now only changes once the held target stops being
  // valid, and releases entirely past STICKY_RELEASE.
  if (current !== null && Math.abs(rawEdgeProj - current) <= threshold) return current
  if (found !== null) return found
  if (current !== null && Math.abs(rawEdgeProj - current) > threshold * STICKY_RELEASE) return null
  return current
}

/** Whether a held target still applies to this frame's raw edge position. */
export function stickyHolds(
  target: number | null,
  rawEdgeProj: number,
  threshold: number,
): target is number {
  return target !== null && Math.abs(rawEdgeProj - target) < threshold * STICKY_RELEASE
}

export interface StickyApplyInput {
  size: Vec3
  location: Vec3
  edge: EdgeKey
  dimKey: string
  worldRight: Vec3
  worldUp: Vec3
  /** target - rawEdgeProj. */
  snapDelta: number
  kind?: string
  /** Local axis the world snap delta maps onto; defaults to the edge's W/H axis. */
  axis?: 'x' | 'y' | 'z'
  /** +1 when the local axis points along the drag direction, -1 when against. */
  sign?: number
}

/**
 * Fold a held snap into the size and anchor this frame produced.
 *
 * The `left`/`bottom` branch moves the anchor for the same reason
 * `wallResizeStep` does — the far edge must not move — but measured from the
 * CURRENT dimension rather than the drag's start, because the snap is applied
 * on top of the drag.
 */
export interface Quat {
  x: number
  y: number
  z: number
  w: number
}

export function isIdentityQuat(q: Quat | null | undefined): boolean {
  if (!q) return true
  return q.x === 0 && q.y === 0 && q.z === 0 && Math.abs(q.w) === 1
}

/** The three local unit axes, rotated into world space (rotation part only). */
export function rotatedAxes(q: Quat): { axis: 'x' | 'y' | 'z'; v: Vec3 }[] {
  const { x, y, z, w } = q
  return [
    {
      axis: 'x',
      v: { x: 1 - 2 * (y * y + z * z), y: 2 * (x * y + z * w), z: 2 * (x * z - y * w) },
    },
    {
      axis: 'y',
      v: { x: 2 * (x * y - z * w), y: 1 - 2 * (x * x + z * z), z: 2 * (y * z + x * w) },
    },
    {
      axis: 'z',
      v: { x: 2 * (x * z + y * w), y: 2 * (y * z - x * w), z: 1 - 2 * (x * x + y * y) },
    },
  ]
}

/**
 * The local axis whose world image aligns with `dir`, plus the sign.
 *
 * Null when no local axis aligns (a 45-degree model): the edge then has no
 * single local dimension along the drag direction and the snap must stay out
 * of it rather than resize the wrong dimension.
 */
export function localAxisAlongDir(
  q: Quat | null | undefined,
  dir: Vec3,
): { axis: 'x' | 'y' | 'z'; sign: number } | null {
  const axes = isIdentityQuat(q)
    ? [
        { axis: 'x' as const, v: { x: 1, y: 0, z: 0 } },
        { axis: 'y' as const, v: { x: 0, y: 1, z: 0 } },
        { axis: 'z' as const, v: { x: 0, y: 0, z: 1 } },
      ]
    : rotatedAxes(q as Quat)
  for (const a of axes) {
    const d = dot(a.v, dir)
    if (Math.abs(d) > 0.99) return { axis: a.axis, sign: d < 0 ? -1 : 1 }
  }
  return null
}

/** The box a drag is measuring, in the same terms `getModelOBB` reads. */
export interface OrientedBox {
  /** The model's anchor: its local-space origin corner, in world units. */
  location: Vec3
  size: Vec3
  quaternion?: Quat | null
  scale?: Vec3 | null
}

/**
 * The model's own extent along `dir`, computed FROM THE BOX PASSED IN.
 *
 * Taking the box explicitly is the whole point. Reading the model's OBB
 * instead — `getOBBCorners(getModelOBB(model))`, which is what this used to
 * do — reads `geometry.size`/`geometry.location`, and during a resize those
 * still hold the PREVIOUS frame's box, snap included. The raw edge then feeds
 * on the last output:
 *
 *     S(n) = P(n) + T - S(n-1)
 *
 * which does not converge, it alternates — the resizing edge spasms between
 * two positions and never follows the pointer. Gating the engage window hides
 * the spasm by never engaging at all, which is how "the snap twitches" turns
 * into "there is no snap". Unrotated models were immune (they took the
 * location+size branch, honest by construction), so only the rotated ones —
 * Shinaide's containment baffles — showed it.
 *
 * The arithmetic mirrors js/transform_tools.js:getModelOBB exactly (centre =
 * anchor + R·(half·scale), half extents = half·|scale|, axes carrying the
 * scale's sign), so this returns the same range the OTHER models' OBBs are
 * compared against. For an identity rotation it degenerates to
 * `projectBoxRange` on location..location+size.
 */
export function ownEdgeRange(box: OrientedBox, dir: Vec3): [number, number] {
  const scale = box.scale ?? null
  const signs: [number, number, number] = [scale?.x || 1, scale?.y || 1, scale?.z || 1]
  const quat = box.quaternion ?? null
  const axes: Vec3[] = isIdentityQuat(quat)
    ? [
        { x: 1, y: 0, z: 0 },
        { x: 0, y: 1, z: 0 },
        { x: 0, y: 0, z: 1 },
      ]
    : rotatedAxes(quat as Quat).map((entry) => entry.v)
  const dims: [number, number, number] = [box.size.x, box.size.y, box.size.z]

  let centre = dot(box.location, dir)
  let reach = 0
  for (let i = 0; i < 3; i++) {
    // Signed half extent, as getModelOBB builds its local centre.
    const half = (dims[i]! / 2) * signs[i]!
    const along = dot(axes[i]!, dir)
    centre += half * along
    reach += Math.abs(half) * Math.abs(along)
  }
  return [centre - reach, centre + reach]
}

/** The moving edge's position along `dir`, from the model's own extent. */
export function edgeRangeProjection(
  range: [number, number],
  edge: EdgeKey,
): number {
  return edge === 'right' || edge === 'top' ? range[1] : range[0]
}

export function applyStickyResize(input: StickyApplyInput): WallResizeResult {
  const { edge, dimKey, worldRight, worldUp } = input
  // The caller maps the world snap delta onto the LOCAL axis whose world image
  // is the drag direction (see localAxisAlongDir). For an unrotated model that
  // is the W/H axis of the dragged edge; for a rotated baffle whose W runs
  // vertically it is a DIFFERENT axis, and using the edge's W/H here is exactly
  // how Shinaide's containment baffle grew 0.42 m through the ceiling.
  const axis = input.axis ?? (DIM_AXIS[dimKey] ?? 'x')
  const sign = input.sign ?? 1
  const snapDelta = (input.snapDelta ?? 0) * sign
  const size: Vec3 = { ...input.size }
  const location: Vec3 = { ...input.location }
  const current = size[axis]

  if (edge === 'right' || edge === 'top') {
    size[axis] = Math.max(MIN_DIM, current + snapDelta)
  } else {
    const next = Math.max(MIN_DIM, current - snapDelta)
    const moved = current - next
    size[axis] = next
    const shiftDir = edge === 'left' ? worldRight : worldUp
    location.x += moved * shiftDir.x
    location.y += moved * shiftDir.y
    location.z += moved * shiftDir.z
  }

  if (input.kind === 'baffle') size.z = 0
  if (input.kind === 'floor') size.y = 0
  return { size, location }
}

export interface PatchSnapInput {
  /** A resize snaps only the edge being dragged; a move snaps both of its own. */
  isResize: boolean
  edge: EdgeKey | null
  /** The patch's own edges, projected into world along right/up. */
  minR: number
  maxR: number
  minU: number
  maxU: number
  /** Candidate edge positions, in the same projected units. */
  targetsR: readonly number[]
  targetsU: readonly number[]
  threshold: number
}

/**
 * The shift a patch drag takes to line its edge up with a neighbour's.
 *
 * Both axes are decided independently, which is what lets a patch snap flush
 * against a rack in one direction while staying free in the other.
 */
export function bestPatchSnap(input: PatchSnapInput): { dr: number; du: number } {
  const { isResize, edge } = input
  let dr = 0
  let du = 0
  let bestR = input.threshold
  let bestU = input.threshold

  const rEdges = isResize ? (edge === 'right' ? [input.maxR] : [input.minR]) : [input.minR, input.maxR]
  const uEdges = isResize ? (edge === 'top' ? [input.maxU] : [input.minU]) : [input.minU, input.maxU]

  if (!isResize || edge === 'left' || edge === 'right') {
    for (const target of input.targetsR) {
      for (const me of rEdges) {
        const d = Math.abs(me - target)
        if (d < bestR) {
          bestR = d
          dr = target - me
        }
      }
    }
  }
  if (!isResize || edge === 'top' || edge === 'bottom') {
    for (const target of input.targetsU) {
      for (const me of uEdges) {
        const d = Math.abs(me - target)
        if (d < bestU) {
          bestU = d
          du = target - me
        }
      }
    }
  }
  return { dr, du }
}

/** Fold a patch snap into the rectangle, by whichever edge is being dragged. */
export function applyPatchSnapRect(
  rect: RectPatch,
  isResize: boolean,
  edge: EdgeKey | null,
  dr: number,
  du: number,
): RectPatch {
  const next = { ...rect }
  if (!isResize) {
    next.x += dr
    next.y += du
    return next
  }
  if (edge === 'left') {
    next.x += dr
    next.w -= dr
  }
  if (edge === 'right') next.w += dr
  if (edge === 'bottom') {
    next.y += du
    next.h -= du
  }
  if (edge === 'top') next.h += du
  return next
}

// ---------------------------------------------------------------------------
// Project bounds
// ---------------------------------------------------------------------------

export interface BoundEntry {
  enabled?: boolean
  value?: number
}

export type ProjectBounds = Partial<Record<'xMin' | 'xMax' | 'yMin' | 'yMax' | 'zMin' | 'zMax', BoundEntry>>

const AXES = [
  { min: 'xMin', max: 'xMax', axis: 'x' },
  { min: 'yMin', max: 'yMax', axis: 'y' },
  { min: 'zMin', max: 'zMax', axis: 'z' },
] as const

/**
 * Whether a box, wherever it sits, is inside the project bounds.
 *
 * Used to REVERT a resize frame rather than to clamp it: clamping mid-drag
 * would let the pointer keep pushing and the model would slide along the
 * boundary instead of stopping at it (STL_generate.html:16735).
 */
export function withinProjectBounds(
  location: Vec3,
  size: Vec3,
  bounds: ProjectBounds | undefined,
  tolerance = 1e-5,
): boolean {
  if (!bounds) return true
  for (const { min, max, axis } of AXES) {
    const lo = bounds[min]
    const hi = bounds[max]
    if (lo?.enabled && location[axis] < (lo.value ?? 0) - tolerance) return false
    if (hi?.enabled && location[axis] + size[axis] > (hi.value ?? 0) + tolerance) return false
  }
  return true
}

/**
 * Pull an anchor inside the project bounds, or refuse.
 *
 * `null` means the model physically cannot fit between an enabled Min and Max,
 * which is why the legacy deletes a drag-created model rather than dropping it
 * somewhere it does not belong (`clampModelToProjectBounds`,
 * STL_generate.html:5483).
 */
export function clampToProjectBounds(
  location: Vec3,
  size: Vec3,
  bounds: ProjectBounds | undefined,
): Vec3 | null {
  if (!bounds) return { ...location }
  const anyEnabled = AXES.some(({ min, max }) => bounds[min]?.enabled || bounds[max]?.enabled)
  if (!anyEnabled) return { ...location }

  for (const { min, max, axis } of AXES) {
    const lo = bounds[min]
    const hi = bounds[max]
    if (lo?.enabled && hi?.enabled && (hi.value ?? 0) - (lo.value ?? 0) < size[axis] - 1e-5) {
      return null
    }
  }
  const next: Vec3 = { ...location }
  for (const { min, max, axis } of AXES) {
    const lo = bounds[min]
    const hi = bounds[max]
    if (lo?.enabled) next[axis] = Math.max(lo.value ?? 0, next[axis])
    if (hi?.enabled) next[axis] = Math.min((hi.value ?? 0) - size[axis], next[axis])
  }
  return next
}

/**
 * Where a drag-created model lands, given the point the pointer is over.
 *
 * The clamp is one-sided on purpose: the MAX side subtracts the model's own
 * extent so the whole box stays inside, the MIN side does not. This runs every
 * frame of the drag, which is why it clamps rather than refusing —
 * `clampToProjectBounds` gets the final word on release
 * (`updateDragCreate`, STL_generate.html:17983).
 */
export function dragCreatePlacement(
  point: Vec3,
  size: Vec3,
  bounds: ProjectBounds | undefined,
): Vec3 {
  // The floor, not the pointer's height: a drag-created model is placed on
  // Y=0 and the ground plane is where the ray was intersected.
  const next: Vec3 = { x: point.x, y: 0, z: point.z }
  if (!bounds) return next
  for (const { min, max, axis } of AXES) {
    const lo = bounds[min]
    const hi = bounds[max]
    if (lo?.enabled) next[axis] = Math.max(lo.value ?? 0, next[axis])
    if (hi?.enabled) next[axis] = Math.min((hi.value ?? 0) - size[axis], next[axis])
  }
  return next
}

/** Has the press travelled far enough to be a drag rather than a click? */
export function dragCreateStarted(dx: number, dy: number): boolean {
  return Math.hypot(dx, dy) >= DRAG_CREATE_THRESHOLD_PX
}
