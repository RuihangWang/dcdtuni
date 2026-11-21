import { afterEach, expect, it, vi } from 'vitest'
import {
  fetchField,
  fetchMesh,
  fetchMeshMeta,
  fetchSlice,
  fetchStreamlines,
  initRun,
} from '../src/postprocess'
import { fetchVolume } from '../src/postprocess/ppVolume'
import { runAiInference, startAiWorker } from '../src/postprocess/ppAi'

const session = { runId: 'run-A', timeValue: 1, revision: 'rev-A' }

function envelope(header: object, data?: Float32Array): ArrayBuffer {
  const encoded = new TextEncoder().encode(JSON.stringify(header))
  const padded = Math.ceil(encoded.length / 4) * 4
  const bytes = new ArrayBuffer(4 + padded + (data?.byteLength ?? 0))
  new DataView(bytes).setUint32(0, padded, true)
  new Uint8Array(bytes, 4).set(encoded)
  if (data) new Float32Array(bytes, 4 + padded).set(data)
  return bytes
}

afterEach(() => vi.unstubAllGlobals())

it('rejects an init response without a snapshot revision', async () => {
  vi.stubGlobal('fetch', async () => ({
    ok: true,
    json: async () => ({ success: true, timeValue: 1, runId: 'run-A' }),
  }))
  await expect(initRun('log/run', 1)).rejects.toThrow(/revision/i)
})

it('binds every result-data request to one revision and verifies binary echoes', async () => {
  const calls: Array<[string, RequestInit | undefined]> = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push([url, init])
    const headers = { get: (name: string) => name === 'X-PP-Revision' ? 'rev-A' : null }
    if (url === '/api/pp/mesh') return {
      ok: true,
      json: async () => ({ patches: [], fields: [], totalPoints: 0, totalTriangles: 0,
        runId: 'run-A', timeValue: 1, revision: 'rev-A' }),
    }
    if (url === '/api/pp/mesh/binary') return { ok: true, headers, arrayBuffer: async () => new ArrayBuffer(0) }
    if (url === '/api/pp/field/T') return { ok: true, headers, arrayBuffer: async () => new ArrayBuffer(0) }
    if (url === '/api/pp/slice') return { ok: true, headers, arrayBuffer: async () =>
      envelope({ nPoints: 0, nTriangles: 0, scalarRange: [0, 0], field: 'T' }) }
    if (url === '/api/pp/streamlines') return { ok: true, headers, arrayBuffer: async () =>
      envelope({ streamlines: [], scalarRange: [0, 0], field: 'speed' }) }
    if (url === '/api/pp/volume') return { ok: true, headers, arrayBuffer: async () =>
      envelope({ dimensions: [1, 1, 1], origin: [0, 0, 0], spacing: [1, 1, 1],
        scalarRange: [301, 301], sentinel: -9999, field: 'T' }, new Float32Array([301])) }
    throw new Error(`Unexpected request ${url}`)
  })

  const meta = await fetchMeshMeta(session)
  await fetchMesh(meta, session)
  await fetchField('T', session)
  await fetchSlice({ origin: [0, 0, 0], normal: [0, 0, 1], field: 'T' }, session)
  await fetchStreamlines({ center: [0, 0, 0], radius: 1, nSeeds: 1 }, session)
  await fetchVolume('T', 16, session)
  expect(calls.map(([url]) => url)).toEqual([
    '/api/pp/mesh', '/api/pp/mesh/binary', '/api/pp/field/T', '/api/pp/slice',
    '/api/pp/streamlines', '/api/pp/volume',
  ])
  for (const [, init] of calls) {
    expect(new Headers(init?.headers).get('X-PP-Revision')).toBe('rev-A')
  }
})

it('rejects a binary response whose revision differs from the requested frame', async () => {
  vi.stubGlobal('fetch', async () => ({
    ok: true,
    headers: { get: (name: string) => name === 'X-PP-Revision' ? 'rev-B' : null },
    arrayBuffer: async () => new ArrayBuffer(0),
  }))
  await expect(fetchField('T', session)).rejects.toThrow(/revision/i)
})

it('binds AI start and inference requests to the active revision', async () => {
  const calls: RequestInit[] = []
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    calls.push(init)
    return { ok: true, json: async () => ({ success: true, runId: 'run-A', timeValue: 1,
      predictionSource: 'independent_h5_graph', timeConditioned: false,
      revision: url.endsWith('/infer') ? 'rev-B' : 'rev-A' }) }
  })
  await startAiWorker('model.state', '/case', session)
  await runAiInference({ min: [0, 0, 0], max: [1, 1, 1] }, session)
  expect(calls).toHaveLength(2)
  for (const init of calls) expect(new Headers(init.headers).get('X-PP-Revision')).toBe('rev-A')
})

it('refuses an AI response that omits its independent-H5 time provenance', async () => {
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({ success: true,
    runId: 'run-A', timeValue: 1, revision: 'rev-A' }) }))
  await expect(startAiWorker('model.state', '/case', session)).rejects.toThrow(/H5|time/i)
})
