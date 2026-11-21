/** Runtime transport; simulation ownership stays on the server across reconnects. */
export type RuntimeStatus = 'ready'|'running'|'paused'|'completed'|'cancelled'|'failed'
export interface RuntimePIDControl {measurement:number;setpoint:number;output:number;saturated:boolean;rate_limited:boolean}
export interface RuntimeDAEControls {pump_rpm_ratios:Record<string,number>;valve_openings:Record<string,number>;primary_inlet_K:Record<string,number>}
export interface RuntimeFrame {
  kind:'frame'; run_id:string; seq:number; time_s:number;
  node_pressure_Pa:number[]; node_temperature_K:number[]; edge_flow_kg_s:number[];
  chip_temp_K:number[]; edge_outlet_temp_K:number[];
  controls:Record<string,RuntimePIDControl>|RuntimeDAEControls;
  diagnostics:Record<string,unknown>;
  heat_multiplier?:number;primary_return_K?:number[];secondary_supply_K?:number[];
  pump_rpm_ratios?:Record<string,number>;valve_openings?:Record<string,number>;
  primary_inlet_K?:Record<string,number>;
}
export type RuntimeEquilibrium=Omit<RuntimeFrame,'kind'|'time_s'> & {kind:'equilibrium';evaluated_at_s:number;input_basis?:string}
export interface RuntimeMetadata {
  node_ids:string[]; edge_ids:string[]; cp_edge_ids:string[];
  total_sample_count:number; dt_s:number; config_hash?:string;
  object_bindings:Record<string,{label:string;kind:string;node_ids:string[];edge_ids:string[];parent_uid?:string}>;
  parameter_provenance?:Record<string,unknown>;
  solver?:string;hx_edge_ids?:string[];pending_heat_multiplier?:number;
  pump_edge_ids?:string[];valve_edge_ids?:string[];
  /** Curve pumps accepting RPM commands. An empty list explicitly disables RPM controls. */
  pump_rpm_control_edge_ids?:string[];
  pending_pump_rpm_ratios?:Record<string,number>;pending_valve_openings?:Record<string,number>;
  pending_primary_inlet_K?:Record<string,number>;
}
export interface RuntimeSession {run_id:string;status:RuntimeStatus;config_hash:string;metadata:RuntimeMetadata;frame:RuntimeFrame;error?:string|null}
export interface RuntimeHeatAudit {
  format:'hybrid-cold-plate-heat-audit/v1';project_uid:string;liquid_run_id:string;
  accepted_prefix_complete:boolean;identity_verified:boolean;auditable:boolean;handoff_ready:boolean;
  audited_server_count:number;checked_server_samples:number;unchecked_server_samples:number;
  max_abs_closure_residual_W:number|null;time_s:number[];
}
export interface RuntimeClientState {
  session:RuntimeSession|null; frames:RuntimeFrame[]; projectRevision:number|null;
  busy:boolean; error:string|null; connection:'idle'|'connected'|'reconnecting';
}
const EMPTY:RuntimeClientState = {session:null,frames:[],projectRevision:null,busy:false,error:null,connection:'idle'}
let state:RuntimeClientState = EMPTY
let generation=0
let frameCursor=-1
let lifecycleRevision=0
let refreshSerial=0
let appliedRefresh=0
let timer:ReturnType<typeof setTimeout>|null=null
let polling=false
const listeners = new Set<()=>void>()
export function subscribeRuntime(listener:()=>void) {listeners.add(listener);return ()=>{listeners.delete(listener)}}
export function runtimeState() {return state}
function update(patch:Partial<RuntimeClientState>) {state={...state,...patch};listeners.forEach(f=>f())}
function errorText(error:unknown) {return error instanceof Error?error.message:String(error)}
export function stopRuntimePolling() {polling=false;if(timer!==null)clearTimeout(timer);timer=null}
export function resetRuntimeClient() {stopRuntimePolling();generation++;frameCursor=-1;state=EMPTY;listeners.forEach(f=>f())}

async function request(path:string,body?:unknown) {
  const response=await fetch(path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  const value=await response.json()
  if(!response.ok)throw new Error(value?.error||`Request failed (${response.status})`)
  return value
}
function checkedFrame(value:RuntimeFrame, id:string, metadata:RuntimeMetadata):RuntimeFrame {
  if(!value||value.kind!=='frame'||value.run_id!==id)throw new Error('Frame belongs to a different liquid run')
  if(!Number.isSafeInteger(metadata.total_sample_count)||metadata.total_sample_count<1||!Number.isFinite(metadata.dt_s)||metadata.dt_s<=0||!Number.isSafeInteger(value.seq)||value.seq<0||value.seq>=metadata.total_sample_count||!Number.isFinite(value.time_s)||Math.abs(value.time_s-value.seq*metadata.dt_s)>1e-8*Math.max(1,value.time_s))throw new Error('Invalid simulation frame time')
  for(const [values,size] of [
    [value.node_pressure_Pa,metadata.node_ids.length], [value.node_temperature_K,metadata.node_ids.length],
    [value.edge_flow_kg_s,metadata.edge_ids.length], [value.chip_temp_K,metadata.cp_edge_ids.length],
    [value.edge_outlet_temp_K,metadata.edge_ids.length],
  ] as [number[],number][]) {
    if(!Array.isArray(values)||values.length!==size||values.some(v=>typeof v!=='number'||!Number.isFinite(v)))throw new Error('Simulation frame does not match its physical model')
  }
  if(metadata.solver==='differentiable_newton_dae') {
    for(const values of [value.primary_return_K,value.secondary_supply_K]) {
      if(!Array.isArray(values)||values.length!==metadata.hx_edge_ids?.length||values.some(v=>!Number.isFinite(v)))throw new Error('DAE frame has invalid primary/secondary temperatures')
    }
  }
  return value
}
function mergeFrames(incoming:RuntimeFrame[], session:RuntimeSession) {
  // Validate the complete response before changing any displayed state.
  const valid=incoming.map(f=>checkedFrame(f,session.run_id,session.metadata))
  const frames=new Map(state.frames.map(f=>[f.seq,f]))
  valid.forEach(f=>frames.set(f.seq,f))
  return [...frames.values()].sort((a,b)=>a.seq-b.seq)
}

export async function createRuntimeSession(liquidrun:unknown,controllers:unknown[],projectRevision:number):Promise<RuntimeSession|null> {
  stopRuntimePolling()
  const token=++generation
  update({busy:true,error:null,connection:'idle'})
  try {
    const session=await request('/api/liquid/runs',{liquidrun,controllers,project_revision:projectRevision}) as RuntimeSession
    if(token!==generation)return null
    checkedFrame(session.frame,session.run_id,session.metadata)
    frameCursor=session.frame.seq===0?0:-1
    update({session,frames:[session.frame],projectRevision,busy:false,connection:'connected'})
    return session
  } catch(error) {
    if(token!==generation)return null
    update({busy:false,error:errorText(error)})
    throw error
  }
}

export async function refreshRuntimeFrames():Promise<void> {
  const session=state.session
  if(!session)return
  const token=generation
  const after=frameCursor
  const lifecycle=lifecycleRevision
  const requestId=++refreshSerial
  try {
    const payload=await request(`/api/liquid/runs/${encodeURIComponent(session.run_id)}/frames?after=${after}`)
    if(token!==generation||state.session?.run_id!==session.run_id)return
    if(!Array.isArray(payload.frames))throw new Error('Runtime response has no frame sequence')
    const frames=mergeFrames(payload.frames,session)
    frameCursor=Math.max(frameCursor,...payload.frames.map((frame:RuntimeFrame)=>frame.seq))
    const latest=frames.at(-1)??session.frame
    const current=state.session!
    const newest=requestId>=appliedRefresh
    const terminal=['completed','cancelled','failed'].includes(current.status)
    const status=newest&&!terminal&&lifecycle===lifecycleRevision&&!state.busy?(payload.status??current.status):current.status
    if(newest)appliedRefresh=requestId
    update({frames,session:{...current,status,frame:latest,error:newest?(payload.error??current.error):current.error},...(newest?{connection:'connected' as const,error:null}:{})})
  } catch(error) {
    if(token===generation&&requestId>=appliedRefresh){appliedRefresh=requestId;update({connection:'reconnecting',error:errorText(error)})}
    throw error
  }
}

export function startRuntimePolling(intervalMs=500) {
  stopRuntimePolling();polling=true
  const token=generation
  let failures=0
  const poll=async()=>{
    if(!polling||token!==generation)return
    try {await refreshRuntimeFrames();failures=0}catch{failures++}
    if(!polling||token!==generation)return
    const status=state.session?.status
    if(status==='completed'||status==='cancelled'||status==='failed') {stopRuntimePolling();return}
    timer=setTimeout(()=>{void poll()},Math.min(5000,intervalMs*Math.max(1,2**Math.min(failures,4))))
  }
  timer=setTimeout(()=>{void poll()},intervalMs)
}

export async function runtimeAction(action:'start'|'pause'|'resume'|'cancel'|'step',speed:number|null=null):Promise<void> {
  const session=state.session
  if(!session)throw new Error('No liquid simulation session')
  lifecycleRevision++
  const token=generation
  update({busy:true,error:null})
  try {
    const next=await request(`/api/liquid/runs/${encodeURIComponent(session.run_id)}/${action}`,action==='start'||action==='resume'?{speed}:{}) as RuntimeSession
    if(token!==generation||state.session?.run_id!==session.run_id)return
    if(next.run_id!==session.run_id)throw new Error('Operation acknowledgment belongs to a different run')
    const frames=mergeFrames(next.frame?[next.frame]:[],session)
    lifecycleRevision++
    update({session:{...session,...next,frame:frames.at(-1)??next.frame},frames,busy:false,connection:'connected'})
    if(action==='start'||action==='resume')startRuntimePolling()
    if(action==='cancel')stopRuntimePolling()
  } catch(error) {
    if(token===generation)update({busy:false,error:errorText(error)})
    throw error
  }
}


/** Raw artifact bytes EXACTLY as the server serialized them.
 * The parsed-JSON download path round-trips through JavaScript numbers, which
 * silently rewrites float/int distinctions and exponential notation (100.0 →
 * 100, 1e-05 → 0.00001): the exported checkpoint then fails the canonical
 * config-hash check on replay, defeating "可独立复现". Exports must be
 * byte-faithful; the server already validates/materializes these artifacts
 * (executed-input re-passes LiquidrunDAEV3 there), so no client-side schema
 * gate is lost by shipping the text verbatim. */
export async function fetchRuntimeArtifactText(kind:'result'|'checkpoint'|'executed-input'):Promise<string> {
  const session=state.session,token=generation
  if(!session)throw new Error('No liquid simulation session')
  if(kind==='executed-input'&&session.metadata.solver!=='differentiable_newton_dae')throw new Error('Accepted input export requires a DAE run')
  const response=await fetch(`/api/liquid/runs/${encodeURIComponent(session.run_id)}/${kind}`)
  if(!response.ok) {
    let message=`Request failed (${response.status})`
    try {const value=await response.json();if(value?.error)message=value.error} catch {/* keep the status text */}
    throw new Error(message)
  }
  const text=await response.text()
  if(token!==generation||state.session?.run_id!==session.run_id)throw new Error('Artifact belongs to an earlier run')
  return text
}

export async function fetchRuntimeHeatAudit(projectUid:string):Promise<RuntimeHeatAudit> {
  const session=state.session,token=generation
  if(!session||session.metadata.solver!=='differentiable_newton_dae')throw new Error('Liquid heat audit requires a DAE run')
  if(!projectUid.trim())throw new Error('Liquid heat audit requires a project UID')
  const value=await request(`/api/liquid/runs/${encodeURIComponent(session.run_id)}/heat-audit?project_uid=${encodeURIComponent(projectUid)}`) as RuntimeHeatAudit
  if(token!==generation||state.session?.run_id!==session.run_id)throw new Error('Liquid heat audit belongs to an earlier run')
  const residual=value?.max_abs_closure_residual_W
  if(value?.format!=='hybrid-cold-plate-heat-audit/v1'||value.project_uid!==projectUid||value.liquid_run_id!==session.run_id||
     !Number.isSafeInteger(value.audited_server_count)||!Number.isSafeInteger(value.checked_server_samples)||
     value.audited_server_count<0||value.checked_server_samples<0||
     typeof value.identity_verified!=='boolean'||typeof value.auditable!=='boolean'||
     !Array.isArray(value.time_s)||!value.time_s.length||value.time_s.some(time=>!Number.isFinite(time))||
     (value.auditable?(typeof residual!=='number'||!Number.isFinite(residual)||residual<0):residual!==null))
    throw new Error('Liquid heat audit does not match the active project and run')
  return value
}

export interface RuntimeControlInputs {
  heat_multiplier?:number
  pump_rpm_ratios?:Record<string,number>
  valve_openings?:Record<string,number>
  primary_inlet_K?:Record<string,number>
}

export async function runtimeControlInputs(inputs:RuntimeControlInputs):Promise<void> {
  const allowed=['heat_multiplier','pump_rpm_ratios','valve_openings','primary_inlet_K'] as const
  if(!inputs||typeof inputs!=='object'||Array.isArray(inputs)||
     Object.keys(inputs).some(key=>!allowed.includes(key as typeof allowed[number]))||
     (inputs.heat_multiplier===undefined&&inputs.pump_rpm_ratios===undefined&&inputs.valve_openings===undefined&&inputs.primary_inlet_K===undefined))
    throw new Error('Provide a DAE heat, pump, valve or supply-temperature input')
  if(inputs.heat_multiplier!==undefined&&(!Number.isFinite(inputs.heat_multiplier)||inputs.heat_multiplier<0))
    throw new Error('Heat multiplier must be finite and nonnegative')
  for(const [field,map] of [['pump_rpm_ratios',inputs.pump_rpm_ratios],['valve_openings',inputs.valve_openings],['primary_inlet_K',inputs.primary_inlet_K]] as const) {
    if(map===undefined)continue
    if(!map||typeof map!=='object'||Array.isArray(map)||!Object.keys(map).length)
      throw new Error(`${field} must be a nonempty edge-ID map`)
    for(const [id,value] of Object.entries(map)) {
      if(field==='primary_inlet_K') {
        // Kelvin boundary: strictly positive, sanity-capped well below any
        // physical liquid-cooling regime; the backend re-validates authoritatively.
        if(!id.trim()||!Number.isFinite(value)||value<=0||value>=1000)
          throw new Error('Supply temperature must be a positive Kelvin value below 1000 K on a nonempty edge ID')
        continue
      }
      if(!id.trim()||!Number.isFinite(value)||(field==='valve_openings'?value<=0||value>1:value<0))
        throw new Error(field==='valve_openings'?'Valve opening must be positive and at most 1':'Pump speed ratio must be finite and nonnegative')
    }
  }
  const session=state.session
  if(!session)throw new Error('No liquid simulation session')
  if(session.metadata.solver!=='differentiable_newton_dae')throw new Error('Live control inputs require a DAE run')
  if(state.busy)throw new Error('A liquid request is already in progress')
  const token=generation; lifecycleRevision++
  update({busy:true,error:null})
  try {
    const next=await request(`/api/liquid/runs/${encodeURIComponent(session.run_id)}/inputs`,inputs) as RuntimeSession
    if(token!==generation||state.session?.run_id!==session.run_id)return
    if(next.run_id!==session.run_id)throw new Error('Input acknowledgment belongs to another run')
    const frames=mergeFrames([next.frame],next)
    lifecycleRevision++
    update({session:{...next,frame:frames.at(-1)!},frames,busy:false})
  }catch(error){if(token===generation)update({busy:false,error:errorText(error)});throw error}
}

export function runtimeHeatInput(heat_multiplier:number):Promise<void> {
  return runtimeControlInputs({heat_multiplier})
}

/** A steady query is displayed separately; it must never enter transient frames. */
export async function runtimeEquilibrium(heat_multiplier?:number):Promise<RuntimeEquilibrium> {
  const session=state.session
  if(!session)throw new Error('No liquid simulation session')
  const token=generation
  update({busy:true,error:null})
  try {
    const result=await request(`/api/liquid/runs/${encodeURIComponent(session.run_id)}/equilibrium`,
      heat_multiplier===undefined?{}:{heat_multiplier}) as RuntimeEquilibrium
    if(token!==generation||state.session?.run_id!==session.run_id)throw new Error('Equilibrium belongs to an earlier run')
    if(result.kind!=='equilibrium')throw new Error('Invalid equilibrium response')
    checkedFrame({...result,kind:'frame',time_s:result.evaluated_at_s},session.run_id,session.metadata)
    update({busy:false});return result
  }catch(error){if(token===generation)update({busy:false,error:errorText(error)});throw error}
}
