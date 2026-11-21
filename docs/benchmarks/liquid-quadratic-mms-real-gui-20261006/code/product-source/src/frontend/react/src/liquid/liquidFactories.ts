/**
 * The three liquid models a selection can spawn: CRH, cold plate, heat source.
 *
 * Ported from `createCRHForRack` (STL_generate.html:13701),
 * `createColdPlateForRack` (:13724) and `createHeatSourceForServer` (:13738),
 * together with the factories they call (`createCrhModel` :2862,
 * `createColdPlateModel` :2890, `createHeatSourceModel` :2920).
 *
 * All three go through the shared v3 seed -> `finalizeNewModelV4` path for the
 * same reason `legacy/factories.ts` does: the migrator is the single definition
 * of what a V4 model is, and a hand-written V4 object here would be a second
 * one. It matters more for these kinds than for a box — they land in the
 * `boxes` bucket with `meta.legacyKind` as their only discriminator, and their
 * spec blocks (`crh`, `coldPlate`, `cooling` for a heat source) are read by the
 * liquid graph generator, where a wrong field name is a silent failure rather
 * than an error.
 *
 * The placement offsets are the legacy's, comments included: they are the
 * Y-up numbers from the 2026-04-18 axis change, not fresh guesses.
 */

import type { CfdProject, Construction, Vec3 } from '../schema/cfdproj'
import { allModels } from '../schema/cfdproj'
import { bucketForKind, finalizeNewModel, rackState, type V3Seed } from '../legacy/cfdprojModules'
import { nextModelUid, type CreatedModel } from '../legacy/factories'

/**
 * The four seed helpers `rack_state.js` exports but `RackStateModule` does not
 * declare. Borrowed rather than re-declared so a face's default shape has one
 * definition; re-typing them here is only about reaching them from TypeScript.
 */
interface RackStateSeedHelpers {
  createFacesState(labelOverrides?: Record<string, string>): Record<string, unknown>
  getDefaultModelTransform(): unknown
  getDefaultModelMoveTool(): unknown
  getDefaultCfd(kind: string): unknown
}

function seedHelpers(): RackStateSeedHelpers {
  return rackState() as unknown as RackStateSeedHelpers
}

/** Mirrors `RACK_DEFAULT_UNIT_HEIGHT`, the 1U height a slot position is scaled by. */
export const DEFAULT_UNIT_HEIGHT = 0.04445

interface SeedOptions {
  uid: string
  kind: string
  name: string
  base: { W: number; D: number; H: number }
  opacity: number
  extra: Record<string, unknown>
}

function liquidSeed(opts: SeedOptions): V3Seed {
  const helpers = seedHelpers()
  return {
    id: opts.uid,
    kind: opts.kind,
    name: opts.name,
    enabled: true,
    // None of the three carry an anchor marker: they are attached to a rack or
    // a server, so a draggable anchor cube would offer a move the parent link
    // does not follow.
    showAnchorMarker: false,
    opacity: opts.opacity,
    anchor: { x: 0, y: 0, z: 0 },
    base: { ...opts.base },
    transform: helpers.getDefaultModelTransform(),
    moveTool: helpers.getDefaultModelMoveTool(),
    faces: helpers.createFacesState(),
    solidRegions: [],
    customSTLBase64: null,
    customOffset: { x: 0, y: 0, z: 0 },
    cfd: helpers.getDefaultCfd(opts.kind),
    ...opts.extra,
  }
}

/**
 * `rotateTool`, attached after the migrator so it lands last in key order.
 *
 * Same reason as `legacy/factories.ts`: the legacy app materialises it lazily
 * on the first inspector render (`ensureModelRotateTool`,
 * STL_generate.html:8342), which for these kinds happens immediately — they are
 * selected the moment they are created — so a model saved without it would
 * differ from the legacy's for no reason.
 */
function withRotateTool(model: Construction): Construction {
  ;(model as Record<string, unknown>).rotateTool = {
    enabled: false,
    axes: { yaw: true, pitch: false, roll: false },
  }
  return model
}

type Bag = Record<string, unknown>

function loc(model: Construction): Vec3 {
  const l = (model.geometry?.location ?? {}) as Partial<Vec3>
  return { x: Number(l.x ?? 0), y: Number(l.y ?? 0), z: Number(l.z ?? 0) }
}

function size(model: Construction): Vec3 {
  const s = (model.geometry?.size ?? {}) as Partial<Vec3>
  return { x: Number(s.x ?? 0), y: Number(s.y ?? 0), z: Number(s.z ?? 0) }
}

// ---------------------------------------------------------------------------
// Placement — pure, so the offsets can be checked without building a model
// ---------------------------------------------------------------------------

/**
 * A CRH is a 20 mm slab covering the rack's BACK face.
 *
 * Y-up: the depth offset is along Z. Sizing it from the rack rather than from
 * the factory's 0.6 x 2.0 default is what makes it fit whichever rack it was
 * created for.
 */
export function crhPlacement(rack: Construction): { location: Vec3; size: Vec3 } {
  const l = loc(rack)
  const s = size(rack)
  return {
    location: { x: l.x, y: l.y, z: l.z + s.z },
    size: { x: s.x, y: s.y, z: 0.02 },
  }
}

/** Mid-height (Y +0.5) and mid-depth (Z +0.1) of the rack, the legacy's offsets. */
export function coldPlateLocation(rack: Construction): Vec3 {
  const l = loc(rack)
  return { x: l.x + 0.05, y: l.y + 0.5, z: l.z + 0.1 }
}

/**
 * At the server's own slot height.
 *
 * `startUnit` is the slot index, so the vertical position is the rack's floor
 * plus `startUnit` unit heights — a heat source pinned to the rack origin would
 * sit under every server but the bottom one.
 */
export function heatSourceLocation(rack: Construction, startUnit: number): Vec3 {
  const l = loc(rack)
  const unitHeight = Number((rack.geometry as Bag | undefined)?.slotHeight ?? DEFAULT_UNIT_HEIGHT)
  const slotY = l.y + (startUnit || 0) * (unitHeight || DEFAULT_UNIT_HEIGHT)
  return { x: l.x + 0.1, y: slotY + 0.03, z: l.z + 0.15 }
}

// ---------------------------------------------------------------------------
// Lookups
// ---------------------------------------------------------------------------

/** The CRH already attached to this rack, if any. A rack may only have one. */
export function findCrhForRack(project: CfdProject, rackUid: string): Construction | null {
  for (const { model } of allModels(project)) {
    if (model.kind !== 'crh') continue
    if ((model as Bag).parentRackId === rackUid) return model
  }
  return null
}

export interface ServerView {
  uid: string
  name: string
  /** Slot index of the server's lowest unit (`geometry.slotPosition`). */
  startUnit: number
}

/** One server of a rack, by uid. V4 keeps them in `constructions.servers`. */
export function rackServer(rack: Construction | null, serverUid: string): ServerView | null {
  const servers = ((rack?.constructions as { servers?: Record<string, Bag> } | undefined)
    ?.servers ?? {}) as Record<string, Bag>
  const srv = servers[serverUid]
  if (!srv) return null
  const meta = (srv.meta ?? {}) as { name?: string }
  const geometry = (srv.geometry ?? {}) as { slotPosition?: number }
  return {
    uid: serverUid,
    // The legacy names the heat source `Heat_<name || id>`; a server that was
    // never renamed has no `meta.name`, and falling back to the uid is what
    // keeps the model from being called "Heat_undefined".
    name: meta.name ?? serverUid,
    startUnit: Number(geometry.slotPosition ?? 0),
  }
}

// ---------------------------------------------------------------------------
// Builders
// ---------------------------------------------------------------------------

export function buildCrh(project: CfdProject, rack: Construction): CreatedModel {
  const placement = crhPlacement(rack)
  const model = withRotateTool(
    finalizeNewModel(
      liquidSeed({
        uid: nextModelUid(project),
        kind: 'crh',
        name: `CRH_${rack.meta?.name ?? rack.uid}`,
        base: { W: 0.6, D: 0.02, H: 2.0 },
        opacity: 0.4,
        extra: {
          parentRackId: rack.uid,
          crh: {
            effectiveness: 0.5,
            waterFlowRate: 20,
            waterInletTemp: 25,
            heatRejectionCapacity: 30000,
          },
        },
      }),
    ),
  )
  model.geometry = { ...model.geometry, location: placement.location, size: placement.size }
  return { bucket: bucketForKind('crh'), model }
}

/**
 * `now` is injectable only so the test can pin the name.
 *
 * The legacy stamps `Date.now()` into a cold plate's name
 * (STL_generate.html:13729) because a rack can carry many and they are not
 * numbered anywhere else — two created in the same session would otherwise
 * collide in the tree.
 */
export function buildColdPlate(
  project: CfdProject,
  rack: Construction,
  now: number = Date.now(),
): CreatedModel {
  const model = withRotateTool(
    finalizeNewModel(
      liquidSeed({
        uid: nextModelUid(project),
        kind: 'cold_plate',
        name: `CP_${rack.meta?.name ?? rack.uid}_${now}`,
        base: { W: 0.1, D: 0.1, H: 0.01 },
        opacity: 1,
        extra: {
          parentRackId: rack.uid,
          parentServerId: null,
          coldPlate: {
            specName: 'cp_default',
            chipTDP: 350,
            thermalModel: { chipArea: 0.004, chipThickness: 0.001, thermalConductivity: 148 },
            inletPortId: null,
            outletPortId: null,
          },
        },
      }),
    ),
  )
  model.geometry = { ...model.geometry, location: coldPlateLocation(rack) }
  return { bucket: bucketForKind('cold_plate'), model }
}

export function buildHeatSource(
  project: CfdProject,
  rack: Construction,
  server: ServerView,
): CreatedModel {
  const model = withRotateTool(
    finalizeNewModel(
      liquidSeed({
        uid: nextModelUid(project),
        kind: 'heat_source',
        name: `Heat_${server.name}`,
        base: { W: 0.04, D: 0.04, H: 0.005 },
        opacity: 1,
        extra: {
          parentRackId: rack.uid,
          parentServerId: server.uid,
          // The migrator SPLITS this block: `heatMode`/`totalPower`/
          // `fixedTemperature` become `cooling.boundaryMode` / `totalPowerW` /
          // `fixedTemperatureC`, and whatever is left is copied onto `cooling`
          // verbatim (cfdproj_migrate.js:162-167). The legacy factory carries
          // only power/chipArea/thermalMass, so all three land on `cooling`.
          heatSource: { power: 350, chipArea: 0.004, thermalMass: 60 },
        },
      }),
    ),
  )
  model.geometry = {
    ...model.geometry,
    location: heatSourceLocation(rack, server.startUnit),
  }
  return { bucket: bucketForKind('heat_source'), model }
}
