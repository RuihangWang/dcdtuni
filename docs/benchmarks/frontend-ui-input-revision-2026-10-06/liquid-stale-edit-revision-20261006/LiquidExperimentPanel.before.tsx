import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'
import { compileDynamicLiquidGraph } from '../liquid/dynamicLiquidGraph'
import { adoptMockDAEExperiment, compileSavedExperiment, readExperiment, resolveSavedExperiment, type ExperimentSettings, type SavedExperiment, type SupplySchedule } from '../liquid/liquidExperiment'
import { schema } from '../legacy/cfdprojModules'
import { toCelsius } from '../liquid/liquidResults'
import * as runtime from '../liquid/liquidRuntimeClient'
import { LiquidSolverPanel } from './LiquidSolverPanel'
import { useTranslation } from '../useTranslation'
import { t, type TranslationKey } from '../i18n'
import { domainModesOf, transientOf } from '../transient/model'

// Frame updates can arrive before the next metadata snapshot. A queued value
// that the latest accepted frame already carries is no longer pending in the UI.
function needsAcceptance(queued:number|undefined,accepted:number|undefined):queued is number {
  return queued!==undefined&&queued!==accepted
}

const statusLabels:Record<runtime.RuntimeStatus,TranslationKey>={ready:'lce.status_ready',running:'lce.status_running',paused:'lce.status_paused',completed:'lce.status_completed',cancelled:'lce.status_cancelled',failed:'lce.status_failed'}
export function LiquidExperimentPanel({project}:{project:CfdProject|null}) {
  const {t}=useTranslation()
  const state=useSyncExternalStore(runtime.subscribeRuntime,runtime.runtimeState)
  const revision=useSyncExternalStore(store.subscribe,store.revision)
  const experiment=readExperiment(project)
  const {duration_s:duration,dt_s:dt,settings}=experiment
  // In a transient project the timeline owns the duration and load / supply
  // changes are keyframes (plan T2c.5); a legacy one still switched on stays
  // shown rather than acting unseen. A steady project keeps them all.
  const timeline=domainModesOf(project).liquid==='transient'?transientOf(project):null
  const showLoadStep=!timeline||settings.loadStep
  const showSupply=!timeline||(settings.supplySchedule?.mode??'fixed')!=='fixed'
  const [speed,setSpeed]=useState('10')
  const [creating,setCreating]=useState(false)
  const [error,setError]=useState<string|null>(null)
  const [notice,setNotice]=useState<TranslationKey|null>(null)
  const [equilibrium,setEquilibrium]=useState<runtime.RuntimeEquilibrium|null>(null)
  const [heatAudit,setHeatAudit]=useState<runtime.RuntimeHeatAudit|null>(null)
  const [catalog,setCatalog]=useState<{pumps:string[];plates:string[]}>({pumps:[],plates:[]})
  const [selectedPump,setSelectedPump]=useState('')
  const [selectedValve,setSelectedValve]=useState('')
  const [pumpRatio,setPumpRatio]=useState(1)
  const [valveOpening,setValveOpening]=useState(1)
  const [selectedHx,setSelectedHx]=useState('')
  const [inletTemp,setInletTemp]=useState(295)
  const save=(patch:Partial<SavedExperiment>)=>store.setRunConfig('liquid','experiment',{...readExperiment(store.getProject()),...patch})
  const change=<K extends keyof ExperimentSettings>(key:K,value:ExperimentSettings[K])=>save({settings:{...settings,[key]:value}})
  const status=state.session?.status
  const frame=state.session?.frame
  const hasNextStep=!!state.session&&(frame?.seq??0)<state.session.metadata.total_sample_count-1
  const active=status==='running'||status==='paused'||(status==='ready'&&hasNextStep)
  const stale=state.session&&state.projectRevision!==revision
  const dae=experiment.dae
  // Mode display follows the same pure choice as compilation. Reading a
  // stale marked input must not show an independent clock or edit the project.
  const analysisMode=experiment.engine==='newton-dae'?domainModesOf(project).liquid:dae?.analysis.mode
  const initialization=experiment.engine==='newton-dae'&&analysisMode==='steady'?'steady'
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
  const execution=dae?.execution??{backend:'pytorch',device:'cpu',dtype:'float64',max_threads:1}
  const threadOptions=[...new Set([1,2,4,execution.max_threads])].sort((a,b)=>a-b)
  const progress=useMemo(()=>state.session?(state.session.metadata.total_sample_count===1?1:(frame?.seq??0)/(state.session.metadata.total_sample_count-1)):0,[state.session,frame])
  useEffect(()=>setEquilibrium(null),[state.session?.run_id])
  useEffect(()=>setHeatAudit(null),[state.session?.run_id,project?.uid])
  function adoptDAE() {
    if(!project)return
    setError(null)
    try {
      const adopted=adoptMockDAEExperiment(project,{analysis:{mode:domainModesOf(project).liquid,initialization:'steady'}})
      adopted.dae!.execution={backend:'pytorch',device:'cpu',dtype:'float64',max_threads:2}
      store.setRunConfig('liquid','experiment',adopted)
      setNotice('lce.notice_adopted')
    }catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  function changeDAEThreads(max_threads:number) {
    if(!dae)return
    const next:SavedExperiment={...experiment,dae:{...dae,execution:{...execution,max_threads}}}
    const errors=schema().validateLiquidExperiment(next)
    if(errors.length){setError(errors.map(e=>`${e.path}: ${e.msg}`).join('; '));return}
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
    try {await runtime.runtimeControlInputs({primary_inlet_K:{[hxId]:inletTemp}})}
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
      const graph=compileDynamicLiquidGraph(project,{duration_s:duration,dt_s:dt,parameterPack:experiment.parameterPack})
      const pumps=graph.edges.filter(e=>e.type==='pump').map(e=>e.id)
      const plates=graph.edges.filter(e=>e.type==='cold_plate').map(e=>e.id)
      setCatalog({pumps,plates})
      save({settings:{...settings,pumpId:pumps.includes(settings.pumpId)?settings.pumpId:pumps.length===1?pumps[0]!: '',measurementId:plates.includes(settings.measurementId)?settings.measurementId:plates.length===1?plates[0]!: ''}})
    }catch(e){setError(e instanceof Error?e.message:String(e))}
  }
  function compilePersistedScenario() {
    const current=store.getProject()
    if(!current)throw new Error('No current project')
    let selected=resolveSavedExperiment(current)
    if(!selected.parameterPack) {
      const graph=compileDynamicLiquidGraph(current,{
        duration_s:selected.duration_s,dt_s:selected.dt_s,
      })
      selected={...selected,parameterPack:graph.parameter_pack}
    }
    // Bind the hash to the adopted, saved inputs, never to the pre-edit object.
    if(JSON.stringify(selected)!==JSON.stringify(readExperiment(current))) {
      store.setRunConfig('liquid','experiment',selected)
      if(JSON.stringify(readExperiment(store.getProject()))!==JSON.stringify(selected))throw new Error('Experiment inputs could not be saved')
    }
    return compileSavedExperiment(store.getProject()!)
  }
  async function create() {
    if(!project)return
    setError(null)
    setCreating(true)
    try {
      const scenario=compilePersistedScenario()
      await runtime.createRuntimeSession(scenario.liquidrun,scenario.controllers,store.revision())
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
  return <div className="content liquid-experiment" id="liquid-experiment-panel">
    <h3>{t('lce.title')}</h3>
    <p className="liquid-experiment-note">{t('lce.introduction')}</p>
    <fieldset disabled={state.busy||!!active}>
      <legend>{t('lce.dae_title')}</legend>
      {experiment.engine==='newton-dae'&&!dae&&<p className="liquid-experiment-note" id="lce-default-newton-assumptions">{t('lce.default_newton_assumptions')}</p>}
      {dae&&experiment.parameterPack?.source==='mock'&&<p className="liquid-experiment-note" id="lce-saved-mock-assumptions">{t('lce.saved_mock_assumptions')}</p>}
      {!dae?<>
        <button className="btn" type="button" disabled={!project} onClick={adoptDAE}>{t('lce.adopt_mock')}</button>
        <p className="liquid-experiment-note">{t('lce.mock_description')}</p>
      </>:<>
        {/* Read-only (E8b): the top-bar switch is the one writer of the liquid mode. */}
        <label className="liquid-experiment-field"><span>{t('lce.analysis_mode')}</span><span className="value" id="lce-analysis-mode" aria-label={t('lce.analysis_mode_aria')}>
          {analysisMode==='transient'?t('lce.mode_transient'):t('lce.mode_steady')}
          <span className="prop-hint" style={{marginLeft:6}}>{t(analysisMode==='transient'&&!timeline
            ?'transient.mode.liquid_own_clock':'transient.mode.liquid_hint')}</span>
        </span></label>
        <p className="liquid-experiment-note">{t('lce.saved_execution',{initialization:initialization==='steady'?t('lce.init_steady'):t('lce.init_consistent'),device:execution.device==='cpu'?'CPU':execution.device,dtype:execution.dtype,threads:execution.max_threads})}</p>
        {execution.device==='cpu'&&<><label className="liquid-experiment-field"><span>{t('lce.cpu_threads')}</span><select aria-label={t('lce.cpu_threads_aria')} value={execution.max_threads}
          onChange={e=>changeDAEThreads(Number(e.target.value))}>
          {threadOptions.map(count=><option key={count} value={count}>{count}</option>)}
        </select></label><p className="liquid-experiment-note">{t('lce.cpu_threads_hint')}</p></>}
        <details><summary>{t('lce.primary_details',{count:dae.primary_circuits.length})}</summary>
          {dae.primary_circuits.map(circuit=><div key={circuit.edge_id} className="prop-group" data-primary-circuit={circuit.edge_id}>
            <strong>{circuit.edge_id} · {circuit.source}</strong>
            <p className="liquid-experiment-note">{t('lce.primary_flow_inlet',{flow:seriesText(circuit.flow_kg_s),temperature:seriesText(circuit.inlet_K,toCelsius)})}</p>
            <p className="liquid-experiment-note">{t('lce.primary_volumes',{primary:circuit.primary_volume_m3,secondary:circuit.secondary_volume_m3})}</p>
            <p className="liquid-experiment-note">{t('lce.fluid_source',{rho:circuit.rho_kg_m3,cp:circuit.cp_J_kgK,source:circuit.source_reference})}</p>
          </div>)}
        </details>
      </>}
      <legend>{t('lce.time_load')}</legend>
      {analysisMode!=='steady'&&(timeline
        ?<label className="liquid-experiment-field"><span>{t('lce.duration')}</span><span className="value" id="lce-duration-timeline">{t('transient.liquid.duration_hint',{d:timeline.durationS})}</span></label>
        :number(t('lce.duration'),duration,v=>{if(Number.isFinite(v))save({duration_s:v})}))}
      {number(t('lce.timestep'),dt,v=>{if(Number.isFinite(v))save({dt_s:v})},.1)}
      {showLoadStep&&<label className="liquid-experiment-check"><input type="checkbox" checked={settings.loadStep} disabled={analysisMode==='steady'} onChange={e=>change('loadStep',e.target.checked)}/>{t('lce.add_load_step')}</label>}
      {settings.loadStep&&<>{number(t('lce.step_time'),settings.stepTime,v=>change('stepTime',v))}{number(t('lce.load_factor'),settings.loadFactor,v=>change('loadFactor',v),.1)}<p className="liquid-experiment-note">{t('lce.load_factor_hint')}</p></>}
      {/* The supply-temperature plan is part of the DAE (v3 .liquidrun)
          contract: the v2 compile path cannot express an inlet_K series and
          would silently drop it. Render the editor only when a DAE config
          exists, so the expansion hint never overclaims. */}
      {timeline&&<p className="liquid-experiment-note" id="lce-keyframe-hint">{t('transient.liquid.keyframe_hint')}</p>}
      {dae&&showSupply&&<><label className="liquid-experiment-field"><span>{t('lce.supply_mode')}</span><select aria-label={t('lce.supply_mode')} value={settings.supplySchedule?.mode??'fixed'} disabled={analysisMode==='steady'}
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
        if(!sched||sched.mode==='fixed'||analysisMode==='steady')return null
        const patch=(p:Record<string,number>)=>change('supplySchedule',{...sched,...p} as SupplySchedule)
        return <>
          {(sched.mode==='step'||sched.mode==='ramp')&&<>
            {number(t('lce.supply_from'),sched.from,v=>patch({from:v}),.5)}
            {number(t('lce.supply_to'),sched.to,v=>patch({to:v}),.5)}
            {number(t('lce.supply_step_time'),sched.stepTime,v=>patch({stepTime:v}),.5)}
          </>}
          {sched.mode==='ramp'&&number(t('lce.supply_ramp_time'),sched.rampTime,v=>patch({rampTime:v}),.5)}
          {sched.mode==='sinusoid'&&<>
            {number(t('lce.supply_mean'),sched.mean,v=>patch({mean:v}),.5)}
            {number(t('lce.supply_amplitude'),sched.amplitude,v=>patch({amplitude:v}),.5)}
            {number(t('lce.supply_period'),sched.period,v=>patch({period:v}),.5)}
          </>}
          <p className="liquid-experiment-note">{t('lce.supply_schedule_hint')}</p>
        </>
      })()}</>}
      <legend>{t('lce.pump_control')}</legend>
      <label className="liquid-experiment-field" htmlFor="lce-control-mode"><span>{t('lce.control_mode')}</span><select id="lce-control-mode" aria-label={t('lce.control_mode')} value={settings.mode} disabled={newtonIntent} onChange={e=>{
        const mode=e.target.value as ExperimentSettings['mode'];save({settings:{...settings,mode,setpoint:mode==='pressure'?30:45,kp:mode==='pressure'?.01:.03,ki:mode==='pressure'?.001:.002}})
      }}><option value="manual">{t('lce.manual')}</option><option value="temperature">{t('lce.temperature_pi')}</option><option value="pressure">{t('lce.pressure_pi')}</option></select></label>
      {newtonIntent&&<p className="liquid-experiment-note">{t('lce.dae_control_hint')}</p>}
      {settings.mode!=='manual'&&<>
        <button className="btn" type="button" onClick={readCatalog}>{t('lce.read_devices')}</button>
        {!catalog.pumps.length&&<p className="liquid-experiment-note">{t('lce.saved_targets_hint')}</p>}
        <label className="liquid-experiment-field"><span>{t('lce.actuator_pump')}</span><select value={settings.pumpId} onChange={e=>change('pumpId',e.target.value)}><option value="">{t('lce.select_pump')}</option>{catalog.pumps.map(id=><option key={id}>{id}</option>)}</select></label>
        {settings.mode==='temperature'&&<label className="liquid-experiment-field"><span>{t('lce.measurement_plate')}</span><select value={settings.measurementId} onChange={e=>change('measurementId',e.target.value)}><option value="">{t('lce.select_plate')}</option>{catalog.plates.map(id=><option key={id}>{id}</option>)}</select></label>}
        {number(settings.mode==='temperature'?t('lce.temperature_setpoint'):t('lce.pressure_setpoint'),settings.setpoint,v=>change('setpoint',v),.1)}
        {number(settings.mode==='temperature'?'Kp / K⁻¹':'Kp / kPa⁻¹',settings.kp,v=>change('kp',v),.001)}
        {number(settings.mode==='temperature'?'Ki / (K·s)⁻¹':'Ki / (kPa·s)⁻¹',settings.ki,v=>change('ki',v),.001)}
        {number(t('lce.output_bias'),settings.bias,v=>change('bias',v),.05)}
        <p className="liquid-experiment-note">{t('lce.pi_hint')}</p>
      </>}
      <p className="liquid-experiment-note">{t('lce.pack_hint',{pack:experiment.parameterPack ? `${experiment.parameterPack.id} · ${experiment.parameterPack.revision}` : t('lce.default_pack')})}</p>
    </fieldset>
    {notice&&<p role="status" className="liquid-experiment-note">{t(notice)}</p>}
    <label className="liquid-experiment-field" htmlFor="lce-playback-speed"><span>{t('lce.playback_speed')}</span><select id="lce-playback-speed" aria-label={t('lce.playback_speed')} value={speed} disabled={status==='running'} onChange={e=>setSpeed(e.target.value)}><option value="1">1×</option><option value="10">10×</option><option value="fast">{t('lce.fastest')}</option></select></label>
    <div className="liquid-experiment-actions">
      <button className="btn" onClick={downloadManifest} disabled={!project||state.busy}>{t('lce.export_manifest')}</button>
      <button className="btn" aria-busy={creating||undefined} aria-live="polite" onClick={()=>void create()} disabled={!project||state.busy||!!active}>{creating?t('lce.preparing_experiment'):state.session?t('lce.new_experiment'):t('lce.prepare_experiment')}</button>
      {status==='ready'&&hasNextStep&&<button className="btn success" disabled={state.busy} onClick={()=>void action('start')}>{t('lce.start')}</button>}
      {status==='running'&&<button className="btn" disabled={state.busy} onClick={()=>void action('pause')}>{t('lce.pause')}</button>}
      {status==='paused'&&<button className="btn success" disabled={state.busy} onClick={()=>void action('resume')}>{t('lce.resume')}</button>}
      {(status==='paused'||status==='ready')&&hasNextStep&&<button className="btn" disabled={state.busy} onClick={()=>void action('step')}>{t('lce.step')}</button>}
      {active&&<button className="btn" disabled={state.busy} onClick={()=>void action('cancel')}>{t('lce.cancel')}</button>}
    </div>
    {daeRun&&state.session&&<fieldset disabled={state.busy}>
      <legend>{t('lce.live_load')}</legend>
      <div className="liquid-experiment-actions liquid-heat-presets">
        {([[.8,t('lce.load_80')],[1,t('lce.load_100')],[1.2,t('lce.load_120')]] as const).map(([value,label])=><button className="btn" key={value}
          aria-label={label} title={label} disabled={!hasNextStep||!active} onClick={()=>void heat(value)}>{Math.round(value*100)}%</button>)}
      </div>
      <p className="liquid-experiment-note" id="lce-accepted-heat">{t('lce.accepted_heat',{percent:(acceptedHeat*100).toFixed(0)})}</p>
      {needsAcceptance(pendingHeat,acceptedHeat)&&<p className="liquid-experiment-note" id="lce-pending-heat">{t('lce.pending_heat',{percent:(pendingHeat*100).toFixed(0)})}</p>}
      <p className="liquid-experiment-note">{t('lce.heat_scale_hint')}</p>
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
        <label className="liquid-experiment-field"><span>{t('lce.supply_temp')}</span><input aria-label={t('lce.supply_temp')} type="number" min="0.01" max="999" step="0.5" value={Number.isNaN(inletTemp)?'':inletTemp} onChange={e=>setInletTemp(e.target.valueAsNumber)}/></label>
        <button className="btn" disabled={!hasNextStep||!active} onClick={()=>void queueSupply()}>{t('lce.queue_supply')}</button>
      </div>}
      {hxId&&needsAcceptance(queuedInlet,frame?.primary_inlet_K?.[hxId])&&<p className="liquid-experiment-note">{t('lce.pending_control',{id:hxId,value:queuedInlet})}</p>}
      {hxId&&frame?.primary_inlet_K?.[hxId]!==undefined&&<p className="liquid-experiment-note">{t('lce.accepted_control',{id:hxId,value:frame.primary_inlet_K[hxId]})}</p>}
      <button className="btn" disabled={status==='failed'||status==='cancelled'} onClick={()=>void queryEquilibrium()}>{t('lce.query_equilibrium')}</button>
      <p className="liquid-experiment-note">{t('lce.equilibrium_hint')}</p>
    </fieldset>}
    {equilibrium&&state.session?.run_id===equilibrium.run_id&&<div id="liquid-dae-equilibrium" className="prop-group">
      <strong>{t('lce.equilibrium_result',{percent:(equilibrium.heat_multiplier??1)*100})}</strong>
      <p className="liquid-experiment-note">{t('lce.equilibrium_time',{time:equilibrium.evaluated_at_s.toFixed(1)})}</p>
      {(state.session.metadata.hx_edge_ids??[]).map((id,index)=><p key={id} className="liquid-experiment-note">
        {t('lce.equilibrium_temperatures',{id,primary:temperatureText(equilibrium.primary_return_K?.[index]),secondary:temperatureText(equilibrium.secondary_supply_K?.[index])})}
      </p>)}
    </div>}
    {state.session&&<div aria-live="polite" className="liquid-experiment-status">
      <strong>{daeRun&&!hasNextStep&&state.session.metadata.total_sample_count===1?t('lce.steady_ready'):t(statusLabels[state.session.status])} · {frame?.time_s.toFixed(1)} s</strong>
      <progress value={progress} max={1}/>
      <small>{t('lce.run_identity',{id:state.session.run_id.slice(0,12),hash:state.session.config_hash.slice(0,10)})}</small>
      {stale&&<p className="liquid-experiment-warning">{t('lce.stale_project')}</p>}
      <div className="liquid-experiment-actions"><button className="btn" onClick={()=>void download('result')}>{t('lce.export_result')}</button><button className="btn" onClick={()=>void download('checkpoint')}>{t('lce.save_checkpoint')}</button></div>
      {daeRun&&<><button className="btn" disabled={state.busy} onClick={()=>void download('executed-input')}>{t('lce.export_executed')}</button>
        <p className="liquid-experiment-note">{t('lce.executed_hint')}</p></>}
      {daeRun&&<button className="btn" disabled={state.busy||!project?.uid} onClick={()=>void queryHeatAudit()}>{t('lce.heat_audit_button')}</button>}
    </div>}
    {heatAudit&&state.session?.run_id===heatAudit.liquid_run_id&&project?.uid===heatAudit.project_uid&&<div id="liquid-heat-audit" className="prop-group">
      <strong>{t('lce.heat_audit_summary',{count:heatAudit.audited_server_count,samples:heatAudit.checked_server_samples,
        residual:heatAudit.max_abs_closure_residual_W===null?'—':heatAudit.max_abs_closure_residual_W.toExponential(2)})}</strong>
      <p className="liquid-experiment-note">{heatAudit.identity_verified?t('lce.heat_audit_identity_verified'):t('lce.heat_audit_identity_unverified')}</p>
      <p className="liquid-experiment-note">{t('lce.heat_audit_limit')}</p>
    </div>}
    {(error||state.error||state.session?.error)&&<p role="alert" className="error">{error||state.error||state.session?.error}</p>}
    <details className="liquid-experiment-legacy"><summary>{t('lce.legacy_solver')}</summary><LiquidSolverPanel project={project}/></details>
  </div>
}

function seriesText(value:number|number[],convert:(value:number)=>number=(value)=>value):string {
  if(Array.isArray(value))return t('lce.series_summary',{first:convert(value[0]??NaN).toFixed(3),last:convert(value.at(-1)??NaN).toFixed(3),count:value.length})
  return convert(value).toFixed(3)
}
function temperatureText(value:number|undefined):string {
  return value!==undefined&&Number.isFinite(value)?`${toCelsius(value).toFixed(2)} °C`:'—'
}
