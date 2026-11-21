import type { TranslationKey } from '../i18n'
import type { ServerInfo } from './session'

/**
 * Why `/api/server-info` did not come back usable.
 *
 * Every failure used to be caught and dropped, so a backend that is down, one
 * behind a Vite proxy pointed at the wrong `--api-port`, and one that is fine
 * all produced the same screen: the run tabs greyed out behind "validate DCTwin
 * first" and a dash where the version should be. The legacy `refreshServerInfo`
 * (STL_generate.html:18713) names the failure instead, and the three cases it
 * separates are the three that need different fixes:
 *
 *  - an HTML body means something OTHER than server.py answered `/api/*` — the
 *    dev server serving index.html because the proxy target is wrong, or a
 *    login page. `log.server_not_running` says to start `start_server.sh`.
 *  - a body that is neither JSON nor HTML is a proxy or gateway talking, and
 *    the first 80 characters are what identifies it.
 *  - a rejected fetch never reached a server at all.
 *
 * Kept pure and separate from `session.ts` so the classification can be tested
 * without a network: the bug this guards against is a body being MISREAD, not a
 * request failing.
 */

export type ServerDiagnosisKind =
  | 'ready'
  | 'notReady'
  | 'notRunning'
  | 'abnormal'
  | 'unreachable'

export interface ServerDiagnosis {
  kind: ServerDiagnosisKind
  key: TranslationKey
  vars?: Record<string, string | number>
}

export interface ServerProbe {
  diagnosis: ServerDiagnosis
  /** Null unless the body parsed AND the response was ok. */
  info: ServerInfo | null
}

/** The legacy slices the body to 80 characters before logging it. */
const BODY_SLICE = 80

/**
 * Classify a response body.
 *
 * `status` is inspected as well as the text, because a JSON error payload from
 * a 500 parses perfectly well and would otherwise be reported as a backend that
 * is merely "not ready" — the one diagnosis that tells a user to go fix their
 * case directory when the server itself is broken.
 */
export function diagnoseServerBody(text: string, status: number, ok: boolean): ServerProbe {
  const trimmed = text.trim()
  if (trimmed.startsWith('<')) {
    return { diagnosis: { kind: 'notRunning', key: 'log.server_not_running' }, info: null }
  }
  let info: ServerInfo | null = null
  try {
    info = JSON.parse(text) as ServerInfo
  } catch {
    return {
      diagnosis: {
        kind: 'abnormal',
        key: 'log.server_response_abnormal',
        vars: { text: trimmed.slice(0, BODY_SLICE) },
      },
      info: null,
    }
  }
  if (!ok) {
    return {
      diagnosis: {
        kind: 'abnormal',
        key: 'log.server_response_abnormal',
        vars: { text: `${status} ${trimmed.slice(0, BODY_SLICE)}` },
      },
      info: null,
    }
  }
  if (info?.allOk === true) {
    return { diagnosis: { kind: 'ready', key: 'log.dctwin_ready' }, info }
  }
  return {
    diagnosis: {
      kind: 'notReady',
      key: 'log.dctwin_not_ready',
      // Printed as the raw booleans the legacy prints: which of the two flags
      // is false is the whole content of the message.
      vars: { dctwin: String(info?.dctwinAvailable ?? false), caseDir: String(info?.caseDirOk ?? false) },
    },
    info,
  }
}

/** A fetch that never produced a response — no server on that port at all. */
export function diagnoseServerFailure(error: unknown): ServerDiagnosis {
  return {
    kind: 'unreachable',
    key: 'log.connection_failed',
    vars: { error: error instanceof Error ? error.message : String(error) },
  }
}

/** Does this diagnosis mean the user has something to fix? */
export function isServerFault(diagnosis: ServerDiagnosis | null): boolean {
  return !!diagnosis && diagnosis.kind !== 'ready' && diagnosis.kind !== 'notReady'
}
