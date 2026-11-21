export interface BenchmarkVector {ids:string[];values:number[]}
export interface BenchmarkMatrix {ids:string[];values:number[][]}
export interface BenchmarkStorage {
 water_temperature_K:BenchmarkVector;solid_temperature_K:BenchmarkVector
 hx_secondary_K:BenchmarkVector;hx_primary_K:BenchmarkVector
}
export interface ManufacturedBenchmark {
 format:'dcdtuni-manufactured-benchmark';version:1;provenance:'synthetic-research'
 formula_id:string;source_reference:string;graph_layout_sha256:string;source_sha256:string
 units:{pressure:'Pa';temperature:'K';mass_flow:'kg/s';power:'W';time:'s';temperature_rate:'K/s'}
 sampling:'endpoint_hold';times_s:number[]
 sources:{momentum_Pa:BenchmarkMatrix;water_W:BenchmarkMatrix;solid_W:BenchmarkMatrix;hx_secondary_W:BenchmarkMatrix;hx_primary_W:BenchmarkMatrix}
 initial_state:BenchmarkStorage&{node_pressure_Pa:BenchmarkVector;flow_kg_s:BenchmarkVector;node_temperature_K:BenchmarkVector}
 initial_temperature_rate:BenchmarkStorage;flow_signs:BenchmarkVector
}
/** The strict generated schema checks fields/numbers. Graph binding is checked
 * here on the actual compile; the backend additionally verifies canonical SHA. */
export function checkManufactured(benchmark:ManufacturedBenchmark,manifest:{
 schemaVersion:number;analysis:{mode:string;initialization:string};time:{N_t:number;dt_s:number}
 nodes:{id:string}[];edges:{id:string;type:string;params:Record<string,unknown>;parameter_ref?:string}[]
 parameter_pack?:{profiles:Record<string,{params:Record<string,unknown>}>}
}) {
 if(manifest.schemaVersion!==4||manifest.analysis.mode!=='transient'||manifest.analysis.initialization!=='consistent')
  throw new Error('Manufactured benchmark requires v4 transient/consistent')
 const node=manifest.nodes.map(n=>n.id),edge=manifest.edges.map(e=>e.id),water:string[]=[]
 for(const e of manifest.edges) {
  const p={...(e.parameter_ref?manifest.parameter_pack?.profiles[e.parameter_ref]?.params:{}),...e.params}
  if(p.mode==='flow'||p.mode==='setpoint'||p.heat_mode==='temperature_rise')throw new Error('Manufactured endpoint mode unsupported')
  if(e.type==='valve') {
   const values=Array.isArray(p.opening)?p.opening:[p.opening]
   if(values.some(v=>typeof v!=='number'||v<=0))throw new Error('Manufactured benchmark requires open valves')
  }
  if(e.type==='pipe') {
   if(!Number.isSafeInteger(p.n_cells)||Number(p.n_cells)<1)throw new Error('Manufactured storage needs explicit n_cells')
   for(let i=0;i<Number(p.n_cells);i++)water.push(e.id+'/cell/'+i)
  }
 }
 const solid=manifest.edges.filter(e=>e.type==='cold_plate').map(e=>e.id),hx=manifest.edges.filter(e=>e.type==='heat_exchanger').map(e=>e.id)
 const coverage=(record:BenchmarkVector|BenchmarkMatrix,ids:string[])=>{
  if(record.values.length!==record.ids.length||record.ids.length!==ids.length||new Set(record.ids).size!==record.ids.length||ids.some(id=>!record.ids.includes(id)))
   throw new Error('Manufactured graph/storage ID coverage mismatch')
 }
 if(benchmark.times_s.length!==manifest.time.N_t||benchmark.times_s.some((t,i)=>t!==i*manifest.time.dt_s))
  throw new Error('Manufactured sample grid mismatch')
 const src=benchmark.sources
 for(const [record,ids] of [[src.momentum_Pa,edge],[src.water_W,water],[src.solid_W,solid],[src.hx_secondary_W,hx],[src.hx_primary_W,hx]] as [BenchmarkMatrix,string[]][]) {
  coverage(record,ids)
  if(record.values.some(row=>row.length!==manifest.time.N_t))throw new Error('Manufactured source sample shape mismatch')
 }
 const ini=benchmark.initial_state,rate=benchmark.initial_temperature_rate
 for(const [record,ids] of [[ini.node_pressure_Pa,node],[ini.flow_kg_s,edge],[ini.node_temperature_K,node],
  [ini.water_temperature_K,water],[ini.solid_temperature_K,solid],[ini.hx_secondary_K,hx],[ini.hx_primary_K,hx],
  [rate.water_temperature_K,water],[rate.solid_temperature_K,solid],[rate.hx_secondary_K,hx],[rate.hx_primary_K,hx],[benchmark.flow_signs,edge]] as [BenchmarkVector,string[]][])coverage(record,ids)
 const signs=new Map(benchmark.flow_signs.ids.map((id,i)=>[id,benchmark.flow_signs.values[i]!]))
 if([...signs.values()].some(s=>s!==1&&s!==-1)||ini.flow_kg_s.ids.some((id,i)=>ini.flow_kg_s.values[i]!*signs.get(id)!<=1e-8))
  throw new Error('Manufactured initial flow regime mismatch')
 if([ini.node_temperature_K,ini.water_temperature_K,ini.solid_temperature_K,ini.hx_secondary_K,ini.hx_primary_K].some(r=>r.values.some(v=>v<=0)))
  throw new Error('Manufactured initial temperature must be Kelvin')
 const scalars=Object.values(src).reduce((n,r)=>n+r.values.reduce((a,b)=>a+b.length,0),0)
 if(scalars>4_000_000||new TextEncoder().encode(JSON.stringify(benchmark)).length>32*1024*1024)throw new Error('Manufactured source resource cap exceeded')
}
