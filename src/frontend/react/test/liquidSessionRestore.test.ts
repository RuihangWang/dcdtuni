import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetRuntimeClient, restoreRuntimeSession, runtimeState } from '../src/liquid/liquidRuntimeClient'
import { fetchHybridHeatLedger, listSavedLiquidRuns, readSavedLiquidRun } from '../src/liquid/liquidSavedRuns'

function frame(id: string, seq = 0) {
  return { kind: 'frame', run_id: id, seq, time_s: seq, node_pressure_Pa: [150000], node_temperature_K: [300],
    edge_flow_kg_s: [0.1], chip_temp_K: [310], edge_outlet_temp_K: [305], controls: {}, diagnostics: {} }
}
const metadata = { node_ids: ['n'], edge_ids: ['e'], cp_edge_ids: ['e'], total_sample_count: 3, dt_s: 1, object_bindings: {} }
const restored = (id: string, extra: Record<string, unknown> = {}) =>
  ({ run_id: id, status: 'completed', config_hash: `hash-${id}`, metadata, frame: frame(id, 2), project_revision: 4,
    read_only: true, restored: true, error: null, ...extra })
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
afterEach(() => { resetRuntimeClient(); vi.restoreAllMocks() })

describe('restoring a saved liquid run after a reload', () => {
  it('restores the run read-only with every saved frame and the caller-checked revision', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json(restored('a')))
      .mockResolvedValueOnce(json({ run_id: 'a', status: 'completed', frames: [frame('a', 0), frame('a', 1), frame('a', 2)] }))
    const session = await restoreRuntimeSession('a', 9)
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/liquid/runs/a/restore')
    expect(fetch.mock.calls[0]?.[1]?.method).toBe('POST')
    expect(fetch.mock.calls[1]?.[0]).toBe('/api/liquid/runs/a/frames?after=-1')
    expect(session?.read_only).toBe(true)
    const state = runtimeState()
    expect(state.session?.status).toBe('completed')
    expect(state.frames.map(f => f.seq)).toEqual([0, 1, 2])
    expect(state.projectRevision).toBe(9)
    expect(state.connection).toBe('connected')
    expect(state.busy).toBe(false)
  })

  it('a run of another project revision is restored as stale (null revision)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json(restored('a')))
      .mockResolvedValueOnce(json({ run_id: 'a', status: 'completed', frames: [frame('a', 2)] }))
    await restoreRuntimeSession('a', null)
    expect(runtimeState().projectRevision).toBeNull()
  })

  it('reports the server refusal and keeps no half-restored session', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ error: 'saved run a lacks executed-inputs.json' }, 400))
    await expect(restoreRuntimeSession('a', 1)).rejects.toThrow('executed-inputs.json')
    expect(runtimeState().session).toBeNull()
    expect(runtimeState().error).toContain('executed-inputs.json')
  })

  it('refuses unsafe run ids and answers that belong to another run', async () => {
    await expect(restoreRuntimeSession('../x', 1)).rejects.toThrow(/run id/)
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json(restored('b')))
    await expect(restoreRuntimeSession('a', 1)).rejects.toThrow(/different run/)
    expect(runtimeState().session).toBeNull()
  })
})

describe('saved runs and the hybrid heat ledger', () => {
  it('lists the saved runs of the case', async () => {
    const run = { name: 'run-a', run_id: 'a', kind: 'dae', status: 'completed', restorable: true, restore_blocker: null, loaded: null }
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ case: 'Shinaide_Liquid', runs: [run] }))
    const listing = await listSavedLiquidRuns()
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/liquid/runs')
    expect(listing.case).toBe('Shinaide_Liquid')
    expect(listing.runs[0]?.restorable).toBe(true)
  })

  it('reads one saved run and the ledger, and passes server errors through', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json({ run: { run_id: 'a' }, metadata: {}, receipt: {}, artifacts: {} }))
      .mockResolvedValueOnce(json({ path: 'model/heat-ledger.json', matches_active_cfdrun: true,
        ledger: { format: 'hybrid-air-heat-handoff/v2', totals: { air_residual_W: 1 } } }))
      .mockResolvedValueOnce(json({ code: 'heat_ledger_missing', error: 'No model/heat-ledger.json' }, 404))
    expect((await readSavedLiquidRun('a')).run.run_id).toBe('a')
    expect((await fetchHybridHeatLedger())?.ledger.totals.air_residual_W).toBe(1)
    expect(await fetchHybridHeatLedger()).toBeNull()
    await expect(readSavedLiquidRun('a/b')).rejects.toThrow(/run id/)
  })
})
