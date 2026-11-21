/**
 * Freezing the editor while a mesh or solve job runs.
 *
 * A job runs against the geometry that was uploaded when it started. An edit
 * made while it runs is not in that job, so the results stop matching the
 * screen — silently, because nothing about the run fails. The legacy answer is
 * `_setGlobalUIFrozen` (STL_generate.html:28288), which walks the mesh panel,
 * the solver panel, the inspector, the six tab buttons, the file bar, the
 * toolbar and the tree, setting `.disabled` on every control and skipping the
 * abort buttons.
 *
 * That exact approach does not survive React: an imperative `.disabled` write
 * on a control React renders is undone by the next re-render, and during a job
 * every streamed log line causes one. So the freeze is expressed the two ways a
 * re-render cannot lose — a class on `<body>` with a stylesheet keyed off it,
 * and the `inert` attribute on containers React does not manage the attribute
 * on.
 *
 * `pointer-events` stops the mouse; `inert` is what also stops a keyboard user
 * tabbing into a field that is merely dimmed. The mesh and solver panels are
 * NOT made inert here — their abort button lives inside them and `inert` cannot
 * be lifted on a descendant. Those two panels wrap their settings half in
 * `FreezeGuard` instead, which is the same skip-list idea as `_freezeSkipIds`,
 * and the stylesheet dims that wrapper's children rather than the panel (see
 * `css()` for why the panel itself must stay live).
 */

const STYLE_ID = 'ui-freeze-style'

export const FROZEN_CLASS = 'ui-frozen'

/** Every region the legacy freeze walks, by this app's ids. */
const FROZEN_REGIONS = [
  '#right-panel',
  '#left-tab-bar',
  '.file-bar',
  '#toolbar',
  '#timeline-settings-panel',
  '#creation-toolbar',
  '#left-geo-export',
  '#tree-container',
]

/**
 * Regions that hold nothing which must stay live, so they can go fully inert.
 *
 * The run panels are absent on purpose: aborting a job is the one thing a user
 * must still be able to do, and those buttons are inside them.
 */
const INERT_REGIONS = [
  '#right-panel',
  '#left-tab-bar',
  '.file-bar',
  '#toolbar',
  '#timeline-settings-panel',
  '#creation-toolbar',
  '#left-geo-export',
  '#tree-container',
]

/** Kept clickable through the freeze — the legacy `_freezeSkipIds` (:28267). */
const LIVE_IDS = ['mesh-abort-btn', 'solve-abort-btn', 'check-mesh-btn', 'lc-stop-btn']

/**
 * The mesh and solver panels are NOT in `FROZEN_REGIONS`: each is the scroll
 * container of its tab, and `pointer-events: none` on a scroll container hands
 * the wheel to whatever is behind it, so the frozen panel could not be scrolled
 * (2026-09-22 bug). Their settings are frozen one level down instead — every
 * child of the `FreezeGuard` wrapper, and every child of the run block except
 * the elements marked `job-live` (the abort and check-mesh buttons). The
 * scroller itself keeps its pointer events, so the wheel still scrolls it, and
 * the live buttons sit outside the dimmed subtree, so they render at full
 * brightness wherever the panel is scrolled to.
 */
export const GUARD_CLASS = 'freeze-guard'
export const RUN_BLOCK_CLASS = 'job-run-block'
export const LIVE_CLASS = 'job-live'

function css(): string {
  const frozen = [
    ...FROZEN_REGIONS.map((sel) => `body.${FROZEN_CLASS} ${sel}`),
    `body.${FROZEN_CLASS} .${GUARD_CLASS} > *`,
    `body.${FROZEN_CLASS} .${RUN_BLOCK_CLASS} > :not(.${LIVE_CLASS})`,
  ].join(',\n')
  const live = [
    ...LIVE_IDS.map((id) => `body.${FROZEN_CLASS} #${id}`),
    `body.${FROZEN_CLASS} .${LIVE_CLASS}`,
  ].join(',\n')
  return (
    `${frozen} {\n  pointer-events: none;\n  opacity: 0.6;\n}\n` +
    // pointer-events is re-enableable on a descendant even when an ancestor
    // switched it off, which is the whole reason the freeze uses it rather than
    // an overlay. Opacity is not, which is why the live buttons are siblings of
    // the dimmed elements rather than descendants.
    `${live} {\n  pointer-events: auto;\n  opacity: 1;\n}\n`
  )
}

function ensureStyle(): void {
  if (document.getElementById(STYLE_ID)) return
  const el = document.createElement('style')
  el.id = STYLE_ID
  el.textContent = css()
  document.head.appendChild(el)
}

let applied = false

/**
 * Freeze or thaw the editor.
 *
 * Guarded on the current value: the session store emits on every streamed log
 * line, and re-running the selectors thousands of times per job would be a
 * needless cost for a state that changes twice.
 */
function markInert(frozen: boolean): void {
  for (const selector of INERT_REGIONS) {
    document.querySelectorAll(selector).forEach((el) => {
      if (frozen) el.setAttribute('inert', '')
      else el.removeAttribute('inert')
    })
  }
}

/**
 * Re-applies `inert` to regions React mounts DURING a job.
 *
 * The pointer half of the freeze is a stylesheet, so it covers a panel that
 * appears mid-run for free. `inert` cannot: it is an attribute on the node that
 * existed when the job started, and React swaps whole subtrees while a job
 * streams (the file bar collapses, a panel re-renders per log line). Without
 * this, a container that mounted mid-run is dimmed and click-proof but still
 * reachable by Tab — the one way left to edit a setting the run is using.
 */
let observer: MutationObserver | null = null

export function setUiFrozen(frozen: boolean): void {
  if (typeof document === 'undefined' || !document.body) return
  if (frozen === applied) return
  applied = frozen
  ensureStyle()
  document.body.classList.toggle(FROZEN_CLASS, frozen)
  markInert(frozen)

  if (frozen) {
    if (!observer && typeof MutationObserver !== 'undefined') {
      observer = new MutationObserver(() => markInert(true))
      observer.observe(document.body, { childList: true, subtree: true })
    }
  } else {
    observer?.disconnect()
    observer = null
  }
}
