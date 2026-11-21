import { describe, expect, it } from 'vitest'
import {
  diagnoseServerBody,
  diagnoseServerFailure,
  isServerFault,
} from '../src/run/serverDiagnosis'
import { t } from '../src/i18n'

/**
 * Every one of these used to be caught and dropped, which is why they are
 * pinned here: the point of the module is that a down backend, a mis-proxied
 * one and a backend with no case directory produce three DIFFERENT messages.
 * A refactor that collapses two of them back into one is exactly the regression.
 */

const OK_INFO = JSON.stringify({
  dctwinAvailable: true,
  caseDirOk: true,
  allOk: true,
  cpuCount: 48,
})

describe('diagnoseServerBody', () => {
  it('names the HTML case, which is the wrong --api-port', () => {
    // A Vite proxy pointed at a port with no server.py answers /api/* with the
    // SPA's index.html. resp.json() turns that into "Unexpected token <".
    const { diagnosis, info } = diagnoseServerBody('<!doctype html><html>…', 200, true)
    expect(diagnosis.kind).toBe('notRunning')
    expect(diagnosis.key).toBe('log.server_not_running')
    expect(info).toBeNull()
    expect(t(diagnosis.key)).toContain('start_server.sh')
  })

  it('quotes the first 80 characters of a body that is neither JSON nor HTML', () => {
    const body = 'x'.repeat(200)
    const { diagnosis } = diagnoseServerBody(body, 502, false)
    expect(diagnosis.kind).toBe('abnormal')
    expect(String(diagnosis.vars?.text)).toHaveLength(80)
  })

  it('does not report a 500 with a JSON body as merely "not ready"', () => {
    // notReady points the user at their case directory. A broken server is not
    // their case directory, and sending them there is the misdiagnosis.
    const { diagnosis, info } = diagnoseServerBody('{"error":"boom"}', 500, false)
    expect(diagnosis.kind).toBe('abnormal')
    expect(String(diagnosis.vars?.text)).toContain('500')
    expect(info).toBeNull()
  })

  it('reports ready only on allOk', () => {
    const ok = diagnoseServerBody(OK_INFO, 200, true)
    expect(ok.diagnosis.kind).toBe('ready')
    expect(ok.info?.cpuCount).toBe(48)
  })

  it('names which flag is false when the server is up but unusable', () => {
    const body = JSON.stringify({ dctwinAvailable: true, caseDirOk: false, allOk: false })
    const { diagnosis, info } = diagnoseServerBody(body, 200, true)
    expect(diagnosis.kind).toBe('notReady')
    expect(diagnosis.vars).toEqual({ dctwin: 'true', caseDir: 'false' })
    // The info is still kept: cpuCount and wsPort are usable even when allOk is
    // false, and the panels read them.
    expect(info).not.toBeNull()
    expect(t(diagnosis.key, diagnosis.vars)).toContain('caseDir=false')
  })
})

describe('diagnoseServerFailure', () => {
  it('carries the fetch error and points at the launcher', () => {
    const diagnosis = diagnoseServerFailure(new TypeError('Failed to fetch'))
    expect(diagnosis.kind).toBe('unreachable')
    expect(t(diagnosis.key, diagnosis.vars)).toContain('Failed to fetch')
    expect(t(diagnosis.key, diagnosis.vars)).toContain('start_server.sh')
  })
})

describe('isServerFault', () => {
  it('separates the server’s problem from the case directory’s', () => {
    expect(isServerFault(diagnoseServerFailure(new Error('x')))).toBe(true)
    expect(isServerFault(diagnoseServerBody('<html>', 200, true).diagnosis)).toBe(true)
    expect(isServerFault(diagnoseServerBody(OK_INFO, 200, true).diagnosis)).toBe(false)
    expect(
      isServerFault(diagnoseServerBody('{"allOk":false}', 200, true).diagnosis),
    ).toBe(false)
    expect(isServerFault(null)).toBe(false)
  })
})
