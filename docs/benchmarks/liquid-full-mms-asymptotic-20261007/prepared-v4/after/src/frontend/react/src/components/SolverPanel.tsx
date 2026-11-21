import { useEffect, useState } from 'react'
import type { CfdProject } from '../schema/cfdproj'
import type { TranslationKey } from '../i18n'
import * as store from '../projectStore'
import * as session from '../run/session'
import {
  DDT_SCHEMES,
  DIV_SCALAR_SCHEMES,
  DIV_U_SCHEMES,
  LAPLACIAN_SCHEMES,
  RELAXATION_KEYS,
  RESIDUAL_KEYS, TRANSIENT_RESIDUAL_KEYS,
  SNGRAD_SCHEMES,
  START_FROM,
  solverAlgorithm,
  solverConfigOf,
  usesRelaxation,
  WRITE_PRECISIONS,
  PRESSURE_LINEAR_DEFAULTS,
  type PressureLinearSolver,
} from '../run/solverConfig'
import { useSolveLauncher } from '../run/useSolveLauncher'
import { useTranslation } from '../useTranslation'
import { meshConfigOf } from '../run/meshConfig'
import { NumberField } from './NumberField'
import {
  applySolverPreset,
  setNonOrthoCorrection,
  setNonOrthoCorrectors,
  setSolverScheme,
  type PresetName,
} from '../run/solverPresets'
import { FreezeGuard } from './FreezeGuard'
import { LIVE_CLASS, RUN_BLOCK_CLASS } from '../run/uiFreeze'
import {
  getMeshWarnings,
  hasRejectedMeshQuality,
  installMeshWarningBridge,
  installMeshWarningFeed,
  subscribeMeshWarnings,
} from '../run/meshWarnings'
import { MeshQualityDialog } from './MeshQualityDialog'
import { meshWarningLabel, meshWarningMessage } from '../run/meshWarningLabels'
import { coreWarning } from '../run/coreCount'
import {
  DDT_SCHEME_LABELS,
  START_FROM_LABELS,
  WRITE_PRECISION_LABELS,
  optionLabelKey,
} from '../run/solverOptionLabels'
import {
  installSolverMonitorBridge,
  installSolverMonitorFeed,
} from '../run/solverMonitor'
import { mountSolverMonitorHost } from '../run/solverMonitorHost'
import { transientOf } from '../transient/model'

/**
 * The solver tab, air-cooling half.
 *
 * Steady and transient share one config object but not one control set, so the
 * panel shows the fields that belong to the active `solverType` rather than all
 * of them — `endTime` is iterations for steady while `endTimeSeconds` is
 * physical seconds for transient, and showing both invites setting the one the
 * run ignores.
 *
 * Relaxation factors are hidden under PISO (transient with
 * `nOuterCorrectors === 1`) because PISO ignores them. Showing numbers that do
 * nothing is worse than showing none.
 *
 * The liquid-cooling sections are not here: liquid mode is not migrated, and a
 * half-wired coolant panel would be a way to write a fluid property that never
 * reaches the solver.
 */

/** The server's own severity scale, shared with the pre-solve dialog. */
const WARN_COLOR: Record<number, string> = { 1: '#ff4444', 2: '#ff8c00', 3: '#ffcc00' }
const WARN_LEVEL_KEY: Record<number, TranslationKey> = {
  1: 'mesh_warn.level_critical',
  2: 'mesh_warn.level_warning',
  3: 'mesh_warn.level_info',
}

export interface SolverPanelProps {
  project: CfdProject | null
}

export function SolverPanel({ project }: SolverPanelProps) {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [advanced, setAdvanced] = useState(false)
  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])

  const cfg = solverConfigOf(project)
  const algorithm = solverAlgorithm(cfg)
  const transient = cfg.solverType === 'transient'
  // The transient TAB follows the same flag: an event authored there is ignored
  // by a steady run, so the legacy hides the button outright (:26188). Applied
  // on every render of this panel as well as from the store watcher, because
  // this is where the radio that changes it lives.
  const info = session.getServerInfo()
  const mesh = meshConfigOf(project)
  const meshParallel = mesh.parallelMesh !== false
  const meshProcs = mesh.meshProcess
  const parallelSolve = cfg.parallelSolve !== false
  const solveProcs = Math.max(1, Math.round(Number(cfg.solveProcess) || 1))
  // Same physical-core threshold the mesh panel uses: the solve decomposes the
  // same way, so oversubscribing hyperthreads costs the same here.
  const solveCores = coreWarning(solveProcs, info?.cpuCount)

  function set(key: string, value: unknown) {
    store.setRunConfig('simulationConfig', key, value)
  }
  function setPressureOption(key: keyof PressureLinearSolver, value: string | number | null): boolean {
    if (session.isUiFrozen()) return false
    if (value !== null) {
      if (key === 'preconditioner') {
        if (value !== 'DIC' && value !== 'GAMG') return false
      } else if (typeof value !== 'number' || !Number.isFinite(value) ||
        (key === 'tolerance' && value <= 0) ||
        (key === 'relTol' && (value < 0 || value >= 1)) ||
        (key === 'maxIter' && (!Number.isInteger(value) || value <= 0))) return false
    }
    return store.edit('setRunConfig', 'pressureLinearSolver.' + key + ' = ' + String(value), (draft) => {
      const defaults = draft.runDefaults ?? (draft.runDefaults = {})
      const simulation = (defaults.simulationConfig ?? (defaults.simulationConfig = {})) as Record<string, unknown>
      const current = simulation.pressureLinearSolver
      const next: Record<string, unknown> = current && typeof current === 'object'
        ? Object.fromEntries(Object.entries(current).filter(([, v]) => v != null)) : {}
      if (value === null) delete next[key]
      else next[key] = value
      if (Object.keys(next).length) simulation.pressureLinearSolver = next
      else delete simulation.pressureLinearSolver
    })
  }

  function setNested(
    group: 'schemes' | 'residualControl' | 'transientResidualControl' | 'relaxationFactors',
    key: string,
    value: unknown,
  ) {
    // A scheme or a relaxation factor also re-derives the preset name: once one
    // of them is hand-edited the badge is a claim about the run, and the legacy
    // recomputes it on every such write (`setSolverScheme`,
    // STL_generate.html:26092). Residual controls are not part of a preset, so
    // they take the plain path.
    if (group === 'residualControl' || group === 'transientResidualControl') {
      store.setNestedRunConfig('simulationConfig', group, key, value)
      return
    }
    setSolverScheme(group, key, value as string | number)
  }

  return (
    // Two nested ids, as the legacy panel has: the outer one is the tab's
    // scroll container and the inner one is what `_applyDisplayMode` hides in
    // liquid mode (STL_generate.html:272-273).
    <div className="content" id="solver-settings-panel">
      <div id="air-solver-settings">
      {/* Picking is deliberately off on this tab (Viewport.tsx:647), so that a
          click drags the locationInMesh sphere instead of grabbing a rack in
          front of it. A user who clicks a rack and gets nothing needs telling
          it is intentional — the legacy leads the panel with the same line
          (STL_generate.html:275). */}
      <div className="prop-hint" id="solver-no-select-hint" style={{ padding: '8px 8px 0', lineHeight: 1.5 }}>
        {t('hint.solver_no_select')}
      </div>
      {/* Frozen while a job runs, down to but not including the run block —
          the abort button inside it has to stay usable. */}
      <FreezeGuard>
      {/* The "inherit liquid results" group that used to sit here is gone:
          nothing in the backend read the flag it wrote (no consumer of
          inheritLiquidResults in aircooling/), so it promised a coupling that
          never happened. The liquid solve is its own workflow now. */}
      {/* ── solver settings ────────────────────────────────────────────── */}
      <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-solver)' }}>
        <div className="prop-title" style={{ color: 'var(--section-header-solver)' }}>
          {t('title.solver_settings')}
        </div>
        {/* What the AIR solver will run, read-only: the top-bar switch (in the
            air view) is its one writer (E8b: air and liquid each have their own mode). */}
        <div className="prop-row">
          <label>{t('label.solver_type')}</label>
          <span className="value" id="slv-mode">
            {transient ? t('transient.mode.transient') : t('transient.mode.steady')}
            <span className="prop-hint" style={{ marginLeft: 6 }}>{t('transient.mode.air_hint')}</span>
          </span>
        </div>
        <div className="prop-row">
          <label>{t('label.algorithm')}</label>
          <span className="value">{algorithm}</span>
        </div>

        {!transient && (
          <>
            <div className="prop-row">
              <label>{t('label.iterations')}</label>
              <NumberField
                id="slv-endtime"
                value={cfg.endTime}
                step={100}
                min={1}
                onCommit={(v) => set('endTime', Math.round(v))}
              />
            </div>
            <div className="prop-row">
              <label>{t('label.write_interval')}</label>
              {/* The box takes a COUNT here and SECONDS in the adaptive
                  transient row below; the two are otherwise identical, and the
                  legacy annotates both for that reason (:286, :302). */}
              <span className="prop-hint" style={{ marginRight: 4 }}>{t('hint.write_by_step')}</span>
              <NumberField
                id="slv-writeinterval"
                value={cfg.writeInterval}
                step={100}
                min={1}
                onCommit={(v) => set('writeInterval', Math.round(v))}
              />
            </div>
          </>
        )}

        {transient && (
          <>
            {/* The duration is the timeline's (its right end); endTimeSeconds
                only mirrors it, so it is shown here, not edited. */}
            <div className="prop-row">
              <label>{t('label.end_time_seconds')}</label>
              <span className="value" id="slv-end-time-s">
                {t('transient.solver.duration_hint', { d: transientOf(project).durationS })}
              </span>
            </div>
            <div className="prop-row">
              <label>{t('label.start_from')}</label>
              <select
                id="slv-start-from"
                value={cfg.startFrom}
                onChange={(e) => set('startFrom', e.target.value)}
              >
                {START_FROM.map((s) => {
                  const key = optionLabelKey(START_FROM_LABELS, s)
                  return (
                    <option key={s} value={s}>
                      {key ? t(key) : s}
                    </option>
                  )
                })}
              </select>
            </div>
            <div className="prop-row">
              <label>{t('label.time_step_mode')}</label>
              <span className="value">
                <label style={{ marginLeft: 8, cursor: 'pointer' }}>
                  <input
                    type="radio"
                    name="dtMode"
                    checked={cfg.adjustTimeStep !== false}
                    onChange={() => set('adjustTimeStep', true)}
                  />{' '}
                  {t('label.adaptive')}
                </label>
                <label style={{ marginLeft: 8, cursor: 'pointer' }}>
                  <input
                    type="radio"
                    name="dtMode"
                    checked={cfg.adjustTimeStep === false}
                    onChange={() => set('adjustTimeStep', false)}
                  />{' '}
                  {t('label.fixed')}
                </label>
              </span>
            </div>
            <div className="prop-row">
              <label>{t('label.delta_t')}</label>
              <NumberField
                id="slv-delta-t"
                value={cfg.deltaT}
                step={0.01}
                min={0}
                onCommit={(v) => set('deltaT', v)}
              />
            </div>
            {cfg.adjustTimeStep !== false && (
              <>
                <div className="prop-row">
                  <label>{t('label.max_delta_t')}</label>
                  <NumberField
                    id="slv-max-delta-t"
                    value={cfg.maxDeltaT}
                    step={0.1}
                    min={0}
                    onCommit={(v) => set('maxDeltaT', v)}
                  />
                </div>
                <div className="prop-row">
                  <label>{t('label.max_courant')}</label>
                  <NumberField
                    id="slv-max-co"
                    value={cfg.maxCo}
                    step={0.1}
                    min={0}
                    onCommit={(v) => set('maxCo', v)}
                  />
                </div>
                <div className="prop-row" id="write-interval-adaptive-row">
                  <label>{t('label.write_interval_seconds')}</label>
                  <span className="prop-hint" style={{ marginRight: 4 }}>{t('hint.write_by_time')}</span>
                  <NumberField
                    id="slv-write-interval-s"
                    value={cfg.writeIntervalSeconds}
                    step={1}
                    min={0}
                    onCommit={(v) => set('writeIntervalSeconds', v)}
                  />
                </div>
              </>
            )}
            {cfg.adjustTimeStep === false && (
              <div className="prop-row" id="write-interval-fixed-row">
                <label>{t('label.write_interval')}</label>
                <span className="prop-hint" style={{ marginRight: 4 }}>{t('hint.write_by_step')}</span>
                <NumberField
                  id="slv-write-interval-fixed"
                  value={cfg.writeInterval}
                  step={10}
                  min={1}
                  onCommit={(v) => set('writeInterval', Math.round(v))}
                />
              </div>
            )}
            <div className="prop-row">
              <label>{t('label.ddt_scheme')}</label>
              <select
                id="slv-ddt-scheme"
                value={cfg.ddtScheme}
                onChange={(e) => set('ddtScheme', e.target.value)}
              >
                {DDT_SCHEMES.map((s) => {
                  const key = optionLabelKey(DDT_SCHEME_LABELS, s)
                  return (
                    <option key={s} value={s}>
                      {key ? t(key) : s}
                    </option>
                  )
                })}
              </select>
            </div>
            {cfg.ddtScheme === 'CrankNicolson' && (
              <div className="prop-row">
                <label>{t('label.cn_coeff')}</label>
                <NumberField
                  id="slv-cn-coeff"
                  value={cfg.crankNicolsonCoeff}
                  step={0.05}
                  min={0}
                  max={1}
                  onCommit={(v) => set('crankNicolsonCoeff', v)}
                />
              </div>
            )}
            <div className="prop-row">
              <label>{t('label.n_correctors')}</label>
              <NumberField
                id="slv-n-correctors"
                value={cfg.nCorrectors}
                step={1}
                min={1}
                max={10}
                onCommit={(v) => set('nCorrectors', Math.round(v))}
              />
            </div>
          </>
        )}

        <div className="prop-row">
          <label>{t('label.write_precision')}</label>
          <select
            id="slv-write-precision"
            value={cfg.writePrecision}
            onChange={(e) => set('writePrecision', e.target.value)}
          >
            {WRITE_PRECISIONS.map((p) => {
              const key = optionLabelKey(WRITE_PRECISION_LABELS, p)
              return (
                <option key={p} value={p}>
                  {key ? t(key) : p}
                </option>
              )
            })}
          </select>
        </div>
        <div className="prop-row">
          <label>{t('label.parallel_solve')}</label>
          <input
            id="slv-parallel"
            type="checkbox"
            checked={parallelSolve}
            onChange={(e) => set('parallelSolve', e.target.checked)}
          />
          {/* Says what the checkbox MEANS for this run, not how many cores the
              host has: "/ 48" read as a capacity, which is not what the legacy
              label carries (:25823). */}
          <span id="slv-procs-label" className="prop-hint" style={{ marginLeft: 6 }}>
            {parallelSolve ? t('status.cores', { n: solveProcs }) : t('status.serial')}
          </span>
        </div>
        <div className="prop-row" style={{ opacity: parallelSolve ? 1 : 0.4 }}>
          <label>{t('label.solve_procs')}</label>
          <NumberField
            id="slv-procs"
            value={cfg.solveProcess}
            step={1}
            min={1}
            max={128}
            disabled={!parallelSolve}
            onCommit={(v) => set('solveProcess', Math.round(v))}
          />
        </div>
        {solveCores && (
          <div id="slv-procs-warning" className="prop-hint" style={{ color: 'var(--hint-color)' }}>
            {t('status.cores_warning', { ...solveCores })}
          </div>
        )}
        {/* The solve core count is independent of the MESH core count, and the
            two disagreeing is a common surprise; the legacy panel states the
            mesh side here rather than making it a guess. */}
        <div className="prop-hint">
          {t('hint.parallel_solve_desc')}{' '}
          <b id="slv-mesh-parallel-status">
            {meshParallel ? t('status.parallel_cores', { n: meshProcs }) : t('status.serial')}
          </b>
        </div>
      </div>

      {/* ── gravity ────────────────────────────────────────────────────── */}
      <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-gravity)' }}>
        <div className="prop-title" style={{ color: 'var(--section-header-gravity)' }}>
          {t('title.gravity')}
        </div>
        <div className="prop-hint">{t('hint.gravity_desc')}</div>
        {(['X', 'Y', 'Z'] as const).map((axis) => (
          <div className="prop-row" key={axis}>
            <label>{axis}</label>
            <NumberField
              id={`slv-g${axis.toLowerCase()}`}
              value={Number(cfg[`gravity${axis}`] ?? 0)}
              step={0.01}
              onCommit={(v) => set(`gravity${axis}`, v)}
            />
          </div>
        ))}
      </div>

      {/* ── preset + advanced schemes ──────────────────────────────────── */}
      <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-solver)' }}>
        <div className="prop-title" style={{ color: 'var(--section-header-solver)' }}>
          {t('title.solver_preset')}
        </div>
        <div className="solver-preset-row">
          {(
            [
              ['fast', 'btn.preset_fast'],
              ['balanced', 'btn.preset_balanced'],
              ['accurate', 'btn.preset_accurate'],
            ] as [string, TranslationKey][]
          ).map(([id, key]) => (
            <button
              key={id}
              id={`preset-${id}`}
              className={`btn${cfg.solverPreset === id ? ' primary' : ''}`}
              style={{ marginBottom: 0 }}
              // Writes the schemes and relaxation factors, not just the name —
              // see `solverPresets.ts`.
              onClick={() => {
                store.logOperation('solve', `${t('title.solver_preset')}: ${id}`)
                applySolverPreset(id as PresetName)
              }}
            >
              {t(key)}
            </button>
          ))}
        </div>
        <div className="prop-hint">{t('hint.preset_desc')}</div>
        <div className="prop-row">
          <label>{t('label.non_ortho_correction')}</label>
          <input
            id="slv-nonortho"
            type="checkbox"
            checked={cfg.nonOrthoCorrection !== false}
            onChange={(e) => setNonOrthoCorrection(e.target.checked)}
          />
        </div>

        <button
          id="slv-advanced-toggle"
          type="button"
          className="collapse-header solver-advanced-toggle"
          aria-expanded={advanced}
          aria-controls="slv-advanced-settings"
          onClick={() => setAdvanced((v) => !v)}
        >
          <span className="collapse-caret" aria-hidden="true">{advanced ? '▼' : '▶'}</span>
          <span>{t('label.advanced_settings')}</span>
        </button>
        {advanced && (
          <div id="slv-advanced-settings">
            <div className="prop-hint solver-pressure-title">{t('solver_pressure.title')}</div>
            <div id="slv-pressure-help" className="prop-hint">{t('solver_pressure.help')}</div>
            <div className="prop-row">
              <label htmlFor="slv-pressure-preconditioner">{t('solver_pressure.method')}</label>
              <select
                id="slv-pressure-preconditioner"
                value={cfg.pressureLinearSolver?.preconditioner ?? ''}
                aria-describedby="slv-pressure-help"
                onChange={(e) => setPressureOption('preconditioner', e.target.value || null)}
              >
                <option value="">{t(transient ? 'solver_pressure.default_transient' : 'solver_pressure.default_steady')}</option>
                <option value="DIC">DIC</option>
                <option value="GAMG">GAMG</option>
              </select>
            </div>
            {([
              ['tolerance', 'solver_pressure.tolerance', 1e-8],
              ['relTol', 'solver_pressure.rel_tol', .01],
              ['maxIter', 'solver_pressure.max_iter', 1],
            ] as const).map(([key, label, step]) => (
              <div className="prop-row" key={key}>
                <label htmlFor={key === 'relTol' ? 'slv-pressure-rel-tol' : key === 'maxIter' ? 'slv-pressure-max-iter' : 'slv-pressure-tolerance'}>{t(label)}</label>
                <NumberField
                  id={key === 'relTol' ? 'slv-pressure-rel-tol' : key === 'maxIter' ? 'slv-pressure-max-iter' : 'slv-pressure-tolerance'}
                  value={cfg.pressureLinearSolver?.[key] ?? null}
                  step={step}
                  placeholder={String(PRESSURE_LINEAR_DEFAULTS[cfg.solverType][key])}
                  ariaDescribedBy="slv-pressure-help"
                  onCommit={(value) => setPressureOption(key, value)}
                  onClear={() => { setPressureOption(key, null) }}
                />
              </div>
            ))}
            <div className="prop-hint" style={{ marginTop: 6 }}>
              {t('label.discretization_schemes')}
            </div>
            <SchemeRow
              id="slv-div-U"
              label="div(U)"
              value={cfg.schemes?.divSchemeU}
              options={DIV_U_SCHEMES}
              onChange={(v) => setNested('schemes', 'divSchemeU', v)}
            />
            <SchemeRow
              id="slv-div-T"
              label="div(T)"
              value={cfg.schemes?.divSchemeT}
              options={DIV_SCALAR_SCHEMES}
              onChange={(v) => setNested('schemes', 'divSchemeT', v)}
            />
            <SchemeRow
              id="slv-div-ke"
              label="div(k,ε)"
              value={cfg.schemes?.divSchemeK}
              options={DIV_SCALAR_SCHEMES}
              onChange={(v) => {
                // The legacy select writes BOTH k and epsilon: the two are always
                // discretised the same way, and letting them diverge here would
                // be a setting nobody wants and the UI cannot show.
                setNested('schemes', 'divSchemeK', v)
                setNested('schemes', 'divSchemeEpsilon', v)
              }}
            />
            <SchemeRow
              id="slv-laplacian"
              label="laplacian"
              value={cfg.schemes?.laplacianScheme}
              options={LAPLACIAN_SCHEMES}
              onChange={(v) => setNested('schemes', 'laplacianScheme', v)}
            />
            <SchemeRow
              id="slv-sngrad"
              label="snGrad"
              value={cfg.schemes?.snGradScheme}
              options={SNGRAD_SCHEMES}
              onChange={(v) => setNested('schemes', 'snGradScheme', v)}
            />
            <div className="prop-row">
              <label>nNonOrthoCorr</label>
              <NumberField
                id="slv-nnonortho"
                value={cfg.nNonOrthogonalCorrectors}
                step={1}
                min={0}
                max={5}
                onCommit={(v) => setNonOrthoCorrectors(Math.round(v))}
              />
            </div>

            <div className="prop-hint" style={{ marginTop: 6 }}>
              {t('label.relaxation_factors')}
            </div>
            {usesRelaxation(cfg) ? (
              RELAXATION_KEYS.map((r) => (
                <div className="prop-row" key={r.key}>
                  <label>{r.label}</label>
                  <NumberField
                    id={r.domId}
                    value={Number(cfg.relaxationFactors?.[r.key] ?? 0.7)}
                    step={0.05}
                    min={0}
                    max={1}
                    onCommit={(v) => setNested('relaxationFactors', r.key, v)}
                  />
                </div>
              ))
            ) : (
              <div className="prop-hint">{t('hint.piso_no_relaxation')}</div>
            )}
          </div>
        )}
      </div>

      {/* ── nonlinear convergence: the set for the air mode (E8c) ──────── */}
      {/* Steady: SIMPLE stops once every residual is below its threshold (the
          iteration count above is the cap). Transient: each time step's outer
          loop runs until its own thresholds are met, at most nOuterCorrectors. */}
      <div className="prop-group" id="slv-convergence" style={{ borderLeftColor: 'var(--section-header-field)' }}>
        <div className="prop-title" style={{ color: 'var(--section-header-field)' }}>
          {t(transient ? 'title.residual_control_transient' : 'title.residual_control')}
        </div>
        <div className="prop-hint">{t(transient ? 'hint.residual_desc_transient' : 'hint.residual_desc')}</div>
        {transient && (
          <div className="prop-row">
            <label>{t('label.n_outer_correctors')}</label>
            <NumberField
              id="slv-n-outer"
              value={cfg.nOuterCorrectors}
              step={1}
              min={1}
              max={100}
              onCommit={(v) => set('nOuterCorrectors', Math.round(v))}
            />
          </div>
        )}
        {(transient ? TRANSIENT_RESIDUAL_KEYS : RESIDUAL_KEYS).map((r) => (
          <ResidualRow
            key={r.domId}
            id={r.domId}
            label={`${r.key === 'k_epsilon' ? 'k / ε' : r.key} ${t(r.labelKey as 'label.res_pressure')}`}
            value={Number((transient ? cfg.transientResidualControl : cfg.residualControl)?.[r.key] ?? 1e-4)}
            onCommit={(v) => setNested(transient ? 'transientResidualControl' : 'residualControl', r.key, v)}
          />
        ))}
      </div>
      </FreezeGuard>

      <SolveRunBlock />
      </div>
    </div>
  )
}

function SchemeRow({
  id,
  label,
  value,
  options,
  onChange,
}: {
  /** The legacy select's id — the parity sweep addresses controls by id. */
  id?: string
  label: string
  value: string | undefined
  options: readonly string[]
  onChange: (v: string) => void
}) {
  // The stored value may be one the preset wrote that is not in the list; keep
  // it selectable rather than silently switching the run to a different scheme.
  const list = value && !options.includes(value) ? [value, ...options] : options
  return (
    <div className="prop-row">
      <label>{label}</label>
      <select
        {...(id === undefined ? {} : { id })}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        style={{ flex: 1, minWidth: 0 }}
      >
        {list.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  )
}

/**
 * A residual threshold.
 *
 * A TEXT input, not a number one: these are written as `1e-4`, and a
 * `<input type="number">` normalises that to `0.0001` on every render — which
 * makes the field impossible to type scientific notation into.
 */
function ResidualRow({
  label,
  value,
  onCommit,
  id,
}: {
  label: string
  value: number
  onCommit: (v: number) => void
  id?: string
}) {
  const [text, setText] = useState(() => String(value))
  const [editing, setEditing] = useState(false)
  useEffect(() => {
    if (!editing) setText(String(value))
  }, [value, editing])

  return (
    <div className="prop-row">
      <label>{label}</label>
      <input
        id={id}
        type="text"
        value={text}
        onFocus={() => setEditing(true)}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => {
          setEditing(false)
          const parsed = Number(e.target.value)
          if (Number.isFinite(parsed) && parsed > 0) onCommit(parsed)
          else setText(String(value))
        }}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      />
    </div>
  )
}

/**
 * Check / run / abort.
 *
 * Running rebuilds the .cfdrun from the settings above and posts it with the
 * run, so a changed endTime, gravity, scheme or residual threshold reaches the
 * solver — `runSolveOnly` (STL_generate.html:28372) re-exports for exactly that
 * reason. No STL goes with it: a solve reuses the mesh the last mesh run built,
 * and the server wipes triSurface/ before writing any set it is sent.
 */
function SolveRunBlock() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [checkStatus, setCheckStatus] = useState('')
  const [warnDetail, setWarnDetail] = useState(false)
  // The mesh-quality gate, the monitor window and the submit itself — shared
  // with the transient tab's ▶, which is the same gesture and used to have none
  // of them. See `useSolveLauncher`.
  const launcher = useSolveLauncher()
  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])
  useEffect(() => subscribeMeshWarnings(() => bump((n) => n + 1)), [])
  useEffect(() => store.subscribeProjectLoad(() => {
    setCheckStatus('')
    setWarnDetail(false)
  }), [])
  // Installed from here because this panel owns both buttons that produce the
  // data: 检测网格状态 is what fills the warning list, and 执行求解 is what opens
  // the monitor. All four are idempotent.
  useEffect(() => {
    installMeshWarningFeed()
    installMeshWarningBridge()
    installSolverMonitorFeed()
    installSolverMonitorBridge()
    mountSolverMonitorHost()
  }, [])

  const job = session.getCurrentCaseJob()
  const running = !!session.getJob()?.running
  const validated = session.isValidated()
  const meshReady = session.isMeshReady()
  const warnings = getMeshWarnings()
  const rejected = hasRejectedMeshQuality()


  // An explicit failed quality verdict remains authoritative after a normal native exit.
  const checkOk =
    job?.kind === 'checkMesh'
      ? typeof job.checkMeshOk === 'boolean'
        ? job.checkMeshOk
        : job.returncode === 0
      : false

  async function check() {
    setCheckStatus(t('status.checking'))
    store.logOperation('checkMesh', t('btn.check_mesh'))
    try {
      await session.runCheckMesh()
      // The job is up now, and the status row says "running" for as long as
      // IT runs. Keeping the line here instead outlived the check: the next
      // solve brought it back under the solve button.
      setCheckStatus('')
    } catch (err) {
      const message =
        err instanceof Error && err.message === 'mesh not found'
          ? t('status.mesh_not_found')
          : t('status.check_failed', { error: err instanceof Error ? err.message : String(err) })
      setCheckStatus(message)
      store.logOperation('checkMesh', message)
    }
  }

  return (
    <div className={RUN_BLOCK_CLASS} style={{ margin: 8 }}>
      {/* Checking BEFORE solving is the point: the solve button unlocks on
          `meshReady`, and a mesh that exists but fails checkMesh is exactly the
          case where a solve burns an hour to diverge. */}
      <button
        className={`btn ${LIVE_CLASS}`}
        id="check-mesh-btn"
        disabled={!validated || running}
        onClick={() => void check()}
      >
        {t('btn.check_mesh')}
      </button>
      <div id="mesh-check-status" className="prop-hint" style={{ textAlign: 'center' }}>
        {/* "running" is read off the job, not remembered: it shows while a
            checkMesh runs and nowhere else. Once the job reports back the result
            below is the answer, and leaving both up says two things at once. */}
        {job?.kind === 'checkMesh'
          ? job.running ? t('status.running_checkmesh') : ''
          : checkStatus}
      </div>
      <button
        className="btn success"
        id="solve-run-btn"
        disabled={!validated || !meshReady || running || launcher.preparing}
        title={
          !validated
            ? t('error.validate_dctwin_first')
            : !meshReady
              ? t('error.mesh_first')
              : undefined
        }
        onClick={launcher.run}
      >
        {launcher.preparing
          ? t('progress.preparing_solve')
          : running && job?.kind === 'solve'
            ? t('status.solving')
            : t('btn.run_solve')}
      </button>
      {running && job?.kind === 'solve' && (
        <button
          className={`btn danger ${LIVE_CLASS}`}
          id="solve-abort-btn"
          disabled={!!(job.abortPending || job.abortRequested)}
          onClick={() => {
            store.logOperation('solve', t('btn.abort_solve'))
            void session.abortJob()
          }}
        >
          {job.abortPending || job.abortRequested ? t('status.stopping') : t('btn.abort_solve')}
        </button>
      )}
      {launcher.error && <div className="error">{launcher.error}</div>}
      {job?.error && <div className={`error ${LIVE_CLASS}`} role="alert">{job.error}</div>}
      {job?.kind === 'solve' && !job.running && job.checkMeshOk === false && (
        <div className="error" id="solve-mesh-rejection" role="status">
          {t('status.checkmesh_rejected')}
        </div>
      )}
      {job?.kind === 'checkMesh' && !job.running && job.returncode !== null && (
        <div
          className="prop-hint"
          id="check-mesh-result"
          style={{ color: checkOk ? 'var(--highlight)' : 'var(--danger,#ff6666)' }}
        >
          {/* The COUNT comes from the warning list, not from `failedChecks`:
              the server sends that as a number ("Failed 3 mesh checks") and
              reading `.length` off it yields undefined, so this line used to
              say "0 warnings" on a mesh with three. */}
          {checkOk
            ? t('status.checkmesh_ok')
            : t('status.checkmesh_rejected')}
        </div>
      )}
      {/* Which checks failed, not how many. A count cannot be acted on:
          non-orthogonality at 71° is a solve that will be slightly wrong, and a
          tiny orphan region is a solve that will not start. The legacy carries
          the same list into its pre-solve dialog (STL_generate.html:28001). */}
      {warnings.length > 0 && (
        <div id="mesh-warn-detail" style={{ marginTop: 4 }}>
          <button
            className="btn"
            id="mesh-warn-toggle"
            onClick={() => setWarnDetail((v) => !v)}
            style={{ fontSize: '0.72rem' }}
          >
            {`${warnDetail ? '▼' : '▶'} ${t(rejected ? 'mesh_warn.title_rejected' : 'mesh_warn.title_warning')} (${warnings.length})`}
          </button>
          {warnDetail && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
              {warnings.map((w, i) => (
                <div
                  key={`${w.code ?? 'warn'}-${i}`}
                  className="prop-hint"
                  style={{
                    textAlign: 'left',
                    padding: '4px 6px',
                    borderLeft: `2px solid ${WARN_COLOR[w.level] ?? '#ffcc00'}`,
                    background: 'var(--bg-input)',
                    lineHeight: 1.5,
                  }}
                >
                  <div
                    style={{
                      color: WARN_COLOR[w.level] ?? '#ffcc00',
                      fontFamily: "'Share Tech Mono',monospace",
                    }}
                  >
                    [{t(WARN_LEVEL_KEY[w.level] ?? 'mesh_warn.level_info')}] {meshWarningLabel(w)}
                  </div>
                  <div>{meshWarningMessage(w)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {/* The checkMesh output itself. The legacy streams these lines into its
          operation log (`_appendDctwinLogLine`, STL_generate.html:25699); here
          they were being collected and never shown, so the numbers behind a
          warning — the actual max skewness — had nowhere to be read. */}
      {job?.kind === 'checkMesh' && job.lines.length > 0 && (
        <pre
          id="check-mesh-log"
          style={{
            marginTop: 8,
            maxHeight: 160,
            overflow: 'auto',
            fontSize: '0.68rem',
            lineHeight: 1.5,
            background: 'var(--bg-input)',
            border: '1px solid var(--border)',
            padding: '6px 8px',
            color: 'var(--log-content)',
            userSelect: 'text',
            whiteSpace: 'pre-wrap',
          }}
        >
          {job.lines.slice(-200).join('\n')}
        </pre>
      )}
      {job?.kind === 'solve' && !job.running && (job.returncode !== null || job.aborted) && (
        <div
          className="prop-hint"
          style={{
            color: job.aborted
              ? 'var(--hint-color)'
              : job.returncode === 0
                ? 'var(--highlight)'
                : '#ff5c72',
          }}
        >
          {job.aborted
            ? t('status.aborted')
            : job.returncode === 0
              ? t('status.solve_ok')
              : t('status.solve_failed', { code: job.returncode ?? '?' })}
        </div>
      )}
      {job?.kind === 'solve' && job.lines.length > 0 && (
        <pre
          style={{
            marginTop: 8,
            maxHeight: 220,
            overflow: 'auto',
            fontSize: '0.68rem',
            lineHeight: 1.5,
            background: 'var(--bg-input)',
            border: '1px solid var(--border)',
            padding: '6px 8px',
            color: 'var(--log-content)',
            userSelect: 'text',
            whiteSpace: 'pre-wrap',
          }}
        >
          {job.lines.slice(-200).join('\n')}
        </pre>
      )}
      {launcher.gate && (
        <MeshQualityDialog
          warnings={launcher.gate}
          rejected={launcher.gateRejected}
          onCancel={launcher.dismiss}
          onContinue={launcher.proceed}
        />
      )}
    </div>
  )
}
