/**
 * What a drag on the floor plan does to the project.
 *
 * The 2D editor moves and turns models the same way the 3D gizmos do, and the
 * rule is the one `mutateLive` states: a drag writes STRAIGHT into the live
 * project for every frame, restores the starting values on release, and then
 * commits ONE `edit`. Two reasons, and both bite:
 *
 *  - an `edit` per frame clones the whole project sixty times a second and puts
 *    sixty entries on the undo stack, so undoing a drag takes sixty presses;
 *  - the viewport rebuilds on the project's IDENTITY (`Viewport.tsx:775`), so a
 *    drag that only ever mutated in place would leave the 3D scene showing the
 *    rack where it used to be — the move would appear to have been lost the
 *    moment the editor was closed.
 *
 * Nothing here imports `projectStore` internals; it goes through the same
 * exported primitives every other panel uses.
 */

import type { Construction } from '../schema/cfdproj'
import { edit, findConstruction, mutateLive } from '../projectStore'
import { quantize } from '../legacy/transformTools'
import type { PlanModel } from './room2dModels'
import type { SnapBox } from './room2dSnap'

/** A model's plan position at the moment a drag started. */
export interface DragOrigin {
  uid: string
  x: number
  z: number
  quaternion: { x: number; y: number; z: number; w: number }
}

export function dragOrigins(models: PlanModel[], uids: Iterable<string>): DragOrigin[] {
  const wanted = new Set(uids)
  return models
    .filter((m) => wanted.has(m.uid))
    .map((m) => ({
      uid: m.uid,
      x: m.location.x,
      z: m.location.z,
      quaternion: { ...m.quaternion },
    }))
}

/** Every OTHER model's plan box, for the snapper. Disabled models do not attract. */
export function snapBoxes(models: PlanModel[], exclude: ReadonlySet<string>): SnapBox[] {
  return models
    .filter((m) => m.enabled && !exclude.has(m.uid))
    .map((m) => ({ uid: m.uid, x: m.location.x, y: m.location.z, w: m.size.x, d: m.size.z }))
}

export interface PlanPlacement {
  uid: string
  x: number
  z: number
  quaternion?: { x: number; y: number; z: number; w: number }
}

function write(model: Construction, at: PlanPlacement): void {
  const geometry = model.geometry ?? (model.geometry = {})
  const location = geometry.location ?? (geometry.location = { x: 0, y: 0, z: 0 })
  // Micrometre grid, like the 3D drag: a plan drag must not leave float
  // dust the export turns into a seam.
  location.x = quantize(at.x)
  location.z = quantize(at.z)
  if (at.quaternion) geometry.quaternion = { ...at.quaternion }
}

/**
 * Move models during a drag: in place, no undo entry, no re-render of the app.
 *
 * The plan redraws itself from the same live project on the next frame, so the
 * canvas follows the pointer without the rest of the app being rebuilt under it.
 */
export function placeLive(placements: PlanPlacement[]): void {
  mutateLive((project) => {
    for (const at of placements) {
      const hit = findConstruction(project, at.uid)
      if (hit) write(hit.model, at)
    }
  })
}

/**
 * End a drag: rewind to where it started, then commit the final placement.
 *
 * The rewind is what makes the undo entry span the whole drag rather than its
 * last frame — `edit` snapshots the project as it finds it, so it has to find
 * the pre-drag state.
 */
export function commitPlacement(
  action: string,
  detail: string,
  origins: DragOrigin[],
  placements: PlanPlacement[],
): void {
  placeLive(origins.map((o) => ({ uid: o.uid, x: o.x, z: o.z, quaternion: o.quaternion })))
  edit(action, detail, (draft) => {
    for (const at of placements) {
      const hit = findConstruction(draft, at.uid)
      if (hit) write(hit.model, at)
    }
  })
}
