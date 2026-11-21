import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PostprocessPanel } from '../../src/components/PostprocessPanel'
import * as pp from '../../src/postprocess'
import { setTempUnit } from '../../src/postprocess/ppUnits'
import * as store from '../../src/projectStore'
import { stateV4 } from '../../src/legacy/cfdprojModules'
import type { CfdProject } from '../../src/schema/cfdproj'

const CASE_DIR = '/fixture/postprocess-volume-field'

vi.mock('../../src/postprocess', async (original) => ({
  ...await original<typeof import('../../src/postprocess')>(),
  fetchVolume: vi.fn(),
}))
vi.mock('../../src/renderer/windResultSourceClient', () => ({
  fetchVerifiedAirResultSource: vi.fn(async () => null),
}))

let host: HTMLDivElement
let root: Root
const apply = vi.fn()
const payload = (field: string): pp.VolumePayload => ({
  dimensions: [2, 2, 2], origin: [0, 0, 0], spacing: [1, 1, 1],
  scalarRange: field === 'T' ? [295.15, 305.15] : [0.002, 0.006],
  sentinel: -1e30,
  scalars: new Float32Array(field === 'T'
    ? [295.15, 296, 297, 298, 299, 300, 301, 305.15]
    : [0.002, 0.0025, 0.003, 0.0035, 0.004, 0.0045, 0.005, 0.006]),
})

beforeEach(() => {
  const project = stateV4().normalizeStateV4(stateV4().getInitialStateV4()) as unknown as CfdProject
  project.meta = { ...project.meta, casePath: CASE_DIR }
  store.loadProject(project, null)
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  setTempUnit('K')
  apply.mockReset()
  vi.mocked(pp.fetchVolume).mockReset().mockImplementation(async (field) => payload(field))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    fillStyle: '', fillRect: vi.fn(),
  } as unknown as CanvasRenderingContext2D)
  const mesh = new ArrayBuffer(72)
  new Float32Array(mesh, 0, 9).set([0, 0, 0, 1, 0, 0, 0, 1, 0])
  new Uint32Array(mesh, 36, 3).set([0, 1, 2])
  new Uint32Array(mesh, 48, 6).set([0, 1, 1, 2, 2, 0])
  const field = new ArrayBuffer(28)
  const header = new DataView(field)
  header.setFloat32(0, 295.15, true); header.setFloat32(4, 305.15, true)
  header.setUint32(8, 1, true); header.setUint32(12, 3, true)
  new Float32Array(field, 16, 3).set([295.15, 300, 305.15])
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    url = new URL(String(url), 'http://fixture.invalid').pathname
    if (url === '/api/scan-case-status') return { ok: true, json: async () => ({
      caseDir: CASE_DIR, logRuns: [{ name: 'run', path: 'log/run', timesteps: [1], runId: 'log/run' }],
    }) }
    if (url === '/api/pp/init') return { ok: true, json: async () => ({
      caseDir: CASE_DIR, success: true, message: 'loaded', n_cells: 1, timeValue: 1, runId: 'log/run', revision: 'r1',
    }) }
    if (url === '/api/pp/mesh') return { ok: true, json: async () => ({
      patches: [{ name: 'wall', nPoints: 3, nTriangles: 1, nEdges: 3, defaultVisible: true }],
      fields: [{ name: 'T', type: 'scalar' }, { name: 'U', type: 'vector' }],
      totalPoints: 3, totalTriangles: 1, totalEdges: 3, timeValue: 1, runId: 'log/run', revision: 'r1',
    }) }
    if (url === '/api/pp/mesh/binary' || url === '/api/pp/field/T') return {
      ok: true, headers: { get: (name: string) => name === 'X-PP-Revision' ? 'r1' : null },
      arrayBuffer: async () => url.endsWith('binary') ? mesh : field,
    }
    throw new Error('Unexpected request: ' + url)
  }))
  ;(window as unknown as { __ppViewport: unknown }).__ppViewport = { apply, clear: vi.fn() }
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount()); host.remove()
  delete (window as unknown as { __ppViewport?: unknown }).__ppViewport
  setTempUnit('C'); vi.restoreAllMocks(); vi.unstubAllGlobals()
})
const input = (id: string) => host.querySelector<HTMLInputElement>('#' + id)!
async function openVolume() {
  await act(async () => root.render(<PostprocessPanel />))
  await act(async () => host.querySelector<HTMLButtonElement>('#btn-init-postprocess')!.click())
  expect(host.querySelector<HTMLElement>('#postprocess-controls')!.style.display).toBe('block')
  act(() => input('pp-vol-show').click())
  await act(async () => host.querySelector<HTMLButtonElement>('#pp-apply-volume')!.click())
  expect(document.querySelector('#pp-colorbar-volume')?.textContent).toContain('T (K)')
}
function selectField(field: string) {
  act(() => {
    const select = host.querySelector<HTMLSelectElement>('#pp-vol-field')!
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, field)
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

it('removes a previous field and its limits until the newly selected volume is applied', async () => {
  await openVolume()
  expect(apply.mock.lastCall![0].volume.payload).not.toBeNull()
  selectField('Umag')
  expect(document.querySelector('#pp-colorbar-volume')).toBeNull()
  expect(apply.mock.lastCall![0].volume.payload).toBeNull()
  expect(input('pp-vol-clim-min').value).toBe('')
  expect(input('pp-vol-clim-max').value).toBe('')
  // The new field's grid is fetched ahead of 应用 (prefetch) but not drawn until applied.
  expect(vi.mocked(pp.fetchVolume).mock.calls.map((c) => c[0])).toEqual(['T', 'Umag'])
  await act(async () => host.querySelector<HTMLButtonElement>('#pp-apply-volume')!.click())
  expect(document.querySelector('#pp-colorbar-volume .pp-colorbar-field')?.textContent).toBe('Umag (m/s)')
  expect(apply.mock.lastCall![0].volume.payload.scalarRange).toEqual([0.002, 0.006])
})

it('shows small velocity limits and preserves their raw precision on focus and blur', async () => {
  await openVolume(); selectField('Umag')
  await act(async () => host.querySelector<HTMLButtonElement>('#pp-apply-volume')!.click())
  const max = input('pp-vol-clim-max')
  expect(Number(max.value)).toBeGreaterThan(0.004)
  expect(Number(max.step)).toBeLessThan(0.0001)
  const before = apply.mock.lastCall![0].volume.clim[1]
  act(() => { max.focus(); max.blur() })
  expect(apply.mock.lastCall![0].volume.clim[1]).toBe(before)
  act(() => document.querySelector('#pp-colorbar-volume')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })))
  expect(Number(document.querySelector<HTMLInputElement>('#pp-colorbar-volume-dialog .pp-cbw-max')!.value)).toBe(before)
})

it('does not apply a late response range to a different selected field', async () => {
  await openVolume()
  let resolve!: (value: pp.VolumePayload) => void
  vi.mocked(pp.fetchVolume).mockImplementationOnce(() => new Promise((done) => { resolve = done }))
  selectField('Umag')
  act(() => host.querySelector<HTMLButtonElement>('#pp-apply-volume')!.click())
  selectField('T')
  await act(async () => resolve(payload('Umag')))
  expect(document.querySelector('#pp-colorbar-volume .pp-colorbar-field')?.textContent).toBe('T (K)')
  expect(apply.mock.lastCall![0].volume.payload.scalarRange).toEqual([295.15, 305.15])
  expect(Number(document.querySelector('#pp-colorbar-volume .pp-colorbar-min')?.textContent?.replace(' K', ''))).toBeGreaterThan(295)
  expect(input('pp-vol-clim-min').value).toBe('')
  expect(input('pp-vol-clim-max').value).toBe('')
})

it('keeps the displayed volume labelled when its configuration section is collapsed', async () => {
  await openVolume()
  act(() => input('pp-vol-show').click())
  expect(host.querySelector<HTMLElement>('#pp-vol-config')!.style.display).toBe('none')
  expect(document.querySelector('#pp-colorbar-volume .pp-colorbar-field')?.textContent).toBe('T (K)')
  expect(apply.mock.lastCall![0].volume.payload.scalarRange).toEqual([295.15, 305.15])
  expect(apply.mock.lastCall![0].volume.visible).toBe(true)
})
