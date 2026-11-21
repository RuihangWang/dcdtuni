/**
 * The room's ambient reference temperature.
 *
 * This is not decoration on the project panel. `geometry.temperature` becomes
 * the case's `TRef`, and the Boussinesq expansion coefficient is derived from
 * it as `beta = 1/TRef` — so it sets the buoyancy of every plume in the room.
 * Leaving it at whatever the file carried means every solve initialises from a
 * number nobody chose.
 *
 * Ported from `_renderRoomTempUI` (STL_generate.html:8264),
 * `window.setRoomTemperature` (:25998) and `window._onRoomTempSelect` (:26006).
 */

import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'

/** The presets the legacy select offers, in °C. */
export const ROOM_TEMP_PRESETS = [18, 20, 22, 24, 26, 28, 30] as const

/** What the panel shows when the project carries no temperature at all. */
export const DEFAULT_ROOM_TEMP_C = 22

const ABSOLUTE_ZERO_OFFSET = 273.15

export function roomTemperatureC(project: CfdProject | null): number {
  const value = project?.geometry?.temperature
  return typeof value === 'number' && Number.isFinite(value) ? value : DEFAULT_ROOM_TEMP_C
}

export function isPresetTemperature(tempC: number): boolean {
  return (ROOM_TEMP_PRESETS as readonly number[]).includes(tempC)
}

export function kelvinOf(tempC: number): number {
  return tempC + ABSOLUTE_ZERO_OFFSET
}

/** `beta = 1/TRef`, the Boussinesq expansion coefficient the solver reads. */
export function betaOf(tempC: number): number {
  return 1 / kelvinOf(tempC)
}

/** The `18°C (291.15 K)` label the legacy prints on each option. */
export function presetLabel(tempC: number): string {
  return `${tempC}°C (${kelvinOf(tempC).toFixed(2)} K)`
}

/**
 * The interpolation values for `inspector.room_temp_current`.
 *
 * Kept beside the writers so the readout cannot drift from what was stored:
 * two decimals on K and three significant figures on beta, as the legacy
 * formats them.
 */
export function currentReadout(tempC: number): { tempC: number; tempK: string; beta: string } {
  return {
    tempC,
    tempK: kelvinOf(tempC).toFixed(2),
    beta: betaOf(tempC).toExponential(3),
  }
}

/** Outside this the number is not a room: the legacy rejects it silently. */
export function isValidRoomTemperature(tempC: number): boolean {
  return Number.isFinite(tempC) && tempC >= -50 && tempC <= 100
}

/**
 * Write the ambient temperature. Out-of-range values are ignored, not clamped.
 *
 * Clamping would let a typo of `220` land as 100 °C and look deliberate; the
 * legacy returns before its checkpoint for the same reason, so a rejected value
 * costs no undo step either.
 */
export function setRoomTemperature(tempC: number) {
  if (!isValidRoomTemperature(tempC)) return
  store.edit('setRoomTemperature', `${tempC}°C (${kelvinOf(tempC).toFixed(2)} K)`, (draft) => {
    const geometry = draft.geometry ?? (draft.geometry = { height: 3, plane: [] })
    geometry.temperature = tempC
  })
}

/**
 * Handle the preset select, including its "custom…" entry.
 *
 * Picking "custom" nudges a preset value by 0.1 °C rather than leaving it
 * alone. The custom number box is shown only while the value is NOT a preset,
 * so without the nudge the select would snap straight back to the preset it was
 * on and the box would never appear — the legacy does the same
 * (STL_generate.html:26009).
 */
export function selectRoomTemperature(value: string) {
  if (value !== 'custom') {
    setRoomTemperature(Number.parseFloat(value))
    return
  }
  const current = roomTemperatureC(store.getProject())
  setRoomTemperature(isPresetTemperature(current) ? current + 0.1 : current)
}

// A dev-only handle, the same idea as `window.__dcdtuni` in main.tsx: the panel
// that drives these lives in the project inspector, and checking what a preset
// actually writes any other way means reading it back out of a saved file.
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as { __roomTemp: unknown }).__roomTemp = {
    roomTemperatureC,
    setRoomTemperature,
    selectRoomTemperature,
    currentReadout,
  }
}
