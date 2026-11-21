// Air results on the timeline (plan T5b): in a transient project, a transient
// run's frame follows the playhead — nearest written time (0/ is not a result),
// colour range held, slices re-requested at the new time — and a pick from the
// time list moves the playhead without the grid snap taking it back.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import '../../src/legacy/geometryBuilder'
import * as store from '../../src/projectStore'
import { readProjectFileText } from '../../src/io/projectFiles'
import { PostprocessPanel } from '../../src/components/PostprocessPanel'
import { clearResultLayerSet } from '../../src/renderer/resultLayerRevision'
import { setSimulationMode } from '../../src/transient/simulationMode'

const CASE = resolve(process.cwd(), '../../../case/Base_coldAisleIso/Base_coldAisleIso.cfdproj')
const CASE_DIR = '/fixture/postprocess-playhead'
const TIMES = [0, 0.5, 1, 1.5, 2]

function meshBytes(): ArrayBuffer {
  const bytes = new ArrayBuffer(3 * 3 * 4 + 3 * 4 + 3 * 2 * 4)
  new Float32Array(bytes, 0, 9).set([0, 0, 0, 1, 0, 0, 0, 1, 0])
  new Uint32Array(bytes, 36, 3).set([0, 1, 2])
  new Uint32Array(bytes, 48, 6).set([0, 1, 1, 2, 2, 0])
  return bytes
}
function fieldBytes(t: number): ArrayBuffer {
  const bytes = new ArrayBuffer(16 + 3 * 4)
  const header = new DataView(bytes)
  header.setFloat32(0, 300 + 10 * t, true)
  header.setFloat32(4, 302 + 10 * t, true)
  header.setUint32(8, 1, true)
  header.setUint32(12, 3, true)
  new Float32Array(bytes, 16, 3).set([300 + 10 * t, 301 + 10 * t, 302 + 10 * t])
  return bytes
}
function sliceBytes(t: number): ArrayBuffer {
  let json = JSON.stringify({ nPoints: 3, nTriangles: 1, scalarRange: [300 + 10 * t, 302 + 10 * t], field: 'T' })
  while ((json.length + 4) % 4) json += ' '
  const head = new TextEncoder().encode(json)
  const bytes = new ArrayBuffer(4 + head.length + 9 * 4 + 3 * 4 + 3 * 4)
  new DataView(bytes).setUint32(0, head.length, true)
  new Uint8Array(bytes, 4, head.length).set(head)
  let off = 4 + head.length
  new Float32Array(bytes, off, 9).set([0, 0, 0, 1, 0, 0, 0, 1, 0]); off += 36
  new Uint32Array(bytes, off, 3).set([0, 1, 2]); off += 12
  new Float32Array(bytes, off, 3).set([300 + 10 * t, 301 + 10 * t, 302 + 10 * t])
  return bytes
}

let host: HTMLDivElement, root: Root
let inits: number[], slices: number[]
function stub(transient: boolean) {
  let time = 0, revision = ''
  inits = []; slices = []
  vi.stubGlobal('fetch', async (url: string, request?: RequestInit) => {
    url = new URL(String(url), 'http://fixture.invalid').pathname
    const rev = { get: (name: string) => (name === 'X-PP-Revision' ? revision : null) }
    if (url === '/api/scan-case-status') return { ok: true, json: async () => ({ caseDir: CASE_DIR, logRuns: [
      { name: 'run', path: 'log/run', timesteps: TIMES, runId: 'log/run', transient }] }) }
    if (url === '/api/pp/init') {
      time = (JSON.parse(String(request?.body)) as { timeValue: number }).timeValue
      inits.push(time)
      revision = `rev-${inits.length}`
      return { ok: true, json: async () => ({ caseDir: CASE_DIR, success: true, message: 'loaded', cached: false, n_cells: 1,
        timeValue: time, runId: 'log/run', revision }) }
    }
    if (url === '/api/pp/mesh') return { ok: true, json: async () => ({
      patches: [{ name: 'wall', nPoints: 3, nTriangles: 1, nEdges: 3, defaultVisible: true }],
      fields: [{ name: 'T', type: 'scalar', label: 'T' }], totalPoints: 3, totalTriangles: 1, totalEdges: 3,
      timeValue: time, runId: 'log/run', revision }) }
    if (url === '/api/pp/render-source') return { ok: false, status: 422, json: async () => ({ code: 'render_source_unavailable' }) }
    if (url === '/api/pp/mesh/binary') return { ok: true, headers: rev, arrayBuffer: async () => meshBytes() }
    if (url === '/api/pp/field/T') return { ok: true, headers: rev, arrayBuffer: async () => fieldBytes(time) }
    if (url === '/api/pp/slice') { slices.push(time); return { ok: true, headers: rev, arrayBuffer: async () => sliceBytes(time) } }
    throw new Error(`Unexpected request: ${url}`)
  })
}
const settle = async (ms = 0) => {
  if (ms) await new Promise((r) => setTimeout(r, ms))
  for (let i = 0; i < 16; i++) await new Promise((r) => setTimeout(r, 0))
}
const $ = <T extends Element>(sel: string) => host.querySelector<T>(sel)!

beforeEach(async () => {
  const r = readProjectFileText(readFileSync(CASE, 'utf8'))
  if (!r.ok) throw new Error(r.error)
  r.project.meta = { ...r.project.meta, casePath: CASE_DIR }
  store.loadProject(r.project, null)
  setSimulationMode('transient')                       // gridS 1
  ;(window as { __ppViewport?: unknown }).__ppViewport = { apply: vi.fn(), clear: vi.fn() }
})
afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  store.setPlayhead(0)
  clearResultLayerSet()
  vi.unstubAllGlobals()
  delete (window as { __ppViewport?: unknown }).__ppViewport
})

async function openAt2() {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  flushSync(() => root.render(<PostprocessPanel />))
  await settle()
  expect($<HTMLSelectElement>('#pp-timestep').value).toBe('2')        // opens at the latest time, as before
  flushSync(() => $<HTMLButtonElement>('#btn-init-postprocess').click())
  await settle()
  expect(inits).toEqual([2])
}

it('opening a run shows its latest time until the playhead moves', async () => {
  stub(true)
  await openAt2()                                     // the playhead sits at 0
  await settle(320)
  expect(inits).toEqual([2])
  expect($('#pp-result-time').textContent).toMatch(/播放头在 0 s|playhead at 0 s/)
  expect($('#pp-result-time').textContent).not.toMatch(/最近|nearest/)
  flushSync(() => store.setPlayhead(1))
  await settle(320)
  expect(inits).toEqual([2, 1])
})

it('a playhead move loads the nearest written time, keeps the colour range and re-cuts the slice', async () => {
  stub(true)
  await openAt2()
  const climMin = $<HTMLInputElement>('#pp-clim-min').value
  flushSync(() => $<HTMLButtonElement>('#pp-apply-slice').click())
  await settle()
  expect(slices).toEqual([2])
  flushSync(() => store.setPlayhead(1))
  await settle(320)
  expect(inits).toEqual([2, 1])
  expect($<HTMLSelectElement>('#pp-timestep').value).toBe('1')
  expect($<HTMLInputElement>('#pp-clim-min').value).toBe(climMin)       // held, not the 1 s frame's own range
  expect(slices).toEqual([2, 1])                                        // the slice is cut again at 1 s
  expect(host.textContent).toContain('Slice #1')
  // The playhead at 0: 0/ is not a result, the first written time is shown and named.
  flushSync(() => store.setPlayhead(0))
  await settle(320)
  expect(inits.at(-1)).toBe(0.5)
  expect($('#pp-result-time').textContent).toContain('0.5')
  expect($('#pp-result-time').textContent).toMatch(/最近|nearest/)
})

it('a pick from the time list moves the playhead and the grid snap does not take it back', async () => {
  stub(true)
  await openAt2()
  const time = $<HTMLSelectElement>('#pp-timestep')
  time.value = '1.5'
  flushSync(() => time.dispatchEvent(new Event('change', { bubbles: true })))
  await settle(320)
  expect(store.playheadTime()).toBe(2)                                   // 1.5 s snapped to the 1 s grid
  expect(inits).toEqual([2, 1.5])
  expect(time.value).toBe('1.5')
})

it('a steady run does not follow the playhead', async () => {
  stub(false)
  await openAt2()
  flushSync(() => store.setPlayhead(1))
  await settle(320)
  expect(inits).toEqual([2])
})
