/** Controlled hardware observations; no GPU, SDK, numeric or case acceptance. */
import * as THREE from 'three'
import {expect,it,vi} from 'vitest'
import {createLineRasterProbe,observeDecodedRaster,DecodedRasterState,LatestViewDelivery,copyGridRaster} from '../src/renderer/displayRaster'
import {buildSceneSnapshot} from '../src/renderer/sceneSnapshot'
import {validateSceneSnapshot} from '../src/renderer/validateScene'
function renderer(ratio=2,cap:[number,number]=[1,1]){
 const canvas=document.createElement('canvas');canvas.width=1600;canvas.height=900;canvas.style.objectFit='fill';canvas.style.padding='0px';canvas.style.border='0px solid';Object.defineProperties(canvas,{clientWidth:{get:()=>800},clientHeight:{get:()=>450}});canvas.getBoundingClientRect=()=>({width:800,height:450,left:0,top:0,right:800,bottom:450,x:0,y:0,toJSON(){}})
 let range=cap,lost=false;const query=vi.fn(()=>range.some(x=>typeof x!=='number')?range:new Float32Array(range))
 const gl={ALIASED_LINE_WIDTH_RANGE:1,getParameter:query,isContextLost:()=>lost}
 const r={domElement:canvas,getContext:()=>gl,getPixelRatio:()=>ratio,getDrawingBufferSize:(v:THREE.Vector2)=>v.set(canvas.width,canvas.height)}as unknown as THREE.WebGLRenderer
 return{r,canvas,query,setRange:(v:[number,number])=>{range=v},lose:()=>{lost=true;canvas.dispatchEvent(new Event('webglcontextlost'))},restore:()=>{lost=false;canvas.dispatchEvent(new Event('webglcontextrestored'))}}
}
function valid(){return{metricVersion:2 as const,line:{owner:'CONTROLLED_THREE',drawingBuffer:[1600,900]as[number,number],css:[800,450]as[number,number],pixelRatio:2,aliasedRange:[1,1]as[number,number],effectiveWidthPx:1,coordinateFrame:'scene-world' as const,worldToClip:new THREE.Matrix4().elements},output:{owner:'CONTROLLED_MEDIA',revision:1,decoded:[960,540]as[number,number],css:[1992,796]as[number,number],layoutCss:[1992,796]as[number,number],objectFit:'contain' as const},binding:{projectUid:'CONTROLLED_PROJECT',projectContentSha256:'a'.repeat(64),sceneRevision:3,viewRevision:4}}}
it.each([[1,[1,1],1],[1.5,[1,1],1],[2,[1,1],1],[2,[1,4],2]]as const)('uses measured DPR/cap for%j and never CSS/decode/request dimensions',(ratio,range,expected)=>{
 const f=renderer(ratio,[...range]),probe=createLineRasterProbe(f.r,()=>1,()=>new THREE.PerspectiveCamera(45,800/450,.1,400))
 try{expect(probe.read()).toMatchObject({drawingBuffer:[1600,900],pixelRatio:ratio,aliasedRange:range,effectiveWidthPx:expected});probe.read();expect(f.query).toHaveBeenCalledTimes(1)}finally{probe.dispose()}
})
it('retires lost/disposed GL and refreshes the actual cap on restoration rather than reusing stale evidence',()=>{
 const f=renderer(),probe=createLineRasterProbe(f.r,()=>1,()=>new THREE.PerspectiveCamera(45,800/450,.1,400));expect(probe.read()?.effectiveWidthPx).toBe(1);f.lose();expect(probe.read()).toBeNull();f.setRange([1,4]);f.restore();expect(probe.read()?.effectiveWidthPx).toBe(2);expect(f.query).toHaveBeenCalledTimes(2);probe.dispose();probe.dispose();expect(probe.read()).toBeNull()
})
it('zero/unavailable/non-finite/bool hardware capabilities cannot become pixel evidence',()=>{
 for(const cap of[[0,1],[2,1],[1,NaN],[true,1]]as any[]){const f=renderer(2,cap),probe=createLineRasterProbe(f.r,()=>1,()=>new THREE.PerspectiveCamera(45,800/450,.1,400));expect(probe.read()).toBeNull();probe.dispose()}
 const f=renderer(),probe=createLineRasterProbe(f.r,()=>1,()=>new THREE.PerspectiveCamera(45,800/450,.1,400));f.canvas.width=0;expect(probe.read()).toBeNull();probe.dispose()
})
it('intrinsic adaptive decoder metadata is monotonic per owner and ignores empty, duplicate and detached video',()=>{
 const video=document.createElement('video');video.style.objectFit='contain';video.style.padding='0px';video.style.border='0px solid';Object.defineProperties(video,{clientWidth:{get:()=>1992},clientHeight:{get:()=>796}});video.getBoundingClientRect=()=>({width:1992,height:796}as DOMRect);let w=0,h=0;Object.defineProperties(video,{videoWidth:{get:()=>w},videoHeight:{get:()=>h}})
 const events:any[]=[],watch=observeDecodedRaster(video,'CONTROLLED_MEDIA',e=>events.push(e));expect(events).toEqual([{type:'owner',owner:'CONTROLLED_MEDIA'}])
 w=1600;h=638;video.dispatchEvent(new Event('loadedmetadata'));w=960;h=382;video.dispatchEvent(new Event('resize'));video.dispatchEvent(new Event('resize'));h=0;video.dispatchEvent(new Event('resize'))
 expect(events.filter(e=>e.type==='raster').map(e=>e.raster.decoded)).toEqual([[1600,638],[960,382]]);expect(events.at(-1)).toEqual({type:'unobserved',owner:'CONTROLLED_MEDIA',revision:3});watch.dispose();watch.dispose();w=1600;h=590;video.dispatchEvent(new Event('resize'));expect(events.at(-1)).toEqual({type:'retired',owner:'CONTROLLED_MEDIA'});expect(events).toHaveLength(5)
})
it('old-owner retirement, late old size and backwards revisions cannot overwrite a new media owner',()=>{
 const s=new DecodedRasterState();s.accept({type:'owner',owner:'OLD'});s.accept({type:'raster',raster:{owner:'OLD',revision:5,decoded:[1600,638],css:[1992,796],layoutCss:[1992,796],objectFit:'contain'}});s.accept({type:'owner',owner:'NEW'});s.accept({type:'raster',raster:{owner:'NEW',revision:1,decoded:[960,382],css:[1992,796],layoutCss:[1992,796],objectFit:'contain'}})
 expect(s.accept({type:'retired',owner:'OLD'})).toBe(false);expect(s.accept({type:'raster',raster:{owner:'OLD',revision:6,decoded:[1600,590],css:[1992,796],layoutCss:[1992,796],objectFit:'contain'}})).toBe(false);expect(s.accept({type:'raster',raster:{owner:'NEW',revision:1,decoded:[1600,590],css:[1992,796],layoutCss:[1992,796],objectFit:'contain'}})).toBe(false);expect(s.read()?.decoded).toEqual([960,382])
})
it('common real snapshot round-trips observations without mutating canonical/science inputs; restored metadata is never activated',()=>{
 const observed=valid(),options={root:new THREE.Group(),project:{uid:'CONTROLLED_PROJECT'},backgroundGrid:{showGrid:true,gridSpacingMm:600}},plain=buildSceneSnapshot(options),packet=buildSceneSnapshot({...options,backgroundGridRaster:observed})
 expect(packet.view.backgroundGrid).toEqual(plain.view.backgroundGrid);expect(packet.primitives).toEqual(plain.primitives);expect(packet.view.backgroundGridRaster).toEqual(observed);expect(()=>validateSceneSnapshot(JSON.parse(JSON.stringify(packet)))).not.toThrow()
 const state=new DecodedRasterState();expect(state.read()).toBeNull();observed.output.decoded[0]=800;expect(packet.view.backgroundGridRaster!.output.decoded[0]).toBe(960)
})
it('the common observation schema rejects malformed/bool/unknown/oversize inputs without relaxation',()=>{
 for(const mutate of[(v:any)=>v.output.decoded[1]=901,(v:any)=>v.output.revision=true,(v:any)=>v.binding.sceneRevision=-1,(v:any)=>v.line.effectiveWidthPx=NaN,(v:any)=>v.binding.projectContentSha256='wrong',(v:any)=>v.line.aliasedRange=[9,9],(v:any)=>v.line.unexpected=true,(v:any)=>v.output.unexpected=true,(v:any)=>v.binding.unexpected=true,(v:any)=>v.unexpected=true]){
  const v=valid();mutate(v);expect(()=>copyGridRaster(v)).toThrow()
 }
 const packet=buildSceneSnapshot({root:new THREE.Group(),project:{uid:'CONTROLLED_PROJECT'},backgroundGridRaster:valid()});(packet.view.backgroundGridRaster as any).unexpected=true;expect(()=>validateSceneSnapshot(packet)).toThrow()
})
it('obsolete in-flight replies/rejections do not affect a newer view, while a current rejection remains visible',async()=>{
 const tasks:any[]=[],sent:number[]=[],seen:number[]=[],errors:any[]=[];const q=new LatestViewDelivery<{v:number},number>(x=>{sent.push(x.v);return new Promise((resolve,reject)=>tasks.push({resolve,reject}))},(_x,reply)=>seen.push(reply),e=>errors.push(e))
 q.offer({v:1});q.offer({v:2});q.offer({v:3});tasks[0].reject(Error('obsolete identity mismatch'));await Promise.resolve();await Promise.resolve();expect(sent).toEqual([1,3]);expect(errors).toEqual([]);tasks[1].reject(Error('CURRENT signature mismatch'));await Promise.resolve();await Promise.resolve();expect(errors).toHaveLength(1);expect(String(errors[0])).toContain('CURRENT');expect(seen).toEqual([]);q.offer({v:4});q.close();tasks[2].resolve(4);await Promise.resolve();expect(seen).toEqual([])
})
