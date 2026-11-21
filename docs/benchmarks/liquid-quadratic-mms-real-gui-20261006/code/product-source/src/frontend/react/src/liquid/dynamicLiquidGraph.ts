/** Compile the real editor topology into an actively cooled, controllable loop.
 * Reuses the existing geometry compiler on a private UID-named snapshot. The
 * legacy boundary-source compiler remains available to older saved workflows.
 */
import mockPack from '../../../../backend/liquidcooling/data/mock-demo-v1.json'
import { allModels, type CfdProject } from '../schema/cfdproj'
import { reviveKinds, schema, projectHybridHeatBudgetErrors } from '../legacy/cfdprojModules'
import { compileLiquidGraph } from './liquidRunGraph'
import { readFluid, readLiquidConfig } from './liquidConfig'
import { backfillPairUids } from './cpCuPairIdentity'

type Bag = Record<string, any>
export interface LiquidNode { id: string; type: string; position_m: number[]; bc?: {P_Pa: number; T_K?: number} }
export interface LiquidEdge { id: string; type: string; from: string; to: string; params: Bag; parameter_ref?: string }
export interface LiquidEvent {time_s: number; target: string; field: string; value: number}
export interface ObjectBinding {label: string; kind: string; node_ids: string[]; edge_ids: string[]; parent_uid?: string}
export interface EquipmentPack {id: string; revision: string; source: string; profiles: Record<string, {type: string; params: Bag}>; [key: string]: unknown}
export interface DynamicLiquidGraph extends Bag {
  nodes: LiquidNode[]; edges: LiquidEdge[]; racks: Bag[];
  time: {dt_s: number; N_t: number; bdf_order: number}; events: LiquidEvent[];
  parameter_pack: EquipmentPack; object_bindings: Record<string,ObjectBinding>;
}
export interface DynamicLiquidOptions {
  dt_s?: number; duration_s?: number; events?: LiquidEvent[]; parameterPack?: EquipmentPack;
  referencePressurePa?: number;
  profileRefs?: {pump?: string; heat_exchanger?: string; valve?: string};
}

function finitePositive(value: number, label: string): number {
  if (!Number.isFinite(value) || value <= 0) throw new Error(`${label} must be positive and finite`)
  return value
}

export function compileDynamicLiquidGraph(project: CfdProject, options: DynamicLiquidOptions = {}): DynamicLiquidGraph {
  const heatBudgetErrors = projectHybridHeatBudgetErrors(project)
  if (heatBudgetErrors.length) throw new Error(heatBudgetErrors.map(e=>`${e.path}: ${e.msg}`).join('; '))
  const topologyErrors = schema().validateLiquidIsolationConnections(project)
  if (topologyErrors.length) throw new Error(topologyErrors.map(e=>`${e.path}: ${e.msg}`).join('; '))
  const dt = finitePositive(options.dt_s ?? 1, 'dt_s')
  const duration = finitePositive(options.duration_s ?? 120, 'duration_s')
  const intervals = duration / dt
  if (Math.abs(intervals - Math.round(intervals)) > 1e-8) throw new Error('duration must lie on the dt grid')
  const pack = JSON.parse(JSON.stringify(options.parameterPack ?? mockPack)) as EquipmentPack
  const profileRefs = {pump:'pump-demo', heat_exchanger:'hx-demo', valve:'valve-demo', ...options.profileRefs}
  function profile(kind: keyof typeof profileRefs): string {
    const id = profileRefs[kind]
    if (!id || pack.profiles?.[id]?.type !== kind) throw new Error(`Missing ${kind} parameter profile: ${id}`)
    return id
  }
  profile('pump'); profile('heat_exchanger'); profile('valve')

  const snapshot = reviveKinds(JSON.parse(JSON.stringify(project)) as CfdProject)
  const models = allModels(snapshot).map(item => item.model)
  const bindings: Record<string,ObjectBinding> = {}
  for (const model of models) {
    if (!model.uid) throw new Error('Every physical object requires a stable uid')
    bindings[model.uid] = {label:model.meta?.name || model.uid, kind:String(model.kind ?? ''), node_ids:[],edge_ids:[]}
    model.meta = {...model.meta, name:model.uid}
    // A server's cold plates are its liquidCooling.cpCuPairs, the one list the
    // inspector, the parameter registry, the timeline and the legacy editor
    // write: membership, order and values all come from it. A pair without a
    // uid gets the one the inspector would write at its first edit (from the
    // migration's coldPlates key); coldPlates is read for nothing else.
    backfillPairUids(model as unknown as Bag)
    const servers = (model as Bag).constructions?.servers ?? {}
    for (const [key, server] of Object.entries(servers) as [string,Bag][]) {
      const sid = String(server.uid || key)
      bindings[`${model.uid}/${sid}`] = {label:server.meta?.name || sid,kind:'server',parent_uid:model.uid,node_ids:[],edge_ids:[]}
      server.meta = {...server.meta, name:sid}
      // The uid, not the display name or the position, names the edge.
      const pairs = (Array.isArray(server.liquidCooling?.cpCuPairs) ? server.liquidCooling.cpCuPairs : []) as Bag[]
      if (pairs.length) {
        server.liquidCooling = {...server.liquidCooling, cpCuPairs:pairs.map(p => ({...p,name:String(p.uid),_stable_uid:encodeURIComponent(String(p.uid))}))}
      }
    }
  }
  const cfg = readLiquidConfig(snapshot)
  const raw = compileLiquidGraph(snapshot, {
    enabled:true, fluid:readFluid(snapshot),
    solver:{...cfg.solver,dt_s:dt,N_t:Math.round(intervals)+1},
  })
  if (!raw) throw new Error('Liquid topology cannot be compiled; check CDU and rack connections')
  const graph = raw as DynamicLiquidGraph
  graph.source = 'react-dynamic'
  graph.parameter_pack = pack
  graph.object_bindings = bindings
  const nodeIds = new Set(graph.nodes.map(n => n.id))
  const edgeIds = new Set(graph.edges.map(e => e.id))
  function appendNode(node: LiquidNode) {
    if (nodeIds.has(node.id)) throw new Error(`Duplicate physical node ${node.id}`)
    graph.nodes.push(node); nodeIds.add(node.id)
  }
  function appendEdge(edge: LiquidEdge) {
    if (edgeIds.has(edge.id)) throw new Error(`Duplicate physical edge ${edge.id}`)
    graph.edges.push(edge); edgeIds.add(edge.id)
  }

  for (const model of models.filter(m => m.kind === 'cdu' && m.meta.enabled !== false)) {
    const uid = model.uid
    const supply = graph.nodes.find(n => n.id === `${uid}_supply`)
    const ret = graph.nodes.find(n => n.id === `${uid}_return`)
    const pump = graph.edges.find(e => e.id === `${uid}_pump`)
    if (!supply || !ret?.bc || !pump) throw new Error(`CDU ${uid} lacks a compiled supply/return/pump`)
    supply.type = 'junction'; delete supply.bc
    const cooling = (model as Bag).cooling ?? {}
    // The return side is the loop's one pressure reference; the parameter
    // registry keeps it in kPa (cdu.returnPressure, default 150).
    const returnKpa = cooling.returnPressureKpa
    ret.bc = {P_Pa:finitePositive(options.referencePressurePa ?? (returnKpa !== undefined ? Number(returnKpa)*1000 : 150000),'referencePressurePa')}
    const mid = `${uid}_pump_discharge`
    appendNode({id:mid,type:'junction',position_m:[...supply.position_m]})
    pump.to = mid; pump.parameter_ref = profile('pump')
    if (cooling.pumpControl === 'flow') {
      // A flow-held pump (.liquidrun v4): the registry's total flow (L/min)
      // is the set point; the pressure rise follows from the network.
      const kgs = Number(cooling.totalFlowRate)*graph.fluid.rho_kg_m3/60000
      if (!Number.isFinite(kgs) || kgs < 0) throw new Error(`CDU ${uid} holds a pump flow but has no valid total flow rate`)
      pump.params = {mode:'flow',flow_set_kg_s:kgs}
    } else {
      const rpm = Number((model as Bag).constructions?.pump?.rpmRatio ?? pump.params.rpm_ratio ?? 1)
      if (!Number.isFinite(rpm) || rpm < 0) throw new Error(`Invalid pump speed ratio on ${uid}`)
      pump.params = {rpm_ratio:rpm}
    }
    const params: Bag = {}
    if (cooling.secondaryControl === 'setpoint') {
      // Ideal secondary supply control (.liquidrun v4): the HX holds the
      // registry's secondary supply temperature.
      const kelvin = Number(cooling.supplyTemperatureC)+273.15
      if (!Number.isFinite(kelvin) || kelvin <= 0) throw new Error(`CDU ${uid} holds a secondary supply temperature but has no valid one`)
      params.mode = 'setpoint'; params.T_set_K = kelvin
    }
    if (cooling.facilitySupplyTemperatureC !== undefined) {
      const kelvin = Number(cooling.facilitySupplyTemperatureC)+273.15
      if (!Number.isFinite(kelvin) || kelvin <= 0) throw new Error(`Invalid facility temperature on ${uid}`)
      params.T_secondary_K = kelvin
    }
    appendEdge({id:`${uid}_hx`,type:'heat_exchanger',from:mid,to:supply.id,parameter_ref:profile('heat_exchanger'),params})
  }

  // A drawn flow-control fitting becomes an actual valve in series with its
  // existing spoke pipe. Tee junctions remain ideal mixing connections.
  for (const model of models.filter(m => m.kind === 'pipe_segment' && m.meta.enabled !== false)) {
    const valves = ((model as Bag).cooling?.valves ?? []) as Bag[]
    for (const valve of valves.filter(v => v.type === 'flow_control')) {
      const spoke = graph.edges.find(e => e.id === `${model.uid}_spoke_${valve.seriesWithValveId ?? valve.id}`)
      if (!spoke) throw new Error(`Control valve ${valve.id} is not connected to a rack`)
      let inlet = graph.nodes.find(n => n.id === spoke.from)!
      if (valve.seriesWithValveId) {
        type Point3 = [number, number, number]
        const checkedPoint = (value: unknown): Point3 => {
          if (!Array.isArray(value) || value.length !== 3 ||
              !value.every(v => typeof v === 'number' && Number.isFinite(v)))
            throw new Error('Inline valve route needs finite three-coordinate points')
          return [value[0], value[1], value[2]]
        }
        const authoredRoute = spoke.params.waypoints_m
        if (!Array.isArray(authoredRoute) || authoredRoute.length < 2)
          throw new Error('Inline valve branch route needs two points')
        const route = authoredRoute.map(checkedPoint)
        const at = checkedPoint([valve.position?.x, valve.position?.y, valve.position?.z])
        let hit = -1, u = 0
        for (let i=0;i<route.length-1;i++) {
          const a=route[i]!, b=route[i+1]!
          const d: Point3=[b[0]-a[0], b[1]-a[1], b[2]-a[2]]
          const len2=d[0]*d[0]+d[1]*d[1]+d[2]*d[2]
          const t=len2 ? ((at[0]-a[0])*d[0]+(at[1]-a[1])*d[1]+(at[2]-a[2])*d[2])/len2 : -1
          if (t>0 && t<1 && Math.hypot(at[0]-a[0]-t*d[0],at[1]-a[1]-t*d[1],at[2]-a[2]-t*d[2])<1e-6) {hit=i;u=t;break}
        }
        if (hit<0) throw new Error('Inline valve must lie strictly inside its branch route')
        const a=route[hit]!, b=route[hit+1]!
        const point: Point3=[a[0]+u*(b[0]-a[0]),a[1]+u*(b[1]-a[1]),a[2]+u*(b[2]-a[2])]
        const pre=route.slice(0,hit+1).concat([point]), post=[point].concat(route.slice(hit+1))
        const id=`${model.uid}_control_${valve.id}_in`
        appendNode({id,type:'junction',position_m:point})
        const routeCells=(points:Point3[])=>Math.max(2,Math.round(points.slice(1).reduce(
          (length,p,i)=>{ const a=points[i]!; return length+Math.hypot(p[0]-a[0],p[1]-a[1],p[2]-a[2]) },0)/0.2))
        appendEdge({id:spoke.id+'_before_control',type:'pipe',from:spoke.from,to:id,
          params:{...spoke.params,waypoints_m:pre,n_cells:routeCells(pre)}})
        spoke.params={...spoke.params,waypoints_m:post,n_cells:routeCells(post)}
        spoke.from=id; inlet=graph.nodes.find(n=>n.id===id)!
      }
      const node = `${model.uid}_control_${valve.id}_out`
      const edge = `${model.uid}_control_${valve.id}`
      appendNode({id:node,type:'junction',position_m:[...inlet.position_m]})
      const opening = Number(valve.opening ?? 1)
      if (!Number.isFinite(opening) || opening < 0 || opening > 1) throw new Error(`Invalid valve opening on ${valve.id}`)
      appendEdge({id:edge,type:'valve',from:spoke.from,to:node,parameter_ref:profile('valve'),params:{opening}})
      spoke.from = node
      bindings[`${model.uid}/${valve.id}`] = {label:String(valve.name ?? valve.id),kind:'valve',parent_uid:model.uid,node_ids:[node],edge_ids:[edge]}
    }
    // Explicit project-owned inline isolation valves are zero-volume elements
    // in the existing branch. Their positions coincide with the parent fitting,
    // so all geometry-derived pipe lengths and stored fluid volumes are retained.
    for (const valve of valves.filter(v => v.type === 'isolation')) {
      const spoke = graph.edges.find(e => e.id === `${model.uid}_spoke_${valve.seriesWithValveId}`)
      if (!spoke) throw new Error(`Isolation valve ${valve.id} has no compiled rack spoke`)
      const inlet = graph.nodes.find(n => n.id === spoke.from)!
      const node = `${model.uid}_isolation_${valve.id}_out`
      const edge = `${model.uid}_isolation_${valve.id}`
      appendNode({id:node,type:'junction',position_m:[...inlet.position_m]})
      appendEdge({id:edge,type:'valve',from:spoke.from,to:node,parameter_ref:profile('valve'),params:{opening:valve.opening}})
      spoke.from = node
      bindings[`${model.uid}/${valve.id}`] = {label:String(valve.name ?? valve.id),kind:'valve',parent_uid:model.uid,node_ids:[node],edge_ids:[edge]}
    }
  }
  for (const [uid,binding] of Object.entries(bindings)) {
    if (binding.kind === 'valve') continue
    const prefix = uid.replace('/', '_') + '_'
    binding.node_ids = graph.nodes.filter(n => n.id.startsWith(prefix)).map(n => n.id)
    binding.edge_ids = graph.edges.filter(e => e.id.startsWith(prefix)).map(e => e.id)
  }
  // Cold plates whose coolant temperature rise is prescribed (.liquidrun v4):
  // the heat is derived from the flow, so the edge carries dT_set_K, not Q_W.
  for (const rackEntry of (graph.racks ?? []) as Bag[]) {
    const rack = models.find(m => m.uid === rackEntry.id) as Bag | undefined
    for (const server of (rackEntry.servers ?? []) as Bag[]) {
      const pairs = (rack?.constructions?.servers?.[server.id]?.liquidCooling?.cpCuPairs ?? []) as Bag[]
      for (const entry of (server.cp_cu_pairs ?? []) as Bag[]) {
        const pair = pairs.find(p => String(p.name) === String(entry.id))
        if (pair?.heatMode !== 'temperature_rise') continue
        const edge = graph.edges.find(e => e.id === entry.edge_id)
        const rise = Number(pair.dT_K)
        if (!edge || !Number.isFinite(rise) || rise < 0) throw new Error(`Cold plate ${entry.id} on ${server.id} needs a coolant temperature rise (K)`)
        delete edge.params.Q_W
        edge.params.heat_mode = 'temperature_rise'; edge.params.dT_set_K = rise
      }
    }
  }
  const validTargets = new Set([...nodeIds,...edgeIds])
  graph.events = (options.events ?? []).map(event => {
    if (!validTargets.has(event.target)) throw new Error(`Unknown event target ${event.target}`)
    if (!Number.isFinite(event.time_s) || event.time_s < 0 || event.time_s > duration || Math.abs(event.time_s/dt-Math.round(event.time_s/dt)) > 1e-8) throw new Error('Event time must lie on the simulation grid')
    if (!Number.isFinite(event.value)) throw new Error('Event value must be finite')
    return {...event}
  })
  return graph
}
