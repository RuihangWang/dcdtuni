import { useEffect } from 'react'
import type { CfdProject, Construction, Vec3 } from '../schema/cfdproj'
import { fromDisplay, LENGTH_UNITS, stepFor, toDisplay, unitOf } from '../lengthUnits'
import { pipeDiameterStated, pipeSummary, setPipeDiameter, setValveOpening, valveSummary } from '../liquid/pipeInspector'
import { KeyframeBadge } from '../transient/KeyframeBadge'
import type { TranslationKey } from '../i18n'
import * as store from '../projectStore'
import type { TreeSelection } from '../interaction/treeNodes'
import { useTranslation } from '../useTranslation'
import { ModelDisplayGroup } from './ModelDisplayControls'
import { NumberField } from './NumberField'

/**
 * The pipe-segment panel.
 *
 * Ports `renderPipeSegmentInspector` (STL_generate.html:8546). Without it a
 * selected pipe fell through to the generic model panel and was shown three
 * size boxes — W, D and H of a `geometry.size` that is `{0,0,0}` for every pipe
 * ever drawn. So the panel described nothing and editing it changed nothing,
 * while the one number that matters, the bore, had no control at all: the
 * liquid solver builds each pipe's hydraulic resistance from
 * `geometry.diameterM`, so a network left at its default bore solves the wrong
 * pressure drop and reports plausible flows for a system nobody built.
 *
 * The two endpoints are READ-ONLY here, as they are in the legacy: a waypoint is
 * moved by dragging its control point in the viewport, where it can snap to a
 * port, and a typed coordinate would land next to the port instead of on it —
 * connected in the picture and unconnected in the file.
 */

const VALVE_TYPE_KEY: Record<string, TranslationKey> = {
  flow_control: 'lq_ux.valve_type_flow_control',
  isolation: 'lq_ux.valve_type_isolation',
  tee_junction: 'lq_ux.valve_type_tee_junction',
}

function EndpointRows({
  prefix,
  point,
  labels,
  unit,
}: {
  prefix: string
  point: Vec3
  labels: [string, string, string]
  unit: ReturnType<typeof unitOf>
}) {
  const precision = LENGTH_UNITS[unit].precision
  return (
    <>
      {(['x', 'y', 'z'] as const).map((axis, i) => (
        <div className="prop-row" key={axis}>
          <label>{labels[i]}</label>
          <span id={`${prefix}-${axis}`}>{toDisplay(point[axis], unit).toFixed(precision)}</span>
        </div>
      ))}
    </>
  )
}

export function PipeSegmentInspector({
  project,
  model,
  node,
}: {
  project: CfdProject
  model: Construction
  /** The tree selection under the pipe, when a fitting row made one. */
  node?: TreeSelection | null
}) {
  const { t } = useTranslation()
  const summary = pipeSummary(model)
  const unit = unitOf(project)
  const unitLabel = LENGTH_UNITS[unit].label

  // Selecting a fitting in the tree selects the pipe — that part is the tree's
  // generic behaviour — and then has to reach into the SCENE to highlight the
  // valve mesh, which only `js/liquid_mode.js` can do. The legacy does the same
  // two-step through `window._selectFittingById` (STL_generate.html:12908).
  const fittingId = node?.type === 'fitting' && node.uid === model.uid ? node.fittingId : undefined
  useEffect(() => {
    if (!fittingId) return
    const w = window as unknown as {
      _selectFittingById?: (pipeUid: string, valveId: string) => void
    }
    w._selectFittingById?.(model.uid, fittingId)
  }, [fittingId, model.uid])

  const valve = fittingId ? valveSummary(model, fittingId) : null
  const valveRack = valve?.connectedRackId ? store.findConstruction(project, valve.connectedRackId)?.model : null

  return (
    <>
      {valve && (
        <div className="prop-group" id="valve-inspector" data-valve={valve.id} style={{ borderLeftColor: '#c678dd' }}>
          <div className="prop-title" style={{ color: '#c678dd' }}>{t('lq_ux.valve_title', { name: valve.name })}</div>
          <div className="prop-row">
            <label>{t('lq_ux.valve_type')}</label>
            <span id="valve-type">{VALVE_TYPE_KEY[valve.type] ? t(VALVE_TYPE_KEY[valve.type]!) : valve.type}</span>
          </div>
          {valve.adjustable ? (
            <>
              <div className="prop-row kf-field">
                <label htmlFor="valve-opening">{t('lq_ux.valve_opening')}<KeyframeBadge modelUid={model.uid} param="valve.opening" target={valve.id} /></label>
                <NumberField
                  id="valve-opening"
                  value={valve.opening}
                  step={0.05}
                  min={0.001}
                  max={1}
                  onCommit={(v) => setValveOpening(model.uid, valve.id, v)}
                />
              </div>
              <div className="prop-hint">{t('lq_ux.valve_opening_hint')}</div>
            </>
          ) : (
            <div className="prop-hint">{t('lq_ux.valve_not_adjustable')}</div>
          )}
          <div className="prop-row">
            <label>{t('lq_ux.valve_rack')}</label>
            <span id="valve-rack">{valveRack ? valveRack.meta?.name ?? valveRack.uid : t('lq_ux.valve_rack_none')}</span>
          </div>
        </div>
      )}
      <div className="prop-group" style={{ borderLeftColor: '#00b4dc' }}>
        <div className="prop-title" style={{ color: '#00b4dc' }}>
          {t('pipe.segment_title')}
        </div>
        <div className="prop-row">
          <label htmlFor="pipe-name">{t('liquid.pipe_name')}</label>
          <input
            id="pipe-name"
            type="text"
            defaultValue={model.meta?.name ?? ''}
            key={model.uid}
            onBlur={(e) => {
              if (e.target.value !== model.meta?.name) store.renameModel(model.uid, e.target.value)
            }}
          />
        </div>
        <div className="prop-row">
          <label>{t('pipe.color_label')}</label>
          <span id="pipe-loop-role" style={{ fontSize: 12, color: summary.loopRoleColor }}>
            {t(summary.loopRoleKey)}
          </span>
        </div>
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('pipe.anchor_title', { unit: unitLabel })}</div>
        <EndpointRows
          prefix="pipe-start"
          point={summary.start}
          unit={unit}
          labels={[t('pipe.start_x'), t('pipe.start_y'), t('pipe.start_z')]}
        />
        <EndpointRows
          prefix="pipe-end"
          point={summary.end}
          unit={unit}
          labels={[t('pipe.end_x'), t('pipe.end_y'), t('pipe.end_z')]}
        />
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('pipe.params_title')}</div>
        <div className="prop-row">
          <label htmlFor="pipe-diameter">{`${t('liquid.pipe_diameter')} (${unitLabel})`}</label>
          {/* 4 decimals, the same `formatLength` rendering the endpoints use,
              so a 100 mm bore reads 100.0000 in both apps. The legacy's
              `min="1"` is NOT carried: it is unit-blind, and in metres it would
              refuse every pipe narrower than a metre. `setPipeDiameter` is the
              real guard, and it rejects anything a resistance cannot divide
              by. */}
          <NumberField
            id="pipe-diameter"
            value={toDisplay(summary.diameterM, unit)}
            step={stepFor(unit)}
            decimals={4}
            onCommit={(v) => setPipeDiameter(model.uid, fromDisplay(v, unit))}
          />
        </div>
        {!pipeDiameterStated(model) && <div className="prop-hint" id="pipe-diameter-default">{t('lq_ux.pipe_diameter_default')}</div>}
        <div className="prop-row">
          <label>{t('pipe.waypoints_label')}</label>
          <span className="badge" id="pipe-waypoint-count">
            {summary.waypointCount}
          </span>
        </div>
        {/* Conditional, as in the legacy: an absent row says "not connected"
            more clearly than an empty one, which reads as a value that failed
            to load. */}
        {summary.startPortId && (
          <div className="prop-row">
            <label>{t('pipe.start_connection')}</label>
            <span id="pipe-start-port" style={{ fontSize: 11, color: '#2196F3' }}>
              {summary.startPortId}
            </span>
          </div>
        )}
        {summary.endPortId && (
          <div className="prop-row">
            <label>{t('pipe.end_connection')}</label>
            <span id="pipe-end-port" style={{ fontSize: 11, color: '#F44336' }}>
              {summary.endPortId}
            </span>
          </div>
        )}
      </div>

      <ModelDisplayGroup model={model} />

      <div className="prop-group" style={{ borderLeftColor: '#e06c75' }}>
        <button className="btn danger" id="pipe-delete" onClick={() => store.removeModel(model.uid)}>
          {t('liquid.delete_pipe')}
        </button>
      </div>
    </>
  )
}
