import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

// The mesh-change check (its own test: meshChangeBlock) answers at once: the mesh fits the geometry.
vi.mock('../../src/run/meshChange', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/run/meshChange')>()),
  currentMeshChange: () => ({ kind: 'none' }),
  meshBlockNow: () => null,
  watchMeshChange: () => () => undefined,
}))

/**
 * Checking the mesh belongs to the mesh tab alone (owner 2026-11-10).
 *
 * The solve tab only asks whether the mesh still matches the geometry
 * (`MeshChangeNote`, mocked here to "no change"): it has no check button, no
 * checkMesh verdict, warning list or log, and no pre-solve quality dialog —
 * quality is reported, never required (owner 2026-10-22). The mesh tab shows
 * what checkMesh found: which checks failed and the checker's own output.
 *
 * Driven through the real session with a stubbed backend.
 */

const CASE = '/srv/case/Slab'
const PROJECT = resolve(process.cwd(), '../../../case/Slab/Slab.cfdproj')

class JobStream {
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor(readonly url: string) { streams.push(this) }
  finish() {
    this.onmessage?.({ data: JSON.stringify({ done: true, returncode: 0, checkMeshOk: true, meshWarnings: [] }) })
  }
}

let streams: JobStream[]
let jobs: number
let host: HTMLDivElement
let root: Root
let prevLocale: string
let session: typeof import('../../src/run/session')
let store: typeof import('../../src/projectStore')
let i18n: typeof import('../../src/i18n')

/** The server ends a job on every stream open to it: the session's and the monitor feed's. */
const finishJob = (jobId: string) => {
  const open = streams.filter((s) => s.url.endsWith(`job_id=${jobId}`))
  expect(open.length).toBeGreaterThan(0)
  open.forEach((s) => s.finish())
}

const response = (data: unknown) => ({
  ok: true, status: 200, statusText: 'OK',
  json: async () => data, text: async () => JSON.stringify(data),
} as Response)

/** A backend with a meshed, undecomposed Slab case. Anything else answers empty. */
function api(url: string): unknown {
  url = new URL(String(url), 'http://fixture.invalid').pathname
  if (url === '/api/auth/check') return { authenticated: true, authRequired: false }
  if (url === '/api/set-case-dir') return { ok: true, caseDir: CASE }
  if (url === '/api/server-info') return { dctwinAvailable: true, caseDir: CASE, caseDirOk: true, allOk: true }
  if (url === '/api/scan-case-status') {
    return { meshExists: true, meshReady: true, solveReady: false, postprocessReady: false, logRuns: [], liquidRuns: [] }
  }
  if (url === '/api/check-mesh') return { meshReady: true, decomposed: false }
  if (url === '/api/run-check-mesh' || url === '/api/run-mesh') return { jobId: `job-${++jobs}` }
  if (url === '/api/server-patches') return { patches: [], acuReturnPatches: [] }
  return {}
}

beforeEach(async () => {
  vi.resetModules()
  streams = []
  jobs = 0
  vi.stubGlobal('fetch', vi.fn(async (url: string) => response(api(String(url)))))
  vi.stubGlobal('EventSource', JobStream)
  session = await import('../../src/run/session')
  store = await import('../../src/projectStore')
  i18n = await import('../../src/i18n')
  const submit = await import('../../src/run/runSubmit')
  vi.spyOn(submit, 'buildRunRequest').mockResolvedValue({} as never)
  prevLocale = i18n.getLocale()
  i18n.setLocale('zh-CN')
})

afterEach(() => {
  flushSync(() => root?.unmount())
  host?.remove()
  i18n.setLocale(prevLocale as ReturnType<typeof i18n.getLocale>)
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function openSlab() {
  const { readProjectFileText } = await import('../../src/io/projectFiles')
  const opened = readProjectFileText(readFileSync(PROJECT, 'utf8'))
  if (!opened.ok) throw new Error(opened.error)
  opened.project.meta = { ...opened.project.meta, casePath: CASE }
  store.loadProject(opened.project, null)
  await vi.waitFor(() => {
    expect(session.isValidated()).toBe(true)
    expect(session.isMeshReady()).toBe(true)
  })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
}

/** A finished checkMesh that rejected the mesh, with two lines of its output. */
async function rejectedCheck() {
  await session.runCheckMesh()
  await vi.waitFor(() => expect(session.getJob()).toMatchObject({ id: 'job-1', kind: 'checkMesh', running: true }))
  const open = streams.filter((s) => s.url.endsWith('job_id=job-1'))
  open.forEach((s) => {
    s.onmessage?.({ data: JSON.stringify('Checking topology...') })
    s.onmessage?.({ data: JSON.stringify('Failed 4 mesh checks.') })
  })
  await new Promise((r) => setTimeout(r, 300))
  open.forEach((s) => s.onmessage?.({ data: JSON.stringify({
    done: true, returncode: 1, checkMeshOk: false, failedChecks: 4,
    meshWarnings: [{ level: 2, code: 'bad_face_tets', count: 77, message: 'native finding' }],
  }) }))
  await vi.waitFor(() => expect(session.getJob()?.running).toBe(false))
  expect(session.isMeshReady()).toBe(false)
}

const MESH_CHECK_IDS = [
  '#check-mesh-btn', '#mesh-check-status', '#check-mesh-result', '#check-mesh-log',
  '#mesh-warn-detail', '#solve-mesh-quality-advisory', '#solve-mesh-rejection',
]

it('the solve tab has no mesh-check controls and solves a rejected mesh without a quality dialog', async () => {
  await openSlab()
  const { SolverPanel } = await import('../../src/components/SolverPanel')
  flushSync(() => root.render(<SolverPanel project={store.getProject()} />))
  for (const id of MESH_CHECK_IDS) expect(host.querySelector(id), id).toBeNull()

  await rejectedCheck()
  for (const id of MESH_CHECK_IDS) expect(host.querySelector(id), id).toBeNull()

  const solve = host.querySelector<HTMLButtonElement>('#solve-run-btn')!
  await vi.waitFor(() => expect(solve.disabled).toBe(false))
  flushSync(() => solve.click())
  expect(document.getElementById('mesh-quality-warning-dialog')).toBeNull()
  await vi.waitFor(() => expect(session.getJob()).toMatchObject({ id: 'job-2', kind: 'solve', running: true }))
  finishJob('job-2')
  await vi.waitFor(() => expect(session.getJob()?.running).toBe(false))
})

it('the mesh tab shows which checks failed and the checker output', async () => {
  await openSlab()
  const { MeshCheckDetail } = await import('../../src/components/MeshCheckDetail')
  flushSync(() => root.render(<MeshCheckDetail />))
  expect(host.querySelector('#mesh-warn-detail')).toBeNull()

  await rejectedCheck()
  await vi.waitFor(() => expect(host.querySelector('#check-mesh-log')?.textContent).toContain('Failed 4 mesh checks.'))
  await vi.waitFor(() => expect(host.querySelector('#mesh-warn-toggle')).not.toBeNull())
  const toggle = host.querySelector<HTMLButtonElement>('#mesh-warn-toggle')!
  expect(toggle.textContent).toContain(i18n.t('mesh_warn.title_rejected'))
  expect(toggle.textContent).toContain('(1)')
  flushSync(() => toggle.click())
  expect(host.querySelector('#mesh-warn-detail')!.textContent).toContain('77')
})
