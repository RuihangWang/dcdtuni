import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import type { CfdProject } from '../src/schema/cfdproj'

// The mesh-change check (its own test: meshChangeBlock) answers at once: the mesh fits the geometry.
vi.mock('../src/run/meshChange', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/run/meshChange')>()),
  currentMeshChange: () => ({ kind: 'none' }),
  meshBlockNow: () => null,
  watchMeshChange: () => () => undefined,
}))

// The solve tab's AI sub-tab end to end through the real session: the model list from the server, the request the
// run posts, and the result card once the job reports scanoResult.

let session: typeof import('../src/run/session')
let store: typeof import('../src/projectStore')
let streams: JobStream[]
let posts: { url: string; body: Record<string, unknown> }[]
let gpuQueries: string[]
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
const MODELS = [
  { id: 'abupt_bqa_ags_c86_s18', name_zh: 'AB-UPT + BQA + AGS（DCTS）', test_error: 0.368 },
  { id: 'scano_c118_s18', name_zh: 'SCANO（DCTS）', recommended: true, description_zh: '论文主模型', test_error: 0.3925,
    test_error_T: 0.172, test_rmse_T_K: 0.8, dataset: 'DCTS', adapter: 'scano-dcts-v1',
    training_ranges: { server_power_W: { min: 400, max: 2200 }, acu_supply_temperature_C: { min: 20, max: 20 } },
    mesh: { engine: 'snappy', base_cell_m: 0.34, note: 'DCTS recipe' } },
  { id: 'broken', error: 'model_card.json names no adapter' },
]
const RESULT = {
  ok: true, runDir: '/case/A/log/2026-10-27-12-00-00_A__SCANO-scano_c118_s18',
  runDirRelative: 'log/2026-10-27-12-00-00_A__SCANO-scano_c118_s18', state: 's_1', changedPatches: ['p1', 'p2'],
  nCells: 897361, nNodes: 1387891, timing: { total_s: 3.3, forward_s: 1.2 }, peakGpuBytes: 1, device: 'GPU 0',
  warnings: ['server power 5000-5000 W outside the training range 400-2200 W'],
}

async function render(onOpen = vi.fn()) {
  const { SolverPanel } = await import('../src/components/SolverPanel')
  flushSync(() => root!.render(<SolverPanel project={store.getProject()} onOpenPostprocess={onOpen} />))
  return onOpen
}

beforeEach(async () => {
  vi.resetModules(); streams = []; posts = []; gpuQueries = []
  localStorage.removeItem('dcdtuni.scano.inference')
  vi.stubGlobal('EventSource', JobStream)
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    const body = options?.body ? JSON.parse(String(options.body)) : {}
    if (options?.method === 'POST') posts.push({ url, body })
    if (url === '/api/set-case-dir') return response({ ok: true, caseDir: body.caseDir })
    if (url === '/api/server-info') return response({ dctwinAvailable: true, caseDir: '/case/A', caseDirOk: true, allOk: true })
    if (url.startsWith('/api/scan-case-status')) return response({ meshExists: true, meshReady: true, solveReady: true, postprocessReady: true, logRuns: [], liquidRuns: [] })
    if (url.startsWith('/api/check-mesh')) return response({ meshReady: true })
    if (url === '/api/ai/scano/models') return response({ models: MODELS })
    if (url === '/api/ai/scano/status') return response({ running: true, device: 'GPU 0 RTX 4090', busy: false })
    if (url === '/api/ai/scano/warmup') return response({ started: true })
    if (url.startsWith('/api/ai/scano/gpu?')) {
      const q = new URLSearchParams(url.split('?')[1])
      gpuQueries.push(q.get('inference') + ':' + q.get('passes'))
      const tta = q.get('inference') !== 'whole'
      return response({ running: true, busy: false, cpu: false, freeForAiBytes: 14 * 2 ** 30, workerCacheBytes: 2 ** 30,
        gpu: { index: '0', uuid: 'GPU-a', name: 'NVIDIA GeForce RTX 4090', totalMiB: 24564, usedMiB: 9634, freeMiB: 14930,
          utilization: 3, workerMiB: 9400 },
        room: { nodes: 8686403, cells: 6504270 },
        plan: { mode: tta ? 'tta' : 'whole', reason: tta ? 'whole does not fit' : 'requested', fits: tta,
          estimate_bytes: tta ? 12.5 * 2 ** 30 : 64 * 2 ** 30, chunk_share: 1, passes: tta ? Number(q.get('passes')) : null,
          forwards: tta ? 126 * Number(q.get('passes')) : 1, wholeBytes: 64 * 2 ** 30, ttaBytes: 12.5 * 2 ** 30, measured: [] } })
    }
    if (url === '/api/ai/scano/run') return response({ jobId: 'ai-1', caseDir: '/case/A', needsFirstIteration: false })
    if (url === '/api/run-mesh') return response({ jobId: 'cfd-1', caseDir: '/case/A' })
    if (url.startsWith('/api/job-status')) return response({ done: false })
    if (url === '/api/abort-job') return response({ status: 'aborting' })
    throw new Error('Unexpected ' + url)
  }))
  session = await import('../src/run/session'); store = await import('../src/projectStore')
  const submit = await import('../src/run/runSubmit')
  vi.spyOn(submit, 'buildRunRequest').mockResolvedValue({ cfdrunJson: '{}', cfdrunFilename: 'A.cfdrun', stlFiles: {}, meshOnly: false, solveOnly: true } as never)
  const locale = await import('../src/i18n'); locale.setLocale('zh-CN')
  store.loadProject({ schemaVersion: 4, uid: 'ai', name: 'ai', meta: { casePath: '/case/A' },
    runDefaults: { meshConfig: { meshEngine: 'snappy' } }, constructions: { racks: {} } } as unknown as CfdProject, null)
  await vi.waitFor(() => expect(session.isValidated()).toBe(true)); await settle()
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  document.body.insertAdjacentHTML('beforeend', '<div id="toolbar"></div><div id="right-panel"></div>')
})
afterEach(async () => {
  if (root) flushSync(() => root!.unmount()); root = null; host?.remove(); host = null
  streams.forEach((s) => s.finish({ done: true, returncode: -15, cancelled: true })); await settle()
  document.getElementById('toolbar')?.remove(); document.getElementById('right-panel')?.remove()
  vi.unstubAllGlobals(); vi.restoreAllMocks()
})

describe('AI inference sub-tab', () => {
  it('lists the models with the recommended one selected and posts the run to the AI endpoint', async () => {
    await render()
    ;(host!.querySelector('#solver-subtab-ai') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(host!.querySelectorAll('#scano-model option').length).toBe(3))
    await render()
    expect((host!.querySelector('#scano-model') as HTMLSelectElement).value).toBe('scano_c118_s18')
    expect((host!.querySelector('#scano-model option[value="broken"]') as HTMLOptionElement).disabled).toBe(true)
    expect(host!.querySelector('#scano-model-card')?.textContent).toBe('测试误差 0.39 · 温度均方根 0.80 K')
    expect(host!.querySelector('#scano-mode')).toBeNull()                         // no implementation choices
    expect(host!.querySelector('#scano-engine-warn')).toBeNull()                  // snappy: no warning
    ;(host!.querySelector('#scano-run-btn') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(posts.some((p) => p.url === '/api/ai/scano/run')).toBe(true))
    const run = posts.find((p) => p.url === '/api/ai/scano/run')!
    expect(run.body).toMatchObject({ model: 'scano_c118_s18', mode: 'auto', solveOnly: true, caseDir: '/case/A' })
    expect(posts.some((p) => p.url === '/api/run-mesh')).toBe(false)
    expect(session.getJob()).toMatchObject({ kind: 'scano', running: true })
  })

  it('shows the GPU live, sends the chosen inference with the run, and reports how the model ran', async () => {
    await render()
    ;(host!.querySelector('#solver-subtab-ai') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(host!.querySelector('#scano-gpu-plan')).not.toBeNull())
    await render()
    expect(host!.querySelector('#scano-inf-auto')?.getAttribute('aria-checked')).toBe('true')
    expect(host!.querySelector('#scano-gpu-device')?.textContent).toContain('GPU 0 · RTX 4090')
    expect(host!.querySelector('#scano-gpu-memory')?.textContent).toContain('已用 9.4 / 24.0 GiB')
    expect(host!.querySelector('#scano-gpu-memory')?.textContent).toContain('可用于推理 14.0 GiB')
    expect(host!.querySelector('#scano-gpu-room')?.textContent).toContain('8.69 M')
    expect(host!.querySelector('#scano-gpu-plan')?.textContent).toContain('TTA ×1 · 约 126 次前向 · 预估峰值 12.5 GiB')
    expect(host!.querySelector('#scano-gpu-plan')?.textContent).toContain('整网格约需 64.0 GiB')
    ;(host!.querySelector('#scano-inf-tta') as HTMLButtonElement).click(); await settle(); await render()
    const passes = host!.querySelector('#scano-passes') as HTMLInputElement
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(passes, '3')
    passes.dispatchEvent(new Event('input', { bubbles: true }))
    passes.dispatchEvent(new FocusEvent('focusout', { bubbles: true })); await settle(); await render()
    await vi.waitFor(() => expect(gpuQueries.at(-1)).toBe('tta:3'))
    ;(host!.querySelector('#scano-run-btn') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(posts.some((p) => p.url === '/api/ai/scano/run')).toBe(true))
    expect(posts.find((p) => p.url === '/api/ai/scano/run')!.body).toMatchObject({ inference: 'tta', passes: 3 })
    expect(JSON.parse(localStorage.getItem('dcdtuni.scano.inference')!)).toEqual({ inference: 'tta', passes: 3 })
    streams.at(-1)!.finish({ done: true, returncode: 0, scanoResult: { ...RESULT, inference: {
      request: 'auto', mode: 'tta', reason: 'whole room ran out of memory (…)', passes: 3, forwards: 378,
      peakBytes: 11.6 * 2 ** 30 } } }); await settle(); await render()
    expect(host!.querySelector('#scano-result-inference')?.textContent)
      .toBe('TTA ×3 · 378 次前向 · 峰值显存 11.6 GiB · 整网格显存不足，已自动改用 TTA')
    ;(host!.querySelector('#scano-inf-whole') as HTMLButtonElement).click(); await settle(); await render()
    expect(host!.querySelector('#scano-passes')).toBeNull()
    await vi.waitFor(() => expect(host!.querySelector('#scano-gpu')?.getAttribute('data-fits')).toBe('false'))
  })

  it('shows the result, its warnings, and opens it in post-processing', async () => {
    const onOpen = await render()
    ;(host!.querySelector('#solver-subtab-ai') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(host!.querySelectorAll('#scano-model option').length).toBe(3))
    await session.runJob('scano', { model: 'scano_c118_s18', mode: 'exact' })
    streams.at(-1)!.finish({ done: true, returncode: 0, scanoResult: RESULT }); await settle(); await render(onOpen)
    expect(session.getJob()?.scanoResult?.runDir).toBe(RESULT.runDir)
    expect(host!.querySelector('#scano-result')?.textContent).toContain('完成，用时 3.3 秒（897361 个单元）')
    expect(host!.querySelector('#scano-warnings')?.textContent).toContain('server power')
    const { takeRequestedPostprocessRun } = await import('../src/postprocess/ppRunRequest')
    ;(host!.querySelector('#scano-open-pp') as HTMLButtonElement).click()
    expect(onOpen).toHaveBeenCalled()
    expect(takeRequestedPostprocessRun()).toBe(RESULT.runDir)
  })

  it("warns when the project does not mesh with the model card's engine, and only then", async () => {
    store.setRunConfig('meshConfig', 'meshEngine', 'dcCart'); await settle()
    await render()
    ;(host!.querySelector('#solver-subtab-ai') as HTMLButtonElement).click(); await settle(); await render()
    expect(host!.querySelector('#scano-engine-warn')?.textContent).toContain('dcCart')
    const select = host!.querySelector('#scano-model') as HTMLSelectElement
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, 'abupt_bqa_ags_c86_s18')
    select.dispatchEvent(new Event('change', { bubbles: true })); await settle(); await render()
    expect(host!.querySelector('#scano-engine-warn')).toBeNull()                  // its card names no mesh
  })

  it('a steady CFD solve carries aiInit with the AI tab\'s model when 初始化 is AI, and none when uniform', async () => {
    const init = await import('../src/run/solveInit')
    const cards = await import('../src/postprocess/scanoModels')
    cards.resetModelCardsForTest()
    localStorage.setItem(init.SCANO_MODEL_KEY, 'abupt_bqa_ags_c86_s18')
    init.setSolveInit('ai')
    await session.runJob('solve')
    let run = posts.filter((p) => p.url === '/api/run-mesh').at(-1)!
    expect(run.body.aiInit).toEqual({ model: 'abupt_bqa_ags_c86_s18' })
    streams.at(-1)!.finish({ done: true, returncode: 0 }); await settle()
    init.setSolveInit('uniform')
    await session.runJob('solve')
    run = posts.filter((p) => p.url === '/api/run-mesh').at(-1)!
    expect(run.body.aiInit).toBeUndefined()
    localStorage.removeItem(init.SCANO_MODEL_KEY)
  })
})
