/**
 * Signing in, and getting back in when the session expires.
 *
 * The server issues a 12-hour cookie (`handle_auth_login`, server.py:265) and
 * answers 401 on every `/api/` call without one. A single-page app has no page
 * load to be redirected on, so a 401 is simply a request that fails: panels stop
 * updating, a mesh run reports a submit failure, and nothing says why.
 *
 * So this does what the legacy page does (STL_generate.html:1284): it wraps
 * `window.fetch`, and on a 401 it puts up a login overlay, waits for the user to
 * sign in, and REPLAYS the request. The caller never sees the 401 — it sees a
 * request that took a while.
 *
 * ## Why the wrapper keeps its own copy of fetch
 *
 * Login and the auth check go through the UNWRAPPED fetch. Routing them through
 * the wrapper would mean a failed login (401 from `/api/auth/login`) triggers
 * the overlay that is already open, and waits for a login that cannot start.
 *
 * ## What cannot be replayed
 *
 * A request whose body is a stream can only be sent once. Every call in this app
 * passes a string body, which is replayable, and the legacy has the same
 * limitation — worth knowing before someone introduces an upload.
 */

import { t } from '../i18n'

export interface AuthState {
  /** Is the overlay up? It is up whenever a sign-in is being waited on. */
  visible: boolean
  message: string
  error: string | null
  busy: boolean
  username: string | null
  role: string | null
  /** False on a server started without auth, where nothing ever 401s. */
  authRequired: boolean
}

let state: AuthState = {
  visible: false,
  message: '',
  error: null,
  busy: false,
  username: null,
  role: null,
  authRequired: true,
}

type Listener = () => void
const listeners = new Set<Listener>()

export function subscribeAuth(fn: Listener): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getAuthState(): Readonly<AuthState> {
  return state
}

function set(patch: Partial<AuthState>) {
  state = { ...state, ...patch }
  listeners.forEach((fn) => fn())
}

/**
 * The unwrapped fetch.
 *
 * Captured when the wrapper is installed, not at module load: a module-load
 * capture pins whatever `fetch` existed at import time, which in a test is the
 * one the test has not stubbed yet, and in the app is fine only by luck of
 * import order.
 */
let originalFetch: typeof fetch | null = null

function raw(...args: Parameters<typeof fetch>): Promise<Response> {
  return originalFetch ? originalFetch(...args) : globalThis.fetch(...args)
}

/** The sign-in currently being waited on, shared by every blocked request. */
let pending: Promise<boolean> | null = null
let resolvePending: ((ok: boolean) => void) | null = null

export interface AuthCheck {
  authenticated: boolean
  authRequired?: boolean
  username?: string
  role?: string
}

/** Ask who the server thinks we are. Never 401s — the route is public. */
export async function checkAuth(): Promise<AuthCheck | null> {
  try {
    const resp = await raw('/api/auth/check')
    const body = (await resp.json()) as AuthCheck
    set({
      username: body.username ?? null,
      role: body.role ?? null,
      authRequired: body.authRequired !== false,
    })
    return body
  } catch {
    // The server is unreachable. Not a sign-in problem, and putting a login
    // form in front of a user whose backend is down helps nobody.
    return null
  }
}

/**
 * Show the overlay and resolve once a sign-in succeeds.
 *
 * Idempotent while one is in flight: ten requests failing at once must produce
 * ONE form, and all ten resume from the same login.
 */
export function requireLogin(message?: string): Promise<boolean> {
  set({ visible: true, error: null, message: message ?? t('auth.required') })
  if (pending) return pending
  pending = new Promise<boolean>((resolve) => {
    resolvePending = resolve
  })
  return pending
}

/**
 * Sign in.
 *
 * Returns false with `state.error` set when the credentials are refused, which
 * is what keeps the overlay up: the caller is a form, not a request.
 */
export async function login(username: string, password: string): Promise<boolean> {
  if (!username || !password) {
    set({ error: t('auth.enter_credentials') })
    return false
  }
  set({ busy: true, error: null })
  try {
    const resp = await raw('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    const body = (await resp.json()) as { ok?: boolean; error?: string; username?: string; role?: string }
    if (!resp.ok || !body.ok) {
      set({ busy: false, error: body.error ?? t('auth.login_failed') })
      return false
    }
    // The same globals the legacy publishes (:1263), so a harness reads the
    // signed-in identity out of either app the same way.
    const w = window as unknown as { __userName?: string | null; __userRole?: string | null }
    w.__userName = body.username ?? username
    w.__userRole = body.role ?? null
    set({
      busy: false,
      visible: false,
      error: null,
      username: body.username ?? username,
      role: body.role ?? null,
    })
    resolvePending?.(true)
    pending = null
    resolvePending = null
    return true
  } catch {
    set({ busy: false, error: t('auth.network_error') })
    return false
  }
}

/** Sign out, and offer the form again — there is no page to go back to. */
export async function logout(): Promise<void> {
  try {
    await raw('/api/auth/logout', { method: 'POST' })
  } catch {
    /* the session is gone locally either way */
  }
  const w = window as unknown as { __userName?: string | null; __userRole?: string | null }
  w.__userName = null
  w.__userRole = null
  set({ username: null, role: null })
  void requireLogin(t('auth.required'))
}

let installed = false

/**
 * Wrap `fetch` and check the session once.
 *
 * Installed from the session store's module scope, which is the first thing in
 * this app that talks to the server: a guard installed by a component would
 * miss every request made before that component mounts.
 */
export function installAuthGuard(): void {
  if (installed || typeof window === 'undefined') return
  installed = true

  originalFetch = window.fetch.bind(window)
  window.fetch = async (...args: Parameters<typeof fetch>) => {
    const resp = await raw(...args)
    if (resp.status !== 401) return resp
    const ok = await requireLogin()
    if (!ok) return resp
    const retry = await raw(...args)
    if (retry.status === 401) throw new Error('Authentication failed after re-login')
    return retry
  }

  void mountOverlay()
  void checkAuth().then((body) => {
    // A server with auth disabled reports authenticated with authRequired
    // false, and loopback is granted admin for the headless worker — neither
    // should see a login form.
    if (body && !body.authenticated) void requireLogin()
  })
}

/**
 * Put the overlay in the document.
 *
 * A dynamic import so this module stays free of JSX and can be tested without
 * rendering anything, and its own React root so the overlay does not depend on
 * where in the tree it is mounted — it has to be able to cover the app.
 */
async function mountOverlay(): Promise<void> {
  if (typeof document === 'undefined') return
  const mod = await import('../components/AuthOverlay')
  mod.mountAuthOverlay()
}

/** Test seam: drop the pending sign-in, the state and the installed wrapper. */
export function resetAuthForTest(): void {
  pending = null
  resolvePending = null
  installed = false
  originalFetch = null
  state = {
    visible: false,
    message: '',
    error: null,
    busy: false,
    username: null,
    role: null,
    authRequired: true,
  }
}

// A dev-only handle, the same idea as `window.__dcdtuni` in main.tsx: an
// expired session cannot be produced on demand from the UI, so this is how the
// overlay and the retry are driven in a browser check.
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as { __dcdtuniAuth: unknown }).__dcdtuniAuth = {
    state: getAuthState,
    requireLogin,
    login,
    logout,
    check: checkAuth,
  }
}
