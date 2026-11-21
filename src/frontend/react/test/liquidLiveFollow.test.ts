import { afterEach, describe, expect, it, vi } from 'vitest'
import { liveFollowRun, startLiquidLiveFollow, LIVE_FOLLOW_MS } from '../src/liquid/liquidLiveFollow'

const running = (id = 'abc', status = 'running') => ({ session: { run_id: id, status }, frames: [{ seq: 0 }, { seq: 1 }] })

describe('which run the 3D view follows while an experiment runs', () => {
  it('follows a running or paused run shown by the newest-run selector or by its own entry', () => {
    const base = { live: true, selector: '', ovMode: 'three' as const, ovLiquidActive: false }
    expect(liveFollowRun(running(), base)).toBe('abc')
    expect(liveFollowRun(running('abc', 'paused'), base)).toBe('abc')
    expect(liveFollowRun(running(), { ...base, selector: 'run-abc' })).toBe('abc')
    // A picked record is its directory's path (liquidRunOptions).
    expect(liveFollowRun(running(), { ...base, selector: '/case/X/Liquid/log/run-abc' })).toBe('abc')
    expect(liveFollowRun(running(), { ...base, selector: '/case/X/Liquid/log/run-abcd' })).toBeNull()
    // A historical run the user picked stays shown; finished runs are liquidRunFollow's.
    expect(liveFollowRun(running(), { ...base, selector: 'run-old' })).toBeNull()
    expect(liveFollowRun(running('abc', 'completed'), base)).toBeNull()
    expect(liveFollowRun({ session: null, frames: [] }, base)).toBeNull()
    expect(liveFollowRun(running(), { ...base, live: false })).toBeNull()
  })

  it('never makes Omniverse resend its scene: in OV only while Kit colours the result itself', () => {
    const ov = { live: true, selector: '', ovMode: 'ov' as const, ovLiquidActive: false }
    expect(liveFollowRun(running(), ov)).toBeNull()
    expect(liveFollowRun(running(), { ...ov, ovLiquidActive: true })).toBe('abc')
  })
})

describe('live follow', () => {
  afterEach(() => { vi.useRealTimers() })

  it('re-reads the running result at most every LIVE_FOLLOW_MS, one read at a time', async () => {
    vi.useFakeTimers()
    let state = running()
    const listeners = new Set<() => void>()
    const reads: string[] = []
    let release: (() => void) | null = null
    const stop = startLiquidLiveFollow({
      runtime: () => state, subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn) },
      context: () => ({ live: true, selector: '', ovMode: 'three', ovLiquidActive: false }),
      fetch: async run => { reads.push(run); await new Promise<void>(r => { release = r }) },
    })
    const frame = (n: number) => { state = { ...state, frames: Array.from({ length: n }, (_, i) => ({ seq: i })) }; listeners.forEach(fn => fn()) }
    frame(3)
    await vi.advanceTimersByTimeAsync(0)
    expect(reads).toEqual(['run-abc'])
    frame(4); frame(5)
    await vi.advanceTimersByTimeAsync(LIVE_FOLLOW_MS * 2)
    expect(reads).toHaveLength(1) // the first read has not returned
    release!()
    await vi.advanceTimersByTimeAsync(LIVE_FOLLOW_MS)
    expect(reads).toHaveLength(2)
    release!()
    // No new frame: no further read.
    await vi.advanceTimersByTimeAsync(LIVE_FOLLOW_MS * 3)
    expect(reads).toHaveLength(2)
    stop()
    frame(9)
    await vi.advanceTimersByTimeAsync(LIVE_FOLLOW_MS * 3)
    expect(reads).toHaveLength(2)
  })
})
