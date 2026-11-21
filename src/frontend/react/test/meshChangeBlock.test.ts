import { afterEach, describe, expect, it, vi } from 'vitest'
import type { JobState } from '../src/run/session'

/** A run started before the mesh check answers can meet a mesh that does not fit the edit (the runner then fails
 * with "source mesh receipt identity does not match"): the buttons wait for the verdict, which a mesh job voids. */

let job: Partial<JobState> | null = null
let meshPresent = true
let revision = 1
let validated = true
const sessionListeners = new Set<() => void>()
const storeListeners = new Set<() => void>()
const loadListeners = new Set<() => void>()
vi.mock('../src/run/session', () => ({
  getJob: () => job,
  isMeshPresent: () => meshPresent,
  isValidated: () => validated,
  subscribeSession: (fn: () => void) => { sessionListeners.add(fn); return () => sessionListeners.delete(fn) },
}))
vi.mock('../src/projectStore', () => ({
  getProject: () => ({}),
  documentInputRevision: () => revision,
  subscribe: (fn: () => void) => { storeListeners.add(fn); return () => storeListeners.delete(fn) },
  subscribeProjectLoad: (fn: () => void) => { loadListeners.add(fn); return () => loadListeners.delete(fn) },
}))
vi.mock('../src/run/caseDir', () => ({ readCasePath: () => '/case' }))
vi.mock('../src/run/runSubmit', () => ({ buildStlUpload: async () => ({ cfdrunJson: '{}', stlFiles: {} }) }))
const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ kind: 'none' }) }))
vi.stubGlobal('fetch', fetchMock)

const mc = await import('../src/run/meshChange')
const emitSession = () => sessionListeners.forEach((fn) => fn())

afterEach(() => vi.useRealTimers())

describe('mesh-change block', () => {
  it('holds a run until there is a verdict; a failed check holds nothing', () => {
    expect(mc.meshBlockFor(null, null)).toBe('pending')
    expect(mc.meshBlockFor(null, 'HTTP 500')).toBe(null)
    expect(mc.meshBlockFor({ kind: 'none' }, null)).toBe(null)
    expect(mc.meshBlockFor({ kind: 'patches' }, null)).toBe('mismatch')
    expect(mc.meshBlockFor({ kind: 'geometry', reason: 'settings' }, null)).toBe('mismatch')
  })

  it('answers a click with the block of this moment, and nothing without a mesh', async () => {
    vi.useFakeTimers()
    const stop = mc.watchMeshChange(10)
    await vi.advanceTimersByTimeAsync(20)
    expect(mc.meshBlockNow()).toBe(null)
    revision += 1 // an edit the check has not answered yet
    expect(mc.meshBlockNow()).toBe('pending')
    meshPresent = false
    expect(mc.meshBlockNow()).toBe(null)
    meshPresent = true
    stop()
  })

  it('voids the verdict when a mesh job starts and checks again after any job that outlived an edit', async () => {
    vi.useFakeTimers()
    const stop = mc.watchMeshChange(10)
    await vi.advanceTimersByTimeAsync(20)
    expect(mc.currentMeshChange()?.kind).toBe('none')

    job = { kind: 'mesh', running: true }
    emitSession()
    expect(mc.currentMeshChange()).toBe(null)
    job = { kind: 'mesh', running: false }
    emitSession()
    await vi.advanceTimersByTimeAsync(20)
    expect(mc.currentMeshChange()?.kind).toBe('none')

    job = { kind: 'scano', running: true }
    emitSession()
    revision += 1
    storeListeners.forEach((fn) => fn())
    await vi.advanceTimersByTimeAsync(20) // stands down while the job runs
    expect(mc.currentMeshChange()).toBe(null)
    const calls = fetchMock.mock.calls.length
    job = { kind: 'scano', running: false }
    emitSession()
    await vi.advanceTimersByTimeAsync(20)
    expect(fetchMock.mock.calls.length).toBe(calls + 1)
    expect(mc.currentMeshChange()?.kind).toBe('none')
    stop()
  })

  it('a panel mounting again does not export the scene again for the same edit and case', async () => {
    vi.useFakeTimers()
    revision += 1
    let stop = mc.watchMeshChange(10)
    await vi.advanceTimersByTimeAsync(20)
    expect(mc.currentMeshChange()?.kind).toBe('none')
    stop()
    const calls = fetchMock.mock.calls.length
    stop = mc.watchMeshChange(10)                     // the CFD sub-tab, then the AI sub-tab
    await vi.advanceTimersByTimeAsync(20)
    expect(fetchMock.mock.calls.length).toBe(calls)
    revision += 1                                     // an edit: checked again
    storeListeners.forEach((fn) => fn())
    await vi.advanceTimersByTimeAsync(20)
    expect(fetchMock.mock.calls.length).toBe(calls + 1)
    stop()
  })
})

describe('background mesh-change check (owner 2026-11-11: ready before the solve tab opens)', () => {
  const edit = () => { revision += 1; storeListeners.forEach((fn) => fn()) }
  const repaint = () => storeListeners.forEach((fn) => fn())

  it('checks right after a mesh job finishes, with no panel mounted', async () => {
    vi.useFakeTimers()
    const stop = mc.startBackgroundMeshChange(1000, 10)
    await vi.advanceTimersByTimeAsync(20)
    job = { kind: 'mesh', running: true }
    sessionListeners.forEach((fn) => fn())
    expect(mc.currentMeshChange()).toBe(null)             // the mesh job voids the verdict
    const calls = fetchMock.mock.calls.length
    job = { kind: 'mesh', running: false, returncode: 0 }
    sessionListeners.forEach((fn) => fn())
    await vi.advanceTimersByTimeAsync(20)
    expect(fetchMock.mock.calls.length).toBe(calls + 1)
    expect(mc.currentMeshChange()?.kind).toBe('none')
    stop()
    job = null
  })

  it('waits for editing to pause, ignores repaints, and stands down while a job runs or without a mesh', async () => {
    vi.useFakeTimers()
    const stop = mc.startBackgroundMeshChange(1000, 10)
    await vi.advanceTimersByTimeAsync(20)
    let calls = fetchMock.mock.calls.length
    edit()
    await vi.advanceTimersByTimeAsync(600)
    edit()                                                // still editing: the wait starts again
    await vi.advanceTimersByTimeAsync(600)
    expect(fetchMock.mock.calls.length).toBe(calls)
    repaint()                                             // a log line is not an edit
    await vi.advanceTimersByTimeAsync(500)
    expect(fetchMock.mock.calls.length).toBe(calls + 1)
    expect(mc.currentMeshChange()?.kind).toBe('none')

    calls = fetchMock.mock.calls.length
    job = { kind: 'solve', running: true }
    edit()
    await vi.advanceTimersByTimeAsync(1100)
    expect(fetchMock.mock.calls.length).toBe(calls)
    job = null
    meshPresent = false
    sessionListeners.forEach((fn) => fn())                // the session reports the mesh gone
    edit()
    await vi.advanceTimersByTimeAsync(1100)
    expect(fetchMock.mock.calls.length).toBe(calls)
    meshPresent = true
    sessionListeners.forEach((fn) => fn())                // the case turns out to have a mesh
    await vi.advanceTimersByTimeAsync(20)
    expect(fetchMock.mock.calls.length).toBe(calls + 1)
    stop()
  })

  it('does not retry a failed check for the same edit, and checks a newly opened project', async () => {
    vi.useFakeTimers()
    const stop = mc.startBackgroundMeshChange(1000, 10)
    fetchMock.mockImplementationOnce(async () => ({ ok: false, json: async () => ({ error: 'boom' }) }) as never)
    edit()
    await vi.advanceTimersByTimeAsync(1100)
    expect(mc.getMeshChange().error).toBe('boom')
    const calls = fetchMock.mock.calls.length
    sessionListeners.forEach((fn) => fn())
    await vi.advanceTimersByTimeAsync(1100)
    expect(fetchMock.mock.calls.length).toBe(calls)
    revision += 1
    loadListeners.forEach((fn) => fn())                   // a project opened
    await vi.advanceTimersByTimeAsync(20)
    expect(fetchMock.mock.calls.length).toBe(calls + 1)
    expect(mc.currentMeshChange()?.kind).toBe('none')
    stop()
  })

  it('is installed once', () => {
    const a = mc.startBackgroundMeshChange()
    expect(mc.startBackgroundMeshChange()).toBe(a)
    a()
  })
})
