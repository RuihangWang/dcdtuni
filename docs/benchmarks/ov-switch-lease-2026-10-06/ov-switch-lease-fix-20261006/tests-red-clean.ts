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


// Timelines below retain the installed 5.18.2 public SDK semantics: init's
// inProgress promise and terminal onStart are independent; terminate while
// connecting only marks cancelled, and real termination clears the controller.
function receipt() {
  const event = deferred()
  return { settled: false, done: event.promise, settle() { this.settled = true; event.resolve() } }
}
it('does not orphan a controller by terminating before initialization finishes', async () => {
  vi.useFakeTimers()
  try {
    const initialization = deferred(), startEvent = receipt()
    let controller = true, status = 'none', initialized = false
    const terminate = vi.fn(async () => {
      if (status === 'connecting') { status = 'cancelled'; return {status: 'inProgress'} }
      // Installed Direct teardown can clear _stream before its await continuation.
      controller = false; status = 'none'; return {status: 'success'}
    })
    const stop = terminateOvViewer(terminate, () => status === 'none', initialization.promise, 1000, 10, startEvent)
    const guarded = stop.then(() => ({ok:true}), error => ({ok:false,error}))
    await vi.advanceTimersByTimeAsync(20)
    expect(terminate).not.toHaveBeenCalled()
    initialized = true; status = 'connected'; startEvent.settle(); initialization.resolve()
    await vi.advanceTimersByTimeAsync(20); expect(await guarded).toEqual({ok:true})
    expect(initialized && !controller).toBe(true)
    expect(terminate).toHaveBeenCalledOnce()
  } finally { vi.useRealTimers() }
})
it('finishes stale connecting after onStart already succeeded and inProgress arrived late', async () => {
  vi.useFakeTimers()
  try {
    const startEvent = receipt(); startEvent.settle()
    let status = 'connecting', controller = true
    const terminate = vi.fn(async () => {
      if (status === 'connecting') { status = 'cancelled'; return {status: 'inProgress'} }
      controller = false; status = 'none'; return {status: 'success'}
    })
    const stop = terminateOvViewer(terminate, () => status === 'none', Promise.resolve({status:'inProgress'}), 1000, 10, startEvent)
    const guarded = stop.then(() => ({ok:true}), error => ({ok:false,error}))
    await vi.advanceTimersByTimeAsync(1100); expect(await guarded).toEqual({ok:true})
    expect(status).toBe('none'); expect(controller).toBe(false)
    expect(terminate).toHaveBeenCalledTimes(2)
  } finally { vi.useRealTimers() }
})
it('keeps cancellation through late init then waits for the SDK terminal callback', async () => {
  vi.useFakeTimers()
  try {
    const initialization = deferred(), startEvent = receipt()
    let status = 'none', done = false
    const terminate = vi.fn(async () => {
      if (status === 'connecting') { status = 'cancelled'; return {status:'inProgress'} }
      status = 'none'; return {status:'success'}
    })
    const stop = terminateOvViewer(terminate, () => status === 'none', initialization.promise, 1000, 10, startEvent)
    const guarded = stop.then(() => ({ok:true}), error => ({ok:false,error})); void stop.then(() => { done = true }, () => {})
    await vi.advanceTimersByTimeAsync(20)
    expect(terminate).not.toHaveBeenCalled()
    status = 'connecting'; initialization.resolve()
    await vi.advanceTimersByTimeAsync(20)
    expect(status).toBe('cancelled'); expect(done).toBe(false)
    // The real SDK wrapper receives canceled and begins its own public terminate.
    status = 'none'; startEvent.settle()
    await vi.advanceTimersByTimeAsync(20); expect(await guarded).toEqual({ok:true})
    expect(terminate).toHaveBeenCalledOnce(); expect(done).toBe(true)
  } finally { vi.useRealTimers() }
})
it('does not trust none while a terminal start callback is still pending', async () => {
  vi.useFakeTimers()
  try {
    const startEvent = receipt(); let status = 'connected', done = false
    const terminate = vi.fn(async () => { status = 'none'; return {status:'success'} })
    const stop = terminateOvViewer(terminate, () => status === 'none', Promise.resolve(), 1000, 10, startEvent)
    const guarded = stop.then(() => ({ok:true}), error => ({ok:false,error})); void stop.then(() => { done = true }, () => {})
    await vi.advanceTimersByTimeAsync(20)
    expect(done).toBe(false)
    startEvent.settle(); await vi.advanceTimersByTimeAsync(20); expect(await guarded).toEqual({ok:true})
    expect(done).toBe(true)
  } finally { vi.useRealTimers() }
})
it('does not overlap a retry with SDK automatic termination after canceled onStart', async () => {
  vi.useFakeTimers()
  try {
    const startEvent = receipt(); let status = 'connecting', sdkTerminating = false, destroyed = 0
    const terminate = vi.fn(async () => {
      if (sdkTerminating) throw {action:'terminate',status:'error',info:'Stream is already terminating.'}
      if (status === 'connecting') { status = 'cancelled'; return {status:'inProgress'} }
      destroyed++; status = 'none'; return {status:'success'}
    })
    const stop = terminateOvViewer(terminate, () => status === 'none', Promise.resolve(), 1000, 10, startEvent)
    const guarded = stop.then(() => ({ok:true}), error => ({ok:false,error}))
    await vi.advanceTimersByTimeAsync(20)
    sdkTerminating = true; startEvent.settle()
    await vi.advanceTimersByTimeAsync(20)
    // The already-terminating response never creates a second physical teardown.
    expect(destroyed).toBe(0)
    sdkTerminating = false; status = 'none'
    await vi.advanceTimersByTimeAsync(20); expect(await guarded).toEqual({ok:true})
    expect(terminate).toHaveBeenCalledTimes(2)
  } finally { vi.useRealTimers() }
})
it('cleans up a rejected initialization even without an onStart receipt', async () => {
  vi.useFakeTimers()
  try {
    const startEvent = receipt(); let status = 'error'
    const terminate = vi.fn(async () => { status = 'none'; return {status:'success'} })
    const stop = terminateOvViewer(terminate, () => status === 'none', Promise.reject(new Error('init')), 1000, 10, startEvent)
    const guarded = stop.then(() => ({ok:true}), error => ({ok:false,error}))
    await vi.advanceTimersByTimeAsync(20); expect(await guarded).toEqual({ok:true})
    expect(terminate).toHaveBeenCalledOnce(); expect(startEvent.settled).toBe(false)
  } finally { vi.useRealTimers() }
})
it('keeps a hung initialization blocked without later destructive calls', async () => {
  vi.useFakeTimers()
  try {
    const initialization = deferred(), startEvent = receipt(), terminate = vi.fn(async () => {})
    const leases = new OvStreamLeases()
    const first = leases.open(() => initialization.promise, started => terminateOvViewer(terminate, () => false, started, 100, 10, startEvent))
    await Promise.resolve()
    const closing = first.close(), guarded = expect(closing).rejects.toThrow('did not reach idle')
    const nextStart = vi.fn(async () => {}), next = leases.open(nextStart, async () => {})
    const queued = expect(next.ready).rejects.toThrow('did not reach idle')
    await vi.advanceTimersByTimeAsync(100); expect(await guarded).toEqual({ok:true}); await queued
    initialization.resolve(); await first.ready
    await vi.advanceTimersByTimeAsync(20); await next.close()
    expect(terminate).not.toHaveBeenCalled(); expect(nextStart).not.toHaveBeenCalled()
  } finally { vi.useRealTimers() }
})
