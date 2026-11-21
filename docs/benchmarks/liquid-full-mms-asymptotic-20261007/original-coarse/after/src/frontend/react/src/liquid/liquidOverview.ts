/** Project topology and physically consistent display values for liquid runs. */

import { modelsIn, type CfdProject } from '../schema/cfdproj'
import { cduConnectedPipes } from './cduPipes'
import { toCelsius, type LiquidResults, type ServerSummary } from './liquidResults'

type Bag = Record<string, unknown>
function asBag(value: unknown): Bag {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Bag : {}
}

/** Specific heat of water, J/(kg·K) — `js/liquid_results_panel.js:107`. */
export const CP_WATER = 4182

/** Below this mass flow the division is meaningless, so the rise is zero. */
const MIN_FLOW_KG_S = 1e-6

export interface CduOverviewRow {
  uid: string
  name: string
  /** The served racks as the panel prints them, or 'None'. */
  served: string
}

/**
 * Every ENABLED CDU with the racks it serves.
 *
 * Disabled CDUs are left out because they are not in the case: listing one
 * beside a solve it took no part in would read as a wiring error.
 */
export function cduOverview(project: CfdProject | null): CduOverviewRow[] {
  if (!project) return []
  const physicalRacks = modelsIn(project, 'racks').filter(rack => rack.meta?.enabled !== false)
  const hasPipeTopology = modelsIn(project, 'pipeSegments').length > 0
  return modelsIn(project, 'cdus')
    .filter((cdu) => cdu.meta?.enabled !== false)
    .map((cdu) => {
      const connected = cduConnectedPipes(project, cdu.uid)
      const rackUids = new Set<string>()
      for (const pipe of [...connected.supply, ...connected.returns]) {
        if (pipe.meta?.enabled === false) continue
        const valves = asBag((pipe as Bag).cooling).valves
        if (!Array.isArray(valves)) continue
        for (const valve of valves) {
          const uid = asBag(valve).connectedRackId
          if (typeof uid === 'string') rackUids.add(uid)
        }
      }
      const legacyRacks = (cdu.meta as { racks?: unknown } | undefined)?.racks
      const list = hasPipeTopology
        ? physicalRacks.filter(rack => rackUids.has(rack.uid)).map(rack => rack.meta?.name || rack.uid)
        : Array.isArray(legacyRacks) ? legacyRacks.filter((r): r is string => typeof r === 'string') : []
      return {
        uid: cdu.uid,
        name: cdu.meta?.name ?? cdu.uid,
        served: list.length ? list.join(', ') : 'None',
      }
    })
}

/**
 * A server's coolant outlet temperature, in the same unit as `supplyC`.
 *
 * Not a Kelvin conversion: the legacy computes the RISE from watts and mass
 * flow and adds it to the rack's supply temperature, already in Celsius.
 */
export function serverOutletC(supplyC: number, flowKgS: number, heatW: number, cpJkgK = CP_WATER): number {
  if (!Number.isFinite(cpJkgK) || cpJkgK <= 0) return NaN
  return supplyC + (Math.abs(flowKgS) > MIN_FLOW_KG_S ? heatW / (Math.abs(flowKgS) * cpJkgK) : 0)
}

/** Read the solved input's fluid, never the potentially edited current project.
 * Defaults apply only to old payloads that omit these fields, and are labelled.
 */
export function resultFluid(raw: unknown) {
  const fluid = asBag(asBag(raw).fluid)
  const rho = fluid.rho_kg_m3, cp = fluid.cp_J_kgK
  const positive = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : NaN
  return {
    rho_kg_m3: rho === undefined ? 997 : positive(rho),
    cp_J_kgK: cp === undefined ? CP_WATER : positive(cp),
    legacyDefaults: rho === undefined || cp === undefined,
  }
}

export function flowLPM(flowKgS: number, rhoKgM3: number): number {
  return Number.isFinite(rhoKgM3) && rhoKgM3 > 0 ? flowKgS / rhoKgM3 * 60000 : NaN
}

interface OutletReading {
  temperatureC: number
  source: 'solved' | 'legacy-estimate' | 'unavailable'
  edgeId?: string
}

/** Cold plates are recorded in their geometric supply-to-return series order.
 * Their water_outlet series already follows the actual flow direction. Select
 * the last plate downstream using the signed edge flow, not the summary's
 * absolute branch flow. Missing current data must not become an estimate.
 */
export function serverResultOutlet(
  results: LiquidResults, server: ServerSummary, sample: number,
  boundary: {supplyC:number;returnC:number;cpJkgK:number},
): OutletReading {
  const signedFlow = server.cp_edge_ids
    .map(id => results.edge_flow_kg_s[results.edge_ids.indexOf(id)]?.[sample])
    .find((value): value is number => typeof value === 'number' && Number.isFinite(value))
  if (results.cp_details.length === 0) {
    const flow = signedFlow ?? server.flow_kg_s[sample] ?? NaN
    const upstream = flow < 0 ? boundary.returnC : boundary.supplyC
    return {temperatureC:serverOutletC(upstream,flow,server.heat_removed_W[sample] ?? NaN,boundary.cpJkgK),source:'legacy-estimate'}
  }
  const unavailable: OutletReading = {temperatureC:NaN,source:'unavailable'}
  if (server.cp_edge_ids.length === 0 || (server.cp_edge_ids.length > 1 && signedFlow === undefined)) return unavailable
  const edgeId = signedFlow !== undefined && signedFlow < 0 ? server.cp_edge_ids[0] : server.cp_edge_ids.at(-1)
  const value = results.cp_details.find(detail => detail.edge_id === edgeId)?.water_outlet_temp_K[sample]
  return typeof value === 'number' && Number.isFinite(value)
    ? {temperatureC:toCelsius(value),source:'solved',edgeId}
    : unavailable
}
