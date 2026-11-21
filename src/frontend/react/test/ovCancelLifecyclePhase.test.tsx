/** Literal installed SDK class/timer/selector and real product lease/view.
 * Controlled transport/DOM/clock only. NO socket/API/browser/Kit is used. */
import{readFileSync}from'node:fs'
import{resolve}from'node:path'
import{createHash}from'node:crypto'
import{act,useState}from'react'
import{createRoot}from'react-dom/client'
import{expect,it,vi}from'vitest'
import{OvStreamLeases,terminateOvViewer,createOvStartReceipt}from'../src/renderer/ovStreamLease'
import{augmentOvSdk,OV_SDK_SHA256,OV_SDK_ANCHOR,OV_SDK_DIAGNOSTIC}from'../build/ovSdkDiagnostics'
const active=vi.hoisted(()=>({app:null as any}))
vi.mock('@nvidia/omniverse-webrtc-streaming-library',()=>({StreamStatus:{none:0},AppStreamer:{connect:(options:any)=>active.app.connect(options),terminate:(destroy:boolean)=>active.app.terminate(destroy),get streamStatus(){return active.app.streamStatus}}}))
vi.mock('../src/useTranslation',()=>({useTranslation:()=>({t:(key:string)=>key})}))
vi.mock('../src/renderer/ovProtocol',()=>({OvCommandClient:class{dispose(){}},ovAckTimeoutDegraded:()=>false}))
import{OvViewport}from'../src/renderer/OvViewport'
const raw=readFileSync(resolve('node_modules/@nvidia/omniverse-webrtc-streaming-library/dist/omniverse-webrtc-streaming-library.js'),'utf8')
function balanced(source:string,start:number){let depth=0,quote='',escaped=false;for(let i=source.indexOf('{',start);i<source.length;i++){const c=source[i]!;if(quote){if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c===quote)quote='';continue}if(c==='"'||c==="'"||c==='`'){quote=c;continue}if(c==='{')depth++;else if(c==='}'&&--depth===0)return source.slice(start,i+1)}throw Error('SDK literal region not delimited')}
function bytes(name:string){const anchor='class '+name+'{';const i=raw.indexOf(anchor);expect(i).toBeGreaterThan(-1);const source=process.env.DCDTUNI_CANCEL_BASELINE==='1'?raw.replace(OV_SDK_ANCHOR,'onStreamStart(e){'+OV_SDK_DIAGNOSTIC+OV_SDK_ANCHOR.slice('onStreamStart(e){'.length)):augmentOvSdk(raw);return balanced(source,source.indexOf(anchor))}
function nativeMethod(name:string){const anchor=name+'(){';expect(raw.split(anchor)).toHaveLength(2);return balanced(raw,raw.indexOf(anchor))}
const enumStart=raw.indexOf('e.RErrorCode=void 0,')+'e.RErrorCode=void 0,'.length,enumEnd=raw.indexOf('(e.RErrorCode||(e.RErrorCode={}))',enumStart)+'(e.RErrorCode||(e.RErrorCode={}))'.length
const R:any={};new Function('e','return ('+raw.slice(enumStart,enumEnd)+');')(R)
function fixture(lag=0){
 expect(createHash('sha256').update(raw).digest('hex')).toBe(OV_SDK_SHA256)
 const events:Array<{ms:number;phase:string;code?:number;status?:string}>=[];let lower:any,player:any,lowStops=0,transportActive=false,App:any
 const event=(phase:string,extra={})=>events.push({ms:Date.now(),phase,...extra})
 const storage=(o:object,key:string,value:unknown)=>Object.defineProperty(o,key,{value,writable:true,configurable:true,enumerable:true})
 const status={success:'success',error:'error',warning:'warning',canceled:'canceled',inProgress:'inProgress'}
 const log={event:(v:any)=>event('sdk-converted',{status:v.status}),setLogLevel(){},setLogFormat(){}}
 const Player=new Function('R','ne','Ji','fe','$','ul','setTimeout','al',bytes('Rs')+';'+bytes('je')+';'+bytes('dl')+';'+bytes('gl')+';return gl')(R,storage,{startSessionResultEvent:'StartSessionResultEvent'},log,status,class{},setTimeout,(n:number)=>'0x'+n.toString(16))
 const Native=new Function('window','t','Tr','Pe','return class Native{'+['kN','TN','bN'].map(nativeMethod).join('')+'}')({setTimeout}, {Log:{i(){}}},()=>'',()=>false)
 class ControlledDirect{async connect(config:any){if(lag)await new Promise(done=>setTimeout(done,lag));transportActive=true;lower=new Native();Object.assign(lower,{id:'CONTROLLED_PEER',cR:false,videoStreamProgress:0,EN:()=>15859712,SN:(code:number)=>{event('native-deadline',{code});player.onStreamStart({error:code})}});player=new Player({...config,maxReconnects:0,reconnectDelay:2000,mic:false,onConnectionDiagnostic:(v:any)=>event('native-before-cancel-wrapper',{code:v.errorCode})});player.startButtonClick=()=>event('retry');lower.kN();event('initialized');return{status:'inProgress'}}async terminate(){lowStops++;transportActive=false;clearTimeout(lower.oR);event('lower-terminate');return{status:'success'}}}
 const anchor='const oe=class e{';expect(raw.split(anchor)).toHaveLength(2);const klass=balanced(raw,raw.indexOf(anchor)+'const oe='.length)
 App=new Function('ne','fe','La','vr','yl','bl','$a','En','$','ie','fr',klass+';ne(e,"_stream");ne(e,"_streamStatus",0);ne(e,"_disconnectStatus",0);return e')(storage,log,{INFO:0},{JSON:0},class{},ControlledDirect,ControlledDirect,(v:any)=>({...v,streamConfig:{...v.streamConfig}}),status,{start:'start',terminate:'terminate'},()=>true)
 const receipt=createOvStartReceipt()
 const options={streamSource:'direct',streamConfig:{onStart:(v:any)=>{receipt.record(v);event('public-start',{status:v.status})}}}
 return{App,receipt,events,options,connect:()=>App.connect(options),get transportActive(){return transportActive},get lowStops(){return lowStops},success:()=>{event('controlled-media-success');player.onStreamStart({})},error:()=>player.onStreamStart({error:3237093913}),guard:(fn:()=>unknown)=>{(options.streamConfig as any).shouldCancelReconnect=fn;if(player)player._props.shouldCancelReconnect=fn}}
}

async function clock(action:()=>Promise<void>){vi.useFakeTimers();vi.setSystemTime(0);try{await action()}finally{vi.clearAllTimers();vi.useRealTimers()}}
function phaseOptions(onCancellationMarked=()=>{}){return{pendingStartMs:45000,onCancellationMarked}}
function phaseStop(f:ReturnType<typeof fixture>,ready:Promise<unknown>,marked:()=>void){return(terminateOvViewer as any)(()=>f.App.terminate(false),()=>f.App.streamStatus===0,ready,15000,50,f.receipt,phaseOptions(marked)) as Promise<void>}
it('real SDK native30s boundary after160ms init releases the old lease only after terminal and idle, then starts the next owner',()=>clock(async()=>{
 const f=fixture(160),leases=new OvStreamLeases();let cancelled=false;f.guard(()=>cancelled)
 const first=leases.open(()=>f.connect(),ready=>phaseStop(f,ready,()=>{cancelled=true}));await vi.advanceTimersByTimeAsync(15000);await first.ready
 const closed=first.close().then(()=>({ok:true}),error=>({ok:false,error})),start=vi.fn(async()=>{}),next=leases.open(start,async()=>{}),nextResult=next.ready.then(()=>({ok:true}),error=>({ok:false,error}))
 await vi.advanceTimersByTimeAsync(15000);expect(start).not.toHaveBeenCalled();expect(f.lowStops).toBe(0)
 await vi.advanceTimersByTimeAsync(1160);expect(await closed).toEqual({ok:true});expect(await nextResult).toEqual({ok:true});expect(start).toHaveBeenCalledOnce();expect(f.lowStops).toBe(1)
 await vi.advanceTimersByTimeAsync(2000);expect(f.events.filter(e=>e.phase==='retry')).toHaveLength(0);await next.close()
 expect(f.events.find(e=>e.phase==='native-before-cancel-wrapper')).toMatchObject({ms:30160,code:3237093913});expect(f.receipt.settled).toBe(true)
}))
it('actual SDK marked connecting cancellation retains NoOffer receipt but never starts a new retry after lower teardown',()=>clock(async()=>{
 const f=fixture();let cancelled=false;f.guard(()=>cancelled);await f.connect();await vi.advanceTimersByTimeAsync(15000)
 expect(await f.App.terminate(false)).toMatchObject({status:'inProgress'});cancelled=true;await vi.advanceTimersByTimeAsync(17000)
 expect(f.lowStops).toBe(1);expect(f.App.streamStatus).toBe(0);expect(f.events.find(e=>e.phase==='native-before-cancel-wrapper')).toMatchObject({code:3237093913})
 expect(f.events.filter(e=>e.phase==='retry')).toHaveLength(0);expect(f.events.find(e=>e.phase==='public-start')).toMatchObject({status:'canceled'})
}))
it('actual SDK first error already queued before cancellation cannot execute its delayed retry afterward',()=>clock(async()=>{
 const f=fixture();let cancelled=false;f.guard(()=>cancelled);await f.connect();await vi.advanceTimersByTimeAsync(14000);f.error()
 expect(f.events.find(e=>e.phase==='public-start')).toMatchObject({status:'warning'});await vi.advanceTimersByTimeAsync(1000)
 expect(await f.App.terminate(false)).toMatchObject({status:'inProgress'});cancelled=true;await vi.advanceTimersByTimeAsync(17000)
 expect(f.events.filter(e=>e.phase==='retry')).toHaveLength(0);expect(f.lowStops).toBe(1);expect(f.App.streamStatus).toBe(0)
}))
it('real OvViewport failure occurs at 30 s, not before; its cleanup permits a later same-page viewer without serializing the canceled scene',()=>clock(async()=>{
 const f=fixture(160);active.app=f.App;const errors:Error[]=[],snapshot=vi.fn(()=>{throw Error('canceled scene must not serialize')});let show!:()=>void
 function Gate(){const[shown,setShown]=useState(true);show=()=>setShown(true);return shown?<OvViewport server="not-contacted.invalid" snapshot={snapshot} projectHash={()=>'controlled'} onError={error=>{errors.push(error);setShown(false)}}/>:<p>three retained</p>}
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);const old=(globalThis as any).IS_REACT_ACT_ENVIRONMENT;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;const info=vi.spyOn(console,'info').mockImplementation(()=>{})
 try{await act(async()=>root.render(<Gate/>));await act(async()=>vi.advanceTimersByTimeAsync(15000));expect(errors).toHaveLength(0)
 await act(async()=>vi.advanceTimersByTimeAsync(15000));expect(errors[0]!.message).toBe('Omniverse connection timed out');expect(f.lowStops).toBe(0)
 await act(async()=>vi.advanceTimersByTimeAsync(18000));expect(f.lowStops).toBe(1);expect(f.events.filter(e=>e.phase==='retry')).toHaveLength(0)
 const next=fixture();active.app=next.App;await act(async()=>show());await act(async()=>vi.advanceTimersByTimeAsync(100));expect(next.transportActive).toBe(true);expect(errors).toHaveLength(1);expect(snapshot).not.toHaveBeenCalled()
 await act(async()=>root.unmount());next.success();await act(async()=>vi.advanceTimersByTimeAsync(100))
 }finally{await act(async()=>root.unmount());host.remove();info.mockRestore();(globalThis as any).IS_REACT_ACT_ENVIRONMENT=old}
}))
