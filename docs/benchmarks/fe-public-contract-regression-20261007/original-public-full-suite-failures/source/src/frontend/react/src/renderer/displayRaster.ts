import * as THREE from 'three'

/** Owned display observations only; no numeric/source authority. */
export interface LineRasterObservation {
  owner:string; drawingBuffer:[number,number]; css:[number,number]; pixelRatio:number
  aliasedRange:[number,number]; effectiveWidthPx:number
  coordinateFrame:'scene-world'; worldToClip:number[]
}
export interface DecodedRasterObservation {
  owner:string; revision:number; decoded:[number,number]
  css:[number,number]; layoutCss:[number,number]; objectFit:'contain'
}
export type DecodedRasterEvent= {type:'owner';owner:string}|{type:'raster';raster:DecodedRasterObservation}|{type:'retired';owner:string}|{type:'unobserved';owner:string;revision:number}
export interface GridRasterBinding {projectUid:string;projectContentSha256:string;sceneRevision:number;viewRevision:number}
export interface BackgroundGridRaster {metricVersion:2;line:LineRasterObservation;output:DecodedRasterObservation;binding:GridRasterBinding}
let serial=0
export function newRasterOwner():string{return `raster:${Date.now().toString(36)}:${++serial}`}
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v)
const positive=(v:unknown):v is number=>finite(v)&&v>0
const integer=(v:unknown):v is number=>finite(v)&&Number.isSafeInteger(v)&&v>=0
const owner=(v:unknown):v is string=>typeof v==='string'&&v.length>0&&v.length<=128
const pair=(v:unknown,valid:(v:unknown)=>boolean):v is[number,number]=>Array.isArray(v)&&v.length===2&&v.every(valid)
const exact=(v:unknown,keys:string[]):boolean=>Boolean(v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&Object.keys(v).every(k=>keys.includes(k)))
export function assertGridRaster(v:BackgroundGridRaster):void {
 if(!exact(v,['metricVersion','line','output','binding'])||v.metricVersion!==2)throw Error('Unsupported decoration presentation metric')
 const l=v.line,o=v.output,b=v.binding
 if(!exact(l,['owner','drawingBuffer','css','pixelRatio','aliasedRange','effectiveWidthPx','coordinateFrame','worldToClip'])||!owner(l.owner)||!pair(l.drawingBuffer,x=>integer(x)&&x>0)||!pair(l.css,positive)||!positive(l.pixelRatio)||!pair(l.aliasedRange,positive)||l.aliasedRange[0]>l.aliasedRange[1]||!positive(l.effectiveWidthPx)||l.effectiveWidthPx>8||l.effectiveWidthPx<l.aliasedRange[0]||l.effectiveWidthPx>l.aliasedRange[1]||l.coordinateFrame!=='scene-world'||!Array.isArray(l.worldToClip)||l.worldToClip.length!==16||!l.worldToClip.every(finite))throw Error('Invalid measured reference presentation')
 if(!exact(o,['owner','revision','decoded','css','layoutCss','objectFit'])||!owner(o.owner)||!integer(o.revision)||!pair(o.decoded,x=>integer(x)&&x>0)||o.decoded[0]>1600||o.decoded[1]>900||!pair(o.css,positive)||!pair(o.layoutCss,positive)||o.objectFit!=='contain')throw Error('Invalid observed video presentation')
 if(!exact(b,['projectUid','projectContentSha256','sceneRevision','viewRevision'])||!owner(b.projectUid)||!/^[0-9a-f]{64}$/.test(b.projectContentSha256)||!integer(b.sceneRevision)||!integer(b.viewRevision))throw Error('Invalid accepted view binding')
}
export function copyGridRaster(v:BackgroundGridRaster):BackgroundGridRaster {
 assertGridRaster(v)
 return{metricVersion:2,line:{...v.line,drawingBuffer:[...v.line.drawingBuffer],css:[...v.line.css],aliasedRange:[...v.line.aliasedRange],worldToClip:[...v.line.worldToClip]},output:{...v.output,decoded:[...v.output.decoded],css:[...v.output.css],layoutCss:[...v.output.layoutCss]},binding:{...v.binding}}
}
/** Bounded public CSSOM measurements. Reject rotation/shear/perspective,
 * border/padding or unsupported fit; do not guess zero/hidden dimensions. */
function cssBox(element:HTMLElement,fit:'fill'|'contain'):{css:[number,number];layoutCss:[number,number]}|null {
 const rect=element.getBoundingClientRect(),style=getComputedStyle(element)
 if(!positive(rect.width)||!positive(rect.height)||!positive(element.clientWidth)||!positive(element.clientHeight)||style.objectFit!==fit)return null
 for(const key of ['paddingLeft','paddingRight','paddingTop','paddingBottom','borderLeftWidth','borderRightWidth','borderTopWidth','borderBottomWidth']as const){const n=Number.parseFloat(style[key]);if(!finite(n)||n!==0)return null}
 let node:HTMLElement|null=element,count=0
 while(node&&count++<64){const computed=getComputedStyle(node),text=computed.transform
  // CSS transform longhands do not appear in computed.transform. A rotated
  // bounding rectangle is not a diagonal presentation scale. Only measured
  // positive 2D scaling is supported; translate/3D effects remain unobserved.
  const active=(property:string)=>{const value=computed.getPropertyValue(property).trim();return value!==''&&value!=='none'}
  if(active('rotate')||active('perspective')||active('translate')||computed.transformStyle==='preserve-3d')return null
  if(active('scale')){const values=computed.getPropertyValue('scale').trim().split(/\s+/).map(Number);if(values.length>2||!values.every(positive))return null}
  if(text&&text!=='none'){
   const match=/^matrix\(([^)]+)\)$/.exec(text),v=match?.[1]?.split(',').map(Number)
   if(!v||v.length!==6||!v.every(finite)||v[1]!==0||v[2]!==0||!positive(v[0])||!positive(v[3]))return null
  }
  node=node.parentElement
 }
 if(node)return null
 return{css:[rect.width,rect.height],layoutCss:[element.clientWidth,element.clientHeight]}
}
export function createLineRasterProbe(renderer:THREE.WebGLRenderer,linewidth:()=>number,camera:()=>THREE.Camera){
 const id=newRasterOwner();let range:[number,number]|null=null,lost=false,closed=false;const canvas=renderer.domElement
 const onLost=()=>{lost=true;range=null},onRestored=()=>{lost=false;range=null}
 canvas.addEventListener('webglcontextlost',onLost);canvas.addEventListener('webglcontextrestored',onRestored)
 return{read():LineRasterObservation|null{
  if(closed||lost)return null
  try{const gl=renderer.getContext();if(!gl||gl.isContextLost())return null
   if(!range){const raw=gl.getParameter(gl.ALIASED_LINE_WIDTH_RANGE);if(!raw||raw.length!==2||!positive(raw[0])||!positive(raw[1])||raw[0]>raw[1])return null;range=[raw[0],raw[1]]}
   const size=renderer.getDrawingBufferSize(new THREE.Vector2()),ratio=renderer.getPixelRatio(),width=linewidth(),box=cssBox(canvas,'fill')
   if(!Number.isSafeInteger(size.x)||!Number.isSafeInteger(size.y)||size.x<=0||size.y<=0||!positive(ratio)||!positive(width)||!box)return null
   const requested=width*ratio,c=camera();if(!positive(requested)||!c)return null
   c.updateMatrixWorld(true)
   // Three elements (column-major) are the same16 numbers as Gf row-vector
   // world-to-clip. Canonical grid.matrix is already in this scene-world frame.
   const matrix=c.projectionMatrix.clone().multiply(c.matrixWorldInverse).elements
   if(!matrix.every(finite))return null
   return{owner:id,drawingBuffer:[size.x,size.y],css:box.css,pixelRatio:ratio,aliasedRange:[...range],effectiveWidthPx:Math.min(range[1],Math.max(range[0],requested)),coordinateFrame:'scene-world',worldToClip:[...matrix]}
  }catch{return null}
 },dispose(){if(closed)return;closed=true;range=null;canvas.removeEventListener('webglcontextlost',onLost);canvas.removeEventListener('webglcontextrestored',onRestored)}}
}
/** Current owned intrinsic video plus actual CSS contain presentation; size
 * events include host-only resizes. Requested caps/pixelRatio are never used. */
export function observeDecodedRaster(video:HTMLVideoElement,id:string,publish:(event:DecodedRasterEvent)=>void){
 if(!owner(id))throw Error('Invalid media owner')
 let closed=false,revision=0,previous='';const sample=()=>{
  if(closed)return
  const w=video.videoWidth,h=video.videoHeight,box=cssBox(video,'contain')
  if(!integer(w)||!integer(h)||w<=0||h<=0||w>1600||h>900||!box){
   if(previous){previous='';publish({type:'unobserved',owner:id,revision:++revision})}
   return
  }
  const key=JSON.stringify([w,h,box.css,box.layoutCss]);if(key===previous)return
  previous=key;publish({type:'raster',raster:{owner:id,revision:++revision,decoded:[w,h],...box,objectFit:'contain'}})
 }
 publish({type:'owner',owner:id});video.addEventListener('loadedmetadata',sample);video.addEventListener('resize',sample)
 const resize=typeof ResizeObserver==='function'?new ResizeObserver(sample):null;resize?.observe(video)
 window.addEventListener('resize',sample);sample()
 return{sample,dispose(){if(closed)return;closed=true;resize?.disconnect();window.removeEventListener('resize',sample);video.removeEventListener('loadedmetadata',sample);video.removeEventListener('resize',sample);publish({type:'retired',owner:id})}}
}
export class DecodedRasterState {
 private owner:string|null=null;private current:DecodedRasterObservation|null=null;private lastRevision=-1
 accept(event:DecodedRasterEvent):boolean{
  if(event.type==='owner'){if(!owner(event.owner)||this.owner===event.owner)return false;this.owner=event.owner;this.current=null;this.lastRevision=-1;return true}
  if(event.type==='retired'){if(event.owner!==this.owner)return false;this.owner=null;this.current=null;this.lastRevision=-1;return true}
  if(event.type==='unobserved'){if(event.owner!==this.owner||!integer(event.revision)||event.revision<=this.lastRevision)return false;this.lastRevision=event.revision;this.current=null;return true}
  const r=event.raster
  if(r.owner!==this.owner||!integer(r.revision)||!pair(r.decoded,x=>integer(x)&&x>0)||r.decoded[0]>1600||r.decoded[1]>900||!pair(r.css,positive)||!pair(r.layoutCss,positive)||r.objectFit!=='contain'||r.revision<=this.lastRevision)return false
  this.lastRevision=r.revision;this.current={...r,decoded:[...r.decoded],css:[...r.css],layoutCss:[...r.layoutCss]};return true
 }
 read():DecodedRasterObservation|null{return this.current?{...this.current,decoded:[...this.current.decoded],css:[...this.current.css],layoutCss:[...this.current.layoutCss]}:null}
}

/** One in-flight command plus one replaceable pending common view. */
export class LatestViewDelivery<T, R> {
  private pending: T | null = null
  private busy = false
  private closed = false
  private sequence = 0
  constructor(private send: (value: T) => Promise<R>, private reply: (value:T,response:R)=>void,
    private rejected: (error:unknown)=>void) {}
  offer(value:T) { if(this.closed)return; this.pending=value; this.sequence++; void this.drain() }
  close() { this.closed=true;this.pending=null;this.sequence++ }
  private async drain() {
    if(this.closed||this.busy||!this.pending)return
    const value=this.pending, sequence=this.sequence
    this.pending=null;this.busy=true
    try {
      const response=await this.send(value)
      // Only truly obsolete work is ignored. A current identity mismatch is
      // still delivered to the validator and cannot masquerade as stale.
      if(!this.closed&&sequence===this.sequence)this.reply(value,response)
    } catch(error) { if(!this.closed&&sequence===this.sequence)this.rejected(error) }
    finally {this.busy=false;if(!this.closed)void this.drain()}
  }
}
