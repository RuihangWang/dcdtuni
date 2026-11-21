/**
 * What a CDU's inspector edits, and what each value means to the DAE.
 *
 * The rules are the compiler's, stated once for the panel:
 *   - the return side is the loop's pressure reference, `cooling.returnPressureKpa`
 *     (registry `cdu.returnPressure`, default 150 kPa; dynamicLiquidGraph.ts);
 *   - the DAE never reads `supplyPressureKpa` (it deletes the supply BC), only
 *     the legacy boundary-condition solver does;
 *   - `supplyTemperatureC` is a set point only with `secondaryControl: 'setpoint'`,
 *     `totalFlowRate` only with `pumpControl: 'flow'`;
 *   - pump speed is `constructions.pump.rpmRatio` (curve pumps);
 *   - the HX reads its UA by `cooling.hxModel`; a saved CDU without the field
 *     compiles without it and runs as two mixed volumes, a CDU made by the
 *     factory states counterflow ε-NTU (owner decision 2026-10-21);
 *   - the primary side (facility water) of a DAE run is `dae.primary_circuits[]`
 *     of the saved experiment, one per HX edge `<cdu uid>_hx`. The registry
 *     keeps the same two values on the CDU (`cdu.primaryInletT`,
 *     `cdu.primaryFlow`) for the timeline. A steady edit writes both, so
 *     every compile path (panel, automation, headless) reads one value.
 */
import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'
import { domainModesOf } from '../transient/model'
import { HX_MODELS, type HxModel } from './dynamicLiquidGraph'
import { readExperiment, resolveSavedExperiment } from './liquidExperiment'

type Bag = Record<string, any>

export const DEFAULT_RETURN_PRESSURE_KPA = 150
export const DEFAULT_PRIMARY_INLET_C = 20
export const DEFAULT_PRIMARY_FLOW_KG_S = 1

export function cduCooling(model: unknown): Bag {
  return ((model as Bag)?.cooling ?? {}) as Bag
}

/** The model the solver will use for this CDU's HX. */
export function effectiveHxModel(cooling: Bag): HxModel {
  return HX_MODELS.includes(cooling.hxModel) ? cooling.hxModel : 'mixed-volumes'
}

/** Which of the two mode-dependent fields the DAE reads. */
export function cduFieldUse(cooling: Bag): { supplyTemperature: boolean; totalFlow: boolean } {
  return { supplyTemperature: cooling.secondaryControl === 'setpoint', totalFlow: cooling.pumpControl === 'flow' }
}

/** L/min of a liquid of density `rho` as kg/s. */
export function lpmToKgS(lpm: number, rho: number): number {
  return lpm * rho / 60000
}

/** kg/s of a liquid of density `rho` as L/min. */
export function kgSToLpm(kgS: number, rho: number): number {
  return kgS * 60000 / rho
}

export function hxEdgeIdOf(cduUid: string): string {
  return `${cduUid}_hx`
}

function finite(value: number, min: number, max: number): boolean {
  return Number.isFinite(value) && value >= min && value <= max
}

function cduIn(draft: CfdProject, uid: string): Bag | null {
  const hit = store.findConstruction(draft, uid)
  return hit && hit.model.kind === 'cdu' ? hit.model as unknown as Bag : null
}

export function setReturnPressure(uid: string, kPa: number): boolean {
  if (!finite(kPa, 1, 2000)) return false
  return store.edit('setCduReturnPressure', `${uid} returnPressureKpa=${kPa}`, draft => {
    const cdu = cduIn(draft, uid); if (cdu) (cdu.cooling ??= {}).returnPressureKpa = kPa
  })
}

export function setPumpSpeedRatio(uid: string, ratio: number): boolean {
  if (!finite(ratio, 0, 2)) return false
  return store.edit('setCduPumpSpeed', `${uid} rpmRatio=${ratio}`, draft => {
    const cdu = cduIn(draft, uid); if (!cdu) return
    const constructions = (cdu.constructions ??= {})
    ;(constructions.pump ??= {}).rpmRatio = ratio
  })
}

export function setHxModel(uid: string, model: HxModel): boolean {
  if (!HX_MODELS.includes(model)) return false
  return store.edit('setCduHxModel', `${uid} hxModel=${model}`, draft => {
    const cdu = cduIn(draft, uid); if (cdu) (cdu.cooling ??= {}).hxModel = model
  })
}

/**
 * Primary (facility) inlet temperature in °C or flow in kg/s.
 *
 * Always the CDU's registry field. In a steady project also the matching
 * scalar of the saved DAE experiment's primary circuit, when one exists, so a
 * steady solve uses the value shown. A transient project's circuits follow
 * the timeline's keyframes instead (applyCircuitSeries), which the panel says.
 */
export function setPrimaryInput(uid: string, field: 'inletC' | 'flowKgS', value: number): boolean {
  if (field === 'inletC' ? !finite(value, 0, 80) : !finite(value, 0, 1000)) return false
  const key = field === 'inletC' ? 'primaryInletTemperatureC' : 'primaryFlowKgS'
  const steady = domainModesOf(store.getProject()).liquid !== 'transient'
  return store.edit('setCduPrimaryInput', `${uid} ${key}=${value}`, draft => {
    const cdu = cduIn(draft, uid); if (!cdu) return
    ;(cdu.cooling ??= {})[key] = value
    if (!steady) return
    const circuits = (draft as Bag).runDefaults?.liquid?.experiment?.dae?.primary_circuits
    if (Array.isArray(circuits)) {
      for (const circuit of circuits) {
        if (circuit?.edge_id !== hxEdgeIdOf(uid)) continue
        if (field === 'inletC') circuit.inlet_K = value + 273.15
        else circuit.flow_kg_s = value
      }
      return
    }
    // No saved DAE inputs yet: adopt them now, as Prepare would, with this
    // value in place. Otherwise a compile path that adopts on its own (the
    // automation contract, a headless build) would use the synthetic
    // 20 °C / 1 kg/s and the panel would show a value no run used.
    if (readExperiment(draft).engine !== 'newton-dae') return
    try {
      const adopted = withCduPrimaryInputs(draft, resolveSavedExperiment(draft))
      const runDefaults = ((draft as Bag).runDefaults ??= {})
      runDefaults.liquid = { ...(runDefaults.liquid ?? {}), experiment: adopted }
    } catch { /* topology not compilable yet: Prepare applies the value later */ }
  })
}

/**
 * The saved experiment with every CDU's stated primary inputs applied to its
 * circuit. For the panel's Prepare path, which adopts default circuits for a
 * project that never had them: without this a value typed into the CDU before
 * the first solve would be replaced by the synthetic 20 °C / 1 kg/s.
 */
export function withCduPrimaryInputs<T extends { dae?: Bag }>(project: CfdProject, experiment: T): T {
  if (!experiment.dae || domainModesOf(project).liquid === 'transient') return experiment
  const cdus = ((project as Bag).constructions?.cdus ?? {}) as Record<string, Bag>
  let changed = false
  const circuits = (experiment.dae.primary_circuits as Bag[]).map(circuit => {
    const cdu = Object.values(cdus).find(c => hxEdgeIdOf(String(c.uid)) === circuit.edge_id)
    const cooling = cduCooling(cdu)
    const next = { ...circuit }
    if (Number.isFinite(cooling.primaryInletTemperatureC) && !Array.isArray(circuit.inlet_K) && circuit.inlet_K !== cooling.primaryInletTemperatureC + 273.15) {
      next.inlet_K = cooling.primaryInletTemperatureC + 273.15; changed = true
    }
    if (Number.isFinite(cooling.primaryFlowKgS) && !Array.isArray(circuit.flow_kg_s) && circuit.flow_kg_s !== cooling.primaryFlowKgS) {
      next.flow_kg_s = cooling.primaryFlowKgS; changed = true
    }
    return next
  })
  return changed ? { ...experiment, dae: { ...experiment.dae, primary_circuits: circuits } } : experiment
}

/** The primary circuit a CDU's HX has in the saved experiment, if any. */
export function savedPrimaryCircuit(project: CfdProject | null, cduUid: string): Bag | null {
  const circuits = (project as Bag | null)?.runDefaults?.liquid?.experiment?.dae?.primary_circuits
  return Array.isArray(circuits) ? circuits.find((c: Bag) => c?.edge_id === hxEdgeIdOf(cduUid)) ?? null : null
}
