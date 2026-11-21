import type { ServerDiagnosis } from './serverDiagnosis'

/**
 * Where the reason the backend is unusable is shown: the Operation Log.
 *
 * It used to be a `position: fixed` banner drawn over the top of the viewport
 * column, and that is where the toolbar is: "DCTwin 未就绪", "server not
 * running" and the like sat over its buttons the moment the app opened
 * without a case directory (user report, 2026-10-19). Every diagnosis is
 * already written to the Operation Log at the bottom (session.ts
 * `logOperation('refreshServerInfo', ...)`), which is reachable in every state,
 * including the ones that disable the mesh and solver tabs. So nothing is drawn
 * here any more; a banner left by an older build of the page is removed.
 */

const HOST_ID = 'server-status-banner'
const STYLE_ID = 'server-status-banner-style'

export function renderServerBanner(_diagnosis: ServerDiagnosis | null): void {
  if (typeof document === 'undefined') return
  document.getElementById(HOST_ID)?.remove()
  document.getElementById(STYLE_ID)?.remove()
}
