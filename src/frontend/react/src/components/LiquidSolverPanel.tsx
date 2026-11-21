import { useEffect, useState } from 'react'
import type { CfdProject } from '../schema/cfdproj'
import {
  readFluid,
  readLiquidConfig,
  totalTimeSeconds,
} from '../liquid/liquidConfig'
import {
  cpCatalogFromGraph,
  cpCatalogFromProject,
  getLiquidMonitorSelection,
  monitorCpIds,
  subscribeLiquidMonitor,
} from '../liquid/liquidMonitorConfig'
import { compileLiquidGraph } from '../liquid/liquidRunGraph'
import * as liquid from '../liquid/liquidSession'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'
import { LiquidFluidEditor } from './LiquidFluidEditor'

/**
 * The liquid-cooling solver tab.
 *
 * Replicates `#liquid-cooling-solver-group` from the legacy editor
 * (STL_generate.html:413-461), writing to the same places so a project
 * round-trips between the two apps. Every control keeps its legacy DOM id so
 * the parity sweep can address both apps with one selector.
 *
 * Result inheritance is NOT here. `#liquid-inheritance-group` is hidden in
 * liquid mode and shown in air mode (`_applyDisplayMode`,
 * STL_generate.html:14107, "Inheritance checkbox only visible in air mode"):
 * it makes the AIR solve pick up heat loads from a previous liquid run, so it
 * belongs to the air solver panel. Putting it here would offer the user a
 * switch that does nothing to the run they are configuring.
 *
 * Editing any fluid property re-types the coolant to 'custom' and flips the
 * preset dropdown, which is the legacy behaviour (`updateFluidProp`,
 * js/liquid_cooling.js:42): a coolant that no longer matches its preset must
 * not keep claiming to be that preset, or the next project load would silently
 * restore the preset values over the edited ones.
 */

export interface LiquidSolverPanelProps {
  project: CfdProject | null
  showFluid?: boolean
}

export function LiquidSolverPanel({ project, showFluid=true }: LiquidSolverPanelProps) {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  useEffect(() => liquid.subscribeLiquid(() => bump((n) => n + 1)), [])
  // The monitored-plate count below the button changes from the OTHER panel.
  useEffect(() => subscribeLiquidMonitor(() => bump((n) => n + 1)), [])

  const cfg = readLiquidConfig(project)
  const fluid = readFluid(project)
  const run = liquid.liquidRunState()
  // Counted from the project, which is what the monitor panel lists. The
  // request itself re-derives the ids from the compiled graph below.
  const monitored = monitorCpIds(cpCatalogFromProject(project), getLiquidMonitorSelection()).length

  function setSolver(key: string, value: unknown) {
    store.setNestedRunConfig('liquid', 'solver', key, value)
  }

  /**
   * Export the graph, then solve.
   *
   * Exporting first and bailing out on failure is the legacy order
   * (js/liquid_cooling.js:173) and it matters: `/api/liquid/solve` reads the
   * `.liquidrun` already on disk, so solving after a failed export would run
   * the PREVIOUS network and report its answer as this one's.
   */
  async function onRun() {
    if (!project) return
    const graph = compileLiquidGraph(project, {
      fluid,
      solver: cfg.solver,
      enabled: cfg.enabled,
    })
    if (!graph) return // the compiler already told the user why
    if (!(await liquid.exportLiquidrun(graph))) return
    // The ids come from the graph JUST compiled, not from the project: the
    // solver matches `monitor_cps` against `graph.edge_str_ids` and silently
    // drops anything else, so an id built from a stale name would monitor
    // nothing and leave the chip-temperature chart empty for the whole run.
    const monitorCps = monitorCpIds(cpCatalogFromGraph(graph), getLiquidMonitorSelection())
    await liquid.runLiquidSolve({ device: cfg.solver.device, monitorCps })
  }

  return (
    <div className="content" id="liquid-solver-settings-panel">
      {showFluid&&<LiquidFluidEditor project={project}/>}

      {/* ── solver parameters ──────────────────────────────────────────── */}
      <div className="prop-group" style={{ borderLeftColor: '#74c7ec' }}>
        <div className="prop-title" style={{ color: '#74c7ec' }}>
          {t('liquid.solver_params_title')}
        </div>
        <details id="lsm-time-details" className="lc-details" open>
          <summary>{t('liquid.time_stepping')}</summary>
          <div className="lc-details-body">
            <div className="prop-row">
              <label htmlFor="lsm-dt">dt (s)</label>
              <NumberField
                id="lsm-dt"
                value={cfg.solver.dt_s}
                step={0.1}
                min={0.001}
                onCommit={(v) => setSolver('dt_s', v)}
              />
            </div>
            <div className="prop-row">
              <label htmlFor="lsm-nt">{t('lq_ux.legacy_steps')}</label>
              <NumberField
                id="lsm-nt"
                value={cfg.solver.N_t}
                step={1}
                min={1}
                onCommit={(v) => setSolver('N_t', Math.max(1, Math.round(v)))}
              />
            </div>
            <div className="prop-row" style={{ opacity: 0.7 }}>
              <label>{t('lq_ux.legacy_total_time')}</label>
              <span id="lsm-total-time" style={{ fontSize: 12, color: '#a6e3a1' }}>
                {totalTimeSeconds(cfg.solver).toFixed(1)} s
              </span>
            </div>
            <div className="prop-row">
              <label htmlFor="lsm-bdf">{t('lq_ux.legacy_bdf')}</label>
              <select
                id="lsm-bdf"
                value={String(cfg.solver.bdf_order)}
                onChange={(e) => setSolver('bdf_order', parseInt(e.target.value, 10))}
              >
                <option value="1">BDF1</option>
                <option value="2">BDF2</option>
                <option value="3">BDF3</option>
              </select>
            </div>
          </div>
        </details>
        <details id="lsm-optim-details" className="lc-details" open>
          <summary>{t('lq_ux.legacy_optim')}</summary>
          <div className="lc-details-body">
            <div className="prop-row">
              <label htmlFor="lsm-tol">{t('lq_ux.legacy_tol')}</label>
              <NumberField
                id="lsm-tol"
                value={cfg.solver.convergence_tol}
                step={1e-7}
                min={1e-15}
                onCommit={(v) => setSolver('convergence_tol', v)}
              />
            </div>
            <div className="prop-row">
              <label htmlFor="lsm-device">{t('lq_ux.legacy_device')}</label>
              <select
                id="lsm-device"
                value={cfg.solver.device}
                onChange={(e) => setSolver('device', e.target.value)}
              >
                <option value="cpu">CPU</option>
                <option value="cuda">CUDA (GPU)</option>
              </select>
            </div>
          </div>
        </details>
      </div>

      {/* ── actions ────────────────────────────────────────────────────── */}
      <div className="lc-action-bar">
        <button
          id="lc-run-btn"
          className="btn primary"
          style={{ width: '100%', margin: '2px 0' }}
          disabled={run.running || !project}
          onClick={() => void onRun()}
        >
          {t('btn.run_liquid_solve')}
        </button>
        <button
          id="lc-stop-btn"
          className="btn"
          style={{
            width: '100%',
            margin: '2px 0',
            display: run.running ? '' : 'none',
            background: '#e06c75',
            color: '#fff',
            border: '1px solid #c0392b',
          }}
          onClick={() => liquid.stopLiquidSolve()}
        >
          {t('btn.stop_liquid_solve')}
        </button>
        <div className="prop-hint" id="lc-monitor-count">
          {t('lsm.cp_cu_monitor')}: {monitored}
        </div>
        {run.error && (
          <div id="lc-run-error" className="prop-hint" style={{ color: '#e06c75' }}>
            {run.error}
          </div>
        )}
      </div>
    </div>
  )
}
