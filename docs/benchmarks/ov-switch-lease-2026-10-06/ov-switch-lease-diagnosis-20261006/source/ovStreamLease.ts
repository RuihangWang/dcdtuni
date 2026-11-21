/** AppStreamer resolves terminate(false) with inProgress while connecting.
 * Its exported streamStatus becomes none only after successful termination
 * has also cleared the singleton controller. Do not infer idle from a reply. */
export function terminateOvViewer(
  terminate: () => Promise<unknown>,
  isIdle: () => boolean,
  started: Promise<unknown>,
  timeoutMs = 15000,
  pollMs = 50,
): Promise<void> {
  return new Promise((resolve, reject) => {
    let startSettled = false, stopSettled = false, failure: unknown
    let finished = false
    const cleanup = () => { clearInterval(poll); clearTimeout(deadline) }
    const check = () => {
      if (finished || !startSettled || !stopSettled) return
      try {
        if (!isIdle()) return
        finished = true; cleanup(); resolve()
      } catch (error) { finished = true; cleanup(); reject(error) }
    }
    const poll = setInterval(check, pollMs)
    const deadline = setTimeout(() => {
      finished = true; cleanup()
      reject(new Error('Omniverse viewer termination did not reach idle; reload the page before reconnecting' +
        (failure ? ': ' + String(failure) : '')))
    }, timeoutMs)
    // A late connect promise can update SDK state after terminate has returned.
    // Its outcome must settle as well before none is a trustworthy idle receipt.
    void started.then(
      () => { startSettled = true; check() },
      () => { startSettled = true; check() },
    )
    void Promise.resolve().then(terminate).then(
      () => { stopSettled = true; check() },
      error => { failure = error; stopSettled = true; check() },
    )
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
