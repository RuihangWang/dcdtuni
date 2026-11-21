import { useMemo } from 'react'
import type { CfdProject, Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import { fromDisplay, stepFor, toDisplay, unitConfig, unitOf } from '../lengthUnits'
import { transformSupport, type TransformScope } from '../interaction/transformSupport'
import { alertIfOutOfBounds } from '../ui/boundsAlert'
import { useTranslation } from '../useTranslation'
import { AnchorMarkerRow } from './ModelDisplayControls'
import { NumberField } from './NumberField'

/**
 * The transform block: the FIRST thing in the right panel for every object.
 *
 * Whatever is selected — a wall, a baffle, a floor, a rack, an ACU, a box, a
 * CDU, a pipe — move and rotate sit in the same place, in the same order, so
 * the hand learns where they are:
 *
 *   1. the move-gizmo toggle        `#model-move-toggle`
 *   2. the rotate-gizmo toggle      `#model-rotate-toggle`
 *      (their axis choices appear under them while the tool is on)
 *   3. the anchor position boxes    `#model-position-{x,y,z}`, with the anchor ball
 *   4. the yaw / pitch / roll boxes `#model-rotation-{yaw,pitch,roll}`
 *
 * This is the legacy's `anchorControlsForModel` (STL_generate.html:9101): ONE
 * block holding the move group, the anchor displacement and the rotation
 * angles, handed to every model panel near its top. The React port had split
 * it — the toggles in one group, the position far below them, the angles
 * further still — and the air pipe had its toggles at the very bottom.
 *
 * A kind without a gizmo drops its rows (`transformSupport`): a T-junction has
 * no rings, an air pipe no anchor boxes, a liquid pipe no block at all. The
 * block still starts at the top.
 *
 * `scope` `part` is the block on a PART of the model — a box face, a rack
 * slot, a wall region: every row the model's own panel has, acting on the
 * whole model, plus a line saying so as the block's LAST row. Last, so no row
 * above it moves: whether the model was picked from its tree row or through a
 * face, each control is at the same place.
 *
 * The move-axis locks show only while move is on, like the rotate axes below
 * them, the legacy block, and the multi-select panel: the locks shape a gizmo
 * that exists only then, and a block at the top of every panel cannot afford
 * four rows that do nothing.
 */
export function TransformSection({
  project,
  model,
  scope,
}: {
  project: CfdProject | null
  model: Construction
  scope: Exclude<TransformScope, 'none'>
}) {
  const { t } = useTranslation()
  const support = transformSupport(model)
  const unit = unitOf(project)
  const unitLabel = unitConfig(project).label
  const step = stepFor(unit)
  const g = model.geometry
  const q = g?.quaternion
  const euler = useMemo(
    () => store.quaternionToEulerDeg(q ?? { x: 0, y: 0, z: 0, w: 1 }),
    [q?.x, q?.y, q?.z, q?.w],
  )

  const moveTool = (model as unknown as {
    moveTool?: { enabled?: boolean; axes?: { x?: boolean; y?: boolean; z?: boolean } }
  }).moveTool
  const moveEnabled = moveTool?.enabled === true
  const moveAxes = moveTool?.axes ?? { x: true, y: true, z: true }
  const rotateTool = (model as unknown as {
    rotateTool?: { enabled?: boolean; axes?: { yaw?: boolean; pitch?: boolean; roll?: boolean } }
  }).rotateTool
  const rotateEnabled = rotateTool?.enabled === true
  const rotateAxes = rotateTool?.axes ?? { yaw: true, pitch: false, roll: false }

  const showPosition = support.position
  const showRotation = support.rotation
  const showGizmos = support.move || support.rotate
  if (!showGizmos && !showPosition && !showRotation) return null

  return (
    <div
      id="transform-section"
      className="transform-section"
      data-transform-scope={scope}
      data-model-uid={model.uid}
    >
      {showGizmos && (
        // The gizmo is per MODEL, not a global mode: locking a rack to the
        // floor plane is a property of that rack, and the .cfdproj stores it.
        <div
          className="prop-group"
          id="transform-gizmo-group"
          style={{ borderLeftColor: 'var(--accent3)' }}
        >
          <div className="prop-title">{t('inspector.move_mode')}</div>
          {support.move && (
            <div className="prop-row">
              <label>{t('inspector.move_btn')}</label>
              <button
                id="model-move-toggle"
                className={`btn frozen-ok${moveEnabled ? ' primary' : ''}`}
                onClick={() => store.setMoveTool(model.uid, { enabled: !moveEnabled })}
              >
                {moveEnabled ? t('inspector.stop_move_btn') : t('inspector.move_btn')}
              </button>
            </div>
          )}
          {/* Turning the rings on drops the move arrows: two gizmos on one
              model would share the same pixels. */}
          {support.rotate && (
            <div className="prop-row">
              <label>{t('btn.enable_rotate')}</label>
              <button
                id="model-rotate-toggle"
                className={`btn frozen-ok${rotateEnabled ? ' primary' : ''}`}
                onClick={() => store.setRotateTool(model.uid, { enabled: !rotateEnabled })}
              >
                {rotateEnabled ? t('btn.stop_rotate') : t('btn.enable_rotate')}
              </button>
            </div>
          )}
          {support.move && moveEnabled && (
            <div className="prop-row" id="model-move-axes">
              <label>{t('inspector.axis_constraint')}</label>
              <div className="transform-axis-choices">
                {(['x', 'y', 'z'] as const).map((axis) => (
                  <label key={axis} htmlFor={`model-move-axis-${axis}`}>
                    <input
                      type="checkbox"
                      id={`model-move-axis-${axis}`}
                      checked={moveAxes[axis] !== false}
                      onChange={(e) =>
                        store.setMoveTool(model.uid, { axes: { [axis]: e.target.checked } })
                      }
                    />
                    {axis.toUpperCase()}
                  </label>
                ))}
              </div>
            </div>
          )}
          {support.rotate && rotateEnabled && (
            <div className="prop-row" id="model-rotate-axes">
              <label>{t('inspector.rotate_axes')}</label>
              <div className="transform-axis-choices">
                {/* The angle boxes' own labels (yaw Y, pitch X, roll Z): each
                    ring turns about the axis its box below writes. */}
                {(
                  [
                    ['yaw', 'inspector.rotation_yaw'],
                    ['pitch', 'inspector.rotation_pitch'],
                    ['roll', 'inspector.rotation_roll'],
                  ] as const
                ).map(([axis, labelKey]) => (
                  <label key={axis} htmlFor={`model-rotate-axis-${axis}`}>
                    <input
                      type="checkbox"
                      id={`model-rotate-axis-${axis}`}
                      checked={rotateAxes[axis] === true}
                      onChange={(e) =>
                        store.setRotateTool(model.uid, { axes: { [axis]: e.target.checked } })
                      }
                    />
                    {t(labelKey)}
                  </label>
                ))}
              </div>
            </div>
          )}
          {/* With no position boxes to sit beside, the anchor ball stays with
              the arrows it is the pivot of. */}
          {support.move && !support.position && <AnchorMarkerRow model={model} />}
        </div>
      )}

      {showPosition && (
        <div
          className="prop-group"
          id="transform-position-group"
          style={{ borderLeftColor: 'var(--accent3)' }}
        >
          <div className="prop-title" style={{ color: 'var(--accent3)' }}>
            {t('inspector.position_title_unit', { unit: unitLabel })}
          </div>
          {/* The ball marks the point these numbers are: a rack's is a bottom
              corner, an ACU's its centre. */}
          <AnchorMarkerRow model={model} />
          {(['x', 'y', 'z'] as const).map((axis) => (
            <div className="prop-row" key={axis}>
              <label htmlFor={`model-position-${axis}`}>
                {axis.toUpperCase()} ({unit})
              </label>
              <NumberField
                id={`model-position-${axis}`}
                value={toDisplay(g?.location?.[axis] ?? 0, unit)}
                step={step}
                // 4 decimals, as the legacy's `formatLength` prints: at mm scale
                // a rack anchor is 1601.0000, and trimming it makes the same
                // field read differently in the two apps.
                decimals={4}
                onCommit={(v) =>
                  alertIfOutOfBounds(
                    store.setGeometryComponent(model.uid, 'location', axis, fromDisplay(v, unit)),
                  )
                }
              />
            </div>
          ))}
          <div className="prop-hint">{t('inspector.position_hint')}</div>
        </div>
      )}

      {showRotation && (
        <div className="prop-group" id="transform-rotation-group">
          <div className="prop-title">{t('inspector.rotation_title')}</div>
          {(
            [
              ['y', 'yaw', 'inspector.rotation_yaw'],
              ['x', 'pitch', 'inspector.rotation_pitch'],
              ['z', 'roll', 'inspector.rotation_roll'],
            ] as const
          ).map(([axis, name, labelKey]) => (
            <div className="prop-row" key={axis}>
              <label htmlFor={`model-rotation-${name}`}>{t(labelKey)}</label>
              <NumberField
                id={`model-rotation-${name}`}
                value={euler[axis]}
                step={15}
                onCommit={(v) => store.setRotationDegrees(model.uid, { ...euler, [axis]: v })}
              />
            </div>
          ))}
          <div className="prop-hint">
            {t('inspector.rotation_hint', {
              quat: q ? `${fmt(q.x)}, ${fmt(q.y)}, ${fmt(q.z)}, ${fmt(q.w)}` : '—',
            })}
          </div>
        </div>
      )}

      {scope === 'part' && (
        <div className="prop-hint transform-parent-hint" id="transform-parent-hint">
          {t('inspector.transform_parent_hint', { name: model.meta?.name ?? model.uid })}
        </div>
      )}
    </div>
  )
}

function fmt(v: number | undefined): string {
  return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(3) : '—'
}
