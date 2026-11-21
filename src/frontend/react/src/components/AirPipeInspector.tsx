import type { CfdProject, Construction } from '../schema/cfdproj'
import { Icon } from './Icon'
import { fromDisplay, LENGTH_UNITS, stepFor, toDisplay, unitOf } from '../lengthUnits'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { ModelDisplayGroup } from './ModelDisplayControls'
import { NumberField } from './NumberField'
import {
  airPipeGeometry,
  airPipeWorldPoints,
  deleteAirPipePoint,
  detachAirPipeEnd,
  insertAirPipePointAfter,
  setAirPipeDiameter,
  setAirPipePointWorld,
  setAirPipeShape,
  setAirPipeSize,
  type PipeShape,
} from '../geometry/airPipe'

/**
 * The air-pipe panel: cross-section, bore, and the polyline in WORLD
 * coordinates.
 *
 * The waypoints are editable here, unlike a liquid pipe's, because an air duct
 * is routed to a drawing — "150 mm up from the unit's left flange to 2.4 m" —
 * and typing that is faster than dragging to it. Dragging stays the way to
 * JOIN two pipes: a typed coordinate that lands on another pipe's end is close
 * in the picture and unjoined in the file, so the panel says which ends are
 * joined and offers to break them, and nothing else.
 */
export function AirPipeInspector({ project, model }: { project: CfdProject; model: Construction }) {
  const { t } = useTranslation()
  const g = airPipeGeometry(model)
  const unit = unitOf(project)
  const unitLabel = LENGTH_UNITS[unit].label
  const decimals = Math.max(3, LENGTH_UNITS[unit].precision)
  const points = airPipeWorldPoints(model)
  const nameOf = (uid: string) => store.findConstruction(project, uid)?.model.meta?.name ?? uid

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: '#b8bec6' }}>
        <div className="prop-title" style={{ color: '#b8bec6' }}>
          {t('pipe.air_title')}
        </div>
        <div className="prop-row">
          <label htmlFor="air-pipe-name">{t('liquid.pipe_name')}</label>
          <input
            id="air-pipe-name"
            type="text"
            defaultValue={model.meta?.name ?? ''}
            key={model.uid}
            onBlur={(e) => {
              if (e.target.value !== model.meta?.name) store.renameModel(model.uid, e.target.value)
            }}
          />
        </div>
        <div className="prop-row">
          <label htmlFor="air-pipe-shape">{t('pipe.shape')}</label>
          <select
            id="air-pipe-shape"
            value={g.shape}
            onChange={(e) => setAirPipeShape(model.uid, e.target.value as PipeShape)}
          >
            <option value="round">{t('pipe.shape_round')}</option>
            <option value="rect">{t('pipe.shape_rect')}</option>
          </select>
        </div>
        {g.shape === 'round' ? (
          <div className="prop-row">
            <label htmlFor="air-pipe-diameter">{`${t('pipe.diameter')} (${unitLabel})`}</label>
            <NumberField
              id="air-pipe-diameter"
              value={toDisplay(g.diameterM, unit)}
              step={stepFor(unit)}
              decimals={decimals}
              onCommit={(v) => setAirPipeDiameter(model.uid, fromDisplay(v, unit))}
            />
          </div>
        ) : (
          <>
            <div className="prop-row">
              <label htmlFor="air-pipe-width">{`${t('pipe.width')} (${unitLabel})`}</label>
              <NumberField
                id="air-pipe-width"
                value={toDisplay(g.widthM, unit)}
                step={stepFor(unit)}
                decimals={decimals}
                onCommit={(v) => setAirPipeSize(model.uid, { widthM: fromDisplay(v, unit) })}
              />
            </div>
            <div className="prop-row">
              <label htmlFor="air-pipe-height">{`${t('pipe.height')} (${unitLabel})`}</label>
              <NumberField
                id="air-pipe-height"
                value={toDisplay(g.heightM, unit)}
                step={stepFor(unit)}
                decimals={decimals}
                onCommit={(v) => setAirPipeSize(model.uid, { heightM: fromDisplay(v, unit) })}
              />
            </div>
          </>
        )}
        <div className="hint" style={{ fontSize: 11, opacity: 0.8, padding: '4px 0' }}>
          {t('pipe.hint_drag')}
        </div>
      </div>

      <div className="prop-group">
        <div className="prop-title">{`${t('pipe.waypoints_title')} (${unitLabel})`}</div>
        {points.map((p, i) => {
          const end = i === 0 ? 'start' : i === points.length - 1 ? 'end' : null
          const join = end ? g.joins.find((j) => j.end === end) : null
          const color = end === 'start' ? '#2196F3' : end === 'end' ? '#F44336' : '#FFEB3B'
          return (
            <div key={i} className="air-pipe-point" data-index={i} style={{ marginBottom: 6 }}>
              <div className="prop-row" style={{ alignItems: 'center' }}>
                <label style={{ color }}>● {t('pipe.point_n', { n: i + 1 })}</label>
                <span style={{ display: 'flex', gap: 4 }}>
                  <button
                    className="btn"
                    id={`air-pipe-insert-${i}`}
                    title={t('pipe.add_point_after')}
                    onClick={() => insertAirPipePointAfter(model.uid, i)}
                  >
                    ＋
                  </button>
                  <button
                    className="btn"
                    id={`air-pipe-delete-${i}`}
                    title={t('pipe.delete_point')}
                    disabled={points.length <= 2}
                    onClick={() => deleteAirPipePoint(model.uid, i)}
                  >
                    －
                  </button>
                </span>
              </div>
              {(['x', 'y', 'z'] as const).map((axis) => (
                <div className="prop-row" key={axis}>
                  <label htmlFor={`air-pipe-wp-${i}-${axis}`}>{axis.toUpperCase()}</label>
                  <NumberField
                    id={`air-pipe-wp-${i}-${axis}`}
                    value={toDisplay(p[axis], unit)}
                    step={stepFor(unit)}
                    decimals={decimals}
                    onCommit={(v) =>
                      setAirPipePointWorld(model.uid, i, { ...p, [axis]: fromDisplay(v, unit) })
                    }
                  />
                </div>
              ))}
              {join && (
                <div className="prop-row">
                  <span style={{ fontSize: 11, color: '#00e5ff' }} id={`air-pipe-join-${end}`}>
                    {t('pipe.joined_with', { name: nameOf(join.pipeUid) })}
                  </span>
                  <button
                    className="btn"
                    id={`air-pipe-unjoin-${end}`}
                    onClick={() => detachAirPipeEnd(model.uid, end!)}
                  >
                    <Icon name="slice" />
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* The move and rotate toggles are in the transform block at the TOP of
          the panel, where every other kind has them — no longer down here
          under the waypoint list. */}
      <ModelDisplayGroup model={model} />

      <div className="prop-group" style={{ borderLeftColor: '#e06c75' }}>
        <button className="btn danger" id="air-pipe-delete" onClick={() => store.removeModel(model.uid)}>
          {t('pipe.delete')}
        </button>
      </div>
    </>
  )
}
