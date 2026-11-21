import { t } from '../i18n'

/**
 * What the terminal writes when its socket closes.
 *
 * The old line was a hard-coded `[terminal disconnected]`, which reads as "the
 * backend died" — and the commonest cause here is nothing of the sort. It is an
 * SSH tunnel that forwarded the HTTP port and not the WebSocket one, so the
 * PORT NUMBER is the actionable half of the message; the second commonest is a
 * session with no token, which the server refuses with close code 4001.
 *
 * The third line matters as much as the first two: mesh and solve go over HTTP
 * `/api/*` and are unaffected by this socket, so a dead terminal is not a dead
 * app. `_terminalWs.onclose` (STL_generate.html:4303) writes all three.
 *
 * Split out of the component so the wording can be tested and read back in the
 * running page without having to break a live socket to see it.
 */

/** The server's close code for an unauthenticated socket (server.py). */
export const CLOSE_NOT_AUTHENTICATED = 4001

export function terminalCloseReason(code: number, wsPort: number): string {
  return code === CLOSE_NOT_AUTHENTICATED
    ? t('error.not_authenticated')
    : t('error.ws_unreachable', { port: wsPort })
}

/** The full three-part line, ANSI colours included, as the legacy writes it. */
export function terminalCloseMessage(code: number, wsPort: number): string {
  return (
    `\r\n\x1b[33m[${t('error.terminal_unavailable')}]\x1b[0m ${terminalCloseReason(code, wsPort)}\r\n` +
    `\x1b[90m${t('error.mesh_solve_unaffected')}\x1b[0m\r\n`
  )
}

// A dev-only handle, as `transient.ts` has: the only other way to read this
// wording back in the running app is to break the socket it belongs to.
if (import.meta.env.DEV && typeof window !== 'undefined') {
  ;(window as unknown as { __dcdtuniTerminal: unknown }).__dcdtuniTerminal = {
    terminalCloseReason,
    terminalCloseMessage,
    CLOSE_NOT_AUTHENTICATED,
  }
}
