/**
 * Which side of a wall or floor is "inside".
 *
 * A wall and a solid floor are zero-thickness sheets, so the STL they export
 * carries the enclosure direction in its triangle winding alone. Get it
 * backwards and snappyHexMesh keeps the cells on the wrong side of the surface
 * — the room meshes inside-out — while the model looks identical in the
 * viewport. `reverseSolidNormal` on the primary face is the flag that flips it.
 *
 * The eligibility and face-id rules come from the SHARED `geometry_pure.js`
 * (`isWallFloorOrientationEligible`, `getPrimaryOrientationFaceId`), because the
 * exporter reads the same two functions: a panel that disagreed about which
 * face is primary would offer a flip that lands somewhere the STL never looks.
 *
 * Ported from `buildWallFloorOrientationInspectorHtml` (STL_generate.html:7455)
 * and `togglePrimaryFaceSolidNormal` (:28947).
 */

import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'

interface OrientationApi {
  getPrimaryOrientationFaceId(model: Construction): string | null
  isWallFloorOrientationEligible(model: Construction): boolean
}

function orientationApi(): OrientationApi | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { geometryPure?: OrientationApi }).geometryPure ?? null
}

/**
 * The face whose winding the flip applies to: a wall's front, a floor's top.
 *
 * Falls back to the same literal answers when `geometry_pure.js` has not
 * loaded, so the panel renders in a bare test environment. A wrong answer here
 * is worse than none, so nothing else gets a guess.
 */
export function primaryOrientationFaceId(model: Construction | null): string | null {
  if (!model) return null
  const api = orientationApi()
  if (api) return api.getPrimaryOrientationFaceId(model)
  if (model.kind === 'wall') return 'front'
  if (model.kind === 'floor') return 'top'
  return null
}

/**
 * Whether this model has an inside and an outside at all.
 *
 * A floor qualifies only while it is a solid boundary: with
 * `defaultBoundaryType` set to `wallBaffle` it is an internal baffle wetted on
 * both sides, and "which side is the room" has no answer.
 */
export function orientationEligible(model: Construction | null): boolean {
  if (!model) return false
  const api = orientationApi()
  if (api) return api.isWallFloorOrientationEligible(model)
  if (model.kind === 'wall') return true
  if (model.kind === 'floor') {
    const cooling = (model as unknown as { cooling?: { defaultBoundaryType?: string } }).cooling
    return (cooling?.defaultBoundaryType || 'wall') === 'wall'
  }
  return false
}

function primaryFace(model: Construction | null): Record<string, unknown> | null {
  const faceId = primaryOrientationFaceId(model)
  const faces = model?.geometry?.faces
  if (!faceId || !faces || Array.isArray(faces)) return null
  const face = (faces as Record<string, unknown>)[faceId]
  return face && typeof face === 'object' ? (face as Record<string, unknown>) : null
}

/** True when the exported winding is flipped from the default. */
export function isPrimaryFaceReversed(model: Construction | null): boolean {
  return primaryFace(model)?.reverseSolidNormal === true
}

/** Flip the exported winding of the model's primary face. */
export function togglePrimaryFaceSolidNormal(uid: string) {
  const project = store.getProject()
  const model = (project ? store.findConstruction(project, uid)?.model : null) ?? null
  if (!orientationEligible(model)) return
  const faceId = primaryOrientationFaceId(model)
  if (!faceId) return
  store.edit('togglePrimaryFaceSolidNormal', `${uid}/${faceId}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model
    const faces = target?.geometry?.faces
    if (!faces || Array.isArray(faces)) return
    const face = (faces as Record<string, Record<string, unknown>>)[faceId]
    if (!face) return
    face.reverseSolidNormal = face.reverseSolidNormal !== true
  })
}
