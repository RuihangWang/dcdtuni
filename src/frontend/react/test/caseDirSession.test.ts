import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CfdProject } from '../src/schema/cfdproj'

let store: typeof import('../src/projectStore')
let session: typeof import('../src/run/session')
let paths: typeof import('../src/run/caseDir')
let backendPath: string | undefined
let calls: { url: string; path?: string; createIfMissing?: boolean }[]
let fetcher: ReturnType<typeof vi.fn>
const response = (data: unknown, status = 200) => ({
  ok: status >= 200 && status < 300, status, statusText: String(status),
  json: async () => data, text: async () => JSON.stringify(data),
} as Response)
const info = (path = backendPath) => response({
  dctwinAvailable: true, dctwinVersion: 'test', pythonExecutable: '/python',
  caseDir: path, caseDirOk: !!path, allOk: !!path,
})
const project = (path = '') => ({
  schemaVersion: 4, uid: 'same-uid', name: 'test', meta: { casePath: path },
  constructions: { racks: {} },
} as CfdProject)
const settle = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

beforeEach(async () => {
  vi.resetModules()
  store = await import('../src/projectStore')
  session = await import('../src/run/session')
  paths = await import('../src/run/caseDir')
  backendPath = '/old-server-case'
  calls = []
  fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/set-case-dir') {
      const data = JSON.parse(String(options?.body))
      calls.push({ url, path: data.caseDir, createIfMissing: data.createIfMissing })
      backendPath = data.caseDir.startsWith('~/') ? '/home/user/' + data.caseDir.slice(2) : data.caseDir
      return response({ ok: true, caseDir: backendPath })
    }
    calls.push({ url: String(url).split('?')[0]! })
    if (url === '/api/server-info') return info()
    if (String(url).split('?')[0] === '/api/scan-case-status') return response({
      meshReady: true, solveReady: true, postprocessReady: true, logRuns: [{ path: backendPath }], liquidRuns: [],
    })
    throw new Error(`Unexpected request ${url}`)
  })
  vi.stubGlobal('fetch', fetcher)
})

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

async function ready(path: string) {
  await vi.waitFor(() => {
    expect(session.getServerInfo()?.caseDir).toBe(path)
    expect(session.isValidated()).toBe(true)
    expect(session.getCaseStatus()?.postprocessReady).toBe(true)
  })
}

describe('automatic project directory restoration', () => {
  it('restores the saved path, refreshes connectivity, and discovers results without an edit', async () => {
    store.loadProject(project('~/工作目录'), null)
    store.logOperation('loadProject', 'local-file.cfdproj')
    await ready('/home/user/工作目录')
    expect(calls.map(c => c.url)).toEqual(['/api/set-case-dir', '/api/server-info', '/api/scan-case-status'])
    expect(calls[0]).toMatchObject({ path: '~/工作目录', createIfMissing: false })
    expect(paths.readCasePath()).toBe('/home/user/工作目录')
    expect(store.isDirty()).toBe(false)
    expect(store.undoDepth()).toBe(0)
  })

  it.each([
    [404, 'case_dir_missing'], [400, 'case_dir_not_directory'],
  ])('clears only a confirmed invalid directory (%s, %s)', async (status, code) => {
    fetcher.mockImplementationOnce(async () => response({ ok: false, code, error: 'invalid directory' }, Number(status)))
    store.loadProject(project('/missing'), null)
    await vi.waitFor(() => expect(paths.readCasePath()).toBe(''))
    expect(session.isValidated()).toBe(false)
    expect(session.isMeshReady()).toBe(false)
    expect(backendPath).toBe('/old-server-case')
    expect(session.getServerDiagnosis()?.key).toBe('log.case_path_invalid')
    // and the editor is asked to let the user choose a directory again
    expect(session.getInvalidCasePath()).toBe('/missing')
  })

  it('forgets the invalid-path prompt once a directory is set or the prompt is dismissed', async () => {
    fetcher.mockImplementationOnce(async () => response({ ok: false, code: 'case_dir_missing', error: 'x' }, 404))
    store.loadProject(project('case/FromAnotherComputer'), null)
    await vi.waitFor(() => expect(session.getInvalidCasePath()).toBe('case/FromAnotherComputer'))
    await session.setCaseDir('/chosen/case')
    expect(session.getInvalidCasePath()).toBeNull()
    fetcher.mockImplementationOnce(async () => response({ ok: false, code: 'case_dir_missing', error: 'x' }, 404))
    store.loadProject(project('/gone'), null)
    await vi.waitFor(() => expect(session.getInvalidCasePath()).toBe('/gone'))
    session.dismissInvalidCasePath()
    expect(session.getInvalidCasePath()).toBeNull()
  })

  it('learns the repository root from server info and stores the portable path', async () => {
    fetcher.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/set-case-dir') {
        const data = JSON.parse(String(options?.body))
        calls.push({ url, path: data.caseDir })
        const abs = data.caseDir.startsWith('/') ? data.caseDir : '/clone/' + data.caseDir
        backendPath = abs
        return response({ ok: true, caseDir: abs, portablePath: abs.startsWith('/clone/') ? abs.slice(7) : abs })
      }
      if (url === '/api/server-info') return response({
        dctwinAvailable: true, caseDir: backendPath, caseDirOk: true, allOk: true, repoRoot: '/clone',
      })
      if (String(url).split('?')[0] === '/api/scan-case-status') return response({
        meshReady: true, solveReady: true, postprocessReady: true, logRuns: [], liquidRuns: [],
      })
      throw new Error(`Unexpected request ${url}`)
    })
    store.loadProject(project('case/Tri_angle'), null)
    await vi.waitFor(() => expect(session.isValidated()).toBe(true))
    expect(calls.find((c) => c.url === '/api/set-case-dir')?.path).toBe('case/Tri_angle')
    expect(paths.readCasePath()).toBe('/clone/case/Tri_angle')
    expect(store.getProject()?.meta?.casePath).toBe('case/Tri_angle')
    expect(store.isDirty()).toBe(false)
  })

  it.each([401, 403, 500])('keeps the saved path after an HTTP %s failure', async (status) => {
    fetcher.mockImplementationOnce(async () => response({ ok: false, error: 'unavailable' }, status))
    store.loadProject(project('/saved'), null)
    await vi.waitFor(() => expect(session.getServerInfoError()).toBeTruthy())
    expect(paths.readCasePath()).toBe('/saved')
    expect(session.isValidated()).toBe(false)
    expect(store.isDirty()).toBe(false)
    expect(session.getServerDiagnosis()?.key).toBe('log.case_path_restore_failed')
  })

  it('retains a path on network failure and restores it when connectivity is refreshed', async () => {
    fetcher.mockRejectedValueOnce(new Error('offline'))
    store.loadProject(project('/saved'), null)
    await vi.waitFor(() => expect(session.getServerInfoError()).toBeTruthy())
    expect(paths.readCasePath()).toBe('/saved')
    expect(session.getServerDiagnosis()?.kind).toBe('unreachable')
    await session.refreshServerInfo()
    await ready('/saved')
  })

  it('keeps solving locked after reload when files exist but strict quality is rejected', async () => {
    fetcher.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/set-case-dir') {
        backendPath = JSON.parse(String(options?.body)).caseDir
        return response({ ok: true, caseDir: backendPath })
      }
      if (url === '/api/server-info') return info()
      if (String(url).split('?')[0] === '/api/scan-case-status') return response({
        meshExists: true, meshReady: false, solveReady: false,
        postprocessReady: false, logRuns: [], liquidRuns: [],
      })
      throw new Error('unexpected request ' + url)
    })
    store.loadProject(project('/rejected'), null)
    await vi.waitFor(() => expect(session.getCaseStatus()?.meshExists).toBe(true))
    expect(session.isValidated()).toBe(true)
    expect(session.isMeshReady()).toBe(false)
    expect(session.getCurrentCaseJob()).toBeNull()
  })

  it('ignores a startup response that arrives after a different project was loaded', async () => {
    let finish!: (response: Response) => void
    fetcher.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const startup = session.refreshServerInfo()
    store.loadProject(project('/new'), null)
    await ready('/new')
    finish(info('/old-server-case'))
    await startup
    expect(paths.readCasePath()).toBe('/new')
    expect(session.getServerInfo()?.caseDir).toBe('/new')
  })

  it('serializes delayed old POSTs so the newest loaded project wins', async () => {
    let finish!: () => void
    fetcher.mockImplementationOnce(async (_url: string, options: RequestInit) => {
      const data = JSON.parse(String(options.body))
      calls.push({ url: '/api/set-case-dir', path: data.caseDir })
      await new Promise<void>(resolve => { finish = resolve })
      backendPath = data.caseDir
      return response({ ok: true, caseDir: backendPath })
    })
    store.loadProject(project('/A'), null)
    await settle()
    store.loadProject(project('/B'), null)
    expect(session.isValidated()).toBe(false)
    await settle()
    expect(calls.filter(c => c.url === '/api/set-case-dir')).toHaveLength(1)
    finish()
    await ready('/B')
    expect(backendPath).toBe('/B')
    expect(paths.readCasePath()).toBe('/B')
    expect(calls.filter(c => c.url === '/api/set-case-dir').map(c => c.path)).toEqual(['/A', '/B'])
    expect(store.undoDepth()).toBe(0)
  })

  it('never gives a project without a saved path the old server directory', async () => {
    store.loadProject(project('/A'), null)
    await ready('/A')
    store.loadProject(project(), null)
    expect(session.isValidated()).toBe(false)
    expect(session.getCaseStatus()).toBeNull()
    await session.refreshServerInfo()
    expect(paths.readCasePath()).toBe('')
    expect(session.isValidated()).toBe(false)
    expect(session.getServerInfo()?.caseDir).toBeUndefined()
  })

  it('ignores result status from the previous project', async () => {
    store.loadProject(project('/A'), null)
    await ready('/A')
    let finish!: (value: Response) => void
    fetcher.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const oldScan = session.scanCaseStatus()
    store.loadProject(project('/B'), null)
    await ready('/B')
    finish(response({ meshReady: false, postprocessReady: false, logRuns: [] }))
    await oldScan
    expect(session.getCaseStatus()?.postprocessReady).toBe(true)
    expect(session.getCaseStatus()?.logRuns[0]?.path).toBe('/B')
  })
})

describe('background jobs after a project switch', () => {
  class JobStream {
    onmessage: ((event: { data: string }) => void) | null = null
    onerror: (() => void) | null = null
    close = vi.fn()
    constructor() { streams.push(this) }
    finish() { this.onmessage?.({ data: JSON.stringify({
      done: true, returncode: 0, checkMeshOk: true, failedChecks: 0,
    }) }) }
  }
  let streams: JobStream[]

  beforeEach(async () => {
    streams = []
    vi.stubGlobal('EventSource', JobStream)
    const submit = await import('../src/run/runSubmit')
    vi.spyOn(submit, 'buildRunRequest').mockResolvedValue({} as never)
    const original = fetcher.getMockImplementation()!
    fetcher.mockImplementation(async (url: string, options?: RequestInit) => {
      if (String(url).split('?')[0] === '/api/check-mesh') return response({ meshReady: true })
      if (url === '/api/run-check-mesh' || url === '/api/run-mesh') {
        calls.push({ url: String(url).split('?')[0]! })
        return response({ jobId: 'job-A' })
      }
      if (url.startsWith('/api/job-status')) return response({ done: true, returncode: 0 })
      if (String(url).split('?')[0] === '/api/scan-case-status' && backendPath === '/B') {
        calls.push({ url: String(url).split('?')[0]! })
        return response({ meshReady: false, postprocessReady: true, logRuns: [{ path: '/B' }], liquidRuns: [] })
      }
      return original(url, options)
    })
    store.loadProject(project('/A'), null)
    await ready('/A')
  })

  it.each(['mesh', 'solve'] as const)('binds %s to the submitting tab before asynchronous export', async (kind) => {
    let complete!: (body: never) => void
    const submit = await import('../src/run/runSubmit')
    vi.mocked(submit.buildRunRequest).mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
    const captured: { url: string; body?: Record<string, unknown> }[] = []
    const original = fetcher.getMockImplementation()!
    fetcher.mockImplementation(async (url: string, options?: RequestInit) => {
      captured.push({ url, body: options?.body ? JSON.parse(String(options.body)) : undefined })
      return original(url, options)
    })
    const pending = session.runJob(kind)
    backendPath = '/other-tab-case'
    complete({} as never)
    await pending
    expect(captured.find(item => item.url === '/api/run-mesh')?.body?.caseDir).toBe('/A')
    expect(session.getJob()?.caseDir).toBe('/A')
  })

  it('reads and checks its own case when another tab changes the server selection', async () => {
    const captured: { url: string; body?: Record<string, unknown> }[] = []
    const original = fetcher.getMockImplementation()!
    fetcher.mockImplementation(async (url: string, options?: RequestInit) => {
      captured.push({ url, body: options?.body ? JSON.parse(String(options.body)) : undefined })
      return original(url, options)
    })
    backendPath = '/other-tab-case'
    await session.scanCaseStatus()
    await session.runCheckMesh()
    expect(captured.some(item => item.url === '/api/scan-case-status?caseDir=%2FA')).toBe(true)
    expect(captured.some(item => item.url === '/api/check-mesh?caseDir=%2FA')).toBe(true)
    expect(captured.find(item => item.url === '/api/run-check-mesh')?.body?.caseDir).toBe('/A')
    expect(session.getJob()?.caseDir).toBe('/A')
  })

  it('does not unlock solving on B when a checkMesh started on A completes', async () => {
    await session.runCheckMesh()
    expect(session.isUiFrozen()).toBe(false)
    store.loadProject(project('/B'), null)
    await ready('/B')
    expect(session.isMeshReady()).toBe(false)
    const scans = calls.filter(call => call.url === '/api/scan-case-status').length
    streams[0]!.finish()
    expect(session.getJob()).toMatchObject({ id: 'job-A', running: false, returncode: 0 })
    expect(session.isMeshReady()).toBe(false)
    expect(calls.filter(call => call.url === '/api/scan-case-status')).toHaveLength(scans)
    expect(streams[0]!.close).toHaveBeenCalledOnce()
  })

  it('a stop whose done event arrives before the abort reply leaves the run finished', async () => {
    // The server holds /api/abort-job until the process tree is gone, marks the
    // job done, and the stream says so first. The abort's own completion must
    // not restore the job it read before the request (running: true) — that
    // left the mesh buttons stuck on "meshing" (Shinaide_Liquid, 2026-10-01).
    let reply!: (value: Response) => void
    const original = fetcher.getMockImplementation()!
    fetcher.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url === '/api/abort-job') return new Promise<Response>((resolve) => { reply = resolve })
      return original(url, options)
    })
    await session.runJob('mesh')
    const stopping = session.abortJob()
    streams[0]!.onmessage?.({ data: JSON.stringify({ done: true, returncode: 143 }) })
    expect(session.getJob()).toMatchObject({ running: false, returncode: 143 })
    reply(response({ status: 'aborted', jobId: 'job-A' }))
    await stopping
    expect(session.getJob()).toMatchObject({ id: 'job-A', running: false, returncode: 143, aborted: true })
    expect(session.isUiFrozen()).toBe(false)
  })

  it('does not apply an old mesh completion received through fallback polling to B', async () => {
    await session.runJob('mesh')
    store.loadProject(project('/B'), null)
    await ready('/B')
    vi.useFakeTimers()
    streams[0]!.onerror?.()
    await vi.advanceTimersByTimeAsync(5000)
    expect(session.getJob()).toMatchObject({ id: 'job-A', running: false, returncode: 0 })
    expect(session.isMeshReady()).toBe(false)
  })

  it('does not run sensor export or add solve completion logs to another project', async () => {
    const results = await import('../src/run/sensorResults')
    const exportSensors = vi.spyOn(results, 'onSolveFinished').mockResolvedValue(null)
    await session.runJob('solve')
    store.loadProject(project('/B'), null)
    await ready('/B')
    const logs = [...store.getLog()]
    streams[0]!.finish()
    expect(exportSensors).not.toHaveBeenCalled()
    expect(store.getLog()).toEqual(logs)
    expect(session.getJob()?.running).toBe(false)
  })

  it('reports the actual Mesh/case.foam path for the submitting case', async () => {
    await session.runJob('mesh')
    backendPath = '/other-tab-case'
    streams[0]!.finish()
    expect(JSON.stringify(store.getLog())).toContain('/A/Mesh/case.foam')
    expect(JSON.stringify(store.getLog())).not.toContain('/other-tab-case/case.foam')
  })

  it('still grants mesh readiness when a check belongs to the current project', async () => {
    await session.runCheckMesh()
    streams[0]!.finish()
    expect(session.isMeshReady()).toBe(true)
    expect(session.getJob()?.running).toBe(false)
  })

  it.each(['mesh', 'checkMesh'] as const)(
    'unlocks a fresh %s success from disk=false before its new scan, then permits revocation',
    async (kind) => {
      fetcher.mockImplementationOnce(async () => response({
        meshExists: true, meshReady: false, postprocessReady: true,
        logRuns: [{ path: '/A' }], liquidRuns: [],
      }))
      await session.scanCaseStatus()
      expect(session.isMeshReady()).toBe(false)
      expect(session.isPostprocessReady()).toBe(true)

      if (kind === 'mesh') await session.runJob('mesh')
      else await session.runCheckMesh()
      let finishFreshScan!: (value: Response) => void
      fetcher.mockImplementationOnce(() => new Promise(resolve => { finishFreshScan = resolve }))
      streams[0]!.finish()

      // The explicit new quality success supersedes the pre-run disk=false.
      expect(session.isMeshReady()).toBe(true)
      expect(session.isPostprocessReady()).toBe(true)
      // Only a scan requested after that success may revoke it.
      finishFreshScan(response({
        meshExists: true, meshReady: false, postprocessReady: true,
        logRuns: [{ path: '/A' }], liquidRuns: [],
      }))
      await settle()
      expect(session.isMeshReady()).toBe(false)
    },
  )

  it('ignores a late running-job scan after a fresh success from disk=false', async () => {
    fetcher.mockImplementationOnce(async () => response({
      meshExists: true, meshReady: false, postprocessReady: true,
      logRuns: [{ path: '/A' }], liquidRuns: [],
    }))
    await session.scanCaseStatus()
    expect(session.isMeshReady()).toBe(false)
    await session.runJob('mesh')
    let finishOldScan!: (value: Response) => void
    fetcher.mockImplementationOnce(() => new Promise(resolve => { finishOldScan = resolve }))
    const oldScan = session.scanCaseStatus()
    streams[0]!.finish()
    expect(session.isMeshReady()).toBe(true)
    finishOldScan(response({
      meshExists: true, meshReady: false, postprocessReady: true,
      logRuns: [{ path: '/A' }], liquidRuns: [],
    }))
    await oldScan
    await settle()
    expect(session.isMeshReady()).toBe(true)
  })

  it('keeps a fresh success from disk=false if its new status scan fails', async () => {
    fetcher.mockImplementationOnce(async () => response({
      meshExists: true, meshReady: false, postprocessReady: true,
      logRuns: [{ path: '/A' }], liquidRuns: [],
    }))
    await session.scanCaseStatus()
    await session.runCheckMesh()
    fetcher.mockRejectedValueOnce(new Error('scan unavailable'))
    streams[0]!.finish()
    expect(session.isMeshReady()).toBe(true)
    await settle()
    expect(session.isMeshReady()).toBe(true)
  })

  it.each(['mesh', 'checkMesh'] as const)(
    'refuses %s terminal success without an explicit true quality field',
    async (kind) => {
      const invalid = [undefined, null, false, 'true', 1]
      for (const checkMeshOk of invalid) {
        if (kind === 'mesh') await session.runJob('mesh')
        else await session.runCheckMesh()
        streams.at(-1)!.onmessage?.({ data: JSON.stringify({
          done: true, returncode: 0, checkMeshOk, failedChecks: 0,
        }) })
        expect(session.isMeshReady()).toBe(false)
        await settle()
        // Even an older/accepted scan cannot override this current quality job.
        expect(session.isMeshReady()).toBe(false)
      }
    },
  )

  it.each([
    { returncode: 1, checkMeshOk: true, failedChecks: 0 },
    { returncode: 0, checkMeshOk: true, failedChecks: 2 },
    { returncode: 0, checkMeshOk: true, failedChecks: 0, cancelled: true },
    { returncode: 0, checkMeshOk: true, failedChecks: 0, cancelRequested: true },
  ])('keeps a failed or aborted check-only job locked: %j', async (final) => {
    expect(session.isMeshReady()).toBe(true)
    await session.runCheckMesh()
    expect(session.isMeshReady()).toBe(false)
    streams[0]!.onmessage?.({ data: JSON.stringify({ done: true, ...final }) })
    expect(session.isMeshReady()).toBe(false)
    await settle()
    expect(session.isMeshReady()).toBe(false)
  })

  it.each([true, false])(
    'preserves the prior quality verdict %s through a solve job with no quality metadata',
    async (accepted) => {
      const results = await import('../src/run/sensorResults')
      vi.spyOn(results, 'onSolveFinished').mockResolvedValue(null)
      fetcher.mockImplementationOnce(async () => response({
        meshExists: true, meshReady: false, postprocessReady: true,
        logRuns: [{ path: '/A' }], liquidRuns: [],
      }))
      await session.scanCaseStatus()
      await session.runCheckMesh()
      fetcher.mockRejectedValueOnce(new Error('scan unavailable'))
      streams[0]!.onmessage?.({ data: JSON.stringify({
        done: true, returncode: 0, checkMeshOk: accepted, failedChecks: accepted ? 0 : 2,
      }) })
      await settle()
      expect(session.isMeshReady()).toBe(accepted)
      await session.runJob('solve')
      expect(session.getCurrentCaseJob()?.kind).toBe('solve')
      expect(session.isMeshReady()).toBe(accepted)
      fetcher.mockRejectedValueOnce(new Error('scan unavailable'))
      streams.at(-1)!.onmessage?.({ data: JSON.stringify({ done: true, returncode: 0 }) })
      expect(session.isMeshReady()).toBe(accepted)
      await settle()
      expect(session.isMeshReady()).toBe(accepted)
    },
  )

  it.each(['mesh', 'checkMesh'] as const)(
    'does not invalidate a new case scan when an old %s POST is accepted late',
    async (kind) => {
      let finishPost!: (value: Response) => void
      const previous = fetcher.getMockImplementation()!
      fetcher.mockImplementation(async (url: string, options?: RequestInit) => {
        if (url === (kind === 'mesh' ? '/api/run-mesh' : '/api/run-check-mesh')) {
          return new Promise(resolve => { finishPost = resolve })
        }
        if (String(url).split('?')[0] === '/api/scan-case-status' && backendPath === '/B') return response({
          meshReady: true, postprocessReady: true, logRuns: [{ path: '/B' }], liquidRuns: [],
        })
        return previous(url, options)
      })
      const starting = kind === 'mesh' ? session.runJob('mesh') : session.runCheckMesh()
      await vi.waitFor(() => expect(finishPost).toBeTypeOf('function'))
      store.loadProject(project('/B'), null)
      await ready('/B')
      expect(session.isMeshReady()).toBe(true)
      let finishScan!: (value: Response) => void
      fetcher.mockImplementationOnce(() => new Promise(resolve => { finishScan = resolve }))
      const newCaseScan = session.scanCaseStatus()
      finishPost(response({ jobId: 'late-job-A' }))
      await starting
      expect(session.getCurrentCaseJob()).toBeNull()
      expect(session.isMeshReady()).toBe(true)
      finishScan(response({
        meshReady: false, postprocessReady: true, logRuns: [{ path: '/B' }], liquidRuns: [],
      }))
      await newCaseScan
      expect(session.isMeshReady()).toBe(false)
    },
  )

  it('does not let a rejected check unlock a previously ready mesh', async () => {
    expect(session.isMeshReady()).toBe(true)
    await session.runCheckMesh()
    streams[0]!.onmessage?.({ data: JSON.stringify({
      done: true, returncode: 0, checkMeshOk: false, failedChecks: 2,
    }) })
    expect(session.isMeshReady()).toBe(false)
  })

  it('revokes cached acceptance when a fresh disk scan rejects changed mesh bytes', async () => {
    await session.runCheckMesh()
    streams[0]!.finish()
    expect(session.isMeshReady()).toBe(true)
    fetcher.mockImplementationOnce(async () => response({
      meshExists: true, meshReady: false, solveReady: false,
      postprocessReady: false, logRuns: [], liquidRuns: [],
    }))
    await session.scanCaseStatus()
    expect(session.isMeshReady()).toBe(false)
  })

  it('keeps a zero-exit mesh job visibly failed when final checkMesh rejects its quality', async () => {
    const quality = await import('../src/run/meshWarnings')
    quality.installMeshWarningFeed()
    await session.runJob('mesh')
    streams[0]!.onmessage?.({ data: JSON.stringify({
      done: true, returncode: 0, checkMeshOk: false, failedChecks: 3,
      meshWarnings: [{ level: 2, code: 'high_skewness', message: '4 highly skew faces' }],
    }) })
    expect(session.getJob()).toMatchObject({
      kind: 'mesh', running: false, returncode: 0, checkMeshOk: false, failedChecks: 3,
    })
    expect(session.isMeshReady()).toBe(false)
    expect(quality.blockingMeshWarnings().map(w => w.code)).toEqual(['high_skewness'])
  })

  it('loads the latest mesh quality when the solver panel opens after meshing', async () => {
    await session.runJob('mesh')
    streams[0]!.onmessage?.({ data: JSON.stringify({
      done: true, returncode: 0, checkMeshOk: false, failedChecks: 1,
      meshWarnings: [{ level: 1, code: 'small_determinant', message: '6 weak cells' }],
    }) })
    const quality = await import('../src/run/meshWarnings')
    quality.installMeshWarningFeed()
    expect(quality.blockingMeshWarnings().map(w => w.code)).toEqual(['small_determinant'])
  })

  it('does not load the previous project mesh verdict when the solver panel opens in a new project', async () => {
    await session.runJob('mesh')
    streams[0]!.onmessage?.({ data: JSON.stringify({
      done: true, returncode: 0, checkMeshOk: false, failedChecks: 1,
      meshWarnings: [{ level: 1, code: 'small_determinant', message: 'old case' }],
    }) })
    store.loadProject(project('/B'), null)
    await ready('/B')
    const quality = await import('../src/run/meshWarnings')
    quality.installMeshWarningFeed()
    expect(quality.blockingMeshWarnings()).toEqual([])
    expect(session.getCurrentCaseJob()).toBeNull()
  })

  it('ignores a late checkMesh warning stream from the previous project', async () => {
    const quality = await import('../src/run/meshWarnings')
    quality.installMeshWarningFeed()
    await session.runCheckMesh()
    expect(streams.length).toBeGreaterThan(0)
    paths.beginCasePathChange()
    for (const stream of streams) stream.onmessage?.({ data: JSON.stringify({
      done: true, returncode: 0, checkMeshOk: false, failedChecks: 1,
      meshWarnings: [{ level: 2, code: 'high_skewness', message: 'old case' }],
    }) })
    expect(quality.blockingMeshWarnings()).toEqual([])
  })

  it('does not let an old disk scan unlock solving after a new mesh job fails', async () => {
    expect(session.isMeshReady()).toBe(true)
    await session.runJob('mesh')
    expect(session.isMeshReady()).toBe(false)
    streams[0]!.onmessage?.({ data: JSON.stringify({ done: true, returncode: 1 }) })
    expect(session.isMeshReady()).toBe(false)
  })

  it('keeps explicit checkMesh warnings when its stream drops and polling completes', async () => {
    const quality = await import('../src/run/meshWarnings')
    quality.installMeshWarningFeed()
    const previousFetch = fetcher.getMockImplementation()!
    fetcher.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.startsWith('/api/job-status')) return response({
        done: true, returncode: 0, checkMeshOk: false, failedChecks: 1,
        meshWarnings: [{ level: 2, code: 'high_skewness', message: 'final check' }],
      })
      return previousFetch(url, options)
    })
    await session.runCheckMesh()
    vi.useFakeTimers()
    for (const stream of streams) stream.onerror?.()
    await vi.advanceTimersByTimeAsync(5000)
    expect(session.getJob()).toMatchObject({ kind: 'checkMesh', running: false })
    expect(quality.blockingMeshWarnings().map(w => w.code)).toEqual(['high_skewness'])
  })

  it('does not feed old solve log lines into the new project monitor', async () => {
    const stream = await import('../src/run/jobStream')
    const seen: string[] = []
    stream.watchJobStream()
    stream.addJobStreamHandler({ onLine: (_kind, line) => seen.push(line) })
    await session.runJob('solve')
    expect(streams).toHaveLength(2)
    paths.beginCasePathChange()
    streams[0]!.onmessage?.({ data: JSON.stringify('old solver residual') })
    expect(seen).toEqual([])
  })
})
