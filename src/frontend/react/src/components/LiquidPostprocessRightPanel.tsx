import { useEffect, useState } from 'react'
import { Icon } from './Icon'
import {
  knownLiquidRuns,
  liquidRunOptions,
  selectedLiquidRun,
  setSelectedLiquidRun,
} from '../liquid/liquidPostprocess'
import { fetchLiquidResults, liquidRunState, subscribeLiquid } from '../liquid/liquidSession'
import { isTransient, parseLiquidResults } from '../liquid/liquidResults'
import { selectedResultSample, subscribeResultSample } from '../liquid/liquidTempColor'
import { useFocusedLiquidEdge } from '../liquid/liquidPostprocessFocus'
import { useAppSelection } from '../selectionBus'
import * as session from '../run/session'
import { useTranslation } from '../useTranslation'
import { LiquidObjectDetails } from './LiquidObjectDetails'

/**
 * The right column on the post-processing tab, in liquid mode.
 *
 * Ports `#liquid-postprocess-right` (STL_generate.html:1125): a solve-record
 * selector and a refresh button. The air tab
 * shows its patch list here; in liquid mode there is no field to shade, so the
 * question this column answers is a different one — WHICH solve is being read.
 *
 * That question was unanswerable before. The only results the port could show
 * were whatever `liquidSession` last fetched, so an earlier solve sitting in
 * `case/<name>/Liquid/log/<timestamp>/liquid_result.json` could not be opened
 * without reloading the page — and a liquid solve is where the interesting
 * comparison is, between the run before a change and the run after it.
 *
 * Refresh re-scans the case directory rather than re-fetching the current
 * result: a solve that finished in another tab adds a DIRECTORY, and a re-fetch
 * of the same `?run=` would keep reporting that it is not there.
 *
 * Below the selector: every value of the selected object (the app selection,
 * narrowed by the network row picked on the left) at the shown sample, read
 * from the same saved result, with its time curves one click away.
 */
export function LiquidPostprocessRightPanel() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const selection = useAppSelection()
  const focusedEdge = useFocusedLiquidEdge()
  // The selection can also change from the heat ledger ("show this run").
  const run = selectedLiquidRun()

  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])
  useEffect(() => subscribeLiquid(() => bump((n) => n + 1)), [])
  useEffect(() => subscribeResultSample(() => bump((n) => n + 1)), [])

  // The list is a snapshot of the last case scan, so opening the tab refreshes
  // it: the solve that produced the run may have finished after the last scan.
  useEffect(() => {
    void session.scanCaseStatus()
  }, [])

  const raw = liquidRunState().results
  const parsed = parseLiquidResults(raw)
  const hasResults = !!parsed
  const sampleCount = parsed?.time_s.length ?? 0
  const sample = selectedResultSample()
  const sampleIndex = sample < 0 ? sampleCount - 1 : Math.min(sample, sampleCount - 1)
  const options = liquidRunOptions(
    knownLiquidRuns(), hasResults, t('label.no_solve_records'), t('label.latest_result'),
  )

  async function refresh() {
    setBusy(true)
    setStatus(null)
    await session.scanCaseStatus()
    setBusy(false)
    bump((n) => n + 1)
  }

  async function choose(value: string) {
    // Keep the displayed record paired with the last valid results while the
    // requested record is being read. Reject malformed documents before the
    // shared session publishes them, so a failed read cannot disturb playback.
    setBusy(true)
    setStatus(null)
    const raw = await fetchLiquidResults(value || undefined, (data) => !!parseLiquidResults(data))
    if (parseLiquidResults(raw)) {
      setSelectedLiquidRun(value)
      setStatus(null)
    } else {
      setStatus(t('lsm.result_read_failed'))
    }
    setBusy(false)
  }

  return (
    <div
      id="liquid-postprocess-right"
      style={{ display: 'flex', flexDirection: 'column', overflowY: 'auto', padding: 0 }}
    >
      <div className="header" style={{ color: '#00b4dc' }}>
        {t('lsm.pp_header')}
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#00b4dc', margin: 8 }}>
        <div className="prop-title" style={{ color: '#00b4dc' }}>
          {t('title.result_selection')}
        </div>
        <label htmlFor="lsm-pp-run-select" style={{ display: 'block', marginBottom: 6, fontSize: 12 }}>
          {t('label.solve_records')}
        </label>
        <div className="prop-row">
          <select
            id="lsm-pp-run-select"
            style={{ flex: 1, width: '100%', minWidth: 0 }}
            title={options.find((o) => o.value === run)?.value || t('label.latest_result')}
            disabled={busy}
            value={options.some((o) => o.value === run) ? run : (options[0]?.value ?? '')}
            onChange={(e) => void choose(e.target.value)}
          >
            {options.map((o) => (
              <option key={o.value || '(latest)'} value={o.value} title={o.value || o.label}>
                {o.label}
              </option>
            ))}
          </select>
          <button
            className="btn"
            id="lsm-pp-refresh"
            title={t('common.refresh')}
            disabled={busy}
            style={{ padding: '2px 8px', width: 'auto', margin: 0, flexShrink: 0 }}
            onClick={() => void refresh()}
          >
            <Icon name="refresh" />
          </button>
        </div>
      </div>

      {status && (
        <div id="lsm-pp-status" role="alert" style={{ padding: 8, fontSize: 12, color: 'var(--text-dim)' }}>
          {status}
        </div>
      )}

      {parsed && (
        <LiquidObjectDetails
          raw={raw}
          sample={sampleIndex}
          transient={isTransient(parsed)}
          selection={selection}
          focusedEdge={focusedEdge}
        />
      )}
    </div>
  )
}
