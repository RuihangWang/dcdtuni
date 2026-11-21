import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { Hint } from './Hint'
import { Icon } from './Icon'
import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'
import { compileDynamicLiquidGraph } from '../liquid/dynamicLiquidGraph'
import { adoptMockDAEExperiment, compileSavedExperiment, readExperiment, resolveSavedExperiment, savedDAEExecution, withDAEExecution, type ExperimentSettings, type SavedExperiment, type SupplySchedule } from '../liquid/liquidExperiment'
import { schema } from '../legacy/cfdprojModules'
import { toCelsius } from '../liquid/liquidResults'
import * as runtime from '../liquid/liquidRuntimeClient'
import { captureCompilerAlerts } from '../liquid/liquidReadiness'
import { withCduPrimaryInputs } from '../liquid/cduInputs'
import { hybridData, refreshHybridData, subscribeHybridData, ensureHybridData } from '../liquid/hybridData'
import { projectSha256 } from '../liquid/hybridWorkflow'
import type { SavedLiquidRun } from '../liquid/liquidSavedRuns'
import { LiquidSolverPanel } from './LiquidSolverPanel'
import { LiquidFluidEditor } from './LiquidFluidEditor'
import { LiquidReadinessCard, useLiquidReadiness } from './LiquidReadinessCard'
import { HybridWorkflowPanel, type HybridTarget } from './HybridWorkflowPanel'
import { NumberField } from './NumberField'
import type { DAEExecution, DAENewton } from '../schema/liquidrunDAE'
import { useTranslation } from '../useTranslation'
import { t, type TranslationKey } from '../i18n'
import { domainModesOf, transientOf } from '../transient/model'
import './LiquidWorkflow.css'

// Frame updates can arrive before the next metadata snapshot. A queued value
// that the latest accepted frame already carries is no longer pending in the UI.
function needsAcceptance(queued:number|undefined,accepted:number|undefined):queued is number {
  return queued!==undefined&&queued!==accepted
}
const KELVIN=273.15
const celsius=(kelvin:number)=>Number((kelvin-KELVIN).toFixed(6))

/** Run `fn`, turning the shared graph compiler's alert() into the thrown error. */
function compiled<T>(fn:()=>T):T {
  const result=captureCompilerAlerts(fn)
  if(result.error!==null||result.value===undefined) {
    const message=result.error instanceof Error?result.error.message:String(result.error??'')
    const detail=[...result.alerts.flatMap(a=>a.split('\n')).map(s=>s.trim()).filter(s=>s&&!/^Liquid graph validation failed:?$/.test(s)),message].filter(Boolean).join('; ')
    throw new Error(t('lq_ux.err_compile',{error:detail}))
  }
  return result.value
}

const statusLabels:Record<runtime.RuntimeStatus,TranslationKey>={ready:'lce.status_ready',running:'lce.status_running',paused:'lce.status_paused',completed:'lce.status_completed',cancelled:'lce.status_cancelled',failed:'lce.status_failed'}

/**
 * The liquid solver tab, in the order a run is built:
 *   ① model readiness  ② operating conditions  ③ solver settings  ④ run  ⑤ export and audit.
 * The hybrid workflow sits above them when the project has fan-cooled liquid servers.
 */
export function LiquidExperimentPanel({project,onNavigate}:{project:CfdProject|null;onNavigate?:(target:HybridTarget)=>void}) {
  const {t}=useTranslation()
  const state=useSyncExternalStore(runtime.subscribeRuntime,runtime.runtimeState)
  const runtimeError=runtime.runtimeFailureMessage(state.session,t('lce.status_failed'))
  const revision=useSyncExternalStore(store.subscribe,store.documentInputRevision)
  const readiness=useLiquidReadiness(project)
  const experiment=readExperiment(project)
  const {duration_s:duration,dt_s:dt,settings}=experiment
  // In a transient project the timeline owns the duration and load / supply
  // changes are keyframes (plan T2c.5); a legacy one still switched on stays
  // shown rather than acting unseen. A steady project keeps them all.
  const timeline=domainModesOf(project).liquid==='transient'?transientOf(project):null
  const showLoadStep=!timeline||settings.loadStep
  const showSupply=!timeline||(settings.supplySchedule?.mode??'fixed')!=='fixed'
  // Fastest by default: 1×/10× make the server wait dt/speed per step, which
  // throttles the computation itself (run_manager); they are demonstration speeds.
  const [speed,setSpeed]=useState('fast')
  const [creating,setCreating]=useState(false)
  const [error,setError]=useState<string|null>(null)
  // A key, not text: a notice re-renders in the new language after a switch.
  const [notice,setNotice]=useState<{key:TranslationKey;vars?:Record<string,string|number>}|null>(null)
  const [equilibrium,setEquilibrium]=useState<runtime.RuntimeEquilibrium|null>(null)
  const [heatAudit,setHeatAudit]=useState<runtime.RuntimeHeatAudit|null>(null)
  const [catalog,setCatalog]=useState<{pumps:string[];plates:string[]}>({pumps:[],plates:[]})
  const [selectedPump,setSelectedPump]=useState('')
  const [selectedValve,setSelectedValve]=useState('')
  const [pumpRatio,setPumpRatio]=useState(1)
  const [valveOpening,setValveOpening]=useState(1)
  const [selectedHx,setSelectedHx]=useState('')
  const [inletC,setInletC]=useState(21.85)
  const save=(patch:Partial<SavedExperiment>)=>store.setRunConfig('liquid','experiment',{...readExperiment(store.getProject()),...patch})
  const change=<K extends keyof ExperimentSettings>(key:K,value:ExperimentSettings[K])=>save({settings:{...settings,[key]:value}})
  const status=state.session?.status
  const frame=state.session?.frame
  const readOnly=state.session?.read_only===true
  const hasNextStep=!!state.session&&!readOnly&&(frame?.seq??0)<state.session.metadata.total_sample_count-1
  const active=status==='running'||status==='paused'||(status==='ready'&&hasNextStep)
  const stale=state.session&&state.projectRevision!==revision
  const dae=experiment.dae
  // Mode display follows the same pure choice as compilation. Reading a
  // stale marked input must not show an independent clock or edit the project.
  const analysisMode=experiment.engine==='newton-dae'?domainModesOf(project).liquid:dae?.analysis.mode
  const steady=analysisMode==='steady'
  const initialization=experiment.engine==='newton-dae'&&steady?'steady'
    :timeline&&analysisMode==='transient'?(timeline.start.kind==='uniform'?'consistent':'steady'):dae?.analysis.initialization
  const newtonIntent=experiment.engine==='newton-dae'||!!dae
  const daeRun=state.session?.metadata.solver==='differentiable_newton_dae'
  const rpmControlIds=state.session?.metadata.pump_rpm_control_edge_ids
  // Missing means a legacy backend; [] means this run has no RPM actuators.
  const pumpIds=rpmControlIds===undefined?(state.session?.metadata.pump_edge_ids??[]):rpmControlIds
  const valveIds=state.session?.metadata.valve_edge_ids??[]
  const pumpId=pumpIds.includes(selectedPump)?selectedPump:pumpIds[0]??''
  const valveId=valveIds.includes(selectedValve)?selectedValve:valveIds[0]??''
  const hxIds=state.session?.metadata.hx_edge_ids??[]
  const hxId=hxIds.includes(selectedHx)?selectedHx:hxIds[0]??''
  const queuedPump=state.session?.metadata.pending_pump_rpm_ratios?.[pumpId]
  const queuedValve=state.session?.metadata.pending_valve_openings?.[valveId]
  const queuedInlet=state.session?.metadata.pending_primary_inlet_K?.[hxId]
  const acceptedHeat=frame?.heat_multiplier??1
  const pendingHeat=state.session?.metadata.pending_heat_multiplier??acceptedHeat
  const execution=savedDAEExecution(experiment)
  const actualExecution=runtime.observedRuntimeExecution(state.session?.metadata)
  const threadOptions=[...new Set([1,2,4,execution.max_threads])].sort((a,b)=>a-b)
  const progress=useMemo(()=>state.session?(state.session.metadata.total_sample_count===1?1:(frame?.seq??0)/(state.session.metadata.total_sample_count-1)):0,[state.session,frame])
  const blocked=!!readiness&&!readiness.ready
  useEffect(()=>setEquilibrium(null),[state.session?.run_id])
  useEffect(()=>setHeatAudit(null),[state.session?.run_id,project?.uid])
  function adoptDAE() {
    if(!project)return
    setError(null)
    try {
      const adopted=compiled(()=>withCduPrimaryInputs(project,adoptMockDAEExperiment(project,{analysis:{mode:domainModesOf(project).liquid,initialization:'steady'}})))
      store.setRunConfig('liquid','experiment',adopted)
      setNotice({key:'lce.notice_adopted'})
    }catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  function changeDAEExecution(patch:Partial<DAEExecution>) {
    try {
      const next=withDAEExecution(readExperiment(store.getProject()),{...execution,...patch})
      store.setRunConfig('liquid','experiment',next);setError(null)
    }catch(e){setError(e instanceof Error?e.message:String(e));return false}
  }
  function changeNewton(key:keyof DAENewton,value:number) {
    if(!dae)return false
    const next={...experiment,dae:{...dae,newton:{...dae.newton,[key]:value}}}
    const errors=schema().validateLiquidExperiment(next)
    if(errors.length){setError(errors.map(e=>`${e.path}: ${e.msg}`).join('; '));return false}
    setError(null);save({dae:next.dae})
  }
  async function heat(multiplier:number) {
    setError(null);setEquilibrium(null)
    try {await runtime.runtimeHeatInput(multiplier)}
    catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  async function queuePump() {
    if(!pumpId)return
    setError(null)
    try {await runtime.runtimeControlInputs({pump_rpm_ratios:{[pumpId]:pumpRatio}})}
    catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  async function queueValve() {
    if(!valveId)return
    setError(null)
    try {await runtime.runtimeControlInputs({valve_openings:{[valveId]:valveOpening}})}
    catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  async function queueSupply() {
    if(!hxId)return
    setError(null)
    // Typed in °C like every temperature in the editor; the DAE takes Kelvin.
    try {await runtime.runtimeControlInputs({primary_inlet_K:{[hxId]:inletC+KELVIN}})}
    catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  async function queryEquilibrium() {
    setError(null)
    try {setEquilibrium(await runtime.runtimeEquilibrium(pendingHeat))}
    catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  async function queryHeatAudit() {
    if(!project?.uid)return
    setError(null)
    try {setHeatAudit(await runtime.fetchRuntimeHeatAudit(project.uid))}
    catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  function readCatalog() {
    if(!project)return
    setError(null)
    try {
      const graph=compiled(()=>compileDynamicLiquidGraph(project,{duration_s:duration,dt_s:dt,parameterPack:experiment.parameterPack}))
      const pumps=graph.edges.filter(e=>e.type==='pump').map(e=>e.id)
      const plates=graph.edges.filter(e=>e.type==='cold_plate').map(e=>e.id)
      setCatalog({pumps,plates})
      save({settings:{...settings,pumpId:pumps.includes(settings.pumpId)?settings.pumpId:pumps.length===1?pumps[0]!: '',measurementId:plates.includes(settings.measurementId)?settings.measurementId:plates.length===1?plates[0]!: ''}})
    }catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  function compilePersistedScenario() {
    const current=store.getProject()
    if(!current)throw new Error('No current project')
    return compiled(()=>{
      let selected=resolveSavedExperiment(current)
      if(!selected.parameterPack) {
        const graph=compileDynamicLiquidGraph(current,{
          duration_s:selected.duration_s,dt_s:selected.dt_s,
        })
        selected={...selected,parameterPack:graph.parameter_pack}
      }
      // A CDU's stated primary inlet/flow, not the synthetic default circuit.
      selected=withCduPrimaryInputs(current,selected)
      // Bind the hash to the adopted, saved inputs, never to the pre-edit object.
      if(JSON.stringify(selected)!==JSON.stringify(readExperiment(current))) {
        store.setRunConfig('liquid','experiment',selected)
        if(JSON.stringify(readExperiment(store.getProject()))!==JSON.stringify(selected))throw new Error('Experiment inputs could not be saved')
      }
      return compileSavedExperiment(store.getProject()!)
    })
  }
  async function create(run:boolean) {
    if(!project)return
    setError(null)
    setCreating(true)
    try {
      const scenario=compilePersistedScenario()
      const session=await runtime.createRuntimeSession(scenario.liquidrun,scenario.controllers,store.documentInputRevision())
      // "Initialise and run": a transient session starts at once at the chosen speed.
      if(run&&session&&session.status==='ready'&&session.frame.seq<session.metadata.total_sample_count-1) {
        await runtime.runtimeAction('start',speed==='fast'?null:Number(speed))
        await runtime.refreshRuntimeFrames()
      }
    }catch(e){setError(e instanceof Error?e.message:String(e))}
    finally {setCreating(false)}
  }
  async function action(kind:'start'|'pause'|'resume'|'cancel'|'step') {
    setError(null)
    try {await runtime.runtimeAction(kind,speed==='fast'?null:Number(speed));await runtime.refreshRuntimeFrames()}
    catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  async function download(kind:'result'|'checkpoint'|'executed-input') {
    try {
      const id=state.session?.run_id
      // Server bytes verbatim: a parsed-then-stringified round-trip rewrites
      // float formatting (100.0 → 100) and breaks the checkpoint's canonical
      // config hash, so the downloaded file would fail replay verification.
      const text=await runtime.fetchRuntimeArtifactText(kind)
      const url=URL.createObjectURL(new Blob([text],{type:'application/json'}))
      const a=document.createElement('a');a.href=url;a.download=`liquid-${id}-${kind}.${kind==='executed-input'?'liquidrun':'json'}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
    }catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  // Continue a DAE run from a checkpoint file (a new run replaying its
  // accepted inputs). A checkpoint of another project revision still resumes,
  // but its session is marked stale like any run of an edited project.
  async function resumeFromCheckpoint(input:HTMLInputElement) {
    const file=input.files?.[0]
    input.value=''
    if(!file)return
    setError(null)
    try {
      const text=await file.text()
      const summary=runtime.readCheckpointSummary(text)
      const current=projectSha256(project)
      await runtime.resumeRuntimeSession(text,summary.projectSha256!==null&&summary.projectSha256===current?revision:null)
      setNotice({key:'lce.resumed_checkpoint',vars:{seq:summary.seq,total:summary.sampleCount-1}})
    }catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  function downloadManifest() {
    if(!project)return
    try {
      const {liquidrun}=compilePersistedScenario()
      const url=URL.createObjectURL(new Blob([JSON.stringify(liquidrun,null,2)],{type:'application/json'}))
      const a=document.createElement('a');a.href=url;a.download=`${liquidrun.projectName||'project'}.liquidrun`;a.click()
      setTimeout(()=>URL.revokeObjectURL(url),1000);setError(null)
    }catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  const number=(label:string,value:number,onChange:(value:number)=>void,step=1)=><label className="liquid-experiment-field"><span>{label}</span><input type="number" aria-label={label} value={Number.isNaN(value)?'':value} step={step} onChange={e=>onChange(e.target.valueAsNumber)}/></label>
  // Schedules are stored in Kelvin (the .liquidrun unit); typed and shown in °C.
  const celsiusField=(label:string,kelvin:number,onChange:(kelvin:number)=>void)=>number(label,celsius(kelvin),v=>{if(Number.isFinite(v))onChange(v+KELVIN)},.5)
  const readyErrors=readiness?readiness.items.filter(i=>i.status==='error').length:0
  return <div className="content liquid-experiment" id="liquid-experiment-panel">
    <HybridWorkflowPanel project={project} onNavigate={onNavigate} compact collapsible id="hybrid-workflow-liquid"/>

    {/* The run action pinned at the top of the panel: the full run section
        sits below the readiness, conditions and settings, a long scroll from
        where a condition is changed. Same handlers, same guards. */}
    <div className="lq-quick-run" id="lq-quick-run" role="region" aria-label={t('lq_ux.quick_run')}>
      <span className="lq-quick-run-state" data-state={readyErrors?'error':'ok'}>
        {readyErrors?t('lq_ux.quick_not_ready',{n:readyErrors}):t('lq_ux.quick_ready')}
      </span>
      {steady
        ?<button className="btn primary" id="lce-solve-quick" aria-busy={creating||undefined} onClick={()=>void create(false)} disabled={!project||state.busy||!!active||blocked}>{creating?t('lq_ux.solving_steady'):t('lq_ux.solve_steady')}</button>
        :<button className="btn primary" id="lce-init-run-quick" aria-busy={creating||undefined} onClick={()=>void create(true)} disabled={!project||state.busy||!!active||blocked}>{creating?t('lce.preparing_experiment'):t('lq_ux.init_and_run')}</button>}
      {active&&<button className="btn" id="lce-cancel-quick" disabled={state.busy} onClick={()=>void action('cancel')}>{t('lce.cancel')}</button>}
      {state.session&&<progress className="lq-quick-run-progress" value={progress} max={1}/>}
      <button type="button" className="lq-quick-run-more" onClick={()=>document.getElementById('lq-section-run')?.scrollIntoView({behavior:'smooth',block:'start'})}>{t('lq_ux.quick_details')}</button>
    </div>

    <details className="lq-section" id="lq-section-ready" open data-state={readyErrors?'error':'ok'}>
      <summary>{t('lq_ux.section_ready')}</summary>
      <LiquidReadinessCard report={readiness}/>
    </details>

    <details className="lq-section" id="lq-section-conditions" open>
      <summary>{t('lq_ux.section_conditions')} <Hint text={t('lq_ux.conditions_hint')}/></summary>
      <fieldset disabled={state.busy||!!active}>
        {newtonIntent&&<label className="liquid-experiment-field"><span>{t('lce.analysis_mode')}</span><span className="value" id="lce-analysis-mode" aria-label={t('lce.analysis_mode_aria')}>
          {analysisMode==='transient'?t('lce.mode_transient'):t('lce.mode_steady')}
          <span className="prop-hint" style={{marginLeft:6}}>{t(analysisMode==='transient'&&!timeline
            ?'transient.mode.liquid_own_clock':'transient.mode.liquid_hint')}</span>
        </span></label>}
        {dae&&<details><summary>{t('lce.primary_details',{count:dae.primary_circuits.length})}</summary>
          {dae.primary_circuits.map(circuit=><div key={circuit.edge_id} className="prop-group" data-primary-circuit={circuit.edge_id}>
            <strong>{circuit.edge_id} · {circuit.source}</strong>
            <p className="liquid-experiment-note">{t('lce.primary_flow_inlet',{flow:seriesText(circuit.flow_kg_s),temperature:seriesText(circuit.inlet_K,toCelsius)})}</p>
            <p className="liquid-experiment-note">{t('lce.primary_volumes',{primary:circuit.primary_volume_m3,secondary:circuit.secondary_volume_m3})}</p>
            <p className="liquid-experiment-note">{t('lce.fluid_source',{rho:circuit.rho_kg_m3,cp:circuit.cp_J_kgK,source:circuit.source_reference})}</p>
          </div>)}
        </details>}
        <legend>{t('lce.time_load')}
          {settings.loadStep&&<Hint text={t('lce.load_factor_hint')}/>}
          {timeline&&<Hint id="lce-keyframe-hint" text={t('transient.liquid.keyframe_hint')}/>}
          {dae&&showSupply&&settings.supplySchedule&&settings.supplySchedule.mode!=='fixed'&&!steady&&<Hint text={t('lce.supply_schedule_hint')}/>}
        </legend>
        {analysisMode!=='steady'&&(timeline
          ?<label className="liquid-experiment-field"><span>{t('lce.duration')}</span><span className="value" id="lce-duration-timeline">{t('transient.liquid.duration_hint',{d:timeline.durationS})}</span></label>
          :number(t('lce.duration'),duration,v=>{if(Number.isFinite(v))save({duration_s:v})}))}
        {number(t('lce.timestep'),dt,v=>{if(Number.isFinite(v))save({dt_s:v})},.1)}
        {showLoadStep&&<label className="liquid-experiment-check"><input type="checkbox" checked={settings.loadStep} disabled={steady} onChange={e=>change('loadStep',e.target.checked)}/>{t('lce.add_load_step')}</label>}
        {settings.loadStep&&<>{number(t('lce.step_time'),settings.stepTime,v=>change('stepTime',v))}{number(t('lce.load_factor'),settings.loadFactor,v=>change('loadFactor',v),.1)}</>}
        {/* The supply-temperature plan is part of the DAE (v3 .liquidrun)
            contract: the v2 compile path cannot express an inlet_K series and
            would silently drop it. Render the editor only when a DAE config
            exists, so the expansion hint never overclaims. */}
        {dae&&showSupply&&<><label className="liquid-experiment-field"><span>{t('lce.supply_mode')}</span><select aria-label={t('lce.supply_mode')} value={settings.supplySchedule?.mode??'fixed'} disabled={steady}
          onChange={e=>{
            const mode=e.target.value as 'fixed'|'step'|'ramp'|'sinusoid'
            const gridTime=Math.max(dt,Math.round(duration/2/dt)*dt)
            change('supplySchedule',
              mode==='fixed'?{mode:'fixed'}
              :mode==='step'?{mode:'step',from:290,to:295,stepTime:gridTime}
              :mode==='ramp'?{mode:'ramp',from:290,to:295,stepTime:gridTime,rampTime:Math.max(dt,Math.round(duration/4/dt)*dt)}
              :{mode:'sinusoid',mean:290,amplitude:2,period:Math.max(2*dt,Math.round(duration/2/dt)*dt)})
          }}>
          <option value="fixed">{t('lce.supply_mode_fixed')}</option><option value="step">{t('lce.supply_mode_step')}</option>
          <option value="ramp">{t('lce.supply_mode_ramp')}</option><option value="sinusoid">{t('lce.supply_mode_sinusoid')}</option>
        </select></label>
        {(()=>{
          const sched=settings.supplySchedule
          if(!sched||sched.mode==='fixed'||steady)return null
          const patch=(p:Record<string,number>)=>change('supplySchedule',{...sched,...p} as SupplySchedule)
          return <>
            {(sched.mode==='step'||sched.mode==='ramp')&&<>
              {celsiusField(t('lq_ux.supply_from_c'),sched.from,v=>patch({from:v}))}
              {celsiusField(t('lq_ux.supply_to_c'),sched.to,v=>patch({to:v}))}
              {number(t('lce.supply_step_time'),sched.stepTime,v=>patch({stepTime:v}),.5)}
            </>}
            {sched.mode==='ramp'&&number(t('lce.supply_ramp_time'),sched.rampTime,v=>patch({rampTime:v}),.5)}
            {sched.mode==='sinusoid'&&<>
              {celsiusField(t('lq_ux.supply_mean_c'),sched.mean,v=>patch({mean:v}))}
              {number(t('lq_ux.supply_amplitude_k'),sched.amplitude,v=>patch({amplitude:v}),.5)}
              {number(t('lce.supply_period'),sched.period,v=>patch({period:v}),.5)}
            </>}
          </>
        })()}</>}
      </fieldset>
    </details>

    <details className="lq-section" id="lq-section-settings" open>
      <summary>{t('lq_ux.section_settings')}</summary>
      <fieldset disabled={state.busy||!!active}>
        <legend>{t('lce.dae_title')}
          {dae&&<Hint text={t('lce.saved_execution',{initialization:initialization==='steady'?t('lce.init_steady'):t('lce.init_consistent'),device:execution.device==='cpu'?'CPU':execution.device,dtype:execution.dtype,threads:execution.max_threads})}/>}
        </legend>
        {experiment.engine==='newton-dae'&&!dae&&<p className="lce-badge-warn" id="lce-default-newton-assumptions" title={t('lce.default_newton_assumptions')}><Icon name="alert" size={12}/>{t('lce.synthetic_badge')}</p>}
        {dae&&experiment.parameterPack?.source==='mock'&&<p className="lce-badge-warn" id="lce-saved-mock-assumptions" title={t('lce.saved_mock_assumptions')}><Icon name="alert" size={12}/>{t('lce.synthetic_badge')}</p>}
        {!dae&&<button className="btn" type="button" disabled={!project} onClick={adoptDAE} title={t('lce.mock_description')}>{t('lce.adopt_mock')}</button>}
        {newtonIntent&&<div className="prop-group liquid-execution-settings">
          <div className="prop-title">{t('lce.execution_title')}<Hint text={`${t('lce.execution_hint')}\n${t('lce.cpu_threads_hint')}`}/></div>
          <label className="liquid-experiment-field" htmlFor="lce-dae-device"><span>{t('lce.execution_device')}</span><select id="lce-dae-device" value={execution.device} onChange={e=>changeDAEExecution({device:e.target.value as DAEExecution['device']})}>
            <option value="cpu">CPU</option><option value="cuda">{t('lce.device_gpu')}</option>
            {execution.device.startsWith('cuda:')&&<option value={execution.device}>{execution.device}</option>}
          </select></label>
          {execution.device.startsWith('cuda')&&<label className="liquid-experiment-field" htmlFor="lce-dae-cuda-index"><span>{t('lce.execution_gpu_index')}</span><NumberField id="lce-dae-cuda-index" value={execution.device==='cuda'?0:Number(execution.device.slice(5))} step={1} min={0} onCommit={index=>{
            if(!Number.isSafeInteger(index)||index<0)return false
            return changeDAEExecution({device:`cuda:${index}`})
          }}/></label>}
          <label className="liquid-experiment-field" htmlFor="lce-dae-dtype"><span>{t('lce.execution_dtype')}</span><select id="lce-dae-dtype" value={execution.dtype} onChange={e=>changeDAEExecution({dtype:e.target.value as DAEExecution['dtype']})}>
            <option value="float64">{t('lce.dtype_double')}</option><option value="float32">{t('lce.dtype_single')}</option>
          </select></label>
          <label className="liquid-experiment-field" htmlFor="lce-dae-threads"><span>{t('lce.cpu_threads')}</span><select id="lce-dae-threads" aria-label={t('lce.cpu_threads_aria')} value={execution.max_threads} onChange={e=>changeDAEExecution({max_threads:Number(e.target.value)})}>
            {threadOptions.map(count=><option key={count} value={count}>{count}</option>)}
          </select></label>
          {dae&&<details className="lc-details"><summary>{t('lce.newton_settings')} <Hint text={t('lce.integrator_hint')}/></summary><div className="lc-details-body">
            {([['max_iterations','lce.newton_iterations',1,1],['atol','lce.newton_atol',1e-8,1e-16],['rtol','lce.newton_rtol',1e-8,0],['max_backtracks','lce.newton_backtracks',1,1]] as const).map(([key,label,step,min])=><label className="liquid-experiment-field" key={key} htmlFor={`lce-newton-${key}`}><span>{t(label)}</span><NumberField id={`lce-newton-${key}`} value={dae.newton[key]} step={step} min={min} onCommit={value=>changeNewton(key,value)}/></label>)}
          </div></details>}
        </div>}
        <LiquidFluidEditor project={project}/>
        <legend>{t('lce.pump_control')}
          {newtonIntent&&<Hint id="lce-pi-dae" text={t('lq_ux.pi_dae')}/>}
          {settings.mode!=='manual'&&<Hint text={t('lce.pi_hint')}/>}
        </legend>
        {/* The DAE solves the loop as a continuous PI law inside each step;
            legacy v2 inputs keep their sampled PID. */}
        {<>
          <label className="liquid-experiment-field" htmlFor="lce-control-mode"><span>{t('lce.control_mode')}</span><select id="lce-control-mode" aria-label={t('lce.control_mode')} value={settings.mode} onChange={e=>{
            const mode=e.target.value as ExperimentSettings['mode'];save({settings:{...settings,mode,setpoint:mode==='pressure'?30:45,kp:mode==='pressure'?.01:.03,ki:mode==='pressure'?.001:.002}})
          }}><option value="manual">{t('lce.manual')}</option><option value="temperature">{t('lce.temperature_pi')}</option><option value="pressure">{t('lce.pressure_pi')}</option></select></label>
          {settings.mode!=='manual'&&<>
            <button className="btn" type="button" onClick={readCatalog} title={catalog.pumps.length?undefined:t('lce.saved_targets_hint')}>{t('lce.read_devices')}</button>
            <label className="liquid-experiment-field"><span>{t('lce.actuator_pump')}</span><select value={settings.pumpId} onChange={e=>change('pumpId',e.target.value)}><option value="">{t('lce.select_pump')}</option>{catalog.pumps.map(id=><option key={id}>{id}</option>)}</select></label>
            {settings.mode==='temperature'&&<label className="liquid-experiment-field"><span>{t('lce.measurement_plate')}</span><select value={settings.measurementId} onChange={e=>change('measurementId',e.target.value)}><option value="">{t('lce.select_plate')}</option>{catalog.plates.map(id=><option key={id}>{id}</option>)}</select></label>}
            {number(settings.mode==='temperature'?t('lce.temperature_setpoint'):t('lce.pressure_setpoint'),settings.setpoint,v=>change('setpoint',v),.1)}
            {number(settings.mode==='temperature'?'Kp / K⁻¹':'Kp / kPa⁻¹',settings.kp,v=>change('kp',v),.001)}
            {number(settings.mode==='temperature'?'Ki / (K·s)⁻¹':'Ki / (kPa·s)⁻¹',settings.ki,v=>change('ki',v),.001)}
            {number(t('lce.output_bias'),settings.bias,v=>change('bias',v),.05)}
          </>}
        </>}
        <p className="lce-meta">{t('lce.pack_hint',{pack:experiment.parameterPack ? `${experiment.parameterPack.id} · ${experiment.parameterPack.revision}` : t('lce.default_pack')})}</p>
      </fieldset>
      <details className="liquid-experiment-legacy" id="lce-legacy-solver"><summary>{t('lq_ux.legacy_solver')} <Hint text={t('lq_ux.legacy_solver_hint')}/></summary>
        {!newtonIntent?<LiquidSolverPanel project={project} showFluid={false}/>:<p className="liquid-experiment-note">{t('lce.legacy_preserved_hint')}</p>}
      </details>
    </details>

    <details className="lq-section" id="lq-section-run" open>
      <summary>{t('lq_ux.section_run')}</summary>
      {!steady&&<><label className="liquid-experiment-field" htmlFor="lce-playback-speed"><span>{t('lce.playback_speed')} <Hint text={t('lq_ux.speed_hint')}/></span><select id="lce-playback-speed" aria-label={t('lce.playback_speed')} value={speed} disabled={status==='running'} onChange={e=>setSpeed(e.target.value)}><option value="fast">{t('lce.fastest')}</option><option value="1">1×</option><option value="10">10×</option></select></label>
</>}
      {blocked&&<p className="liquid-experiment-warning" id="lce-solve-blocked">{t('lq_ux.solve_blocked')}</p>}
      <div className="liquid-experiment-actions">
        {steady
          ?<button className="btn primary" id="lce-solve" aria-busy={creating||undefined} aria-live="polite" onClick={()=>void create(false)} disabled={!project||state.busy||!!active||blocked}>{creating?t('lq_ux.solving_steady'):t('lq_ux.solve_steady')}</button>
          :<>
            <button className="btn primary" id="lce-init-run" aria-busy={creating||undefined} aria-live="polite" onClick={()=>void create(true)} disabled={!project||state.busy||!!active||blocked}>{creating?t('lce.preparing_experiment'):t('lq_ux.init_and_run')}</button>
            <button className="btn" id="lce-init-only" title={t('lq_ux.init_only_hint')} onClick={()=>void create(false)} disabled={!project||state.busy||!!active||blocked}>{t('lq_ux.init_only')}</button>
          </>}
        {status==='ready'&&hasNextStep&&<button className="btn success" disabled={state.busy} onClick={()=>void action('start')}>{t('lce.start')}</button>}
        {status==='running'&&<button className="btn" disabled={state.busy} onClick={()=>void action('pause')}>{t('lce.pause')}</button>}
        {status==='paused'&&<button className="btn success" disabled={state.busy} onClick={()=>void action('resume')}>{t('lce.resume')}</button>}
        {(status==='paused'||status==='ready')&&hasNextStep&&<button className="btn" disabled={state.busy} onClick={()=>void action('step')}>{t('lce.step')}</button>}
        {active&&<button className="btn" disabled={state.busy} onClick={()=>void action('cancel')}>{t('lce.cancel')}</button>}
      </div>
      {state.session&&<div aria-live="polite" className="liquid-experiment-status">
        <strong>{daeRun&&!hasNextStep&&state.session.metadata.total_sample_count===1?t('lce.steady_ready'):t(statusLabels[state.session.status])} · {frame?.time_s.toFixed(1)} s</strong>
        <progress value={progress} max={1}/>
        <small>{t('lce.run_identity',{id:state.session.run_id.slice(0,12),hash:state.session.config_hash.slice(0,10)})}</small>
        {readOnly&&<p className="liquid-experiment-note" id="lce-read-only">{t('lq_ux.read_only_run')}</p>}
        {stale&&<p className="liquid-experiment-warning">{t('lce.stale_project')}</p>}
      </div>}
      {/* Live inputs act on the next accepted step: a steady run has none. */}
      {daeRun&&state.session&&!readOnly&&state.session.metadata.total_sample_count>1&&<fieldset disabled={state.busy}>
        <legend>{t('lce.live_load')} <Hint text={t('lce.heat_scale_hint')}/></legend>
        <div className="liquid-experiment-actions liquid-heat-presets">
          {([[.8,t('lce.load_80')],[1,t('lce.load_100')],[1.2,t('lce.load_120')]] as const).map(([value,label])=><button className="btn" key={value}
            aria-label={label} title={label} disabled={!hasNextStep||!active} onClick={()=>void heat(value)}>{Math.round(value*100)}%</button>)}
        </div>
        <p className="liquid-experiment-note" id="lce-accepted-heat">{t('lce.accepted_heat',{percent:(acceptedHeat*100).toFixed(0)})}</p>
        {needsAcceptance(pendingHeat,acceptedHeat)&&<p className="liquid-experiment-note" id="lce-pending-heat">{t('lce.pending_heat',{percent:(pendingHeat*100).toFixed(0)})}</p>}
        {pumpIds.length>0&&<div className="liquid-experiment-actions">
          <label className="liquid-experiment-field"><span>{t('lce.pump_edge')}</span><select aria-label={t('lce.pump_edge')} value={pumpId} onChange={e=>setSelectedPump(e.target.value)}>{pumpIds.map(id=><option key={id} value={id}>{id}</option>)}</select></label>
          <label className="liquid-experiment-field"><span>{t('lce.pump_ratio')}</span><input aria-label={t('lce.pump_ratio')} type="number" min="0" step="0.05" value={Number.isNaN(pumpRatio)?'':pumpRatio} onChange={e=>setPumpRatio(e.target.valueAsNumber)}/></label>
          <button className="btn" disabled={!hasNextStep||!active} onClick={()=>void queuePump()}>{t('lce.queue_pump')}</button>
        </div>}
        {pumpId&&needsAcceptance(queuedPump,frame?.pump_rpm_ratios?.[pumpId])&&<p className="liquid-experiment-note">{t('lce.pending_control',{id:pumpId,value:queuedPump})}</p>}
        {pumpId&&frame?.pump_rpm_ratios?.[pumpId]!==undefined&&<p className="liquid-experiment-note">{t('lce.accepted_control',{id:pumpId,value:frame.pump_rpm_ratios[pumpId]})}</p>}
        {valveIds.length>0&&<div className="liquid-experiment-actions">
          <label className="liquid-experiment-field"><span>{t('lce.valve_edge')}</span><select aria-label={t('lce.valve_edge')} value={valveId} onChange={e=>setSelectedValve(e.target.value)}>{valveIds.map(id=><option key={id} value={id}>{id}</option>)}</select></label>
          <label className="liquid-experiment-field"><span>{t('lce.valve_opening')}</span><input aria-label={t('lce.valve_opening')} type="number" min="0.01" max="1" step="0.05" value={Number.isNaN(valveOpening)?'':valveOpening} onChange={e=>setValveOpening(e.target.valueAsNumber)}/></label>
          <button className="btn" disabled={!hasNextStep||!active} onClick={()=>void queueValve()}>{t('lce.queue_valve')}</button>
        </div>}
        {valveId&&needsAcceptance(queuedValve,frame?.valve_openings?.[valveId])&&<p className="liquid-experiment-note">{t('lce.pending_control',{id:valveId,value:queuedValve})}</p>}
        {valveId&&frame?.valve_openings?.[valveId]!==undefined&&<p className="liquid-experiment-note">{t('lce.accepted_control',{id:valveId,value:frame.valve_openings[valveId]})}</p>}
        {hxIds.length>0&&<div className="liquid-experiment-actions">
          <label className="liquid-experiment-field"><span>{t('lce.hx_edge')}</span><select aria-label={t('lce.hx_edge')} value={hxId} onChange={e=>setSelectedHx(e.target.value)}>{hxIds.map(id=><option key={id} value={id}>{id}</option>)}</select></label>
          <label className="liquid-experiment-field"><span>{t('lq_ux.supply_temp_c')}</span><input aria-label={t('lq_ux.supply_temp_c')} type="number" min="-50" max="200" step="0.5" value={Number.isNaN(inletC)?'':inletC} onChange={e=>setInletC(e.target.valueAsNumber)}/></label>
          <button className="btn" disabled={!hasNextStep||!active} onClick={()=>void queueSupply()}>{t('lce.queue_supply')}</button>
        </div>}
        {hxId&&needsAcceptance(queuedInlet,frame?.primary_inlet_K?.[hxId])&&<p className="liquid-experiment-note">{t('lq_ux.pending_supply_c',{id:hxId,value:celsius(queuedInlet)})}</p>}
        {hxId&&frame?.primary_inlet_K?.[hxId]!==undefined&&<p className="liquid-experiment-note">{t('lq_ux.accepted_supply_c',{id:hxId,value:celsius(frame.primary_inlet_K[hxId])})}</p>}
        <button className="btn" disabled={status==='failed'||status==='cancelled'} title={t('lce.equilibrium_hint')} onClick={()=>void queryEquilibrium()}>{t('lce.query_equilibrium')}</button>
      </fieldset>}
      {equilibrium&&state.session?.run_id===equilibrium.run_id&&<div id="liquid-dae-equilibrium" className="prop-group">
        <strong>{t('lce.equilibrium_result',{percent:(equilibrium.heat_multiplier??1)*100})}</strong>
        <p className="liquid-experiment-note">{t('lce.equilibrium_time',{time:equilibrium.evaluated_at_s.toFixed(1)})}</p>
        {(state.session.metadata.hx_edge_ids??[]).map((id,index)=><p key={id} className="liquid-experiment-note">
          {t('lce.equilibrium_temperatures',{id,primary:temperatureText(equilibrium.primary_return_K?.[index]),secondary:temperatureText(equilibrium.secondary_supply_K?.[index])})}
        </p>)}
      </div>}
      <SavedRunHistory project={project} revision={revision} busy={state.busy||!!active} currentRun={state.session?.run_id??null} onNotice={setNotice} onError={setError}/>
    </details>

    <details className="lq-section" id="lq-section-export" open>
      <summary>{t('lq_ux.section_export')}</summary>
      <div className="liquid-experiment-actions">
        <button className="btn" onClick={downloadManifest} disabled={!project||state.busy}>{t('lce.export_manifest')}</button>
        {state.session&&<><button className="btn" onClick={()=>void download('result')}>{t('lce.export_result')}</button><button className="btn" onClick={()=>void download('checkpoint')}>{t('lce.save_checkpoint')}</button></>}
        <label className="btn" htmlFor="lce-resume-file" id="lce-resume" title={t('lce.resume_hint')} aria-disabled={state.busy||!!active||undefined}>{t('lce.resume_checkpoint')}</label>
        <input id="lce-resume-file" type="file" accept=".json,application/json" hidden disabled={state.busy||!!active} onChange={e=>void resumeFromCheckpoint(e.currentTarget)}/>
      </div>
      {daeRun&&state.session&&<><button className="btn" disabled={state.busy} title={t('lce.executed_hint')} onClick={()=>void download('executed-input')}>{t('lce.export_executed')}</button>
        <button className="btn" disabled={state.busy||!project?.uid} onClick={()=>void queryHeatAudit()}>{t('lce.heat_audit_button')}</button></>}
      {heatAudit&&state.session?.run_id===heatAudit.liquid_run_id&&project?.uid===heatAudit.project_uid&&<div id="liquid-heat-audit" className="prop-group">
        <strong>{t('lce.heat_audit_summary',{count:heatAudit.audited_server_count,samples:heatAudit.checked_server_samples,
          residual:heatAudit.max_abs_closure_residual_W===null?'—':heatAudit.max_abs_closure_residual_W.toExponential(2)})} <Hint text={t('lce.heat_audit_limit')}/></strong>
        <p className="liquid-experiment-note">{heatAudit.identity_verified?t('lce.heat_audit_identity_verified'):t('lce.heat_audit_identity_unverified')}</p>
      </div>}
      {daeRun&&state.session&&<p id="lce-actual-execution" className="liquid-experiment-note">{actualExecution?t('lce.actual_execution',{device:actualExecution.device,dtype:actualExecution.dtype,threads:actualExecution.threads}):t('lce.actual_execution_unknown')}</p>}
    </details>
    {notice&&<p role="status" className="liquid-experiment-note">{t(notice.key,notice.vars)}</p>}
    {(error||state.error||runtimeError)&&<p role="alert" className="error">{error||state.error||runtimeError}</p>}
  </div>
}

/**
 * The case's saved liquid runs; a finished DAE run can be taken over as the
 * session (read-only), which survives reloads and server restarts.
 */
function SavedRunHistory({project,revision,busy,currentRun,onNotice,onError}:{project:CfdProject|null;revision:number;busy:boolean;currentRun:string|null;onNotice:(notice:{key:TranslationKey;vars?:Record<string,string|number>})=>void;onError:(text:string|null)=>void}) {
  const {t}=useTranslation()
  const data=useSyncExternalStore(subscribeHybridData,hybridData)
  const [open,setOpen]=useState(false)
  useEffect(()=>{if(open)ensureHybridData()},[open])
  const sha=projectSha256(project)
  const runs=(data.runs??[]).filter((run:SavedLiquidRun)=>run.kind==='dae'&&run.run_id)
  async function restore(run:SavedLiquidRun) {
    onError(null)
    try {
      await runtime.restoreRuntimeSession(run.run_id!,run.source_project?.sha256===sha?revision:null)
      onNotice({key:'lq_ux.history_restored',vars:{id:run.run_id!.slice(0,8)}})
    }catch(e){onError(e instanceof Error?e.message:String(e))}
  }
  return <details className="lq-run-history-group" id="lce-saved-runs" onToggle={e=>setOpen((e.target as HTMLDetailsElement).open)}>
    <summary>{t('lq_ux.history_title')}</summary>
    <button className="btn" type="button" disabled={data.loading} onClick={()=>void refreshHybridData()}>{t('lq_ux.history_refresh')}</button>
    {data.error&&<p className="liquid-experiment-warning">{t('lq_ux.history_load_error',{error:data.error})}</p>}
    {data.runs&&!runs.length&&<p className="liquid-experiment-note">{t('lq_ux.history_empty')}</p>}
    {runs.length>0&&<table className="lq-run-history"><tbody>{runs.slice(0,30).map(run=>{
      const current=run.run_id===currentRun
      return <tr key={run.name} data-run={run.run_id!} data-current={current}>
        <td>{t('lq_ux.history_row',{id:run.run_id!.slice(0,8),status:run.status&&run.status in statusLabels?t(statusLabels[run.status as runtime.RuntimeStatus]):run.status??'—',samples:run.samples.accepted??0,
          time:run.accepted_time_s??'—',revision:run.source_project?.sha256===sha?t('lq_ux.history_this_revision'):t('lq_ux.history_other_revision')})}
          {!run.restorable&&run.restore_blocker&&<span className="prop-hint" style={{display:'block'}}>{t('lq_ux.history_not_restorable',{reason:run.restore_blocker})}</span>}</td>
        <td>{run.restorable&&!current&&<button className="btn" type="button" disabled={busy} onClick={()=>void restore(run)}>{t('lq_ux.history_restore')}</button>}</td>
      </tr>
    })}</tbody></table>}
  </details>
}

function seriesText(value:number|number[],convert:(value:number)=>number=(value)=>value):string {
  if(Array.isArray(value))return t('lce.series_summary',{first:convert(value[0]??NaN).toFixed(3),last:convert(value.at(-1)??NaN).toFixed(3),count:value.length})
  return convert(value).toFixed(3)
}
function temperatureText(value:number|undefined):string {
  return value!==undefined&&Number.isFinite(value)?`${toCelsius(value).toFixed(2)} °C`:'—'
}
