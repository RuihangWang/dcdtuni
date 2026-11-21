/**
 * Renaming a sensor probe, and typing its coordinates in the project's unit.
 *
 * A probe's NAME is not a label. It becomes the OpenFOAM `probes` function-object
 * name, the legend entry in the monitor and the key in the harvested sensor
 * JSON, so `sensor_1` / `sensor_2` is what a whole result set ends up carrying
 * when there is no way to rename one.
 *
 * The UNIT is the other half. Every geometry box in the app follows
 * `meta.inputUnit`, and the sensor row is the one place that does not: in a
 * project authored in millimetres — the common case, since rack datasheets are
 * in mm — typing 3000 for a probe at 3 m stores 3000 METRES and puts the probe
 * outside the domain. The list then reads back in metres against boxes filled
 * in millimetres, so nothing on screen says what happened.
 *
 * Ports the validation of `editSensorPointDialog` (STL_generate.html:23336) and
 * the unit handling of `addSensorPoint` / `_updateSensorListUI` (:23206, :23250).
 */

import type { CfdProject } from '../schema/cfdproj'
import { LENGTH_UNITS, unitOf, type LengthUnit } from '../lengthUnits'
import { sensorProbes, type SensorProbe } from '../projectStore'

export type SensorEditError =
  | 'sensor.err_name_chars'
  | 'sensor.err_name_taken'
  | 'sensor.err_coord_invalid'

export interface SensorEditDraft {
  name: string
  /** As TYPED, in the project's input unit — not metres. */
  x: number | string
  y: number | string
  z: number | string
}

export interface SensorEditResult {
  ok: boolean
  error?: SensorEditError
  patch?: { name: string; location: { x: number; y: number; z: number } }
}

/**
 * The scale from a typed number to metres.
 *
 * Exported rather than inlined because the same factor has to divide on the way
 * back out: a value converted on entry and NOT converted on display is the same
 * defect one step later.
 */
export function sensorUnitScale(project: CfdProject | null): number {
  return LENGTH_UNITS[unitOf(project)].scale
}

/** The unit label beside the XYZ boxes, e.g. "mm". */
export function sensorUnitLabel(project: CfdProject | null): string {
  return LENGTH_UNITS[unitOf(project)].label
}

/** A stored metre value, as the boxes and the list should show it. */
export function toSensorDisplay(metres: number, unit: LengthUnit): number {
  return metres / LENGTH_UNITS[unit].scale
}

/** A typed value, in metres. */
export function fromSensorDisplay(typed: number, unit: LengthUnit): number {
  return typed * LENGTH_UNITS[unit].scale
}

/** "X 3000.0000  Y 1200.0000  Z 500.0000 mm" — the legacy list row. */
export function formatSensorLocation(
  location: { x: number; y: number; z: number },
  unit: LengthUnit,
): string {
  const { scale, precision, label } = LENGTH_UNITS[unit]
  const at = (v: number) => (v / scale).toFixed(precision)
  return `X ${at(location.x)}  Y ${at(location.y)}  Z ${at(location.z)} ${label}`
}

/**
 * Is this name already another probe's?
 *
 * Compared against every probe EXCEPT the one being renamed, so re-applying a
 * probe's own name is not a collision.
 */
export function sensorNameTaken(
  project: CfdProject | null,
  name: string,
  exceptId: string,
): boolean {
  return sensorProbes(project).some(
    (p: SensorProbe) => p.uid !== exceptId && (p.meta?.name ?? p.uid) === name,
  )
}

/**
 * Validate a rename + coordinate edit, and convert the coordinates to metres.
 *
 * The name charset is the strict one the legacy enforces: the string reaches
 * OpenFOAM as a function-object name, where a space or a slash is a parse error
 * in `controlDict` rather than a cosmetic problem.
 */
export function validateSensorEdit(
  project: CfdProject | null,
  id: string,
  draft: SensorEditDraft,
): SensorEditResult {
  const name = String(draft.name ?? '').trim()
  if (!/^[A-Za-z0-9_]+$/.test(name)) return { ok: false, error: 'sensor.err_name_chars' }
  if (sensorNameTaken(project, name, id)) return { ok: false, error: 'sensor.err_name_taken' }

  const unit = unitOf(project)
  const nums = [draft.x, draft.y, draft.z].map((v) => fromSensorDisplay(parseFloat(String(v)), unit))
  if (!nums.every((v) => Number.isFinite(v))) return { ok: false, error: 'sensor.err_coord_invalid' }

  return {
    ok: true,
    patch: { name, location: { x: nums[0] as number, y: nums[1] as number, z: nums[2] as number } },
  }
}
