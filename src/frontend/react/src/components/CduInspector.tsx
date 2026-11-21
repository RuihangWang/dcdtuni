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
import { domainModesOf } from '../transient/model'
import { readFluid } from '../liquid/liquidConfig'
import { cduOverview } from '../liquid/liquidOverview'
import type { HxModel } from '../liquid/dynamicLiquidGraph'
import {
  cduCooling, cduFieldUse, DEFAULT_PRIMARY_FLOW_KG_S, DEFAULT_PRIMARY_INLET_C, DEFAULT_RETURN_PRESSURE_KPA,
  effectiveHxModel, kgSToLpm, lpmToKgS, savedPrimaryCircuit, setHxModel, setPrimaryInput, setPumpSpeedRatio, setReturnPressure,
} from '../liquid/cduInputs'

/**
 * The CDU panel — a liquid loop's source.
 *
 * Every field says what the DAE does with it (cduInputs.ts): the return side
 * is the pressure reference, the supply pressure is legacy-only, the supply
 * temperature and total flow are set points only in their modes, and the
 * primary side, pump speed and HX model are editable for a steady solve.
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
  const cooling = cduCooling(model)
  const meta = ((model as unknown as Bag).meta ?? {}) as Bag
  const served = new Set((meta.racks ?? []) as string[])
  const use = cduFieldUse(cooling)
  const rho = readFluid(project).rho
  const flowLpm = Number(cooling.totalFlowRate ?? 60)
  const pumpRatio = Number(((model as unknown as { constructions?: { pump?: { rpmRatio?: number } } }).constructions?.pump?.rpmRatio) ?? 1)
  const returnKpa = Number(cooling.returnPressureKpa ?? DEFAULT_RETURN_PRESSURE_KPA)
  const transient = domainModesOf(project).liquid === 'transient'
  const circuit = savedPrimaryCircuit(project, model.uid)
  const primaryRho = Number(circuit?.rho_kg_m3 ?? rho)
  const primaryInletC = Number(cooling.primaryInletTemperatureC ??
    (typeof circuit?.inlet_K === 'number' ? circuit.inlet_K - 273.15 : DEFAULT_PRIMARY_INLET_C))
  const primaryFlow = Number(cooling.primaryFlowKgS ?? (typeof circuit?.flow_kg_s === 'number' ? circuit.flow_kg_s : DEFAULT_PRIMARY_FLOW_KG_S))
  const hxModel = effectiveHxModel(cooling)
  // With pipes, which racks a CDU serves FOLLOWS from the pipes and valves
  // (liquidOverview.cduOverview); meta.racks is a pre-pipe declaration the
  // compiler never reads, so it is shown read-only rather than as a switch.
  const hasPipeTopology = Object.keys(project.constructions?.pipeSegments ?? {}).length > 0
  const derived = hasPipeTopology ? cduOverview(project).find((row) => row.uid === model.uid)?.served ?? '' : ''
  const dim = { opacity: 0.55 }

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
        <div className="prop-title" style={{ color: '#00b4dc' }} id="cdu-title">
          {t('lq_ux.cdu_title')}
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
        {/* A field the DAE does not read in the chosen mode is shown, greyed,
            with the reason — not hidden, so the value survives a mode switch. */}
        <div className="prop-row kf-field" style={use.supplyTemperature ? undefined : dim} data-cdu-field="supplyTemperature" data-used={use.supplyTemperature}>
          <label htmlFor="cdu-supply-temp">{t('liquid.supply_temp')}<KeyframeBadge modelUid={model.uid} param="cdu.secondarySupplyT" target="" /></label>
          <NumberField
            id="cdu-supply-temp"
            value={Number(cooling.supplyTemperatureC ?? 35)}
            step={0.5}
            disabled={!use.supplyTemperature}
            onCommit={(v) => store.setCduLiquidParam(model.uid, 'supplyTemperature', v)}
          />
        </div>
        {!use.supplyTemperature && <div className="prop-hint" id="cdu-supply-temp-reason">{t('lq_ux.cdu_supply_temp_disabled')}</div>}
        <div className="prop-row kf-field" style={use.totalFlow ? undefined : dim} data-cdu-field="totalFlow" data-used={use.totalFlow}>
          <label htmlFor="cdu-flow">{t('liquid.flow_rate')}<KeyframeBadge modelUid={model.uid} param="pump.flow" target="" /></label>
          <NumberField
            id="cdu-flow"
            value={flowLpm}
            step={1}
            min={0}
            disabled={!use.totalFlow}
            onCommit={(v) => store.setCduLiquidParam(model.uid, 'totalFlowRate', v)}
          />
        </div>
        <div className="prop-hint" id="cdu-flow-kgs">
          {t('lq_ux.cdu_flow_kgs', { kgs: lpmToKgS(flowLpm, rho).toFixed(3), rho: Number(rho.toFixed(1)) })}
          {!use.totalFlow && <><br />{t('lq_ux.cdu_flow_disabled')}</>}
        </div>
        <div className="prop-row kf-field" style={use.totalFlow ? dim : undefined}>
          <label htmlFor="cdu-pump-speed">{t('lq_ux.cdu_pump_speed')}<KeyframeBadge modelUid={model.uid} param="pump.rpm" target="" /></label>
          <NumberField
            id="cdu-pump-speed"
            value={pumpRatio}
            step={0.05}
            min={0}
            max={2}
            disabled={use.totalFlow}
            onCommit={(v) => setPumpSpeedRatio(model.uid, v)}
          />
        </div>
        <div className="prop-hint">{t('lq_ux.cdu_pump_speed_hint')}</div>
        <div className="prop-row kf-field">
          <label htmlFor="cdu-return-pressure">{t('lq_ux.cdu_return_pressure')}<KeyframeBadge modelUid={model.uid} param="cdu.returnPressure" target="" /></label>
          <NumberField
            id="cdu-return-pressure"
            value={returnKpa}
            step={10}
            min={1}
            max={2000}
            onCommit={(v) => setReturnPressure(model.uid, v)}
          />
        </div>
        <div className="prop-hint">{t('lq_ux.cdu_return_pressure_hint')}</div>
        <div className="prop-row">
          <label htmlFor="cdu-hx-model">{t('lq_ux.cdu_hx_model')}</label>
          <select
            id="cdu-hx-model"
            value={hxModel}
            onChange={(e) => setHxModel(model.uid, e.target.value as HxModel)}
          >
            <option value="counterflow-ntu">{t('lq_ux.cdu_hx_counterflow')}</option>
            <option value="mixed-volumes">{t('lq_ux.cdu_hx_mixed')}</option>
          </select>
        </div>
        {cooling.hxModel === undefined && <div className="prop-hint" id="cdu-hx-unset">{t('lq_ux.cdu_hx_unset')}</div>}
        <div className="prop-hint">{t('lq_ux.cdu_hx_hint')}</div>
        <details className="lc-details" id="cdu-legacy-params">
          <summary>{t('lq_ux.cdu_supply_pressure_legacy')}</summary>
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
          <div className="prop-hint">{t('lq_ux.cdu_supply_pressure_hint')}</div>
        </details>
      </div>

      <div className="prop-group" id="cdu-primary" style={{ borderLeftColor: '#61afef' }}>
        <div className="prop-title" style={{ color: '#61afef' }}>{t('lq_ux.cdu_primary_title')}</div>
        <div className="prop-row kf-field">
          <label htmlFor="cdu-primary-inlet">{t('lq_ux.cdu_primary_inlet')}<KeyframeBadge modelUid={model.uid} param="cdu.primaryInletT" target="" /></label>
          <NumberField
            id="cdu-primary-inlet"
            value={Number(primaryInletC.toFixed(4))}
            step={0.5}
            min={0}
            max={80}
            onCommit={(v) => setPrimaryInput(model.uid, 'inletC', v)}
          />
        </div>
        {/* Typed in L/min like every liquid flow in the editor; stored in
            kg/s, the registry's and the .liquidrun circuit's unit. */}
        <div className="prop-row kf-field">
          <label htmlFor="cdu-primary-flow">{t('lq_ux.cdu_primary_flow')}<KeyframeBadge modelUid={model.uid} param="cdu.primaryFlow" target="" /></label>
          <NumberField
            id="cdu-primary-flow"
            value={Number(kgSToLpm(primaryFlow, primaryRho).toFixed(3))}
            step={1}
            min={0}
            onCommit={(v) => setPrimaryInput(model.uid, 'flowKgS', lpmToKgS(v, primaryRho))}
          />
        </div>
        <div className="prop-hint" id="cdu-primary-flow-kgs">{t('lq_ux.cdu_flow_kgs', { kgs: primaryFlow.toFixed(3), rho: Number(primaryRho.toFixed(1)) })}</div>
        <div className="prop-hint">{t(transient ? 'lq_ux.cdu_primary_transient_hint' : 'lq_ux.cdu_primary_steady_hint')}</div>
        {circuit && typeof circuit.inlet_K === 'number' && typeof circuit.flow_kg_s === 'number' && (
          <div className="prop-hint" id="cdu-primary-saved">
            {t('lq_ux.cdu_primary_saved', { inlet: (circuit.inlet_K - 273.15).toFixed(2), lpm: kgSToLpm(circuit.flow_kg_s, primaryRho).toFixed(1), flow: circuit.flow_kg_s })}
          </div>
        )}
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#00b4dc' }} id="cdu-served">
        {hasPipeTopology ? (
          <>
            <div className="prop-row">
              <label>{t('lq_ux.cdu_served_derived')}</label>
            </div>
            <div className="prop-hint" id="cdu-served-derived">{derived || t('lq_ux.cdu_served_none')}</div>
            <div className="prop-hint">{t('lq_ux.cdu_served_derived_hint')}</div>
          </>
        ) : (
          <>
            <div className="prop-row">
              <label>{t('liquid.cdu_served_racks')}</label>
              <span className="badge">{served.size}</span>
            </div>
            {racks.length === 0 ? (
              <div className="prop-hint">{t('lq_ux.cdu_no_racks')}</div>
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
          </>
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
            {pipe.meta?.name ?? pipe.uid}
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
