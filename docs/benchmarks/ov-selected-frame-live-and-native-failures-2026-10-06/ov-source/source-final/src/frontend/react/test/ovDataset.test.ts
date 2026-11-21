import { expect, it, vi } from 'vitest'
import { listOvRuns, requestOvDatasetGrant } from '../src/renderer/ovDataset'

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const RID = 'a'.repeat(64)

it('lists only runs carrying a well-formed server identity', async () => {
  const f = vi.fn(async () => json({ runs: [{ name: 'good', runId: RID }, { name: 'bad', runId: '../x' }, { name: 'none' }] }))
  expect(await listOvRuns(f)).toEqual([{ name: 'good', runId: RID }])
  expect(f).toHaveBeenCalledWith('/api/pp/runs')
})

it('asks for a grant by run identity only and returns it verbatim', async () => {
  const grant = { payload: { kind: 'dataset', path: '/srv/x.case' }, signature: 'f'.repeat(64) }
  const f = vi.fn(async (_u: string, _i?: RequestInit) => json({ grant, run: 'r', time: '2', fields: ['T'], cached: false, format: 'ensight' }))
  const out = await requestOvDatasetGrant(RID, f)
  expect(out.grant).toEqual(grant)
  const [url, init] = f.mock.calls[0]!
  expect(url).toBe('/api/ov/result-dataset')
  expect(JSON.parse(String(init!.body))).toEqual({ runId: RID })
})

it('surfaces the server refusal (e.g. no --ov key) instead of a blank dataset', async () => {
  const f = vi.fn(async () => json({ code: 'ov_grant_unavailable', error: 'Server was not started with --ov' }, 503))
  await expect(requestOvDatasetGrant(RID, f)).rejects.toThrow(/--ov/)
  const g = vi.fn(async () => json({ run: 'r' }))
  await expect(requestOvDatasetGrant(RID, g)).rejects.toThrow(/no display grant/)
})

it('preserves actual complete positive times and the transient marker from the server', async () => {
  const f = vi.fn(async () => json({ runs: [{ name: 'r', runId: RID, timesteps: [0, .01, .02], transient: true }] }))
  expect(await listOvRuns(f)).toEqual([{ name: 'r', runId: RID, timesteps: [.01, .02], transient: true }])
})

it('sends an explicit numeric frame without changing the legacy request shape', async () => {
  const grant = { payload: { kind: 'dataset', time: '1e-02', runId: RID }, signature: 'f'.repeat(64) }
  const f = vi.fn(async (_u: string, _i?: RequestInit) => json({ grant, time: '1e-02' }))
  await requestOvDatasetGrant(RID, f, .01)
  expect(JSON.parse(String(f.mock.calls[0]![1]!.body))).toEqual({ runId: RID, time: .01 })
})
