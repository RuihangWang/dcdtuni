import { t } from '../i18n'

/**
 * The blocking overlay for a long SYNCHRONOUS rebuild.
 *
 * Entering STL-preview mode on case_13_large_B (210 racks) re-runs the exporter
 * for every solid on the main thread. Nothing yields, so the tab stops painting
 * and stops answering the mouse: from the outside that is indistinguishable
 * from a hang, and the honest fix is not to make it faster but to SAY it is
 * working. The legacy `#loading` element does exactly that around each of these
 * passes (STL_generate.html:11739, :29178, :29805).
 *
 * ## The 60 ms yield is the whole trick
 *
 * Showing the overlay and then starting the work in the same task paints
 * nothing: the browser has no chance to render between the DOM write and the
 * blocking loop, so the user sees the freeze and never the spinner. The legacy
 * waits 60 ms (or 100) before starting for that reason, and so does this.
 *
 * ## Imperative, not a React component
 *
 * The work it covers blocks the main thread, which means React cannot commit a
 * render while it runs — a `useState`-driven overlay would be scheduled and
 * then never painted until the work it was meant to cover had finished. A
 * direct DOM write plus a yield is the only shape that can appear at all.
 */

const HOST_ID = 'loading'
const STYLE_ID = 'busy-overlay-style'

/** Long enough for one paint at 60 Hz, as the legacy waits before its passes. */
const PAINT_DELAY_MS = 60

function ensureStyle(): void {
  if (document.getElementById(STYLE_ID)) return
  const el = document.createElement('style')
  el.id = STYLE_ID
  // Copied from web/theme/base.css:207 and cyberpunk-dark.css, not eyeballed:
  // the legacy overlay is a centred flex column over the whole app.
  el.textContent = `
#${HOST_ID} {
  position: fixed;
  inset: 0;
  z-index: 9998;
  display: none;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  background: rgba(10, 10, 16, 0.82);
  backdrop-filter: blur(4px);
  color: var(--highlight, #00ff88);
  font-family: var(--font-primary, monospace);
  font-size: 0.95rem;
  letter-spacing: 0.06em;
}
#${HOST_ID}.busy-open { display: flex; }
#${HOST_ID} .spinner {
  width: 42px;
  height: 42px;
  margin-bottom: 16px;
  border: 3px solid rgba(0, 255, 136, 0.15);
  border-top-color: var(--highlight, #00ff88);
  border-radius: 50%;
  animation: busy-overlay-spin 0.9s linear infinite;
}
#${HOST_ID} .busy-hint {
  margin-top: 12px;
  font-size: 0.65rem;
  color: #384858;
  letter-spacing: 0.05em;
}
@keyframes busy-overlay-spin { to { transform: rotate(360deg); } }
`
  document.head.appendChild(el)
}

function host(): HTMLElement {
  ensureStyle()
  let el = document.getElementById(HOST_ID)
  if (!el) {
    el = document.createElement('div')
    el.id = HOST_ID
    el.innerHTML =
      '<div class="spinner"></div><span id="loading-text"></span><span class="busy-hint"></span>'
    document.body.appendChild(el)
  }
  return el
}

/**
 * Nesting depth, so an inner pass cannot uncover the outer one.
 *
 * The package export runs the STL builder inside its own overlay; without a
 * count the inner `hide` would reveal a frozen page for the rest of the outer
 * pass.
 */
let depth = 0

export function showBusyOverlay(titleKey: 'loading.csg_computing' = 'loading.csg_computing'): void {
  if (typeof document === 'undefined' || !document.body) return
  const el = host()
  const title = el.querySelector('#loading-text')
  const hint = el.querySelector('.busy-hint')
  if (title) title.textContent = t(titleKey)
  if (hint) hint.textContent = t('loading.csg_hint')
  depth += 1
  el.classList.add('busy-open')
}

export function hideBusyOverlay(): void {
  if (typeof document === 'undefined') return
  depth = Math.max(0, depth - 1)
  if (depth > 0) return
  document.getElementById(HOST_ID)?.classList.remove('busy-open')
}

export function isBusyOverlayOpen(): boolean {
  return depth > 0
}

/**
 * Run `work` behind the overlay, painting it first.
 *
 * The overlay is taken down in a `finally`, so a rebuild that throws does not
 * leave the app permanently covered — which is a worse failure than the one the
 * overlay exists to explain.
 */
export async function runBlocking<T>(work: () => T | Promise<T>): Promise<T> {
  showBusyOverlay()
  try {
    await new Promise((resolve) => setTimeout(resolve, PAINT_DELAY_MS))
    return await work()
  } finally {
    hideBusyOverlay()
  }
}
