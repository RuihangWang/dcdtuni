import { afterEach, expect, it, vi } from 'vitest'
import { getMeshPreviewSession, loadMeshPreviewSession, resetMeshPreviewSession } from '../src/run/meshPreviewSession'
import { clearResultLayerSet, currentResultLayerSet } from '../src/renderer/resultLayerRevision'

afterEach(() => {
  resetMeshPreviewSession()
  clearResultLayerSet()
  vi.unstubAllGlobals()
})

it('keeps a real Three.js preview when trusted OV lineage is unavailable', async () => {
  resetMeshPreviewSession()
  const key = 'a'.repeat(64)
  const bytes = new ArrayBuffer(3 * 3 * 4 + 3 * 4 + 3 * 2 * 4)
  new Float32Array(bytes, 0, 9).set([0, 0, 0, 1, 0, 0, 0, 1, 0])
  new Uint32Array(bytes, 36, 3).set([0, 1, 2])
  new Uint32Array(bytes, 48, 6).set([0, 1, 1, 2, 2, 0])
  const urls: string[] = []
  vi.stubGlobal('fetch', async (url: string) => {
    urls.push(url)
    if (url === '/api/mesh-preview/load') return { ok: true, json: async () => ({
      patches: [{ name: 'wall', nPoints: 3, nTriangles: 1, nEdges: 3, defaultVisible: true }],
      totalPoints: 3, totalTriangles: 1, totalEdges: 3, meshSnapshotKey: key,
    }) }
    if (url.includes('/api/mesh-preview/binary')) return { ok: true,
      headers: { get: () => key }, arrayBuffer: async () => bytes }
    if (url.includes('/api/mesh-preview/render-source')) return { ok: false, status: 422,
      json: async () => ({ code: 'render_source_unavailable', missing: ['verified_patch_lineage'] }) }
    throw new Error(`Unexpected request ${url}`)
  })

  await loadMeshPreviewSession()
  for (let i = 0; i < 4; i++) await new Promise((resolve) => setTimeout(resolve, 0))

  expect(getMeshPreviewSession().built).not.toBeNull()
  expect(getMeshPreviewSession().summary).toContain('1 patches')
  expect(urls).toContain(`/api/mesh-preview/render-source?meshSnapshotKey=${key}`)
  expect(currentResultLayerSet()).toBeNull()
})
