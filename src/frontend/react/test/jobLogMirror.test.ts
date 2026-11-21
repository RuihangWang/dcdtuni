import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as store from '../src/projectStore'
import {
  flushJobLogForTest,
  jobLogFinish,
  jobLogLine,
  jobLogStart,
} from '../src/run/jobLogMirror'

/**
 * The dock's copy of a job log arrives in BATCHES.
 *
 * A mesh run prints tens of thousands of lines in bursts; one log entry per
 * line would push that many store notifications and DOM rows through the bottom
 * dock. So lines accumulate and flush as one multi-line entry, on a timer or
 * when the buffer gets big — and the run's verdict lands AFTER the lines it
 * verdicts, never above them.
 */

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  flushJobLogForTest()
  vi.useRealTimers()
})

/** The dock is newest-first, so the last thing logged is getLog()[0]. */
const logged = () => store.getLog().map((e) => ({ action: e.action, detail: e.detail }))

describe('jobLogMirror', () => {
  it('opens a lane with a start entry', () => {
    jobLogStart('mesh', 'job_42')
    expect(logged()[0]!.action).toBe('dctwin')
    expect(logged()[0]!.detail).toContain('job_42')
  })

  it('holds lines until the flush tick, then writes them as ONE entry', () => {
    jobLogStart('solve', 'job_7')
    jobLogLine('solve', 'line one')
    jobLogLine('solve', 'line two')
    expect(logged().filter((e) => e.detail.includes('line one'))).toHaveLength(0)
    vi.advanceTimersByTime(250)
    const batch = logged().find((e) => e.detail.includes('line one'))
    expect(batch).toBeDefined()
    expect(batch!.detail).toBe('line one\nline two')
    expect(logged().filter((e) => e.detail.includes('line two'))).toHaveLength(1)
  })

  it('flushes immediately once the buffer is a burst', () => {
    jobLogStart('mesh', 'job_9')
    for (let i = 0; i < 250; i++) jobLogLine('mesh', `l${i}`)
    // No timer tick needed: 250 buffered lines flush on the spot.
    expect(logged().some((e) => e.detail.includes('l249'))).toBe(true)
  })

  it('puts the verdict below the lines it verdicts', () => {
    jobLogStart('mesh', 'job_1')
    jobLogLine('mesh', 'final snappy line')
    jobLogFinish('mesh', 'done rc=0')
    const log = logged()
    // Newest first: the finish entry sits ABOVE the batch it closed.
    expect(log[0]!.detail).toBe('done rc=0')
    expect(log[1]!.detail).toBe('final snappy line')
  })

  it('keeps two kinds in their own lanes', () => {
    jobLogStart('mesh', 'job_1')
    jobLogLine('mesh', 'mesh line')
    jobLogLine('checkMesh', 'check line')
    flushJobLogForTest()
    const meshEntry = logged().find((e) => e.detail === 'mesh line')
    const checkEntry = logged().find((e) => e.detail === 'check line')
    // Both kinds share the legacy terminal's single [dctwin] tag.
    expect(meshEntry!.action).toBe('dctwin')
    expect(checkEntry!.action).toBe('dctwin')
  })
})
