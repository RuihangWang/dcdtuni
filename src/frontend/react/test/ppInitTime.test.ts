import { afterEach, expect, it, vi } from 'vitest'
import { initRun } from '../src/postprocess'

afterEach(() => vi.unstubAllGlobals())

it('sends the selected OpenFOAM time with the postprocess init request', async () => {
  const calls: Array<[string, RequestInit]> = []
  vi.stubGlobal('fetch', async (url: string, request: RequestInit) => {
    calls.push([url, request])
    return { ok: true, json: async () => ({ success: true, timeValue: 1,
      runId: 'run-A', revision: 'revision-A' }) }
  })

  await initRun('log/run', 1)

  expect(calls[0]?.[0]).toBe('/api/pp/init')
  expect(JSON.parse(String(calls[0]?.[1].body))).toEqual({ runDir: 'log/run', timeValue: 1 })
})

it('refuses a result for another time instead of showing stale fields', async () => {
  vi.stubGlobal('fetch', async () => ({
    ok: true,
    json: async () => ({ success: true, timeValue: 2 }),
  }))

  await expect(initRun('log/run', 1)).rejects.toThrow(/requested.*1.*returned.*2/i)
})
