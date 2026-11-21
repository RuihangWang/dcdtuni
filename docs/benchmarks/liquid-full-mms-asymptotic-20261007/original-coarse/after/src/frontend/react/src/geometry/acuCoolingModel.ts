/**
 * What drives an ACU's capacity-based cooling models.
 *
 * `fixedTemp` needs one number — the supply temperature. The other two modes
 * describe a unit with a FINITE capacity whose supply temperature rises as the
 * room heats up, and that curve needs four inputs the fixed model has no use
 * for: the set point it is trying to hold, the rated cooling duty, the return
 * temperature that duty was rated at, and the coolant temperature it can never
 * blow colder than. Without them, picking `linearCapacity` or `tableCapacity`
 * changes the mode and configures nothing, and the solver falls back to
 * defaults nobody chose.
 *
 * The values live on the SUPPLY PATCH, not on the model: an ACU with two supply
 * openings has two of everything, and the legacy writes each one separately
 * (`_buildAcuCoolingModelHtml`, STL_generate.html:8653, called once per supply
 * patch at :8726). The model-level panel here writes them to every supply patch
 * at once, which is the same thing on the single-supply units that make up
 * every case in this repo, and is the only sensible reading of a control that
 * is not attached to one opening.
 */

import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import { syncAcuReturnFlow } from './patchPhysics'

type Bag = Record<string, unknown>

export type AcuPerformanceMode = 'fixedTemp' | 'linearCapacity' | 'tableCapacity'

/** A driving input, and what the legacy falls back to when it is unset. */
export const ACU_DRIVING_DEFAULTS = {
  coolingCapacity: 35,
  ratedReturnTemp: 35,
  coolantTemperature: 14,
} as const

export type AcuDrivingField =
  | 'setPointTemperature'
  | 'coolingCapacity'
  | 'ratedReturnTemp'
  | 'coolantTemperature'

export interface AcuTableRow {
  returnTemp: number
  capacity: number
}

/** The three-point curve a fresh `tableCapacity` ACU starts from. */
export const DEFAULT_ACU_TABLE: AcuTableRow[] = [
  { returnTemp: 25, capacity: 20 },
  { returnTemp: 30, capacity: 30 },
  { returnTemp: 35, capacity: 35 },
]

export function acuPerformanceMode(model: Construction): AcuPerformanceMode {
  const raw = (model as unknown as Bag).acuPerformanceMode
  return raw === 'linearCapacity' || raw === 'tableCapacity' ? raw : 'fixedTemp'
}

export function acuTable(model: Construction): AcuTableRow[] {
  const raw = (model as unknown as Bag).coolingPerformanceTable
  return Array.isArray(raw) && raw.length > 0 ? (raw as AcuTableRow[]) : DEFAULT_ACU_TABLE
}

export interface AcuSupplyPatch {
  faceId: string
  patchId: string
  physics: Bag
}

function facesOf(model: Construction): Record<string, Bag> {
  const faces = (model.geometry as Bag | undefined)?.faces
  if (!faces || typeof faces !== 'object' || Array.isArray(faces)) return {}
  return faces as Record<string, Bag>
}

/** Every patch on the ACU whose role is `supply`, in face then patch order. */
export function supplyPatches(model: Construction): AcuSupplyPatch[] {
  const out: AcuSupplyPatch[] = []
  for (const [faceId, face] of Object.entries(facesOf(model))) {
    for (const patch of (face.patches ?? []) as Bag[]) {
      const physics = (patch.physics ?? {}) as Bag
      if (physics.role === 'supply') {
        out.push({ faceId, patchId: String(patch.id), physics })
      }
    }
  }
  return out
}

/**
 * The value a driving input should show.
 *
 * `setPointTemperature` falls back to the supply temperature rather than to a
 * constant: a unit switched from `fixedTemp` was already told what to blow, and
 * that is the temperature it is trying to hold. The legacy reads it the same
 * way (`pp.setPointTemperature != null ? … : (pp.temperature || 22)`).
 */
export function drivingValue(physics: Bag | undefined, field: AcuDrivingField): number {
  const stored = physics?.[field]
  if (typeof stored === 'number' && Number.isFinite(stored)) return stored
  if (field === 'setPointTemperature') {
    const supply = physics?.temperature
    return typeof supply === 'number' && Number.isFinite(supply) && supply !== 0 ? supply : 22
  }
  return ACU_DRIVING_DEFAULTS[field]
}

/**
 * Read a driving input for the whole model.
 *
 * Takes the FIRST supply patch. With several, the panel is showing one unit's
 * number and writing all of them — which is exactly what the per-patch panel in
 * the patch inspector exists to escape.
 */
export function modelDrivingValue(model: Construction, field: AcuDrivingField): number {
  return drivingValue(supplyPatches(model)[0]?.physics, field)
}

const SUPPLY_DEFAULTS: Bag = { role: 'supply', velocity: 2.0, temperature: 15.0, volumeFlowRate: 4.0 }

/**
 * Write a driving input to one supply patch, or to all of them.
 *
 * One `edit`, so a change made from the model panel is a single undo step
 * however many openings the unit has.
 */
export function setAcuDrivingValue(
  uid: string,
  field: AcuDrivingField,
  value: number,
  patchId: string | null = null,
) {
  if (!Number.isFinite(value)) return
  const scope = patchId ?? 'all supply patches'
  store.edit('updateAcuPatchPhysics', `${uid}/${scope} ${field}=${value}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model
    if (!model) return
    for (const [, face] of Object.entries(facesOf(model))) {
      for (const patch of (face.patches ?? []) as Bag[]) {
        if (patchId !== null && patch.id !== patchId) continue
        const physics = (patch.physics ?? (patch.physics = { ...SUPPLY_DEFAULTS })) as Bag
        if (patchId === null && physics.role !== 'supply') continue
        physics[field] = value
      }
    }
    // Same edit as the write, so the mirrored return flow can never be one undo
    // step behind the supply it is derived from.
    syncAcuReturnFlow(model)
  })
}

export function setAcuPerformanceMode(uid: string, mode: string) {
  store.edit('updateAcuCoolingModel', `${uid} ${mode}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model as unknown as Bag | undefined
    if (model) model.acuPerformanceMode = mode
  })
}

export function setAcuTable(uid: string, rows: AcuTableRow[]) {
  store.edit('setAcuPerfTable', `${uid} ${rows.length} rows`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model as unknown as Bag | undefined
    if (model) model.coolingPerformanceTable = rows
  })
}

/**
 * The row the ＋ button appends.
 *
 * It continues the curve rather than repeating its end: two points at the same
 * return temperature make the capacity lookup ambiguous. The legacy steps both
 * columns by 5 (`addAcuPerfTableRow`, STL_generate.html:28655).
 */
export function nextTableRow(rows: AcuTableRow[]): AcuTableRow {
  const last = rows[rows.length - 1]
  return last
    ? { returnTemp: last.returnTemp + 5, capacity: last.capacity + 5 }
    : { returnTemp: 30, capacity: 30 }
}

/**
 * Whether a row may be removed.
 *
 * Two points are the minimum a curve can be interpolated from, and the legacy
 * refuses below that (`removeAcuPerfTableRow`, :28674).
 */
export function canRemoveTableRow(rows: AcuTableRow[]): boolean {
  return rows.length > 2
}

// A dev-only handle, the same idea as `window.__dcdtuni` in main.tsx: the
// driving inputs sit behind a mode select and a patch selection, and what they
// wrote is only visible in the exported .cfdrun otherwise.
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as { __acuCooling: unknown }).__acuCooling = {
    acuPerformanceMode,
    setAcuPerformanceMode,
    supplyPatches,
    modelDrivingValue,
    setAcuDrivingValue,
    acuTable,
    setAcuTable,
  }
}
