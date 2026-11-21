import { describe, expect, it } from 'vitest'
import { getLog } from '../src/projectStore'
import { ppLog, volumeSummary } from '../src/postprocess/ppLog'

/**
 * Post-processing's Operation Log lines.
 *
 * The bottom log is the documented debugging surface, and post-processing was
 * the one long action that never wrote to it: the panel's status text is
 * overwritten by the next request, so what a slice or a volume actually
 * returned was unrecoverable a minute later.
 */

describe('ppLog', () => {
  it('records under the legacy PP channel, newest first', () => {
    ppLog('slice #1 added')
    const [entry] = getLog()
    expect(entry?.action).toBe('PP')
    expect(entry?.detail).toBe('slice #1 added')
  })

  it('keeps the AI panel on its own channel', () => {
    ppLog('inference finished (41200 cells, 1.8s)', 'PP AI')
    const [entry] = getLog()
    expect(entry?.action).toBe('PP AI')
  })
})

describe('volumeSummary', () => {
  it('names the grid and the range it spans', () => {
    expect(volumeSummary([128, 128, 128], 'T', 21.04, 37.21)).toBe('128x128x128, T: 21.0~37.2')
  })

  it('survives a header that reported fewer than three dimensions', () => {
    // A malformed header must not take the whole render down with it — the
    // grid is already built and drawable by the time this line is composed.
    expect(volumeSummary([64], 'p', -1.5, 2.25)).toBe('64x0x0, p: -1.50~2.25')
  })
})

it('keeps a small velocity range distinct in the completed volume log', () => {
  expect(volumeSummary([64, 64, 64], 'Umag', 0.002, 0.005780115723609924))
    .toBe('64x64x64, Umag: 0.00200~0.00578')
})
