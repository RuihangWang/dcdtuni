/**
 * The bottom dock's copy of a running job's log.
 *
 * The mesh and solve tabs keep their own scrollback (`session.getJob().lines`,
 * last 2000 lines) and draw the tail of it in a `<pre>` inside the panel. That
 * is the right home for watching one run, and the wrong one for the record: the
 * dock at the bottom is where this editor prints everything else — loads,
 * saves, edits, exports — and a run that only ever logged its final verdict
 * left the dock silent through the one operation that takes an hour.
 *
 * ## Why lines arrive in batches
 *
 * A mesh run prints tens of thousands of lines, in bursts of hundreds per
 * second while snappyHexMesh iterates. One `logOperation` per line would push
 * that many store notifications and that many DOM rows through the dock, which
 * is exactly the class of freeze the tree's windowing was added to remove. So
 * lines accumulate here and flush as ONE multi-line entry, at most every
 * `FLUSH_MS` or every `FLUSH_LINES` lines — a few dock rows per second however
 * fast the server prints.
 *
 * The entry's detail is the joined text and the dock renders it pre-wrapped, so
 * the log still reads as a log; only the granularity of one ROW changes.
 */

import { t } from '../i18n'
import * as store from '../projectStore'
import type { JobKind } from './session'

const FLUSH_MS = 200
const FLUSH_LINES = 250

let current: JobKind | null = null
let buffer: string[] = []
let timer: ReturnType<typeof setTimeout> | null = null

/**
 * Every job line lands under the legacy terminal's own tag, `[dctwin]`.
 *
 * The HTML editor writes the whole server stream through `_appendDctwinLogLine`
 * (STL_generate.html:30053), which stamps `[HH:MM:SS] [dctwin] <line>` in cyan —
 * that is the format this dock replicates, so a run reads identically in both
 * editors instead of growing a React-only `[mesh]`/`[solve]` dialect.
 */
const JOB_TAG = 'dctwin'

function flush(): void {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  if (!buffer.length) return
  const text = buffer.join('\n')
  buffer = []
  store.logOperation(JOB_TAG, text)
}

function push(line: string): void {
  buffer.push(line)
  if (buffer.length >= FLUSH_LINES) {
    flush()
    return
  }
  if (!timer) timer = setTimeout(flush, FLUSH_MS)
}

/** A run was submitted. Opens this job's lane in the dock. */
export function jobLogStart(kind: JobKind, jobId: string): void {
  flush()
  current = kind
  store.logOperation(JOB_TAG, t('log.job_started', { kind, id: jobId }))
}

/** One streamed line. Batched; see the module note. */
export function jobLogLine(kind: JobKind, line: string): void {
  if (current !== kind) {
    flush()
    current = kind
  }
  push(line)
}

/**
 * The run reported done (or the stream dropped for good). Flushes whatever is
 * buffered FIRST, so the verdict never lands above the lines it verdicts.
 */
export function jobLogFinish(kind: JobKind, detail: string): void {
  flush()
  if (current === kind) current = null
  store.logOperation(JOB_TAG, detail)
}

/** The user asked the server to stop the run. */
export function jobLogAbort(kind: JobKind): void {
  store.logOperation(JOB_TAG, t('log.job_aborted', { kind }))
}

/** Test seam: drain the batch without waiting for the timer. */
export function flushJobLogForTest(): void {
  flush()
}
