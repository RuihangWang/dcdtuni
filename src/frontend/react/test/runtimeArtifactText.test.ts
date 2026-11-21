/** Downloaded run artifacts must be byte-faithful.
 * Stage 5 browser E2E found the old path (fetch → JSON.parse → JSON.stringify)
 * rewrote float formatting (100.0 → 100, 1e-05 → 0.00001), so a UI-downloaded
 * checkpoint failed the canonical config-hash check on replay. These tests pin
 * the raw-text contract of fetchRuntimeArtifactText. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as runtime from '../src/liquid/liquidRuntimeClient'

const RUN = 'run-artifact-text'
// Deliberately carries float spellings a JS round-trip would rewrite.
const RAW_CHECKPOINT = '{"format":"liquid-dae-replay-checkpoint","version":3,"value":100.0,"small":1e-05,"big":600.0}'

function fakeSession(id: string = RUN): runtime.RuntimeSession {
  const frame = {
    kind: 'frame', run_id: id, seq: 0, time_s: 0,
    node_pressure_Pa: [150000], node_temperature_K: [300], edge_flow_kg_s: [0.1],
    edge_outlet_temp_K: [305], chip_temp_K: [330], primary_return_K: [300],
    secondary_supply_K: [310], heat_multiplier: 1, pump_rpm_ratios: {},
    valve_openings: {}, primary_inlet_K: { hx_1: 290 },
    controls: { pump_rpm_ratios: {}, valve_openings: {}, primary_inlet_K: { hx_1: 290 } },
    diagnostics: { energy_balance_W: 0 },
  } as unknown as runtime.RuntimeFrame
  return {
    run_id: id, status: 'ready', config_hash: 'hash-artifact', frame,
    metadata: {
      solver: 'differentiable_newton_dae', node_ids: ['n1'], edge_ids: ['e1'],
      cp_edge_ids: ['e1'], hx_edge_ids: ['hx_1'], total_sample_count: 2, dt_s: 1,
      object_bindings: {}, pending_heat_multiplier: 1, pump_edge_ids: [],
      valve_edge_ids: [], pending_pump_rpm_ratios: {}, pending_valve_openings: {},
      pending_primary_inlet_K: {},
    },
  } as unknown as runtime.RuntimeSession
}

beforeEach(() => {
  runtime.resetRuntimeClient()
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url === '/api/liquid/runs') {
      return new Response(JSON.stringify(fakeSession()), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.endsWith('/checkpoint')) {
      return new Response(RAW_CHECKPOINT, { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.endsWith('/result')) {
      return new Response(JSON.stringify({ error: 'boom' }), { status: 500, headers: { 'Content-Type': 'application/json' } })
    }
    throw new Error('Unexpected route ' + url)
  })
})
afterEach(() => { runtime.resetRuntimeClient(); vi.restoreAllMocks() })

it('returns the server bytes verbatim — float spellings a JS round-trip would rewrite survive', async () => {
  await runtime.createRuntimeSession({ schemaVersion: 3 }, [], 1)
  const text = await runtime.fetchRuntimeArtifactText('checkpoint')
  expect(text).toBe(RAW_CHECKPOINT)
  // The corruption this guards against, demonstrated: parsing and re-stringifying
  // the same payload changes the bytes (100.0 → 100, 1e-05 → 0.00001).
  expect(JSON.stringify(JSON.parse(RAW_CHECKPOINT))).not.toBe(RAW_CHECKPOINT)
})

it('surfaces the server error body on a failed artifact fetch', async () => {
  await runtime.createRuntimeSession({ schemaVersion: 3 }, [], 1)
  await expect(runtime.fetchRuntimeArtifactText('result')).rejects.toThrow('boom')
})

it('refuses without a session', async () => {
  await expect(runtime.fetchRuntimeArtifactText('checkpoint'))
    .rejects.toThrow(/No liquid simulation session/)
})

it('does not attach a late artifact response to a new run', async () => {
  // Mirrors the guard the retired fetchRuntimeExecutedInput carried: a slow
  // artifact response must not land on a session created after the request.
  let release!: (r: Response) => void
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(json(fakeSession('run-a')))
    .mockReturnValueOnce(new Promise<Response>(resolve => { release = resolve }))
    .mockResolvedValueOnce(json(fakeSession('run-b')))
  await runtime.createRuntimeSession({ schemaVersion: 3 }, [], 1)
  const pending = runtime.fetchRuntimeArtifactText('checkpoint')
  await runtime.createRuntimeSession({ schemaVersion: 3 }, [], 2)
  release(new Response(RAW_CHECKPOINT, { status: 200, headers: { 'Content-Type': 'application/json' } }))
  await expect(pending).rejects.toThrow(/earlier run/)
})
