import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PostprocessPanel } from '../../src/components/PostprocessPanel'
import { clearResultLayerSet, currentResultLayerSet, publishResultLayerSet } from '../../src/renderer/resultLayerRevision'
import type { ResultLayerSet } from '../../src/renderer/resultLayers'
import * as store from '../../src/projectStore'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import type { CfdProject } from '../../src/schema/cfdproj'

const CASE_DIR = '/fixture/postprocess-time-selection'
beforeEach(() => {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  project.meta = { ...project.meta, casePath: CASE_DIR }
  store.loadProject(project, null)
})

function meshBytes(): ArrayBuffer {
  const bytes = new ArrayBuffer(3 * 3 * 4 + 3 * 4 + 3 * 2 * 4)
  new Float32Array(bytes, 0, 9).set([0, 0, 0, 1, 0, 0, 0, 1, 0])
  new Uint32Array(bytes, 36, 3).set([0, 1, 2])
  new Uint32Array(bytes, 48, 6).set([0, 1, 1, 2, 2, 0])
  return bytes
}

function fieldBytes(): ArrayBuffer {
  const bytes = new ArrayBuffer(16 + 3 * 4)
  const header = new DataView(bytes)
  header.setFloat32(0, 300, true)
  header.setFloat32(4, 302, true)
  header.setUint32(8, 1, true)
  header.setUint32(12, 3, true)
  new Float32Array(bytes, 16, 3).set([300, 301, 302])
  return bytes
}

afterEach(() => {
  clearResultLayerSet()
  vi.unstubAllGlobals()
  delete (window as { __ppViewport?: unknown }).__ppViewport
})

it('releases its own OV result on tab unmount without erasing a mesh-preview packet', () => {
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({ caseDir: CASE_DIR, logRuns: [] }) }))
  const source = { projectUid: 'p', projectContentSha256: 'a'.repeat(64),
    cfdrunSha256: 'b'.repeat(64), meshReceiptSha256: 'c'.repeat(64),
    meshQuality: 'accepted' as const, run: { uid: 'run', receiptSha256: 'd'.repeat(64) } }
  const air: ResultLayerSet = {
    schema: 'dcdtuni.render-result-layers/v1', revision: 1, source,
    coordinateSystem: { upAxis: 'Y', metersPerUnit: 1, handedness: 'right' }, timeSeconds: 10,
    layers: [{ kind: 'meshEdges', layerId: 'edge/wall', meshPatchName: 'wall',
      sourceFaceUids: ['p/wall'], visible: true, opacity: 0.85,
      positions: [0, 0, 0, 1, 0, 0], indices: [0, 1] }],
  }
  const host = document.createElement('div')
  document.body.append(host)
  const first: Root = createRoot(host)
  flushSync(() => first.render(<PostprocessPanel />))
  publishResultLayerSet(air, source, 10, undefined, 'air-postprocess', {
    owner: 'air-postprocess', session: { runId: 'selected-run', timeValue: 10, revision: 'pp-1' },
    surfaceField: 'T', meshReceiptSha256: source.meshReceiptSha256,
  })
  flushSync(() => first.unmount())
  expect(currentResultLayerSet()).toBeNull()

  const preview = { ...air, revision: 2, source: { ...source, run: null }, timeSeconds: null }
  const second: Root = createRoot(host)
  flushSync(() => second.render(<PostprocessPanel />))
  publishResultLayerSet(preview, preview.source, null, undefined, 'mesh-preview', {
    owner: 'mesh-preview', meshSnapshotKey: 'e'.repeat(64),
  })
  flushSync(() => second.unmount())
  expect(currentResultLayerSet()).toEqual(preview)
  host.remove()
})

it('clears an already displayed frame as soon as another time or run is selected', async () => {
  const clear = vi.fn()
  const apply = vi.fn()
  ;(window as { __ppViewport?: unknown }).__ppViewport = { apply, clear }
  const mesh = meshBytes()
  const field = fieldBytes()
  let activeRunId = ''
  let activeTime = 0
  let activeRevision = ''
  let initCount = 0
  let resultSourceRequests = 0
  let releaseInit: () => void = () => {}
  const initGate = new Promise<void>((resolve) => { releaseInit = resolve })
  vi.stubGlobal('fetch', async (url: string, request?: RequestInit) => {
    url = new URL(String(url), 'http://fixture.invalid').pathname
    if (url === '/api/scan-case-status') {
      return { ok: true, json: async () => ({ caseDir: CASE_DIR, logRuns: [
        { name: 'run', path: 'log/run', timesteps: [1, 2], runId: 'log/run' },
        { name: 'other', path: 'log/other', timesteps: [1, 2], runId: 'log/other' },
      ] }) }
    }
    if (url === '/api/pp/init') {
      await initGate
      const body = JSON.parse(String(request?.body)) as { runDir: string; timeValue: number }
      activeRunId = body.runDir
      activeTime = body.timeValue
      activeRevision = `revision-${++initCount}`
      return { ok: true, json: async () => ({ caseDir: CASE_DIR, success: true, message: 'loaded', cached: false,
        n_cells: 1, timeValue: activeTime, runId: activeRunId, revision: activeRevision }) }
    }
    if (url === '/api/pp/mesh') {
      return { ok: true, json: async () => ({
        patches: [{ name: 'wall', nPoints: 3, nTriangles: 1, nEdges: 3, defaultVisible: true }],
        fields: [{ name: 'T', type: 'scalar', label: 'T' }],
        totalPoints: 3, totalTriangles: 1, totalEdges: 3,
        timeValue: activeTime, runId: activeRunId, revision: activeRevision,
      }) }
    }
    if (url === '/api/pp/render-source') {
      resultSourceRequests++
      return { ok: false, status: 422, json: async () => ({
        code: 'render_source_unavailable', missing: ['verified_patch_lineage', 'mesh_quality_receipt', 'run_result_receipt'],
      }) }
    }
    if (url === '/api/pp/mesh/binary') return { ok: true,
      headers: { get: (name: string) => name === 'X-PP-Revision' ? activeRevision : null },
      arrayBuffer: async () => mesh }
    if (url === '/api/pp/field/T') {
      return { ok: true,
        headers: { get: (name: string) => name === 'X-PP-Revision' ? activeRevision : null },
        arrayBuffer: async () => field }
    }
    throw new Error(`Unexpected request: ${url}`)
  })

  const host = document.createElement('div')
  document.body.append(host)
  const root: Root = createRoot(host)
  try {
    flushSync(() => root.render(<PostprocessPanel />))
    for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
    const time = host.querySelector<HTMLSelectElement>('#pp-timestep')!
    expect(time.value).toBe('2')
    flushSync(() => host.querySelector<HTMLButtonElement>('#btn-init-postprocess')!.click())
    expect(time.disabled).toBe(true)
    expect(host.querySelector<HTMLSelectElement>('#pp-log-run')?.disabled).toBe(true)
    releaseInit()
    for (let i = 0; i < 12; i++) await new Promise((r) => setTimeout(r, 0))
    expect(host.querySelector<HTMLElement>('#postprocess-controls')?.style.display).toBe('block')
    expect(apply).toHaveBeenCalled()
    expect(resultSourceRequests).toBe(1)
    expect(currentResultLayerSet()).toBeNull()
    clear.mockClear()

    time.value = '1'
    flushSync(() => time.dispatchEvent(new Event('change', { bubbles: true })))

    expect(host.querySelector<HTMLElement>('#postprocess-controls')?.style.display).toBe('none')
    expect(clear).toHaveBeenCalledOnce()

    flushSync(() => host.querySelector<HTMLButtonElement>('#btn-init-postprocess')!.click())
    for (let i = 0; i < 12; i++) await new Promise((r) => setTimeout(r, 0))
    expect(host.querySelector<HTMLElement>('#postprocess-controls')?.style.display).toBe('block')
    expect(resultSourceRequests).toBe(2)
    expect(currentResultLayerSet()).toBeNull()
    clear.mockClear()
    const run = host.querySelector<HTMLSelectElement>('#pp-log-run')!
    run.value = 'log/other'
    flushSync(() => run.dispatchEvent(new Event('change', { bubbles: true })))
    expect(host.querySelector<HTMLElement>('#postprocess-controls')?.style.display).toBe('none')
    expect(clear).toHaveBeenCalledOnce()
  } finally {
    flushSync(() => root.unmount())
    host.remove()
  }
})

it('keeps the newer init busy when an older overlapping init finishes', async () => {
  let releaseA: () => void = () => {}
  let releaseB: () => void = () => {}
  const first = new Promise<void>((resolve) => { releaseA = resolve })
  const second = new Promise<void>((resolve) => { releaseB = resolve })
  let initRequests = 0
  vi.stubGlobal('fetch', async (url: string) => {
    url = new URL(String(url), 'http://fixture.invalid').pathname
    if (url === '/api/scan-case-status') return { ok: true, json: async () => ({
      caseDir: CASE_DIR, logRuns: [{ name: 'run', path: 'log/run', timesteps: [1], runId: 'run' }],
    }) }
    if (url === '/api/pp/init') {
      const order = ++initRequests
      await (order === 1 ? first : second)
      return { ok: true, json: async () => ({ caseDir: CASE_DIR, success: true, message: 'loaded',
        cached: false, n_cells: 1, timeValue: 1,
        runId: 'run', revision: `revision-${order}` }) }
    }
    throw new Error(`Unexpected old-frame request: ${url}`)
  })
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    flushSync(() => root.render(<PostprocessPanel />))
    for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
    const button = host.querySelector<HTMLButtonElement>('#btn-init-postprocess')!
    flushSync(() => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(initRequests).toBe(2)
    releaseA()
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0))
    expect(button.disabled).toBe(true)
    expect(host.querySelector<HTMLElement>('#postprocess-controls')?.style.display).toBe('none')
  } finally {
    releaseB()
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0))
    flushSync(() => root.unmount())
    host.remove()
  }
})

it('discards a field response after the selected time changes without showing an old error', async () => {
  let releaseField: () => void = () => {}
  const fieldGate = new Promise<void>((resolve) => { releaseField = resolve })
  let requestedP = false
  vi.stubGlobal('fetch', async (url: string) => {
    url = new URL(String(url), 'http://fixture.invalid').pathname
    if (url === '/api/scan-case-status') return { ok: true, json: async () => ({
      caseDir: CASE_DIR, logRuns: [{ name: 'run', path: 'log/run', timesteps: [1, 2], runId: 'run' }],
    }) }
    if (url === '/api/pp/init') return { ok: true, json: async () => ({ caseDir: CASE_DIR, success: true,
      message: 'loaded', cached: false, n_cells: 1, runId: 'run', timeValue: 2,
      revision: 'revision-A' }) }
    if (url === '/api/pp/mesh') return { ok: true, json: async () => ({
      patches: [{ name: 'wall', nPoints: 3, nTriangles: 1, nEdges: 3, defaultVisible: true }],
      fields: [{ name: 'T', type: 'scalar', label: 'T' }, { name: 'p', type: 'scalar', label: 'p' }],
      totalPoints: 3, totalTriangles: 1, totalEdges: 3,
      runId: 'run', timeValue: 2, revision: 'revision-A',
    }) }
    if (url === '/api/pp/mesh/binary') return { ok: true,
      headers: { get: (name: string) => name === 'X-PP-Revision' ? 'revision-A' : null }, arrayBuffer: async () => meshBytes() }
    if (url === '/api/pp/field/T') return { ok: true,
      headers: { get: (name: string) => name === 'X-PP-Revision' ? 'revision-A' : null },
      arrayBuffer: async () => fieldBytes() }
    if (url === '/api/pp/field/p') {
      requestedP = true
      await fieldGate
      return { ok: true,
        headers: { get: (name: string) => name === 'X-PP-Revision' ? 'revision-A' : null },
        arrayBuffer: async () => fieldBytes() }
    }
    throw new Error(`Unexpected request ${url}`)
  })
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    flushSync(() => root.render(<PostprocessPanel />))
    for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
    flushSync(() => host.querySelector<HTMLButtonElement>('#btn-init-postprocess')!.click())
    for (let i = 0; i < 12; i++) await new Promise((r) => setTimeout(r, 0))
    expect(host.querySelector<HTMLElement>('#postprocess-controls')?.style.display).toBe('block')
    const field = host.querySelector<HTMLSelectElement>('#pp-field-select')!
    field.value = 'p'
    flushSync(() => field.dispatchEvent(new Event('change', { bubbles: true })))
    expect(requestedP).toBe(true)
    const time = host.querySelector<HTMLSelectElement>('#pp-timestep')!
    time.value = '1'
    flushSync(() => time.dispatchEvent(new Event('change', { bubbles: true })))
    releaseField()
    for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0))
    expect(host.querySelector<HTMLElement>('#postprocess-controls')?.style.display).toBe('none')
    expect(host.querySelector('#pp-error')).toBeNull()
  } finally {
    releaseField()
    flushSync(() => root.unmount())
    host.remove()
  }
})

it('rejects an init result from another case with the same run path and time', async () => {
  let meshRequests = 0
  vi.stubGlobal('fetch', async (url: string) => {
    url = new URL(String(url), 'http://fixture.invalid').pathname
    if (url === '/api/scan-case-status') return { ok: true, json: async () => ({
      caseDir: CASE_DIR, logRuns: [{ name: 'run', path: 'log/run', timesteps: [1], runId: 'case-A-run' }],
    }) }
    if (url === '/api/pp/init') return { ok: true, json: async () => ({ caseDir: CASE_DIR, success: true,
      message: 'loaded', cached: false, n_cells: 1, timeValue: 1,
      runId: 'case-B-run', revision: 'revision-B' }) }
    if (url === '/api/pp/mesh') { meshRequests++; throw new Error('wrong run was read') }
    throw new Error(`Unexpected request ${url}`)
  })
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  try {
    flushSync(() => root.render(<PostprocessPanel />))
    for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
    flushSync(() => host.querySelector<HTMLButtonElement>('#btn-init-postprocess')!.click())
    for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
    expect(meshRequests).toBe(0)
    expect(host.querySelector('#pp-error')?.textContent).toMatch(/run identity/i)
  } finally {
    flushSync(() => root.unmount())
    host.remove()
  }
})


it('rejects a result list from another case directory', async () => {
  vi.stubGlobal('fetch', async (url: string) => {
    const request = new URL(String(url), 'http://fixture.invalid')
    expect(request.pathname).toBe('/api/scan-case-status')
    expect(request.searchParams.get('caseDir')).toBe(CASE_DIR)
    return { ok: true, json: async () => ({ caseDir: '/fixture/another-case',
      logRuns: [{ name: 'run', path: 'log/run', timesteps: [1], runId: 'run' }] }) }
  })
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  try {
    flushSync(() => root.render(<PostprocessPanel />))
    for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
    expect(host.querySelector<HTMLSelectElement>('#pp-timestep')!.value).toBe('')
    expect(host.querySelector('#pp-error')?.textContent).toMatch(/another case directory/i)
  } finally { flushSync(() => root.unmount()); host.remove() }
})

it('rejects an init from another directory even when its run and time match', async () => {
  let meshRequests = 0
  vi.stubGlobal('fetch', async (url: string, request?: RequestInit) => {
    const route = new URL(String(url), 'http://fixture.invalid')
    if (route.pathname === '/api/scan-case-status') {
      expect(route.searchParams.get('caseDir')).toBe(CASE_DIR)
      return { ok: true, json: async () => ({ caseDir: CASE_DIR,
        logRuns: [{ name: 'run', path: 'log/run', timesteps: [1], runId: 'run' }] }) }
    }
    if (route.pathname === '/api/pp/init') {
      expect(JSON.parse(String(request?.body)).caseDir).toBe(CASE_DIR)
      return { ok: true, json: async () => ({ caseDir: '/fixture/another-case',
        success: true, message: 'loaded', cached: false, n_cells: 1,
        timeValue: 1, runId: 'run', revision: 'revision-other-case' }) }
    }
    if (route.pathname === '/api/pp/mesh') { meshRequests++; throw new Error('wrong case was read') }
    throw new Error(`Unexpected request ${url}`)
  })
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  try {
    flushSync(() => root.render(<PostprocessPanel />))
    for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
    expect(host.querySelector<HTMLSelectElement>('#pp-timestep')!.value).toBe('1')
    flushSync(() => host.querySelector<HTMLButtonElement>('#btn-init-postprocess')!.click())
    for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
    expect(meshRequests).toBe(0)
    expect(host.querySelector('#pp-error')?.textContent).toMatch(/another case directory/i)
  } finally { flushSync(() => root.unmount()); host.remove() }
})
