import { describe, expect, it } from 'vitest'
import { getLocale, setLocale } from '../src/i18n'
import {
  CLOSE_NOT_AUTHENTICATED,
  terminalCloseMessage,
  terminalCloseReason,
} from '../src/run/terminalStatus'

/**
 * `[terminal disconnected]` reads as "the backend died", and the commonest
 * cause is nothing of the sort — an SSH tunnel that forwarded 8005 and not
 * 8006. These pin the two things the legacy message carries that the port had
 * dropped: which failure it was, and that mesh/solve are unaffected.
 */

describe('terminalCloseReason', () => {
  it('names the port on an unreachable socket', () => {
    const before = getLocale()
    try {
      setLocale('en-US')
      expect(terminalCloseReason(1006, 8006)).toContain('8006')
      expect(terminalCloseReason(1006, 8006)).not.toContain('{port}')
    } finally {
      setLocale(before)
    }
  })

  it('reports an auth refusal on the server’s own close code, not a port problem', () => {
    const before = getLocale()
    try {
      setLocale('en-US')
      expect(CLOSE_NOT_AUTHENTICATED).toBe(4001)
      const reason = terminalCloseReason(CLOSE_NOT_AUTHENTICATED, 8006)
      expect(reason).toMatch(/authenticated/i)
      expect(reason).not.toContain('8006')
    } finally {
      setLocale(before)
    }
  })
})

describe('terminalCloseMessage', () => {
  it('always ends with the mesh/solve reassurance', () => {
    const before = getLocale()
    try {
      setLocale('en-US')
      for (const code of [1006, 1000, CLOSE_NOT_AUTHENTICATED]) {
        expect(terminalCloseMessage(code, 8006)).toContain('/api/*')
      }
    } finally {
      setLocale(before)
    }
  })

  it('translates, so a zh-CN user does not read English', () => {
    const before = getLocale()
    try {
      setLocale('zh-CN')
      const zh = terminalCloseMessage(1006, 8006)
      setLocale('en-US')
      expect(terminalCloseMessage(1006, 8006)).not.toBe(zh)
      // The port survives the switch: it is the actionable half.
      expect(zh).toContain('8006')
    } finally {
      setLocale(before)
    }
  })
})
