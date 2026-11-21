import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readProjectFileText } from '../src/io/projectFiles'
import * as store from '../src/projectStore'
import type { CfdProject } from '../src/schema/cfdproj'
import type { SavedLiquidRun } from '../src/liquid/liquidSavedRuns'
import { projectSha256, resetHybridSourceMemory, writeHybridSource } from '../src/liquid/hybridWorkflow'
import { getLocale, setLocale, t } from '../src/i18n'

const listing = vi.hoisted(() => ({ runs: [] as unknown[], fail: false }))
vi.mock('../src/liquid/liquidSavedRuns', () => ({
  listSavedLiquidRuns: async () => {
    if (listing.fail) throw new Error('Request failed (404)')
    return { case: 'Shinaide_Liquid', runs: listing.runs }
  },
}))
import { hybridHeatForRun } from '../src/run/runSubmit'

type Bag = Record<string, any>
const root = resolve(process.cwd(), '../../..')
function load(path: string): CfdProject {
  const result = readProjectFileText(readFileSync(resolve(root, path), 'utf8'))
  if (!result.ok) throw new Error(result.error)
  store.loadProject(result.project, null)
  return store.getProject()!
}
function run(id: string, sha: string | null): SavedLiquidRun {
  return { name: id, run_id: id, kind: 'dae', location: 'Liquid/log', modified: 0, status: 'completed', error: null,
    solver: 'differentiable_newton_dae', analysis: null, samples: { accepted: 11, total: 11 }, dt_s: 2, accepted_time_s: 20,
    config_hash: null, project_revision: null, source_project: sha ? { sha256: sha } : null, execution: null,
    restorable: true, restore_blocker: null, loaded: null }
}

beforeEach(() => { resetHybridSourceMemory(); localStorage.clear(); listing.runs = []; listing.fail = false })

describe('hybridHeatForRun', () => {
  it('needs no liquid run for a mesh, nor for a project without hybrid servers', async () => {
    expect(await hybridHeatForRun('mesh', load('case/Shinaide_Liquid/Shinaide_liquid.cfdproj'))).toBeNull()
    expect(await hybridHeatForRun('solve', load('case/LiquidSingle_Control/LiquidSingle_Control.cfdproj'))).toBeNull()
  })

  it('takes the newest completed run of this revision after a reload, with no session in the page', async () => {
    const project = load('case/Shinaide_Liquid/Shinaide_liquid.cfdproj'), sha = projectSha256(project)!
    listing.runs = [run('run-other', 'f'.repeat(64)), run('run-new', sha), run('run-old', sha)]
    const heat = await hybridHeatForRun('solve', project)
    expect(heat).toMatchObject({ liquidRunId: 'run-new', sampleIndex: 10 })
    expect((heat!.project as Bag).uid).toBe(project.uid)
  })

  it('keeps the run chosen in the workflow', async () => {
    const project = load('case/Shinaide_Liquid/Shinaide_liquid.cfdproj'), sha = projectSha256(project)!
    listing.runs = [run('run-new', sha), run('run-old', sha)]
    writeHybridSource(project.uid, { runId: 'run-old', sampleIndex: 4, timeS: 8, projectSha256: sha })
    expect(await hybridHeatForRun('solve', project)).toMatchObject({ liquidRunId: 'run-old', sampleIndex: 4 })
  })

  it('says what to do, in the UI language, when there is nothing to hand over', async () => {
    const previous = getLocale(); setLocale('zh-CN')
    try {
      const project = load('case/Shinaide_Liquid/Shinaide_liquid.cfdproj')
      await expect(hybridHeatForRun('solve', project)).rejects.toThrow(t('lq_ux.err_no_liquid_run'))
      const bare = structuredClone(project) as Bag
      delete bare.runDefaults.hybridHeatBudget
      await expect(hybridHeatForRun('solve', bare as CfdProject)).rejects.toThrow(t('lq_ux.err_budget_missing'))
      expect(t('lq_ux.err_budget_missing')).toMatch(/[一-鿿]/)
    } finally { setLocale(previous) }
  })

  it('falls back to the explicit choice when the server cannot list runs', async () => {
    const project = load('case/Shinaide_Liquid/Shinaide_liquid.cfdproj')
    listing.fail = true
    await expect(hybridHeatForRun('solve', project)).rejects.toThrow(t('lq_ux.err_no_liquid_run'))
    writeHybridSource(project.uid, { runId: 'run-x', sampleIndex: 2, timeS: null, projectSha256: null })
    expect(await hybridHeatForRun('solve', project)).toMatchObject({ liquidRunId: 'run-x', sampleIndex: 2 })
  })
})
