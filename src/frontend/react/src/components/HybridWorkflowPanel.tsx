import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'
import * as runtime from '../liquid/liquidRuntimeClient'
import { adoptSavedServerPowerForHybrid, hasHybridFanServer } from '../liquid/hybridFanHeatBudget'
import { ensureHybridData, hybridData, refreshHybridData, subscribeHybridData } from '../liquid/hybridData'
import {
  hybridServerErrorHint, hybridWorkflow, lastSample, readHybridSource, sourceFromRun, subscribeHybridSource, writeHybridSource,
  type HybridStepId, type HybridStepStatus,
} from '../liquid/hybridWorkflow'
import type { HybridHeatLedgerResponse, SavedLiquidRun } from '../liquid/liquidSavedRuns'
import type { TranslationKey } from '../i18n'
import { useTranslation } from '../useTranslation'

/**
 * The air–liquid hybrid workflow, step by step.
 *
 * Shown from the top bar's 风液混合 badge and inside both solver panels.
 * Everything it shows is read back from the server (saved runs, heat ledger)
 * and the case status, so a reload or a server restart picks the workflow up
 * where it was; the only browser-held value is which run was adopted.
 */
export type HybridTarget = 'liquid-solver' | 'air-mesh' | 'air-solver'

const STEP_NAME: Record<HybridStepId, TranslationKey> = {
  liquid: 'lq_ux.hybrid_name_liquid', adopt: 'lq_ux.hybrid_name_adopt', mesh: 'lq_ux.hybrid_name_mesh',
  solve: 'lq_ux.hybrid_name_solve', ledger: 'lq_ux.hybrid_name_ledger',
}
const STATUS_NAME: Record<HybridStepStatus, TranslationKey> = {
  done: 'lq_ux.hybrid_status_done', todo: 'lq_ux.hybrid_status_todo', blocked: 'lq_ux.hybrid_status_blocked', stale: 'lq_ux.hybrid_status_stale',
}
const STATUS_COLOR: Record<HybridStepStatus, string> = { done: '#7fd18b', todo: '#61afef', blocked: '#808590', stale: '#e5c07b' }

/** The workflow state from live sources, re-derived when any of them changes. */
export function useHybridWorkflow(project: CfdProject | null) {
  const data = useSyncExternalStore(subscribeHybridData, hybridData)
  const stored = useSyncExternalStore(subscribeHybridSource, () => readHybridSource(project?.uid))
  const runtimeState = useSyncExternalStore(runtime.subscribeRuntime, runtime.runtimeState)
  const hybrid = !!project && hasHybridFanServer(project)
  // A liquid run finishing in this page is a new candidate: list again.
  const completedRun = runtimeState.session?.status === 'completed' ? runtimeState.session.run_id : null
  useEffect(() => { if (hybrid && completedRun) void refreshHybridData() }, [hybrid, completedRun])
  useEffect(() => { if (hybrid) ensureHybridData() }, [hybrid, project?.uid])
  const meshReady = data.meshReady
  const state = useMemo(() => hybridWorkflow({ project, runs: data.runs, source: stored, meshReady, ledger: data.ledger }),
    [project, data.runs, data.ledger, stored, meshReady])
  return { state, data, stored }
}

function timeText(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? '—' : `${Number(value.toPrecision(6))}`
}
function shortId(id: string | null | undefined): string {
  return String(id ?? '').replace(/^run-/, '').slice(0, 8)
}

export function HybridWorkflowPanel({ project, onNavigate, onClose, compact = false, collapsible = false, id = 'hybrid-workflow' }: {
  project: CfdProject | null
  onNavigate?: (target: HybridTarget) => void
  onClose?: () => void
  compact?: boolean
  /** Folded to its header and next step, for a panel whose own work comes first. */
  collapsible?: boolean
  id?: string
}) {
  const { t } = useTranslation()
  const { state, data } = useHybridWorkflow(project)
  const [pickRun, setPickRun] = useState('')
  const [pickSample, setPickSample] = useState<number | null>(null)
  const [showLedger, setShowLedger] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const runtimeBusy = useSyncExternalStore(runtime.subscribeRuntime, runtime.runtimeState).busy
  const candidates = state.candidates
  // The pickers start at the adopted run and sample, else the newest run's last.
  const chosenRun: SavedLiquidRun | undefined = candidates.find(r => r.run_id === (pickRun || state.source?.runId)) ?? candidates[0]
  const chosenLast = chosenRun ? lastSample(chosenRun).sampleIndex : 0
  const adoptedHere = state.source && chosenRun?.run_id === state.source.runId ? state.source.sampleIndex : null
  const sample = pickSample !== null && pickSample <= chosenLast ? pickSample : adoptedHere ?? chosenLast
  const newest = candidates[0]

  function declareBudget() {
    if (!project) return
    setError(null)
    try {
      const budget = adoptSavedServerPowerForHybrid(project)
      store.edit('adoptHybridBudget', t('lq_ux.hybrid_declare_budget'), draft => {
        draft.runDefaults ??= {}
        draft.runDefaults.hybridHeatBudget = budget
        draft.runDefaults.liquid = { ...((draft.runDefaults.liquid as Record<string, unknown>) ?? {}), enabled: true, inheritResults: true }
      })
      setNotice(t('lq_ux.hybrid_budget_declared'))
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  function adopt(run: SavedLiquidRun | undefined, index?: number) {
    if (!run || !project?.uid) return
    setError(null); setNotice(null)
    writeHybridSource(project.uid, sourceFromRun(run, index))
  }

  if (!state.hybrid) return null
  const next = state.steps.find(step => step.status !== 'done')
  const body = (
    <section id={id} className={`hybrid-workflow${compact ? ' compact' : ''}`} aria-label={t('lq_ux.hybrid_title')}
      data-completed={state.completed} style={{ fontSize: 12 }}>
      <div className="hybrid-workflow-header" style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: collapsible ? 'flex-end' : 'space-between' }}>
        {/* Folded, the <summary> already carries the title and the count. */}
        {!collapsible && <strong style={{ color: '#56b6c2' }}>{t('lq_ux.hybrid_title')} · {state.completed}/5</strong>}
        <span style={{ display: 'flex', gap: 4 }}>
          <button className="btn" type="button" disabled={data.loading} onClick={() => void refreshHybridData()}>{t('lq_ux.hybrid_refresh')}</button>
          {onClose && <button className="btn" type="button" onClick={onClose}>{t('lq_ux.hybrid_close')}</button>}
        </span>
      </div>
      {!compact && <p className="liquid-experiment-note" style={{ margin: '4px 0' }}>{t('lq_ux.hybrid_intro')}</p>}
      <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {state.steps.map((step, index) => (
          <li key={step.id} data-hybrid-step={step.id} data-status={step.status}
            style={{ borderLeft: `3px solid ${STATUS_COLOR[step.status]}`, padding: '4px 6px', margin: '4px 0', background: 'rgba(255,255,255,0.02)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
              <span><span style={{ color: '#9aa0aa' }}>{t('lq_ux.hybrid_step_n', { n: index + 1 })}</span> · {t(STEP_NAME[step.id])}</span>
              <span className="hybrid-step-status" style={{ color: STATUS_COLOR[step.status], whiteSpace: 'nowrap' }}>{t(STATUS_NAME[step.status])}</span>
            </div>
            <div className="hybrid-step-detail" style={{ color: '#b9bec7', margin: '2px 0' }}>{t(step.key, step.vars)}</div>
            <div className="hybrid-step-actions" style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
              {step.id === 'liquid' && <>
                {!state.budgetDeclared && <button className="btn primary" type="button" disabled={!project || runtimeBusy} onClick={declareBudget}>{t('lq_ux.hybrid_declare_budget')}</button>}
                {onNavigate && <button className="btn" type="button" onClick={() => onNavigate('liquid-solver')}>{t('lq_ux.hybrid_go_liquid')}</button>}
              </>}
              {step.id === 'adopt' && state.budgetDeclared && (candidates.length ? <>
                <label style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  <span>{t('lq_ux.hybrid_run_label')}</span>
                  <select aria-label={t('lq_ux.hybrid_run_label')} value={chosenRun?.run_id ?? ''} onChange={e => { setPickRun(e.target.value); setPickSample(null) }}>
                    {candidates.map(run => <option key={run.run_id!} value={run.run_id!}>{shortId(run.run_id)} · {run.samples.accepted ?? 0} · t={timeText(run.accepted_time_s)} s</option>)}
                  </select>
                </label>
                <label style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  <span>{t('lq_ux.hybrid_sample_label')}</span>
                  <select aria-label={t('lq_ux.hybrid_sample_label')} value={sample} onChange={e => setPickSample(Number(e.target.value))}>
                    {Array.from({ length: chosenLast + 1 }, (_, i) => i).reverse().map(i => (
                      <option key={i} value={i}>{t('lq_ux.hybrid_sample_option', { index: i, time: chosenRun?.dt_s !== null && chosenRun?.dt_s !== undefined ? timeText(i * chosenRun.dt_s) : '—' })}</option>
                    ))}
                  </select>
                </label>
                <button className="btn primary" type="button" onClick={() => adopt(chosenRun, sample)}>{t('lq_ux.hybrid_adopt')}</button>
                {state.source && newest && newest.run_id !== state.source.runId && <>
                  <span style={{ color: '#e5c07b' }}>{t('lq_ux.hybrid_newer_available', { id: shortId(newest.run_id) })}</span>
                  <button className="btn" type="button" onClick={() => adopt(newest)}>{t('lq_ux.hybrid_adopt_newest')}</button>
                </>}
                {state.source && <button className="btn" type="button" onClick={() => writeHybridSource(project?.uid, null)}>{t('lq_ux.hybrid_clear_choice')}</button>}
              </> : <span style={{ color: '#808590' }}>{t('lq_ux.hybrid_no_candidates')}</span>)}
              {step.id === 'mesh' && onNavigate && <button className="btn" type="button" onClick={() => onNavigate('air-mesh')}>{t('lq_ux.hybrid_go_mesh')}</button>}
              {step.id === 'solve' && onNavigate && <button className="btn" type="button" onClick={() => onNavigate('air-solver')}>{t('lq_ux.hybrid_go_solve')}</button>}
              {step.id === 'ledger' && data.ledger && <button className="btn" type="button" onClick={() => setShowLedger(v => !v)}>{showLedger ? t('lq_ux.hybrid_hide_ledger') : t('lq_ux.hybrid_show_ledger')}</button>}
            </div>
          </li>
        ))}
      </ol>
      {showLedger && data.ledger && <HybridLedgerView ledger={data.ledger} />}
      {notice && <p role="status" className="liquid-experiment-note">{notice}</p>}
      {data.error && <p role="note" className="liquid-experiment-warning">{t('lq_ux.history_load_error', { error: data.error })}</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </section>
  )
  if (!collapsible) return body
  return (
    <details className="hybrid-workflow-fold" id={`${id}-fold`} data-completed={state.completed}>
      <summary>
        <strong style={{ color: '#56b6c2' }}>{t('lq_ux.hybrid_title')} · {state.completed}/5</strong>
        {next && <span className="hybrid-fold-next">{t('lq_ux.hybrid_fold_next', { n: state.steps.indexOf(next) + 1, name: t(STEP_NAME[next.id]) })}</span>}
      </summary>
      {body}
    </details>
  )
}

function kW(value: number | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? (value / 1000).toFixed(2) + ' kW' : '—'
}

/** The heat handed from the liquid run to the air side, as the server wrote it. */
export function HybridLedgerView({ ledger }: { ledger: HybridHeatLedgerResponse }) {
  const { t } = useTranslation()
  const totals = ledger.ledger.totals
  const servers = (ledger.ledger.servers ?? []) as Record<string, unknown>[]
  return (
    <div id="hybrid-heat-ledger" className="prop-group" style={{ fontSize: 12 }}>
      <div className="prop-title">{t('lq_ux.ledger_title')}</div>
      <p className="liquid-experiment-note">{t('lq_ux.ledger_source', { id: shortId(ledger.ledger.liquid_run_id), sample: ledger.ledger.sample_index ?? '—', time: timeText(ledger.ledger.time_s) })}</p>
      <p className="liquid-experiment-note" style={{ color: ledger.matches_active_cfdrun === false ? '#e5c07b' : '#7fd18b' }}>
        {ledger.matches_active_cfdrun === false ? t('lq_ux.ledger_mismatch') : t('lq_ux.ledger_matches')}</p>
      <dl className="liquid-monitor-readings" style={{ display: 'grid', gridTemplateColumns: 'auto auto', gap: '2px 10px', margin: '4px 0' }}>
        {([['lq_ux.ledger_total_it', totals.total_it_W], ['lq_ux.ledger_cp_input', totals.cp_input_W], ['lq_ux.ledger_liquid', totals.liquid_removed_W],
          ['lq_ux.ledger_storage', totals.solid_storage_rate_W], ['lq_ux.ledger_air', totals.air_residual_W]] as const).map(([key, value]) => (
          <div key={key} style={{ display: 'contents' }}><dt style={{ color: '#9aa0aa' }}>{t(key)}</dt><dd data-ledger={key} style={{ margin: 0, textAlign: 'right' }}>{kW(value)}</dd></div>
        ))}
      </dl>
      {servers.length > 0 && <details><summary>{t('lq_ux.ledger_servers', { count: servers.length })}</summary>
        <table className="liquid-ledger-table" style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
          <thead><tr><th style={{ textAlign: 'left' }}>{t('lq_ux.ledger_col_server')}</th><th>{t('lq_ux.ledger_col_it')}</th><th>{t('lq_ux.ledger_col_liquid')}</th><th>{t('lq_ux.ledger_col_air')}</th></tr></thead>
          <tbody>{servers.map(row => (
            <tr key={String(row.server_uid)}>
              <td>{String(row.server_uid)}</td>
              <td style={{ textAlign: 'right' }}>{Number(row.total_it_W).toFixed(0)}</td>
              <td style={{ textAlign: 'right' }}>{Number(row.liquid_removed_W).toFixed(1)}</td>
              <td style={{ textAlign: 'right' }}>{Number(row.air_residual_W).toFixed(1)}</td>
            </tr>
          ))}</tbody>
        </table>
      </details>}
    </div>
  )
}

/**
 * The air solver panel's view of the handoff: which liquid run and sample
 * this solve will take its heat from, chosen from the completed runs of this
 * project revision on disk.
 */
export function HybridAirSourceSection({ project, solveError }: { project: CfdProject | null; solveError?: string | null }) {
  const { t } = useTranslation()
  const { state } = useHybridWorkflow(project)
  const [open, setOpen] = useState(false)
  if (!state.hybrid) return null
  const effective = state.effective
  const hint = hybridHint(solveError)
  return (
    <div className="prop-group" id="hybrid-air-source" style={{ borderLeftColor: '#56b6c2' }}>
      <div className="prop-title" style={{ color: '#56b6c2' }}>{t('lq_ux.air_source_title')}</div>
      <p className="liquid-experiment-note" data-hybrid-source={effective?.runId ?? ''} data-chosen={!!state.source}>
        {state.source
          ? t('lq_ux.air_source_current', { id: shortId(state.source.runId), sample: state.source.sampleIndex, time: timeText(state.source.timeS) })
          : state.sourceProblem
            ? t(state.sourceProblem === 'other_revision' ? 'lq_ux.hybrid_step_adopt_other_revision' : 'lq_ux.hybrid_step_adopt_missing', { id: shortId(readHybridSource(project?.uid)?.runId) })
            : effective ? `${t('lq_ux.air_source_none')} — ${t('lq_ux.air_source_current', { id: shortId(effective.runId), sample: effective.sampleIndex, time: timeText(effective.timeS) })}`
              : t('lq_ux.hybrid_step_liquid_todo')}
      </p>
      {hint && <p className="liquid-experiment-warning" role="note">{t(hint)}</p>}
      <button className="btn" type="button" onClick={() => setOpen(v => !v)}>{open ? t('lq_ux.hybrid_close') : t('lq_ux.air_source_open')}</button>
      {open && <HybridWorkflowPanel project={project} compact id="hybrid-workflow-air" />}
    </div>
  )
}

function hybridHint(message: string | null | undefined): TranslationKey | null {
  return hybridServerErrorHint(message)
}
