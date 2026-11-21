/**
 * The floor's remaining-area boundary condition, and what it drags with it.
 *
 * `wall`, `wallBaffle` and `perforated` are the most consequential choice in the
 * floor inspector: a wall makes snappyHexMesh DELETE the mesh on one side, a
 * wall-baffle keeps both sides and applies no-slip, and perforated emits a
 * master/slave `porousBafflePressure` pair. Two of the three are INTERNAL
 * surfaces, and an internal surface cannot also be the boundary the domain ends
 * at — so picking one has to clear `roomBoundaryLocation` and `isRoomBoundary`
 * with it, or the project reaches a state the legacy cannot produce: a
 * perforated tile still claiming to be the room's bottom.
 *
 * Ports `updateFloorDefaultBC` (STL_generate.html:28854), including its seeded
 * perforation block and its debounced seed-collision re-check.
 */

import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import { collectSeedCollisionWarnings } from '../run/meshPreflight'
import { showSeedCollisionDialog } from '../run/meshPreflightUi'

/** The block the legacy installs the moment a floor becomes perforated. */
export const DEFAULT_PERFORATION = {
  shape: 'circle',
  holeDiameterMm: 3.0,
  openAreaPct: 64.0,
} as const

/** The two types that put air on BOTH sides, so the floor is not a domain boundary. */
export function isInternalBoundaryType(type: string): boolean {
  return type === 'wallBaffle' || type === 'perforated'
}

/**
 * What a type change writes, as a plain function of the cooling bag.
 *
 * Separated from the store write so the coupling can be asserted without a
 * project: the interesting part is that three fields move together, not that a
 * draft was cloned.
 */
export function floorDefaultBcPatch(
  cooling: Record<string, unknown>,
  type: string,
): { cooling: Record<string, unknown>; isRoomBoundary?: boolean } {
  const next: Record<string, unknown> = { ...cooling, defaultBoundaryType: type }
  if (type === 'perforated' && !next.defaultPerforation) {
    next.defaultPerforation = { ...DEFAULT_PERFORATION }
  }
  if (!isInternalBoundaryType(type)) return { cooling: next }
  next.roomBoundaryLocation = 'none'
  return { cooling: next, isRoomBoundary: false }
}

// A switch to an internal type can put a seed point inside another model, and
// the legacy waits 300 ms before re-checking so a user cycling through the
// dropdown is not shown three dialogs. Module-level because the timer must
// survive the panel re-rendering under it.
let seedCheckTimer: ReturnType<typeof setTimeout> | null = null

function scheduleSeedCheck() {
  if (seedCheckTimer) clearTimeout(seedCheckTimer)
  seedCheckTimer = setTimeout(() => {
    seedCheckTimer = null
    const warnings = collectSeedCollisionWarnings(store.getProject())
    // Informational at edit time — the mesh-time check is the one that gates a
    // run, so the answer here is not acted on.
    if (warnings.length) void showSeedCollisionDialog(warnings)
  }, 300)
}

/** Set the remaining-area BC of a floor, keeping the room-boundary fields in step. */
export function setFloorDefaultBoundaryType(uid: string, type: string) {
  store.edit('updateFloorDefaultBC', `${uid} type=${type}`, (draft) => {
    const target = store.findConstruction(draft, uid)?.model as unknown as {
      kind?: string
      cooling?: Record<string, unknown>
      isRoomBoundary?: boolean
    } | null
    if (!target) return
    const patch = floorDefaultBcPatch(target.cooling ?? {}, type)
    target.cooling = patch.cooling
    if (patch.isRoomBoundary !== undefined) target.isRoomBoundary = patch.isRoomBoundary
  })
  if (isInternalBoundaryType(type)) scheduleSeedCheck()
}

/** Whether this model is a floor, for callers that hold a `Construction`. */
export function isFloor(model: Construction): boolean {
  return model.kind === 'floor'
}
