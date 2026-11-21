/**
 * The case's saved liquid runs and heat ledger, fetched once and shared by
 * every view of the hybrid workflow (top-bar popover, liquid solver panel,
 * air solver panel). Read-only; a refresh replaces both.
 */
import { fetchHybridHeatLedger, listSavedLiquidRuns, type HybridHeatLedgerResponse, type SavedLiquidRun } from './liquidSavedRuns'

export interface HybridData {
  runs: SavedLiquidRun[] | null
  caseName: string | null
  ledger: HybridHeatLedgerResponse | null
  loading: boolean
  error: string | null
  /** Date.now() of the last finished refresh, 0 before the first. */
  at: number
  /**
   * Whether the case has an accepted air mesh, published by the app shell
   * from the run session. Kept here so the workflow views do not import the
   * session module, which acts on the server when it loads.
   */
  meshReady: boolean
}

const EMPTY: HybridData = { runs: null, caseName: null, ledger: null, loading: false, error: null, at: 0, meshReady: false }
let data: HybridData = EMPTY
let inFlight: Promise<void> | null = null
let generation = 0
const listeners = new Set<() => void>()

export function hybridData(): HybridData { return data }
export function subscribeHybridData(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
function update(patch: Partial<HybridData>) { data = { ...data, ...patch }; listeners.forEach(f => f()) }

/** Fetch both again; concurrent callers share one request. */
export function refreshHybridData(): Promise<void> {
  if (inFlight) return inFlight
  const token = generation
  update({ loading: true })
  inFlight = (async () => {
    const [runs, ledger] = await Promise.allSettled([listSavedLiquidRuns(), fetchHybridHeatLedger()])
    if (token !== generation) return
    const errors = [runs, ledger].filter((r): r is PromiseRejectedResult => r.status === 'rejected')
      .map(r => r.reason instanceof Error ? r.reason.message : String(r.reason))
    update({
      runs: runs.status === 'fulfilled' ? runs.value.runs : data.runs,
      caseName: runs.status === 'fulfilled' ? runs.value.case : data.caseName,
      ledger: ledger.status === 'fulfilled' ? ledger.value : data.ledger,
      loading: false, error: errors.length ? errors.join('; ') : null, at: Date.now(),
    })
  })().finally(() => { inFlight = null })
  return inFlight
}

/** Refresh unless the data is younger than `maxAgeMs`. */
export function ensureHybridData(maxAgeMs = 5000): void {
  if (!inFlight && Date.now() - data.at > maxAgeMs) void refreshHybridData()
}

/** The app shell's mesh readiness, for step 3. */
export function setHybridMeshReady(meshReady: boolean): void {
  if (data.meshReady !== meshReady) update({ meshReady })
}

/** Forget the case's runs and ledger (a new project or case). */
export function resetHybridData(): void {
  generation++
  inFlight = null
  data = { ...EMPTY, meshReady: data.meshReady }
  listeners.forEach(f => f())
}
