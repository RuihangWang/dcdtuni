/**
 * Saved liquid runs of the selected case and the hybrid heat ledger.
 *
 * Read-only views of what the server already wrote to disk:
 *   GET /api/liquid/runs              every saved run, newest first
 *   GET /api/liquid/runs/<id>/saved   one run's header, metadata and receipt
 *   GET /api/hybrid/heat-ledger       model/heat-ledger.json of the case
 * Taking a saved run over as the live session is restoreRuntimeSession
 * (liquidRuntimeClient.ts).
 */

export interface SavedLiquidRun {
  /** Directory name, also the `?run=` key of /api/liquid/results. */
  name: string
  run_id: string | null
  /** dae: restorable runtime run; runtime: legacy runtime run; legacy: batch result. */
  kind: 'dae' | 'runtime' | 'legacy'
  location: 'Liquid/log' | 'log'
  modified: number | null
  status: string | null
  error: unknown
  solver: string | null
  analysis: { mode?: string; initialization?: string } | null
  samples: { accepted: number | null; total: number | null }
  dt_s: number | null
  accepted_time_s: number | null
  config_hash: string | null
  project_revision: number | null
  source_project: { project_uid?: string; sha256?: string; schema_version?: number; project_name?: string } | null
  execution: { device?: string; dtype?: string; max_threads?: number } | null
  restorable: boolean
  restore_blocker: string | null
  /** Live state when this server holds the run in memory. */
  loaded: { status: string; read_only: boolean } | null
}

export interface SavedLiquidRunList {
  case: string
  runs: SavedLiquidRun[]
}

export interface SavedLiquidRunDetail {
  run: SavedLiquidRun
  metadata: Record<string, unknown> | null
  receipt: Record<string, unknown> | null
  artifacts: Record<string, boolean>
}

export interface HybridHeatLedgerTotals {
  total_it_W: number
  cp_input_W: number
  liquid_removed_W: number
  solid_storage_rate_W: number
  air_residual_W: number
  face_heat_W?: number
}

export interface HybridHeatLedger {
  format: string
  liquid_run_id?: string
  sample_index?: number
  time_s?: number
  heat_multiplier?: number
  totals: HybridHeatLedgerTotals
  servers?: Record<string, unknown>[]
  [key: string]: unknown
}

export interface HybridHeatLedgerResponse {
  path: string
  modified: number
  active_cfdrun: { filename: string; run_id: string | null } | null
  /** False means the ledger and the active .cfdrun were edited apart. */
  matches_active_cfdrun: boolean | null
  ledger: HybridHeatLedger
}

const RUN_ID = /^[A-Za-z0-9_-]{1,64}$/

async function getJson(path: string): Promise<{ status: number; body: Record<string, unknown> }> {
  const response = await fetch(path)
  let body: Record<string, unknown> = {}
  try { body = await response.json() } catch { /* keep the status */ }
  if (!response.ok && response.status !== 404) {
    throw new Error(typeof body.error === 'string' ? body.error : `Request failed (${response.status})`)
  }
  return { status: response.status, body }
}

export async function listSavedLiquidRuns(): Promise<SavedLiquidRunList> {
  const { status, body } = await getJson('/api/liquid/runs')
  if (status === 404) throw new Error(typeof body.error === 'string' ? body.error : 'Saved liquid runs are unavailable')
  if (!Array.isArray(body.runs)) throw new Error('Saved liquid run listing has no runs')
  return body as unknown as SavedLiquidRunList
}

export async function readSavedLiquidRun(runId: string): Promise<SavedLiquidRunDetail> {
  if (!RUN_ID.test(runId)) throw new Error('Invalid liquid run id')
  const { status, body } = await getJson(`/api/liquid/runs/${encodeURIComponent(runId)}/saved`)
  if (status === 404) throw new Error(typeof body.error === 'string' ? body.error : `No saved liquid run ${runId}`)
  return body as unknown as SavedLiquidRunDetail
}

/** The ledger of the case, or null when no hybrid air run has been published. */
export async function fetchHybridHeatLedger(): Promise<HybridHeatLedgerResponse | null> {
  const { status, body } = await getJson('/api/hybrid/heat-ledger')
  if (status === 404) {
    if (body.code === 'heat_ledger_missing') return null
    throw new Error(typeof body.error === 'string' ? body.error : 'Heat ledger endpoint is unavailable')
  }
  const ledger = body.ledger as HybridHeatLedger | undefined
  if (!ledger || typeof ledger.format !== 'string' || !ledger.totals) throw new Error('Heat ledger response is malformed')
  return body as unknown as HybridHeatLedgerResponse
}
