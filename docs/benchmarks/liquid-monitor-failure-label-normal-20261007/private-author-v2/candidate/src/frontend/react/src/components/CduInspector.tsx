import { useMemo } from 'react'
import { allModels, type CfdProject, type Construction } from '../schema/cfdproj'
import { cduConnectedPipes } from '../liquid/cduPipes'
import { fromDisplay, stepFor, toDisplay, unitConfig, unitOf } from '../lengthUnits'
import * as store from '../projectStore'
import { requestSelect } from '../selectionBus'
import { useTranslation } from '../useTranslation'
import { alertIfOutOfBounds } from '../ui/boundsAlert'
import { NumberField } from './NumberField'
import { KeyframeBadge } from '../transient/KeyframeBadge'

/**
 * The CDU panel — a liquid loop's source.
 *
 * Ports `renderCduModelInspector` (STL_generate.html:8499). A CDU is not a box
 * with extra fields: its supply temperature, pressure and total flow are what
 * the liquid solver integrates from, and the racks it serves define the loop's
 * topology. Falling through to the generic model panel would leave all of that
 * uneditable while looking complete.
 */

type Bag = Record<string, unknown>

export function CduProperties({ project, model }: { project: CfdProject; model: Construction }) {
  const { t } = useTranslation()
  const unit = unitOf(project)
  const step = stepFor(unit)
  const unitLabel = unitConfig(project).label
  const cooling = ((model as unknown as Bag).cooling ?? {}) as Bag
  const meta = ((model as unknown as Bag).meta ?? {}) as Bag
  const served = new Set((meta.racks ?? []) as string[])

  // Only ENABLED racks are offered: a disabled rack is not in the case, and a
  // loop that serves one would compile to a branch with nothing on its end.
  const racks = useMemo(
    () =>
      allModels(project)
        .map(({ model: m }) => m)
        .filter((m) => m.kind === 'rack' && m.meta?.enabled !== false),
    [project],
  )

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: '#00b4dc' }}>
        <div className="prop-title" style={{ color: '#00b4dc' }}>
          🌊 CDU — Cooling Distribution Unit
        </div>
        <div className="prop-row">
          <label htmlFor="cdu-name">{t('label.model_name')}</label>
          <input
            id="cdu-name"
            type="text"
            defaultValue={model.meta?.name ?? ''}
            key={model.uid}
            onBlur={(e) => {
              if (e.target.value !== model.meta?.name) store.renameModel(model.uid, e.target.value)
            }}
          />
        </div>
      </div>

      {/* In the PROJECT's unit, like every other length box. Hard-coded metres
          here meant a CDU in a millimetre project offered `0.8` beside racks
          reading `600`, and typing `800` into it built an 800-metre cabinet.
          The legacy renders these through `formatLength`/`getLengthStep`
          (STL_generate.html:8513). */}
      <div className="prop-group">
        <div className="prop-title">{t('inspector.parametric_size', { unit: unitLabel })}</div>
        {([
          ['x', 'label.envelope_w'],
          ['z', 'label.envelope_d'],
          ['y', 'label.envelope_h'],
        ] as const).map(([axis, labelKey]) => (
          <div className="prop-row" key={axis}>
            <label htmlFor={`cdu-size-${axis}`}>{t(labelKey)}</label>
            <NumberField
              id={`cdu-size-${axis}`}
              value={toDisplay(Number(model.geometry?.size?.[axis] ?? 0), unit)}
              step={step}
              min={toDisplay(0.01, unit)}
              onCommit={(v) =>
                alertIfOutOfBounds(
                  store.setGeometryComponent(model.uid, 'size', axis, fromDisplay(v, unit)),
                )
              }
            />
          </div>
        ))}
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#00b4dc' }}>
        <div className="prop-title" style={{ color: '#00b4dc' }}>
          {t('liquid.cooling_params_title')}
        </div>
        {/* .liquidrun v4 endpoint forms: how the secondary supply and the
            pump are held (static; both need the DAE solver). */}
        <div className="prop-row">
          <label htmlFor="cdu-secondary-control">{t('liquid.secondary_control')}</label>
          <select
            id="cdu-secondary-control"
            value={cooling.secondaryControl === 'setpoint' ? 'setpoint' : 'physics'}
            onChange={(e) => store.setModelCooling(model.uid, 'secondaryControl', e.target.value)}
          >
            <option value="physics">{t('liquid.secondary_control_physics')}</option>
            <option value="setpoint">{t('liquid.secondary_control_setpoint')}</option>
          </select>
        </div>
        <div className="prop-row">
          <label htmlFor="cdu-pump-control">{t('liquid.pump_control')}</label>
          <select
            id="cdu-pump-control"
            value={cooling.pumpControl === 'flow' ? 'flow' : 'curve'}
            onChange={(e) => store.setModelCooling(model.uid, 'pumpControl', e.target.value)}
          >
            <option value="curve">{t('liquid.pump_control_curve')}</option>
            <option value="flow">{t('liquid.pump_control_flow')}</option>
          </select>
        </div>
        <div className="prop-hint">{t('liquid.v4_mode_hint')}</div>
        <div className="prop-row kf-field">
          <label htmlFor="cdu-supply-temp">{t('liquid.supply_temp')}<KeyframeBadge modelUid={model.uid} param="cdu.secondarySupplyT" target="" /></label>
          <NumberField
            id="cdu-supply-temp"
            value={Number(cooling.supplyTemperatureC ?? 35)}
            step={0.5}
            onCommit={(v) => store.setCduLiquidParam(model.uid, 'supplyTemperature', v)}
          />
        </div>
        <div className="prop-row">
          <label htmlFor="cdu-supply-pressure">{t('liquid.supply_pressure')}</label>
          <NumberField
            id="cdu-supply-pressure"
            value={Number(cooling.supplyPressureKpa ?? 250)}
            step={10}
            min={100}
            onCommit={(v) => store.setCduLiquidParam(model.uid, 'supplyPressure', v)}
          />
        </div>
        {/* Fixed, and stated: the return side is atmospheric, so the pump curve
            is read against a constant — an editable box here would suggest a
            degree of freedom the solver does not have. */}
        <div className="prop-hint">Return pressure: 101.325 kPa (atmospheric, fixed)</div>
        <div className="prop-row kf-field">
          <label htmlFor="cdu-flow">{t('liquid.flow_rate')}<KeyframeBadge modelUid={model.uid} param="pump.flow" target="" /></label>
          <NumberField
            id="cdu-flow"
            value={Number(cooling.totalFlowRate ?? 60)}
            step={1}
            min={0}
            onCommit={(v) => store.setCduLiquidParam(model.uid, 'totalFlowRate', v)}
          />
        </div>

        <div className="prop-row">
          <label>{t('liquid.cdu_served_racks')}</label>
          <span className="badge">{served.size}</span>
        </div>
        {racks.length === 0 ? (
          <div className="prop-hint">No rack models in scene</div>
        ) : (
          racks.map((rack) => {
            const name = rack.meta?.name ?? rack.uid
            return (
              <div className="prop-row" key={rack.uid}>
                <label htmlFor={`cdu-rack-${rack.uid}`}>{name}</label>
                <input
                  type="checkbox"
                  id={`cdu-rack-${rack.uid}`}
                  checked={served.has(name)}
                  onChange={(e) => store.setCduServedRack(model.uid, name, e.target.checked)}
                />
              </div>
            )
          })
        )}
      </div>

      <ConnectedPipes project={project} cduUid={model.uid} />

      <div className="prop-group" style={{ borderLeftColor: '#e06c75' }}>
        <button
          className="btn danger"
          id="cdu-delete"
          onClick={() => store.removeModel(model.uid)}
        >
          {t('liquid.delete_object')}
        </button>
      </div>
    </>
  )
}

/**
 * The supply and return pipes plugged into this CDU.
 *
 * Ports `_buildCduConnectedPipesHtml` (STL_generate.html:8457). Each row
 * selects its pipe, which is the only way to reach a pipe that is buried under
 * the geometry — the legacy calls `selectModelInScene` from the same rows.
 */
function ConnectedPipes({ project, cduUid }: { project: CfdProject; cduUid: string }) {
  const { t } = useTranslation()
  const groups = useMemo(() => cduConnectedPipes(project, cduUid), [project, cduUid])

  const list = (labelKey: 'liquid.cdu_supply_pipes' | 'liquid.cdu_return_pipes', pipes: Construction[], color: string) =>
    pipes.length === 0 ? null : (
      <div key={labelKey}>
        <div className="prop-hint" style={{ color, fontWeight: 600 }}>
          {t(labelKey)} ({pipes.length})
        </div>
        {pipes.map((pipe) => (
          <div
            key={pipe.uid}
            className="cdu-pipe-row"
            data-pipe={pipe.uid}
            style={{ padding: '2px 16px', fontSize: 11, cursor: 'pointer' }}
            onClick={() => requestSelect(pipe.uid)}
          >
            🔗 {pipe.meta?.name ?? pipe.uid}
          </div>
        ))}
      </div>
    )

  return (
    <div className="prop-group" id="cdu-connected-pipes" style={{ borderLeftColor: '#26C6DA' }}>
      <div className="prop-title" style={{ color: '#26C6DA' }}>
        {t('liquid.cdu_connected_pipes')}{' '}
        <span className="badge" id="cdu-pipe-count">
          {groups.total}
        </span>
      </div>
      {groups.total === 0 ? (
        <div className="prop-hint">{t('liquid.cdu_no_pipes')}</div>
      ) : (
        <>
          {list('liquid.cdu_supply_pipes', groups.supply, '#26C6DA')}
          {list('liquid.cdu_return_pipes', groups.returns, '#EF5350')}
        </>
      )}
    </div>
  )
}
