/**
 * Reading a solve out of its log.
 *
 * The solver's residual history is not available anywhere else: OpenFOAM prints
 * it and nothing writes it to disk in a form the browser can read back. So the
 * convergence plot is a parse of the streaming log, exactly as the legacy
 * `_smParseLogLine` (STL_generate.html:25587) does it.
 *
 * Kept pure and separate from the store so it can be tested against REAL solver
 * logs (`case/<name>/log/<stamp>/<stamp>.log`).
 *
 * Two things the regexes here must keep doing:
 *
 * - **No `^` anchors.** Lines arrive with a loguru prefix —
 *   `2026-08-18 05:13:37 | INFO     | Time = 1` — so every pattern matches
 *   content anywhere in the line. An anchored version matches nothing at all
 *   and the chart stays empty for the whole run.
 * - **First residual per field per step wins.** A PIMPLE step solves `Ux` once
 *   per outer corrector; plotting every one of them would draw several points
 *   at the same x and the curve would fold back on itself.
 */

export interface SeriesPoint {
  step: number
  value: number
}

export type SeriesMap = Record<string, SeriesPoint[]>

export interface MonitorData {
  residuals: SeriesMap
  /** Patch-average temperatures, keyed by the `avgT_<patch>` function-object name. */
  temperatures: SeriesMap
  /** Sensor probes, keyed by probe name. Filled by the poller, not by this parser. */
  sensors: SeriesMap
  /** Probes the solver reported as outside the mesh — shown in the legend, not plotted. */
  sensorNotFound: Record<string, true>
  currentStep: number
  seenFieldsThisStep: Record<string, true>
  /**
   * The function-object name from a `... write:` header line.
   *
   * surfaceFieldValue prints its value on the NEXT line, so the parser is a
   * two-line state machine and this is its one bit of state.
   */
  foExpectValue: string | null
}

export function emptyMonitorData(): MonitorData {
  return {
    residuals: {},
    temperatures: {},
    sensors: {},
    sensorNotFound: {},
    currentStep: 0,
    seenFieldsThisStep: {},
    foExpectValue: null,
  }
}

/**
 * What the caller should do once the line has been consumed.
 *
 * `redraw` is what keeps the chart at one repaint per time step instead of one
 * per log line: a 200-rack solve prints dozens of lines per step and repainting
 * on each of them is what made the legacy window stutter before it moved the
 * redraw onto the step boundary.
 */
export type ParseOutcome = 'none' | 'redraw' | 'probe-poll'

const RE_TIME = /\bTime = ([\d.eE+-]+)/
const RE_RESIDUAL =
  /Solving for (\w+), Initial residual = ([\d.eE+-]+), Final residual = ([\d.eE+-]+)/
// Matches the old `avgT_x output:` banner and the current
// `surfaceFieldValue avgT_x write:` one.
const RE_FO_HEADER = /(\S+)\s+(?:output|write):\s*$/
// Matches `areaAverage(T) = 295` and `areaAverage(patchName) of T = 295.81`.
const RE_AREA_AVERAGE = /areaAverage\([^)]+\)(?:\s+of\s+T)?\s*=\s*([\d.eE+-]+)/

/**
 * Fold one log line into the monitor data. Mutates `data` and says what changed.
 *
 * Order matters and matches the legacy chain: a line is claimed by the FIRST
 * pattern that fits and the rest are not tried.
 */
export function parseSolverLogLine(data: MonitorData, line: string): ParseOutcome {
  const timeMatch = RE_TIME.exec(line)
  if (timeMatch) {
    // Redraw on the step BOUNDARY — the step just finished is the one now
    // complete in the data.
    const outcome: ParseOutcome = data.currentStep > 0 ? 'redraw' : 'none'
    data.currentStep = parseFloat(timeMatch[1] as string)
    data.seenFieldsThisStep = {}
    return outcome
  }

  const resMatch = RE_RESIDUAL.exec(line)
  if (resMatch && data.currentStep > 0) {
    const field = resMatch[1] as string
    // The INITIAL residual is the convergence measure: it is the error the step
    // started from. The final one is how well that step's linear solve went,
    // which says nothing about whether the run is converging.
    const initial = parseFloat(resMatch[2] as string)
    if (!data.seenFieldsThisStep[field]) {
      data.seenFieldsThisStep[field] = true
      ;(data.residuals[field] ??= []).push({ step: data.currentStep, value: initial })
    }
    return 'none'
  }

  const foMatch = RE_FO_HEADER.exec(line)
  if (foMatch) {
    data.foExpectValue = foMatch[1] as string
    return 'none'
  }

  if (data.foExpectValue) {
    const valMatch = RE_AREA_AVERAGE.exec(line)
    if (valMatch) {
      const name = data.foExpectValue
      ;(data.temperatures[name] ??= []).push({
        step: data.currentStep,
        value: parseFloat(valMatch[1] as string),
      })
    }
    // Cleared whether or not the value was found: the header's next line is the
    // only place the value can appear, and leaving the flag set would attach a
    // much later number to this function object.
    data.foExpectValue = null
    return 'none'
  }

  if (line.indexOf('writing probe data') !== -1 || line.indexOf('probes write') !== -1) {
    return 'probe-poll'
  }

  if (line.indexOf('ExecutionTime') !== -1) return 'redraw'

  return 'none'
}

/**
 * Fold a `/api/probes-latest` answer into the sensor series.
 *
 * `fields.T[i][j]` is step `i`, probe `j`, in the order the names were asked
 * for. A value at or below -1e299 is OpenFOAM's "this point is outside the
 * mesh" marker: plotting it would drag the whole y-axis to -1e299 and flatten
 * every real curve into a single line at the top.
 */
export function absorbProbeSample(
  data: MonitorData,
  names: string[],
  payload: { steps?: number[]; fields?: { T?: (number | null)[][] } },
  since: number,
): number {
  let latest = since
  const steps = payload.steps ?? []
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i] as number
    if (step > latest) latest = step
    const row = payload.fields?.T?.[i]
    if (!row) continue
    for (let j = 0; j < names.length; j++) {
      const name = names[j] as string
      const value = row[j]
      if (value == null) continue
      if (value <= -1e299) {
        data.sensorNotFound[name] = true
        continue
      }
      ;(data.sensors[name] ??= []).push({ step, value })
    }
  }
  return latest
}
