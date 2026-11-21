import { useEffect, useRef, useState } from 'react'
import { terminalCloseMessage } from '../run/terminalStatus'
import { useTranslation } from '../useTranslation'

/**
 * The shell terminal, on the same WebSocket the legacy editor uses.
 *
 * xterm.js is loaded as CLASSIC scripts from the vendored bundles rather than
 * imported: both files are UMD builds that assign to a top-level `var`
 * (`Terminal`, `FitAddon`), which is a global only in script scope — imported
 * as a module they would be module-scoped and the globals would stay undefined.
 * Same trap as the BVH bundle.
 *
 * The socket is the WS port the server reports, not `httpPort + 1`: the
 * launcher scans upward for a free PAIR on a busy host, so the derived guess is
 * wrong exactly when several people are using the machine — which is when a
 * terminal is most wanted.
 *
 * Auth: the token comes from sessionStorage, where the server's own login page
 * puts it. A React dev server is a DIFFERENT ORIGIN from that page, so the
 * token is usually absent — and the connection still works, because the server
 * skips auth for a loopback peer (server.py:3985). Off the loopback it will be
 * refused, exactly as the legacy page would be without its token; fixing that
 * needs a login flow, not a terminal change.
 */

interface XtermTerminal {
  open(el: HTMLElement): void
  write(data: string): void
  onData(cb: (data: string) => void): void
  dispose(): void
}

interface FitAddonInstance {
  fit(): void
  proposeDimensions(): { rows: number; cols: number } | undefined
}

/**
 * One load per URL, awaited by every caller.
 *
 * Keyed on a PROMISE rather than on the presence of the <script> tag. React
 * StrictMode mounts this component twice in development: the first run appends
 * the tag, the second sees it and — with a presence check — resolves
 * immediately, while the script is still executing. `window.Terminal` is then
 * undefined and the terminal never appears, which is exactly what happened.
 */
const loading = new Map<string, Promise<void>>()

function loadScript(src: string): Promise<void> {
  const existing = loading.get(src)
  if (existing) return existing
  const p = new Promise<void>((resolve, reject) => {
    const el = document.createElement('script')
    el.src = src
    el.dataset.termSrc = src
    el.onload = () => resolve()
    el.onerror = () => reject(new Error(`failed to load ${src}`))
    document.head.appendChild(el)
  })
  loading.set(src, p)
  return p
}

function loadCss(href: string) {
  if (document.querySelector(`link[data-term-css="${href}"]`)) return
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = href
  link.dataset.termCss = href
  document.head.appendChild(link)
}

export function TerminalPanel() {
  const { t } = useTranslation()
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let terminal: XtermTerminal | null = null
    let socket: WebSocket | null = null
    let observer: ResizeObserver | null = null
    let disposed = false

    void (async () => {
      try {
        // Each file resolved on its own. A directory URL joined by string
        // concatenation lost its trailing slash under Vite and produced
        // `…/vendorxterm.js`, which the dev server happily served as 200 —
        // from the wrong path, so the globals never appeared.
        loadCss(new URL('../../../shared/vendor/xterm.css', import.meta.url).href)
        await loadScript(new URL('../../../shared/vendor/xterm.js', import.meta.url).href)
        await loadScript(new URL('../../../shared/vendor/addon-fit.js', import.meta.url).href)
        if (disposed) return
        const w = window as unknown as {
          Terminal?: new (opts: unknown) => XtermTerminal & { loadAddon(a: unknown): void }
          FitAddon?: { FitAddon: new () => FitAddonInstance }
        }
        if (!w.Terminal) throw new Error('xterm.js did not install window.Terminal')

        const term = new w.Terminal({
          theme: {
            background: '#161620',
            foreground: '#00ff88',
            cursor: '#00ff88',
            cursorAccent: '#161620',
            selectionBackground: 'rgba(0,255,136,0.2)',
            black: '#161620',
            green: '#00ff88',
            cyan: '#00d4ff',
            red: '#ff2244',
            yellow: '#ffb800',
            magenta: '#cc44ff',
          },
          fontFamily: '"Share Tech Mono", "Cascadia Code", "Fira Code", "Consolas", monospace',
          fontSize: 13,
          cursorBlink: true,
        })
        terminal = term
        const fit = w.FitAddon ? new w.FitAddon.FitAddon() : null
        if (fit) term.loadAddon(fit)
        term.open(host)
        fit?.fit()

        let wsPort = Number(location.port || '8000') + 1
        try {
          const resp = await fetch('/api/server-info')
          if (resp.ok) {
            const info = (await resp.json()) as { wsPort?: number }
            if (info.wsPort) wsPort = info.wsPort
          }
        } catch {
          // Fall back to the derived port; the socket's own error path reports
          // the failure to the user rather than this being silent.
        }
        const token = sessionStorage.getItem('stlgen_ws_token') ?? ''
        const url = `ws://${location.hostname || 'localhost'}:${wsPort}${token ? `?token=${encodeURIComponent(token)}` : ''}`
        socket = new WebSocket(url)
        socket.onopen = () => {
          term.write('\r\n\x1b[32m[connected to terminal]\x1b[0m\r\n')
          const dims = fit?.proposeDimensions()
          if (dims) socket?.send(JSON.stringify({ type: 'resize', rows: dims.rows, cols: dims.cols }))
        }
        socket.onmessage = (e) => term.write(String(e.data))
        // The close CODE is the diagnosis. 4001 is the server refusing an
        // unauthenticated socket; anything else on this port is almost always
        // an SSH tunnel that forwarded the HTTP port and not the WS one, so the
        // port number is the actionable half of the message. Either way the
        // mesh and solve buttons go over HTTP /api/* and still work — saying so
        // is what stops '[terminal disconnected]' reading as a dead backend.
        // Mirrors `_terminalWs.onclose` (STL_generate.html:4303).
        socket.onclose = (evt) => term.write(terminalCloseMessage(evt.code, wsPort))
        // Silent on purpose: a failed socket fires error THEN close, and the
        // close handler above is the one that can name the cause.
        socket.onerror = () => {}
        term.onData((data) => {
          // RAW keystrokes, not a JSON envelope. The server writes any message
          // that is not a resize straight into the PTY (server.py:4062), so a
          // wrapper is typed into the shell verbatim — the first test of this
          // panel echoed `{"type":"input","data":"e"}` at a bash prompt.
          if (socket?.readyState === WebSocket.OPEN) socket.send(data)
        })

        // Held on the outer scope so the EFFECT's cleanup can disconnect it —
        // returning it from inside the async function returns from the IIFE,
        // and React never sees that value.
        observer = new ResizeObserver(() => {
          // Skip a zero-size box. Collapsing the log dock hides this element
          // rather than unmounting it, so that a running command survives the
          // fold; fitting to 0x0 throws inside xterm's renderer and would take
          // the whole panel down with it.
          if (host.clientWidth === 0 || host.clientHeight === 0) return
          fit?.fit()
          const dims = fit?.proposeDimensions()
          if (dims && socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'resize', rows: dims.rows, cols: dims.cols }))
          }
        })
        observer.observe(host)
      } catch (e) {
        if (!disposed) setError(e instanceof Error ? e.message : String(e))
      }
    })()

    return () => {
      disposed = true
      observer?.disconnect()
      socket?.close()
      terminal?.dispose()
    }
  }, [])

  return (
    <>
      <div id="terminal-container" ref={hostRef} />
      {error && (
        <div className="prop-hint" style={{ color: '#e06c75' }}>
          {t('error.terminal_unavailable')}: {error}
        </div>
      )}
    </>
  )
}
