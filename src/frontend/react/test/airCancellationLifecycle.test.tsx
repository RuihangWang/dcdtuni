import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'

let session: typeof import('../src/run/session')
let store: typeof import('../src/projectStore')
let paths: typeof import('../src/run/caseDir')
let fetcher: ReturnType<typeof vi.fn>
let streams: JobStream[]
let backendPath: string
let nextJob: number
let scans: number
let statusCalls: number
let abortCalls: number
let mountedRoot: Root | null = null
let host: HTMLDivElement | null = null
let abortReply: () => Promise<Response>
let jobStatus: () => Promise<Response>
const settle = async () => { for (let i = 0; i < 15; i++) await Promise.resolve() }
const response = (data: unknown, status = 200) => ({
  ok: status >= 200 && status < 300, status, statusText: String(status),
  json: async () => data, text: async () => JSON.stringify(data),
} as Response)
const project = (path = '/case/A') => ({
  schemaVersion: 4, uid: path, name: 'cancel test', meta: { casePath: path },
  constructions: { racks: {} },
} as CfdProject)

class JobStream {
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor() { streams.push(this) }
  finish(final: unknown = { done: true, returncode: -15, cancelRequested: true }) {
    this.onmessage?.({ data: JSON.stringify(final) })
  }
}

beforeEach(async () => {
  vi.resetModules()
  streams = []
  backendPath = '/case/A'
  nextJob = scans = statusCalls = abortCalls = 0
  abortReply = async () => response({ status: 'aborting' })
  jobStatus = async () => response({ done: false })
  fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/set-case-dir') {
      backendPath = JSON.parse(String(options?.body)).caseDir
      return response({ ok: true, caseDir: backendPath })
    }
    if (url === '/api/server-info') return response({
      dctwinAvailable: true, caseDir: backendPath, caseDirOk: true, allOk: true,
    })
    if (String(url).split('?')[0] === '/api/scan-case-status') {
      scans++
      return response({ meshReady: true, solveReady: false, postprocessReady: false, logRuns: [], liquidRuns: [] })
    }
    if (url === '/api/run-mesh' || url === '/api/run-check-mesh') return response({ jobId: 'job-' + ++nextJob })
    if (String(url).split('?')[0] === '/api/check-mesh') return response({ meshReady: true })
    if (url === '/api/abort-job') { abortCalls++; return abortReply() }
    if (url.startsWith('/api/job-status')) { statusCalls++; return jobStatus() }
    throw new Error('Unexpected request ' + url)
  })
  vi.stubGlobal('fetch', fetcher)
  vi.stubGlobal('EventSource', JobStream)
  session = await import('../src/run/session')
  store = await import('../src/projectStore')
  paths = await import('../src/run/caseDir')
  const submit = await import('../src/run/runSubmit')
  vi.spyOn(submit, 'buildRunRequest').mockResolvedValue({} as never)
  store.loadProject(project(), null)
  await vi.waitFor(() => expect(session.isValidated()).toBe(true))
  await settle()
  document.body.insertAdjacentHTML('beforeend', '<div id="toolbar"></div><div id="right-panel"></div>')
})

afterEach(async () => {
  if (mountedRoot) flushSync(() => mountedRoot?.unmount())
  mountedRoot = null
  host?.remove()
  host = null
  streams.forEach(stream => stream.finish())
  await settle()
  vi.clearAllTimers()
  vi.useRealTimers()
  document.getElementById('toolbar')?.remove()
  document.getElementById('right-panel')?.remove()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('air job cancellation waits for backend completion', () => {
  it('recovers a silent healthy SSE via status polling, refreshes disk state, thaws, and allows a fresh run', async () => {
    await session.runJob('mesh')
    vi.useFakeTimers()
    expect(document.body.classList.contains('ui-frozen')).toBe(true)
    expect(document.getElementById('toolbar')!.hasAttribute('inert')).toBe(true)
    const beforeScans = scans
    await session.abortJob()
    await settle()
    expect(statusCalls).toBe(1)
    expect(session.getJob()?.running).toBe(true)
    expect(session.isUiFrozen()).toBe(true)
    jobStatus = async () => response({ done: true, returncode: -15, cancelRequested: true, checkMeshOk: false })
    await vi.advanceTimersByTimeAsync(5000)
    expect(session.getJob()).toMatchObject({ running: false, aborted: true, returncode: -15, error: null })
    expect(document.body.classList.contains('ui-frozen')).toBe(false)
    expect(document.getElementById('toolbar')!.hasAttribute('inert')).toBe(false)
    expect(scans).toBe(beforeScans + 1)
    expect(session.isMeshReady()).toBe(false) // partial/stale disk mesh cannot grant readiness
    expect(streams[0]!.close).toHaveBeenCalledOnce()
    await session.runJob('mesh')
    expect(session.getJob()).toMatchObject({ id: 'job-2', running: true, aborted: false })
  })

  it.each(['http', 'network'])('does not claim cancellation after an abort %s failure and supports retry', async (failure) => {
    await session.runJob('mesh')
    vi.useFakeTimers()
    abortReply = failure === 'http'
      ? async () => response({ error: 'stop rejected' }, 500)
      : async () => { throw new Error('stop offline') }
    await session.abortJob()
    expect(session.getJob()).toMatchObject({ running: true, aborted: false })
    expect(session.getJob()?.error).toContain(failure === 'http' ? 'stop rejected' : 'stop offline')
    expect(session.isUiFrozen()).toBe(true)
    abortReply = async () => response({ status: 'aborted' })
    jobStatus = async () => response({ done: true, returncode: -15, cancelRequested: true })
    await session.abortJob()
    await settle()
    expect(session.getJob()).toMatchObject({ running: false, aborted: true, error: null })
    expect(abortCalls).toBe(2)
  })

  it.each(['http', 'network', 'malformed'])('keeps an unconfirmed job frozen after a status %s failure and recovers', async (failure) => {
    await session.runJob('mesh')
    vi.useFakeTimers()
    jobStatus = failure === 'http'
      ? async () => response({ done: true, returncode: 0, error: 'status unavailable' }, 500)
      : failure === 'network'
        ? async () => { throw new Error('status offline') }
        : async () => response(null)
    streams[0]!.onerror?.()
    await vi.advanceTimersByTimeAsync(5000)
    expect(session.getJob()?.running).toBe(true)
    expect(session.isUiFrozen()).toBe(true)
    expect(session.getJob()?.error).toBeTruthy()
    jobStatus = async () => response({ done: true, returncode: -15, cancelRequested: true })
    await vi.advanceTimersByTimeAsync(5000)
    expect(session.getJob()).toMatchObject({ running: false, aborted: true, error: null })
    expect(session.isUiFrozen()).toBe(false)
  })

  it('coalesces stop requests and fallback monitors, then ignores late terminal events', async () => {
    await session.runJob('mesh')
    vi.useFakeTimers()
    let reply!: (value: Response) => void
    abortReply = () => new Promise(resolve => { reply = resolve })
    const first = session.abortJob()
    const second = session.abortJob()
    expect(abortCalls).toBe(1)
    reply(response({ status: 'aborting' }))
    await Promise.all([first, second])
    await settle()
    streams[0]!.onerror?.()
    streams[0]!.onerror?.()
    expect(statusCalls).toBe(1)
    jobStatus = async () => response({ done: true, returncode: -15, cancelRequested: true })
    await vi.advanceTimersByTimeAsync(5000)
    streams[0]!.finish({ done: true, returncode: 0, checkMeshOk: true })
    expect(session.getJob()).toMatchObject({ running: false, returncode: -15, aborted: true })
    expect(session.isMeshReady()).toBe(false)
  })

  it('does not relock or mark a newer run when the old abort acknowledgement arrives late', async () => {
    await session.runJob('mesh')
    let reply!: (value: Response) => void
    abortReply = () => new Promise(resolve => { reply = resolve })
    const stopping = session.abortJob()
    streams[0]!.finish()
    await session.runJob('mesh')
    reply(response({ status: 'aborted' }))
    await stopping
    expect(session.getJob()).toMatchObject({ id: 'job-2', running: true, aborted: false })
  })

  it('does not treat an already-completed successful run as cancelled', async () => {
    await session.runJob('mesh')
    vi.useFakeTimers()
    abortReply = async () => response({ status: 'already_done' })
    jobStatus = async () => response({ done: true, returncode: 0, checkMeshOk: true })
    await session.abortJob()
    await settle()
    expect(session.getJob()).toMatchObject({ running: false, returncode: 0, aborted: false })
  })

  it('does not refresh or unlock another project from an old cancelled job', async () => {
    await session.runJob('mesh')
    vi.useFakeTimers()
    await session.abortJob()
    paths.beginCasePathChange()
    const beforeScans = scans
    jobStatus = async () => response({ done: true, returncode: -15, cancelRequested: true })
    await vi.advanceTimersByTimeAsync(5000)
    expect(session.getJob()?.running).toBe(false)
    expect(session.getCurrentCaseJob()).toBeNull()
    expect(scans).toBe(beforeScans)
  })

  it('gives a current checkMesh polling completion the same readiness as SSE', async () => {
    await session.runCheckMesh()
    vi.useFakeTimers()
    streams[0]!.onerror?.()
    jobStatus = async () => response({ done: true, returncode: 1, checkMeshOk: false, failedChecks: 3 })
    await vi.advanceTimersByTimeAsync(5000)
    expect(session.getJob()?.running).toBe(false)
    expect(session.isMeshReady()).toBe(false) // explicit rejected quality overrides earlier disk acceptance
  })

  it('performs solve completion side effects once when polling beats the SSE final', async () => {
    const sensors = await import('../src/run/sensorResults')
    const finish = vi.spyOn(sensors, 'onSolveFinished').mockResolvedValue(null)
    await session.runJob('solve')
    vi.useFakeTimers()
    jobStatus = async () => response({ done: true, returncode: -15, cancelRequested: true })
    await session.abortJob()
    await settle()
    streams[0]!.finish()
    expect(finish).toHaveBeenCalledOnce()
  })
})

async function renderMeshPanel() {
  const { readProjectFileText } = await import('../src/io/projectFiles')
  const { MeshPanel } = await import('../src/components/MeshPanel')
  const doc = readProjectFileText(readFileSync(resolve(process.cwd(), '../../../case/Slab/Slab.cfdproj'), 'utf8'))
  if (!doc.ok) throw new Error(doc.error)
  doc.project.meta = { ...doc.project.meta, casePath: '/case/A' }
  store.loadProject(doc.project, null)
  await vi.waitFor(() => expect(session.isValidated()).toBe(true))
  host = document.createElement('div')
  document.body.append(host)
  mountedRoot = createRoot(host)
  flushSync(() => mountedRoot!.render(<MeshPanel project={store.getProject()} />))
  return host
}

it('the actual MeshPanel interrupt button unlocks its run button only after confirmed cancellation', async () => {
  const panel = await renderMeshPanel()
  flushSync(() => panel.querySelector<HTMLButtonElement>('#mesh-run-btn')!.click())
  await vi.waitFor(() => expect(session.getJob()?.running).toBe(true))
  streams[0]!.onmessage?.({ data: JSON.stringify('native started') })
  await vi.waitFor(() => expect(panel.querySelector<HTMLButtonElement>('#mesh-abort-btn')).not.toBeNull())
  await settle()
  vi.useFakeTimers()
  let acknowledge!: (response: Response) => void
  abortReply = () => new Promise(resolve => { acknowledge = resolve })
  flushSync(() => panel.querySelector<HTMLButtonElement>('#mesh-abort-btn')!.click())
  await settle()
  flushSync(() => {})
  const pendingButton = panel.querySelector<HTMLButtonElement>('#mesh-abort-btn')!
  expect(pendingButton.disabled).toBe(true)
  expect(pendingButton.textContent).toBe((await import('../src/i18n')).t('status.stopping'))
  expect(statusCalls).toBe(0) // the HTTP acknowledgement has not arrived
  acknowledge(response({ status: 'aborting' }))
  await settle()
  flushSync(() => {})
  expect(statusCalls).toBe(1)
  expect(panel.querySelector<HTMLButtonElement>('#mesh-run-btn')!.disabled).toBe(true)
  expect(document.body.classList.contains('ui-frozen')).toBe(true)
  const waitingButton = panel.querySelector<HTMLButtonElement>('#mesh-abort-btn')!
  expect(waitingButton.disabled).toBe(true)
  expect(waitingButton.textContent).toBe((await import('../src/i18n')).t('status.stopping'))
  flushSync(() => waitingButton.click())
  await settle()
  expect(abortCalls).toBe(1)
  await session.abortJob() // accepted stop is durable and cannot be requested twice
  expect(abortCalls).toBe(1)
  jobStatus = async () => response({ done: true, returncode: -15, cancelled: true })
  await vi.advanceTimersByTimeAsync(5000)
  flushSync(() => {})
  expect(panel.querySelector<HTMLButtonElement>('#mesh-run-btn')!.disabled).toBe(false)
  expect(panel.querySelector('#mesh-abort-btn')).toBeNull()
  expect(panel.querySelector('#mesh-run-status')!.textContent).toContain((await import('../src/i18n')).t('status.aborted'))
  expect(document.body.classList.contains('ui-frozen')).toBe(false)
})

it('MeshPanel shows stop HTTP rejection without hiding the live retry button', async () => {
  const panel = await renderMeshPanel()
  await session.runJob('mesh')
  await vi.waitFor(() => expect(panel.querySelector('#mesh-abort-btn')).not.toBeNull())
  abortReply = async () => response({ error: 'owned stop rejection' }, 500)
  flushSync(() => panel.querySelector<HTMLButtonElement>('#mesh-abort-btn')!.click())
  await vi.waitFor(() => expect(panel.querySelector('[role="alert"]')!.textContent).toContain('owned stop rejection'))
  expect(panel.querySelector<HTMLButtonElement>('#mesh-run-btn')!.disabled).toBe(true)
  expect(panel.querySelector<HTMLButtonElement>('#mesh-abort-btn')!.disabled).toBe(false)
  expect(session.getJob()).toMatchObject({ running: true, aborted: false })
})

it('a confirmed cancelled zero-exit response cannot grant mesh readiness', async () => {
  await session.runJob('mesh')
  streams[0]!.finish({ done: true, returncode: 0, cancelled: true })
  expect(session.getJob()).toMatchObject({ running: false, aborted: true })
  expect(session.isMeshReady()).toBe(false)
})
