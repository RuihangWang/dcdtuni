import {expect,it} from 'vitest'
import {createOvPostprocessAdapter} from '../src/renderer/ovPostprocessAdapter'
import type {OvNativeIntent} from '../src/renderer/ovNativePostprocess'
import type {DisplaySnapshot} from '../src/postprocess/displaySession'

function fakeClient(calls:unknown[][]) {
  const ack=(extra:object={})=>Promise.resolve({ok:true,requestId:'x',...extra})
  return {
    loadVerifiedResultLayers:()=>ack(),clearResultLayers:()=>ack(),
    loadResultDataset:(grant:unknown)=>{calls.push(['loadResultDataset',grant]);return ack({dataset:{runId:'run-1',time:'1002',bounds:null,fields:{},ranges:{},hasT:true,hasU:true}})},
    setSurfaceDisplay:(f:string,d:unknown)=>{calls.push(['setSurfaceDisplay',f,d]);return ack()},
    setVolumeDisplay:(f:string,d:unknown)=>{calls.push(['setVolumeDisplay',f,d]);return ack({volume:{field:f}})},
    setSlice:(p:{visible:boolean})=>{calls.push(['setSlice',p.visible]);return ack()},
    fireCommand:(n:string,a:{enabled:boolean})=>{calls.push([n,a.enabled]);return Promise.resolve()},
  }
}
const native=(over:Partial<OvNativeIntent>={}):OvNativeIntent=>({runId:'run-1',timeValue:1002,
  surface:{field:'T',visible:true,range:[292,300]},volume:{field:'T',visible:false,range:[292,300]},
  slice:null,stream:null,extraSlices:0,extraStreams:0,...over})
const frame=(n:OvNativeIntent):DisplaySnapshot=>({native:n,packet:null,generation:1,scopeKey:'s',isCurrent:()=>true,
  layers:{surface:{visible:true,colorMap:'coolwarm',climMin:0,climMax:1,autoScale:false}},internalVisible:true,
  volume:{field:'T',unit:'K',visible:false,payload:null,range:[0,1],palette:'coolwarm',opacity:1,resolution:64},
  handles:{ringSetId:'draft',seedSetId:'draft'}} as unknown as DisplaySnapshot)

it('realises the shared selection with kit-cae commands, loading the run once', async () => {
  const calls:unknown[][]=[],grants:unknown[][]=[]
  const adapter=createOvPostprocessAdapter({client:fakeClient(calls) as never,sceneIdle:async()=>{},
    scene:()=>({projectUid:'p',projectHash:'h',sceneRevision:1}),
    requestDatasetGrant:async(runId,time)=>{grants.push([runId,time]);return {grant:{payload:{},signature:'s'} as never,time:'1002'}}})
  const ctx={isCurrent:()=>true}
  expect((await adapter.render(frame(native()),ctx)).status).toBe('displayed')
  expect(grants).toEqual([['run-1',1002]])
  expect(calls).toEqual([['loadResultDataset',{payload:{},signature:'s'}],['setSurfaceDisplay','T',[292,300]]])
  // unchanged selection: nothing is resent; a new field only touches the surface
  calls.length=0
  await adapter.render(frame(native()),ctx);expect(calls).toEqual([])
  await adapter.render(frame(native({surface:{field:'speed',visible:true,range:[0,2]}})),ctx)
  expect(calls).toEqual([['setSurfaceDisplay','U',[0,2]]])
  // a field Kit does not have is reported, not silently dropped
  calls.length=0
  const out=await adapter.render(frame(native({surface:{field:'p_rgh',visible:true,range:[0,1]}})),ctx)
  expect(out.status).toBe('unsupported');expect(calls).toEqual([['setSurfaceDisplay','off',null]])
  // slice and streamlines go to Kit's own plane and seed sphere
  calls.length=0
  await adapter.render(frame(native({slice:{field:'T',visible:true,range:[292,300],origin:[1,1,1],normal:[0,1,0],radius:3},
    stream:{field:'speed',visible:true,centre:[1,1,1],radius:.5,nSeeds:27}})),ctx)
  expect(calls).toEqual([['setSurfaceDisplay','T',[292,300]],['setSlice',true],['setStreamlines',true]])
  adapter.dispose()
})
