import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'

let session: typeof import('../src/run/session')
let store: typeof import('../src/projectStore')
let submit: typeof import('../src/run/runSubmit')
let fetcher: ReturnType<typeof vi.fn>
let streams: JobStream[]
let backendPath: string
let submitReply: () => Promise<Response>
let root: Root | null = null
let host: HTMLDivElement | null = null
const settle = async () => { for (let i = 0; i < 15; i++) await Promise.resolve() }
const response = (data: unknown) => ({
  ok: true, status: 200, statusText: '200',
  json: async () => data, text: async () => JSON.stringify(data),
} as Response)
const project = (path = '/case/preparation') => ({
  schemaVersion: 4, uid: path, name: 'preparation test', meta: { casePath: path },
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
  backendPath = '/case/preparation'
  submitReply = async () => response({ jobId: 'real-job' })
  fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/set-case-dir') {
      backendPath = JSON.parse(String(options?.body)).caseDir
      return response({ ok: true, caseDir: backendPath })
    }
    if (url === '/api/server-info') return response({
      dctwinAvailable: true, caseDir: backendPath, caseDirOk: true, allOk: true,
    })
    if (String(url).split('?')[0] === '/api/scan-case-status') return response({
      meshReady: true, solveReady: false, postprocessReady: false, logRuns: [], liquidRuns: [],
    })
    if (url === '/api/run-mesh') return submitReply()
    if (url === '/api/abort-job') return response({ status: 'aborting' })
    if (url.startsWith('/api/job-status')) return response({ done: false })
    throw new Error('Unexpected request ' + url)
  })
  vi.stubGlobal('fetch', fetcher)
  vi.stubGlobal('EventSource', JobStream)
  session = await import('../src/run/session')
  store = await import('../src/projectStore')
  submit = await import('../src/run/runSubmit')
  vi.spyOn(submit, 'buildRunRequest').mockResolvedValue({} as never)
  store.loadProject(project(), null)
  await vi.waitFor(() => expect(session.isValidated()).toBe(true))
  await settle()
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] })
})
afterEach(async () => {
  if (root) flushSync(() => root!.unmount())
  root = null
  host?.remove()
  host = null
  document.getElementById('mesh-progress-overlay')?.remove()
  streams.forEach(stream => stream.finish())
  await settle()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
const meshPosts = () => fetcher.mock.calls.filter(([url]) => url === '/api/run-mesh')
const abortPosts = () => fetcher.mock.calls.filter(([url]) => url === '/api/abort-job')

it('reserves preparation before the first await and aborts it without a mesh POST', async () => {
  let receivedSignal: AbortSignal | undefined
  vi.mocked(submit.buildRunRequest).mockImplementationOnce((_kind, _sources, signal) => {
    receivedSignal = signal
    return new Promise((_resolve, reject) => {
      signal!.addEventListener('abort', () => {
        const error = new Error('worker preparation cancelled')
        error.name = 'AbortError'
        reject(error)
      }, { once: true })
    })
  })
  const running = session.runJob('mesh')
  const rejection = running.catch(error => error)
  const preparation = session.getRunPreparation()!
  expect(preparation).toMatchObject({ kind: 'mesh', phase: 'preparing', abortRequested: false })
  expect(receivedSignal).toBeInstanceOf(AbortSignal)
  expect(session.isUiFrozen()).toBe(true)
  await expect(session.runJob('mesh')).rejects.toThrow('a job is already running')
  await expect(session.runCheckMesh()).rejects.toThrow('a job is already running')
  await session.abortJob(preparation.token)
  expect(receivedSignal!.aborted).toBe(true)
  expect(await rejection).toMatchObject({ name: 'AbortError' })
  expect(meshPosts()).toHaveLength(0)
  expect(abortPosts()).toHaveLength(0)
  expect(session.getRunPreparation()).toBeNull()
  expect(session.getJob()).toBeNull()
  expect(session.isUiFrozen()).toBe(false)
})

it('retains an in-flight submission and stops its actual job only after the ACK', async () => {
  let finishSubmit!: (reply: Response) => void
  submitReply = () => new Promise(resolve => { finishSubmit = resolve })
  const running = session.runJob('mesh')
  await settle()
  const preparation = session.getRunPreparation()!
  expect(preparation.phase).toBe('submitting')
  expect(meshPosts()).toHaveLength(1)
  expect(meshPosts()[0]![1]?.signal).toBeUndefined()
  await session.abortJob(preparation.token)
  expect(session.getRunPreparation()).toMatchObject({ token: preparation.token, abortRequested: true })
  expect(session.isUiFrozen()).toBe(true)
  expect(abortPosts()).toHaveLength(0)
  await expect(session.runJob('solve')).rejects.toThrow('a job is already running')
  vi.useFakeTimers()
  finishSubmit(response({ jobId: 'accepted-real-job', caseDir: '/case/preparation' }))
  await running
  await settle()
  expect(abortPosts()).toHaveLength(1)
  expect(JSON.parse(String(abortPosts()[0]![1]?.body))).toEqual({ jobId: 'accepted-real-job' })
  expect(session.getRunPreparation()).toBeNull()
  expect(session.getJob()).toMatchObject({
    id: 'accepted-real-job', running: true, abortRequested: true, aborted: false,
  })
  expect(session.isUiFrozen()).toBe(true)
  streams[0]!.finish()
  expect(session.getJob()).toMatchObject({ id: 'accepted-real-job', running: false, aborted: true })
  expect(session.isUiFrozen()).toBe(false)
})

it('keeps a submitted old-case job accountable after the project changes', async () => {
  let finishSubmit!: (reply: Response) => void
  submitReply = () => new Promise(resolve => { finishSubmit = resolve })
  const running = session.runJob('mesh')
  await settle()
  const token = session.getRunPreparation()!.token
  store.loadProject(project('/case/after-submit'), null)
  await vi.waitFor(() => expect(session.getServerInfo()?.caseDir).toBe('/case/after-submit'))
  expect(session.getRunPreparation()).toMatchObject({ token, phase: 'submitting' })
  await session.abortJob(token)
  await expect(session.runJob('mesh')).rejects.toThrow('a job is already running')
  vi.useFakeTimers()
  finishSubmit(response({ jobId: 'old-case-real-job', caseDir: '/case/preparation' }))
  await running
  await settle()
  expect(meshPosts()).toHaveLength(1)
  expect(JSON.parse(String(meshPosts()[0]![1]?.body)).caseDir).toBe('/case/preparation')
  expect(JSON.parse(String(abortPosts()[0]![1]?.body))).toEqual({ jobId: 'old-case-real-job' })
  expect(session.getJob()).toMatchObject({
    id: 'old-case-real-job', caseDir: '/case/preparation', running: true, abortRequested: true,
  })
  expect(session.getCurrentCaseJob()).toBeNull()
  expect(session.isUiFrozen()).toBe(true)
  streams[0]!.finish()
  expect(session.isUiFrozen()).toBe(false)
})

it('ignores an old token and old completion after a newer preparation acquires the slot', async () => {
  let finishOld!: (body: never) => void
  let finishNew!: (body: never) => void
  vi.mocked(submit.buildRunRequest)
    .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
    .mockImplementationOnce(() => new Promise(resolve => { finishNew = resolve }))
  const oldRun = session.runJob('mesh')
  const oldRejection = oldRun.catch(error => error)
  const oldToken = session.getRunPreparation()!.token
  await session.abortJob(oldToken)
  const newRun = session.runJob('mesh')
  const newToken = session.getRunPreparation()!.token
  expect(newToken).not.toBe(oldToken)
  await session.abortJob(oldToken)
  expect(session.getRunPreparation()).toMatchObject({ token: newToken, abortRequested: false })
  finishOld({} as never)
  expect(await oldRejection).toMatchObject({ name: 'AbortError' })
  expect(session.getRunPreparation()).toMatchObject({ token: newToken })
  expect(session.isUiFrozen()).toBe(true)
  expect(meshPosts()).toHaveLength(0)
  finishNew({} as never)
  await newRun
  expect(meshPosts()).toHaveLength(1)
  expect(session.getJob()).toMatchObject({ id: 'real-job', running: true, aborted: false })
})

it('cancels a superseded case build and never submits its late result', async () => {
  let finishOld!: (body: never) => void
  let oldSignal: AbortSignal | undefined
  vi.mocked(submit.buildRunRequest).mockImplementationOnce((_kind, _sources, signal) => {
    oldSignal = signal
    return new Promise(resolve => { finishOld = resolve })
  })
  const oldRun = session.runJob('mesh')
  const oldRejection = oldRun.catch(error => error)
  store.loadProject(project('/case/new'), null)
  await vi.waitFor(() => expect(session.isValidated()).toBe(true))
  expect(oldSignal!.aborted).toBe(true)
  await session.runJob('mesh')
  finishOld({} as never)
  expect(await oldRejection).toMatchObject({ name: 'AbortError' })
  expect(meshPosts()).toHaveLength(1)
  expect(JSON.parse(String(meshPosts()[0]![1]?.body)).caseDir).toBe('/case/new')
  expect(session.getJob()).toMatchObject({ id: 'real-job', caseDir: '/case/new', running: true })
})

it('shows an enabled stop during preparation and lets the overlay action cancel without an error', async () => {
  const { readProjectFileText } = await import('../src/io/projectFiles')
  const { MeshPanel } = await import('../src/components/MeshPanel')
  const doc = readProjectFileText(readFileSync(resolve(process.cwd(), '../../../case/Slab/Slab.cfdproj'), 'utf8'))
  if (!doc.ok) throw new Error(doc.error)
  doc.project.meta = { ...doc.project.meta, casePath: '/case/preparation' }
  store.loadProject(doc.project, null)
  await vi.waitFor(() => expect(session.isValidated()).toBe(true))
  vi.mocked(submit.buildRunRequest).mockImplementationOnce((_kind, _sources, signal) =>
    new Promise((_resolve, reject) => signal!.addEventListener('abort', () => {
      const error = new Error('cancelled')
      error.name = 'AbortError'
      reject(error)
    }, { once: true })))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  flushSync(() => root!.render(<MeshPanel project={store.getProject()} />))
  flushSync(() => host!.querySelector<HTMLButtonElement>('#mesh-run-btn')!.click())
  expect(host.querySelector<HTMLButtonElement>('#mesh-abort-btn')?.disabled).toBe(false)
  const overlayStop = document.querySelector<HTMLButtonElement>('#mesh-preparation-abort-btn')!
  expect(overlayStop.disabled).toBe(false)
  flushSync(() => overlayStop.click())
  await settle()
  expect(session.getRunPreparation()).toBeNull()
  expect(meshPosts()).toHaveLength(0)
  expect(document.getElementById('mesh-progress-overlay')).toBeNull()
  expect(host.querySelector('[role="alert"]')).toBeNull()
  expect(host.querySelector<HTMLButtonElement>('#mesh-run-btn')!.disabled).toBe(false)
})


it('uses a shorter saved project limit across all stages and clears the timer after on-time strict success', async () => {
  const shorter = project()
  shorter.runDefaults = { meshConfig: { meshTimeLimitSeconds: 2 } } as CfdProject['runDefaults']
  store.loadProject(shorter, null)
  await settle()
  let finishBuild!: (body: never) => void
  vi.mocked(submit.buildRunRequest).mockImplementationOnce(() => new Promise(resolve => { finishBuild = resolve }))
  const run = session.runJob('mesh')
  await vi.advanceTimersByTimeAsync(500)
  finishBuild({} as never)
  await run
  expect(JSON.parse(String(meshPosts()[0]![1]?.body)).meshPreparationElapsedSeconds).toBe(.5)
  await vi.advanceTimersByTimeAsync(1499)
  streams[0]!.finish({ done: true, returncode: 0, checkMeshOk: true, failedChecks: 0 })
  await settle()
  expect(session.isMeshReady()).toBe(true)
  await vi.advanceTimersByTimeAsync(1)
  expect(abortPosts()).toHaveLength(0)
  expect(session.getJob()?.budgetExceeded).toBe(false)
})


it('lets a mesh job run past the five-minute target and only logs the time', async () => {
  // soft target (owner decision 2026-10-20): no deadline, no automatic abort
  const run = session.runJob('mesh')
  await settle()
  await run
  expect(session.getJob()).toMatchObject({ id: 'real-job', running: true })
  vi.advanceTimersByTime(20 * 60 * 1000)
  await settle()
  expect(fetcher.mock.calls.some(([url]) => url === '/api/abort-job')).toBe(false)
  expect(session.getJob()).toMatchObject({ running: true, budgetExceeded: false })
  streams.at(-1)!.finish({ done: true, returncode: 0, checkMeshOk: true, failedChecks: 0 })
  await settle()
  expect(session.getJob()).toMatchObject({ running: false, returncode: 0, budgetExceeded: false })
  const log = store.getLog()
  expect(JSON.stringify(log)).toMatch(/1200 s/)
})

it('batches a long job log into a few page updates and keeps the last lines', async () => {
  // Base604's mesh streamed 104 000 lines; one state update per line froze the page
  const run = session.runJob('mesh')
  await settle()
  await run
  let updates = 0
  const off = session.subscribeSession(() => { updates++ })
  const stream = streams.at(-1)!
  for (let i = 0; i < 3000; i++) stream.onmessage?.({ data: JSON.stringify(`line ${i}`) })
  expect(updates).toBe(0)
  vi.advanceTimersByTime(200)
  expect(updates).toBe(1)
  expect(session.getJob()!.lines.length).toBe(2000)
  expect(session.getJob()!.lines.at(-1)).toBe('line 2999')
  stream.onmessage?.({ data: JSON.stringify('last line') })
  stream.finish({ done: true, returncode: 0, checkMeshOk: true, failedChecks: 0 })
  await settle()
  expect(session.getJob()!.lines.at(-1)).toBe('last line')
  off()
})
