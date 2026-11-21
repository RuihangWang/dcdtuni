/**
 * The air–liquid hybrid workflow as data: which liquid run the air side takes
 * its heat from, and how far the five steps have got.
 *
 *   1 liquid solve          a completed DAE run of THIS project revision
 *   2 adopt as air budget   one run and one accepted sample chosen for the air side
 *   3 air mesh              the case has a mesh
 *   4 air solve             the case's heat ledger names the chosen run and matches
 *                           the active .cfdrun
 *   5 heat ledger           model/heat-ledger.json can be read
 *
 * The chosen run lives in this browser (localStorage, keyed by project uid),
 * never in the project: writing it into the .cfdproj would change the project
 * content hash that the liquid run itself is bound to, and the server would
 * then refuse the very run that was just chosen. A choice that is lost (a
 * private window, cleared storage) is recovered from disk: the newest
 * completed run of the current project revision is offered again, and the
 * server restores a run it no longer holds in memory (run_manager.restore).
 */
import type { CfdProject } from '../schema/cfdproj'
import type { TranslationKey } from '../i18n'
import { cfdrunExport } from '../legacy/cfdrunBuild'
import { hasHybridFanServer } from './hybridFanHeatBudget'
import type { HybridHeatLedgerResponse, SavedLiquidRun } from './liquidSavedRuns'

export interface HybridSource {
  runId: string
  /** Accepted sample of that run whose heat the air side takes. */
  sampleIndex: number
  /** Simulated time of that sample, for display. */
  timeS: number | null
  /** Content hash of the project revision the run was compiled from. */
  projectSha256: string | null
}

const STORAGE_PREFIX = 'dcdtuni.hybridSource.'
const listeners = new Set<() => void>()
/** In-memory copy, so a page without storage still keeps its choice. */
const memory = new Map<string, HybridSource | null>()

export function subscribeHybridSource(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function valid(value: unknown): value is HybridSource {
  const v = value as Partial<HybridSource> | null
  return !!v && typeof v.runId === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(v.runId) &&
    Number.isSafeInteger(v.sampleIndex) && (v.sampleIndex as number) >= 0
}

export function readHybridSource(projectUid: string | null | undefined): HybridSource | null {
  if (!projectUid) return null
  if (memory.has(projectUid)) return memory.get(projectUid) ?? null
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + projectUid)
    const parsed = raw ? JSON.parse(raw) : null
    const value = valid(parsed) ? parsed : null
    memory.set(projectUid, value)
    return value
  } catch {
    return null
  }
}

export function writeHybridSource(projectUid: string | null | undefined, source: HybridSource | null): void {
  if (!projectUid) return
  if (source !== null && !valid(source)) throw new Error('Invalid hybrid liquid source')
  memory.set(projectUid, source)
  try {
    if (source) localStorage.setItem(STORAGE_PREFIX + projectUid, JSON.stringify(source))
    else localStorage.removeItem(STORAGE_PREFIX + projectUid)
  } catch { /* storage blocked: the in-memory choice still holds for this page */ }
  listeners.forEach(f => f())
}

/** Test seam: forget every in-memory choice. */
export function resetHybridSourceMemory(): void {
  memory.clear()
}

/** The project content hash a liquid run records as its source (cfdrun_export). */
export function projectSha256(project: CfdProject | null): string | null {
  if (!project) return null
  try { return cfdrunExport().projectContentHashSync(project) } catch { return null }
}

/** Completed DAE runs of exactly this project revision, newest first. */
export function candidateRuns(runs: SavedLiquidRun[], sha: string | null): SavedLiquidRun[] {
  if (!sha) return []
  return runs.filter(run => run.kind === 'dae' && run.status === 'completed' && !!run.run_id &&
    run.source_project?.sha256 === sha && (run.restorable || run.loaded !== null))
}

/** The last accepted sample of a run, and its time. */
export function lastSample(run: SavedLiquidRun): { sampleIndex: number; timeS: number | null } {
  const accepted = run.samples.accepted ?? run.samples.total ?? 1
  return { sampleIndex: Math.max(0, accepted - 1), timeS: run.accepted_time_s }
}

export function sourceFromRun(run: SavedLiquidRun, sampleIndex?: number): HybridSource {
  const last = lastSample(run)
  const index = sampleIndex ?? last.sampleIndex
  const timeS = index === last.sampleIndex ? last.timeS : run.dt_s !== null ? index * run.dt_s : null
  return { runId: run.run_id!, sampleIndex: index, timeS, projectSha256: run.source_project?.sha256 ?? null }
}

export type HybridStepId = 'liquid' | 'adopt' | 'mesh' | 'solve' | 'ledger'
/** done: finished · todo: can be done now · blocked: an earlier step is missing · stale: done for another revision/run */
export type HybridStepStatus = 'done' | 'todo' | 'blocked' | 'stale'

export interface HybridStep {
  id: HybridStepId
  status: HybridStepStatus
  key: TranslationKey
  vars?: Record<string, string | number>
}

export interface HybridWorkflowInput {
  project: CfdProject | null
  runs: SavedLiquidRun[] | null
  source: HybridSource | null
  meshReady: boolean
  ledger: HybridHeatLedgerResponse | null
}

export interface HybridWorkflowState {
  hybrid: boolean
  budgetDeclared: boolean
  sha: string | null
  candidates: SavedLiquidRun[]
  /** The chosen source, when it is still a completed run of this revision. */
  source: HybridSource | null
  /**
   * What an air solve would hand over now: the choice, else (with no choice
   * at all) the last sample of the newest completed run of this revision —
   * resolveHybridSubmission's rule. Null when a stale choice blocks it.
   */
  effective: HybridSource | null
  /** Why a stored choice no longer applies. */
  sourceProblem: 'other_revision' | 'missing' | null
  steps: HybridStep[]
  completed: number
}

function shortId(id: string): string {
  return id.replace(/^run-/, '').slice(0, 8)
}

function timeText(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? '—' : `${Number(value.toPrecision(6))}`
}

export function hybridWorkflow(input: HybridWorkflowInput): HybridWorkflowState {
  const project = input.project
  const hybrid = !!project && hasHybridFanServer(project)
  const budget = (project as { runDefaults?: { hybridHeatBudget?: { version?: number }; liquid?: { inheritResults?: boolean } } } | null)?.runDefaults
  const budgetDeclared = budget?.hybridHeatBudget?.version === 2 && budget?.liquid?.inheritResults === true
  const sha = projectSha256(project)
  const candidates = candidateRuns(input.runs ?? [], sha)
  const stored = input.source
  let source: HybridSource | null = null
  let sourceProblem: HybridWorkflowState['sourceProblem'] = null
  if (stored) {
    const run = candidates.find(r => r.run_id === stored.runId)
    if (run && stored.sampleIndex <= lastSample(run).sampleIndex) source = stored
    else if (input.runs !== null) sourceProblem = (input.runs ?? []).some(r => r.run_id === stored.runId) ? 'other_revision' : 'missing'
    else source = stored // the listing is not in yet: keep showing the choice
  }
  const newest = candidates[0]
  const effective = source ?? (!stored && budgetDeclared && newest ? sourceFromRun(newest) : null)
  const steps: HybridStep[] = []
  steps.push(!budgetDeclared ? { id: 'liquid', status: 'todo', key: 'lq_ux.hybrid_step_liquid_budget' }
    : newest ? { id: 'liquid', status: 'done', key: 'lq_ux.hybrid_step_liquid_done',
      vars: { id: shortId(newest.run_id!), samples: newest.samples.accepted ?? 0, time: timeText(newest.accepted_time_s) } }
      : { id: 'liquid', status: 'todo', key: 'lq_ux.hybrid_step_liquid_todo' })
  steps.push(source ? { id: 'adopt', status: 'done', key: 'lq_ux.hybrid_step_adopt_done',
    vars: { id: shortId(source.runId), sample: source.sampleIndex, time: timeText(source.timeS) } }
    : sourceProblem ? { id: 'adopt', status: 'stale', key: sourceProblem === 'other_revision' ? 'lq_ux.hybrid_step_adopt_other_revision' : 'lq_ux.hybrid_step_adopt_missing',
      vars: { id: shortId(stored!.runId) } }
      : effective ? { id: 'adopt', status: 'todo', key: 'lq_ux.hybrid_step_adopt_default',
        vars: { id: shortId(effective.runId), sample: effective.sampleIndex, time: timeText(effective.timeS) } }
        : { id: 'adopt', status: 'blocked', key: 'lq_ux.hybrid_step_adopt_todo' })
  steps.push({ id: 'mesh', status: input.meshReady ? 'done' : 'todo', key: input.meshReady ? 'lq_ux.hybrid_step_mesh_done' : 'lq_ux.hybrid_step_mesh_todo' })
  const ledger = input.ledger
  const ledgerRun = ledger?.ledger.liquid_run_id ?? null
  const ledgerMatches = !!ledger && ledger.matches_active_cfdrun !== false
  steps.push(ledger && ledgerMatches && effective && ledgerRun === effective.runId
    ? { id: 'solve', status: 'done', key: 'lq_ux.hybrid_step_solve_done',
      vars: { id: shortId(ledgerRun), sample: ledger.ledger.sample_index ?? '—', time: timeText(ledger.ledger.time_s) } }
    : ledger && ledgerMatches && ledgerRun
      ? { id: 'solve', status: 'stale', key: 'lq_ux.hybrid_step_solve_other_run', vars: { id: shortId(ledgerRun) } }
      : { id: 'solve', status: effective && input.meshReady ? 'todo' : 'blocked', key: 'lq_ux.hybrid_step_solve_todo' })
  // The ledger is only current when step 4 is: one written for another run
  // still reads, but as the old answer, not as a finished step.
  const solveDone = steps[steps.length - 1]?.status === 'done'
  const ledgerVars = ledger ? { id: shortId(ledgerRun ?? ''), residual: ((ledger.ledger.totals?.air_residual_W ?? NaN) / 1000).toFixed(2), liquid: ((ledger.ledger.totals?.liquid_removed_W ?? NaN) / 1000).toFixed(2) } : undefined
  steps.push(!ledger ? { id: 'ledger', status: 'blocked', key: 'lq_ux.hybrid_step_ledger_todo' }
    : !ledgerMatches ? { id: 'ledger', status: 'stale', key: 'lq_ux.hybrid_step_ledger_stale', vars: ledgerVars }
      : solveDone ? { id: 'ledger', status: 'done', key: 'lq_ux.hybrid_step_ledger_done', vars: ledgerVars }
        : { id: 'ledger', status: 'stale', key: 'lq_ux.hybrid_step_ledger_other_run', vars: ledgerVars })
  return { hybrid, budgetDeclared, sha, candidates, source, effective, sourceProblem, steps, completed: steps.filter(s => s.status === 'done').length }
}

/**
 * Which liquid run an air solve hands over, decided at submission.
 *
 * The stored choice when it is still a completed run of this revision;
 * otherwise the newest such run on disk. Throws a sentence the user can act
 * on when there is none.
 */
export function resolveHybridSubmission(project: CfdProject, runs: SavedLiquidRun[] | null, stored: HybridSource | null,
  translate: (key: TranslationKey, vars?: Record<string, string | number>) => string): HybridSource {
  const state = hybridWorkflow({ project, runs, source: stored, meshReady: true, ledger: null })
  if (!state.budgetDeclared) throw new Error(translate('lq_ux.err_budget_missing'))
  if (state.effective) return state.effective
  if (state.sourceProblem === 'other_revision') throw new Error(translate('lq_ux.err_source_other_revision', { id: stored!.runId }))
  // A choice whose run is gone falls back to the newest run, as no choice does.
  const newest = state.candidates[0]
  if (newest) return sourceFromRun(newest)
  throw new Error(translate('lq_ux.err_no_liquid_run'))
}

/**
 * A server-side hybrid refusal, with what to do about it.
 *
 * The handoff's own reasons stay in English (they name fields and UIDs); this
 * puts the action first.
 */
export function hybridServerErrorHint(message: string | null | undefined): TranslationKey | null {
  if (!message) return null
  if (/sha256 does not match|contentHash does not match|this \.cfdproj revision|content hash/i.test(message)) return 'lq_ux.err_hint_revision'
  if (/completed owned DAE run|not loaded|no saved run/i.test(message)) return 'lq_ux.err_hint_rerun'
  if (/IT budget|IT heat|IT power|heat budget|hybridHeatBudget|hybrid budget/i.test(message)) return 'lq_ux.err_hint_budget'
  if (/hybrid|liquid run|accepted liquid sample|fan hybrid/i.test(message)) return 'lq_ux.err_hint_generic'
  return null
}
