import { useEffect, useRef, useState } from 'react'
import * as store from '../projectStore'
import { toggleLogDock } from '../ui/layoutMetrics'
import { TerminalPanel } from './TerminalPanel'
import { captureViewportPng } from '../ui/viewportScreenshot'

/**
 * Save the displayed OV frame or freshly rendered Three canvas as a PNG.
 * Capture errors are visible in the existing Operation Log.
 */
async function captureScreenshot() {
  try {
    const handle = (window as unknown as { __viewportRender?: () => HTMLCanvasElement | null })
      .__viewportRender
    const imageData = captureViewportPng(document, handle)
    const resp = await fetch('/api/screenshot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ imageData }),
    })
    const result = (await resp.json()) as { path?: string; error?: string }
    store.logAction('captureScreenshot', result.error ?? result.path ?? 'saved')
  } catch (e) {
    store.logAction('captureScreenshot', e instanceof Error ? e.message : String(e))
  }
}
import { useTranslation } from '../useTranslation'
import { logRows } from './logRows'

/**
 * The bottom terminal / operation log.
 *
 * The legacy panel hosts an xterm.js terminal for server job output and keeps a
 * separate hidden operation log. This one shows the operation log, which is the
 * half that exists in React so far; the server job stream lands here when the
 * mesh/solve panels migrate.
 *
 * Undo depth is printed in the header the way the legacy panel does, because it
 * is the only place a user can see whether an action was checkpointed at all.
 */

const CATEGORY: Record<string, string> = {
  loadProject: 'file',
  saveProject: 'file',
  removeModel: 'delete',
  duplicateModel: 'create',
  renameModel: 'modify',
  toggleModelEnabled: 'modify',
  undo: 'view',
  redo: 'view',
  // Job lanes: the mesh tab's run/checkMesh stream and the solver tab's run,
  // mirrored into the dock by run/jobLogMirror under the legacy terminal's own
  // [dctwin] tag; the entries the panels log for their own buttons keep a
  // category of their own so a click and a stream line read differently.
  dctwin: 'dctwin',
  // The HTML editor files its run operations under `file`
  // (`logOperation('checkMesh', …, 'file')`, STL_generate.html:25717), so the
  // tags read `[file:checkMesh]` there; matching it keeps one dialect.
  mesh: 'file',
  checkMesh: 'file',
  meshPreview: 'view',
  solve: 'file',
  runSolveOnly: 'file',
}

/**
 * The tag text, in the HTML editor's format: `[category:action]`, except the
 * terminal's job lines, which are a bare `[dctwin]` (STL_generate.html:30064).
 */
function tagFor(action: string): string {
  const category = CATEGORY[action] ?? 'view'
  return category === 'dctwin' ? '[dctwin]' : `[${category}:${action}]`
}

/**
 * Rows drawn at once.
 *
 * The dock prints oldest-first with the newest line at the bottom, the way a
 * terminal does and the way the HTML editor's does; the cap (see `logRows`) is
 * purely "how much DOM does one streamed batch re-render".
 */

/** Stick to the bottom unless the user has scrolled up to read history. */
const NEAR_BOTTOM_PX = 48

function stamp(at: number): string {
  const d = new Date(at)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const DEFAULT_LOG_HEIGHT = 160

function readLogHeightPx(): number {
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--log-panel-height')
  const px = parseFloat(raw)
  return Number.isFinite(px) ? px : DEFAULT_LOG_HEIGHT
}

export function LogPanel() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  // Which half of the dock is showing. The operation log is the default: it is
  // what a migration user is watching, and the shell is one click away.
  const [view, setView] = useState<'terminal' | 'log'>('log')
  const panelRef = useRef<HTMLDivElement | null>(null)
  const restoreHeight = useRef(DEFAULT_LOG_HEIGHT)

  useEffect(() => store.subscribe(() => bump((n) => n + 1)), [])

  // Collapsing writes the LAYOUT variable, the way the legacy editor's
  // toggleLogPanel does (STL_generate.html:4379). Shrinking only this element —
  // which is what an inline height does — left every panel positioned against
  // the old 160px and opened a 132px band of bare app background above the
  // folded dock.
  //
  // The dock's own height is the state. A `collapsed` boolean beside it would
  // go stale the moment the dock was dragged instead of toggled, and there is
  // nothing to hide: at 28px the header fills the box and `overflow: hidden`
  // does the rest, so both views stay mounted and a running command survives.
  const toggleDock = () => {
    const next = toggleLogDock(readLogHeightPx(), restoreHeight.current)
    restoreHeight.current = next.rememberPx
    document.documentElement.style.setProperty('--log-panel-height', `${next.heightPx}px`)
  }

  // The frame is laid out against what the dock MEASURES, not what was asked
  // for: min-height, max-height: 50vh and a collapse each move one without the
  // other. A window shrunk after the dock was dragged tall hits the 50vh clamp,
  // and the gap that opened there looked exactly like the collapse bug.
  useEffect(() => {
    const el = panelRef.current
    if (!el) return
    const publish = () => {
      const h = Math.round(el.getBoundingClientRect().height)
      document.documentElement.style.setProperty('--log-dock-height', `${h}px`)
    }
    publish()
    const observer = new ResizeObserver(publish)
    observer.observe(el)
    window.addEventListener('resize', publish)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', publish)
    }
  }, [])

  const entries = store.getLog()
  // The store keeps newest-first; a terminal reads top-down.
  const { rows, hiddenOlder } = logRows(entries)

  // Newest at the bottom means the dock has to follow its own tail: scroll to
  // the end on every new line, but never yank a user who scrolled up to read
  // history back down to it.
  const contentRef = useRef<HTMLDivElement | null>(null)
  const stickToBottom = useRef(true)
  useEffect(() => {
    const el = contentRef.current
    if (!el) return
    if (!stickToBottom.current) return
    el.scrollTop = el.scrollHeight
  })
  const onDockScroll = () => {
    const el = contentRef.current
    if (!el) return
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
  }

  return (
    <div id="log-panel" ref={panelRef}>
      <div id="log-header">
        <span>
          Terminal
          <span className="log-undo-info">
            {store.canUndo() ? t('log.undo_available', { count: store.undoDepth() }) : t('log.undo_none')}
          </span>
        </span>
        <div className="log-actions">
          <button onClick={() => store.undo()} disabled={!store.canUndo()} title="Ctrl+Z">
            {t('btn.undo')}
          </button>
          <button onClick={() => store.redo()} disabled={!store.canRedo()} title="Ctrl+Y">
            {t('btn.redo')}
          </button>
          {/* Terminal / operation log, as two views of the same dock. The
              legacy panel shows the shell and hides its log element; both are
              reachable here. */}
          <button
            id="log-view-terminal"
            className={view === 'terminal' ? 'active' : undefined}
            onClick={() => setView('terminal')}
          >
            Terminal
          </button>
          <button
            id="log-view-log"
            className={view === 'log' ? 'active' : undefined}
            onClick={() => setView('log')}
          >
            Log
          </button>
          <button onClick={toggleDock}>Toggle</button>
          <button
            id="btn-screenshot"
            onClick={() => void captureScreenshot()}
            style={{
              background: 'rgba(0,255,136,0.08)',
              borderColor: 'rgba(0,255,136,0.35)',
              color: 'var(--highlight)',
            }}
          >
            {t('btn.screenshot')}
          </button>
        </div>
      </div>
      {/* Both halves stay MOUNTED — while the other view shows, and while the
          dock is folded down to its header. Unmounting would drop the shell, so
          a running command dies on a tab switch or on a fold; the legacy panel
          keeps both in the DOM for the same reason. */}
      <div className="log-view" hidden={view !== 'terminal'}>
        <TerminalPanel />
      </div>
      <div id="log-content" hidden={view !== 'log'} ref={contentRef} onScroll={onDockScroll}>
        {rows.length === 0 && <span style={{ opacity: 0.6 }}>{t('log.no_entries')}</span>}
        {hiddenOlder > 0 && (
          <div className="log-entry" style={{ opacity: 0.6 }}>
            {t('log.earlier_entries', { count: hiddenOlder })}
          </div>
        )}
        {/* A streamed batch is ONE entry holding many lines; the terminal
            prints each as its own row under the same stamp and tag. */}
        {rows.map(({ entry: e, line }, i) => (
          <div className="log-entry" key={`${e.at}-${i}`}>
            <span className="log-time">[{stamp(e.at)}]</span>
            <span className={`log-cat-${CATEGORY[e.action] ?? 'view'}`}> {tagFor(e.action)}</span>
            <span> {line}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
