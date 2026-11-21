import { describe, expect, it, vi } from 'vitest'
import type { JobState } from '../src/run/session'
let context = 1
let job: JobState | null = null
const listeners = new Set<() => void>()
vi.mock('../src/run/session', () => ({
  getJob: () => job,
  subscribeSession: (fn: () => void) => { listeners.add(fn); return () => listeners.delete(fn) },
}))
vi.mock('../src/run/caseDir', () => ({
  isCasePathContextCurrent: (value: number) => value === context,
}))
const { installMeshWarningFeed, hasRejectedMeshQuality, getMeshWarnings } = await import('../src/run/meshWarnings')
const emit = () => listeners.forEach(fn => fn())
const result = (over: Partial<JobState> = {}): JobState => ({
  id: 'quality1', kind: 'checkMesh', caseContext: 1, running: false, aborted: false,
  returncode: 1, checkMeshOk: false, failedChecks: 4, lines: [], error: null,
  meshWarnings: [{ level: 1, code: 'bad_volume', message: 'invalid mesh' }], ...over,
})
describe('completed mesh quality belongs to the checked case', () => {
  it('retains rejection across solve jobs, expires on case change, and clears only after a new successful check', () => {
    installMeshWarningFeed()
    job = result()
    emit()
    expect(hasRejectedMeshQuality()).toBe(true)
    expect(getMeshWarnings()).toHaveLength(1)
    job = result({ id: 'solve1', kind: 'solve', failedChecks: null, checkMeshOk: null })
    emit()
    expect(hasRejectedMeshQuality()).toBe(true)

    context = 2
    emit()
    expect(hasRejectedMeshQuality()).toBe(false)
    expect(getMeshWarnings()).toEqual([])
    job = result({ id: 'quality2', caseContext: 2, meshWarnings: null })
    emit()
    expect(hasRejectedMeshQuality()).toBe(true) // no parser detail cannot bypass a failure
    job = result({ id: 'quality3', caseContext: 2, returncode: 0, checkMeshOk: true, failedChecks: 0, meshWarnings: [] })
    emit()
    expect(hasRejectedMeshQuality()).toBe(false)
    expect(getMeshWarnings()).toEqual([])
  })
})


it('captures a backend solve preflight rejection and does not confuse physics failures with quality', () => {
  installMeshWarningFeed()
  context = 3
  job = result({ id: 'backend-quality-refusal', kind: 'solve', caseContext: 3 })
  emit()
  expect(hasRejectedMeshQuality()).toBe(true)
  expect(getMeshWarnings()).toHaveLength(1)
  context = 4
  job = result({ id: 'physics-failure', kind: 'solve', caseContext: 4, checkMeshOk: null, failedChecks: null, meshWarnings: [] })
  emit()
  expect(hasRejectedMeshQuality()).toBe(false)
  expect(getMeshWarnings()).toEqual([])
})
