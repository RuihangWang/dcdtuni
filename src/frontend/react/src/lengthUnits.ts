/**
 * The input unit: what the geometry boxes are typed in.
 *
 * The project stores metres, always. This is a DISPLAY setting — someone
 * working from a rack datasheet in millimetres should be able to type 600
 * rather than 0.6, and the legacy offers exactly mm / cm / m
 * (`renderUnitControls`, STL_generate.html:4702).
 *
 * It lives in `meta.inputUnit`, so it is saved with the project: the unit a
 * room was authored in is a property of that work, not of the browser session.
 *
 * Only the GEOMETRY fields follow it. Mesh sizes, solver settings and physics
 * are in metres and SI in both apps, and converting those would change numbers
 * whose units are part of their meaning.
 */

import type { CfdProject } from './schema/cfdproj'

export type LengthUnit = 'mm' | 'cm' | 'm'

export interface UnitConfig {
  scale: number
  label: string
  step: number
  precision: number
}

export const LENGTH_UNITS: Record<LengthUnit, UnitConfig> = {
  mm: { scale: 0.001, label: 'mm', step: 0.1, precision: 4 },
  cm: { scale: 0.01, label: 'cm', step: 0.01, precision: 4 },
  m: { scale: 1, label: 'm', step: 0.0001, precision: 4 },
}

export function unitOf(project: CfdProject | null): LengthUnit {
  const raw = (project?.meta as { inputUnit?: string } | undefined)?.inputUnit
  return raw === 'mm' || raw === 'cm' ? raw : 'm'
}

export function unitConfig(project: CfdProject | null): UnitConfig {
  return LENGTH_UNITS[unitOf(project)]
}

/** Metres -> what the box shows. */
export function toDisplay(metres: number, unit: LengthUnit): number {
  return metres / LENGTH_UNITS[unit].scale
}

/** What was typed -> metres. */
export function fromDisplay(value: number, unit: LengthUnit): number {
  return value * LENGTH_UNITS[unit].scale
}

/**
 * The step a box should use in this unit.
 *
 * Not the same number scaled: 0.05 m is a sensible nudge, and 0.05 mm is not.
 * The legacy hardcodes a per-unit step for the same reason.
 */
export function stepFor(unit: LengthUnit): number {
  return LENGTH_UNITS[unit].step
}
