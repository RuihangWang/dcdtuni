import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MeshPreviewPanel } from '../../src/components/MeshPreviewPanel'
import { resetMeshPreviewSession } from '../../src/run/meshPreviewSession'
import { meshPreviewSummary } from '../../src/run/meshPreview'
import * as store from '../../src/projectStore'
import { t } from '../../src/i18n'
import type { MeshMeta, PatchMeta } from '../../src/postprocess'

/**
 * Loading a mesh preview: the stage dialog, cancel, and the size summary.
 *
 * Two gaps, one gesture. Pressing 加载网格 on a real case is 45 s of a disabled
 * button — the legacy runs it behind `showBlockingTaskDialog` with a line per
 * stage and a cancel that aborts both fetches (STL_generate.html:24340), and
 * ends by reporting what was loaded: `N patches, N cells, N boundary faces,
 * N points, N triangles` (:24467-24483), to the dialog AND the operation log.
 * React had the dialog component and dropped every count at the type boundary.
 *
 * The counts matter beyond curiosity: `nCells` is the number `maxGlobalCells`
 * is set against, so it is the one figure that says whether the mesh that was
 * just built is the size that was asked for.
 */

/** One triangle and one edge per patch — the binary the panel decodes. */
function fixture(count: number): { meta: MeshMeta; buffer: ArrayBuffer } {
  const patches: PatchMeta[] = Array.from({ length: count }, (_, i) => ({
    name: `p${i}`,
    nPoints: 3,
    nTriangles: 1,
    nEdges: 1,
    defaultVisible: true,
  })) as PatchMeta[]
  const positions = new Float32Array(count * 9)
  const indices = new Uint32Array(count * 3)
  const edges = new Uint32Array(count * 2)
  for (let i = 0; i < count; i++) {
    const base = i * 3
    positions.set([0, i, 0, 1, i, 0, 0, i, 1], base * 3)
    indices.set([base, base + 1, base + 2], i * 3)
    edges.set([base, base + 1], i * 2)
  }
  const buffer = new ArrayBuffer(
    positions.byteLength + indices.byteLength + edges.byteLength,
  )
  let off = 0
  new Float32Array(buffer, off, positions.length).set(positions)
  off += positions.byteLength
  new Uint32Array(buffer, off, indices.length).set(indices)
  off += indices.byteLength
  new Uint32Array(buffer, off, edges.length).set(edges)
  const meta = {
    patches,
    totalPoints: count * 3,
    totalTriangles: count,
    totalEdges: count,
    nCells: 1234567,
    nBoundaryFaces: 89012,
    nBoundarySum: 89012,
  } as MeshMeta
  return { meta, buffer }
}

describe('meshPreviewSummary', () => {
  const { meta } = fixture(3)

  it('reads like the legacy line, thousands separators and all', () => {
    expect(meshPreviewSummary(meta)).toBe(
      '3 patches, 1,234,567 cells, 89,012 boundary faces, 9 points, 3 triangles',
    )
  })

  it('says ? for a cell count the polyMesh header did not carry', () => {
    // `nCells` comes from the owner file's `note`. A mesh written without one
    // still previews; claiming 0 cells for it would be a lie.
    expect(meshPreviewSummary({ ...meta, nCells: null } as MeshMeta)).toContain('? cells')
  })

  it('falls back to the per-patch face sum, and says where it came from', () => {
    const fallback = meshPreviewSummary({
      ...meta,
      nBoundaryFaces: null,
      nBoundarySum: 4321,
    } as MeshMeta)
    expect(fallback).toContain('4,321 (from boundary) boundary faces')
  })
})

/** A fetch whose two responses are resolved by the test, one stage at a time. */
function stagedFetch(meta: MeshMeta, buffer: ArrayBuffer) {
  const gates: Array<() => void> = []
  const calls: RequestInit[] = []
  const wait = (init: RequestInit | undefined, done: () => unknown) =>
    new Promise((resolve, reject) => {
      init?.signal?.addEventListener('abort', () =>
        reject(new DOMException('aborted', 'AbortError')),
      )
      gates.push(() => resolve(done()))
    })
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    calls.push(init ?? {})
    return url.includes('binary')
      ? wait(init, () => ({ ok: true, headers: { get: () => null }, arrayBuffer: async () => buffer }))
      : wait(init, () => ({ ok: true, json: async () => meta }))
  })
  return {
    fetchMock,
    calls,
    /** Let the next pending request answer, then flush React. */
    async release() {
      gates.shift()?.()
      for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0))
    },
  }
}

describe('the 加载网格 gesture', () => {
  let host: HTMLDivElement
  let root: Root
  let staged: ReturnType<typeof stagedFetch>

  const dialog = () => document.getElementById('blocking-task-dialog')
  const lines = () =>
    [...(document.getElementById('blocking-task-log')?.children ?? [])].map(
      (el) => el.textContent ?? '',
    )
  const click = (id: string) => {
    const el = document.getElementById(id) as HTMLElement
    flushSync(() => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  }

  beforeEach(() => {

    // The session is module-level and outlives the panel: a load left pending
    // by the previous test would hand this one a disabled 加载网格 button.
    resetMeshPreviewSession()
    const { meta, buffer } = fixture(3)
    staged = stagedFetch(meta, buffer)
    vi.stubGlobal('fetch', staged.fetchMock)
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    flushSync(() =>
      root.render(<MeshPreviewPanel />),
    )
  })

  afterEach(() => {
    resetMeshPreviewSession()
    flushSync(() => root.unmount())
    host.remove()
    vi.unstubAllGlobals()
  })

  it('runs behind the staged dialog, not a silently disabled button', async () => {
    click('mp-load-btn')
    await new Promise((r) => setTimeout(r, 0))
    flushSync(() => {})
    expect(dialog()).not.toBeNull()
    // The first stage is logged BEFORE the request it describes — otherwise the
    // dialog opens empty for however long the server takes to answer.
    expect(lines().length).toBeGreaterThan(0)
  })

  it('logs each stage as it starts', async () => {
    click('mp-load-btn')
    await staged.release()
    // Metadata answered: the binary stage names its size, which is the only
    // hint of how long the next wait will be.
    expect(lines().join('\n')).toMatch(/9.*3/s)
    expect(lines().length).toBeGreaterThanOrEqual(2)
  })

  it('hands both fetches the same abort signal', async () => {
    click('mp-load-btn')
    await staged.release()
    expect(staged.calls).toHaveLength(2)
    for (const init of staged.calls) expect(init.signal).toBeInstanceOf(AbortSignal)
    expect(staged.calls[0]!.signal).toBe(staged.calls[1]!.signal)
  })

  it('cancels mid-load without leaving an error on the panel', async () => {
    click('mp-load-btn')
    await staged.release()
    click('pp-init-cancel')
    for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0))
    flushSync(() => {})
    expect(dialog()).toBeNull()
    // An abort is a choice, not a failure: the legacy logs it and shows nothing.
    expect(host.querySelector('.error')).toBeNull()
    expect(document.getElementById('mp-mesh-summary')?.textContent ?? '').toBe('')
    // The log entry is the receipt that the abort was HANDLED rather than
    // swallowed — the panel shows nothing at all in this path.
    expect(store.getLog()[0]).toMatchObject({
      action: 'meshPreview',
      detail: t('mp.load_cancelled'),
    })
  })

  it('ends with the summary, on the panel and in the operation log', async () => {
    click('mp-load-btn')
    await staged.release()
    await staged.release()
    const summary = '3 patches, 1,234,567 cells, 89,012 boundary faces, 9 points, 3 triangles'
    expect(document.getElementById('mp-mesh-summary')?.textContent).toBe(summary)
    expect(store.getLog()[0]?.detail).toBe(summary)
    // And the dialog is gone — it is not a results window.
    expect(dialog()).toBeNull()
  })
})
