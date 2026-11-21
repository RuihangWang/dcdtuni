/** Result-history selection shared by the results panel and playback. */
import { getCaseStatus } from '../run/session'

export interface LiquidRunOption {
  /** What goes to `/api/liquid/results?run=`. */
  value: string
  label: string
}

interface RawLiquidRun {
  name?: unknown
  path?: unknown
}

/**
 * The solve records offered by the run selector.
 *
 * `scan-case-status` already lists them — a directory under `case/<name>/log` or
 * `case/<name>/Liquid/log` counts as a liquid run when it holds a
 * `liquid_result.json`. The value is `path || name`, as the legacy option is
 * built: the server resolves `?run=` under its own log bases, and an absolute
 * path joined onto a base still lands on the same file, so both forms work and
 * this one matches the legacy DOM.
 *
 * An empty list is NOT an empty selector. `hasResults` distinguishes "there is
 * a result loaded but no run directory was found" — which offers "Latest", the
 * unqualified endpoint — from "nothing has ever been solved here".
 */
export function liquidRunOptions(
  runs: unknown[] | null | undefined,
  hasResults: boolean,
  noRecordsLabel: string,
  latestLabel = 'Latest',
): LiquidRunOption[] {
  const list = (runs ?? []).filter((r): r is RawLiquidRun => !!r && typeof r === 'object')
  const records = list.map((run) => {
    const name = typeof run.name === 'string' ? run.name : ''
    const path = typeof run.path === 'string' ? run.path : ''
    return { value: path || name, label: name || path }
  }).filter((option) => option.value !== '')
  if (records.length === 0) {
    return [{ value: '', label: hasResults ? latestLabel : noRecordsLabel }]
  }
  // The unqualified endpoint can hold a newer partial or cancelled run. Keep
  // its empty selection explicit instead of displaying a historical record
  // while the left panel reads that newer result.
  return [{ value: '', label: latestLabel }, ...records]
}

/** The liquid runs the last `scan-case-status` found. */
export function knownLiquidRuns(): unknown[] {
  return getCaseStatus()?.liquidRuns ?? []
}

/** Which run the selector is pointing at; '' means the newest one. */
let selectedRun = ''

export function selectedLiquidRun(): string {
  return selectedRun
}

export function setSelectedLiquidRun(run: string) {
  selectedRun = run
}
