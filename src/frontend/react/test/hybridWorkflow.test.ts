import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'
import { readProjectFileText } from '../src/io/projectFiles'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'
import type { HybridHeatLedgerResponse, SavedLiquidRun } from '../src/liquid/liquidSavedRuns'
import {
  candidateRuns, hybridServerErrorHint, hybridWorkflow, projectSha256, readHybridSource,
  resetHybridSourceMemory, resolveHybridSubmission, sourceFromRun, writeHybridSource,
} from '../src/liquid/hybridWorkflow'
import { t } from '../src/i18n'

type Bag = Record<string, any>
const root = resolve(process.cwd(), '../../..')
function shinaide(): CfdProject {
  const result = readProjectFileText(readFileSync(resolve(root, 'case/Shinaide_Liquid/Shinaide_liquid.cfdproj'), 'utf8'))
  if (!result.ok) throw new Error(result.error)
  store.loadProject(result.project, null)
  return store.getProject()!
}
function run(id: string, sha: string | null, patch: Partial<SavedLiquidRun> = {}): SavedLiquidRun {
  return {
    name: `run-${id}`, run_id: id, kind: 'dae', location: 'Liquid/log', modified: 0, status: 'completed', error: null,
    solver: 'differentiable_newton_dae', analysis: { mode: 'transient' }, samples: { accepted: 11, total: 11 }, dt_s: 2,
    accepted_time_s: 20, config_hash: 'c', project_revision: 1, source_project: sha ? { sha256: sha } : null,
    execution: null, restorable: true, restore_blocker: null, loaded: null, ...patch,
  }
}
function ledger(runId: string, matches = true): HybridHeatLedgerResponse {
  return { path: 'model/heat-ledger.json', modified: 0, active_cfdrun: { filename: 'x.cfdrun', run_id: 'air' }, matches_active_cfdrun: matches,
    ledger: { format: 'hybrid-air-heat-handoff/v2', liquid_run_id: runId, sample_index: 10, time_s: 20,
      totals: { total_it_W: 84000, cp_input_W: 21000, liquid_removed_W: 21000, solid_storage_rate_W: 0, air_residual_W: 63000 } } }
}

beforeEach(() => { resetHybridSourceMemory(); localStorage.clear() })

describe('hybrid source choice', () => {
  it('survives a reload through localStorage, keyed by project uid', () => {
    writeHybridSource('proj_A', { runId: 'run-abc', sampleIndex: 3, timeS: 6, projectSha256: 'x' })
    resetHybridSourceMemory()
    expect(readHybridSource('proj_A')).toEqual({ runId: 'run-abc', sampleIndex: 3, timeS: 6, projectSha256: 'x' })
    expect(readHybridSource('proj_B')).toBeNull()
  })
  it('refuses a malformed choice', () => {
    expect(() => writeHybridSource('p', { runId: '../etc', sampleIndex: 0, timeS: null, projectSha256: null })).toThrow()
    localStorage.setItem('dcdtuni.hybridSource.p', '{"runId":"ok","sampleIndex":-1}')
    expect(readHybridSource('p')).toBeNull()
  })
})

describe('hybridWorkflow on the canonical Shinaide project', () => {
  it('recognises the project as hybrid with its budget declared', () => {
    const state = hybridWorkflow({ project: shinaide(), runs: [], source: null, meshReady: false, ledger: null })
    expect(state.hybrid).toBe(true)
    expect(state.budgetDeclared).toBe(true)
    expect(state.steps.map(s => [s.id, s.status])).toEqual([
      ['liquid', 'todo'], ['adopt', 'blocked'], ['mesh', 'todo'], ['solve', 'blocked'], ['ledger', 'blocked']])
  })

  it('offers only completed DAE runs of this exact revision, newest first', () => {
    const project = shinaide(), sha = projectSha256(project)!
    const runs = [run('new', sha), run('old-rev', 'f'.repeat(64)), run('failed', sha, { status: 'failed' }),
      run('legacy', sha, { kind: 'legacy' }), run('blocked', sha, { restorable: false, restore_blocker: 'no receipt' }), run('older', sha)]
    expect(candidateRuns(runs, sha).map(r => r.run_id)).toEqual(['new', 'older'])
  })

  it('walks the five steps as the run, the choice, the mesh and the ledger appear', () => {
    const project = shinaide(), sha = projectSha256(project)!
    const runs = [run('run-new', sha)]
    let state = hybridWorkflow({ project, runs, source: null, meshReady: false, ledger: null })
    expect(state.steps[0]).toMatchObject({ status: 'done', vars: { id: 'new', samples: 11, time: '20' } })
    expect(state.steps[1]!.status).toBe('todo')
    const source = sourceFromRun(runs[0]!)
    expect(source).toEqual({ runId: 'run-new', sampleIndex: 10, timeS: 20, projectSha256: sha })
    state = hybridWorkflow({ project, runs, source, meshReady: true, ledger: null })
    expect(state.steps.map(s => s.status)).toEqual(['done', 'done', 'done', 'todo', 'blocked'])
    state = hybridWorkflow({ project, runs, source, meshReady: true, ledger: ledger('run-new') })
    expect(state.steps.map(s => s.status)).toEqual(['done', 'done', 'done', 'done', 'done'])
    expect(state.completed).toBe(5)
    expect(state.steps[4]!.vars).toEqual({ id: 'new', residual: '63.00', liquid: '21.00' })
  })

  it('without a choice, says the air solve takes the newest run and judges the ledger against it', () => {
    const project = shinaide(), sha = projectSha256(project)!
    const runs = [run('run-new', sha), run('run-older', sha)]
    let state = hybridWorkflow({ project, runs, source: null, meshReady: true, ledger: ledger('run-new') })
    expect(state.source).toBeNull()
    expect(state.effective).toEqual(sourceFromRun(runs[0]!))
    expect(state.steps[1]).toMatchObject({ status: 'todo', key: 'lq_ux.hybrid_step_adopt_default', vars: { id: 'new', sample: 10, time: '20' } })
    // The ledger was written from the run an unchosen air solve takes: step 4 is done, not "another run".
    expect(state.steps.map(s => s.status)).toEqual(['done', 'todo', 'done', 'done', 'done'])
    state = hybridWorkflow({ project, runs, source: null, meshReady: true, ledger: ledger('run-older') })
    expect(state.steps[3]).toMatchObject({ status: 'stale', key: 'lq_ux.hybrid_step_solve_other_run' })
    expect(state.steps[4]).toMatchObject({ status: 'stale', key: 'lq_ux.hybrid_step_ledger_other_run', vars: { id: 'older' } })
  })

  it('marks a choice from another project revision stale instead of using it', () => {
    const project = shinaide(), sha = projectSha256(project)!
    const runs = [run('run-old', 'f'.repeat(64)), run('run-new', sha)]
    const stored = { runId: 'run-old', sampleIndex: 10, timeS: 20, projectSha256: 'f'.repeat(64) }
    const state = hybridWorkflow({ project, runs, source: stored, meshReady: true, ledger: ledger('run-old') })
    expect(state.source).toBeNull()
    expect(state.sourceProblem).toBe('other_revision')
    // A ledger written for another run is the old answer, not a finished step 5.
    expect(state.steps.map(s => s.status)).toEqual(['done', 'stale', 'done', 'stale', 'stale'])
  })

  it('resolves the air submission from the choice, else the newest run, else an actionable error', () => {
    const project = shinaide(), sha = projectSha256(project)!
    const runs = [run('run-new', sha), run('run-older', sha)]
    expect(resolveHybridSubmission(project, runs, { runId: 'run-older', sampleIndex: 4, timeS: 8, projectSha256: sha }, t).runId).toBe('run-older')
    expect(resolveHybridSubmission(project, runs, null, t)).toMatchObject({ runId: 'run-new', sampleIndex: 10 })
    expect(() => resolveHybridSubmission(project, [], null, t)).toThrow(t('lq_ux.err_no_liquid_run'))
    expect(() => resolveHybridSubmission(project, [run('run-x', 'e'.repeat(64))], { runId: 'run-x', sampleIndex: 1, timeS: 2, projectSha256: 'e' }, t))
      .toThrow(t('lq_ux.err_source_other_revision', { id: 'run-x' }))
    const bare = structuredClone(project) as Bag
    delete bare.runDefaults.hybridHeatBudget
    expect(() => resolveHybridSubmission(bare as CfdProject, runs, null, t)).toThrow(t('lq_ux.err_budget_missing'))
  })

  it('refuses a sample beyond what the chosen run accepted', () => {
    const project = shinaide(), sha = projectSha256(project)!
    const state = hybridWorkflow({ project, runs: [run('run-new', sha)], source: { runId: 'run-new', sampleIndex: 11, timeS: 22, projectSha256: sha }, meshReady: false, ledger: null })
    expect(state.source).toBeNull()
  })
})

describe('hybridServerErrorHint', () => {
  it('puts an action on the handoff refusals the server actually sends', () => {
    expect(hybridServerErrorHint('liquid source_project.sha256 does not match this .cfdproj revision')).toBe('lq_ux.err_hint_revision')
    expect(hybridServerErrorHint('hybrid fan air requires a completed owned DAE run')).toBe('lq_ux.err_hint_rerun')
    expect(hybridServerErrorHint('server m_1/e_1 total IT heat is insufficient: negative air residual -5 W')).toBe('lq_ux.err_hint_budget')
    expect(hybridServerErrorHint('sample_index is outside accepted liquid samples')).toBe('lq_ux.err_hint_generic')
    expect(hybridServerErrorHint('mesh failed')).toBeNull()
  })
})
