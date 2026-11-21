/**
 * Perforated-surface physics, and the patch/face writes behind the inspector.
 *
 * The pressure-loss coefficient is what a perforated baffle or floor tile
 * becomes in the case, so the formula has to be the SAME one the legacy editor
 * shows — a user reading K here and K there must see one number.
 */

import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'

export type HoleShape = 'circle' | 'rect' | 'diamond' | 'hexagon'

export interface Perforation {
  shape?: HoleShape
  holeDiameterMm?: number
  openAreaPct?: number
}

/**
 * Shape factor in the K estimate.
 *
 * A square hole loses more than a round one of the same open area, and a
 * diamond more again: the sharper the corner, the more the jet contracts.
 */
export const PERFORATED_SHAPE_PHI: Record<string, number> = {
  circle: 1.0,
  rect: 1.1,
  diamond: 1.15,
  // Six obtuse corners: between a round hole and a square one.
  hexagon: 1.05,
}

/**
 * Pressure-loss coefficient for a perforated plate.
 *
 * `K = (1 + φ·0.707·√(1-σ) - σ)² / σ²`, with σ the open-area fraction — the
 * Idelchik form the legacy inspector displays. σ is clamped away from 0 and 1
 * because both ends are singular: a solid plate is not a perforated one, and a
 * fully open one has no plate at all.
 */
export function perforatedK(perf: Perforation | undefined): number {
  const raw = Number.isFinite(perf?.openAreaPct) ? (perf?.openAreaPct as number) : 64
  const sigma = Math.max(1e-4, Math.min(0.9999, raw / 100))
  const phi = PERFORATED_SHAPE_PHI[perf?.shape ?? 'circle'] ?? 1.0
  return Math.pow(1 + phi * 0.707 * Math.sqrt(1 - sigma) - sigma, 2) / (sigma * sigma)
}

type Bag = Record<string, unknown>

function facesOf(model: Bag): Record<string, Bag> | null {
  const faces = (model.geometry as Bag | undefined)?.faces
  if (!faces || Array.isArray(faces)) return null
  return faces as Record<string, Bag>
}

/** Find a patch by id, and the face it belongs to. */
export function findPatch(
  project: CfdProject,
  uid: string,
  patchId: string,
): { faceId: string; patch: Bag } | null {
  const hit = store.findConstruction(project, uid)
  if (!hit) return null
  const faces = facesOf(hit.model as unknown as Bag)
  if (!faces) return null
  for (const [faceId, face] of Object.entries(faces)) {
    const patches = (face.patches ?? []) as Bag[]
    const patch = patches.find((p) => p.id === patchId)
    if (patch) return { faceId, patch }
  }
  return null
}

/** Set one field on a patch. */
export function updatePatch(uid: string, patchId: string, field: string, value: unknown) {
  store.edit('updatePatch', `${uid}/${patchId} ${field}=${String(value)}`, (draft) => {
    const found = findPatch(draft, uid, patchId)
    if (found) found.patch[field] = value
  })
}

/**
 * Set a patch's physics, creating the block if absent.
 *
 * Dotted paths ('perforated.openAreaPct') are supported because that is how the
 * legacy inspector addresses the nested perforation.
 */
/**
 * The seed a patch's `physics` gets on its first write.
 *
 * Exported because there are two writers — the inspector panel and the
 * automation contract's `updateAcuPatchPhysics` — and the seed is not
 * cosmetic: a patch whose physics is missing `velocity` exports NO boundary
 * binding at all, silently. The contract's first version passed no defaults and
 * produced an ACU with role, temperature and flow set and zero bindings in the
 * .cfdrun.
 */
export const PATCH_PHYSICS_DEFAULTS = {
  role: 'wall',
  velocity: 2.0,
  temperature: 15.0,
  volumeFlowRate: 4.0,
} as const

/**
 * Re-derive an ACU's `supplyFaces` / `returnFaces` from its patches' roles.
 *
 * These two lists are what the .cfdrun exporter reads to emit an ACU's boundary
 * bindings (`js/cfdrun_export.js:529-627`). The ONLY thing that ever wrote them
 * was the v3->V4 migrator (`js/cfdproj_migrate.js:396-408`), which runs on load
 * — nothing maintained them while editing, in either editor.
 *
 * So an ACU built live and exported straight away carried no supply and no
 * return: a room with no cooling, in a manifest that validates and a UI that
 * looks correct. It never showed up because the old pipeline saved the project
 * and loaded it back before exporting, which ran the migrator by accident.
 *
 * Derived from scratch rather than appended to, so a role changed back to
 * `wall` — or a patch deleted — drops out instead of leaving a boundary the
 * user has just said should not exist.
 */
export function syncAcuSupplyReturnFaces(model: { kind?: string; geometry?: unknown }): void {
  // A floor (a ceiling) can host RETURN grilles: the exporter reads the same
  // `returnFaces` mirror off it. It never hosts a supply.
  if (model.kind !== 'acu' && model.kind !== 'floor') return
  const geometry = (model.geometry ?? (model.geometry = {})) as {
    faces?: Record<string, { patches?: { id?: string; physics?: { role?: string } }[] }>
    supplyFaces?: { faceId: string; patchId: string }[]
    returnFaces?: { faceId: string; patchId: string }[]
  }
  const supply: { faceId: string; patchId: string }[] = []
  const ret: { faceId: string; patchId: string }[] = []
  for (const [faceId, face] of Object.entries(geometry.faces ?? {})) {
    for (const patch of face?.patches ?? []) {
      const role = patch?.physics?.role
      if (!patch?.id) continue
      if (role === 'supply') supply.push({ faceId, patchId: patch.id })
      else if (role === 'return') ret.push({ faceId, patchId: patch.id })
    }
  }
  geometry.supplyFaces = supply
  geometry.returnFaces = ret
}

export function updatePatchPhysics(
  uid: string,
  patchId: string,
  path: string,
  value: unknown,
  defaults: Bag = {},
) {
  store.edit('updatePatchPhysics', `${uid}/${patchId} ${path}=${String(value)}`, (draft) => {
    const found = findPatch(draft, uid, patchId)
    if (!found) return
    const physics = (found.patch.physics ?? (found.patch.physics = { ...defaults })) as Bag
    const parts = path.split('.')
    let target = physics
    for (const key of parts.slice(0, -1)) {
      target = (target[key] ?? (target[key] = {})) as Bag
    }
    target[parts[parts.length - 1] as string] = value
    // In the SAME edit, so the mirror can never be one undo step behind the
    // supply flow it is derived from. Off t = 0 the supply flow is a keyframe
    // and the return patches are frozen scene state: the compile derives their
    // flow from the supply track instead (transient design §1.5).
    const model = store.findConstruction(draft, uid)?.model
    if (model && !store.sceneFrozen()) syncAcuReturnFlow(model)
    if (model) syncAcuSupplyReturnFaces(model)
  })
}

/**
 * Give every return patch its share of the ACU's total supply flow.
 *
 * An ACU pushes what it takes: the return flow is not an independent number,
 * it is the supply total divided by however many return openings there are.
 * The legacy writes it during the ACU inspector's RENDER
 * (STL_generate.html:8709), which means the stored value only ever caught up
 * when someone happened to look at that panel — here it is written whenever a
 * patch changes, because the exporter reads the stored number and a stale one
 * is a wrong boundary condition in the .cfdrun.
 *
 * Returns the per-patch share, for tests and callers that want to display it.
 */
export function syncAcuReturnFlow(model: { geometry?: unknown }): number {
  const faces = ((model as Bag).geometry as Bag | undefined)?.faces
  const physicsList: Bag[] = []
  const collect = (face: Bag) => {
    for (const patch of (face.patches ?? []) as Bag[]) {
      if (patch.physics) physicsList.push(patch.physics as Bag)
    }
  }
  if (Array.isArray(faces)) faces.forEach((f) => collect(f as Bag))
  else if (faces && typeof faces === 'object') {
    Object.values(faces as Record<string, Bag>).forEach(collect)
  }

  let supplyTotal = 0
  const returns: Bag[] = []
  for (const physics of physicsList) {
    if (physics.role === 'supply') supplyTotal += Number(physics.volumeFlowRate) || 0
    else if (physics.role === 'return') returns.push(physics)
  }
  if (!returns.length) return 0
  // A return-only unit (a wall-mounted return brick, a ceiling grille) has no
  // supply to mirror; its return flow stays whatever the user typed rather
  // than being overwritten with 0 / n.
  if (!physicsList.some((physics) => physics.role === 'supply')) return 0
  const each = supplyTotal / returns.length
  returns.forEach((physics) => {
    physics.volumeFlowRate = each
  })
  return each
}

/**
 * A floor patch's opening, which is NOT stored on the patch.
 *
 * Floor openings live in `geometry.openings` keyed by patchId — a floor tile is
 * a hole in the plenum, so its type belongs to the floor's geometry rather than
 * to the surface decoration that draws it.
 */
export function updateFloorOpening(uid: string, patchId: string, path: string, value: unknown) {
  store.edit('updateFloorOpening', `${uid}/${patchId} ${path}=${String(value)}`, (draft) => {
    const hit = store.findConstruction(draft, uid)
    if (!hit) return
    const geometry = ((hit.model as unknown as Bag).geometry ?? {}) as Bag
    const openings = (geometry.openings ?? (geometry.openings = [])) as Bag[]
    let opening = openings.find((o) => o.patchId === patchId)
    if (!opening) {
      opening = { patchId, type: 'wall' }
      openings.push(opening)
    }
    const parts = path.split('.')
    let target = opening
    for (const key of parts.slice(0, -1)) {
      target = (target[key] ?? (target[key] = {})) as Bag
    }
    target[parts[parts.length - 1] as string] = value
  })
}

export function floorOpening(project: CfdProject, uid: string, patchId: string): Bag {
  const hit = store.findConstruction(project, uid)
  const geometry = (hit?.model as unknown as Bag | undefined)?.geometry as Bag | undefined
  const openings = (geometry?.openings ?? []) as Bag[]
  return openings.find((o) => o.patchId === patchId) ?? { patchId, type: 'wall' }
}

/** Set one field on a face. */
export function updateFace(uid: string, faceId: string, field: string, value: unknown) {
  store.edit('updateFace', `${uid}/${faceId} ${field}=${String(value)}`, (draft) => {
    const hit = store.findConstruction(draft, uid)
    if (!hit) return
    const faces = facesOf(hit.model as unknown as Bag)
    const face = faces?.[faceId]
    if (face) face[field] = value
  })
}

/**
 * Make a floor patch an ACU RETURN outlet, or a plain opening again.
 *
 * A ceiling return grille is a floor-kind patch that the CFD run must treat as
 * an outlet, not as a tile. The role lives on the patch (`physics.role`), the
 * mirror on the floor (`geometry.returnFaces`), and the opening entry is
 * dropped so the exporter cannot see the same patch as both.
 */
export function setFloorPatchReturn(uid: string, patchId: string, isReturn: boolean, flowRate = 1) {
  store.edit('setFloorPatchReturn', `${uid}/${patchId} -> ${isReturn}`, (draft) => {
    const found = findPatch(draft, uid, patchId)
    if (!found) return
    const physics = (found.patch.physics ?? (found.patch.physics = {})) as Bag
    if (isReturn) {
      physics.role = 'return'
      if (!Number.isFinite(Number(physics.volumeFlowRate))) physics.volumeFlowRate = flowRate
      if (!Number.isFinite(Number(physics.velocity))) physics.velocity = 2
      const owner = store.findConstruction(draft, uid)?.model as unknown as Bag | undefined
      const geometry = (owner?.geometry ?? {}) as Bag
      const openings = (geometry.openings ?? []) as Bag[]
      geometry.openings = openings.filter((o) => o.patchId !== patchId)
    } else {
      delete physics.role
    }
    const model = store.findConstruction(draft, uid)?.model
    if (model) syncAcuSupplyReturnFaces(model)
  })
}
