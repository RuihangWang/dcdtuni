/** Versioned derived execution manifest. .cfdproj remains the editable truth.
 *
 * Mirrors backend/liquidcooling/liquidrun_schema.py. The distributed JSON
 * Schema validates structural fields; the backend also checks graph/profile
 * references, time-grid alignment, series lengths and actuator ownership.
 * makeLiquidrunV2 validates the distributed structural contract before export;
 * Python additionally validates the physical graph and controller semantics.
 */
import Ajv2020 from 'ajv/dist/2020'
import schema from '../../../../backend/liquidcooling/liquidrun.schema.json'
import type { DynamicLiquidGraph, EquipmentPack, ObjectBinding } from '../liquid/dynamicLiquidGraph'

const validateManifest = new Ajv2020({strict:false, strictNumbers:true, allErrors:true}).compile(schema)

/** Validate a standalone file using the exact schema distributed by Python. */
export function assertLiquidrunV2(value: unknown): asserts value is LiquidrunV2 {
  if (validateManifest(value)) return
  const errors = (validateManifest.errors ?? []).filter(error => !['anyOf','oneOf'].includes(error.keyword))
  const details = errors.slice(0, 8).map(error => {
    const extra = error.params.additionalProperty ?? error.params.missingProperty
    const suffix = typeof extra === 'string' ? '/' + extra.replace(/~/g,'~0').replace(/\//g,'~1') : ''
    return `${error.instancePath}${suffix || (error.instancePath ? '' : '/')}: ${error.message}`
  })
  throw new Error(`Liquidrun schema: ${details.join('; ') || 'invalid manifest'}`)
}

export interface LiquidController {
  id: string
  measurement: {
    kind: 'node_temperature' | 'pressure_difference' | 'solid_temperature' | 'mass_flow'
    target_id: string
    reference_id?: string
  }
  actuator: { edge_id: string; field: 'rpm_ratio' | 'opening' }
  pid: {
    kp: number; ki: number; kd?: number; setpoint?: number; bias?: number
    output_min?: number; output_max?: number; action?: 'direct' | 'reverse'
    rate_limit_per_s?: number; derivative_tau_s?: number
  }
  sample_period_s: number
}

export interface LiquidrunSourceProject {
  format: 'cfdproj'
  /** 5 when the source is a cfdproj v5 (transient keyframes) document. */
  schema_version: 4 | 5
  project_uid: string
  project_name: string
  revision?: number
  sha256?: string
}

type Vector3 = [number, number, number]
type Series = number | number[]
type EdgeBase = { id: string; from: string; to: string; parameter_ref?: string }
/** A profile may fill omitted fields; all effective required fields are checked
 * after resolution. Unlike legacy graphs, unknown parameter names are rejected. */
export type LiquidrunEdge = EdgeBase & (
  | {type:'pipe';params:{D_m?:number;roughness_m?:number;n_cells?:number;waypoints_m?:Vector3[];K_minor_per_bend?:number;K_minor_additional?:number;heat_source_W_per_m?:number|(number|number[])[]}}
  | {type:'pump';params:{H0_m?:number;a_coef?:number;rpm_ratio?:Series}}
  | {type:'valve';params:{D_m?:number;Kv_max?:number;opening?:Series;characteristic?:'linear'|'equal_percentage'|'quick_opening'}}
  | {type:'cold_plate';params:{dp_a?:number;dp_b?:number;UA_W_per_K?:number;C_solid_J_per_K?:number;V_fluid_m3?:number;Q_W?:Series}}
  | {type:'heat_exchanger';params:{dp_a?:number;dp_b?:number;UA_W_per_K?:number;T_secondary_K?:number}}
)

interface LiquidrunRack {
  id:string;position_m?:Vector3;n_slots?:number
  manifold?:{supply_node:string;return_node:string;layout:string;D_m:number}
  supply_manifold?:{node_id:string;D_m?:number;n_cells?:number}
  return_manifold?:{node_id:string;D_m?:number;n_cells?:number}
  servers: {
    id:string;slot_range?:[number,number];z_m?:number;slot_u?:number;span_u?:number
    inlet_node?:string;outlet_node?:string
    cp_cu_pairs?:{id:string;edge_id:string;Q_W:number}[]
    cp_cu?:{server_id:string;cold_plate_edge_id:string;power_W?:number;T_inlet_K?:number}
  }[]
}

export interface LiquidrunV2 {
  format: 'liquidrun'
  schemaVersion: 2
  version: '2.0'
  source: string
  projectName: string
  source_project: LiquidrunSourceProject
  units: {length:'m';time:'s';temperature:'K';pressure:'Pa';mass_flow:'kg/s';power:'W'}
  execution: {backend:'pytorch';device:'cpu';dtype:'float64';max_threads:1}
  gravity: Vector3
  fluid: {rho_kg_m3:number;cp_J_kgK:number;mu_Pa_s:number;k_W_mK:number}
  time: {dt_s:number;N_t:number;bdf_order:1}
  nodes: {id:string;type:'pressure_bc'|'junction';position_m:Vector3;bc?:{P_Pa:number;T_K?:number}}[]
  edges: LiquidrunEdge[]
  racks: LiquidrunRack[]
  events: {time_s:number;target:string;field:'rpm_ratio'|'opening'|'Q_W';value:number}[]
  initial_conditions: {T_fluid_K:number;T_solid_K:number;P_uniform_Pa:number}
  solver?: {optimizer:'adam';lr:number;max_epochs:number;convergence_tol:number;weights:{mass:number;momentum:number;energy:number;bc:number;ic:number}}
  controllers: LiquidController[]
  object_bindings: Record<string,ObjectBinding>
  parameter_pack?: EquipmentPack
  parameter_provenance?: {
    id:string;revision:string;source:'mock'|'vendor'|'calibrated';description:string;sha256:string;resolved_sha256:string
    bindings:{edges:Record<string,{profile:string;overrides:Record<string,unknown>}>;fluid?:{profile:string;overrides:Record<string,unknown>}}
  }
}

export interface LiquidrunV2Options {
  projectUid: string
  projectName: string
  controllers: LiquidController[]
  projectRevision?: number
  projectSha256?: string
  schemaVersion?: 4 | 5
}

export function makeLiquidrunV2(graph: DynamicLiquidGraph, options: LiquidrunV2Options): LiquidrunV2 {
  if (!options.projectUid?.trim()) throw new Error('liquidrun source project requires its stable uid')
  if (!options.projectName?.trim()) throw new Error('liquidrun source project requires its display name')
  if (graph.time.bdf_order !== 1) throw new Error('Liquid runtime currently requires bdf_order 1')
  if (options.projectRevision !== undefined && (!Number.isInteger(options.projectRevision) || options.projectRevision < 0)) throw new Error('Invalid source project revision')
  if (options.projectSha256 !== undefined && !/^[0-9a-f]{64}$/.test(options.projectSha256)) throw new Error('Invalid source project SHA-256')
  const source: LiquidrunSourceProject = {
    format:'cfdproj',schema_version:options.schemaVersion ?? 4,project_uid:options.projectUid,project_name:options.projectName,
    ...(options.projectRevision !== undefined ? {revision:options.projectRevision} : {}),
    ...(options.projectSha256 !== undefined ? {sha256:options.projectSha256} : {}),
  }
  // Preserve all graph fields; schema validation rejects unsupported
  // extras instead of silently discarding a contradictory solver/device value.
  // The one exception: hxModel selects the DAE's HX law; the v2 runtime's HX
  // is an infinite facility reservoir (T_secondary_K) that has no such choice.
  const copy = structuredClone(graph)
  for (const edge of copy.edges) if (edge.type === 'heat_exchanger') delete edge.params.hxModel
  const manifest = {
    ...copy,
    format:'liquidrun',schemaVersion:2,version:'2.0',source_project:source,
    units:{length:'m',time:'s',temperature:'K',pressure:'Pa',mass_flow:'kg/s',power:'W'},
    execution:{backend:'pytorch',device:'cpu',dtype:'float64',max_threads:1},
    controllers:structuredClone(options.controllers),
  }
  assertLiquidrunV2(manifest)
  return manifest
}
