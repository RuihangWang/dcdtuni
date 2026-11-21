import type {GridRasterBinding,DecodedRasterObservation} from './displayRaster'

export interface SourcePickContext {binding:GridRasterBinding;output:DecodedRasterObservation;signalPort:number}
export interface SourcePickRequest {binding:{projectUid:string;projectHash:string;sceneRevision:number;viewRevision:number};
  owner:string;rasterRevision:number;decoded:[number,number];point:[number,number];gestureId:number;signalPort:number}
export interface SourcePickReply {schema:'dcdtuni.source-pick/v1';gestureId:number;binding:SourcePickRequest['binding'];owner:string;
  rasterRevision:number;status:'hit'|'miss'|'refused';reason:string;event?:unknown;measurements?:unknown}
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v)
const positive=(v:unknown):v is number=>finite(v)&&v>0

/** Observe the actual DOM contain fit, rather than requested SDK resolution. */
export function videoContentPoint(video:HTMLVideoElement,x:number,y:number):[number,number]|null {
  const r=video.getBoundingClientRect(),s=getComputedStyle(video)
  if(![r.left,r.top,r.width,r.height,x,y].every(finite)||!positive(r.width)||!positive(r.height)
    ||video.videoWidth<=0||video.videoHeight<=0||s.objectFit!=='contain'||video.readyState<2||video.paused||video.ended)return null
  for(const k of ['paddingLeft','paddingRight','paddingTop','paddingBottom','borderLeftWidth','borderRightWidth','borderTopWidth','borderBottomWidth']as const)
    if(Number.parseFloat(s[k])!==0)return null
  let n:HTMLElement|null=video,count=0
  while(n&&count++<64){const st=getComputedStyle(n),active=(key:string)=>{const v=st.getPropertyValue(key).trim();return v!==''&&v!=='none'}
    if(active('rotate')||active('perspective')||active('translate')||st.transformStyle==='preserve-3d')return null
    if(active('scale')){const a=st.getPropertyValue('scale').trim().split(/\s+/).map(Number);if(a.length>2||!a.every(positive))return null}
    if(st.transform&&st.transform!=='none'){const a=/^matrix\(([^)]+)\)$/.exec(st.transform)?.[1]?.split(',').map(Number)
      if(!a||a.length!==6||!a.every(finite)||a[1]!==0||a[2]!==0||a[0]!<=0||a[3]!<=0)return null}
    n=n.parentElement
  }
  if(n)return null
  const scale=Math.min(r.width/video.videoWidth,r.height/video.videoHeight)
  const w=video.videoWidth*scale,h=video.videoHeight*scale,left=r.left+(r.width-w)/2,top=r.top+(r.height-h)/2
  const u=(x-left)/w,v=(y-top)/h
  return u>=0&&u<1&&v>=0&&v<1?[u,v]:null
}

export function validateSourcePickReply(text:unknown,request:SourcePickRequest):SourcePickReply {
  if(typeof text!=='string'||text.length>8192)throw Error('Invalid source pick reply')
  const v=JSON.parse(text) as SourcePickReply
  if(v?.measurements!==undefined){const m=v.measurements as Record<string,unknown>,keys=['appPosition','appSize','framebuffer','mainPoints','scales','framePosition','frameSize','frameContent','canvasSize','resolution']
    if(!m||typeof m!=='object'||Object.keys(m).length!==keys.length||Object.keys(m).some(k=>!keys.includes(k))
      ||keys.some(k=>!Array.isArray(m[k])||(m[k]as unknown[]).length!==(k==='scales'?3:2)
        ||!(m[k]as unknown[]).every(x=>finite(x)&&Math.abs(x)<=16384)))throw Error('Invalid source pick measurements')}
  if(!v||v.schema!=='dcdtuni.source-pick/v1'||v.gestureId!==request.gestureId||v.owner!==request.owner
    ||v.rasterRevision!==request.rasterRevision||(!v.binding||Object.keys(v.binding).length!==4||(['projectUid','projectHash','sceneRevision','viewRevision']as const).some(k=>v.binding[k]!==request.binding[k]))
    ||!['hit','miss','refused'].includes(v.status)||typeof v.reason!=='string'||!/^source-pick-[a-z-]{1,80}$/.test(v.reason)
    ||Object.keys(v).some(k=>!['schema','gestureId','binding','owner','rasterRevision','status','reason','event','measurements'].includes(k))
    ||(v.status==='hit')!==('event'in v))throw Error('Invalid source pick reply')
  return v
}

/** Real pointer-pair + click only. No focus policy, fake keys or SDK internals.
 * Capture listeners prevent the SDK's duplicate Shift selection; plain, Ctrl,
 * Meta, Alt and other pointer types retain the native SDK path unchanged. */
export function installSourcePickPointer(video:HTMLVideoElement,id:string,
  current:()=>SourcePickContext|null,send:(request:SourcePickRequest)=>void) {
  let closed=false,serial=0,frameCallback:number|null=null,lastPresentation=-Infinity,lastPresented=-1,lastCallbackNow=-Infinity
  const resetFrames=()=>{lastPresentation=-Infinity;lastPresented=-1;lastCallbackNow=-Infinity}
  const frame=(now:number,metadata:VideoFrameCallbackMetadata)=>{frameCallback=null
    if(closed)return
    const count=metadata.presentedFrames
    if(!video.isConnected||video.paused||video.ended||video.ownerDocument.visibilityState==='hidden'
      ||!finite(now)||now<0||now<lastCallbackNow||!Number.isSafeInteger(count)||count<1||count<lastPresented)resetFrames()
    else {lastCallbackNow=now;if(count>lastPresented){lastPresented=count;lastPresentation=performance.now()}}
    frameCallback=video.requestVideoFrameCallback(frame)}
  if(typeof video.requestVideoFrameCallback==='function'&&typeof video.cancelVideoFrameCallback==='function')
    frameCallback=video.requestVideoFrameCallback(frame)
  const frameEvents=['pause','ended','emptied','waiting','stalled','error','playing','seeked']
  frameEvents.forEach(e=>video.addEventListener(e,resetFrames))
  video.ownerDocument.addEventListener('visibilitychange',resetFrames)
  type Pending={id:number;x:number;y:number;at:number;context:string;up:boolean;moved:boolean}
  let pending:Pending|null=null
  const live=()=>!closed&&video.isConnected&&document.getElementById(id)===video
    &&video.parentElement?.id===`${id}-input-parent`
  const ownerContext=()=>{if(!live()||video.paused||video.ended||performance.now()-lastPresentation>1000)return null
    const c=current();if(!c||c.output.decoded[0]!==video.videoWidth||c.output.decoded[1]!==video.videoHeight)return null
    const r=video.getBoundingClientRect();if(c.output.css[0]!==r.width||c.output.css[1]!==r.height
      ||c.output.layoutCss[0]!==video.clientWidth||c.output.layoutCss[1]!==video.clientHeight)return null
    return c}
  const shift=(e:MouseEvent)=>e.isTrusted&&e.button===0&&e.shiftKey&&!e.ctrlKey&&!e.metaKey&&!e.altKey
  const stop=(e:Event)=>{e.preventDefault();e.stopImmediatePropagation()}
  const down=(e:PointerEvent)=>{pending=null
    if(e.target!==video||e.pointerType!=='mouse'||!shift(e))return
    const c=ownerContext(),point=videoContentPoint(video,e.clientX,e.clientY)
    if(!c||!point||!finite(e.timeStamp))return
    pending={id:e.pointerId,x:e.clientX,y:e.clientY,at:e.timeStamp,context:JSON.stringify(c),up:false,moved:false};stop(e)}
  const move=(e:PointerEvent)=>{if(pending&&e.pointerId===pending.id&&Math.hypot(e.clientX-pending.x,e.clientY-pending.y)>5)pending.moved=true}
  const up=(e:PointerEvent)=>{if(!pending||e.pointerId!==pending.id)return
    if(e.target!==video||!shift(e)||pending.moved||Math.hypot(e.clientX-pending.x,e.clientY-pending.y)>5){pending=null;return}
    pending.up=true;stop(e)}
  const mouse=(e:MouseEvent)=>{if(pending&&e.target===video&&e.isTrusted&&e.button===0)stop(e)}
  const click=(e:MouseEvent)=>{const p=pending;pending=null
    if(!p||e.target!==video||!shift(e))return
    stop(e);const c=ownerContext(),point=videoContentPoint(video,e.clientX,e.clientY)
    if(!p.up||p.moved||!c||!point||JSON.stringify(c)!==p.context||!finite(e.timeStamp)
      ||e.timeStamp<p.at||e.timeStamp-p.at>2000||Math.hypot(e.clientX-p.x,e.clientY-p.y)>5)return
    send({binding:{projectUid:c.binding.projectUid,projectHash:c.binding.projectContentSha256,
      sceneRevision:c.binding.sceneRevision,viewRevision:c.binding.viewRevision},owner:c.output.owner,
      rasterRevision:c.output.revision,decoded:[...c.output.decoded],point,gestureId:++serial,signalPort:c.signalPort})}
  const cancel=()=>{pending=null}
  video.addEventListener('pointerdown',down,true);video.addEventListener('mousedown',mouse,true)
  video.addEventListener('mouseup',mouse,true);video.addEventListener('click',click,true)
  window.addEventListener('pointermove',move,true);window.addEventListener('pointerup',up,true)
  window.addEventListener('pointercancel',cancel,true);window.addEventListener('blur',cancel)
  return{dispose(){if(closed)return;closed=true;pending=null;resetFrames()
    if(frameCallback!==null)video.cancelVideoFrameCallback(frameCallback)
    frameEvents.forEach(e=>video.removeEventListener(e,resetFrames))
    video.ownerDocument.removeEventListener('visibilitychange',resetFrames)
    video.removeEventListener('pointerdown',down,true);video.removeEventListener('mousedown',mouse,true)
    video.removeEventListener('mouseup',mouse,true);video.removeEventListener('click',click,true)
    window.removeEventListener('pointermove',move,true);window.removeEventListener('pointerup',up,true)
    window.removeEventListener('pointercancel',cancel,true);window.removeEventListener('blur',cancel)}}
}
