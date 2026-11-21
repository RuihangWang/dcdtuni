/** Derived v3 DAE manifest, using the backend's distributed structural schema.
 * Python remains authoritative for graph/profile-unit and residual admission.
 * These checks also catch missing HX bindings, time-series lengths and known
 * valve active-set changes before a browser exports an unusable file.
 */
import Ajv2020 from 'ajv/dist/2020'
import schema from '../../../../backend/liquidcooling/liquidrun_dae.schema.json'
import schemaV4 from '../../../../backend/liquidcooling/liquidrun_dae_v4.schema.json'
import {checkManufactured} from './manufacturedBenchmark'
import type { DynamicLiquidGraph, EquipmentPack } from '../liquid/dynamicLiquidGraph'
import type { LiquidrunV2, LiquidrunEdge } from './liquidrun'

export interface DAEAnalysis {mode:'steady'|'transient';initialization:'steady'|'consistent'}
export interface DAENewton {max_iterations:number;atol:number;rtol:number;max_backtracks:number
  /** Omitted: dense up to 4000 unknowns, sparse above (backend liquidrun_dae_schema.NewtonConfig). */
  linear_solver?:'dense'|'sparse'|'gmres'|'pgmres'|'block'}
export interface PrimaryCircuitBinding {
  edge_id:string;flow_kg_s:number|number[];inlet_K:number|number[]
  cp_J_kgK:number;rho_kg_m3:number;primary_volume_m3:number;secondary_volume_m3:number
  source:'mock'|'vendor'|'calibrated';source_reference:string
}
export interface DAEExecution {backend:'pytorch';device:'cpu'|'cuda'|`cuda:${number}`;dtype:'float32'|'float64';max_threads:number}
export interface SavedDAEConfig {
  equation_system:'incompressible_thermal_dae'
  analysis:DAEAnalysis
  newton:DAENewton
  primary_circuits:PrimaryCircuitBinding[]
  manufactured_benchmark?:import('./manufacturedBenchmark').ManufacturedBenchmark
  execution?:DAEExecution
}
type DAEEdge=Exclude<LiquidrunEdge,{type:'heat_exchanger'}>|{
  id:string;type:'heat_exchanger';from:string;to:string;parameter_ref?:string
  params:{dp_a?:number;dp_b?:number;UA_W_per_K?:number;hxModel?:'counterflow-ntu'|'mixed-volumes'}
}
export type LiquidrunDAEV3=Omit<LiquidrunV2,'schemaVersion'|'version'|'solver'|'parameter_provenance'|'edges'|'controllers'|'execution'>&SavedDAEConfig&{
  schemaVersion:3;version:'3.0';edges:DAEEdge[];controllers:LiquidrunV2['controllers']
  execution:NonNullable<SavedDAEConfig['execution']>
}

/** cfdproj v5 timeline the run was sampled from (transient design §3). */
export interface TransientProvenance {
  durationS:number
  start:{kind:'steadyConverged'|'uniform';uniform?:{airT_C?:number;waterT_C?:number;solidT_C?:number}}
  tracks:{modelUid:string;param:string;target:string}[]
}
/** .liquidrun v4: every keyframed input a list[N_t], no events, four endpoint forms. */
export type LiquidrunDAEV4=Omit<LiquidrunDAEV3,'schemaVersion'|'version'|'events'|'edges'|'nodes'>&{
  schemaVersion:4;version:'4.0';events:[];transient:TransientProvenance
  edges:(DAEEdge|{id:string;type:string;from:string;to:string;parameter_ref?:string;params:Record<string,unknown>})[]
  nodes:{id:string;type:string;position_m:number[];bc?:{P_Pa:number|number[];T_K?:number}}[]
}

const ajv=new Ajv2020({strict:false,strictNumbers:true,allErrors:true})
const validate=ajv.compile<LiquidrunDAEV3>(schema)
const validateV4=ajv.compile<LiquidrunDAEV4>(schemaV4)

function schemaErrors(check:typeof validate|typeof validateV4):string {
  return (check.errors??[]).filter(e=>!['anyOf','oneOf'].includes(e.keyword)).slice(0,8).map(e=>{
    const key=e.params.additionalProperty??e.params.missingProperty
    const suffix=typeof key==='string'?'/'+key.replace(/~/g,'~0').replace(/\//g,'~1'):''
    return `${e.instancePath}${suffix||(!e.instancePath?'/':'')}: ${e.message}`
  }).join('; ')
}

export function assertLiquidrunDAEV3(value:unknown):asserts value is LiquidrunDAEV3 {
  if(!validate(value))throw new Error(`Liquidrun DAE schema: ${schemaErrors(validate)}`)
  daeChecks(value)
}

export function assertLiquidrunDAEV4(value:unknown):asserts value is LiquidrunDAEV4 {
  if(!validateV4(value))throw new Error(`Liquidrun DAE v4 schema: ${schemaErrors(validateV4)}`)
  daeChecks(value as unknown as LiquidrunDAEV3)
  if(value.manufactured_benchmark)checkManufactured(value.manufactured_benchmark,value as unknown as Parameters<typeof checkManufactured>[1])
  const count=value.time.N_t
  for(const node of value.nodes) {
    const series=node.bc?.P_Pa
    if(Array.isArray(series)&&series.length!==count)throw new Error(`node ${node.id}.P_Pa series must have exactly N_t samples`)
  }
}

/** Either DAE version, by the one it declares. */
export function assertLiquidrunDAE(value:unknown):asserts value is LiquidrunDAEV3|LiquidrunDAEV4 {
  if((value as {schemaVersion?:unknown}|null)?.schemaVersion===4)assertLiquidrunDAEV4(value)
  else assertLiquidrunDAEV3(value)
}

function daeChecks(manifest:LiquidrunDAEV3) {
  const expected=new Set(manifest.edges.filter(e=>e.type==='heat_exchanger').map(e=>e.id))
  const bindings=new Set(manifest.primary_circuits.map(c=>c.edge_id))
  if(bindings.size!==manifest.primary_circuits.length||bindings.size!==expected.size||[...expected].some(id=>!bindings.has(id)))
    throw new Error('primary_circuits must bind every heat-exchanger HX exactly once')
  const count=manifest.time.N_t
  if(manifest.analysis.mode==='steady'&&(count!==1||manifest.events.length||manifest.analysis.initialization!=='steady'))
    throw new Error('steady DAE analysis requires N_t=1, no events, and steady initialization')
  for(const circuit of manifest.primary_circuits) {
    for(const key of ['flow_kg_s','inlet_K'] as const) {
      const series=circuit[key]
      if(Array.isArray(series)&&series.length!==count)throw new Error(`primary_circuits ${circuit.edge_id}.${key} series must have exactly N_t samples`)
    }
  }
  for(const [id,profile] of Object.entries(manifest.parameter_pack?.profiles??{})) {
    if(profile.type==='heat_exchanger'&&'T_secondary_K' in profile.params)
      throw new Error(`parameter_pack ${id}: T_secondary_K is not a DAE parameter`)
  }
  for(const edge of manifest.edges) {
    const profile=edge.parameter_ref?manifest.parameter_pack?.profiles[edge.parameter_ref]?.params:undefined
    const params={...profile,...edge.params} as Record<string,unknown>
    for(const key of ['rpm_ratio','opening','Q_W','flow_set_kg_s','dT_set_K','T_set_K']) {
      const series=params[key]
      if(Array.isArray(series)&&series.length!==count)throw new Error(`edge ${edge.id}.${key} series must have exactly N_t samples`)
    }
    if(edge.type==='valve'&&(typeof params.opening==='number'||Array.isArray(params.opening))) {
      const opening=(Array.isArray(params.opening)?[...params.opening]:Array(count).fill(params.opening)) as number[]
      for(const event of manifest.events.filter(e=>e.target===edge.id&&e.field==='opening')) {
        const step=Math.round(event.time_s/manifest.time.dt_s)
        opening.fill(event.value,step)
      }
      if(opening.some(value=>(value<=0)!==(opening[0]!<=0)))throw new Error('DAE requires a fixed valve active set; open/closed switching is not supported')
    }
  }
}

/** Called only after an explicit project-owned DAE configuration exists. The
 * old graph compiler's derived reservoir column is not part of v3 physics.
 * The saved parameter pack itself must already use the DAE model contract.
 */
export function makeLiquidrunDAEV3(graph:DynamicLiquidGraph,dae:SavedDAEConfig,source:{projectUid:string;projectName:string;projectSha256?:string;schemaVersion?:4|5},controllers:LiquidrunV2['controllers']=[]):LiquidrunDAEV3 {
  if(!source.projectUid?.trim())throw new Error('DAE source project requires a stable UID')
  if(source.projectSha256!==undefined&&!/^[0-9a-f]{64}$/.test(source.projectSha256))throw new Error('DAE source project SHA-256 must be a lowercase digest')
  const copy=structuredClone(graph)
  delete copy.solver
  // Newton DAE uses backward Euler independently of preserved legacy solver settings.
  copy.time.bdf_order=1
  delete copy.parameter_provenance
  for(const edge of copy.edges)if(edge.type==='heat_exchanger')delete edge.params.T_secondary_K
  if(dae.analysis.mode==='steady') {
    if(copy.events.length)throw new Error('steady DAE analysis cannot discard configured events')
    copy.time.N_t=1
  }
  const manifest={...copy,...structuredClone(dae),format:'liquidrun',schemaVersion:3,version:'3.0',
    source_project:{format:'cfdproj',schema_version:source.schemaVersion??4,project_uid:source.projectUid,project_name:source.projectName,
      ...(source.projectSha256!==undefined?{sha256:source.projectSha256}:{})},
    units:{length:'m',time:'s',temperature:'K',pressure:'Pa',mass_flow:'kg/s',power:'W'},
    execution:dae.execution??{backend:'pytorch',device:'cpu',dtype:'float64',max_threads:1},controllers:structuredClone(controllers)}
  assertLiquidrunDAEV3(manifest)
  return manifest
}

/** The v4 form of makeLiquidrunDAEV3: the graph's schedules are already
 * list[N_t] (no events), and the timeline it came from is recorded. */
export function makeLiquidrunDAEV4(graph:DynamicLiquidGraph,dae:SavedDAEConfig,source:{projectUid:string;projectName:string;projectSha256?:string},transient:TransientProvenance,controllers:LiquidrunV2['controllers']=[]):LiquidrunDAEV4 {
  if(!source.projectUid?.trim())throw new Error('DAE source project requires a stable UID')
  if(source.projectSha256!==undefined&&!/^[0-9a-f]{64}$/.test(source.projectSha256))throw new Error('DAE source project SHA-256 must be a lowercase digest')
  if(graph.events.length)throw new Error('.liquidrun v4 carries every scheduled input as a series; lower events first')
  const copy=structuredClone(graph)
  delete copy.solver
  // Newton DAE uses backward Euler independently of preserved legacy solver settings.
  copy.time.bdf_order=1
  delete copy.parameter_provenance
  for(const edge of copy.edges)if(edge.type==='heat_exchanger')delete edge.params.T_secondary_K
  if(dae.analysis.mode==='steady')copy.time.N_t=1
  const manifest={...copy,...structuredClone(dae),format:'liquidrun',schemaVersion:4,version:'4.0',events:[],
    transient:structuredClone(transient),
    source_project:{format:'cfdproj',schema_version:5,project_uid:source.projectUid,project_name:source.projectName,
      ...(source.projectSha256!==undefined?{sha256:source.projectSha256}:{})},
    units:{length:'m',time:'s',temperature:'K',pressure:'Pa',mass_flow:'kg/s',power:'W'},
    execution:dae.execution??{backend:'pytorch',device:'cpu',dtype:'float64',max_threads:1},controllers:structuredClone(controllers)}
  assertLiquidrunDAEV4(manifest)
  return manifest
}

export interface MockDAEOptions {
  flow_kg_s?:number;inlet_K?:number;cp_J_kgK?:number;rho_kg_m3?:number
  primary_volume_m3?:number;secondary_volume_m3?:number;source_reference?:string
  analysis?:DAEAnalysis;newton?:DAENewton
}

/** Materialize deliberate mock choices into data that the caller saves in the
 * cfdproj. Never runs automatically for old projects, and never mutates input.
 */
export function mockDAEChoices(graph:DynamicLiquidGraph,options:MockDAEOptions={}):{dae:SavedDAEConfig;parameterPack:EquipmentPack} {
  const pack=structuredClone(graph.parameter_pack)
  if(pack.source!=='mock')throw new Error('Mock DAE adoption requires an explicit mock equipment pack; do not reinterpret vendor/calibrated curves')
  for(const profile of Object.values(pack.profiles)) {
    if(profile.type!=='heat_exchanger')continue
    delete profile.params.T_secondary_K
    const units=(profile as unknown as {units?:Record<string,string>}).units
    if(units)delete units.T_secondary_K
  }
  if(!pack.revision.endsWith('-dae-v3'))pack.revision+='-dae-v3'
  const note='Explicit mock two-mixed-volume HX interpretation; not NV measurements or calibrated vendor performance.'
  if(!String(pack.description??'').includes(note))pack.description=`${pack.description??''} ${note}`.trim()
  const dae:SavedDAEConfig={equation_system:'incompressible_thermal_dae',
    analysis:options.analysis??{mode:'transient',initialization:'steady'},
    newton:options.newton??{max_iterations:40,atol:1e-10,rtol:1e-10,max_backtracks:18},
    primary_circuits:graph.edges.filter(edge=>edge.type==='heat_exchanger').map(edge=>({
      edge_id:edge.id,flow_kg_s:options.flow_kg_s??1,inlet_K:options.inlet_K??293.15,
      cp_J_kgK:options.cp_J_kgK??graph.fluid.cp_J_kgK,rho_kg_m3:options.rho_kg_m3??graph.fluid.rho_kg_m3,
      primary_volume_m3:options.primary_volume_m3??.02,secondary_volume_m3:options.secondary_volume_m3??.02,
      source:'mock',source_reference:options.source_reference??'Explicit mock adoption; numerical choices are recorded in this binding. Primary cp/rho follow the project fluid unless explicitly overridden. Not NV calibration.',
    }))}
  return {dae,parameterPack:pack}
}
