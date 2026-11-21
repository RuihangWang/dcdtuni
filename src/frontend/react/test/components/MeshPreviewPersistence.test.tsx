import * as store from '../../src/projectStore'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MeshPreviewPanel } from '../../src/components/MeshPreviewPanel'
import {
  closeMeshPreviewSession,
  getMeshPreviewSession,
  resetMeshPreviewSession,
  setMeshPreviewGeoOpacity,
  setMeshPreviewVisible,
} from '../../src/run/meshPreviewSession'
import { resetWorkspace } from '../../src/workspaceReset'
import type { MeshMeta, PatchMeta } from '../../src/postprocess'

/**
 * The mesh preview outlives the tab it was loaded on.
 *
 * It used to live in the panel, and the panel only exists while the mesh tab's
 * preview sub-tab is up — so switching to 几何与属性 to look at a solid disposed
 * a multi-million-triangle mesh and coming back re-downloaded it. The rule the
 * user asked for is that a preview ends when they CLOSE it, and that a tab
 * switch only hides the group at the Three.js level with the buffers still in
 * memory.
 *
 * The other half is the exception to that rule: a new `.cfdproj` can arrive at
 * any moment, and every load funnels through `resetWorkspace`. A mesh from the
 * case being closed describes another room's `polyMesh/boundary`, so it must go.
 */

/** One triangle and one edge per named patch — the binary the panel decodes. */
function fixture(names: string[]): { meta: MeshMeta; buffer: ArrayBuffer } {
  const count = names.length
  const patches = names.map((name) => ({
    name,
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
  const buffer = new ArrayBuffer(positions.byteLength + indices.byteLength + edges.byteLength)
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
    nCells: 1000,
    nBoundaryFaces: count,
    nBoundarySum: count,
  } as MeshMeta
  return { meta, buffer }
}

let host: HTMLDivElement
let root: Root
let fetchMock: ReturnType<typeof vi.fn>
/** Every group the session handed the viewport, and every fade it asked for. */
let attached: unknown[]
let opacities: number[]

function mount() {
  flushSync(() => root.render(<MeshPreviewPanel />))
}

function unmount() {
  flushSync(() => root.unmount())
}

async function load(names: string[]) {
  const { meta, buffer } = fixture(names)
  fetchMock.mockImplementation(async (url: string) =>
    url.includes('binary')
      ? { ok: true, arrayBuffer: async () => buffer }
      : { ok: true, json: async () => meta },
  )
  const btn = document.getElementById('mp-load-btn') as HTMLElement
  flushSync(() => btn.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
  flushSync(() => {})
}

const group = () => getMeshPreviewSession().built?.group ?? null
const rowCount = () => document.querySelectorAll('#mp-patch-list [data-patch]').length

beforeEach(() => {
  resetMeshPreviewSession()
  attached = []
  opacities = []
  const w = window as unknown as {
    __attachMeshPreview?: (g: unknown) => void
    __setGeometryOpacity?: (v: number) => void
  }
  w.__attachMeshPreview = (g) => attached.push(g)
  w.__setGeometryOpacity = (v) => opacities.push(v)
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  mount()
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
  const w = window as unknown as {
    __attachMeshPreview?: unknown
    __setGeometryOpacity?: unknown
  }
  delete w.__attachMeshPreview
  delete w.__setGeometryOpacity
  resetMeshPreviewSession()
})

describe('leaving the mesh tab', () => {
  it('hides the group and keeps every buffer', async () => {
    await load(['inlet', 'outlet'])
    const built = getMeshPreviewSession().built
    expect(built).not.toBeNull()
    expect(group()!.visible).toBe(true)

    setMeshPreviewVisible(false)

    // Hidden at the Three.js level, still parented, still holding its patches.
    expect(group()!.visible).toBe(false)
    expect(attached[attached.length - 1]).toBe(group())
    expect(getMeshPreviewSession().built).toBe(built)
    expect(opacities[opacities.length - 1]).toBe(1)
  })

  it('keeps the mesh across an unmount, and needs no second load to come back', async () => {
    await load(['inlet', 'outlet'])
    const built = getMeshPreviewSession().built
    const calls = fetchMock.mock.calls.length
    expect(calls).toBeGreaterThan(0)

    unmount()
    setMeshPreviewVisible(false)

    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    setMeshPreviewVisible(true)
    mount()

    expect(fetchMock.mock.calls.length).toBe(calls)
    expect(getMeshPreviewSession().built).toBe(built)
    expect(group()!.visible).toBe(true)
    // The geometry fade is back up with the mesh (the preview's own value, 30% by default).
    expect(opacities[opacities.length - 1]).toBeCloseTo(0.3)
  })

  it('fades the geometry only while the mesh is drawn, and never edits the models\' opacity', async () => {
    await load(['inlet'])
    const before = JSON.stringify(store.getProject())
    setMeshPreviewGeoOpacity(0.4)
    expect(opacities[opacities.length - 1]).toBeCloseTo(0.4)
    // Leaving the tab takes the fade down: the geometry is back at its own opacity.
    setMeshPreviewVisible(false)
    expect(opacities[opacities.length - 1]).toBe(1)
    // Coming back puts this tab's value up again.
    setMeshPreviewVisible(true)
    expect(opacities[opacities.length - 1]).toBeCloseTo(0.4)
    // The fade is a view: the document (the geometry tab's opacities) is untouched.
    expect(JSON.stringify(store.getProject())).toBe(before)
  })
})

describe('the two things that really end it', () => {
  it('关闭预览 disposes and detaches', async () => {
    await load(['inlet'])
    closeMeshPreviewSession()
    expect(getMeshPreviewSession().built).toBeNull()
    expect(attached[attached.length - 1]).toBeNull()
    expect(opacities[opacities.length - 1]).toBe(1)
  })

  it('loading a project clears it, through the same reset every load runs', async () => {
    await load(['inlet', 'outlet'])
    expect(getMeshPreviewSession().patches.length).toBe(2)

    resetWorkspace()

    const session = getMeshPreviewSession()
    expect(session.built).toBeNull()
    expect(session.patches).toEqual([])
    expect(session.summary).toBe('')
    expect(session.hidden.size).toBe(0)
    expect(session.loadLines).toEqual([])
    expect(attached[attached.length - 1]).toBeNull()
    expect(opacities[opacities.length - 1]).toBe(1)
  })

  it('a project load also drops the settings, which belong to the closed case', async () => {
    await load(['inlet'])
    setMeshPreviewGeoOpacity(0.55)
    setMeshPreviewVisible(false)
    resetWorkspace()
    const session = getMeshPreviewSession()
    expect(session.geoOpacity).not.toBe(0.55)
    expect(session.visible).toBe(true)
    expect(session.collapsed).toEqual({})
  })
})

describe('the patch list', () => {
  it('is back after a remount, with the fold state the load set', async () => {
    await load(['inlet', 'outlet'])
    const folded = getMeshPreviewSession().collapsed
    expect(Object.values(folded).every((v) => v === true)).toBe(true)
    expect(rowCount()).toBe(0)

    unmount()
    host = document.createElement('div')
    document.body.appendChild(host)
    root = createRoot(host)
    mount()

    // Still folded — a remount must not re-fold a list the user had opened.
    expect(getMeshPreviewSession().collapsed).toEqual(folded)
  })
})
