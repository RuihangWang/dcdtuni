/**
 * What a solve leaves behind for the probes: the harvested sensor JSON.
 *
 * Two halves of one feature, and they are easy to mistake for each other:
 *
 * - WHILE a solve runs, `solverMonitor` polls `/api/probes-latest` every two
 *   seconds and builds the live curve. Nothing is written to disk.
 * - WHEN it finishes, the last probe row has to be turned into a file, and only
 *   the frontend knows which column belongs to which probe — the OpenFOAM
 *   `probes` output is columns of numbers with no names. So the browser POSTs
 *   the NAMES, in the order it wrote them into the case, and the server writes
 *   `<run>/sensorResults/<case>_sensor_<HH-MM-SS>.json`
 *   (`handle_harvest_sensor_results`, server.py:1503).
 *
 * Ported from the solve-done branch of `_streamDctwinLog`
 * (STL_generate.html:28529-28553) and `openLatestSensorJson` (:23644).
 *
 * ## Order is the contract
 *
 * The server refuses the harvest when the name count does not match the probe
 * column count, which is the only check standing between a mismatched list and
 * a JSON file that attributes every reading to the wrong probe. The names must
 * therefore be taken in the same order the run was submitted with — the project
 * dict's insertion order — not from a set or a re-sorted list.
 */

import { getProject, sensorProbes } from '../projectStore'
import { tempUnit, type TempUnit } from '../postprocess/ppUnits'

export interface SensorReading {
  name: string
  location: [number, number, number] | null
  /** Final temperature, in `unit` — the server converts before writing. */
  tFinal: number | null
  uFinal: [number, number, number] | null
  pFinal: number | null
}

export interface SensorResultsState {
  /** Absolute path of the most recent sensor JSON, or null if none was written. */
  path: string | null
  /** The unit `tFinal` is in, which is what the file was written with. */
  unit: TempUnit
  readings: SensorReading[]
  busy: boolean
  error: string | null
}

let state: SensorResultsState = {
  path: null,
  unit: tempUnit(),
  readings: [],
  busy: false,
  error: null,
}

type Listener = () => void
const listeners = new Set<Listener>()

export function subscribeSensorResults(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

function set(patch: Partial<SensorResultsState>) {
  state = { ...state, ...patch }
  listeners.forEach((fn) => fn())
}

export function getSensorResults(): Readonly<SensorResultsState> {
  return state
}

/** Back to Kelvin from the unit the file was written in, for display. */
export function toKelvin(value: number, unit: TempUnit): number {
  if (unit === 'K') return value
  if (unit === 'F') return ((value - 32) * 5) / 9 + 273.15
  return value + 273.15
}

/** The enabled probes' names, in project order. */
export function probeNames(): string[] {
  return sensorProbes(getProject()).map((p) => p.meta?.name ?? p.uid)
}

interface SensorFile {
  caseName?: string
  unitTemperature?: TempUnit
  sensors?: {
    name?: string
    location?: number[] | null
    T_final?: number | null
    U_final?: number[] | null
    p_final?: number | null
  }[]
}

/** Turn the file the server wrote into rows the panel can render. */
export function parseSensorFile(body: SensorFile): { unit: TempUnit; readings: SensorReading[] } {
  const unit = body.unitTemperature === 'K' || body.unitTemperature === 'F' ? body.unitTemperature : 'C'
  const readings = (body.sensors ?? []).map((s, i) => ({
    name: s.name ?? `sensor_${i + 1}`,
    location:
      Array.isArray(s.location) && s.location.length === 3
        ? ([s.location[0], s.location[1], s.location[2]] as [number, number, number])
        : null,
    // `null` is the server's marker for a probe OUTSIDE the mesh (OpenFOAM
    // writes -1e+300 there): a real "no reading", not a zero.
    tFinal: typeof s.T_final === 'number' ? s.T_final : null,
    uFinal:
      Array.isArray(s.U_final) && s.U_final.length === 3
        ? ([s.U_final[0], s.U_final[1], s.U_final[2]] as [number, number, number])
        : null,
    pFinal: typeof s.p_final === 'number' ? s.p_final : null,
  }))
  return { unit, readings }
}

/**
 * Ask the server to write the sensor JSON, then read it back.
 *
 * The legacy only records the path. Reading the file back as well is what fills
 * the final column in the probe list — otherwise the reading exists on disk and
 * the panel that asked for it still shows nothing.
 */
export async function harvestSensorResults(
  names: string[] = probeNames(),
  unit: TempUnit = tempUnit(),
): Promise<string | null> {
  if (!names.length) return null
  set({ busy: true, error: null })
  try {
    const resp = await fetch('/api/harvest-sensor-results', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        // The legacy passes `uiState.session.caseName`, which nothing ever
        // assigns, so this is the value it actually sends (:28535). It names
        // the output file; the run directory already identifies the case.
        caseName: 'case',
        sensorNames: names,
        unitTemperature: unit,
      }),
    })
    const body = (await resp.json()) as { path?: string; error?: string }
    if (!resp.ok || body.error || !body.path) {
      throw new Error(body.error ?? `harvest failed (${resp.status})`)
    }
    set({ path: body.path, unit })
    await loadSensorJson(body.path)
    return body.path
  } catch (e) {
    set({ error: e instanceof Error ? e.message : String(e) })
    return null
  } finally {
    set({ busy: false })
  }
}

/** Read a sensor JSON off the server and publish its rows. */
export async function loadSensorJson(path: string): Promise<SensorReading[]> {
  const resp = await fetch(`/api/read-file?path=${encodeURIComponent(path)}`)
  const body = (await resp.json()) as { ok?: boolean; content?: string; error?: string }
  if (!body.ok || typeof body.content !== 'string') {
    throw new Error(body.error ?? `read-file -> ${resp.status}`)
  }
  const parsed = parseSensorFile(JSON.parse(body.content) as SensorFile)
  set({ path, unit: parsed.unit, readings: parsed.readings })
  return parsed.readings
}

/**
 * A solve has finished — harvest, if this project has probes.
 *
 * Called from the session store's stream-done branch, where the legacy calls it
 * (:28530), and gated the same way: no probes, no request. A case with no
 * `probes` function object has no `postProcessing/probes` directory either, and
 * the server answers 500.
 */
export async function onSolveFinished(): Promise<string | null> {
  const names = probeNames()
  if (!names.length) return null
  return harvestSensorResults(names)
}

/**
 * 📂 打开最近 sensor JSON.
 *
 * Opens the file the way the legacy does — a new tab on `/api/read-file`, which
 * is the only route to the server's filesystem the browser has. Returns an
 * error string to show when there is nothing to open yet.
 */
export function openLatestSensorJson(): string | null {
  if (!state.path) return 'sensor.no_saved_json'
  window.open(`/api/read-file?path=${encodeURIComponent(state.path)}`, '_blank')
  return null
}

/** Test seam: forget the last harvest. */
export function resetSensorResults(): void {
  state = { path: null, unit: tempUnit(), readings: [], busy: false, error: null }
  listeners.forEach((fn) => fn())
}

// A dev-only handle, the same idea as `window.__dcdtuni` in main.tsx: the
// harvest normally fires from a solve that takes minutes, and this is how it
// can be driven — and its result read — from a browser console.
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as { __dcdtuniSensors: unknown }).__dcdtuniSensors = {
    harvest: harvestSensorResults,
    onSolveFinished,
    results: getSensorResults,
    probeNames,
    openLatest: openLatestSensorJson,
  }
}
