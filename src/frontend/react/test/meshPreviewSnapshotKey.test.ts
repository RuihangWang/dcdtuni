import { afterEach, expect, it, vi } from 'vitest'
import { loadMeshPreview } from '../src/run/meshPreview'

afterEach(() => vi.unstubAllGlobals())

it('binds the binary response to the metadata mesh snapshot while preserving old servers', async () => {
  const key = 'a'.repeat(64)
  const urls: string[] = []
  vi.stubGlobal('fetch', async (url: string) => {
    urls.push(url)
    return url === '/api/mesh-preview/load'
      ? { ok: true, json: async () => ({ patches: [], totalPoints: 0, totalTriangles: 0, totalEdges: 0, meshSnapshotKey: key }) }
      : { ok: true, headers: { get: () => key }, arrayBuffer: async () => new ArrayBuffer(0) }
  })
  const loaded = await loadMeshPreview()
  expect(loaded.meta.meshSnapshotKey).toBe(key)
  expect(urls).toEqual(['/api/mesh-preview/load', `/api/mesh-preview/binary?meshSnapshotKey=${key}`])

  urls.length = 0
  vi.stubGlobal('fetch', async (url: string) => {
    urls.push(url)
    return url === '/api/mesh-preview/load'
      ? { ok: true, json: async () => ({ patches: [], totalPoints: 0, totalTriangles: 0, totalEdges: 0 }) }
      : { ok: true, arrayBuffer: async () => new ArrayBuffer(0) }
  })
  await loadMeshPreview()
  expect(urls).toEqual(['/api/mesh-preview/load', '/api/mesh-preview/binary'])
})

it('rejects a binary body whose snapshot echo differs from the metadata', async () => {
  const key = 'a'.repeat(64)
  vi.stubGlobal('fetch', async (url: string) => url === '/api/mesh-preview/load'
    ? { ok: true, json: async () => ({ patches: [], meshSnapshotKey: key }) }
    : { ok: true, headers: { get: () => 'b'.repeat(64) }, arrayBuffer: async () => new ArrayBuffer(0) })
  await expect(loadMeshPreview()).rejects.toThrow(/snapshot/i)
})
