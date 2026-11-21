import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  chipStatus,
  isTransient,
  liquidResultLifecycle,
  lastVal,
  parseLiquidResults,
  systemOverview,
  toCelsius,
  type ColdPlateDetail,
} from '../liquid/liquidResults'
import { cduOverview, flowLPM, resultFluid, serverResultOutlet } from '../liquid/liquidOverview'
import * as liquid from '../liquid/liquidSession'
import * as color from '../liquid/liquidTempColor'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { TimeSeriesChart, type Series } from './TimeSeriesChart'
import { selectedLiquidRun } from '../liquid/liquidPostprocess'
import { pickLiquidSample } from '../transient/resultPlayback'

/**
 * The liquid-cooling results panel.
 *
 * Replicates `#liquid-results-group` / `js/liquid_results_panel.js`: solver
 * status, CDU overview, system totals, one collapsible section per rack with a
 * server table, and a per-cold-plate transient chart.
 *
 * Steady and transient results share the same series format. Direct steady
 * analysis has one accepted equilibrium sample; a transient has its initial
 * condition and accepted steps. Tables and 3D overlays use the selected sample,
 * defaulting to the final accepted one. Sample count alone never proves that
 * a transient has settled to equilibrium.
 */

export interface LiquidResultsPanelProps {
  /** Cold-plate ids the user picked in the model tree, if any. */
  focusEdgeIds?: string[]
}

export function LiquidResultsPanel({ focusEdgeIds }: LiquidResultsPanelProps) {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [openChart, setOpenChart] = useState<string | null>(null)
  useEffect(() => liquid.subscribeLiquid(() => bump((n) => n + 1)), [])
  // The shown sample is shared: the timeline's playhead picks it too (resultPlayback).
  useEffect(() => color.subscribeResultSample(() => bump((n) => n + 1)), [])
  // The CDU overview is read from the PROJECT, not from the result document, so
  // renaming a CDU or re-assigning its racks has to repaint this panel too.
  useEffect(() => store.subscribe(() => bump((n) => n + 1)), [])

  // Pick up a result left by a previous session, so opening the tab after a
  // reload shows the run that is on disk instead of an empty panel.
  useEffect(() => {
    if (!liquid.liquidRunState().results) void liquid.fetchLiquidResults(selectedLiquidRun() || undefined)
  }, [])

  const run = liquid.liquidRunState()
  const results = parseLiquidResults(run.results)

  if (!results) {
    return (
      <div className="content" id="liquid-results-container">
        <PanelTitle />
        <div className="empty">{t('liquid.no_results')}</div>
        <RefreshButton />
      </div>
    )
  }

  const overview = systemOverview(results)
  const transient = isTransient(results)
  const lifecycle = liquidResultLifecycle(results)
  const lifecycleKeys = {
    cancelled: transient ? 'lrd.lifecycle_cancelled_transient' : 'lrd.lifecycle_cancelled',
    failed: 'lrd.lifecycle_failed', incomplete: 'lrd.lifecycle_incomplete',
    'transient-completed': 'lrd.lifecycle_completed_transient',
    'steady-converged': 'lrd.lifecycle_converged_steady',
    'numerical-converged': 'lrd.lifecycle_converged_numerical',
    'not-converged': 'lrd.lifecycle_not_converged',
  } as const
  const initialOnly = transient && results.time_s.length > 0 && results.time_s.every(time => time === 0)
  const sample = color.selectedResultSample()
  const sampleIndex=sample<0?results.time_s.length-1:Math.min(sample,results.time_s.length-1)
  const sampleVal=(series:number[])=>series[sampleIndex]??NaN
  const labels=(run.results as {object_bindings?:Record<string,{label:string}>})?.object_bindings??{}
  const label=(id:string,parent?:string)=>labels[parent?`${parent}/${id}`:id]?.label??id
  const cpById = new Map(results.cp_details.map((c) => [c.edge_id, c]))
  const chartLocation = openChart ? results.rack_summaries.flatMap(rack =>
    rack.servers.filter(server => server.cp_edge_ids.includes(openChart))
      .map(server => ({ rack, server })),
  )[0] : undefined
  const chartTitle = chartLocation
    ? label(chartLocation.rack.rack_id) + ' / ' + label(chartLocation.server.server_id, chartLocation.rack.rack_id)
    : t('liquid.cold_plate_chart_title')
  const fluid = resultFluid(run.results)
  const asLPM = (value:number) => flowLPM(value,fluid.rho_kg_m3)
  const selectedMaxChipC = toCelsius(Math.max(...results.rack_summaries.flatMap(rack =>
    rack.servers.map(server => sampleVal(server.max_chip_temp_K)))))

  return (
    <div className="content" id="liquid-results-container">
      <PanelTitle />
      {transient&&<label className="liquid-experiment-field">
        <span>{t('liquid.result_time_value',{time:results.time_s[sampleIndex]?.toFixed(1)??'—'})}</span>
        <input aria-label={t('liquid.result_time_aria')} type="range" min={0} max={results.time_s.length-1}
          value={sampleIndex} onChange={e=>pickLiquidSample(Number(e.target.value))}/>
      </label>}

      {/* ── solver status ──────────────────────────────────────────────── */}
      <details className="prop-group lr-status-details" id="lr-status" style={{ borderLeftColor: '#00b4dc' }}>
        <summary className="lr-status-toggle">{t(lifecycleKeys[lifecycle])}</summary>
        <div className="prop-hint">
          {results.converged
            ? t(initialOnly ? 'lrd.initial_numerical_converged' : 'lrd.accepted_numerical_converged')
            : t('lrd.lifecycle_not_converged')}
        </div>
        <dl className="lr-status-metrics">
          <div><dt>{t('lrd.loss')}</dt><dd>{results.final_loss != null ? results.final_loss.toExponential(2) : '?'}</dd></div>
          <div><dt>{t('lrd.iterations')}</dt><dd>{results.epochs ?? '?'}</dd></div>
        </dl>
      </details>

      {/* ── system totals ──────────────────────────────────────────────── */}
      <div className="prop-group" id="lr-overview" style={{ borderLeftColor: '#00b4dc' }}>
        <div className="prop-title" style={{ color: '#00b4dc' }}>
          {t('liquid.system_overview')}
        </div>
        <div className="prop-row">
          <label>{t('lrd.total_flow')}</label>
          <span id="lr-total-flow">{asLPM(results.rack_summaries.reduce((sum,r)=>sum+sampleVal(r.total_flow_kg_s),0)).toFixed(1)} LPM</span>
        </div>
        <div className="prop-row">
          <label>{t('liquid.inherit_max_chip')}</label>
          <span id="lr-max-chip" style={{ color: chipStatus(selectedMaxChipC).color }}>
            {selectedMaxChipC.toFixed(1)} °C
          </span>
        </div>
        <div className="prop-row">
          <label>{t('liquid.inherit_servers')}</label>
          <span id="lr-server-count">{overview.serverCount}</span>
        </div>
        <div className="prop-row" style={{ opacity: 0.7 }}>
          <label>{t('liquid.time_samples')}</label>
          <span id="lr-time-samples">
            {results.time_s.length}
            {transient
              ? results.expected_duration_s != null
                ? ` (${results.time_s[0]?.toFixed(1) ?? '—'} – ${lastVal(results.time_s).toFixed(1)} / ${results.expected_duration_s.toFixed(1)} s)`
                : ` (0 – ${lastVal(results.time_s).toFixed(1)} s)`
              : ` (${t('liquid.steady')})`}
          </span>
        </div>
        {fluid.legacyDefaults && <div className="prop-hint" id="lr-fluid-defaults">
          {t('liquid.legacy_fluid_hint')}
        </div>}
      </div>

      <CduOverview />

      {/* ── per rack ───────────────────────────────────────────────────── */}
      {results.rack_summaries.map((rack) => {
        const open = expanded[rack.rack_id] ?? true
        // Only legacy files lacking cold-plate details need an energy estimate.
        const supplyC = toCelsius(sampleVal(rack.supply_temp_K))
        return (
          <div
            className="prop-group"
            key={rack.rack_id}
            data-rack={rack.rack_id}
            style={{ borderLeftColor: '#00b4dc' }}
          >
            <button type="button" className="prop-title lr-rack-toggle"
              aria-label={label(rack.rack_id)} aria-expanded={open}
              onClick={() => setExpanded((e) => ({ ...e, [rack.rack_id]: !open }))}>
              <span>📊 {label(rack.rack_id)}</span>
              <span aria-hidden="true">{open ? '▾' : '▸'}</span>
            </button>
            {open && (
              <>
                <div className="lr-rack-summary">
                  <div className="lr-rack-metric">
                    <span>{t('liquid.rack_flow')}</span>
                    <strong>{asLPM(sampleVal(rack.total_flow_kg_s)).toFixed(1)} LPM</strong>
                  </div>
                  <div className="lr-rack-metric">
                    <span>{t('liquid.rack_supply')}</span>
                    <strong>{supplyC.toFixed(1)} °C</strong>
                  </div>
                  <div className="lr-rack-metric">
                    <span>{t('liquid.rack_return')}</span>
                    <strong>{toCelsius(sampleVal(rack.return_temp_K)).toFixed(1)} °C</strong>
                  </div>
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table className="lr-table">
                    <thead>
                      <tr>
                        <th>{t('liquid.col_server')}</th>
                        <th>{t('liquid.col_flow')}</th>
                        <th>{t('liquid.col_chip')}</th>
                        <th>{t('liquid.col_outlet')}</th>
                        <th title={t('liquid.col_heat')}>{t('liquid.col_heat_short')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rack.servers.map((srv) => {
                        const chipC = toCelsius(sampleVal(srv.max_chip_temp_K))
                        const st = chipStatus(chipC)
                        const outlet = serverResultOutlet(results,srv,sampleIndex,{
                          supplyC,returnC:toCelsius(sampleVal(rack.return_temp_K)),cpJkgK:fluid.cp_J_kgK,
                        })
                        const cp = srv.cp_edge_ids
                          .map((id) => cpById.get(id))
                          .find((c): c is ColdPlateDetail => !!c)
                        const chartKey = cp?.edge_id ?? null
                        const serverName = label(srv.server_id, rack.rack_id)
                        const rackPrefix = label(rack.rack_id) + '-'
                        const displayName = serverName.startsWith(rackPrefix)
                          ? serverName.slice(rackPrefix.length) : serverName
                        const highlighted =
                          !!focusEdgeIds?.some((id) => srv.cp_edge_ids.includes(id))
                        return (
                          <tr
                            key={srv.server_id}
                            data-server={srv.server_id}
                            className={highlighted ? 'lr-row-focus' : undefined}
                          >
                            <td title={serverName}>
                              {chartKey && transient ? (
                                <button
                                  className="lr-link lr-server-link"
                                  aria-label={serverName} title={serverName}
                                  onClick={() =>
                                    setOpenChart((k) => (k === chartKey ? null : chartKey))
                                  }
                                >
                                  {displayName}
                                </button>
                              ) : (
                                displayName
                              )}
                            </td>
                            <td>{asLPM(sampleVal(srv.flow_kg_s)).toFixed(1)}</td>
                            <td style={{ color: st.color }}>
                              {st.emoji} {chipC.toFixed(1)}
                            </td>
                            <td title={outlet.source==='legacy-estimate' ? t('liquid.outlet_legacy_estimate')
                              :outlet.source==='solved'?t('liquid.outlet_solved',{edge:outlet.edgeId??''}):t('liquid.outlet_unavailable')}>
                              {Number.isFinite(outlet.temperatureC)
                                ? `${outlet.source==='legacy-estimate'?'≈ ':''}${outlet.temperatureC.toFixed(1)}`
                                : '—'}
                            </td>
                            <td>{sampleVal(srv.heat_removed_W).toFixed(0)}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        )
      })}

      <ThreeDDisplay />

      {/* ── transient chart for the chosen cold plate ──────────────────── */}
      {openChart && transient && (
        <ChipChart
          cp={cpById.get(openChart)}
          title={chartTitle}
          time={results.time_s}
          onClose={() => setOpenChart(null)}
        />
      )}

      <RefreshButton />
    </div>
  )
}

/**
 * The `🎨 3D Display` block (js/liquid_results_panel.js:129-138).
 *
 * The checked state is held in the colouring module, not here: the scene stays
 * painted across a tab switch, and a checkbox that reset itself on remount
 * would offer "turn on" for something already on — which is what the legacy
 * does, since it rebuilds its panel as unchecked innerHTML over a coloured
 * scene.
 */
function ThreeDDisplay() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  // Re-applied on mount and on every project notification, because a scene
  // rebuild — a project load, a model edit, an air/liquid switch — replaces the
  // meshes and the new ones come back in their default colours. Without this
  // the checkbox says the pipes are coloured while they are red and blue again,
  // and the only way back is to switch it off and on.
  useEffect(() => {
    const reapply = () => {
      if (color.isPipeColoringOn()) color.setPipeTempColoring(true)
      if (color.isChipColoringOn()) color.setChipTempColoring(true)
    }
    reapply()
    return store.subscribe(reapply)
  }, [])
  return (
    <div className="prop-group" id="lc-3d-display" style={{ borderLeftColor: '#00b4dc' }}>
      <div className="prop-title" style={{ color: '#00b4dc' }}>
        🎨 {t('lrd.display_3d')}
      </div>
      <div className="prop-row">
        <label htmlFor="lc-color-pipes">{t('lrd.pipe_temp_color')}</label>
        <input
          type="checkbox"
          id="lc-color-pipes"
          checked={color.isPipeColoringOn()}
          onChange={(e) => {
            color.setPipeTempColoring(e.target.checked)
            bump((n) => n + 1)
          }}
        />
      </div>
      <div className="prop-row">
        <label htmlFor="lc-color-servers">{t('lrd.chip_temp_color')}</label>
        <input
          type="checkbox"
          id="lc-color-servers"
          checked={color.isChipColoringOn()}
          onChange={(e) => {
            color.setChipTempColoring(e.target.checked)
            bump((n) => n + 1)
          }}
        />
      </div>
    </div>
  )
}

/**
 * The panel heading.
 *
 * The legacy carries it on `#liquid-results-group` (STL_generate.html:776),
 * OUTSIDE the container the results are rendered into — so it is on screen
 * before a solve exists too, and both states here draw it.
 */
function PanelTitle() {
  const { t } = useTranslation()
  return (
    <div className="prop-title" id="liquid-results-title" style={{ color: '#00b4dc' }}>
      {t('liquid.results_title')}
    </div>
  )
}

/**
 * Re-read `liquid_result.json` from disk.
 *
 * The panel otherwise only ever shows what THIS session solved, so a run
 * finished in another window — or before the page was reloaded — needed a full
 * reload to appear. Shown in the empty state too, which is exactly where it is
 * most useful.
 */
function RefreshButton() {
  const { t } = useTranslation()
  return (
    <div className="prop-group" id="lr-refresh-group">
      <button
        className="btn"
        id="btn-refresh-liquid-results"
        onClick={() => void liquid.fetchLiquidResults(selectedLiquidRun() || undefined)}
        style={{
          background: 'rgba(0,180,220,0.1)',
          borderColor: 'rgba(0,180,220,0.3)',
          color: '#00b4dc',
        }}
      >
        {t('btn.refresh_liquid_results')}
      </button>
    </div>
  )
}

/**
 * Which CDU feeds which racks, read from the project.
 *
 * Ports the CDU block of `buildResultsPanel` (js/liquid_results_panel.js:46).
 * The solver document names racks but never says where their coolant came
 * from, so without this a multi-CDU result cannot be traced back to a loop.
 */
function CduOverview() {
  const { t } = useTranslation()
  const rows = cduOverview(store.getProject())
  if (rows.length === 0) return null
  return (
    <details className="prop-group lr-cdu-details" id="lr-cdu-overview" style={{ borderLeftColor: '#00b4dc' }}>
      <summary className="lr-cdu-toggle">
        {t('liquid.cdu_connections')}<span className="lr-cdu-count">{rows.length}</span>
      </summary>
      {rows.map((row) => (
        <div className="prop-hint" key={row.uid} data-cdu={row.uid}>
          {row.name} → {row.served}
        </div>
      ))}
    </details>
  )
}

function ChipChart({
  cp,
  title,
  time,
  onClose,
}: {
  cp: ColdPlateDetail | undefined
  title: string
  time: number[]
  onClose: () => void
}) {
  const { t } = useTranslation()
  const dialogRef = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = dialogRef.current
    const invokingControl = document.activeElement instanceof HTMLElement
      ? document.activeElement : null
    dialog?.showModal?.()
    return () => {
      if (dialog?.open) dialog.close?.()
      if (invokingControl?.isConnected) invokingControl.focus({ preventScroll: true })
    }
  }, [])
  if (!cp) return null
  const series: Series[] = [
    { label: t('liquid.chart_chip_temperature'), color: '#e06c75', points: cp.chip_temp_K.map(toCelsius) },
    { label: t('liquid.chart_coolant_inlet'), color: '#61afef', points: cp.water_inlet_temp_K.map(toCelsius) },
    { label: t('liquid.chart_coolant_outlet'), color: '#e5c07b', points: cp.water_outlet_temp_K.map(toCelsius) },
  ]
  return createPortal(
    <dialog ref={dialogRef} className="lr-chart-dialog" id="lsm-chart-group"
      aria-labelledby="lsm-chart-title"
      onCancel={(event) => { event.preventDefault(); onClose() }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="prop-title lr-chart-header">
        <span id="lsm-chart-title">{title}</span>
        <button type="button" className="lr-link lr-chart-close"
          aria-label={t('liquid.chart_close')} title={t('liquid.chart_close')} onClick={onClose}>
          ✕
        </button>
      </div>
      <TimeSeriesChart
        id="lsm-chart-canvas"
        time={time}
        series={series}
        xLabel={t('liquid.axis_time')}
        yLabel="°C"
      />
    </dialog>,
    document.body,
  )
}
