import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as solverConfig from '../src/run/solverConfig'
import { t } from '../src/i18n'

/**
 * Both solve buttons go through the same gesture.
 *
 * The solver tab's 求解 and the transient tab's ▶ 执行瞬态求解 are ONE function
 * in the legacy — `runTransientSolve = function() { window.runSolveOnly(); }`
 * (STL_generate.html:26661) — so the transient run inherits the mesh-quality
 * confirmation, the live monitor window and the probe poller.
 *
 * React's transient button called `session.runJob('solve')` directly. It
 * skipped both, which put the brakes on the wrong run: a transient solve is the
 * longest kind there is, and it was the one launched past a level-1 checkMesh
 * warning with no confirmation and no live curve to watch it diverge on.
 */

const runJob = vi.fn(async () => {})
const openSolverMonitor = vi.fn()
const shouldOpenMonitor = vi.fn(() => true)
let blocking: unknown[] = []
let allWarnings: unknown[] = []
let rejectedQuality = false

vi.mock('../src/run/session', () => ({
  runJob: (...args: unknown[]) => runJob(...(args as [])),
  getJob: () => null,
  getCurrentCaseJob: () => null,
  isValidated: () => true,
  isMeshReady: () => true,
  subscribeSession: () => () => {},
}))

vi.mock('../src/run/meshWarnings', () => ({
  blockingMeshWarnings: () => blocking,
  hasRejectedMeshQuality: () => rejectedQuality,
  getMeshWarnings: () => allWarnings,
  subscribeMeshWarnings: () => () => {},
  installMeshWarningFeed: () => {},
}))

vi.mock('../src/run/solverMonitor', () => ({
  openSolverMonitor: (...args: unknown[]) => openSolverMonitor(...(args as [])),
  shouldOpenMonitor: (...args: unknown[]) => shouldOpenMonitor(...(args as [])) as boolean,
}))

// The real store, with only the probe list replaced: a hand-written mock of
// the whole module misses exports other modules call at load time, and the
// failure arrives as "no tests", not as a missing export.
vi.mock('../src/projectStore', async () => ({
  ...(await vi.importActual<typeof import('../src/projectStore')>('../src/projectStore')),
  sensorProbes: () => [
    { uid: 's1', meta: { name: 'sensor_1', enabled: true } },
    { uid: 's2', meta: { name: 'sensor_2', enabled: false } },
  ],
}))

const { useSolveLauncher, submitSolve } = await import('../src/run/useSolveLauncher')

let host: HTMLDivElement
let root: Root
let api: ReturnType<typeof useSolveLauncher>

function Probe() {
  api = useSolveLauncher()
  return null
}

const settle = async () => {
  for (let i = 0; i < 3; i++) await new Promise<void>((r) => setTimeout(r, 0))
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ meshReady: true, decomposed: false, nPartitions: 0 }),
  }))
  runJob.mockClear()
  openSolverMonitor.mockClear()
  shouldOpenMonitor.mockClear()
  shouldOpenMonitor.mockReturnValue(true)
  blocking = []
  rejectedQuality = false
  allWarnings = []
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  flushSync(() => root.render(<Probe />))
})

afterEach(() => {
  flushSync(() => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useSolveLauncher', () => {
  // Mesh quality is reported, never required (owner 2026-10-22), and it is read
  // on the mesh tab only (owner 2026-11-10): no quality dialog before a solve.
  it('solves a rejected or flagged mesh straight away, with no quality dialog', async () => {
    rejectedQuality = true
    blocking = [{ level: 1, key: 'nonOrthogonality' }]
    allWarnings = blocking
    flushSync(() => api.run())
    await settle()
    expect(runJob).toHaveBeenCalledWith('solve')
    expect(openSolverMonitor).toHaveBeenCalledWith(['sensor_1'])
  })

  it('central submission solves a rejected mesh too', async () => {
    rejectedQuality = true
    await submitSolve()
    expect(runJob).toHaveBeenCalledWith('solve')
  })

  it('submits straight away when checkMesh raised nothing', async () => {
    flushSync(() => api.run())
    await settle()
    expect(runJob).toHaveBeenCalledWith('solve')
  })

  it('opens the monitor with the ENABLED probes only, before submitting', async () => {
    flushSync(() => api.run())
    await settle()
    // A disabled probe is not in the case; asking OpenFOAM to watch it would be
    // a function object on a point nothing writes.
    expect(openSolverMonitor).toHaveBeenCalledWith(['sensor_1'])
  })

  it('leaves the monitor closed when the run does not warrant one', async () => {
    shouldOpenMonitor.mockReturnValue(false)
    flushSync(() => api.run())
    await settle()
    expect(openSolverMonitor).not.toHaveBeenCalled()
    expect(runJob).toHaveBeenCalledWith('solve')
  })

  it('reports a failed submit instead of swallowing it', async () => {
    runJob.mockRejectedValueOnce(new Error('no case dir'))
    flushSync(() => api.run())
    await settle()
    flushSync(() => {})
    expect(api.error).toBe('no case dir')
  })

  it('waits for the partition choice before opening the monitor or submitting', async () => {
    vi.spyOn(solverConfig, 'solverConfigOf').mockReturnValue({
      ...solverConfig.defaultSolverConfig(), parallelSolve: true, solveProcess: 4,
    })
    vi.mocked(fetch).mockResolvedValue({
      ok: true, json: async () => ({ meshReady: true, decomposed: true, nPartitions: 8 }),
    } as Response)
    flushSync(() => api.run())
    await settle()
    expect(document.getElementById('solve-partition-warning-dialog')).toBeTruthy()
    expect(runJob).not.toHaveBeenCalled()
    expect(openSolverMonitor).not.toHaveBeenCalled()
    document.getElementById('solve-partition-cancel-btn')!.click()
    await settle()
    expect(runJob).not.toHaveBeenCalled()
    expect(openSolverMonitor).not.toHaveBeenCalled()
    expect(api.preparing).toBe(false)
    expect(api.error).toBeNull()
  })

  it('continues once even if both solve launchers are invoked during confirmation', async () => {
    vi.spyOn(solverConfig, 'solverConfigOf').mockReturnValue({
      ...solverConfig.defaultSolverConfig(), parallelSolve: true, solveProcess: 4,
    })
    vi.mocked(fetch).mockResolvedValue({
      ok: true, json: async () => ({ meshReady: true, decomposed: true, nPartitions: 8 }),
    } as Response)
    const first = submitSolve()
    const second = submitSolve()
    await settle()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(runJob).not.toHaveBeenCalled()
    document.getElementById('solve-partition-continue-btn')!.click()
    await Promise.all([first, second])
    expect(runJob).toHaveBeenCalledTimes(1)
    expect(openSolverMonitor).toHaveBeenCalledTimes(1)
  })

  it('does not launch or open the monitor when the fresh probe fails', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error('offline'))
    flushSync(() => api.run())
    await settle()
    expect(api.error).toBe(t('error.solve_partition_check_failed'))
    expect(runJob).not.toHaveBeenCalled()
    expect(openSolverMonitor).not.toHaveBeenCalled()
  })
})
