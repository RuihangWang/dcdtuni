import {afterEach,expect,it,vi} from 'vitest'
import {PostprocessDisplayDriver,type DisplayInput} from '../src/postprocess/displaySession'
import type {ResultLayerSet,ResultSource} from '../src/renderer/resultLayers'
import {createOvPostprocessAdapter} from '../src/renderer/ovPostprocessAdapter'
import {clearResultLayerSet,setLiveAirResultSelection} from '../src/renderer/resultLayerRevision'
const sha=(v:string)=>v.repeat(64)
function input(): DisplayInput {
 const session={runId:sha('9'),timeValue:0.015,revision:'pp-current'}
 const source={projectUid:'p',projectContentSha256:sha('a'),cfdrunSha256:sha('b'),meshReceiptSha256:sha('c'),meshQuality:'accepted' as const,run:{uid:'run-execution',receiptSha256:sha('d')}}
 const mesh={positions:new Float32Array([0,0,0,1,0,0,0,1,0]),indices:new Uint32Array([0,1,2]),edges:new Uint32Array([0,1,1,2,2,0]),patches:[{name:'wall',nPoints:3,nTriangles:1,nEdges:3,defaultVisible:true}],totals:{points:3,triangles:1,edges:3}}
 setLiveAirResultSelection({owner:'air-postprocess',session,surfaceField:'T',meshReceiptSha256:sha('c')})
 return {layers:{surface:{visible:true,colorMap:'viridis',climMin:299,climMax:303,autoScale:false}},
  windFrame:{session,descriptor:{schema:'dcdtuni.verified-air-result-source/v1',postprocess:session,source,fieldUnits:{T:'K'},sourceFacesByPatch:{wall:['p/wall/face']}},
    currentProject:{uid:'p',contentSha256:sha('a')},mesh,activeSurfaceField:'T',surface:{field:'T',values:new Float32Array([300,301,302]),colorMap:'viridis',range:[299,303],opacity:0.23,visible:true,hidden:new Set()},slices:[],streamlines:[]},
  volume:{field:'T',unit:'K',visible:false,payload:null,range:[299,303],palette:'viridis',opacity:0.23,resolution:256},internalVisible:true,handles:{ringSetId:42,seedSetId:9},isCurrent:()=>true}
}
function setup() {
 let scene:{projectUid:string;projectHash:string;sceneRevision:number}|null={projectUid:'p',projectHash:sha('a'),sceneRevision:7}
 const client={loadResultLayers:vi.fn(async(packet:ResultLayerSet,_source:ResultSource,_time:number|null)=>({requestId:'ack',ok:true,revision:packet.revision})),clearResultLayers:vi.fn(async()=>({requestId:'ack',ok:true}))}
 const idle=vi.fn(async()=>{})
 const adapter=createOvPostprocessAdapter({client,sceneIdle:idle,scene:()=>scene})
 return {client,idle,adapter,setScene:(value:typeof scene)=>scene=value}
}
afterEach(()=>{clearResultLayerSet();setLiveAirResultSelection(null)})
it('lowers exactly the public accepted source/time and raw LUT without requesting another run',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);d.present(input());await d.whenIdle()
 const [packet,source,time]=s.client.loadResultLayers.mock.calls[0]!
 expect(time).toBe(0.015);expect(packet.timeSeconds).toBe(0.015);expect(source).toEqual(packet.source)
 expect(packet.layers.find((layer:{kind:string})=>layer.kind==='surfaceField')).toMatchObject({field:{name:'T',unit:'K'},lut:{palette:'viridis',range:[299,303],opacity:0.23}})
 expect(d.status().status).toBe('displayed')
})
it('does not bypass missing accepted mesh/run provenance for a diagnostic frame',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter),v=input();v.windFrame=null;d.present(v);await d.whenIdle()
 expect(s.client.loadResultLayers).not.toHaveBeenCalled();expect(d.status()).toMatchObject({status:'rejected'})
})
it('requires the currently acknowledged project content and scene generation',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);s.setScene({projectUid:'other',projectHash:sha('a'),sceneRevision:7})
 d.present(input());await d.whenIdle();expect(s.client.loadResultLayers).not.toHaveBeenCalled()
 s.setScene({projectUid:'p',projectHash:sha('f'),sceneRevision:7});d.present(input());await d.whenIdle();expect(s.client.loadResultLayers).not.toHaveBeenCalled()
})
it('refuses an independently changed accepted session before any OV transfer',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter),v=input();d.present(v)
 setLiveAirResultSelection({owner:'air-postprocess',session:{...v.windFrame!.session,revision:'pp-new'},surfaceField:'T',meshReceiptSha256:sha('c')})
 await d.whenIdle();expect(s.client.loadResultLayers).not.toHaveBeenCalled();expect(d.status().status).toBe('rejected')
})
it('preserves all common volume parameters and reports the missing converter explicitly',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter),v=input();v.volume.visible=true
 v.volume.payload={dimensions:[2,2,2],origin:[0,0,0],spacing:[1,1,1],scalarRange:[299,303],sentinel:-1e30,scalars:new Float32Array(8).fill(300)}
 d.present(v);await d.whenIdle();expect(d.status()).toMatchObject({status:'unsupported'})
 expect(d.status().message).toContain('256³, viridis, opacity 0.23');expect(d.current()!.volume.payload).toBe(v.volume.payload)
})
it('reports pending preview conversion with concrete list identity without re-tracing a singleton',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter),v=input();v.layers.ring={show:true,origin:[0,1,0],normal:[0,1,0],radius:2};v.layers.seed={show:true,centre:[0,1,0],radius:1}
 d.present(v);await d.whenIdle();expect(d.status().message).toContain('handle 42');expect(d.status().message).toContain('handle 9')
 expect(s.client.loadResultLayers).toHaveBeenCalledOnce()
})
it('keeps Kit provenance rejection visible instead of reporting an optimistically displayed frame',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);s.client.loadResultLayers.mockRejectedValueOnce(new Error('Trusted renderer provenance is unavailable'))
 d.present(input());await d.whenIdle();expect(d.status()).toMatchObject({status:'rejected',message:'Trusted renderer provenance is unavailable'})
})
it('does not certify a late ACK after a new project stage is acknowledged',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);let resolve!:(value:{requestId:string;ok:boolean;revision:number})=>void
 s.client.loadResultLayers.mockImplementationOnce(()=>new Promise(done=>resolve=done));d.present(input())
 for(let i=0;i<4;i++)await Promise.resolve();const revision=d.current()!.packet!.revision
 s.setScene({projectUid:'p',projectHash:sha('a'),sceneRevision:8});resolve({requestId:'ack',ok:true,revision});await d.whenIdle()
 expect(d.status().status).toBe('rejected')
})
it('makes a disposed adapter inert while waiting for scene idle, with no new mutation',async()=>{
 const s=setup(),d=new PostprocessDisplayDriver(s.adapter);let release!:()=>void;s.idle.mockImplementationOnce(()=>new Promise(done=>release=done));d.present(input())
 await Promise.resolve();s.adapter.dispose();release();await d.whenIdle();expect(s.client.loadResultLayers).not.toHaveBeenCalled()
})

it('serializes an already in-flight old clear before the new accepted air author on the same client',async()=>{
 const s=setup();let release!:(value:{requestId:string;ok:boolean})=>void;const stage:string[]=[]
 s.client.clearResultLayers.mockImplementationOnce(async()=>{await new Promise<{requestId:string;ok:boolean}>(done=>release=done);stage.push('old-clear');return {requestId:'clear',ok:true}})
 s.client.loadResultLayers.mockImplementation(async(packet)=>{stage.push('new-air');return {requestId:'load',ok:true,revision:packet.revision}})
 const previous=new PostprocessDisplayDriver(s.adapter);previous.present(input());await previous.whenIdle();s.client.loadResultLayers.mockClear();stage.length=0
 const cleared=s.adapter.clear({isCurrent:()=>true});for(let i=0;i<4;i++)await Promise.resolve()
 const d=new PostprocessDisplayDriver(s.adapter);d.present(input());for(let i=0;i<6;i++)await Promise.resolve()
 expect(s.client.loadResultLayers).not.toHaveBeenCalled()
 release({requestId:'clear',ok:true});await cleared;await d.whenIdle()
 expect(stage).toEqual(['old-clear','new-air']);expect(d.status().status).toBe('displayed')
})
