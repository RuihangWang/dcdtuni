import { beforeEach, describe, expect, it } from 'vitest'
import { renderServerBanner } from '../src/run/serverBanner'
import { diagnoseServerBody, diagnoseServerFailure } from '../src/run/serverDiagnosis'

/**
 * The backend diagnosis goes to the Operation Log only: a banner over the top
 * of the viewport column covered the toolbar's buttons. Nothing is drawn in
 * any state, and a banner an older page left behind is removed.
 */

function banner(): HTMLElement | null {
  return document.getElementById('server-status-banner')
}

beforeEach(() => {
  banner()?.remove()
})

describe('renderServerBanner', () => {
  it('draws nothing in any state', () => {
    for (const d of [
      null,
      diagnoseServerBody('{"allOk":true}', 200, true).diagnosis,
      diagnoseServerBody('<!doctype html>', 200, true).diagnosis,
      diagnoseServerBody('{"allOk":false,"caseDirOk":false}', 200, true).diagnosis,
      diagnoseServerFailure(new Error('down')),
    ])
    {
      renderServerBanner(d)
      expect(banner()).toBeNull()
    }
  })

  it('removes a banner an older page left behind', () => {
    const el = document.createElement('div')
    el.id = 'server-status-banner'
    document.body.appendChild(el)
    renderServerBanner(diagnoseServerFailure(new Error('down')))
    expect(banner()).toBeNull()
  })
})
