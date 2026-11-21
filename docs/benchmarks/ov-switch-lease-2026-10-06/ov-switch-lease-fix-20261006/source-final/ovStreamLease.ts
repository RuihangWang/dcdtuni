/** The SDK initialization promise and terminal onStart event are independent.
 * A late inProgress initialization reply can overwrite connected/cancelled. */
export interface OvStartReceipt {
  readonly settled: boolean
  readonly done: Promise<void>
}
export function createOvStartReceipt() {
  let settled = false, finish!: () => void
  const done = new Promise<void>(resolve => { finish = resolve })
  return {
    get settled() { return settled }, done,
    record(message: unknown): boolean {
      const status = message && typeof message === 'object'
        ? (message as {status?: unknown}).status : undefined
      if (settled || (status !== 'success' && status !== 'error' && status !== 'canceled')) return false
      settled = true; finish(); return true
    },
  }
}

/** Hold the owner until initialization AND its terminal start event settle.
 * Never destroy a Direct controller while its initialization continuation can
 * still create a runtime. A connecting terminate reply only marks cancellation;
 * after terminal onStart it may need one serial public-API completion attempt. */
export function terminateOvViewer(
  terminate: () => Promise<unknown>,
  isIdle: () => boolean,
  started: Promise<unknown>,
  timeoutMs = 15000,
  pollMs = 50,
  startEvent?: OvStartReceipt,
): Promise<void> {
  return new Promise((resolve, reject) => {
    let startSettled = false, startFailed = false, stopSettled = false
    let failure: unknown, finished = false, attempts = 0, markedCancellation = false
    const cleanup = () => { clearInterval(poll); clearTimeout(deadline) }
    const check = () => {
      if (finished || !startSettled || !stopSettled) return
      try {
        if (startEvent && !startEvent.settled && !startFailed) return
        if (isIdle()) { finished = true; cleanup(); resolve(); return }
        // SDK onStart may have completed BEFORE the inProgress init reply.
        // In that ordering no future event can complete its canceled marker.
        // An SDK auto-termination may already be running: its public rejection
        // is retained while polling for the same owner's eventual idle receipt.
        if (markedCancellation && attempts === 1 && startEvent?.settled) stop()
      } catch (error) { finished = true; cleanup(); reject(error) }
    }
    const stop = () => {
      if (finished) return
      stopSettled = false; attempts++
      void Promise.resolve().then(() => { if (!finished) return terminate() }).then(
        reply => {
          if (finished) return
          if (reply && typeof reply === 'object' && (reply as {status?:unknown}).status === 'inProgress')
            markedCancellation = true
          stopSettled = true; check()
        },
        error => { if (!finished) { failure = error; stopSettled = true; check() } },
      )
    }
    const poll = setInterval(check, pollMs)
    const deadline = setTimeout(() => {
      finished = true; cleanup()
      const detail = failure && typeof failure === 'object' && 'info' in failure
        ? String((failure as {info:unknown}).info) : failure ? String(failure) : ''
      reject(new Error('Omniverse viewer termination did not reach idle; reload the page before reconnecting' +
        ` (initialization ${startSettled ? 'settled' : 'pending'}, start event ${startEvent?.settled ? 'settled' : startEvent ? 'pending' : 'untracked'}, stop attempts ${attempts})` +
        (detail ? ': ' + detail : '')))
    }, timeoutMs)
    const initialized = (failed: boolean) => {
      if (finished) return
      startSettled = true; startFailed = failed
      try {
        if (isIdle()) { stopSettled = true; check() }
        else stop()
      } catch (error) { finished = true; cleanup(); reject(error) }
    }
    void started.then(() => initialized(false), () => initialized(true))
    void startEvent?.done.then(check, error => { failure = error; check() })
  })
}

/** AppStreamer is a singleton. A new viewport waits for the old viewer's
 * confirmed idle state; a cancelled queued viewer never connects. */
export class OvStreamLeases {
  private tail: Promise<void> = Promise.resolve()
  private blocked: Error | null = null
  open(start: () => Promise<unknown>, stop: (started: Promise<unknown>) => Promise<unknown>) {
    const previous = this.tail
    let finish!: () => void
    this.tail = new Promise<void>(resolve => { finish = resolve })
    let closed = false, owns = false
    let cleanup: Promise<void> | null = null
    const ready = previous.then(async () => {
      if (closed) return
      if (this.blocked) throw this.blocked
      owns = true
      await start()
    })
    return {
      ready,
      close: () => {
        closed = true
        if (!cleanup) cleanup = previous.then(async () => {
          try { if (owns) await stop(ready) }
          catch (error) {
            // Release waiters so they can report a bounded failure, but retain
            // the ownership barrier: none may connect to an uncertain singleton.
            this.blocked = error instanceof Error ? error : new Error(String(error))
            throw this.blocked
          }
          finally { owns = false; finish() }
        })
        return cleanup
      },
    }
  }
}
export const ovStreamLeases = new OvStreamLeases()
