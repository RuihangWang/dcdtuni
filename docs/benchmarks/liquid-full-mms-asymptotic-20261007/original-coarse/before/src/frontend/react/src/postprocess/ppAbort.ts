/**
 * Giving up on a request that cannot itself be cancelled.
 *
 * `/api/pp/volume` resamples the whole result onto a 128³ grid and answers in
 * one shot; there is no cancel endpoint, and `fetch` is already in flight by
 * the time the user reaches for the button. So Cancel abandons the WAIT: the
 * caller stops blocking, the dialog closes, and the response — when it finally
 * arrives — is dropped on the floor. The legacy editor's volume dialog does the
 * same thing, checking `task.isCancelled()` after its await
 * (STL_generate.html:20308).
 *
 * The rejection carries `name: 'AbortError'`, which is what a caller tests to
 * tell a cancel from a failure: one is a button the user pressed and gets a
 * quiet log line, the other is red text.
 */

export function abortError(): Error {
  const error = new Error('Aborted')
  error.name = 'AbortError'
  return error
}

export function abortable<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) {
    // The work is still consumed, or its eventual rejection would surface as an
    // unhandled promise rejection long after the caller stopped caring.
    void work.catch(() => {})
    return Promise.reject(abortError())
  }
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(abortError())
    signal.addEventListener('abort', onAbort, { once: true })
    work.then(
      (value) => {
        signal.removeEventListener('abort', onAbort)
        resolve(value)
      },
      (reason: unknown) => {
        signal.removeEventListener('abort', onAbort)
        reject(reason instanceof Error ? reason : new Error(String(reason)))
      },
    )
  })
}
