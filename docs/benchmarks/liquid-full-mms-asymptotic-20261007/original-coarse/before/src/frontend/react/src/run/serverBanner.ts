import { t } from '../i18n'
import { isServerFault, type ServerDiagnosis } from './serverDiagnosis'

/**
 * Says WHY the backend is unusable, where the user can still see it.
 *
 * The first attempt at this was a React banner inside the mesh and solver
 * panels, and testing it in the running app showed it can never appear: those
 * tabs are gated on `isValidated()` (App.tsx `leftTabGate`), so the moment the
 * diagnosis is worth reading the panel holding it is unreachable. Every
 * unusable-backend state — server down, wrong `--api-port`, no case directory —
 * disables the same buttons and leaves the same `error.validate_dctwin_first`
 * tooltip behind, which is the whole complaint.
 *
 * So the banner is imperative and lives on `document.body`, the way
 * `uiFreeze.ts` reaches DOM it does not own and `busyOverlay.ts` mounts its own
 * host. It is `position: fixed` over the top of the viewport column, outside
 * React's tree entirely, so no re-render can drop it and it cannot disturb the
 * reconciliation of a panel another module owns.
 *
 * `pointer-events: none` — it must never swallow a click meant for the
 * toolbar underneath it.
 */

const HOST_ID = 'server-status-banner'
const STYLE_ID = 'server-status-banner-style'

function ensureStyle(): void {
  if (document.getElementById(STYLE_ID)) return
  const el = document.createElement('style')
  el.id = STYLE_ID
  // Spans the viewport column, between the two side panels, so it covers the
  // 3D view rather than any control. `--left/right-panel-w` are the DRAWN
  // widths (styles.css:72), not the asked-for ones, so a resized or collapsed
  // panel moves the banner with it.
  el.textContent = `
#${HOST_ID} {
  position: fixed;
  top: 6px;
  left: calc(var(--left-panel-w, 330px) + 12px);
  right: calc(var(--right-panel-w, 340px) + 12px);
  z-index: 60;
  padding: 6px 10px;
  font-family: var(--font-primary, monospace);
  font-size: 0.76rem;
  line-height: 1.4;
  border-radius: 3px;
  pointer-events: none;
}
#${HOST_ID}[data-fault="true"] {
  color: #ff5c72;
  border: 1px solid rgba(255, 92, 114, 0.35);
  background: rgba(40, 12, 16, 0.92);
}
#${HOST_ID}[data-fault="false"] {
  color: var(--hint-color, #e5c07b);
  border: 1px solid rgba(229, 192, 123, 0.3);
  background: rgba(36, 30, 16, 0.92);
}
`
  document.head.appendChild(el)
}

/**
 * Draw, update or remove the banner.
 *
 * `null` and a `ready` diagnosis both remove it — the first means the probe has
 * not run yet, and claiming the server is down while the request is still in
 * flight would be its own false alarm.
 */
export function renderServerBanner(diagnosis: ServerDiagnosis | null): void {
  if (typeof document === 'undefined' || !document.body) return
  const existing = document.getElementById(HOST_ID)
  if (!diagnosis || diagnosis.kind === 'ready') {
    existing?.remove()
    return
  }
  ensureStyle()
  const el = existing ?? document.createElement('div')
  if (!existing) {
    el.id = HOST_ID
    document.body.appendChild(el)
  }
  el.dataset.diagnosis = diagnosis.kind
  // A fault is the SERVER's; `notReady` is the case directory's, and the second
  // is an ordinary state a new project starts in rather than an error.
  el.dataset.fault = String(isServerFault(diagnosis))
  el.textContent = t(diagnosis.key, diagnosis.vars)
}
