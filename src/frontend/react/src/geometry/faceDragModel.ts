/**
 * Reading a V4 construction the way a drag needs it.
 *
 * Six accessors, all of them defensive in the same way: a .cfdproj that came
 * off disk can be missing any of these, and a drag that reads `undefined` as a
 * coordinate writes NaN into the project and the model disappears from the
 * scene with no error anywhere.
 *
 * Split from `faceDrag.ts` so the drag controller reads as the state machine it
 * is, and so `faceDragSnap.ts` can reach the same accessors rather than keeping
 * a second, subtly different copy.
 */

import type * as THREE from 'three'
import type { Construction } from '../schema/cfdproj'
import type { FaceBasis, RectPatch, Vec3 } from './faceDragMath'
import { geometryPure } from '../legacy/geometryBuilder'
import { FACE_AXES, type PatchRecord } from './patchGeometry'

/** One face's frame in model-local space, as `getFaceLayouts` returns it. */
export interface Layout {
  origin: THREE.Vector3
  right: THREE.Vector3
  up: THREE.Vector3
}

export const v3 = (v: { x: number; y: number; z: number }): Vec3 => ({ x: v.x, y: v.y, z: v.z })

export function facesOf(model: Construction): Record<string, Record<string, unknown>> | null {
  const faces = (model.geometry as { faces?: unknown } | undefined)?.faces
  if (!faces || Array.isArray(faces)) return null
  return faces as Record<string, Record<string, unknown>>
}

export function faceAxes(model: Construction, faceId: string): { wType: string; hType: string } {
  const face = facesOf(model)?.[faceId]
  const fallback = FACE_AXES[faceId] ?? { wType: 'W', hType: 'H' }
  return {
    wType: typeof face?.wType === 'string' ? face.wType : fallback.wType,
    hType: typeof face?.hType === 'string' ? face.hType : fallback.hType,
  }
}

export function sizeOf(model: Construction): Vec3 {
  const size = (model.geometry as { size?: Partial<Vec3> } | undefined)?.size ?? {}
  return { x: Number(size.x) || 0, y: Number(size.y) || 0, z: Number(size.z) || 0 }
}

export function locationOf(model: Construction): Vec3 {
  const loc = (model.geometry as { location?: Partial<Vec3> } | undefined)?.location ?? {}
  return { x: Number(loc.x) || 0, y: Number(loc.y) || 0, z: Number(loc.z) || 0 }
}

export function layoutOf(model: Construction, faceId: string): Layout | null {
  const layouts = geometryPure().getFaceLayouts(model) as Record<string, Layout | undefined>
  return layouts?.[faceId] ?? null
}

/**
 * The partition-wall reconciler, which throws when the box no longer fits its
 * holes. Reached through the index signature because `geometryPure` types only
 * the handful of members the scene builder needs.
 */
export function syncPartitionWall(model: Construction): void {
  ;(geometryPure() as unknown as { syncPartitionWallModelState(m: unknown): void })
    .syncPartitionWallModelState(model)
}

export function basisOf(layout: Layout): FaceBasis {
  return { origin: v3(layout.origin), right: v3(layout.right), up: v3(layout.up) }
}

export function rectOf(patch: PatchRecord): RectPatch {
  return {
    x: Number(patch.x) || 0,
    y: Number(patch.y) || 0,
    w: Number(patch.w) || 0,
    h: Number(patch.h) || 0,
  }
}
