import { expect, it, vi } from 'vitest'
import { OvStreamLeases, terminateOvViewer } from '../src/renderer/ovStreamLease'
function deferred() { let resolve!: () => void; const promise = new Promise<void>(r => { resolve = r }); return {promise, resolve} }
it('waits for the previous singleton viewer to terminate before reconnecting', async () => {
  const leases = new OvStreamLeases(), terminating = deferred(), events: string[] = []
  const first = leases.open(async () => { events.push('connect 1') }, async () => { events.push('stop 1'); await terminating.promise })
  await first.ready
  const closing = first.close()
  const second = leases.open(async () => { events.push('connect 2') }, async () => { events.push('stop 2') })
  await Promise.resolve(); await Promise.resolve()
  expect(events).toEqual(['connect 1', 'stop 1'])
  terminating.resolve(); await closing; await second.ready
  expect(events).toEqual(['connect 1', 'stop 1', 'connect 2'])
  await second.close()
})
it('never connects or terminates a queued viewport cancelled before ownership', async () => {
  const leases = new OvStreamLeases(), terminating = deferred()
  const first = leases.open(async () => {}, () => terminating.promise)
  await first.ready; const closing = first.close()
  const start = vi.fn(async () => {}), stop = vi.fn(async () => {})
  const cancelled = leases.open(start, stop); const cancelledClose = cancelled.close()
  terminating.resolve(); await closing; await cancelledClose; await cancelled.ready
  expect(start).not.toHaveBeenCalled(); expect(stop).not.toHaveBeenCalled()
  const nextStart = vi.fn(async () => {})
  const next = leases.open(nextStart, async () => {}); await next.ready
  expect(nextStart).toHaveBeenCalledOnce(); await next.close()
})

it('holds the singleton across the SDK connecting cancellation until real idle', async () => {
  vi.useFakeTimers()
  try {
    const leases = new OvStreamLeases(), events: string[] = []
    // Same lifecycle as the installed SDK: terminate while connecting changes
    // streamStatus to cancelled and resolves inProgress; onStart(canceled)
    // triggers the actual termination, whose completion finally sets none.
    let status = 'connecting'
    const first = leases.open(async () => { events.push('connect 1') },
      started => terminateOvViewer(async () => {
        events.push('cancel connecting'); status = 'cancelled'
        return { status: 'inProgress' }
      }, () => status === 'none', started, 1000, 10))
    await first.ready
    const closing = first.close()
    const next = leases.open(async () => { events.push('connect 2') }, async () => {})
    await vi.advanceTimersByTimeAsync(100)
    expect(events).toEqual(['connect 1', 'cancel connecting'])
    status = 'none' // actual SDK termination completed and cleared _stream
    await vi.advanceTimersByTimeAsync(10)
    await closing; await next.ready
    expect(events).toEqual(['connect 1', 'cancel connecting', 'connect 2'])
    await next.close()
  } finally { vi.useRealTimers() }
})
it('waits for a late connect promise as well as a successful termination', async () => {
  vi.useFakeTimers()
  try {
    const starting = deferred()
    let idle = true
    let done = false
    const stopped = terminateOvViewer(async () => ({ status: 'success' }), () => idle, starting.promise, 1000, 10)
    void stopped.then(() => { done = true })
    await vi.advanceTimersByTimeAsync(100)
    expect(done).toBe(false)
    idle = false // a late connect updated the singleton after terminate
    starting.resolve()
    await vi.advanceTimersByTimeAsync(100)
    expect(done).toBe(false)
    idle = true
    await vi.advanceTimersByTimeAsync(10); await stopped
    expect(done).toBe(true)
  } finally { vi.useRealTimers() }
})
it('blocks queued and future connections when cancellation never confirms idle', async () => {
  vi.useFakeTimers()
  try {
    const leases = new OvStreamLeases()
    const first = leases.open(async () => {}, started => terminateOvViewer(
      async () => ({ status: 'inProgress' }), () => false, started, 100, 10))
    await first.ready
    const closing = first.close()
    const closeFailure = expect(closing).rejects.toThrow('did not reach idle')
    const start = vi.fn(async () => {})
    const queued = leases.open(start, async () => {})
    const queuedFailure = expect(queued.ready).rejects.toThrow('did not reach idle')
    await vi.advanceTimersByTimeAsync(100)
    await closeFailure; await queuedFailure; await queued.close()
    const future = leases.open(start, async () => {})
    await expect(future.ready).rejects.toThrow('did not reach idle')
    await future.close()
    expect(start).not.toHaveBeenCalled()
  } finally { vi.useRealTimers() }
})
