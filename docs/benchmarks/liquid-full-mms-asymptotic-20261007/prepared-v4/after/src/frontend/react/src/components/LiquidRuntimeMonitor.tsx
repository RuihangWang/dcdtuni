import { useState, useSyncExternalStore } from 'react'
import * as runtime from '../liquid/liquidRuntimeClient'
import { alignAnalyticAxis, analyticCelsiusPoints, solidTraceMatches, type AnalyticCurveKey } from '../liquid/mmsOverlay'
import type { TranslationKey } from '../i18n'
import { TimeSeriesChart } from './TimeSeriesChart'
import { LiquidMonitorPanel } from './LiquidMonitorPanel'
import { useTranslation } from '../useTranslation'
export function LiquidRuntimeMonitor() {
  const {t}=useTranslation()
  const state=useSyncExternalStore(runtime.subscribeRuntime,runtime.runtimeState)
  const [selected,setSelected]=useState('')
  const [tab,setTab]=useState<'runtime'|'legacy'>('runtime')
  const [analytic,setAnalytic]=useState(false)
  const [analyticScenario,setAnalyticScenario]=useState<'load'|'supply'>('load')
  const session=state.session
  const frame=session?.frame
  const metadata=session?.metadata
  const cpIndex=Math.max(0,metadata?.cp_edge_ids.indexOf(selected)??0)
  const cpId=metadata?.cp_edge_ids[cpIndex]
  const edgeIndex=metadata?.edge_ids.indexOf(cpId??'')??-1
  const time=state.frames.map(f=>f.time_s)
  const label=cpId??t('lrm.cold_plate')
  const temperature=state.frames.map(f=>(f.chip_temp_K[cpIndex]??NaN)-273.15)
  const flow=state.frames.map(f=>f.edge_flow_kg_s[edgeIndex]??NaN)
  const dae=metadata?.solver==='differentiable_newton_dae'
  const controller=!dae&&frame?Object.keys(frame.controls)[0]:undefined
  const pid=(sample:runtime.RuntimeFrame|undefined)=>controller&&sample?
    (sample.controls as Record<string,runtime.RuntimePIDControl>)[controller]:undefined
  // Analytic reference overlay: closed-form curves of the MMS product fixture
  // (generated module, sha-bound to the acceptance receipt). Honesty gate, all
  // three terms required — otherwise the note explains why nothing is drawn:
  //   1. `dae`: the toggle lives inside the DAE block but the charts do not,
  //      and `analytic` survives a session swap;
  //   2. axis: frame times match the fixture axis sample-for-sample
  //      (tail-aligned; alignment/padding semantics in mmsOverlay.ts);
  //   3. identity: the run's own solid-temperature trace follows the receipt's
  //      DAE trace within 0.02 K — the axis alone does not identify the
  //      fixture (any 60 s / 1 s run would axis-match), and a wrong case,
  //      load factor or scenario moves the trace far more than the tolerance.
  const analyticAlignment=analytic&&dae?alignAnalyticAxis(time,analyticScenario):null
  const analyticComparable=analyticAlignment!==null&&
    solidTraceMatches(state.frames.map(f=>f.chip_temp_K[cpIndex]),analyticAlignment)
  const analyticSeries=(key:AnalyticCurveKey,label:TranslationKey)=>
    analyticAlignment&&analyticComparable?[{label:t(label),color:'#9a9a9a',dash:true,
      points:analyticCelsiusPoints(analyticAlignment,key)}]:[]
  const thermal=frame?.diagnostics.thermal as {energy_balance_W?:number}|undefined
  const value=(frame:runtime.RuntimeFrame|undefined,key:string)=>typeof frame?.diagnostics[key]==='number'?frame.diagnostics[key] as number:NaN
  const balance=dae?value(frame,'energy_balance_W'):thermal?.energy_balance_W
  const hxLabel=(id:string)=>Object.values(metadata?.object_bindings??{}).find(binding=>binding.edge_ids.includes(id))?.label??id
  return <div className="content liquid-experiment" id="liquid-runtime-monitor">
    <div className="subtab-bar"><button className={`subtab${tab==='runtime'?' active':''}`} onClick={()=>setTab('runtime')}>{t('lrm.runtime_tab')}</button><button className={`subtab${tab==='legacy'?' active':''}`} onClick={()=>setTab('legacy')}>{t('lrm.legacy_tab')}</button></div>
    {tab==='legacy'?<LiquidMonitorPanel/>:!session?<p>{t('lrm.empty')}</p>:<>
      <h3>{t('lrm.title')}</h3>
      <p aria-live="polite">{t('lrm.simulation_time')} <strong>{frame?.time_s.toFixed(1)} s</strong>{t('lrm.frame_count',{count:state.frames.length})}{state.connection==='reconnecting'?t('lrm.reconnecting'):''}</p>
      <label className="liquid-experiment-field"><span>{t('lrm.cold_plate')}</span><select value={cpId??''} onChange={e=>setSelected(e.target.value)}>{metadata?.cp_edge_ids.map(id=><option key={id}>{id}</option>)}</select></label>
      <h4>{t('lrm.temperature_title')}</h4><TimeSeriesChart time={time} series={[{label,color:'#e7a967',points:temperature},...analyticSeries('analytic_solid_K','lrm.analytic_solid')]} xLabel={t('lrm.time_axis')} yLabel="°C" height={175}/>
      <h4>{t('lrm.flow_title')}</h4><TimeSeriesChart time={time} series={[{label,color:'#65b7df',points:flow}]} xLabel={t('lrm.time_axis')} yLabel="kg/s" height={150}/>
      {dae&&(metadata?.hx_edge_ids??[]).map((id,index)=><div key={id} data-runtime-hx={id}>
        <h4>{t('lrm.hx_title',{label:hxLabel(id)})}</h4>
        <p className="liquid-experiment-note">{t('lrm.hx_values',{primary:temperatureText(frame?.primary_return_K?.[index]),secondary:temperatureText(frame?.secondary_supply_K?.[index])})}</p>
        <div className="liquid-hx-charts">
          <TimeSeriesChart time={time} series={[
            {label:t('lrm.primary_return'),color:'#e7a967',points:state.frames.map(sample=>(sample.primary_return_K?.[index]??NaN)-273.15)},
            ...(index===0&&(metadata?.hx_edge_ids?.length??0)===1?analyticSeries('analytic_primary_K','lrm.analytic_primary'):[]),
          ]} xLabel={t('lrm.time_axis')} yLabel="°C" height={150}/>
          <TimeSeriesChart time={time} series={[
            {label:t('lrm.secondary_supply'),color:'#65b7df',points:state.frames.map(sample=>(sample.secondary_supply_K?.[index]??NaN)-273.15)},
            ...(index===0&&(metadata?.hx_edge_ids?.length??0)===1?analyticSeries('analytic_secondary_K','lrm.analytic_secondary'):[]),
          ]} xLabel={t('lrm.time_axis')} yLabel="°C" height={150}/>
        </div>
        <p className="liquid-experiment-note">{t('lrm.temperature_scales')}</p>
      </div>)}
      {dae&&<>
        <label className="liquid-experiment-check"><input type="checkbox" aria-label={t('lrm.analytic_toggle')} checked={analytic} onChange={e=>setAnalytic(e.target.checked)}/>{t('lrm.analytic_toggle')}</label>
        {analytic&&<>
          <label className="liquid-experiment-field"><span>{t('lrm.analytic_toggle')}</span><select aria-label={t('lrm.analytic_toggle')} value={analyticScenario} onChange={e=>setAnalyticScenario(e.target.value as 'load'|'supply')}>
            <option value="load">{t('lrm.analytic_scenario_load')}</option><option value="supply">{t('lrm.analytic_scenario_supply')}</option>
          </select></label>
          <p className="liquid-experiment-note">{analyticComparable?t('lrm.analytic_note'):t('lrm.analytic_incomparable')}</p>
        </>}
        <h4>{t('lrm.heat_title')}</h4>
        <TimeSeriesChart time={time} series={[
          {label:t('lrm.heat_input'),color:'#e7a967',points:state.frames.map(sample=>value(sample,'heat_input_W')/1000)},
          {label:t('lrm.heat_rejection'),color:'#65b7df',points:state.frames.map(sample=>value(sample,'primary_rejection_W')/1000)},
          {label:t('lrm.storage_rate'),color:'#8fc8a5',points:state.frames.map(sample=>value(sample,'storage_rate_W')/1000)},
        ]} xLabel={t('lrm.time_axis')} yLabel="kW" height={175}/>
        <p className="liquid-experiment-note">{t('lrm.heat_values',{input:(value(frame,'heat_input_W')/1000).toFixed(2),rejection:(value(frame,'primary_rejection_W')/1000).toFixed(2),storage:(value(frame,'storage_rate_W')/1000).toFixed(2)})}</p>
      </>}
      {controller&&<><h4>{t('lrm.pump_output')}</h4><TimeSeriesChart time={time} series={[{label:t('lrm.pump_speed'),color:'#8fc8a5',points:state.frames.map(f=>pid(f)?.output??NaN)}]} xLabel={t('lrm.time_axis')} yLabel="ratio" height={150}/><p>{pid(frame)?.saturated?t('lrm.saturated'):t('lrm.within_limits')}{pid(frame)?.rate_limited?t('lrm.rate_limited'):''}</p></>}
      {balance!==undefined&&Number.isFinite(balance)&&<p className="liquid-experiment-note">{t('lrm.energy_balance',{value:balance.toExponential(2)})}</p>}
      <p className="liquid-experiment-note">{t('lrm.physical_hint')}</p>
    </>}
  </div>
}

function temperatureText(value:number|undefined):string {
  return value!==undefined&&Number.isFinite(value)?`${(value-273.15).toFixed(2)} °C`:'—'
}
