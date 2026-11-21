import type { CfdProject, Construction } from '../schema/cfdproj'
import {
  acuPerformanceMode,
  acuTable,
  canRemoveTableRow,
  drivingValue,
  modelDrivingValue,
  nextTableRow,
  setAcuDrivingValue,
  setAcuPerformanceMode,
  setAcuTable,
  type AcuDrivingField,
} from '../geometry/acuCoolingModel'
import { solverConfigOf } from '../run/solverConfig'
import { useTranslation } from '../useTranslation'
import { setAcuFrontRadius, setAcuUnitType } from '../geometry/acuUnitType'
import { NumberField } from './NumberField'

/**
 * How an ACU decides what temperature it supplies.
 *
 * `fixedTemp` is the steady answer: the unit blows air at whatever it is told,
 * for ever. The other two are transient models — the unit has a finite
 * capacity and its supply temperature RISES as the room heats up, which is the
 * whole question in a cooling-failure study.
 *
 * `linearCapacity` derives that capacity from one rated point;
 * `tableCapacity` reads it off a curve. Both are disabled under a steady
 * solver, as in the legacy: a capacity model has nothing to do when the answer
 * is a single converged state, and offering it there invites a user to set it
 * and wonder why nothing changed.
 *
 * Ported from `_buildAcuCoolingModelHtml` (STL_generate.html:8653).
 */

export interface AcuCoolingModelProps {
  project: CfdProject | null
  model: Construction
}

export function AcuCoolingModel({ project, model }: AcuCoolingModelProps) {
  const { t } = useTranslation()
  if (model.kind !== 'acu') return null

  const mode = acuPerformanceMode(model)
  const table = acuTable(model)
  const transient = solverConfigOf(project).solverType === 'transient'

  const unitType = String((model as unknown as { cooling?: { unitType?: unknown } }).cooling?.unitType ?? 'floor')
  const radius = Number((model.geometry as { frontRadius?: unknown } | undefined)?.frontRadius) || 0
  return (
    <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
      <div className="prop-title" style={{ color: '#16825d' }}>
        {t('label.cooling_model')}
      </div>
      {/* Floor-standing CRAH (a box) or a wall-mounted split unit — four faces,
          the front turning through a quarter circle into the bottom, with the
          louvred outlet as a patch on that curve. */}
      <div className="prop-row">
        <label htmlFor="acu-unit-type">{t('label.acu_unit_type')}</label>
        <select id="acu-unit-type" value={unitType} onChange={(e) => setAcuUnitType(model.uid, e.target.value)}>
          <option value="floor">{t('option.acu_floor')}</option>
          <option value="wallMounted">{t('option.acu_wall_mounted')}</option>
        </select>
      </div>
      {unitType === 'wallMounted' && (
        <div className="prop-row">
          <label htmlFor="acu-front-radius">{t('label.acu_front_radius')}</label>
          <NumberField
            id="acu-front-radius"
            value={radius}
            step={0.01}
            min={0}
            placeholder="auto"
            onCommit={(v) => setAcuFrontRadius(model.uid, v)}
          />
        </div>
      )}
      {unitType === 'wallMounted' && <div className="prop-hint">{t('hint.acu_wall_mounted')}</div>}
      <div className="prop-row">
        <label htmlFor="acu-cooling-model">{t('label.cooling_model')}</label>
        <select
          id="acu-cooling-model"
          value={mode}
          onChange={(e) => setAcuPerformanceMode(model.uid, e.target.value)}
        >
          <option value="fixedTemp">{t('option.fixed_temp')}</option>
          <option value="linearCapacity" disabled={!transient}>
            {t('option.linear_capacity')}
          </option>
          <option value="tableCapacity" disabled={!transient}>
            {t('option.table_capacity')}
          </option>
        </select>
      </div>
      {!transient && <div className="prop-hint">{t('hint.requires_transient')}</div>}

      {/* The model panel writes every supply opening at once; the per-opening
          boxes are in the patch inspector. */}
      <AcuDrivingInputs model={model} patchId={null} idPrefix="acu-model" />

      {mode === 'tableCapacity' && (
        <>
          <div className="prop-hint">{t('option.table_capacity')}</div>
          <div className="prop-row">
            <label className="dim">{t('label.perf_table_return')}</label>
            <label className="dim">{t('label.perf_table_capacity')}</label>
          </div>
          {table.map((row, i) => (
            <div className="prop-row" key={i}>
              <NumberField
                id={`acu-table-${i}-temp`}
                value={row.returnTemp}
                step={1}
                width={70}
                onCommit={(v) =>
                  setAcuTable(model.uid, table.map((r, k) => (k === i ? { ...r, returnTemp: v } : r)))
                }
              />
              <NumberField
                id={`acu-table-${i}-capacity`}
                value={row.capacity}
                step={1}
                width={70}
                onCommit={(v) =>
                  setAcuTable(model.uid, table.map((r, k) => (k === i ? { ...r, capacity: v } : r)))
                }
              />
              <button
                className="node-action-btn"
                title={t('tooltip.delete')}
                // Two points are the fewest a curve can be read off; the legacy
                // refuses below that rather than leaving a lookup with nothing
                // to interpolate between.
                disabled={!canRemoveTableRow(table)}
                onClick={() => setAcuTable(model.uid, table.filter((_, k) => k !== i))}
              >
                ✕
              </button>
            </div>
          ))}
          <button
            className="btn"
            id="acu-table-add"
            onClick={() => setAcuTable(model.uid, [...table, nextTableRow(table)])}
          >
            ＋
          </button>
        </>
      )}
    </div>
  )
}

/**
 * The inputs the capacity models are driven by.
 *
 * Rendered in two places for the two scopes the values have: with `patchId`
 * null it reads and writes every supply opening on the unit (the model panel),
 * and with one set it edits that opening alone (the patch inspector, which is
 * where the legacy puts these). `idPrefix` keeps the two sets of DOM ids apart
 * so both can be on screen without colliding.
 *
 * `linearCapacity` needs all four; `tableCapacity` reads its duty off the curve
 * instead, so it needs only the set point and the coolant temperature — the
 * legacy splits them the same way (STL_generate.html:8666 and :8676).
 */
export function AcuDrivingInputs({
  model,
  patchId,
  physics,
  idPrefix,
}: {
  model: Construction
  patchId: string | null
  /** The patch's own physics, when the caller already has it. */
  physics?: Record<string, unknown>
  idPrefix: string
}) {
  const { t } = useTranslation()
  const mode = acuPerformanceMode(model)
  if (mode === 'fixedTemp') return null

  const read = (field: AcuDrivingField) =>
    patchId === null ? modelDrivingValue(model, field) : drivingValue(physics, field)
  const write = (field: AcuDrivingField, value: number) =>
    setAcuDrivingValue(model.uid, field, value, patchId)

  const row = (field: AcuDrivingField, label: string, step: number) => (
    <div className="prop-row">
      <label htmlFor={`${idPrefix}-${field}`}>{label}</label>
      <NumberField
        id={`${idPrefix}-${field}`}
        value={read(field)}
        step={step}
        onCommit={(v) => write(field, v)}
      />
    </div>
  )

  return (
    <>
      {/* The legacy labels these two in English inline rather than through the
          dictionary (STL_generate.html:8666, :8669); kept verbatim so the two
          apps read the same in both locales. */}
      {row('setPointTemperature', 'Set Point (°C)', 0.5)}
      {mode === 'linearCapacity' && (
        <>
          {row('coolingCapacity', t('label.rated_cooling'), 1)}
          {row('ratedReturnTemp', t('label.rated_return_temp'), 0.5)}
        </>
      )}
      {row('coolantTemperature', 'Coolant T (°C)', 0.5)}
    </>
  )
}
