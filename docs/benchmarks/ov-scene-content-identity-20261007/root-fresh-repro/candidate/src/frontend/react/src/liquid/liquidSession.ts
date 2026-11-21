/**
 * Driving a liquid-cooling solve from the browser.
 *
 * This is a DIFFERENT mechanism from the air-cooling jobs in `session.ts`, and
 * the two must not be merged:
 *
 *   air     POST /api/run-mesh -> jobId, then GET /api/log-stream?job_id=,
 *           cancelled server-side with POST /api/abort-job
 *   liquid  POST /api/liquid/solve, which streams the progress ITSELF as SSE
 *           on the response body, cancelled CLIENT-side by aborting the fetch
 *
 * The liquid solve runs in-process in the server, so there is no job id and no
 * separate log stream to attach to. Cancelling means dropping the connection —
 * `stopLiquidSolve` (js/liquid_cooling.js:274) does exactly that.
 *
 * Progress arrives as `stage` values from `backend/liquidcooling/runner.py`:
 * compiling -> validated -> compiled -> model_built -> torch_compiled ->
 * solving (repeatedly, carrying epoch/loss) -> done, or error at any point.
 */

export type LiquidStage =
  | 'compiling'
  | 'validated'
  | 'compiled'
  | 'model_built'
  | 'torch_compiled'
  | 'solving'
  | 'done'
  | 'error'

export interface LiquidProgress {
  stage: LiquidStage
  [key: string]: unknown
}

export interface LiquidRunState {
  running: boolean
  /** The user pressed stop. An aborted fetch is then expected, not a failure. */
  aborted: boolean
  stage: LiquidStage | null
  /** Every SSE payload, in order — the monitor panel plots from these. */
  progress: LiquidProgress[]
  /** The `done` payload plus whatever /api/liquid/results adds to it. */
  results: unknown | null
  error: string | null
}

const EMPTY: LiquidRunState = {
  running: false,
  aborted: false,
  stage: null,
  progress: [],
  results: null,
  error: null,
}

let state: LiquidRunState = EMPTY
let controller: AbortController | null = null
// Result reads can overlap across selection, refresh and project loading. Only
// the latest requested read in the current workspace may publish its result.
let resultRequestGeneration = 0

type Listener = () => void
const listeners = new Set<Listener>()

function emit() {
  listeners.forEach((fn) => fn())
}

function set(patch: Partial<LiquidRunState>) {
  state = { ...state, ...patch }
  emit()
}

export function subscribeLiquid(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function liquidRunState(): LiquidRunState {
  return state
}

/** Reset to the pre-run state. Used when a project is loaded. */
export function resetLiquidRun() {
  resultRequestGeneration += 1
  controller = null
  state = EMPTY
  emit()
}

/**
 * Export the liquid graph to `<case>/model/*.liquidrun`.
 *
 * Separate from solving because the legacy flow exports first and bails out if
 * the graph cannot be built — solving a stale `.liquidrun` after a failed
 * export would silently run the PREVIOUS network.
 */
export async function exportLiquidrun(graph: unknown): Promise<boolean> {
  const resp = await fetch('/api/liquid/export-liquidrun', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(graph),
  })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      detail = ((await resp.json()) as { error?: string }).error ?? detail
    } catch {
      /* body was not JSON */
    }
    set({ error: detail })
    return false
  }
  return true
}

/** Parse one SSE frame body. Returns null for anything that is not `data:`. */
function parseFrame(line: string): LiquidProgress | null {
  const trimmed = line.trim()
  if (!trimmed.startsWith('data: ')) return null
  try {
    return JSON.parse(trimmed.slice(6)) as LiquidProgress
  } catch {
    // A truncated frame is normal at a chunk boundary; the caller keeps the
    // tail in its buffer and this line will be re-parsed whole next time.
    return null
  }
}

export interface RunLiquidOptions {
  device: string
  monitorCps?: string[]
  onProgress?: (p: LiquidProgress) => void
}

/**
 * Run the solve, streaming progress until it finishes or is stopped.
 *
 * Resolves with the final results on success and null otherwise — including a
 * user stop, which is not an error.
 */
export async function runLiquidSolve(opts: RunLiquidOptions): Promise<unknown | null> {
  if (state.running) throw new Error('a liquid solve is already running')

  controller = new AbortController()
  set({ running: true, aborted: false, stage: null, progress: [], results: null, error: null })

  try {
    const resp = await fetch('/api/liquid/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device: opts.device, monitor_cps: opts.monitorCps ?? [] }),
      signal: controller.signal,
    })
    if (!resp.ok || !resp.body) {
      const detail = resp.ok ? 'server sent no response body' : await resp.text()
      set({ running: false, error: detail })
      return null
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let last: LiquidProgress | null = null

    for (;;) {
      const chunk = await reader.read()
      if (chunk.done) break
      buffer += decoder.decode(chunk.value, { stream: true })
      const lines = buffer.split('\n')
      // The final element is whatever came after the last newline — possibly
      // half a frame. Keep it buffered rather than parsing it.
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        const payload = parseFrame(line)
        if (!payload) continue
        last = payload
        state = { ...state, stage: payload.stage, progress: [...state.progress, payload] }
        emit()
        opts.onProgress?.(payload)
      }
    }

    if (last?.stage === 'done') {
      // The streamed `done` frame is a summary; the full field data is fetched
      // separately. Fall back to the summary when that call fails, so a
      // successful solve is never reported as having produced nothing.
      let results: unknown = last
      try {
        const full = await fetch('/api/liquid/results')
        if (full.ok) results = await full.json()
      } catch {
        /* keep the summary */
      }
      set({ running: false, results })
      return results
    }
    if (last?.stage === 'error') {
      set({ running: false, error: String(last.message ?? 'unknown solver error') })
      return null
    }
    set({ running: false, error: 'the solve stream ended before reporting a result' })
    return null
  } catch (e) {
    const aborted = e instanceof DOMException && e.name === 'AbortError'
    set({
      running: false,
      aborted,
      error: aborted ? null : e instanceof Error ? e.message : String(e),
    })
    return null
  } finally {
    controller = null
  }
}

/** Stop a running solve by dropping the connection. */
export function stopLiquidSolve() {
  if (!controller) return
  controller.abort()
  controller = null
  set({ running: false, aborted: true })
}

/**
 * Fetch the results of a previous solve without running one.
 * Selectors may validate a document before it replaces the current result.
 */
export async function fetchLiquidResults(
  runName?: string,
  acceptResult?: (data: unknown) => boolean,
): Promise<unknown | null> {
  const requestGeneration = ++resultRequestGeneration
  const url = runName
    ? `/api/liquid/results?run=${encodeURIComponent(runName)}`
    : '/api/liquid/results'
  try {
    const resp = await fetch(url)
    if (!resp.ok) return null
    const data: unknown = await resp.json()
    if (requestGeneration !== resultRequestGeneration) return null
    if (acceptResult && !acceptResult(data)) return null
    set({ results: data })
    return data
  } catch {
    return null
  }
}
