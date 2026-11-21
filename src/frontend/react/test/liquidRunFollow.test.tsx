// A live experiment that finishes becomes the shown liquid result (E7 T5 browser
// gate, 2026-09-30): the results panel had loaded an older run, the experiment
// ran to completion, and 后处理 kept the old run until 🔄 was pressed.
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LiquidResultsPanel } from '../src/components/LiquidResultsPanel'
import { liquidRunState, resetLiquidRun } from '../src/liquid/liquidSession'
import { setSelectedLiquidRun } from '../src/liquid/liquidPostprocess'
import { createRuntimeSession, refreshRuntimeFrames, resetRuntimeClient } from '../src/liquid/liquidRuntimeClient'
import { startLiquidRunFollow } from '../src/liquid/liquidRunFollow'
import { selectedResultSample, setResultSample } from '../src/liquid/liquidTempColor'
import { startResultPlayback } from '../src/transient/resultPlayback'

let host:HTMLDivElement,root:Root,stops:(()=>void)[]
const response=(value:unknown)=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}})
/** A result document; the server keeps a runtime run's under `run-<run_id>`. */
function result(runId:string,samples:number,duration:number) {
  const time_s=Array.from({length:samples},(_,i)=>i*duration/(samples-1)),s=(v:number)=>time_s.map(()=>v)
  return {run_id:runId,converged:true,time_s,
    rack_summaries:[{rack_id:'rack',total_flow_kg_s:s(1),supply_temp_K:s(300),return_temp_K:s(310),
      servers:[{server_id:'server',flow_kg_s:s(1),max_chip_temp_K:s(320),heat_removed_W:s(100),cp_edge_ids:[]}]}],cp_details:[]}
}
function frame(id:string,seq=0) {return {kind:'frame',run_id:id,seq,time_s:seq,node_pressure_Pa:[150000],node_temperature_K:[300],edge_flow_kg_s:[.1],chip_temp_K:[310],edge_outlet_temp_K:[305],controls:{},diagnostics:{}}}
function session(id:string,status='ready') {return {run_id:id,status,config_hash:`hash-${id}`,metadata:{node_ids:['n'],edge_ids:['e'],cp_edge_ids:['e'],total_sample_count:4,dt_s:1,object_bindings:{}},frame:frame(id)}}

/** The server, routed by URL: what `/api/liquid/results[?run=]` returns, and the live run. */
function server(results:Record<string,unknown>,run:{id:string,created?:string}) {
  return vi.spyOn(globalThis,'fetch').mockImplementation(async input=>{
    const url=String(input)
    if(url==='/api/liquid/runs')return response(session(run.id,run.created))
    if(url.startsWith(`/api/liquid/runs/${run.id}/frames`))return response({status:'completed',frames:[frame(run.id,1),frame(run.id,2),frame(run.id,3)]})
    if(url.startsWith('/api/liquid/results'))return url in results?response(results[url]):new Response('{"error":"No liquid results found"}',{status:404})
    throw new Error(`unexpected request ${url}`)
  })
}
const resultReads=(request:ReturnType<typeof server>)=>request.mock.calls.map(([url])=>String(url)).filter(url=>url.startsWith('/api/liquid/results'))
const samplesText=()=>host.querySelector('#lr-time-samples')?.textContent
/** The follow-up read is fired from the runtime listener, not awaited by the caller. */
const settle=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,0))})
async function finishExperiment() {
  await act(async()=>{await createRuntimeSession({},[],1)})
  await act(async()=>{await refreshRuntimeFrames()})
  await settle()
}

beforeEach(()=>{
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true
  resetRuntimeClient();resetLiquidRun();setSelectedLiquidRun('');setResultSample(-1)
  stops=[startLiquidRunFollow()]
  host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(()=>{
  act(()=>root.unmount());host.remove()
  stops.forEach(stop=>stop())
  resetRuntimeClient();resetLiquidRun();setSelectedLiquidRun('');setResultSample(-1);vi.restoreAllMocks()
})

it('shows the run a live experiment just finished, without pressing refresh',async()=>{
  const request=server({'/api/liquid/results':result('old-run',7,120),'/api/liquid/results?run=run-new-run':result('new-run',16,150)},{id:'new-run'})
  await act(async()=>root.render(<LiquidResultsPanel/>))
  expect(samplesText()).toBe('7 (0 – 120.0 s)')

  await finishExperiment()
  expect(resultReads(request)).toEqual(['/api/liquid/results','/api/liquid/results?run=run-new-run'])
  expect((liquidRunState().results as {run_id:string}).run_id).toBe('new-run')
  expect(samplesText()).toBe('16 (0 – 150.0 s)')

  // Later notifications about the same finished run do not read it again.
  await act(async()=>{await refreshRuntimeFrames()})
  await settle()
  expect(resultReads(request)).toHaveLength(2)
})

it('shows a run that is complete as soon as it is accepted (a steady DAE)',async()=>{
  const request=server({'/api/liquid/results':result('old-steady',7,120),'/api/liquid/results?run=run-steady-run':result('steady-run',1,0)},{id:'steady-run',created:'completed'})
  await act(async()=>root.render(<LiquidResultsPanel/>))
  await act(async()=>{await createRuntimeSession({},[],1)})
  await settle()
  expect(resultReads(request).at(-1)).toBe('/api/liquid/results?run=run-steady-run')
  expect((liquidRunState().results as {run_id:string}).run_id).toBe('steady-run')
})

it('keeps an explicitly selected historical run when an experiment finishes',async()=>{
  const request=server({'/api/liquid/results?run=run-historical':result('historical',7,600),'/api/liquid/results?run=run-other-run':result('other-run',16,150)},{id:'other-run'})
  setSelectedLiquidRun('run-historical')
  await act(async()=>root.render(<LiquidResultsPanel/>))
  await finishExperiment()
  expect(resultReads(request)).toEqual(['/api/liquid/results?run=run-historical'])
  expect((liquidRunState().results as {run_id:string}).run_id).toBe('historical')
  expect(samplesText()).toBe('7 (0 – 600.0 s)')
})

it('re-reads an explicitly selected run when that very run finishes',async()=>{
  const partial=result('watched-run',3,20),complete=result('watched-run',16,150)
  let finished=false
  const request=server({},{id:'watched-run'})
  const serve=request.getMockImplementation()!
  request.mockImplementation(async(input,init)=>String(input)==='/api/liquid/results?run=run-watched-run'
    ?response(finished?complete:partial):serve(input,init))
  setSelectedLiquidRun('run-watched-run')
  await act(async()=>root.render(<LiquidResultsPanel/>))
  expect(samplesText()).toBe('3 (0 – 20.0 s)')
  finished=true
  await finishExperiment()
  expect(resultReads(request)).toEqual(['/api/liquid/results?run=run-watched-run','/api/liquid/results?run=run-watched-run'])
  expect(samplesText()).toBe('16 (0 – 150.0 s)')
})

it('opens the finished run at its latest sample (the playback reset on a new run_id)',async()=>{
  stops.push(startResultPlayback())
  server({'/api/liquid/results':result('picked-run',7,120),'/api/liquid/results?run=run-fresh-run':result('fresh-run',16,150)},{id:'fresh-run'})
  await act(async()=>root.render(<LiquidResultsPanel/>))
  act(()=>setResultSample(2))
  expect(selectedResultSample()).toBe(2)
  await finishExperiment()
  expect((liquidRunState().results as {run_id:string}).run_id).toBe('fresh-run')
  expect(selectedResultSample()).toBe(-1)
})
