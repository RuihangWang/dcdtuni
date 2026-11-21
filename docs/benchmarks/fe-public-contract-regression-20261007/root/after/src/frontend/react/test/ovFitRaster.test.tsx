/** Actual installed SDK base controller fitting; only transport/DOM carriers controlled. */
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {createHash} from 'node:crypto'
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {expect,it,vi} from 'vitest'
import {augmentOvSdk,OV_SDK_SHA256} from '../build/ovSdkDiagnostics'
const raw=readFileSync(resolve('node_modules/@nvidia/omniverse-webrtc-streaming-library/dist/omniverse-webrtc-streaming-library.js'),'utf8')
function klass(source:string,name:string){const start=source.indexOf('class '+name+'{');expect(start).toBeGreaterThan(-1);let depth=0,quote='',escaped=false;for(let i=source.indexOf('{',start);i<source.length;i++){const c=source[i]!;if(quote){if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c===quote)quote='';continue}if(c==='"'||c==="'"||c==='`'){quote=c;continue}if(c==='{')depth++;else if(c==='}'&&--depth===0)return source.slice(start,i+1)}throw Error('SDK class not delimited')}
function controller(source:string=augmentOvSdk(raw)){
 const effects:unknown[]=[],video={clientWidth:1320,clientHeight:560};let config:any,mounted=true
 const storage=(o:object,k:string,value:unknown)=>Object.defineProperty(o,k,{value,writable:true,configurable:true,enumerable:true})
 // Fit helper, if present, comes from the same actual transformed SDK source.
 const helper=source.match(/function dcdtuniFitRaster\([^\n]+/);const body=(helper?.[0]??'')+';'+klass(source,'$a')+';return $a'
 const Base=new Function('ne','document','ie','$','En','fr','fe','ai','It',body)(storage,{getElementById:()=>mounted?video:null},{start:'start',update:'update'},{success:'success',error:'error',warning:'warning'},(x:any)=>({...x}),()=>true,{event:(x:unknown)=>effects.push(['log',x])},{json:'json'},{stream:'stream'})
 const base=new Base();base._connect=async(options:any)=>{config=options;return{status:'inProgress'}};base._ragnarokApp={sendCustomMessage:async(message:any)=>{effects.push(['resize',message]);return{status:'success',info:'CONTROLLED_SEND_RETURN'}}}
 return{base,effects,video,native:base._ragnarokApp,detach:()=>{mounted=false},get config(){return config}}
}
const sdk=vi.hoisted(()=>({options:null as any}))
vi.mock('@nvidia/omniverse-webrtc-streaming-library',()=>({StreamStatus:{none:0},AppStreamer:{connect:async(options:any)=>{sdk.options=options;return{status:'inProgress'}},terminate:async()=>({status:'success'}),streamStatus:0}}))
vi.mock('../src/useTranslation',()=>({useTranslation:()=>({t:(key:string)=>key})}))
vi.mock('../src/renderer/ovProtocol',()=>({OvCommandClient:class{dispose(){}},ovAckTimeoutDegraded:()=>false}))
import{OvViewport}from'../src/renderer/OvViewport'
it('real View uses explicit aligned resizing while retaining initial media caps and native wait policy',async()=>{
 const host=document.createElement('div'),root=createRoot(host);document.body.append(host);const old=(globalThis as any).IS_REACT_ACT_ENVIRONMENT;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true
 try{await act(async()=>root.render(<OvViewport server="not-contacted.invalid" snapshot={()=>{throw Error('no media')}} projectHash={()=>'controlled'}/>));expect(sdk.options.streamConfig).toMatchObject({fitStreamResolution:false,width:1600,height:900,maxReconnects:0})}finally{await act(async()=>root.unmount());sdk.options?.streamConfig.onStart({status:'canceled'});await Promise.resolve();await Promise.resolve();host.remove();(globalThis as any).IS_REACT_ACT_ENVIRONMENT=old}
})
it('actual SDK onStart(success) fits the DOM raster before forwarding public start, retaining caps at initial connect',async()=>{
 const f=controller();await f.base.connect({width:1600,height:900,fitStreamResolution:true,videoElementId:'CONTROLLED',onStart:()=>f.effects.push(['start'])})
 expect(f.config).toMatchObject({width:1600,height:900});f.config.onStart({status:'success'});expect(f.effects).toEqual([['resize',{messageType:'json',messageRecipient:'stream',data:{type:'resize',value:{height:560,width:1320}}}],['start']])
})
it('actual fit resize keeps a wide aspect inside both caps, with only bounded integer raster quantization',async()=>{
 const f=controller();await f.base.connect({width:1600,height:900,fitStreamResolution:true,videoElementId:'CONTROLLED'})
 const reply=await f.base.resize(2400,1000),value=(f.effects[0]as any)[1].data.value
 expect(value).toEqual({width:1600,height:666});expect(reply).toEqual({status:'success',info:'CONTROLLED_SEND_RETURN'});expect(Math.abs(value.height-1000*1600/2400)).toBeLessThan(1)
})
it.each([[1320,560],[800,1600],[4096,1080],[3840,2160],[1920,1200],[220,900],[900,220]])('actual fitting is bounded and preserves each axis to under one raster pixel for%j',async(w,h)=>{
 const f=controller();await f.base.connect({width:1600,height:900,fitStreamResolution:true,videoElementId:'CONTROLLED'});await f.base.resize(w,h);const v=(f.effects[0]as any)[1].data.value,scale=Math.min(1,1600/w,900/h)
 expect(v.width).toBeGreaterThan(0);expect(v.height).toBeGreaterThan(0);expect(v.width).toBeLessThanOrEqual(1600);expect(v.height).toBeLessThanOrEqual(900);expect(Math.abs(v.width-w*scale)).toBeLessThan(1);expect(Math.abs(v.height-h*scale)).toBeLessThan(1)
})
it('zero-sized DOM resize holds the last raster without emitting a native zero frame; invalid sizes fail before communication',async()=>{
 const f=controller();await f.base.connect({width:1600,height:900,fitStreamResolution:true,videoElementId:'CONTROLLED'})
 expect(f.base.resize(0,560)).toBeUndefined();expect(f.base.resize(1320,0)).toBeUndefined();expect(f.effects).toEqual([])
 for(const [w,h]of[[NaN,560],[1320,Infinity],[-1,560],[true as any,560],[1320.5,560],[100000,1]])expect(()=>f.base.resize(w,h)).toThrow()
 expect(f.effects).toEqual([])
})
it('false-fit literal SDK retains exact resize messages, return, overflow rejection and first-start callback order',async()=>{
 const before=controller(raw),after=controller();for(const f of[before,after]){await f.base.connect({width:1600,height:900,fitStreamResolution:false,videoElementId:'CONTROLLED',onStart:()=>f.effects.push(['start'])});f.config.onStart({status:'success'});expect(await f.base.resize(1320,560)).toEqual({status:'success',info:'CONTROLLED_SEND_RETURN'});expect(()=>f.base.resize(2400,1000)).toThrow()}
 expect(after.effects).toEqual(before.effects);expect(createHash('sha256').update(raw).digest('hex')).toBe(OV_SDK_SHA256)
})

it('a late successful start for a removed video forwards its original start callback but cannot resize a different viewer',async()=>{const f=controller();await f.base.connect({width:1600,height:900,fitStreamResolution:true,videoElementId:'CONTROLLED_REMOVED',onStart:()=>f.effects.push(['start'])});f.detach();f.config.onStart({status:'success'});expect(f.effects).toEqual([['start']])})

function liveController(source:string=augmentOvSdk(raw)){
 const effects:unknown[]=[],video={clientWidth:1320,clientHeight:560};let options:any
 const storage=(o:object,k:string,value:unknown)=>Object.defineProperty(o,k,{value,writable:true,configurable:true,enumerable:true})
 const helper=source.match(/function dcdtuniFitRaster\([^\n]+/),body=(helper?.[0]??'')+';'+klass(source,'$a')+';return $a'
 // The actual SDK _connect is executed. gl is only its native/UI carrier;
 // its resize callback, public wrapper and raster calculation are not stubbed.
 class Transport{constructor(o:any){options=o}async init(){effects.push(['init'])}async startButtonClick(){effects.push(['native-start'])}async sendCustomMessage(m:any){effects.push(['resize',m]);return{status:'success'}}}
 const Base=new Function('ne','document','ie','$','En','fr','fe','ai','It','hl','Ai','gl','Uh','Ln','Al',body)(storage,{getElementById:()=>video},{start:'start',update:'update'},{success:'success',error:'error',warning:'warning',inProgress:'inProgress'},(x:any)=>({...x}),()=>true,{event:()=>{}},{json:'json'},{stream:'stream'},{maxReconnects:0,authenticate:false},{platformDetails:async()=>({})},Transport,class{},class{},'controlled')
 return{base:new Base(),effects,video,get options(){return options}}
}
it('actual SDK live resize callback uses the bounded shared canvas aspect across layout changes, with original init/start order',async()=>{
 const f=liveController();expect(await f.base.connect({width:1600,height:900,fitStreamResolution:true,videoElementId:'CONTROLLED',autoLaunch:true})).toMatchObject({status:'inProgress'});expect(f.effects).toEqual([['init'],['native-start']])
 for(const [width,height]of[[1320,560],[800,1600],[2400,1000]]){f.options.onResize({width,height});await Promise.resolve()}
 expect(f.effects.slice(2).map((x:any)=>x[1].data.value)).toEqual([{width:1320,height:560},{width:450,height:900},{width:1600,height:666}])
})
it('actual false-fit live resize callback remains inert and the initial SDK connect cap refusal is unchanged',async()=>{
 for(const source of[raw,augmentOvSdk(raw)]){const f=liveController(source);await f.base.connect({width:1600,height:900,fitStreamResolution:false,videoElementId:'CONTROLLED'});f.options.onResize({width:800,height:1600});expect(f.effects).toEqual([['init']]);await expect(f.base.connect({width:4097,height:900,fitStreamResolution:true})).rejects.toMatchObject({status:'error'});expect(f.effects).toEqual([['init']])}
})
it('a fitted native error reply or rejected transport remains the original result and cannot be promoted to success',async()=>{
 const f=controller();await f.base.connect({width:1600,height:900,fitStreamResolution:true,videoElementId:'CONTROLLED'});const reply={status:'error',info:'CONTROLLED_NATIVE_REFUSAL'};f.native.sendCustomMessage=async()=>reply;expect(await f.base.resize(2400,1000)).toBe(reply)
 const refusal=Error('CONTROLLED_TRANSPORT_REFUSAL');f.native.sendCustomMessage=async()=>{throw refusal};await expect(f.base.resize(800,1600)).rejects.toBe(refusal)
})
