/**
 * The partition wall: a solid divider with holes punched through it.
 *
 * Unlike a baffle it has THICKNESS, so its holes are through-bores cut by CSG
 * and no mesh survives inside the wall body. Two consequences drive this
 * module.
 *
 * **Resizing re-clamps the holes.** `syncPartitionWallModelState`
 * (js/geometry_pure.js:666) re-fits every hole inside the new width and height
 * and THROWS if two of them then overlap — an overlap would produce a
 * self-intersecting cutter and a broken solid. The throw is the rollback:
 * `store.edit` works on a draft, so an escaping error leaves the live project
 * and the undo stack untouched.
 *
 * **Leakage is per hole, not per wall.** `leakageRate` is a percentage on the
 * PATCH: 100% is a clear opening and 0% is maximum resistance, with the source
 * term gated on anything below 100. A sealed divider is a wall with no patch at
 * all — deleting the patch, not setting it to zero. Ported from
 * `updatePartitionWallPatchPhysics` (STL_generate.html:28724) and the leakage
 * row at :10821.
 */

import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'

interface PartitionSyncApi {
  syncPartitionWallModelState(model: Construction): void
}

function partitionSync(): PartitionSyncApi | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { geometryPure?: PartitionSyncApi }).geometryPure ?? null
}

export type PartitionSizeKey = 'W' | 'H' | 'D'

const SIZE_AXIS: Record<PartitionSizeKey, 'x' | 'y' | 'z'> = { W: 'x', H: 'y', D: 'z' }

/** The wall's default hole physics, as `ensurePatchPhysics` seeds it. */
export const PARTITION_PATCH_PHYSICS_DEFAULTS = { resistanceEnabled: false, leakageRate: 100 }

export interface PartitionPatch {
  id: string
  name?: string
  leakageRate: number
}

function facesOf(model: Construction | null): Record<string, Record<string, unknown>> | null {
  const faces = model?.geometry?.faces
  if (!faces || Array.isArray(faces)) return null
  return faces as Record<string, Record<string, unknown>>
}

/**
 * Every hole in the wall, with the leakage rate currently set on it.
 *
 * The physics can live in two places — on the patch, or parked in
 * `cooling.unboundPatchPhysics` under the patch id when the patch was written
 * before its physics — and `ensurePatchPhysics` reads both. Reading only the
 * patch would show 100% for a hole that is actually set to 20.
 */
export function partitionPatches(model: Construction | null): PartitionPatch[] {
  if (!model || model.kind !== 'partitionWall') return []
  const unbound = ((model as unknown as { cooling?: { unboundPatchPhysics?: Record<string, { leakageRate?: number }> } })
    .cooling?.unboundPatchPhysics ?? {}) as Record<string, { leakageRate?: number }>
  const faces = facesOf(model)
  if (!faces) return []
  const rows: PartitionPatch[] = []
  for (const face of Object.values(faces)) {
    const patches = (face.patches ?? []) as Record<string, unknown>[]
    if (!Array.isArray(patches)) continue
    for (const patch of patches) {
      const id = String(patch.id ?? '')
      if (!id) continue
      const physics = (patch.physics ?? {}) as { leakageRate?: number }
      const raw = physics.leakageRate ?? unbound[id]?.leakageRate
      rows.push({
        id,
        name: typeof patch.name === 'string' ? patch.name : undefined,
        leakageRate: typeof raw === 'number' && Number.isFinite(raw) ? raw : 100,
      })
    }
  }
  return rows
}

/** Percentages outside 0–100 are not a physical opening fraction. */
export function clampLeakageRate(value: unknown): number {
  return Math.max(0, Math.min(100, Number(value) || 0))
}

/** Set one hole's leakage rate, in percent. */
export function setPartitionPatchLeakage(uid: string, patchId: string, percent: number) {
  const rate = clampLeakageRate(percent)
  store.edit('partitionWallPatchPhysics', `${uid}/${patchId} leakageRate=${rate}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    if (!model || model.kind !== 'partitionWall') return
    const faces = facesOf(model)
    if (!faces) return
    for (const face of Object.values(faces)) {
      const patches = (face.patches ?? []) as Record<string, unknown>[]
      if (!Array.isArray(patches)) continue
      const patch = patches.find((p) => p.id === patchId)
      if (!patch) continue
      const physics = (patch.physics ??
        (patch.physics = { ...PARTITION_PATCH_PHYSICS_DEFAULTS })) as Record<string, unknown>
      physics.leakageRate = rate
      return
    }
    // No bound patch carries that id — park it where `ensurePatchPhysics` looks
    // next, or the setting is written into nothing.
    const cooling = ((model as unknown as { cooling?: Record<string, unknown> }).cooling ??
      ((model as unknown as { cooling: Record<string, unknown> }).cooling = {})) as Record<string, unknown>
    const unbound = (cooling.unboundPatchPhysics ??
      (cooling.unboundPatchPhysics = {})) as Record<string, Record<string, unknown>>
    const bag = (unbound[patchId] ??
      (unbound[patchId] = { ...PARTITION_PATCH_PHYSICS_DEFAULTS })) as Record<string, unknown>
    bag.leakageRate = rate
  })
}

export type PartitionResizeResult = { ok: true } | { ok: false; error: string }

/**
 * Set the wall's width, height or thickness, in metres.
 *
 * Refused, with the overlap message, when the new size would push two holes
 * into each other — see the module note on why the throw is the rollback.
 */
export function updatePartitionWallSize(
  uid: string,
  key: PartitionSizeKey,
  metres: number,
): PartitionResizeResult {
  if (!Number.isFinite(metres)) return { ok: false, error: 'not a number' }
  try {
    store.edit('updateModel', `${uid} ${key}=${metres}`, (draft) => {
      const model = store.findConstruction(draft, uid)?.model
      if (!model || model.kind !== 'partitionWall') return
      const geometry = (model.geometry ?? (model.geometry = {})) as { size?: Record<string, number> }
      const size = (geometry.size ?? (geometry.size = {})) as Record<string, number>
      size[SIZE_AXIS[key]] = metres
      partitionSync()?.syncPartitionWallModelState(model)
    })
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
  return { ok: true }
}
