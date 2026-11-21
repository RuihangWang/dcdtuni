import { act, StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { OvPostProcessPanel, ovRunsKey } from '../src/components/OvPostProcessPanel'
import type { OvCommandClient, RenderAck } from '../src/renderer/ovProtocol'
import { airDisplayPreference, rememberAirDisplay, resetOvDisplayPreferences, type AirDisplayPreference } from '../src/renderer/ovDisplayPreferences'
import { setOvSession } from '../src/renderer/ovSession'
import * as store from '../src/projectStore'
import { freshProject } from '../src/schema'

/** The OV panel reports what Kit acknowledged, not what it asked for: the
 * dataset comes from a server grant, the legend from the domain IndeX was
 * given, and a viewport drag of the slice disc flows back into the numbers. */
let host: HTMLDivElement, root: Root
const RID = 'b'.repeat(64)
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)) })

function fakeClient(over: Partial<Record<string, (...a: any[]) => Promise<RenderAck>>> = {}) {
  const calls: { name: string; args: unknown[] }[] = []
  const ack = (extra: Partial<RenderAck>) => async (...args: unknown[]) => ({ requestId: 'x', ok: true, ...extra, args } as RenderAck)
  const base: Record<string, (...a: any[]) => Promise<unknown>> = {
    getSliceState: ack({ slice: { visible: false, origin: [0, 1, 0], normal: [0, 1, 0], radius: 2, field: 'T', domain: null }, adopted: false }),
    loadResultDataset: ack({ dataset: { run: 'run1', runId: RID, time: '2', fields: { T: { min: 303, max: 303, p1: 303, p99: 303 } },
      ranges: {}, bounds: [0, 1, 0, 0.5, 0, 1], hasT: true, hasU: true } }),
    setVolumeDisplay: ack({ volume: { field: 'T', visible: true, domain: [299.97, 306.03], degenerate: true, source: 'dataset p1..p99' } }),
    setSlice: ack({ sliceResult: { tris: 100, field: 'T', found: true, min: 303, max: 303 } }),
    fireCommand: async () => undefined,
    ...over,
  }
  const client = Object.fromEntries(Object.entries(base).map(([k, fn]) => [k, (...args: unknown[]) => { calls.push({ name: k, args }); return fn(...args) }]))
  return { client: client as unknown as OvCommandClient, calls }
}

beforeEach(() => {
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
  resetOvDisplayPreferences(); setOvSession({ sceneIdle: null })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ fillRect: vi.fn() } as unknown as CanvasRenderingContext2D)
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: any) => String(input) === '/api/pp/runs'
    ? json({ runs: [{ name: 'run1', runId: RID }] })
    : json({ grant: { payload: { kind: 'dataset' }, signature: 'c'.repeat(64) }, run: 'run1', time: '2', fields: ['T', 'U'], cached: true, format: 'ensight' }))
})
afterEach(() => { act(() => root.unmount()); host.remove(); resetOvDisplayPreferences(); setOvSession({ sceneIdle: null }); vi.restoreAllMocks() })

const button = (text: RegExp) => [...host.querySelectorAll('button')].find(b => text.test(b.textContent ?? ''))!

it('loads a run by server grant, then places slice and seed inside the dataset bounds', async () => {
  const { client, calls } = fakeClient()
  await act(async () => root.render(<OvPostProcessPanel client={client} degraded={false} />))
  await flush()
  await act(async () => button(/Load into OV|载入 OV/).click())
  await flush()
  const load = calls.find(c => c.name === 'loadResultDataset')!
  expect(load.args[0]).toEqual({ payload: { kind: 'dataset' }, signature: 'c'.repeat(64) })
  expect(host.querySelector('[data-ov-dataset="run1"]')).not.toBeNull()
  const originX = host.querySelector<HTMLInputElement>('input[aria-label$=" X"]')!
  expect(originX.value).toBe('0.5')
})

it('draws the volume legend from the domain Kit reports and flags a uniform fixture', async () => {
  const { client, calls } = fakeClient()
  await act(async () => root.render(<OvPostProcessPanel client={client} degraded={false} />))
  await flush()
  await act(async () => button(/Load into OV|载入 OV/).click())
  await flush()
  const tRadio = [...host.querySelectorAll<HTMLInputElement>('input[name="ov-volume"]')][1]!
  await act(async () => tRadio.click())
  await flush()
  expect(calls.find(c => c.name === 'setVolumeDisplay')!.args).toEqual(['T', null])
  const legend = host.querySelector('[data-ov-legend="T (K)"]')!
  expect(legend.getAttribute('aria-label')).toContain('300.0')
  expect(legend.textContent).toMatch(/303/)
})

it('read-back adopts a dragged disc origin into the panel numbers', async () => {
  const { client } = fakeClient({
    getSliceState: async () => ({ requestId: 'x', ok: true, adopted: true,
      slice: { visible: true, origin: [0.8, 0.25, 0.3], normal: [0, 1, 0], radius: 0.6, field: 'Umag', domain: null },
      sliceResult: { tris: 42, field: 'Umag', found: true, min: 0.1, max: 0.1 } }),
  })
  await act(async () => root.render(<OvPostProcessPanel client={client} degraded={false} />))
  await flush()
  expect(host.querySelector<HTMLInputElement>('input[aria-label$=" X"]')!.value).toBe('0.8')
  expect(host.querySelector('[data-ov-slice-tris="42"]')).not.toBeNull()
})

it('keeps dataset-dependent controls disabled until a dataset is loaded', async () => {
  const { client } = fakeClient()
  await act(async () => root.render(<OvPostProcessPanel client={client} degraded={false} />))
  await flush()
  const byLegend = (re: RegExp) => [...host.querySelectorAll('fieldset')].find(f => re.test(f.querySelector('legend')?.textContent ?? ''))!
  expect(byLegend(/体渲染|Volume render/).disabled).toBe(true)
  expect(byLegend(/流线|Streamlines/).disabled).toBe(true)
})


it('the run list follows the case: a new case directory or a finished solve re-lists', async () => {
  const a = ovRunsKey({ caseDir: '/c/Shinaide_Liquid' }, { logRuns: [] })
  const b = ovRunsKey({ caseDir: '/c/Tri_angle' }, { logRuns: [] })
  const c = ovRunsKey({ caseDir: '/c/Tri_angle' }, { logRuns: [{ name: '2026-10-01-03-12-52_Tri_angle', path: '/p' }] })
  expect(new Set([a, b, c]).size).toBe(3)
  expect(ovRunsKey({ caseDir: '/c/Tri_angle' }, { logRuns: [{ name: '2026-10-01-03-12-52_Tri_angle', path: '/q' }] })).toBe(c)
  const { readFileSync } = await import('node:fs')
  const { resolve } = await import('node:path')
  const src = readFileSync(resolve(__dirname, '../src/components/OvPostProcessPanel.tsx'), 'utf8')
  expect(src).toMatch(/useSyncExternalStore\(subscribeSession, \(\) => ovRunsKey\(getServerInfo\(\), getCaseStatus\(\)\)\)/)
  expect(src).toMatch(/\}, \[runsKey\]\)/)
})

const AIR_PREF: AirDisplayPreference = {
  volume: { field: 'U', domain: [0.1, 2] },
  slice: { visible: true, origin: [0.2, 0.3, 0.4], normal: [0, 0, 1], radius: 0.6, field: 'Umag', domain: [0, 3] },
  normalKey: 'z', streamOn: true, streamField: 'U',
  seed: { center: [0.4, 0.3, 0.2], radius: 0.1, nSeeds: 17 }, seedVisible: true,
}
const deferred = <T,>() => {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
const checkboxIn = (text: RegExp) => [...host.querySelectorAll('fieldset')].find(f => text.test(f.querySelector('legend')?.textContent ?? ''))!.querySelector<HTMLInputElement>('input[type="checkbox"]')!

it('remembers acknowledged air choices and restores them once after fresh scene and dataset ACKs', async () => {
  const first = fakeClient()
  await act(async () => root.render(<OvPostProcessPanel client={first.client} degraded={false} projectUid="project-a" />))
  await flush()
  await act(async () => button(/Load into OV|载入 OV/).click())
  await flush()
  await act(async () => [...host.querySelectorAll<HTMLInputElement>('input[name="ov-volume"]')][1]!.click())
  await flush()
  await act(async () => checkboxIn(/切片|Slice/).click())
  await act(async () => { await new Promise(r => setTimeout(r, 220)) })
  await act(async () => checkboxIn(/流线|Streamlines/).click())
  await flush()
  await act(async () => button(/Show\/move seed|显示\/移动种子/).click())
  await flush()
  const saved = airDisplayPreference('project-a', RID)!
  expect(saved.volume).toEqual({ field: 'T', domain: [299.97, 306.03] })
  expect(saved.slice.visible).toBe(true)
  expect(saved.streamOn).toBe(true)
  expect(saved.seedVisible).toBe(true)
  const oldGrantCount = vi.mocked(fetch).mock.calls.filter(([url]) => String(url) !== '/api/pp/runs').length
  await act(async () => root.unmount())
  root = createRoot(host)
  const idle = deferred<void>()
  setOvSession({ sceneIdle: () => idle.promise })
  const second = fakeClient({
    setVolumeDisplay: async (field, domain) => ({ requestId: 'v', ok: true, volume: { field, visible: field !== 'off', domain } } as RenderAck),
  })
  await act(async () => root.render(<StrictMode><OvPostProcessPanel client={second.client} degraded={false} projectUid="project-a" /></StrictMode>))
  await flush()
  expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url) !== '/api/pp/runs')).toHaveLength(oldGrantCount)
  expect(second.calls.filter(c => c.name === 'loadResultDataset')).toHaveLength(0)
  await act(async () => idle.resolve())
  await flush()
  expect(second.calls.filter(c => c.name === 'loadResultDataset')).toHaveLength(1)
  expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url) !== '/api/pp/runs')).toHaveLength(oldGrantCount + 1)
  expect(second.calls.find(c => c.name === 'setVolumeDisplay')!.args).toEqual(['T', [299.97, 306.03]])
  expect(second.calls.find(c => c.name === 'setSlice')!.args).toEqual([saved.slice])
  const streams = second.calls.filter(c => c.name === 'fireCommand' && c.args[0] === 'setStreamlines')
  expect(streams).toHaveLength(1)
  expect(streams[0]!.args[1]).toMatchObject({ enabled: true, seedCenter: saved.seed.center, nSeeds: saved.seed.nSeeds })
  expect(second.calls.filter(c => c.name === 'fireCommand' && c.args[0] === 'setSeedSphere')).toHaveLength(1)
  expect(checkboxIn(/流线|Streamlines/).checked).toBe(true)
  expect(checkboxIn(/切片|Slice/).checked).toBe(true)
})

it('never restores preferences across a project or a result absent from the current run list', async () => {
  rememberAirDisplay('project-a', RID, AIR_PREF)
  const otherProject = fakeClient()
  await act(async () => root.render(<OvPostProcessPanel client={otherProject.client} degraded={false} projectUid="project-b" />))
  await flush()
  expect(otherProject.calls.filter(c => c.name === 'loadResultDataset')).toHaveLength(0)
  expect(checkboxIn(/切片|Slice/).checked).toBe(false)
  await act(async () => root.unmount())
  root = createRoot(host)
  vi.mocked(fetch).mockImplementation(async () => json({ runs: [{ name: 'new-result', runId: 'e'.repeat(64) }] }))
  const otherRun = fakeClient()
  await act(async () => root.render(<OvPostProcessPanel client={otherRun.client} degraded={false} projectUid="project-a" />))
  await flush()
  expect(otherRun.calls.filter(c => c.name === 'loadResultDataset')).toHaveLength(0)
  expect(otherRun.calls.filter(c => c.name === 'setVolumeDisplay' || c.name === 'setSlice' || c.name === 'fireCommand')).toHaveLength(0)
})

it('discards an old client grant and coalesces replacement clients to one current restore', async () => {
  rememberAirDisplay('project-a', RID, AIR_PREF)
  const oldGrant = deferred<Response>()
  let grants = 0
  vi.mocked(fetch).mockImplementation(async (input: any) => {
    if (String(input) === '/api/pp/runs') return json({ runs: [{ name: 'run1', runId: RID }] })
    if (++grants === 1) return oldGrant.promise
    return json({ grant: { payload: { kind: 'dataset' }, signature: 'd'.repeat(64) }, run: 'run1', time: '2' })
  })
  const a = fakeClient(), b = fakeClient(), c = fakeClient()
  await act(async () => root.render(<OvPostProcessPanel client={a.client} degraded={false} projectUid="project-a" />))
  await flush()
  expect(grants).toBe(1)
  await act(async () => root.render(<OvPostProcessPanel client={b.client} degraded={false} projectUid="project-a" />))
  await act(async () => root.render(<OvPostProcessPanel client={c.client} degraded={false} projectUid="project-a" />))
  await act(async () => oldGrant.resolve(json({ grant: { payload: { kind: 'dataset' }, signature: 'c'.repeat(64) }, run: 'run1', time: '2' })))
  await flush()
  expect(a.calls.filter(x => x.name === 'loadResultDataset')).toHaveLength(0)
  expect(b.calls.filter(x => x.name === 'loadResultDataset')).toHaveLength(0)
  expect(c.calls.filter(x => x.name === 'loadResultDataset')).toHaveLength(1)
  expect(c.calls.find(x => x.name === 'loadResultDataset')!.args[0]).toMatchObject({ signature: 'd'.repeat(64) })
  expect(c.calls.filter(x => x.name === 'fireCommand' && x.args[0] === 'setStreamlines')).toHaveLength(1)
  expect(grants).toBe(2)
})

it('coalesces rapid stream edits behind one in-flight command and remembers the last applied choice', async () => {
  const sending = deferred<RenderAck>()
  let streamCalls = 0
  const { client, calls } = fakeClient({
    fireCommand: async (name) => name === 'setStreamlines' && ++streamCalls === 1 ? sending.promise : { requestId: 'fire', ok: true },
  })
  await act(async () => root.render(<OvPostProcessPanel client={client} degraded={false} projectUid="project-a" />))
  await flush()
  await act(async () => button(/Load into OV|载入 OV/).click())
  await flush()
  await act(async () => checkboxIn(/流线|Streamlines/).click())
  await act(async () => checkboxIn(/流线|Streamlines/).click())
  await act(async () => checkboxIn(/流线|Streamlines/).click())
  await act(async () => checkboxIn(/流线|Streamlines/).click())
  expect(calls.filter(c => c.name === 'fireCommand')).toHaveLength(1)
  await act(async () => sending.resolve({ requestId: 'fire', ok: true }))
  await flush()
  const streams = calls.filter(c => c.name === 'fireCommand' && c.args[0] === 'setStreamlines')
  expect(streams.map(c => (c.args[1] as { enabled: boolean }).enabled)).toEqual([true, false])
  expect(airDisplayPreference('project-a', RID)!.streamOn).toBe(false)
})

it('rejects a dataset ACK for another run before applying any remembered display', async () => {
  rememberAirDisplay('project-a', RID, AIR_PREF)
  const { client, calls } = fakeClient({
    loadResultDataset: async () => ({ requestId: 'wrong', ok: true,
      dataset: { run: 'other', runId: 'e'.repeat(64), time: '2', fields: {}, ranges: {}, hasT: true, hasU: true } }),
  })
  await act(async () => root.render(<OvPostProcessPanel client={client} degraded={false} projectUid="project-a" />))
  await flush()
  expect(host.textContent).toContain('Kit acknowledged a different air run')
  expect(host.querySelector('[data-ov-dataset]')).toBeNull()
  expect(calls.filter(c => c.name === 'setVolumeDisplay' || c.name === 'setSlice' || c.name === 'fireCommand')).toHaveLength(0)
  expect(airDisplayPreference('project-a', RID)).toEqual(AIR_PREF)
})

function frameServer(times = [.01, .02, .03]) {
  vi.mocked(fetch).mockImplementation(async (input: any, init?: RequestInit) => {
    if (String(input) === '/api/pp/runs') return json({ runs: [{ name: 'run1', runId: RID, timesteps: times, transient: true }] })
    const body = JSON.parse(String(init?.body))
    const time = String(body.time ?? times.at(-1))
    return json({ grant: { payload: { kind: 'dataset', runId: RID, time }, signature: 'c'.repeat(64) }, run: 'run1', time, fields: ['T','U'], format: 'ensight' })
  })
}
function frameClient(over: Partial<Record<string, (...a: any[]) => Promise<RenderAck>>> = {}) {
  return fakeClient({ loadResultDataset: async grant => ({ requestId: 'f', ok: true, dataset: {
    run: 'run1', runId: RID, time: String(grant.payload.time), fields: {}, ranges: {}, hasT: true, hasU: true,
  } }), ...over })
}
function frameSelect() { return host.querySelector<HTMLSelectElement>('#ov-air-time')! }
async function pickFrame(value: string) {
  const node = frameSelect()
  await act(async () => { node.value = value === "" ? "" : String(Number(value)); node.dispatchEvent(new Event('change', { bubbles:true })) })
  await flush()
}
it('keeps a manual exact air frame across a fresh client and re-applies the real display', async () => {
  frameServer()
  const first = frameClient()
  await act(async () => root.render(<OvPostProcessPanel client={first.client} degraded={false} projectUid="p" />))
  await flush()
  await act(async () => button(/Load into OV|载入 OV/).click()); await flush()
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls.at(-1)![1]?.body))).toEqual({ runId:RID })
  await pickFrame('.01')
  expect(host.querySelector('[data-ov-dataset]')!.textContent).toContain('0.01')
  await act(async () => root.unmount()); root=createRoot(host)
  const second=frameClient()
  await act(async () => root.render(<OvPostProcessPanel client={second.client} degraded={false} projectUid="p" />)); await flush()
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls.at(-1)![1]?.body))).toEqual({ runId:RID, time:.01 })
  expect(second.calls.filter(c=>c.name==='setVolumeDisplay')).toHaveLength(1)
  expect(second.calls.filter(c=>c.name==='setSlice')).toHaveLength(1)
})
it('rejects an air ACK for the right run but wrong physical time', async () => {
  frameServer()
  const c=frameClient({ loadResultDataset: async () => ({ requestId:'bad',ok:true,dataset:{ run:'run1',runId:RID,time:'.02',fields:{},ranges:{},hasT:true,hasU:true } }) })
  await act(async () => root.render(<OvPostProcessPanel client={c.client} degraded={false} projectUid="p" />)); await flush()
  await pickFrame('.01')
  await act(async () => button(/Load into OV|载入 OV/).click()); await flush()
  expect(host.textContent).toContain('different air frame')
  expect(host.querySelector('[data-ov-dataset]')).toBeNull()
})
it('queues only the final requested frame behind an in-flight dataset import', async () => {
  frameServer()
  const first=deferred<RenderAck>()
  let n=0
  const c=frameClient({ loadResultDataset: async grant => {
    if (++n===1) return first.promise
    return { requestId:'next',ok:true,dataset:{run:'run1',runId:RID,time:String(grant.payload.time),fields:{},ranges:{},hasT:true,hasU:true} }
  } })
  await act(async () => root.render(<OvPostProcessPanel client={c.client} degraded={false} projectUid="p" />)); await flush()
  await act(async () => button(/Load into OV|载入 OV/).click()); await flush()
  await pickFrame('.01'); await pickFrame('.02')
  expect(n).toBe(1)
  await act(async()=> first.resolve({requestId:'first',ok:true,dataset:{run:'run1',runId:RID,time:'.03',fields:{},ranges:{},hasT:true,hasU:true}})); await flush()
  expect(n).toBe(2)
  expect(host.querySelector('[data-ov-dataset]')!.textContent).toContain('0.02')
  const requests=vi.mocked(fetch).mock.calls.filter(([u])=>u==='/api/ov/result-dataset')
  expect(requests.map(([,i])=>JSON.parse(String(i?.body)))).toEqual([{runId:RID},{runId:RID,time:.02}])
})

it('waits for old display mutation to settle and ignores its ACK before replacing a frame', async () => {
  frameServer()
  const oldVolume=deferred<RenderAck>()
  let volumeCalls=0
  const c=frameClient({ setVolumeDisplay: async (field,domain) => {
    if (++volumeCalls===1) return oldVolume.promise
    return {requestId:'v',ok:true,volume:{field,visible:field!=='off',domain}}
  } })
  await act(async()=>root.render(<OvPostProcessPanel client={c.client} degraded={false} projectUid="p" />));await flush()
  await act(async()=>button(/Load into OV|载入 OV/).click());await flush()
  await act(async()=>[...host.querySelectorAll<HTMLInputElement>('input[name="ov-volume"]')][1]!.click());await flush()
  await pickFrame('.01')
  expect(c.calls.filter(c=>c.name==='loadResultDataset')).toHaveLength(1)
  await act(async()=>oldVolume.resolve({requestId:'old',ok:true,volume:{field:'T',visible:true,domain:[999,1000]}}));await flush()
  expect(c.calls.filter(c=>c.name==='loadResultDataset')).toHaveLength(2)
  expect(host.querySelector('[data-ov-legend]')?.textContent).not.toContain('999')
  expect(airDisplayPreference('p',RID)!.volume.domain).not.toEqual([999,1000])
})
it('uses actual nearest earlier time after playhead movement, while a snapped manual pick stays exact', async () => {
  const project=freshProject()
  project.transient={ mode:'transient',durationS:10,gridS:1,interpolation:'step',tracks:{} } as typeof project.transient
  store.loadProject(project)
  frameServer([.01,.02,.03,1,3])
  const c=frameClient()
  await act(async()=>root.render(<OvPostProcessPanel client={c.client} degraded={false} projectUid="p" />));await flush()
  await act(async()=>button(/Load into OV|载入 OV/).click());await flush()
  await pickFrame('.01')
  expect(store.playheadTime()).toBe(0)
  await act(async()=>{await new Promise(r=>setTimeout(r,270))})
  expect(host.querySelector('[data-ov-dataset]')!.textContent).toContain('0.01')
  await act(async()=>store.setPlayhead(2))
  await act(async()=>{await new Promise(r=>setTimeout(r,270))});await flush()
  const bodies=vi.mocked(fetch).mock.calls.filter(([u])=>u==='/api/ov/result-dataset').map(([,i])=>JSON.parse(String(i?.body)))
  expect(bodies.at(-1)).toEqual({runId:RID,time:1})
})
