import { useCallback, useEffect, useMemo, useState } from 'react'
import type { CfdProject } from '../schema/cfdproj'
import { hasHybridFanServer } from '../liquid/hybridFanHeatBudget'
import { fetchHybridHeatLedger, type HybridHeatLedgerResponse } from '../liquid/liquidSavedRuns'
import { useLiquidResultDocument } from '../liquid/liquidPostprocessFocus'
import { fetchLiquidResults } from '../liquid/liquidSession'
import { setSelectedLiquidRun } from '../liquid/liquidPostprocess'
import { fmtNumber, fmtWatts } from '../liquid/liquidDisplayFormat'
import { useTranslation } from '../useTranslation'
import './LiquidPostprocess.css'

/**
 * The liquid-to-air heat handoff of a hybrid project, read from
 * `model/heat-ledger.json` through `GET /api/hybrid/heat-ledger`.
 *
 * Of each server's total IT power the cold plates take their share into the
 * liquid; what the liquid run actually removed (and stored in the solids)
 * leaves the rest to the air side as fan-enthalpy heat. The ledger names the
 * accepted liquid sample it was taken from and whether it still pairs with the
 * active `.cfdrun`; both are shown, so a stale ledger cannot pass for the
 * current one.
 */

export function isHybridProject(project: CfdProject | null): boolean {
  if (!project) return false
  if (project.runDefaults?.hybridHeatBudget) return true
  try {
    return hasHybridFanServer(project)
  } catch {
    return false
  }
}

type LedgerState =
  | { status: 'loading' }
  | { status: 'missing' }
  | { status: 'error'; message: string }
  | { status: 'ready'; response: HybridHeatLedgerResponse }

type ServerRow = { uid: string; it: number | null; cp: number | null; liquid: number | null; storage: number | null; air: number | null; closure: number | null }

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

export function HybridHeatLedgerPanel({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation()
  const [state, setState] = useState<LedgerState>({ status: 'loading' })
  const doc = useLiquidResultDocument()
  const load = useCallback(async () => {
    setState({ status: 'loading' })
    try {
      const response = await fetchHybridHeatLedger()
      setState(response ? { status: 'ready', response } : { status: 'missing' })
    } catch (error) {
      setState({ status: 'error', message: error instanceof Error ? error.message : String(error) })
    }
  }, [])
  useEffect(() => { void load() }, [load])

  const servers = useMemo<ServerRow[]>(() => {
    if (state.status !== 'ready') return []
    return (state.response.ledger.servers ?? []).map((s) => ({
      uid: typeof s.server_uid === 'string' ? s.server_uid : '?',
      it: num(s.total_it_W), cp: num(s.cp_input_W), liquid: num(s.liquid_removed_W),
      storage: num(s.solid_storage_rate_W), air: num(s.air_residual_W), closure: num(s.closure_residual_W),
    }))
  }, [state])
  const bindings = (doc as { object_bindings?: Record<string, { label?: string }> } | null)?.object_bindings ?? {}
  const label = (uid: string) => bindings[uid]?.label ?? uid

  const header = (
    <div className="lq-pp-section-title">
      <span>{t('lq_pp.hybrid_title')}</span>
      <button type="button" className="lr-link" onClick={() => void load()}>{t('lq_pp.refresh')}</button>
    </div>
  )
  if (state.status === 'loading') return <div className="lq-pp-section" id="lq-pp-hybrid">{header}<p className="lq-pp-note">{t('lq_pp.hybrid_loading')}</p></div>
  if (state.status === 'missing') return <div className="lq-pp-section" id="lq-pp-hybrid">{header}<p className="lq-pp-note" data-ledger-missing>{t('lq_pp.hybrid_missing')}</p></div>
  if (state.status === 'error') return <div className="lq-pp-section" id="lq-pp-hybrid">{header}<p className="lq-pp-warn" role="status">{t('lq_pp.hybrid_error', { error: state.message })}</p></div>

  const { response } = state
  const ledger = response.ledger
  const totals = ledger.totals
  const runId = typeof ledger.liquid_run_id === 'string' ? ledger.liquid_run_id : '—'
  const shownRun = (doc as { run_id?: unknown } | null)?.run_id
  const pairing = response.matches_active_cfdrun === true ? { cls: 'lq-pp-ok', text: t('lq_pp.hybrid_matches') }
    : response.matches_active_cfdrun === false ? { cls: 'lq-pp-bad', text: t('lq_pp.hybrid_mismatch') }
      : { cls: '', text: t('lq_pp.hybrid_no_cfdrun') }
  return (
    <div className="lq-pp-section" id="lq-pp-hybrid">
      {header}
      <dl className="lq-pp-cards">
        <Card id="total-it" label={t('lq_pp.hybrid_total_it')} value={fmtWatts(totals.total_it_W)} />
        <Card id="cp-input" label={t('lq_pp.hybrid_cp_input')} value={fmtWatts(totals.cp_input_W)} />
        <Card id="liquid-removed" label={t('lq_pp.hybrid_liquid_removed')} value={fmtWatts(totals.liquid_removed_W)} />
        <Card id="storage" label={t('lq_pp.hybrid_storage')} value={fmtWatts(totals.solid_storage_rate_W)} />
        <Card id="air-residual" wide label={t('lq_pp.hybrid_air_residual')} value={fmtWatts(totals.air_residual_W)} />
      </dl>
      <p className="lq-pp-note" data-ledger-source>
        {t('lq_pp.hybrid_source', {
          run: runId, sample: ledger.sample_index ?? '—',
          time: typeof ledger.time_s === 'number' ? ledger.time_s.toFixed(1) : '—',
          multiplier: fmtNumber(ledger.heat_multiplier, 3),
        })}
      </p>
      <p className="lq-pp-note">
        <span className={ledger.identity_verified === true ? 'lq-pp-ok' : 'lq-pp-bad'} data-ledger-identity>
          {ledger.identity_verified === true ? t('lq_pp.hybrid_identity') : t('lq_pp.hybrid_identity_unverified')}
        </span>
        {' · '}<span className={pairing.cls} data-ledger-pairing>{pairing.text}</span>
        {typeof ledger.air_derived_run_id === 'string' && <> · {t('lq_pp.hybrid_air_run', { run: ledger.air_derived_run_id })}</>}
      </p>
      {runId !== '—' && shownRun !== runId && (
        <button type="button" className="btn" data-ledger-open-run onClick={async () => {
          const name = `run-${runId}`
          if (await fetchLiquidResults(name)) setSelectedLiquidRun(name)
        }}>{t('lq_pp.hybrid_open_liquid')}</button>
      )}
      {!compact && servers.length > 0 && <>
        <div className="lq-pp-section-title">{t('lq_pp.hybrid_servers', { count: servers.length })}</div>
        <div className="lq-pp-table-wrap">
          <table className="lq-pp-ledger-table" data-ledger-servers>
            <thead><tr>
              <th>{t('lq_pp.hybrid_col_server')}</th><th>{t('lq_pp.hybrid_col_it')}</th>
              <th>{t('lq_pp.hybrid_col_liquid')}</th><th>{t('lq_pp.hybrid_col_air')}</th>
            </tr></thead>
            <tbody>
              {servers.map((s) => (
                <tr key={s.uid} data-server-uid={s.uid}
                  title={`${s.uid}\n${t('lq_pp.hybrid_col_cp')}: ${fmtNumber(s.cp, 3)} · ${t('lq_pp.hybrid_storage')}: ${fmtNumber(s.storage, 3)} W`}>
                  <td>{label(s.uid)}</td>
                  <td>{fmtNumber(s.it, 1)}</td>
                  <td>{fmtNumber(s.liquid, 3)}</td>
                  <td>{fmtNumber(s.air, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>}
    </div>
  )
}

function Card({ id, label, value, wide }: { id: string; label: string; value: string; wide?: boolean }) {
  return (
    <div className={`lq-pp-card${wide ? ' wide' : ''}`}>
      <dt>{label}</dt>
      <dd data-ledger-value={id}>{value}</dd>
    </div>
  )
}

/**
 * Air field or liquid network, for a hybrid project's post-processing.
 *
 * The two are different result documents in different scenes (an OpenFOAM
 * field against a solved pipe network), so this switches the cooling mode
 * the post-processing tab reads — the same switch as the top bar, placed where
 * the results are. In the air view the heat ledger stays one click away.
 */
export function HybridPostprocessSwitch({ project, mode, onMode }: {
  project: CfdProject | null
  mode: 'air' | 'liquid'
  onMode: (mode: 'air' | 'liquid') => void
}) {
  const { t } = useTranslation()
  const [ledgerOpen, setLedgerOpen] = useState(false)
  if (!isHybridProject(project)) return null
  return (
    <>
      <div className="lq-pp-switch" role="group" aria-label={t('lq_pp.switch_aria')} id="lq-pp-hybrid-switch">
        <button type="button" aria-pressed={mode === 'air'} onClick={() => onMode('air')}>{t('lq_pp.switch_air')}</button>
        <button type="button" aria-pressed={mode === 'liquid'} onClick={() => onMode('liquid')}>{t('lq_pp.switch_liquid')}</button>
      </div>
      {mode === 'air' && (
        <details className="prop-group" style={{ borderLeftColor: '#00b4dc' }} open={ledgerOpen}
          onToggle={(e) => setLedgerOpen((e.target as HTMLDetailsElement).open)}>
          <summary className="lr-cdu-toggle">{t('lq_pp.hybrid_title')}</summary>
          {ledgerOpen && <HybridHeatLedgerPanel compact />}
        </details>
      )}
    </>
  )
}
