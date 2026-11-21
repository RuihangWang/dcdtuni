import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

let session: typeof import('../src/run/session')
let store: typeof import('../src/projectStore')
let fetcher: ReturnType<typeof vi.fn>
let streams: JobStream[]
let diskReady: boolean
let probe: () => Promise<Response>
let root: Root | null = null
let host: HTMLDivElement | null = null
const response = (data: unknown) => ({ ok: true, status: 200, statusText: '200',
  json: async () => data, text: async () => JSON.stringify(data) } as Response)
class JobStream {
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor() { streams.push(this) }
  finish(final: unknown) { this.onmessage?.({ data: JSON.stringify(final) }) }
}
beforeEach(async () => {
  vi.resetModules()
  diskReady = false
  streams = []
  probe = async () => response({ meshReady: true })
  fetcher = vi.fn(async (url: string) => {
    if (url === '/api/set-case-dir') return response({ ok: true, caseDir: '/case/strict' })
    if (url === '/api/server-info') return response({ dctwinAvailable: true, caseDir: '/case/strict', caseDirOk: true, allOk: true })
    if (String(url).split('?')[0] === '/api/scan-case-status') return response({ meshExists: true, meshReady: diskReady, solveReady: false, liquidReady: false, postprocessReady: false, logRuns: [], liquidRuns: [] })
    if (String(url).split('?')[0] === '/api/check-mesh') return probe()
    if (url === '/api/run-check-mesh') return response({ jobId: 'check-' + streams.length })
    throw new Error('Unexpected request ' + url)
  })
  vi.stubGlobal('fetch', fetcher)
  vi.stubGlobal('EventSource', JobStream)
  session = await import('../src/run/session')
  store = await import('../src/projectStore')
  const { readProjectFileText } = await import('../src/io/projectFiles')
  const { MeshPanel } = await import('../src/components/MeshPanel')
  const doc = readProjectFileText(readFileSync(resolve(process.cwd(), '../../../case/Slab/Slab.cfdproj'), 'utf8'))
  if (!doc.ok) throw new Error(doc.error)
  doc.project.meta = { ...doc.project.meta, casePath: '/case/strict' }
  store.loadProject(doc.project, null)
  await vi.waitFor(() => expect(session.getCaseStatus()?.meshExists).toBe(true))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  flushSync(() => root!.render(<MeshPanel project={store.getProject()} />))
})
afterEach(async () => {
  streams.forEach(stream => stream.finish({ done: true, returncode: -15, cancelled: true }))
  for (let i = 0; i < 12; i++) await Promise.resolve()
  if (root) flushSync(() => root!.unmount())
  root = null
  host?.remove()
  host = null
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
const checkButton = () => host!.querySelector<HTMLButtonElement>('#mesh-check-btn')!
const runButton = () => host!.querySelector<HTMLButtonElement>('#mesh-run-btn')!
it('offers a normal check from the mesh tab and says a rejected reload verdict is advice', async () => {
  expect(session.isMeshReady()).toBe(false)
  expect(checkButton().disabled).toBe(false)
  expect(host!.textContent).toContain((await import('../src/i18n')).t('hint.mesh_quality_advisory'))
  flushSync(() => checkButton().click())
  await vi.waitFor(() => expect(session.getCurrentCaseJob()?.kind).toBe('checkMesh'))
  expect(checkButton().disabled).toBe(true)
  expect(runButton().disabled).toBe(true)
  expect(fetcher.mock.calls.filter(([url]) => String(url).split('?')[0] === '/api/check-mesh')).toHaveLength(1)
  expect(fetcher.mock.calls.filter(([url]) => url === '/api/run-check-mesh')).toHaveLength(1)
  expect(fetcher.mock.calls.some(([url]) => url === '/api/run-mesh')).toBe(false)
})
it('a successful actual check-job transition visibly confirms quality', async () => {
  flushSync(() => checkButton().click())
  await vi.waitFor(() => expect(streams).toHaveLength(1))
  diskReady = true
  flushSync(() => streams[0]!.finish({ done: true, returncode: 0, checkMeshOk: true, failedChecks: 0 }))
  expect(session.isMeshReady()).toBe(true)
  await vi.waitFor(() => expect(checkButton().disabled).toBe(false))
  expect(host!.querySelector('#mesh-run-status')!.textContent).toContain((await import('../src/i18n')).t('status.checkmesh_ok'))
  expect(host!.textContent).not.toContain((await import('../src/i18n')).t('hint.mesh_quality_advisory'))
})
it('an explicit rejected quality verdict is reported even with a zero process exit', async () => {
  flushSync(() => checkButton().click())
  await vi.waitFor(() => expect(streams).toHaveLength(1))
  flushSync(() => streams[0]!.finish({ done: true, returncode: 0, checkMeshOk: false, failedChecks: 2 }))
  expect(session.isMeshReady()).toBe(false)
  await vi.waitFor(() => expect(checkButton().disabled).toBe(false))
  expect(host!.querySelector('#mesh-run-status')!.textContent).toContain((await import('../src/i18n')).t('status.checkmesh_rejected'))
})
it('a missing physical mesh gives a normal inline error and starts no quality job', async () => {
  probe = async () => response({ meshReady: false })
  flushSync(() => checkButton().click())
  const { t } = await import('../src/i18n')
  await vi.waitFor(() => expect(host!.querySelector('[role="alert"]')?.textContent).toContain(t('status.mesh_not_found')))
  expect(streams).toHaveLength(0)
  expect(session.isMeshReady()).toBe(false)
  expect(checkButton().disabled).toBe(false)
})
it('disables both actions during the physical probe and prevents duplicate checks', async () => {
  let resolveProbe!: (r: Response) => void
  probe = () => new Promise(resolve => { resolveProbe = resolve })
  flushSync(() => checkButton().click())
  await vi.waitFor(() => expect(resolveProbe).toBeTypeOf('function'))
  expect(checkButton().disabled).toBe(true)
  expect(runButton().disabled).toBe(true)
  flushSync(() => checkButton().click())
  expect(fetcher.mock.calls.filter(([url]) => String(url).split('?')[0] === '/api/check-mesh')).toHaveLength(1)
  resolveProbe(response({ meshReady: false }))
  await vi.waitFor(() => expect(checkButton().disabled).toBe(false))
  expect(streams).toHaveLength(0)
})

it('revokes an accepted disk verdict when the physical probe finds no mesh, including a late old scan', async () => {
  diskReady = true
  await session.scanCaseStatus()
  expect(session.isMeshReady()).toBe(true)
  let finishOldScan!: (r: Response) => void
  fetcher.mockImplementationOnce(() => new Promise(resolve => { finishOldScan = resolve }))
  const oldScan = session.scanCaseStatus()
  probe = async () => response({ meshReady: false })
  flushSync(() => checkButton().click())
  const { t } = await import('../src/i18n')
  await vi.waitFor(() => expect(host!.querySelector('[role="alert"]')?.textContent).toContain(t('status.mesh_not_found')))
  expect(session.isMeshReady()).toBe(false)
  finishOldScan(response({ meshExists: true, meshReady: true, solveReady: false, postprocessReady: false, logRuns: [], liquidRuns: [] }))
  await oldScan
  expect(session.isMeshReady()).toBe(false)
  expect(streams).toHaveLength(0)
})
