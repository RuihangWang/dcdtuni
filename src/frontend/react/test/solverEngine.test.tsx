import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import type { CfdProject } from '../src/schema/cfdproj'

vi.mock('../src/run/meshChange', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/run/meshChange')>()),
  currentMeshChange: () => ({ kind: 'none' }),
  meshBlockNow: () => null,
  watchMeshChange: () => () => undefined,
}))

// The solve page's engine choice through the real session: it exists only when the server was started with
// --gpu (gpuSolver.enabled in /api/server-info), and only then does a steady solve ask for the GPU solver.

let session: typeof import('../src/run/session')
let store: typeof import('../src/projectStore')
let engine: typeof import('../src/run/solverEngine')
let streams: JobStream[]
let posts: { url: string; body: Record<string, unknown> }[]
let gpuSolver: { enabled: boolean; devices?: string[] | null } | undefined
let solverType: 'steady' | 'transient'
let gpuStatus: Record<string, unknown>
let gpuStatusCalls: number
let root: Root | null = null
let host: HTMLDivElement | null = null
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
const response = (body: unknown, status = 200) =>
  ({ ok: status === 200, status, json: async () => body, text: async () => JSON.stringify(body) } as Response)
class JobStream {
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  constructor() { streams.push(this) }
  finish(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }) }
}

async function render() {
  const { SolverPanel } = await import('../src/components/SolverPanel')
  flushSync(() => root!.render(<SolverPanel project={store.getProject()} />))
}
const choose = (id: string, value: string) => {
  const select = host!.querySelector(id) as HTMLSelectElement
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, value)
  select.dispatchEvent(new Event('change', { bubbles: true }))
}
const lastRun = () => posts.filter((p) => p.url === '/api/run-mesh').at(-1)!

async function start(offer: typeof gpuSolver) {
  gpuSolver = offer
  session = await import('../src/run/session'); store = await import('../src/projectStore')
  engine = await import('../src/run/solverEngine')
  const submit = await import('../src/run/runSubmit')
  vi.spyOn(submit, 'buildRunRequest').mockImplementation(async () => ({
    cfdrunJson: JSON.stringify({ simulationConfig: { solverType } }), cfdrunFilename: 'A.cfdrun', stlFiles: {},
    meshOnly: false, solveOnly: true }) as never)
  const locale = await import('../src/i18n'); locale.setLocale('zh-CN')
  store.loadProject({ schemaVersion: 4, uid: 'gpu', name: 'gpu', meta: { casePath: '/case/A' },
    runDefaults: { meshConfig: { meshEngine: 'dcCart' } }, constructions: { racks: {} } } as unknown as CfdProject, null)
  await vi.waitFor(() => expect(session.isValidated()).toBe(true)); await settle()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  document.body.insertAdjacentHTML('beforeend', '<div id="toolbar"></div><div id="right-panel"></div>')
}

// the panel's module graph is transformed once here, outside the first test's time limit
beforeAll(async () => { await import('../src/components/SolverPanel') }, 120_000)

beforeEach(() => {
  vi.resetModules(); streams = []; posts = []; solverType = 'steady'; gpuStatusCalls = 0
  gpuStatus = { enabled: true, devices: ['GPU-a', 'GPU-b'], message: null,
    cards: [
      { index: '1', uuid: 'GPU-a', name: 'NVIDIA GeForce RTX 4090', totalMiB: 24564, usedMiB: 20000, freeMiB: 4564, utilization: 97, solver: null },
      { index: '3', uuid: 'GPU-b', name: 'NVIDIA GeForce RTX 4090', totalMiB: 24564, usedMiB: 2048, freeMiB: 22516, utilization: 12, solver: 'standby' },
    ],
    mesh: { cells: 970982, estimateMiB: 18278, bestFreeMiB: 22516, fits: true } }
  localStorage.removeItem('dcdtuni.solve.engine'); localStorage.removeItem('dcdtuni.solve.init')
  vi.stubGlobal('EventSource', JobStream)
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {}
    if (options?.method === 'POST') posts.push({ url, body })
    if (url === '/api/set-case-dir') return response({ ok: true, caseDir: body.caseDir })
    if (url === '/api/server-info') return response({ dctwinAvailable: true, caseDir: '/case/A', caseDirOk: true,
      allOk: true, ...(gpuSolver ? { gpuSolver } : {}) })
    if (url.startsWith('/api/scan-case-status')) return response({ meshExists: true, meshReady: true, solveReady: true, postprocessReady: true, logRuns: [], liquidRuns: [] })
    if (url.startsWith('/api/check-mesh')) return response({ meshReady: true })
    if (url === '/api/ai/scano/models') return response({ models: [{ id: 'scano_c118_s18', recommended: true }] })
    if (url === '/api/run-mesh') return response({ jobId: 'cfd-1', caseDir: '/case/A' })
    if (url === '/api/gpu-solver/status') { gpuStatusCalls += 1; return response(gpuStatus) }
    if (url.startsWith('/api/job-status')) return response({ done: false })
    if (url === '/api/abort-job') return response({ status: 'aborting' })
    throw new Error('Unexpected ' + url)
  }))
})
afterEach(async () => {
  if (root) flushSync(() => root!.unmount()); root = null; host?.remove(); host = null
  streams.forEach((s) => s.finish({ done: true, returncode: -15, cancelled: true })); await settle()
  document.getElementById('toolbar')?.remove(); document.getElementById('right-panel')?.remove()
  vi.unstubAllGlobals(); vi.restoreAllMocks()
})

describe('solver engine choice', () => {
  it('is absent on a server started without --gpu, and a remembered GPU choice does not reach the request', async () => {
    localStorage.setItem('dcdtuni.solve.engine', 'gpuNewton')
    await start({ enabled: false, devices: null })
    await render()
    expect(host!.querySelector('#solve-engine-select')).toBeNull()
    expect((host!.querySelector('#solve-init-select') as HTMLSelectElement).disabled).toBe(false)
    await session.runJob('solve')
    expect(lastRun().body.solverEngine).toBeUndefined()
  })

  it('is absent when the server reports nothing about a GPU solver (an older server)', async () => {
    await start(undefined)
    await render()
    expect(host!.querySelector('#solve-engine-select')).toBeNull()
  })

  it('offers OpenFOAM and the GPU solver on a --gpu server; the GPU solve carries solverEngine and no aiInit', async () => {
    await start({ enabled: true, devices: ['GPU-a', 'GPU-b'] })
    const init = await import('../src/run/solveInit')
    init.setSolveInit('ai')
    await render()
    const select = host!.querySelector('#solve-engine-select') as HTMLSelectElement
    expect(select.value).toBe('openfoam')                                    // OpenFOAM stays the default
    expect([...select.options].map((o) => o.value)).toEqual(['openfoam', 'gpuNewton'])
    await session.runJob('solve')
    expect(lastRun().body.solverEngine).toBeUndefined()
    expect(lastRun().body.aiInit).toEqual({ model: 'scano_c118_s18' })
    streams.at(-1)!.finish({ done: true, returncode: 0 }); await settle()

    choose('#solve-engine-select', 'gpuNewton'); await settle(); await render()
    expect(engine.getSolverEngine()).toBe('gpuNewton')
    expect(localStorage.getItem('dcdtuni.solve.engine')).toBe('gpuNewton')
    // the GPU solver starts from the uniform field: the initialisation row shows it and cannot be changed
    const initSelect = host!.querySelector('#solve-init-select') as HTMLSelectElement
    expect(initSelect.disabled).toBe(true)
    expect(initSelect.value).toBe('uniform')
    await session.runJob('solve')
    expect(lastRun().body.solverEngine).toBe('gpuNewton')
    expect(lastRun().body.aiInit).toBeUndefined()
    streams.at(-1)!.finish({ done: true, returncode: 0 }); await settle()

    choose('#solve-engine-select', 'openfoam'); await settle(); await render()
    expect((host!.querySelector('#solve-init-select') as HTMLSelectElement).value).toBe('ai')   // the choice was kept
    await session.runJob('solve')
    expect(lastRun().body.solverEngine).toBeUndefined()
  })

  it('shows every card of the launch with its memory and utilisation while the GPU solver is selected', async () => {
    await start({ enabled: true, devices: ['GPU-a', 'GPU-b'] })
    await render()
    expect(host!.querySelector('#gpu-solver-status')).toBeNull()             // OpenFOAM selected: no GPU block
    expect(gpuStatusCalls).toBe(0)
    choose('#solve-engine-select', 'gpuNewton'); await settle(); await render()
    await vi.waitFor(() => expect(host!.querySelectorAll('.gpu-solver-card').length).toBe(2))
    const cards = [...host!.querySelectorAll('.gpu-solver-card')] as [HTMLElement, HTMLElement]
    expect(cards.map((c) => c.dataset.card)).toEqual(['1', '3'])
    expect(cards[0].textContent).toContain('GPU 1 · RTX 4090')
    expect(cards[0].textContent).toContain('利用率 97%')
    expect(cards[0].textContent).toContain('显存 已用 19.5 / 24.0 GiB')
    expect(cards[0].textContent).toContain('空闲 4.5 GiB')
    expect(cards[1].textContent).toContain('求解进程待命')
    expect(cards[1].textContent).toContain('利用率 12%')
    // the estimate of this mesh is drawn on the card a solve would take, the freest one
    expect(cards[0].querySelector('.scano-gpu-bar > i')).toBeNull()
    expect(cards[1].querySelector('.scano-gpu-bar > i')).not.toBeNull()
    expect(host!.querySelector('#gpu-solver-mesh')!.textContent).toContain('0.97 M')
    expect(host!.querySelector('#gpu-solver-mesh')!.textContent).toContain('17.8 GiB')
    expect(host!.querySelector('#gpu-solver-no-fit')).toBeNull()
    expect(host!.querySelector('#gpu-solver-failure')).toBeNull()
    choose('#solve-engine-select', 'openfoam'); await settle(); await render()
    expect(host!.querySelector('#gpu-solver-status')).toBeNull()
  })

  it('warns before the solve when no card has the memory, and names the restart with --gpu', async () => {
    await start({ enabled: true, devices: ['GPU-a'] })
    gpuStatus = { ...gpuStatus, cards: (gpuStatus.cards as unknown[]).slice(0, 1),
      mesh: { cells: 1158391, estimateMiB: 21651, bestFreeMiB: 4564, fits: false } }
    engine.setSolverEngine('gpuNewton')
    await render()
    await vi.waitFor(() => expect(host!.querySelector('#gpu-solver-no-fit')).not.toBeNull())
    const warning = host!.querySelector('#gpu-solver-no-fit')!.textContent!
    expect(warning).toContain('start_server.sh'); expect(warning).toContain('--gpu')
    expect(warning).toContain('21.1 GiB'); expect(warning).toContain('4.5 GiB')
    expect((host!.querySelector('#gpu-solver-status') as HTMLElement).dataset.fits).toBe('false')
  })

  it('a solve the GPU solver ended for lack of memory shows the error and what to do', async () => {
    await start({ enabled: true, devices: ['GPU-a'] })
    engine.setSolverEngine('gpuNewton')
    await render()
    await session.runJob('solve')
    expect(host!.querySelector('#gpu-solver-failure')).toBeNull()
    streams.at(-1)!.finish({ done: true, returncode: 1,
      solverFailure: { code: 'gpu_out_of_memory', error: 'this mesh (1.16 million cells) needs about 21.1 GiB' } })
    await settle(); await render()
    expect(session.getJob()!.solverFailure).toEqual({ code: 'gpu_out_of_memory',
      error: 'this mesh (1.16 million cells) needs about 21.1 GiB' })
    await vi.waitFor(() => expect(host!.querySelector('#gpu-solver-failure')).not.toBeNull())
    const alert = host!.querySelector('#gpu-solver-failure')!
    expect(alert.getAttribute('role')).toBe('alert')
    expect(alert.textContent).toContain('GPU 显存不足')
    expect(alert.textContent).toContain('start_server.sh'); expect(alert.textContent).toContain('--gpu')
    expect(alert.textContent).toContain('21.1 GiB')
    // the next solve clears it
    await session.runJob('solve'); await settle(); await render()
    expect(host!.querySelector('#gpu-solver-failure')).toBeNull()
  })

  it('a transient solve never asks for the GPU solver', async () => {
    await start({ enabled: true, devices: null })
    engine.setSolverEngine('gpuNewton')
    solverType = 'transient'
    await session.runJob('solve')
    expect(lastRun().body.solverEngine).toBeUndefined()
  })
})
