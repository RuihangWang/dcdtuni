import { describe, expect, it } from 'vitest'
import { abortable } from '../src/postprocess/ppAbort'
import { SLICE_NORMALS } from '../src/postprocess/ppConfig'
import { getLocale, setLocale } from '../src/i18n'

describe('abortable', () => {
  it('passes the value through when nothing aborts', async () => {
    const ac = new AbortController()
    await expect(abortable(Promise.resolve(7), ac.signal)).resolves.toBe(7)
  })

  it('rejects with AbortError as soon as the signal fires, before the work finishes', async () => {
    const ac = new AbortController()
    let settled = false
    const slow = new Promise<number>((resolve) => {
      setTimeout(() => {
        settled = true
        resolve(1)
      }, 50)
    })
    const raced = abortable(slow, ac.signal)
    ac.abort()
    await expect(raced).rejects.toMatchObject({ name: 'AbortError' })
    // The point of the helper: /api/pp/volume has no cancel, so the request is
    // still in flight — the caller has stopped waiting for it, that is all.
    expect(settled).toBe(false)
  })

  it('rejects immediately when the signal was already aborted', async () => {
    const ac = new AbortController()
    ac.abort()
    await expect(abortable(Promise.resolve(1), ac.signal)).rejects.toMatchObject({
      name: 'AbortError',
    })
  })

  it('forwards a real failure unchanged', async () => {
    const ac = new AbortController()
    await expect(abortable(Promise.reject(new Error('boom')), ac.signal)).rejects.toThrow('boom')
  })
})

describe('SLICE_NORMALS', () => {
  it('names the axes the way the legacy select does', () => {
    expect(SLICE_NORMALS.map((n) => n.id)).toEqual(['x', 'y', 'z', 'custom'])
    expect(SLICE_NORMALS.slice(0, 3).map((n) => n.label)).toEqual(['X', 'Y', 'Z'])
  })

  it('translates the custom option, and follows a locale switch', () => {
    const before = getLocale()
    try {
      setLocale('en-US')
      expect(SLICE_NORMALS[3]?.label).toBe('Custom')
      setLocale('zh-CN')
      // Read again from the same array: the label is a getter precisely so the
      // two selects that render it do not freeze the boot language.
      expect(SLICE_NORMALS[3]?.label).not.toBe('Custom')
    } finally {
      setLocale(before)
    }
  })
})
