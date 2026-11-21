import { afterEach, expect, it, vi } from 'vitest'
import { fetchVerifiedAirResultSource, fetchVerifiedMeshPreviewSource } from '../src/renderer/windResultSourceClient'

afterEach(() => vi.unstubAllGlobals())

it('treats missing trusted receipts as no OV packet while leaving Three.js loads usable', async () => {
  const calls: Array<[string, RequestInit | undefined]> = []
  vi.stubGlobal('fetch', async (url: string, request?: RequestInit) => {
    calls.push([url, request])
    return { status: 422, ok: false, json: async () => ({
      code: 'render_source_unavailable', missing: ['verified_patch_lineage', 'mesh_quality_receipt', 'run_result_receipt'],
    }) }
  })
  const session = { runId: 'run-1', timeValue: 20, revision: 'pp-1' }
  expect(await fetchVerifiedAirResultSource(session)).toBeNull()
  expect(await fetchVerifiedMeshPreviewSource('a'.repeat(64))).toBeNull()
  expect(calls[0]?.[0]).toBe('/api/pp/render-source')
  expect((calls[0]?.[1]?.headers as Headers).get('X-PP-Revision')).toBe('pp-1')
  expect(calls[1]?.[0]).toContain('meshSnapshotKey=' + 'a'.repeat(64))
})

it('does not accept a descriptor for an old PP revision or another preview mesh', async () => {
  const source = { projectUid: 'p', projectContentSha256: 'a'.repeat(64),
    cfdrunSha256: 'b'.repeat(64), meshReceiptSha256: 'c'.repeat(64), meshQuality: 'accepted',
    run: { uid: 'run-receipt', receiptSha256: 'd'.repeat(64) } }
  vi.stubGlobal('fetch', async (url: string) => ({ status: 200, ok: true, json: async () => (
    url.includes('/api/pp/')
      ? { schema: 'dcdtuni.verified-air-result-source/v1', postprocess: { runId: 'run-1', timeValue: 20, revision: 'old' },
        source, sourceFacesByPatch: { wall: ['p/wall/source-face'] }, fieldUnits: { T: 'K' } }
      : { schema: 'dcdtuni.verified-mesh-preview-source/v1', meshSnapshotKey: 'f'.repeat(64),
        source: { ...source, run: null }, sourceFacesByPatch: { wall: ['p/wall/source-face'] } }
  ) }))
  await expect(fetchVerifiedAirResultSource({ runId: 'run-1', timeValue: 20, revision: 'pp-1' })).rejects.toThrow(/revision/i)
  await expect(fetchVerifiedMeshPreviewSource('a'.repeat(64))).rejects.toThrow(/mesh/i)
})
