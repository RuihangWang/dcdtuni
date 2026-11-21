import { useState, useSyncExternalStore } from 'react'
import * as runtime from '../liquid/liquidRuntimeClient'
import { alignAnalyticAxis, analyticCelsiusPoints, solidTraceMatches, type AnalyticCurveKey } from '../liquid/mmsOverlay'
import type { TranslationKey } from '../i18n'
import { TimeSeriesChart } from './TimeSeriesChart'
import './LiquidRuntimeMonitor.css'
import { useTranslation } from '../useTranslation'
export function LiquidRuntimeMonitor() {
  const {t}=useTranslation()
  const state=useSyncExternalStore(runtime.subscribeRuntime,runtime.runtimeState)
  const [selected,setSelected]=useState('')
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
  const bindingLabel=(id:string)=>Object.values(metadata?.object_bindings??{}).find(binding=>binding.edge_ids.includes(id))?.label??id
  const statusKey:TranslationKey=session?('lce.status_'+session.status) as TranslationKey:'lrm.not_prepared'
  return <div className="content liquid-monitor" id="liquid-runtime-monitor">
    <header className="liquid-monitor-header">
      <div><h3>{t('lrm.title')}</h3><p>{t('lrm.subtitle')}</p></div>
      <span className="liquid-monitor-status" data-runtime-status={session?.status??'empty'} role="status" aria-live="polite">{t(statusKey)}</span>
    </header>
    {state.error&&<p className="liquid-monitor-error" role="alert">{state.error}</p>}
    {!session?<p className="liquid-monitor-empty" data-runtime-empty>{t('lrm.empty')}</p>:<>
      <section className="liquid-monitor-card" aria-label={t('lrm.run_overview')}>
        <div className="liquid-monitor-section-heading">
          <h4>{t('lrm.run_overview')}</h4>
          <span className="liquid-monitor-connection" data-runtime-connection={state.connection}>{t(('lrm.connection_'+state.connection) as TranslationKey)}</span>
        </div>
        <dl className="liquid-monitor-readings">
          <div className="liquid-monitor-reading"><dt>{t('lrm.simulation_time')}</dt><dd data-runtime-value="time" data-time-s={frame?.time_s}>{timeText(frame?.time_s)}</dd></div>
          <div className="liquid-monitor-reading"><dt>{t('lrm.current_frame')}</dt><dd data-runtime-value="frame" data-frame-seq={frame?.seq} title={t('lrm.frame_ordinal_hint')}>{frame?(frame.seq+1)+' / '+metadata?.total_sample_count:'—'}</dd></div>
        </dl>
        <p className="liquid-monitor-note" data-runtime-samples>{t('lrm.accepted_samples',{count:state.frames.length})}</p>
      </section>
      <section className="liquid-monitor-card" aria-labelledby="lrm-live-heading">
        <h4 id="lrm-live-heading">{t('lrm.live_values')}</h4>
        <label className="liquid-monitor-field"><span>{t('lrm.cold_plate')}</span><select aria-label={t('lrm.cold_plate')} value={cpId??''} onChange={e=>setSelected(e.target.value)}>{metadata?.cp_edge_ids.map(id=><option key={id} value={id} title={id}>{bindingLabel(id)===id?id:bindingLabel(id)+' · '+id}</option>)}</select></label>
        {cpId&&<p className="liquid-monitor-edge-id" title={cpId}>{cpId}</p>}
        <dl className="liquid-monitor-readings">
          <Reading id="solid" label={t('lrm.solid_temperature')} value={temperatureText(frame?.chip_temp_K[cpIndex])}/>
          <Reading id="outlet" label={t('lrm.outlet_temperature')} value={temperatureText(frame?.edge_outlet_temp_K[edgeIndex])}/>
          <Reading id="flow" label={t('lrm.mass_flow')} value={numberText(frame?.edge_flow_kg_s[edgeIndex],4,'kg/s')}/>
        </dl>
      </section>
      <div className="liquid-monitor-section-heading liquid-monitor-history-heading"><h4>{t('lrm.run_history')}</h4><span>{t('lrm.time_axis')}</span></div>
      {dae&&<section className="liquid-monitor-card liquid-monitor-reference" data-runtime-reference data-comparable={analyticComparable}>
        <label className="liquid-monitor-check"><input type="checkbox" aria-label={t('lrm.analytic_toggle')} checked={analytic} onChange={e=>setAnalytic(e.target.checked)}/><span>{t('lrm.analytic_toggle')}</span></label>
        {analytic&&<>
          <label className="liquid-monitor-field"><span>{t('lrm.reference_scenario')}</span><select aria-label={t('lrm.analytic_toggle')} value={analyticScenario} onChange={e=>setAnalyticScenario(e.target.value as 'load'|'supply')}>
            <option value="load">{t('lrm.analytic_scenario_load')}</option><option value="supply">{t('lrm.analytic_scenario_supply')}</option>
          </select></label>
          <p className="liquid-monitor-note">{analyticComparable?t('lrm.analytic_note'):t('lrm.analytic_incomparable')}</p>
        </>}
      </section>}
      <section className="liquid-monitor-card">
        <h4>{t('lrm.temperature_title')}</h4>
        <TimeSeriesChart time={time} series={[{label,color:'#e7a967',points:temperature},...analyticSeries('analytic_solid_K','lrm.analytic_solid')]} xLabel={t('lrm.time_axis')} yLabel="°C" height={175}/>
      </section>
      <section className="liquid-monitor-card">
        <h4>{t('lrm.flow_title')}</h4>
        <TimeSeriesChart time={time} series={[{label,color:'#65b7df',points:flow}]} xLabel={t('lrm.time_axis')} yLabel="kg/s" height={150}/>
      </section>
      {dae&&(metadata?.hx_edge_ids??[]).map((id,index)=><section className="liquid-monitor-card" key={id} data-runtime-hx={id}>
        <h4>{t('lrm.hx_title',{label:hxLabel(id)})}</h4>
        <dl className="liquid-monitor-readings">
          <Reading id={'primary-'+id} label={t('lrm.primary_return')} value={temperatureText(frame?.primary_return_K?.[index])}/>
          <Reading id={'secondary-'+id} label={t('lrm.secondary_supply')} value={temperatureText(frame?.secondary_supply_K?.[index])}/>
        </dl>
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
        <p className="liquid-monitor-note">{t('lrm.temperature_scales')}</p>
      </section>)}
      {dae&&<section className="liquid-monitor-card">
        <h4>{t('lrm.heat_title')}</h4>
        <dl className="liquid-monitor-readings">
          <Reading id="heat-input" label={t('lrm.heat_input')} value={numberText(value(frame,'heat_input_W')/1000,2,'kW')}/>
          <Reading id="heat-rejection" label={t('lrm.heat_rejection')} value={numberText(value(frame,'primary_rejection_W')/1000,2,'kW')}/>
          <Reading id="heat-storage" label={t('lrm.storage_rate')} value={numberText(value(frame,'storage_rate_W')/1000,2,'kW')}/>
        </dl>
        <TimeSeriesChart time={time} series={[
          {label:t('lrm.heat_input'),color:'#e7a967',points:state.frames.map(sample=>value(sample,'heat_input_W')/1000)},
          {label:t('lrm.heat_rejection'),color:'#65b7df',points:state.frames.map(sample=>value(sample,'primary_rejection_W')/1000)},
          {label:t('lrm.storage_rate'),color:'#8fc8a5',points:state.frames.map(sample=>value(sample,'storage_rate_W')/1000)},
        ]} xLabel={t('lrm.time_axis')} yLabel="kW" height={175}/>
      </section>}
      {controller&&<section className="liquid-monitor-card">
        <h4>{t('lrm.pump_output')}</h4>
        <TimeSeriesChart time={time} series={[{label:t('lrm.pump_speed'),color:'#8fc8a5',points:state.frames.map(f=>pid(f)?.output??NaN)}]} xLabel={t('lrm.time_axis')} yLabel="ratio" height={150}/>
        <p className="liquid-monitor-note">{pid(frame)?.saturated?t('lrm.saturated'):t('lrm.within_limits')}{pid(frame)?.rate_limited?t('lrm.rate_limited'):''}</p>
      </section>}
      <footer className="liquid-monitor-footer">
        {balance!==undefined&&Number.isFinite(balance)&&<p>{t('lrm.energy_balance',{value:balance.toExponential(2)})}</p>}
        <p>{t('lrm.physical_hint')}</p>
      </footer>
    </>}
  </div>
}

function Reading({id,label,value}:{id:string;label:string;value:string}) {
  return <div className="liquid-monitor-reading"><dt>{label}</dt>{' '}<dd data-runtime-value={id}>{value}</dd></div>
}
function numberText(value:number|undefined,digits:number,unit:string):string {
  return value!==undefined&&Number.isFinite(value)?value.toFixed(digits)+' '+unit:'—'
}
function temperatureText(value:number|undefined):string {
  return value!==undefined&&Number.isFinite(value)?numberText(value-273.15,2,'°C'):'—'
}
function timeText(value:number|undefined):string {
  return value!==undefined&&Number.isFinite(value)?Number(value.toPrecision(8)).toString()+' s':'—'
}
