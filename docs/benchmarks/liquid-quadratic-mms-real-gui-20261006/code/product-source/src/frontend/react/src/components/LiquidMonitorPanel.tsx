import { useEffect, useMemo, useState } from 'react'
import {
  CDU_METRIC_FRAME_KEY,
  CDU_METRIC_LABEL_KEYS,
  CDU_METRICS,
  cpCatalogFromProject,
  getLiquidMonitorSelection,
  isCpMonitored,
  setAllCpMonitored,
  setCduMetric,
  setCpMonitored,
  setLossMonitored,
  subscribeLiquidMonitor,
  type CduMetric,
  type CpCuMonitorEntry,
} from '../liquid/liquidMonitorConfig'
import {
  CP_METRICS,
  cpSeriesPoints,
  type CpMetric,
  epochFrames,
  hasSamples,
  reportedCpIds,
} from '../liquid/liquidMonitorFrames'
import { KELVIN_OFFSET } from '../liquid/liquidResults'
import * as liquid from '../liquid/liquidSession'
import type { LiquidStage } from '../liquid/liquidSession'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { TimeSeriesChart, type Series } from './TimeSeriesChart'

/**
 * The liquid solve monitor, right-hand side.
 *
 * Replicates `#liquid-solver-monitoring-right` / `js/liquid_solve_monitor.js`:
 * a stage badge, the loss convergence curve, the per-cold-plate list with its
 * chip-temperature curve, and the CDU metric toggles.
 *
 * The curves are derived from `liquidSession`'s stored progress frames rather
 * than accumulated into their own state. Every frame is already kept so the
 * panel can be opened MID-SOLVE and still draw the history — a monitor that
 * only plots what arrived after it mounted shows a converged solve as a single
 * point, which is exactly when the operator wants to see the whole curve.
 *
 * The cold-plate checkboxes are not a display filter. They decide `monitor_cps`
 * on the solve request, and the solver reports NOTHING about a plate that was
 * not asked for (`backend/liquidcooling/runner.py:26`) — so an unchecked plate
 * has no curve to hide. They live in `liquidMonitorConfig` because the solve is
 * started from the LEFT panel, which cannot read checkboxes that a tab switch
 * has unmounted.
 */

const STAGE_LABEL: Record<LiquidStage, string> = {
  compiling: 'lsm.stage_compiling',
  validated: 'lsm.stage_compiled',
  compiled: 'lsm.stage_compiled',
  model_built: 'lsm.stage_model_built',
  torch_compiled: 'lsm.stage_model_built',
  solving: 'lsm.stage_solving',
  done: 'lsm.stage_done',
  error: 'lsm.stage_error',
}

const LOSS_COLORS: Record<string, string> = {
  total: '#e06c75',
  mass: '#61afef',
  momentum: '#98c379',
  energy: '#e5c07b',
  bc: '#c678dd',
  ic: '#56b6c2',
}

const FALLBACK_COLORS = ['#ff6b9d', '#4ecdc4', '#f7dc6f', '#d19a66', '#56b6c2']

const CDU_COLORS: Record<CduMetric, string> = {
  return_P: '#ff6b9d',
  supply_P: '#4ecdc4',
  total_flow: '#f7dc6f',
}

export function LiquidMonitorPanel() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [hidden, setHidden] = useState<Record<string, boolean>>({})
  useEffect(() => liquid.subscribeLiquid(() => bump((n) => n + 1)), [])
  useEffect(() => subscribeLiquidMonitor(() => bump((n) => n + 1)), [])
  // The cold-plate list is read out of the PROJECT, so it has to repaint when
  // a pair is added or a rack renamed — the edge ids are built from the names.
  useEffect(() => store.subscribe(() => bump((n) => n + 1)), [])

  const run = liquid.liquidRunState()
  const selection = getLiquidMonitorSelection()
  const project = store.getProject()
  const frames = useMemo(() => epochFrames(run.progress), [run.progress])
  const catalog = useMemo(() => cpCatalogFromProject(project), [project])

  const epochs = frames.map((f) => f.epoch)
  const lossKeys = useMemo(() => {
    const keys = new Set<string>()
    for (const f of frames) {
      if (f.total != null) keys.add('total')
      for (const k of Object.keys(f.losses)) keys.add(k)
    }
    return [...keys].sort()
  }, [frames])

  const cpIds = useMemo(() => reportedCpIds(frames), [frames])

  const labelOf = useMemo(() => {
    const byId = new Map(catalog.map((cp: CpCuMonitorEntry) => [cp.id, cp.label]))
    return (id: string) => byId.get(id) ?? (id.length > 18 ? `…${id.slice(-16)}` : id)
  }, [catalog])

  // Losses span many decades, so the curve is plotted as log10. On a linear
  // axis a solve that falls from 1e2 to 1e-16 is a vertical drop followed by a
  // flat line at zero — which is the shape of both a converged run and a
  // stalled one.
  const lossSeries: Series[] = lossKeys
    .filter((k) => !hidden[k])
    .map((k, i) => ({
      label: k,
      color: LOSS_COLORS[k] ?? (FALLBACK_COLORS[i % FALLBACK_COLORS.length] as string),
      points: frames.map((f) => {
        const v = k === 'total' ? f.total : f.losses[k]
        return typeof v === 'number' && v > 0 ? Math.log10(v) : NaN
      }),
    }))

  // One series per plate PER METRIC, named as the legacy names them
  // (`<id> T_chip` / `<id> T_out` / `<id> m`). The two temperatures share a
  // chart because they share °C; the mass flow gets its own, since 0.05 kg/s on
  // a 30-70 °C axis is the axis.
  const cpSeriesFor = (metric: CpMetric): Series[] => {
    const suffix = CP_METRICS.find((m) => m.key === metric)?.suffix ?? metric
    const slot = CP_METRICS.findIndex((m) => m.key === metric)
    const toCelsius = metric === 'flow' ? undefined : (v: number) => v - KELVIN_OFFSET
    return cpIds
      .map((id, i) => ({
        label: `${labelOf(id)} ${suffix}`,
        color: FALLBACK_COLORS[
          (i * CP_METRICS.length + slot) % FALLBACK_COLORS.length
        ] as string,
        points: cpSeriesPoints(frames, id, metric, toCelsius),
      }))
      .filter((s) => hasSamples(s.points))
  }

  // Reported and drawn are two different lists: a hidden series keeps its
  // legend entry, or there would be nothing left to click to bring it back.
  const cpTempReported: Series[] = [...cpSeriesFor('chip'), ...cpSeriesFor('waterOut')]
  const cpFlowReported: Series[] = cpSeriesFor('flow')
  const cpTempSeries = cpTempReported.filter((s) => !hidden[s.label])
  const cpFlowSeries = cpFlowReported.filter((s) => !hidden[s.label])

  const hasCdu = frames.some((f) => Object.keys(f.cdu).length > 0)
  const cduSeries = (metrics: CduMetric[], scale: number): Series[] =>
    metrics
      .filter((m) => selection.cduMetrics.includes(m))
      .map((m) => ({
        label: t(CDU_METRIC_LABEL_KEYS[m]),
        color: CDU_COLORS[m],
        points: frames.map((f) => {
          const v = f.cdu[CDU_METRIC_FRAME_KEY[m]]
          return typeof v === 'number' ? v * scale : NaN
        }),
      }))
      .filter((s) => s.points.some((v) => Number.isFinite(v)))

  // Pa -> kPa, and pressure kept off the flow chart: one shared y-range over
  // 1.7e5 Pa and 0.3 kg/s draws the flow as a flat line on the axis.
  const pressureSeries = cduSeries(['return_P', 'supply_P'], 1e-3)
  const flowSeries = cduSeries(['total_flow'], 1)

  return (
    <div className="content" id="liquid-solver-monitoring-right">
      <div className="header" style={{ color: '#00b4dc' }}>
        {t('lsm.header')}
      </div>

      <div className="prop-group" id="lsm-stage" style={{ borderLeftColor: '#00b4dc' }}>
        <div className="prop-row">
          <label>{t('lsm.stage')}</label>
          <span id="lsm-stage-badge" style={{ color: run.error ? '#e06c75' : 'var(--highlight)' }}>
            {run.error
              ? `${t('lsm.stage_error')}: ${run.error}`
              : run.stage
                ? t(STAGE_LABEL[run.stage] as never)
                : run.aborted
                  ? t('status.aborted')
                  : '—'}
          </span>
        </div>
        <div className="prop-row">
          <label>{t('lsm.epochs')}</label>
          <span id="lsm-epoch-count">{frames.length ? epochs[epochs.length - 1] : '—'}</span>
        </div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#00b4dc' }}>
        <div className="prop-row">
          <label htmlFor="lsm-loss-cb">{t('lsm.monitor_loss')}</label>
          <input
            type="checkbox"
            id="lsm-loss-cb"
            checked={selection.loss}
            onChange={(e) => setLossMonitored(e.target.checked)}
          />
        </div>
        {selection.loss &&
          (lossKeys.length > 0 ? (
            <>
              {lossSeries.length > 0 && (
                <TimeSeriesChart
                  id="lsm-loss-canvas"
                  time={epochs}
                  series={lossSeries}
                  xLabel="epoch"
                  yLabel="log10"
                />
              )}
              <Toggles keys={lossKeys} hidden={hidden} setHidden={setHidden} />
            </>
          ) : (
            <div className="prop-hint">{t('lsm.cp_list_placeholder')}</div>
          ))}
      </div>

      <div className="prop-group" id="lsm-cp-group" style={{ borderLeftColor: '#00b4dc' }}>
        <div className="sm-section-head">
          <label>{t('lsm.cp_cu_monitor')}</label>
          <div className="sm-section-btns">
            <button id="lsm-cp-all" onClick={() => setAllCpMonitored(catalog, true)}>
              {t('common.select_all')}
            </button>
            <button id="lsm-cp-none" onClick={() => setAllCpMonitored(catalog, false)}>
              {t('common.deselect_all')}
            </button>
          </div>
        </div>
        <div id="lsm-cp-list">
          {catalog.length === 0 ? (
            <div className="sm-empty">{t('lsm.cp_list_placeholder')}</div>
          ) : (
            catalog.map((cp) => (
              <div className="sm-row" key={cp.id}>
                <label>
                  <input
                    type="checkbox"
                    className="lsm-cp-cb"
                    data-cp-id={cp.id}
                    data-label={cp.label}
                    checked={isCpMonitored(selection, cp.id)}
                    onChange={(e) => setCpMonitored(cp.id, e.target.checked)}
                  />{' '}
                  {cp.label} ({cp.qW}W)
                </label>
              </div>
            ))
          )}
        </div>
        {cpTempReported.length > 0 && (
          <>
            {cpTempSeries.length > 0 && (
              <TimeSeriesChart
                id="lsm-cp-canvas"
                time={epochs}
                series={cpTempSeries}
                xLabel="epoch"
                yLabel="°C"
              />
            )}
            <Toggles keys={cpTempReported.map((s) => s.label)} hidden={hidden} setHidden={setHidden} />
          </>
        )}
        {cpFlowReported.length > 0 && (
          <>
            {cpFlowSeries.length > 0 && (
              <TimeSeriesChart
                id="lsm-cp-flow-canvas"
                time={epochs}
                series={cpFlowSeries}
                xLabel="epoch"
                yLabel="kg/s"
              />
            )}
            <Toggles keys={cpFlowReported.map((s) => s.label)} hidden={hidden} setHidden={setHidden} />
          </>
        )}
      </div>

      <div className="prop-group" id="lsm-cdu-group" style={{ borderLeftColor: '#00b4dc' }}>
        <div className="sm-section-head">
          <label>{t('lsm.cdu_monitor')}</label>
        </div>
        {CDU_METRICS.map((metric) => (
          <div className="sm-row" key={metric}>
            <label>
              <input
                type="checkbox"
                className="lsm-cdu-cb"
                data-metric={metric}
                checked={selection.cduMetrics.includes(metric)}
                onChange={(e) => setCduMetric(metric, e.target.checked)}
              />{' '}
              {t(CDU_METRIC_LABEL_KEYS[metric])}
            </label>
          </div>
        ))}
        {!hasCdu ? (
          <div className="sm-empty">{t('lsm.cp_list_placeholder')}</div>
        ) : (
          <>
            {pressureSeries.length > 0 && (
              <TimeSeriesChart
                id="lsm-cdu-p-canvas"
                time={epochs}
                series={pressureSeries}
                xLabel="epoch"
                yLabel="kPa"
              />
            )}
            {flowSeries.length > 0 && (
              <TimeSeriesChart
                id="lsm-cdu-flow-canvas"
                time={epochs}
                series={flowSeries}
                xLabel="epoch"
                yLabel="kg/s"
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Toggles({
  keys,
  hidden,
  setHidden,
}: {
  keys: string[]
  hidden: Record<string, boolean>
  setHidden: (fn: (h: Record<string, boolean>) => Record<string, boolean>) => void
}) {
  return (
    <div className="lsm-toggles">
      {keys.map((k) => (
        <label key={k} style={{ fontSize: 10, color: '#ccc', display: 'block' }}>
          <input
            type="checkbox"
            className="lsm-series-cb"
            data-series={k}
            checked={!hidden[k]}
            onChange={(e) =>
              setHidden((h) => ({ ...h, [k]: !e.target.checked }))
            }
          />{' '}
          {k}
        </label>
      ))}
    </div>
  )
}
