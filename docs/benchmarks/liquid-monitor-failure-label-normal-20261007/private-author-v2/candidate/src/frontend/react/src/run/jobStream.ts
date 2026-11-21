/**
 * A second reader of the running job's log.
 *
 * `session.ts` streams a job's log to drive the run buttons and the log `<pre>`,
 * and it keeps only the last 2000 lines. That is right for a scrollback and
 * wrong for the live solver monitor:
 *
 * - the convergence plot needs EVERY line, because a residual dropped off the
 *   front of the buffer is a hole in the curve, and a real solve prints far
 *   more than 2000 lines;
 * Rather than retain every solve line in the shared session store, this opens its own EventSource
 * on the same job. The server re-sends the log from line 0 to every subscriber
 * (`handle_log_stream`, server.py:3302) and runs on a ThreadingHTTPServer, so a
 * second reader costs one thread and loses nothing.
 */

import * as session from './session'
import type { JobKind } from './session'
import { isCasePathContextCurrent } from './caseDir'

export interface JobFinal {
  done: true
  returncode?: number
  checkMeshOk?: boolean | null
  failedChecks?: number | null
  meshWarnings?: unknown[]
}

export interface JobStreamHandler {
  onStart?: (kind: JobKind, jobId: string) => void
  onLine?: (kind: JobKind, line: string) => void
  onDone?: (kind: JobKind, final: JobFinal) => void
}

const handlers = new Set<JobStreamHandler>()

export function addJobStreamHandler(handler: JobStreamHandler): () => void {
  handlers.add(handler)
  return () => {
    handlers.delete(handler)
  }
}

/**
 * The kinds worth a second reader.
 *
 * Only the live solve chart needs every line. Mesh and checkMesh quality comes
 * from session's final state, including its polling fallback.
 */
const WATCHED_KINDS: JobKind[] = ['solve']

let watchedJobId: string | null = null
let source: EventSource | null = null
let installed = false

function closeStream() {
  source?.close()
  source = null
}

function open(kind: JobKind, jobId: string, caseContext: number) {
  closeStream()
  watchedJobId = jobId
  handlers.forEach((h) => h.onStart?.(kind, jobId))
  const es = new EventSource(`/api/log-stream?job_id=${encodeURIComponent(jobId)}`)
  source = es
  es.onmessage = (event) => {
    // A late event from a stream that has already been replaced would fold a
    // finished job's lines into the new job's chart.
    if (watchedJobId !== jobId || !isCasePathContextCurrent(caseContext)) {
      es.close()
      if (source === es) source = null
      return
    }
    let payload: unknown
    try {
      payload = JSON.parse(event.data as string)
    } catch {
      payload = event.data
    }
    if (typeof payload === 'string') {
      handlers.forEach((h) => h.onLine?.(kind, payload as string))
      return
    }
    const final = payload as JobFinal
    if (final?.done) {
      // Closed here, not left to the browser: the server ENDS the response
      // after the final event, and an EventSource that is not closed treats
      // that as a dropped connection and reconnects — replaying the entire log
      // into the chart a second time.
      es.close()
      if (source === es) source = null
      handlers.forEach((h) => h.onDone?.(kind, final))
    }
  }
  es.onerror = () => {
    es.close()
    if (source === es) source = null
  }
}

/**
 * Follow whatever job the session store is running.
 *
 * Idempotent: modules that need the stream call this at import time, and the
 * dev server re-executes a module on every hot reload.
 */
export function watchJobStream(): void {
  if (installed) return
  installed = true
  session.subscribeSession(() => {
    const job = session.getJob()
    if (!job) return
    if (job.id === watchedJobId) return
    if (!job.running) return
    if (!WATCHED_KINDS.includes(job.kind)) return
    if (!isCasePathContextCurrent(job.caseContext)) return
    open(job.kind, job.id, job.caseContext)
  })
}

/** Test seam: forget the current job so a fresh one is picked up. */
export function resetJobStream(): void {
  closeStream()
  watchedJobId = null
}
