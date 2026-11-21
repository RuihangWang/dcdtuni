/**
 * A patch's ANCHOR in world coordinates.
 *
 * A patch is stored in its face's own 2D frame — `x`/`y` for a rectangle's
 * bottom-left corner, `cx`/`cy` for a circle's centre, in metres from the face
 * origin along the face's `right` and `up` axes. That frame is what the drag
 * and the STL writer work in, and it is NOT what a user placing an opening
 * against a room drawing thinks in: they know where the vent is in the room,
 * not how far it is from the mirrored corner of a wall that may be rotated.
 *
 * So the inspector shows and edits the anchor in WORLD space, and this module
 * is the round trip. The stored numbers do not change shape: a world edit is
 * converted back to face `u`/`v` before it is written, so every existing file
 * keeps its geometry byte for byte.
 */

import type { Construction, Vec3 } from '../schema/cfdproj'
import { faceLayout, modelToWorld } from './seedConfig'

type Bag = Record<string, unknown>

interface Quat {
  x: number
  y: number
  z: number
  w: number
}

/** The stored anchor, in the face's 2D frame. */
export function patchAnchorFace(patch: Bag): { u: number; v: number } | null {
  const circle = patch.type === 'circle'
  const u = Number(circle ? patch.cx : patch.x)
  const v = Number(circle ? patch.cy : patch.y)
  if (!Number.isFinite(u) || !Number.isFinite(v)) return null
  return { u, v }
}

/** The two stored keys the anchor lives under. */
export function patchAnchorKeys(patch: Bag): { u: string; v: string } {
  return patch.type === 'circle' ? { u: 'cx', v: 'cy' } : { u: 'x', v: 'y' }
}

/**
 * The anchor in world space, or null when the model has no face layout for it
 * (a partition wall stores its patches in corner space, a rack's belong to its
 * servers; both are out of scope here).
 */
export function patchAnchorWorld(model: Construction, faceId: string, patch: Bag): Vec3 | null {
  if (model.kind === 'partitionWall' || model.kind === 'rack' || model.kind === 'custom') return null
  const layout = faceLayout(model.geometry?.size, faceId)
  const uv = patchAnchorFace(patch)
  if (!layout || !uv) return null
  const local = {
    x: layout.origin.x + layout.right.x * uv.u + layout.up.x * uv.v,
    y: layout.origin.y + layout.right.y * uv.u + layout.up.y * uv.v,
    z: layout.origin.z + layout.right.z * uv.u + layout.up.z * uv.v,
  }
  return modelToWorld(model, local)
}

/** Rotate by the conjugate of `q`: world direction back into model space. */
function unrotate(p: Vec3, q: Quat | undefined): Vec3 {
  if (!q) return p
  const c = { x: -q.x, y: -q.y, z: -q.z, w: q.w }
  const { x, y, z } = p
  const ix = c.w * x + c.y * z - c.z * y
  const iy = c.w * y + c.z * x - c.x * z
  const iz = c.w * z + c.x * y - c.y * x
  const iw = -c.x * x - c.y * y - c.z * z
  return {
    x: ix * c.w + iw * -c.x + iy * -c.z - iz * -c.y,
    y: iy * c.w + iw * -c.y + iz * -c.x - ix * -c.z,
    z: iz * c.w + iw * -c.z + ix * -c.y - iy * -c.x,
  }
}

/**
 * A world point back to the face's `u`/`v`. The component along the face
 * normal is dropped: a patch lives ON its face, so a world edit that leaves the
 * plane is projected back onto it rather than refused.
 */
export function worldToPatchAnchor(
  model: Construction,
  faceId: string,
  world: Vec3,
): { u: number; v: number } | null {
  if (model.kind === 'partitionWall' || model.kind === 'rack' || model.kind === 'custom') return null
  const layout = faceLayout(model.geometry?.size, faceId)
  if (!layout) return null
  const geom = (model.geometry ?? {}) as { location?: Vec3; scale?: Vec3; quaternion?: Quat }
  const loc = geom.location ?? { x: 0, y: 0, z: 0 }
  const s = geom.scale ?? { x: 1, y: 1, z: 1 }
  const r = unrotate({ x: world.x - loc.x, y: world.y - loc.y, z: world.z - loc.z }, geom.quaternion)
  const local = { x: r.x / (s.x || 1), y: r.y / (s.y || 1), z: r.z / (s.z || 1) }
  const d = { x: local.x - layout.origin.x, y: local.y - layout.origin.y, z: local.z - layout.origin.z }
  return {
    u: d.x * layout.right.x + d.y * layout.right.y + d.z * layout.right.z,
    v: d.x * layout.up.x + d.y * layout.up.y + d.z * layout.up.z,
  }
}
