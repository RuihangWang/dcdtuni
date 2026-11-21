import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRuntimeSession, runtimeState, resetRuntimeClient, runtimeAction, refreshRuntimeFrames } from '../src/liquid/liquidRuntimeClient'

function frame(id:string,seq=0) {return {kind:'frame',run_id:id,seq,time_s:seq,node_pressure_Pa:[150000],node_temperature_K:[300],edge_flow_kg_s:[.1],chip_temp_K:[310],edge_outlet_temp_K:[305],controls:{},diagnostics:{}}}
function session(id:string) {return {run_id:id,status:'ready',config_hash:`hash-${id}`,metadata:{node_ids:['n'],edge_ids:['e'],cp_edge_ids:['e'],total_sample_count:4,dt_s:1,object_bindings:{}},frame:frame(id)}}
const response = (body:unknown) => new Response(JSON.stringify(body), {status:200, headers:{'Content-Type':'application/json'}})
afterEach(() => {resetRuntimeClient();vi.restoreAllMocks()})

describe('liquid runtime browser client', () => {
  it('submits the actual graph and project revision', async () => {
    const fetch = vi.spyOn(globalThis,'fetch').mockResolvedValue(response(session('a')))
    await createRuntimeSession({nodes:[{id:'n'}]},[],7)
    expect(JSON.parse(fetch.mock.calls[0]?.[1]?.body as string)).toEqual({liquidrun:{nodes:[{id:'n'}]},controllers:[],project_revision:7})
    expect(runtimeState().session?.run_id).toBe('a')
    expect(runtimeState().frames.map(f=>f.seq)).toEqual([0])
  })

  it('backfills frames exactly once and preserves physical time', async () => {
    vi.spyOn(globalThis,'fetch')
      .mockResolvedValueOnce(response(session('a')))
      .mockImplementation(async()=>response({status:'paused',frames:[frame('a',1),frame('a',2),frame('a',2)]}))
    await createRuntimeSession({},[],1)
    await refreshRuntimeFrames()
    await refreshRuntimeFrames()
    expect(runtimeState().frames.map(f=>f.time_s)).toEqual([0,1,2])
  })

  it('never attaches a late creation response to a newer project', async () => {
    let resolveOld!:(r:Response)=>void
    const old = new Promise<Response>(r=>{resolveOld=r})
    vi.spyOn(globalThis,'fetch').mockReturnValueOnce(old).mockResolvedValueOnce(response(session('new')))
    const first = createRuntimeSession({},[],1)
    await createRuntimeSession({},[],2)
    resolveOld(response(session('old')))
    await first
    expect(runtimeState().session?.run_id).toBe('new')
    expect(runtimeState().projectRevision).toBe(2)
  })

  it('uses server lifecycle acknowledgment and reports operation errors', async () => {
    vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(response(session('a')))
      .mockResolvedValueOnce(response({...session('a'),status:'paused'}))
      .mockResolvedValueOnce(new Response(JSON.stringify({error:'run is cancelled'}),{status:409}))
    await createRuntimeSession({},[],1)
    await runtimeAction('pause')
    expect(runtimeState().session?.status).toBe('paused')
    await expect(runtimeAction('step')).rejects.toThrow('run is cancelled')
    expect(runtimeState().error).toBe('run is cancelled')
  })

  it('rejects a frame belonging to another run', async () => {
    vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(response(session('a')))
      .mockResolvedValueOnce(response({status:'paused',frames:[frame('other',1)]}))
    await createRuntimeSession({},[],1)
    await expect(refreshRuntimeFrames()).rejects.toThrow(/run/)
    expect(runtimeState().frames).toHaveLength(1)
  })

  it('backfills intermediate frames when an action acknowledgment jumps ahead', async () => {
    const fetch=vi.spyOn(globalThis,'fetch')
      .mockResolvedValueOnce(response(session('a')))
      .mockResolvedValueOnce(response({...session('a'),status:'completed',frame:frame('a',3)}))
      .mockResolvedValueOnce(response({status:'completed',frames:[frame('a',1),frame('a',2),frame('a',3)]}))
    await createRuntimeSession({},[],1)
    await runtimeAction('start')
    await refreshRuntimeFrames()
    expect(fetch.mock.calls[2]?.[0]).toContain('after=0')
    expect(runtimeState().frames.map(f=>f.seq)).toEqual([0,1,2,3])
  })

  it('keeps old frames tied to their old revision while new creation is pending', async () => {
    let complete!:(r:Response)=>void
    vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(response(session('old')))
      .mockReturnValueOnce(new Promise<Response>(r=>{complete=r}))
    await createRuntimeSession({},[],1)
    const pending=createRuntimeSession({},[],2)
    expect(runtimeState().projectRevision).toBe(1)
    complete(response(session('new')))
    await pending
    expect(runtimeState().projectRevision).toBe(2)
  })
})

describe('runtime request ordering',()=>{
 it('does not let an older poll overwrite a newer lifecycle acknowledgment',async()=>{
  let completePoll!:(value:Response)=>void
  vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(response(session('a')))
   .mockReturnValueOnce(new Promise<Response>(r=>{completePoll=r}))
   .mockResolvedValueOnce(response({...session('a'),status:'paused',frame:frame('a',2)}))
  await createRuntimeSession({},[],1)
  const poll=refreshRuntimeFrames()
  await runtimeAction('pause')
  completePoll(response({status:'running',frames:[frame('a',1)]}))
  await poll
  expect(runtimeState().session?.status).toBe('paused')
  expect(runtimeState().session?.frame.seq).toBe(2)
 })
})

it('keeps newer completed status when an older concurrent refresh arrives last',async()=>{
 let older!:(r:Response)=>void
 vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(response(session('a')))
  .mockReturnValueOnce(new Promise<Response>(r=>{older=r}))
  .mockResolvedValueOnce(response({status:'completed',frames:[frame('a',3)]}))
 await createRuntimeSession({},[],1)
 const first=refreshRuntimeFrames();await refreshRuntimeFrames()
 older(response({status:'running',frames:[frame('a',1)]}));await first
 expect(runtimeState().session?.status).toBe('completed')
 expect(runtimeState().session?.frame.seq).toBe(3)
})
it('rejects off-grid frame time',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(response(session('a')))
  .mockResolvedValueOnce(response({status:'paused',frames:[{...frame('a',1),time_s:9}]}))
 await createRuntimeSession({},[],1)
 await expect(refreshRuntimeFrames()).rejects.toThrow(/time/)
})

it('queues live heat without replacing old frames and keeps equilibrium out of the trace',async()=>{
 const client=await import('../src/liquid/liquidRuntimeClient')
 const dae={...session('a'),metadata:{...session('a').metadata,solver:'differentiable_newton_dae',hx_edge_ids:['hx']},
   frame:{...frame('a',0),primary_return_K:[305],secondary_supply_K:[295]}}
 const eq={...dae.frame,kind:'equilibrium',evaluated_at_s:0,time_s:undefined}
 const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(response(dae))
  .mockResolvedValueOnce(response({...dae,metadata:{...dae.metadata,pending_heat_multiplier:1.2}}))
  .mockResolvedValueOnce(response(eq))
 await createRuntimeSession({},[],1)
 const before=structuredClone(runtimeState().frames)
 await client.runtimeHeatInput(1.2)
 expect(JSON.parse(fetch.mock.calls[1]?.[1]?.body as string)).toEqual({heat_multiplier:1.2})
 const answer=await client.runtimeEquilibrium()
 expect(answer.kind).toBe('equilibrium')
 expect(runtimeState().frames).toEqual(before)
 expect(runtimeState().session?.metadata.pending_heat_multiplier).toBe(1.2)
})
