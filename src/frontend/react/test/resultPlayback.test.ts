// Results on the timeline (transient design §2, plan T5a): the playhead picks the
// liquid result sample nearest its time, the result's own slider moves the
// playhead, and a fresh run still opens at its latest sample.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '../src/legacy/geometryBuilder'
import * as store from '../src/projectStore'
import { readProjectFileText } from '../src/io/projectFiles'
import { fetchLiquidResults, resetLiquidRun } from '../src/liquid/liquidSession'
import { selectedResultSample, setResultSample } from '../src/liquid/liquidTempColor'
import { setSimulationMode } from '../src/transient/simulationMode'
import { nearestIndex, pickLiquidSample, startResultPlayback } from '../src/transient/resultPlayback'

const CASE = resolve(process.cwd(), '../../../case/Base_coldAisleIso/Base_coldAisleIso.cfdproj')

function result(runId: string, times: number[]) {
  const s = (v: number) => times.map(() => v)
  return { run_id: runId, converged: true, time_s: times,
    rack_summaries: [{ rack_id: 'rack', total_flow_kg_s: s(1), supply_temp_K: s(300), return_temp_K: s(310),
      servers: [{ server_id: 'server', flow_kg_s: s(1), max_chip_temp_K: s(320), heat_removed_W: s(100), cp_edge_ids: [] }] }],
    cp_details: [] }
}
async function loadResult(value: unknown) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  await fetchLiquidResults()
}

let stop: () => void
beforeEach(() => {
  const r = readProjectFileText(readFileSync(CASE, 'utf8'))
  if (!r.ok) throw new Error(r.error)
  store.loadProject(r.project, null)
  setSimulationMode('transient')                   // durationS 600, gridS 1
  resetLiquidRun()
  setResultSample(-1)
  stop = startResultPlayback()
})
afterEach(() => {
  stop()
  store.setPlayhead(0)
  resetLiquidRun()
  vi.restoreAllMocks()
})

describe('nearestIndex', () => {
  it('picks the nearest time, the earlier one on a tie, and clamps outside the range', () => {
    expect(nearestIndex([], 3)).toBe(-1)
    expect(nearestIndex([0, 10, 20], 4)).toBe(0)
    expect(nearestIndex([0, 10, 20], 5)).toBe(0)
    expect(nearestIndex([0, 10, 20], 6)).toBe(1)
    expect(nearestIndex([0, 10, 20], 100)).toBe(2)
    expect(nearestIndex([0, 10, 20], -3)).toBe(0)
    expect(nearestIndex([0, 0.1, 0.2, 0.30000000000000004], 0.3)).toBe(3)
  })
})

describe('the liquid result follows the playhead', () => {
  it('moving the playhead selects the nearest sample', async () => {
    await loadResult(result('a', [0, 10, 20, 30]))
    expect(selectedResultSample()).toBe(-1)          // a fresh result opens at its latest sample
    expect(store.playheadTime()).toBe(30)             // its clock shows that same sample
    store.setPlayhead(20)
    expect(selectedResultSample()).toBe(2)
    store.setPlayhead(14)
    expect(selectedResultSample()).toBe(1)
  })

  it('the slider moves the playhead and the grid snap does not take the pick back', async () => {
    await loadResult(result('a', [0, 0.5, 1, 1.5]))
    pickLiquidSample(1)                               // 0.5 s; the playhead snaps to the 1 s grid
    expect(selectedResultSample()).toBe(1)
    expect(store.playheadTime()).toBe(1)
    pickLiquidSample(3)
    expect(selectedResultSample()).toBe(3)
    expect(store.playheadTime()).toBe(2)
  })

  it('a steady result, or a steady project, is left alone', async () => {
    await loadResult(result('steady', [0]))
    store.setPlayhead(20)
    expect(selectedResultSample()).toBe(-1)
    await loadResult(result('b', [0, 10, 20]))
    setSimulationMode('steady')                      // resets the playhead to 0 after the mode is steady
    expect(selectedResultSample()).toBe(-1)
    store.setPlayhead(20)
    expect(store.playheadTime()).toBe(0)
    expect(selectedResultSample()).toBe(-1)
  })

  it('opening a cancelled transient initial frame resets the preceding completed clock', async () => {
    await loadResult(result('completed', [0, 10, 600]))
    expect(store.playheadTime()).toBe(600)
    await loadResult({
      ...result('cancelled', [0]),
      status: 'cancelled', completed: false,
      analysis: { mode: 'transient', initialization: 'steady' },
      expected_duration_s: 600,
    })
    expect(store.playheadTime()).toBe(0)
    expect(selectedResultSample()).toBe(-1)
  })

  it('a new run opens at its latest sample again', async () => {
    await loadResult(result('a', [0, 10, 20, 30]))
    store.setPlayhead(10)
    expect(selectedResultSample()).toBe(1)
    await loadResult(result('b', [0, 10, 20, 40]))
    expect(selectedResultSample()).toBe(-1)
    expect(store.playheadTime()).toBe(40)
  })

  it('stopping unsubscribes, and starting twice subscribes once', async () => {
    await loadResult(result('a', [0, 10, 20, 30]))
    expect(startResultPlayback()).toBe(stop)
    stop()
    store.setPlayhead(20)
    expect(selectedResultSample()).toBe(-1)
    stop = startResultPlayback()
  })
})
