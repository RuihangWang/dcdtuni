import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { getAuthState, login, subscribeAuth } from '../run/auth'
import { useTranslation } from '../useTranslation'

/**
 * The sign-in form, over everything.
 *
 * A port of `_showAuthOverlay` (STL_generate.html:1229) with its markup
 * (`#auth-overlay`, :100-113): the same ids, so one selector drives either app,
 * and the same behaviour — the form is up whenever a request is waiting on a
 * session, and Enter submits.
 *
 * ## Its own React root
 *
 * Mounted into a container of its own rather than into the app tree. The
 * overlay has to appear on a 401 from ANY request, including ones that fire
 * before the editor has rendered, and it must survive whatever the app is doing
 * — it is the thing standing between the user and an app that cannot talk to
 * its server.
 */

export function AuthOverlay() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  useEffect(() => subscribeAuth(() => bump((n) => n + 1)), [])
  const auth = getAuthState()

  // Cleared on every open: a password left in the field is a password left on
  // screen, and the legacy blanks it for the same reason (:1240).
  useEffect(() => {
    if (auth.visible) setPassword('')
  }, [auth.visible])

  const submit = () => {
    void login(username.trim(), password).then((ok) => {
      if (ok) setPassword('')
    })
  }

  return (
    <div
      id="auth-overlay"
      // Up = a modal: the editor's keys stand down while it shows
      // (`editorKeys` DIALOG_SELECTOR). It stays mounted when hidden, so a
      // class alone could not say which.
      data-modal-open={auth.visible ? '' : undefined}
      style={{
        display: auth.visible ? 'flex' : 'none',
        position: 'fixed',
        inset: 0,
        zIndex: 999999,
        background: 'rgba(10,12,18,0.92)',
        backdropFilter: 'blur(6px)',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          width: 320,
          maxWidth: '90vw',
          padding: 28,
          background: '#1a1d24',
          border: '1px solid #2a2e38',
          borderRadius: 8,
          boxShadow: '0 20px 60px rgba(0,0,0,0.6)',
        }}
      >
        <h2 style={{ margin: '0 0 6px', color: '#e6e6e6', fontSize: '1.05rem' }}>
          {t('auth.sign_in')}
        </h2>
        <p id="auth-overlay-msg" style={{ margin: '0 0 18px', color: '#8892a4', fontSize: '0.8rem' }}>
          {auth.message || t('auth.required')}
        </p>
        <div style={{ marginBottom: 12 }}>
          <input
            type="text"
            id="auth-overlay-user"
            autoComplete="username"
            value={username}
            placeholder="admin"
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            style={{
              width: '100%',
              padding: '8px 10px',
              background: '#12141a',
              border: '1px solid #333',
              borderRadius: 4,
              color: '#e6e6e6',
              fontSize: '0.85rem',
              boxSizing: 'border-box',
            }}
          />
        </div>
        <div style={{ marginBottom: 12 }}>
          <input
            type="password"
            id="auth-overlay-pass"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            style={{
              width: '100%',
              padding: '8px 10px',
              background: '#12141a',
              border: '1px solid #333',
              borderRadius: 4,
              color: '#e6e6e6',
              fontSize: '0.85rem',
              boxSizing: 'border-box',
            }}
          />
        </div>
        <div
          id="auth-overlay-error"
          style={{
            display: auth.error ? 'block' : 'none',
            marginBottom: 12,
            padding: '6px 10px',
            background: 'rgba(255,70,70,0.1)',
            border: '1px solid rgba(255,70,70,0.3)',
            borderRadius: 4,
            color: '#ff6b6b',
            fontSize: '0.78rem',
          }}
        >
          {auth.error}
        </div>
        <button
          id="auth-overlay-btn"
          disabled={auth.busy}
          onClick={submit}
          style={{
            width: '100%',
            padding: 9,
            background: '#2563eb',
            color: '#fff',
            border: 'none',
            borderRadius: 4,
            fontSize: '0.85rem',
            cursor: 'pointer',
            fontWeight: 500,
          }}
        >
          {auth.busy ? t('auth.signing_in') : t('auth.sign_in')}
        </button>
      </div>
    </div>
  )
}

/** Put the overlay in the document, once. Called by `installAuthGuard`. */
export function mountAuthOverlay(): void {
  if (typeof document === 'undefined') return
  if (document.getElementById('auth-overlay-root')) return
  const host = document.createElement('div')
  host.id = 'auth-overlay-root'
  document.body.appendChild(host)
  createRoot(host).render(<AuthOverlay />)
}
