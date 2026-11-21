/**
 * Reading the AI worker's replies for the status line.
 *
 * `/api/pp/ai/start` and `/api/pp/ai/infer` forward whatever the worker put in
 * its `status` and `result` objects, and the parts the status line needs —
 * which device the model loaded on, how big the warm-up was, how many cells the
 * run actually inferred and how long it took — are NOT in the response types
 * the fetch helpers declare. They are read here from `unknown` rather than
 * added to those types, so a worker that stops sending one of them degrades to
 * a missing figure instead of an exception.
 *
 * The numbers themselves come from `pp_ai_worker.py`: `selectedCount` is the
 * pivot-node count (line 432) and `inferSeconds` the wall time of the forward
 * pass (line 450).
 */

interface Dict {
  [key: string]: unknown
}

function asDict(value: unknown): Dict | null {
  return value && typeof value === 'object' ? (value as Dict) : null
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** `cuda:0` / `cpu`, or '' when the worker did not say. */
export function aiDevice(response: unknown): string {
  const status = asDict(asDict(response)?.status)
  const device = status?.device
  return typeof device === 'string' ? device : ''
}

/** The warm-up inference the worker runs at start, or null if it reported none. */
export function aiWarmup(response: unknown): { count: number; seconds: number } | null {
  const warm = asDict(asDict(asDict(response)?.result)?.warmup)
  if (!warm) return null
  return { count: asNumber(warm.selectedCount) ?? 0, seconds: asNumber(warm.inferSeconds) ?? 0 }
}

/**
 * How many cells a finished inference covered, and how long it took.
 *
 * The panel used to report `Object.keys(result.ranges).length` here, which is
 * the number of FIELDS the worker returned ranges for — three — for a run that
 * inferred tens of thousands of cells.
 */
export function aiInferStats(response: unknown): { cells: number; seconds: number } {
  const result = asDict(asDict(response)?.result)
  return {
    cells: asNumber(result?.selectedCount) ?? 0,
    seconds: asNumber(result?.inferSeconds) ?? 0,
  }
}

/**
 * The worker-ready line: the message, the device it loaded on, the warm-up.
 *
 * Same assembly as STL_generate.html:20630-20633 — ` [cuda:0]` appended in
 * brackets, then the warm-up phrase, which carries its own leading separator in
 * both dictionaries.
 */
export function aiReadyMessage(base: string, device: string, warmup: string | null): string {
  return `${base}${device ? ` [${device}]` : ''}${warmup ?? ''}`
}

/** True when a box has no volume — the server reads one as an empty region. */
export function aiBoxIsEmpty(box: { min: readonly number[]; max: readonly number[] }): boolean {
  for (let i = 0; i < 3; i++) {
    const lo = box.min[i]
    const hi = box.max[i]
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return true
    if ((hi as number) - (lo as number) === 0) return true
  }
  return false
}
