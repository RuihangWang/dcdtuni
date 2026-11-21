import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getAuthState,
  installAuthGuard,
  login,
  logout,
  requireLogin,
  resetAuthForTest,
} from '../src/run/auth'

/**
 * The responses here are the server's: 401 `{error: 'Authentication required'}`
 * from `_require_auth` (server.py:107), and the login's
 * `{ok, token, username, role}` (:299).
 */

const realFetch = window.fetch
let served: { status: number; body: unknown }[] = []
let seen: string[] = []

function serve(...responses: { status: number; body: unknown }[]) {
  served = responses
  seen = []
  const impl = (url: string) => {
    seen.push(url)
    const next = served.shift() ?? { status: 200, body: {} }
    return Promise.resolve({
      ok: next.status < 400,
      status: next.status,
      json: () => Promise.resolve(next.body),
    } as unknown as Response)
  }
  window.fetch = impl as unknown as typeof fetch
}

beforeEach(() => {
  resetAuthForTest()
})

afterEach(() => {
  window.fetch = realFetch
  vi.unstubAllGlobals()
})

describe('requireLogin', () => {
  it('raises the overlay', () => {
    void requireLogin('Session expired')
    expect(getAuthState().visible).toBe(true)
    expect(getAuthState().message).toBe('Session expired')
  })

  it('gives every blocked request the SAME sign-in to wait on', () => {
    // Ten requests failing at once must produce one form, not ten.
    const a = requireLogin()
    const b = requireLogin()
    expect(a).toBe(b)
  })
})

describe('login', () => {
  it('refuses to post empty credentials', async () => {
    serve()
    expect(await login('', '')).toBe(false)
    expect(seen).toEqual([])
    expect(getAuthState().error).toBeTruthy()
  })

  it('closes the overlay and remembers who signed in', async () => {
    serve({ status: 200, body: { ok: true, username: 'admin', role: 'admin' } })
    void requireLogin()
    expect(await login('admin', 'WIN#@desk')).toBe(true)
    expect(seen).toEqual(['/api/auth/login'])
    expect(getAuthState()).toMatchObject({ visible: false, username: 'admin', role: 'admin' })
  })

  it('keeps the overlay up and shows the server message on a bad password', async () => {
    serve({ status: 401, body: { ok: false, error: 'Invalid username or password' } })
    void requireLogin()
    expect(await login('admin', 'nope')).toBe(false)
    expect(getAuthState().visible).toBe(true)
    expect(getAuthState().error).toBe('Invalid username or password')
  })

  it('resolves the waiting requests', async () => {
    serve({ status: 200, body: { ok: true, username: 'admin', role: 'admin' } })
    const waiter = requireLogin()
    await login('admin', 'WIN#@desk')
    expect(await waiter).toBe(true)
  })
})

describe('the fetch guard', () => {
  it('replays a 401 request after a sign-in, and the caller only sees the retry', async () => {
    serve(
      { status: 200, body: { authenticated: true, authRequired: true } }, // the install check
      { status: 401, body: { error: 'Authentication required' } },
      { status: 200, body: { ok: true, username: 'admin', role: 'admin' } }, // the login
      { status: 200, body: { jobId: 'job-7' } }, // the replay
    )
    installAuthGuard()

    const call = fetch('/api/run-mesh', { method: 'POST', body: '{}' })
    // The request is now parked on the overlay; nothing has failed.
    await Promise.resolve()
    expect(getAuthState().visible).toBe(true)

    await login('admin', 'WIN#@desk')
    const resp = await call
    expect(resp.status).toBe(200)
    expect(await resp.json()).toEqual({ jobId: 'job-7' })
    // /api/auth/check, the failed POST, the login, the replay.
    expect(seen).toEqual(['/api/auth/check', '/api/run-mesh', '/api/auth/login', '/api/run-mesh'])
  })

  it('passes a normal response straight through', async () => {
    serve(
      { status: 200, body: { authenticated: true } },
      { status: 200, body: { meshReady: true } },
    )
    installAuthGuard()
    const resp = await fetch('/api/check-mesh')
    expect(await resp.json()).toEqual({ meshReady: true })
    expect(getAuthState().visible).toBe(false)
  })

  it('does not swallow a 500 into a login prompt', async () => {
    serve({ status: 200, body: { authenticated: true } }, { status: 500, body: { error: 'boom' } })
    installAuthGuard()
    const resp = await fetch('/api/pp/mesh')
    expect(resp.status).toBe(500)
    expect(getAuthState().visible).toBe(false)
  })

  it('installs once, so a hot reload does not wrap the wrapper', () => {
    serve({ status: 200, body: { authenticated: true } })
    installAuthGuard()
    const first = window.fetch
    installAuthGuard()
    expect(window.fetch).toBe(first)
  })

  it('offers the form when the server says the session is not valid', async () => {
    serve({ status: 200, body: { authenticated: false, authRequired: true } })
    installAuthGuard()
    await new Promise((r) => setTimeout(r, 0))
    expect(getAuthState().visible).toBe(true)
  })

  it('stays out of the way when the server has auth disabled', async () => {
    serve({ status: 200, body: { authenticated: true, authRequired: false } })
    installAuthGuard()
    await new Promise((r) => setTimeout(r, 0))
    expect(getAuthState().visible).toBe(false)
    expect(getAuthState().authRequired).toBe(false)
  })
})

describe('logout', () => {
  it('asks for a sign-in again, because there is no page to go back to', async () => {
    serve({ status: 200, body: { ok: true } })
    await logout()
    expect(seen).toEqual(['/api/auth/logout'])
    expect(getAuthState().visible).toBe(true)
    expect(getAuthState().username).toBeNull()
  })
})
