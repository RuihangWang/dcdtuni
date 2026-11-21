/** Physical scenario construction shared by the React controls and browser automation. */
import { compileDynamicLiquidGraph, type DynamicLiquidGraph, type EquipmentPack } from './dynamicLiquidGraph'
import { domainModesOf, projectAtStart, sourceSchemaVersion, transientModel, transientOf } from '../transient/model'
import { liquidTimeline, lowerEventsToSeries, scheduleLiquidInputs, type LiquidForm } from '../transient/runSchedules'
import type { CfdProject } from '../schema/cfdproj'
import { schema } from '../legacy/cfdprojModules'
import { makeLiquidrunV2, type LiquidController } from '../schema/liquidrun'
import { makeLiquidrunDAEV3, makeLiquidrunDAEV4, mockDAEChoices, type SavedDAEConfig, type MockDAEOptions, type TransientProvenance } from '../schema/liquidrunDAE'
import { cfdrunExport } from '../legacy/cfdrunBuild'
/** Creation-time intent for the facility supply temperature (primary inlet).
 * Mirrors the loadStep pattern: the SETTINGS carry the intent; the compiler
 * expands it into primary_circuits[].inlet_K (scalar | list[N_t], both already
 * v3-schema-native). Sample semantics match the MMS acceptance script: sample
 * i carries the value active on (t_{i-1}, t_i] — a step at stepTime takes
 * effect at the first sample with i*dt >= stepTime. */
export type SupplySchedule =
  | {mode:'fixed'}
  | {mode:'step';from:number;to:number;stepTime:number}
  | {mode:'ramp';from:number;to:number;stepTime:number;rampTime:number}
  | {mode:'sinusoid';mean:number;amplitude:number;period:number}

export interface ExperimentSettings {
  mode:'manual'|'temperature'|'pressure';pumpId:string;measurementId:string;
  setpoint:number;kp:number;ki:number;bias:number;
  loadStep:boolean;stepTime:number;loadFactor:number;
  supplySchedule?:SupplySchedule;
}

export function expandSupplySchedule(schedule:SupplySchedule|undefined,baseInlet:number,nT:number,dtS:number):number|number[] {
  if(!schedule||schedule.mode==='fixed')return baseInlet
  const positive=(value:number,name:string)=>{
    if(typeof value!=='number'||!Number.isFinite(value)||value<=0)throw new Error(`${name} must be a positive finite number`)
  }
  const onGrid=(stepTime:number)=>{
    if(!Number.isFinite(stepTime)||stepTime<=0||stepTime>(nT-1)*dtS||Math.abs(stepTime/dtS-Math.round(stepTime/dtS))>1e-8)
      throw new Error('Supply-temperature change must lie on the simulation time grid')
  }
  if(schedule.mode==='step'){
    positive(schedule.from,'Supply schedule start');positive(schedule.to,'Supply schedule target');onGrid(schedule.stepTime)
    return Array.from({length:nT},(_,i)=>i*dtS>=schedule.stepTime?schedule.to:schedule.from)
  }
  if(schedule.mode==='ramp'){
    positive(schedule.from,'Supply schedule start');positive(schedule.to,'Supply schedule target')
    onGrid(schedule.stepTime);positive(schedule.rampTime,'Supply ramp duration')
    return Array.from({length:nT},(_,i)=>{
      const t=i*dtS
      if(t<schedule.stepTime)return schedule.from
      return schedule.from+(schedule.to-schedule.from)*Math.min(1,(t-schedule.stepTime)/schedule.rampTime)
    })
  }
  positive(schedule.mean,'Supply schedule mean');positive(schedule.period,'Supply schedule period')
  if(typeof schedule.amplitude!=='number'||!Number.isFinite(schedule.amplitude)||schedule.amplitude<0)
    throw new Error('Supply schedule amplitude must be finite and nonnegative')
  if(schedule.mean-schedule.amplitude<=0)
    throw new Error('Supply schedule must stay positive (Kelvin) at every sample')
  return Array.from({length:nT},(_,i)=>schedule.mean+schedule.amplitude*Math.sin(2*Math.PI*(i*dtS)/schedule.period))
}

/** Compile-time application: the project-saved dae config keeps its scalar
 * inlet; only the compiled manifest carries the expanded list, and the
 * expansion is recorded in source_reference for provenance. */
export function applySupplySchedule(dae:SavedDAEConfig,schedule:SupplySchedule|undefined,nT:number,dtS:number):SavedDAEConfig {
  if(!schedule||schedule.mode==='fixed')return dae
  // Steady analysis has N_t=1 and no time axis: a schedule is meaningless and
  // its grid validation would reject every positive stepTime. Ignore it (the
  // panel also disables the editor in steady mode); the scalar binding stays.
  if(dae.analysis.mode==='steady'||nT<2)return dae
  return {...dae,primary_circuits:dae.primary_circuits.map(circuit=>{
    const base=Array.isArray(circuit.inlet_K)?circuit.inlet_K[0]!:circuit.inlet_K
    return {...circuit,
      inlet_K:expandSupplySchedule(schedule,base,nT,dtS) as number[],
      source_reference:`${circuit.source_reference} Supply schedule ${JSON.stringify(schedule)} expanded at compile time (N_t=${nT}, dt=${dtS}s).`}
  })}
}
/** Keyframed primary-circuit inlet/flow as N_t series, recorded in source_reference as a supply schedule is. */
function applyCircuitSeries(dae:SavedDAEConfig,circuits:Map<string,Partial<Record<'inlet_K'|'flow_kg_s',number[]>>>,nT:number,dtS:number):SavedDAEConfig {
  return {...dae,primary_circuits:dae.primary_circuits.map(circuit=>{
    const series=circuits.get(circuit.edge_id)
    if(!series)return circuit
    return {...circuit,...series,
      source_reference:`${circuit.source_reference} Keyframed ${Object.keys(series).join(' and ')} sampled from the project timeline at compile time (N_t=${nT}, dt=${dtS}s).`}
  })}
}
export interface SavedExperiment {
  /** Lightweight intent only: reading it never builds a graph or supplies HX bindings. */
  engine?: 'newton-dae'
  duration_s: number
  dt_s: number
  settings: ExperimentSettings
  parameterPack?: EquipmentPack
  dae?: SavedDAEConfig
}
/** Project-owned inputs. Transport/playback speed is deliberately not physics. */
export function readExperiment(project: CfdProject | null): SavedExperiment {
  const saved = (project as unknown as {runDefaults?: {liquid?: {experiment?: Partial<SavedExperiment>}}})
    ?.runDefaults?.liquid?.experiment
  return {
    ...(!saved ? {engine:'newton-dae' as const} : saved.engine !== undefined ? {engine:saved.engine} : {}),
    duration_s: saved?.duration_s ?? 180,
    dt_s: saved?.dt_s ?? 1,
    settings: {mode:'manual',pumpId:'',measurementId:'',setpoint:45,kp:.03,ki:.002,bias:.8,
      loadStep:false,stepTime:60,loadFactor:1.5,...saved?.settings},
    ...(saved?.parameterPack ? {parameterPack:structuredClone(saved.parameterPack)} : {}),
    ...(saved?.dae ? {dae:structuredClone(saved.dae)} : {}),
  }
}
/** Resolve missing Newton inputs for compilation, without editing the source.
 * Existing explicit legacy experiments remain v2. Prepare/export persists this
 * value through the normal store edit before computing the project hash.
 */
export function resolveSavedExperiment(project:CfdProject):SavedExperiment {
  const experiment=readExperiment(project)
  const errors=schema().validateLiquidExperiment(experiment)
  if(errors.length)throw new Error(errors.map(e=>e.path+': '+e.msg).join('; '))
  if(experiment.engine==='newton-dae') {
    if(experiment.settings.mode!=='manual')throw new Error('Newton DAE requires manual schedules; saved PID settings are not discarded')
    const mode=domainModesOf(project).liquid
    if(!experiment.dae) {
      const selected=adoptMockDAEExperiment(project,{
        analysis:{mode,initialization:'steady'},
        source_reference:'Synthetic default adopted at Prepare/export: 1 kg/s primary inlet at 293.15 K, 0.02 m3 on each HX side; primary fluid cp/rho follow the saved project fluid. Not measured or calibrated vendor data.',
      })
      selected.dae!.execution={backend:'pytorch',device:'cpu',dtype:'float64',max_threads:2}
      return {...experiment,parameterPack:selected.parameterPack,dae:selected.dae}
    }
    // Marked inputs take their mode from the top-bar domain choice. Unmarked
    // saved v3 independent-clock inputs retain their existing compatibility.
    if(experiment.dae.analysis.mode!==mode||(mode==='steady'&&experiment.dae.analysis.initialization!=='steady')) {
      return {...experiment,dae:{...experiment.dae,analysis:{...experiment.dae.analysis,mode,
        ...(mode==='steady'?{initialization:'steady' as const}:{})}}}
    }
  }
  return experiment
}
export function compileSavedExperiment(source: CfdProject) {
  // Steady/v3 compile reads the start values (t = 0) whatever the playhead or
  // file form; the content hash is of the canonical disk form either way.
  const project=projectAtStart(source)
  const experiment=resolveSavedExperiment(project)
  const errors=schema().validateLiquidExperiment(experiment)
  if(errors.length)throw new Error(errors.map(e=>`${e.path}: ${e.msg}`).join('; '))
  // A time-marching run of a transient v5 project follows its timeline: the
  // timeline's duration, and its keyframes sampled on the dt grid (plan T2.5).
  const timeline=liquidTimeline(source,!experiment.dae||experiment.dae.analysis.mode==='transient')
  const options={duration_s:timeline?.durationS??experiment.duration_s,dt_s:experiment.dt_s,parameterPack:experiment.parameterPack}
  const graph=compileDynamicLiquidGraph(project,options)
  // .liquidrun v4 (design §3) for a DAE run the timeline drives or one using a
  // v4 endpoint form; otherwise the file stays v3 as before. A v2 graph run
  // cannot take the new forms at all.
  const v4Forms=usesV4Forms(graph)
  if(!experiment.dae&&v4Forms)throw new Error('A held secondary supply, a flow-held pump or a temperature-rise cold plate needs the DAE solver (.liquidrun v4)')
  const form:LiquidForm=!experiment.dae?'v2':(timeline||v4Forms||experiment.dae.manufactured_benchmark)?'v4':'v3'
  const schedule=timeline?scheduleLiquidInputs(source,timeline,graph,p=>compileDynamicLiquidGraph(p,options),{
    form,circuits:experiment.dae?.primary_circuits.map(c=>c.edge_id)??[],
    loadStep:experiment.settings.loadStep,supplySchedule:(experiment.settings.supplySchedule?.mode??'fixed')!=='fixed',
  }):null
  const built=buildExperiment(graph,experiment.settings)
  if(schedule)built.liquidrun.events.push(...schedule.events)
  if(experiment.dae) {
    if(built.controllers.length)throw new Error('DAE input requires manual schedules; PID is not supported')
    let dae=applySupplySchedule(experiment.dae,experiment.settings.supplySchedule,
      built.liquidrun.time.N_t,built.liquidrun.time.dt_s)
    if(schedule?.circuits.size)dae=applyCircuitSeries(dae,schedule.circuits,built.liquidrun.time.N_t,built.liquidrun.time.dt_s)
    if(form==='v4') {
      lowerEventsToSeries(built.liquidrun)
      const start=transientOf(source).start
      if(timeline) {
        // The timeline's start decides the initial field (design §3): the
        // steady converged field, or a uniform one at the given temperatures.
        const uniform=start.kind==='uniform'
        dae={...dae,analysis:{...dae.analysis,initialization:dae.manufactured_benchmark?'consistent':uniform?'consistent':'steady'}}
        if(uniform) {
          const u=start.uniform??{}
          const conditions=built.liquidrun.initial_conditions as {T_fluid_K:number;T_solid_K:number}
          if(Number.isFinite(u.waterT_C))conditions.T_fluid_K=Number(u.waterT_C)+273.15
          if(Number.isFinite(u.solidT_C))conditions.T_solid_K=Number(u.solidT_C)+273.15
        }
      }
      const registry=new Map(transientModel().registry().params.map(p=>[p.key,p.mode]))
      const provenance:TransientProvenance=JSON.parse(JSON.stringify({
        durationS:timeline?.durationS??experiment.duration_s,start,
        tracks:transientOf(source).tracks.filter(t=>registry.get(t.param)==='liquid').map(({modelUid,param,target})=>({modelUid,param,target})),
      }))
      return {liquidrun:makeLiquidrunDAEV4(built.liquidrun,dae,{
        projectUid:project.uid??'',projectName:built.liquidrun.projectName,
        projectSha256:cfdrunExport().projectContentHashSync(source),
      },provenance),controllers:[] as LiquidController[]}
    }
    return {liquidrun:makeLiquidrunDAEV3(built.liquidrun,dae,{
      projectUid:project.uid??'',projectName:built.liquidrun.projectName,
      projectSha256:cfdrunExport().projectContentHashSync(source),schemaVersion:sourceSchemaVersion(source),
    }),controllers:[] as LiquidController[]}
  }
  return {liquidrun:makeLiquidrunV2(built.liquidrun,{
    projectUid:project.uid??'',projectName:built.liquidrun.projectName,controllers:built.controllers,
    schemaVersion:sourceSchemaVersion(source),
  }),controllers:built.controllers}
}

/** Does the graph use a .liquidrun v4 endpoint form? */
function usesV4Forms(graph:DynamicLiquidGraph):boolean {
  return graph.edges.some(edge=>edge.params.mode==='flow'||edge.params.mode==='setpoint'||edge.params.heat_mode==='temperature_rise')
}

/** Explicit opt-in. The caller persists this returned value with the normal
 * project edit API; an old v2 project is not changed by merely reading it.
 */
export function adoptMockDAEExperiment(project:CfdProject,options:MockDAEOptions={}):SavedExperiment {
  const experiment=readExperiment(project)
  if(experiment.settings.mode!=='manual')throw new Error('Select manual control before explicitly adopting mock DAE; PID settings are not discarded')
  const graph=compileDynamicLiquidGraph(project,{
    duration_s:experiment.duration_s,dt_s:experiment.dt_s,parameterPack:experiment.parameterPack,
  })
  const selected={...experiment,...mockDAEChoices(graph,options)}
  const errors=schema().validateLiquidExperiment(selected)
  if(errors.length)throw new Error(errors.map(e=>`${e.path}: ${e.msg}`).join('; '))
  return selected
}
export function buildExperiment(graph:DynamicLiquidGraph, settings:ExperimentSettings) {
  if(!['manual','temperature','pressure'].includes(settings.mode))throw new Error('Unknown controller mode')
  const liquidrun=structuredClone(graph)
  const {dt_s,N_t}=graph.time
  if(settings.loadStep) {
    const {stepTime,loadFactor}=settings
    if(!Number.isFinite(stepTime)||stepTime<=0||stepTime>(N_t-1)*dt_s||Math.abs(stepTime/dt_s-Math.round(stepTime/dt_s))>1e-8)throw new Error('Load step must lie on the simulation time grid')
    if(!Number.isFinite(loadFactor)||loadFactor<0)throw new Error('Load multiplier must be finite and nonnegative')
    for(const edge of liquidrun.edges.filter(e=>e.type==='cold_plate')) {
      const baseline=Number(edge.params.Q_W)
      if(!Number.isFinite(baseline)||baseline<0)throw new Error(`Invalid baseline heat load on ${edge.id}`)
      liquidrun.events.push({time_s:stepTime,target:edge.id,field:'Q_W',value:baseline*loadFactor})
    }
  }
  const controllers:LiquidController[]=[]
  if(settings.mode!=='manual') {
    const pump=graph.edges.find(e=>e.id===settings.pumpId&&e.type==='pump')
    if(!pump)throw new Error('Select a pump from this physical model')
    if(![settings.setpoint,settings.kp,settings.ki,settings.bias].every(Number.isFinite)||settings.kp<0||settings.ki<0||settings.bias<.2||settings.bias>1)throw new Error('PID gains must be nonnegative and initial output must be 0.2–1.0')
    const temperature=settings.mode==='temperature'
    if(temperature&&!graph.edges.some(e=>e.id===settings.measurementId&&e.type==='cold_plate'))throw new Error('Select a cold plate from this physical model')
    if(temperature&&settings.setpoint<=-273.15)throw new Error('Temperature setpoint must exceed absolute zero')
    if(!temperature&&settings.setpoint<=0)throw new Error('Pressure setpoint must be positive')
    controllers.push({id:'editor-pump-pid',measurement:temperature?
      {kind:'solid_temperature',target_id:settings.measurementId}:
      {kind:'pressure_difference',target_id:pump.to,reference_id:pump.from},
      actuator:{edge_id:pump.id,field:'rpm_ratio'},
      pid:{kp:settings.kp/(temperature?1:1000),ki:settings.ki/(temperature?1:1000),kd:0,
        setpoint:temperature?settings.setpoint+273.15:settings.setpoint*1000,action:temperature?'direct':'reverse',
        bias:settings.bias,output_min:.2,output_max:1,rate_limit_per_s:.05},sample_period_s:dt_s})
  }
  return {liquidrun,controllers}
}
