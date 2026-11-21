/** Ordinary workspace display choices only. Never retain signed grants,
 * client objects, rendered frames or ACKs across an OV connection. */
export interface LiquidDisplayPreference { enabled: boolean; fixedScale: boolean }
const liquid = new Map<string, LiquidDisplayPreference>()
function key(projectUid: string | null | undefined, runId: string | null) {
  return projectUid && runId ? JSON.stringify([projectUid, runId]) : null
}
export function liquidDisplayPreference(projectUid: string | null | undefined, runId: string | null) {
  const id = key(projectUid, runId)
  return id ? liquid.get(id) ?? null : null
}
export function rememberLiquidDisplay(projectUid: string | null | undefined, runId: string | null, value: LiquidDisplayPreference) {
  const id = key(projectUid, runId)
  if (id) liquid.set(id, { ...value })
}
export type DisplayVec3 = [number, number, number]
export interface AirDisplayPreference {
  volume: { field: 'off' | 'T' | 'U'; domain: [number, number] | null }
  slice: { visible: boolean; origin: DisplayVec3; normal: DisplayVec3; radius: number; field: 'T' | 'U' | 'Umag'; domain: [number, number] | null }
  normalKey: 'y' | 'yneg' | 'x' | 'z'
  streamOn: boolean
  streamField: 'T' | 'U'
  seed: { center: DisplayVec3; radius: number; nSeeds: number }
  seedVisible: boolean
}
const air = new Map<string, AirDisplayPreference>()
const airSelection = new Map<string, string>()
const copyAir = (value: AirDisplayPreference): AirDisplayPreference => JSON.parse(JSON.stringify(value))
export function selectedAirDisplayRun(projectUid: string | null | undefined) {
  return projectUid ? airSelection.get(projectUid) ?? null : null
}
export function airDisplayPreference(projectUid: string | null | undefined, runId: string | null) {
  const id = key(projectUid, runId)
  const value = id ? air.get(id) : null
  return value ? copyAir(value) : null
}
export function rememberAirDisplay(projectUid: string | null | undefined, runId: string | null, value: AirDisplayPreference) {
  const id = key(projectUid, runId)
  if (id && projectUid && runId) { air.set(id, copyAir(value)); airSelection.set(projectUid, runId) }
}
export function resetOvDisplayPreferences() { liquid.clear(); air.clear(); airSelection.clear() }
