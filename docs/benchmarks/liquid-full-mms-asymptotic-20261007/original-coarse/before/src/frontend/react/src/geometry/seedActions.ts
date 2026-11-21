/**
 * Writing a seed override back into the project.
 *
 * Kept apart from `seedConfig.ts` so the maths there stays pure and testable;
 * everything here goes through `projectStore`'s primitives and lands on the undo
 * stack as ONE entry per edit, the way `updateSeedSurfaceDirectionComponent`
 * (STL_generate.html:28746) and its two siblings do.
 *
 * The four stores are the legacy's, field for field — an override the HTML app
 * wrote has to be found by this app and vice versa, and the .cfdrun builder
 * reads `cooling.defaultSeedConfigs` / `cooling.wallRegionSeedConfigs` /
 * `openings[].seedConfig` / `physics.seedConfig` by those exact names
 * (js/cfdrun_build.js:776, :1415).
 */

import type { CfdProject, Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import {
  collectSeedSurfaces,
  normalizeSeedConfig,
  seedLocalCellSize,
  seedOffsetMin,
  type SeedConfig,
  type SeedSurface,
  approxEqual,
  approxVecEqual,
} from './seedConfig'

type Bag = Record<string, unknown>

interface PatchBag {
  id?: string
  physics?: Bag
}

function facesList(model: Construction): Bag[] {
  const faces = (model.geometry as { faces?: unknown } | undefined)?.faces
  if (!faces) return []
  return Array.isArray(faces) ? (faces as Bag[]) : (Object.values(faces as Record<string, Bag>) as Bag[])
}

function findPatch(model: Construction, patchId: string): PatchBag | null {
  for (const face of facesList(model)) {
    const patch = ((face.patches ?? []) as PatchBag[]).find((p) => p?.id === patchId)
    if (patch) return patch
  }
  return null
}

function coolingBag(model: Construction): Bag {
  const bag = model as unknown as { cooling?: Bag }
  return bag.cooling ?? (bag.cooling = {})
}

function geometryBag(model: Construction): Bag {
  const bag = model as unknown as { geometry?: Bag }
  return bag.geometry ?? (bag.geometry = {})
}

function writeStored(model: Construction, surface: SeedSurface, cfg: SeedConfig) {
  if (surface.owner === 'floor-opening') {
    const geom = geometryBag(model)
    const openings = (geom.openings ?? (geom.openings = [])) as Bag[]
    let opening = openings.find((o) => o.patchId === surface.patchId)
    if (!opening) {
      opening = { patchId: surface.patchId, type: 'wall' }
      openings.push(opening)
    }
    opening.seedConfig = cfg
    return
  }
  if (surface.owner === 'floor-default') {
    const cooling = coolingBag(model)
    const perRegion = (cooling.defaultSeedConfigs ?? (cooling.defaultSeedConfigs = {})) as Bag
    perRegion[surface.solidName] = cfg
    // The scalar copy as well: the .cfdrun emits BOTH `defaultBCSeedConfig` and
    // `defaultBCSeedConfigs`, and a runner reading only the scalar would take
    // the stale one if this were left behind.
    cooling.defaultSeedConfig = cfg
    return
  }
  if (surface.owner === 'baffle-patch') {
    const patch = surface.patchId ? findPatch(model, surface.patchId) : null
    if (!patch) return
    const cooling = coolingBag(model)
    const physics = (patch.physics ?? (patch.physics = {})) as Bag
    // Seeded the way `getDefaultBafflePatchPhysics` (STL_generate.html:28940)
    // resolves them: a patch that has never been edited inherits the baffle's
    // own type, and writing a seed must not silently drop it back to `wall`.
    if (physics.baffleType === undefined) physics.baffleType = cooling.type ?? 'wall'
    if (physics.porosity === undefined) physics.porosity = cooling.porosity ?? 0
    physics.seedConfig = cfg
    return
  }
  const cooling = coolingBag(model)
  const perRegion = (cooling.wallRegionSeedConfigs ?? (cooling.wallRegionSeedConfigs = {})) as Bag
  perRegion[surface.solidName] = cfg
  cooling.seedConfig = cfg
}

function clearStored(model: Construction, surface: SeedSurface) {
  const cooling = (model as unknown as { cooling?: Bag }).cooling
  if (surface.owner === 'floor-opening') {
    const openings = ((model.geometry as { openings?: Bag[] } | undefined)?.openings ?? []) as Bag[]
    const opening = openings.find((o) => o.patchId === surface.patchId)
    if (opening) delete opening.seedConfig
    return
  }
  if (!cooling) return
  if (surface.owner === 'floor-default') {
    const perRegion = cooling.defaultSeedConfigs as Bag | undefined
    if (perRegion) {
      delete perRegion[surface.solidName]
      if (!Object.keys(perRegion).length) delete cooling.defaultSeedConfigs
    }
    delete cooling.defaultSeedConfig
    return
  }
  if (surface.owner === 'baffle-patch') {
    const patch = surface.patchId ? findPatch(model, surface.patchId) : null
    if (patch?.physics) delete (patch.physics as Bag).seedConfig
    return
  }
  const perRegion = cooling.wallRegionSeedConfigs as Bag | undefined
  if (perRegion) {
    delete perRegion[surface.solidName]
    if (!Object.keys(perRegion).length) delete cooling.wallRegionSeedConfigs
  }
  delete cooling.seedConfig
}

/**
 * Turn one component of the seed direction, then re-normalise.
 *
 * A direction of length zero would put both seeds ON the surface, so the legacy
 * refuses the edit rather than storing it — the previous direction stays and the
 * input snaps back on the next render.
 */
export function setSeedDirectionComponent(
  surface: SeedSurface,
  axis: 'x' | 'y' | 'z',
  value: number,
): boolean {
  if (!Number.isFinite(value)) return false
  const next = { ...surface.seedConfig.direction, [axis]: value }
  if (next.x * next.x + next.y * next.y + next.z * next.z <= 1e-10) return false
  const cfg = normalizeSeedConfig(surface, {
    enabled: true,
    anchor: surface.seedConfig.anchor,
    direction: next,
    offset: surface.seedConfig.offset,
  })
  applySeedConfig(surface, cfg, 'updateSeedSurfaceDirection')
  return true
}

/** Push the pair further apart. Clamped to a quarter cell — see `seedOffsetMin`. */
export function setSeedOffset(surface: SeedSurface, value: number): boolean {
  if (!Number.isFinite(value)) return false
  const cfg = normalizeSeedConfig(surface, {
    enabled: true,
    anchor: surface.seedConfig.anchor,
    direction: surface.seedConfig.direction,
    offset: Math.max(value, surface.offsetMin),
  })
  applySeedConfig(surface, cfg, 'updateSeedSurfaceOffset')
  return true
}

/**
 * What a drag has to put back before it commits.
 *
 * `null` means the surface had NO override when the drag began. Restoring it to
 * the default CONFIG instead of clearing it would leave a stored override that
 * happens to equal the default — the surface would read as customised, the 重置
 * button would light up, and the .cfdrun would carry a seed block the project
 * did not ask for.
 */
export type SeedRestore = SeedConfig | null

/** Snapshot the surface's stored override, before a drag mutates it. */
export function beginSeedEdit(surface: SeedSurface): SeedRestore {
  return surface.isCustom ? surface.seedConfig : null
}

/**
 * Write a config into the LIVE project: no undo entry, no re-render.
 *
 * For the drag frames only — see `projectStore.mutateLive`. Sixty `edit` calls
 * a second would clone the project sixty times and make undoing one drag sixty
 * presses.
 */
export function liveSeedConfig(surface: SeedSurface, cfg: SeedConfig) {
  store.mutateLive((project) => {
    const model = store.findConstruction(project, surface.modelUid)?.model
    if (model) writeStored(model, surface, cfg)
  })
}

/**
 * End a drag: put the starting state back, then commit the final one.
 *
 * The restore is what makes the undo entry span the WHOLE drag — `edit`
 * snapshots the project as it finds it, so committing without restoring would
 * snapshot the dragged state and undo would land on it.
 */
export function commitSeedEdit(
  surface: SeedSurface,
  restore: SeedRestore,
  final: SeedConfig,
  action = 'dragSeedSurface',
) {
  store.mutateLive((project) => {
    const model = store.findConstruction(project, surface.modelUid)?.model
    if (!model) return
    if (restore) writeStored(model, surface, restore)
    else clearStored(model, surface)
  })
  applySeedConfig(surface, final, action)
}

function applySeedConfig(surface: SeedSurface, cfg: SeedConfig, action: string) {
  store.edit(action, `${surface.label} (${surface.owner})`, (draft) => {
    const model = store.findConstruction(draft, surface.modelUid)?.model
    if (model) writeStored(model, surface, cfg)
  })
}

/** Drop the override so the surface goes back to centre ± normal × default offset. */
export function resetSeedConfig(surface: SeedSurface) {
  store.edit('resetSeedSurfaceConfig', `${surface.label} (${surface.owner})`, (draft) => {
    const model = store.findConstruction(draft, surface.modelUid)?.model
    if (model) clearStored(model, surface)
  })
}

/**
 * The seed-helper's on-screen visibility.
 *
 * A session flag, not project data — the legacy keeps it in `uiState.session`
 * (STL_generate.html:28736) and it is deliberately absent from the saved
 * .cfdproj, so a project does not carry one user's view preference to the next.
 */
let helperVisible = true
const listeners = new Set<() => void>()

export function isSeedHelperVisible(): boolean {
  return helperVisible
}

export function toggleSeedHelperVisibility(): boolean {
  helperVisible = !helperVisible
  listeners.forEach((fn) => fn())
  return helperVisible
}

export function subscribeSeedHelper(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

/**
 * The surface whose seed the inspector is showing, published for the viewport.
 *
 * The legacy draws its helper for `getCurrentEditableSeedSurfaceContext()` —
 * the one surface being edited, never all of them, because a room with forty
 * perforated tiles would be a thicket of arrows. React's panel is the only
 * thing that knows which surface that is, and the scene is five components
 * away, so it publishes here rather than threading a prop through the app.
 */
let activeSurface: SeedSurface | null = null

export function setActiveSeedSurface(surface: SeedSurface | null): void {
  const same =
    activeSurface && surface
      ? activeSurface.modelUid === surface.modelUid &&
        activeSurface.solidName === surface.solidName &&
        approxVecEqual(activeSurface.seedConfig.anchor, surface.seedConfig.anchor) &&
        approxVecEqual(activeSurface.seedConfig.direction, surface.seedConfig.direction) &&
        approxEqual(activeSurface.seedConfig.offset, surface.seedConfig.offset)
      : activeSurface === surface
  if (same) return
  activeSurface = surface
  listeners.forEach((fn) => fn())
}

export function activeSeedSurface(): SeedSurface | null {
  return activeSurface
}

/** The offset floor for a project, for a panel that has no surface in hand yet. */
export function projectSeedOffsetMin(project: CfdProject | null): number {
  return seedOffsetMin(seedLocalCellSize(project))
}

// A dev-only handle, the same idea as `window.__dcdtuni` in main.tsx. The seed
// panel only appears on a selected wall-baffle surface, so checking that an edit
// LANDED means driving the same functions the buttons call and reading the
// project back — which is what the legacy console can already do through
// `window.updateSeedSurfaceOffset` and friends.
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as { __dcdtuniSeed: unknown }).__dcdtuniSeed = {
    surfaces: () => collectSeedSurfaces(store.getProject()),
    setSeedOffset,
    beginSeedEdit,
    liveSeedConfig,
    commitSeedEdit,
    setSeedDirectionComponent,
    resetSeedConfig,
    toggleSeedHelperVisibility,
    isSeedHelperVisible,
  }
}
