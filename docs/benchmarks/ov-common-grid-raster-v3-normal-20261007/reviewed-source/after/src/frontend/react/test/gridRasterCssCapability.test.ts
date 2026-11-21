/** Actual installed CSSOM/public producer; geometry/GL are controlled, not pixel proof. */
import * as THREE from 'three'
import {afterEach,expect,it} from 'vitest'
import {createLineRasterProbe,observeDecodedRaster} from '../src/renderer/displayRaster'
afterEach(()=>document.body.replaceChildren())
function carrier(tag:'canvas'|'video'){
 const host=document.createElement('div'),element=document.createElement(tag);host.append(element);document.body.append(host)
 Object.assign(element.style,{objectFit:tag==='canvas'?'fill':'contain',padding:'0px',border:'0px solid'})
 Object.defineProperties(element,{clientWidth:{get:()=>100},clientHeight:{get:()=>100}});element.getBoundingClientRect=()=>({width:141.42,height:141.42}as DOMRect)
 return{host,element}
}
const refused=[{rotate:'45deg'},{rotate:'x 30deg',perspective:'500px'},{perspective:'500px'},{scale:'-1'},{scale:'0'},{scale:'1 1 2'},{translate:'0px 0px 30px'},{translate:'10px'},{transformStyle:'preserve-3d'},{transform:'matrix(1,.2,0,1,0,0)'}]
it.each(refused)('does not claim a diagonal CSS measurement for%j',style=>{
 const {host,element}=carrier('canvas');Object.assign(host.style,style)
 const gl={ALIASED_LINE_WIDTH_RANGE:1,getParameter:()=>new Float32Array([1,1]),isContextLost:()=>false},camera=new THREE.PerspectiveCamera(45,1,.1,100)
 const renderer={domElement:element,getContext:()=>gl,getPixelRatio:()=>1.25,getDrawingBufferSize:(v:THREE.Vector2)=>v.set(125,125)}as unknown as THREE.WebGLRenderer
 const probe=createLineRasterProbe(renderer,()=>1,()=>camera)
 try{expect(probe.read()).toBeNull()}finally{probe.dispose()}
})
it.each([{}, {scale:'1.2 .8'}, {scale:'2'}, {transform:'matrix(1.2,0,0,.8,0,0)'}])('preserves measured positive axis scale%j',style=>{
 const {host,element}=carrier('canvas');Object.assign(host.style,style)
 const gl={ALIASED_LINE_WIDTH_RANGE:1,getParameter:()=>new Float32Array([1,1]),isContextLost:()=>false},camera=new THREE.PerspectiveCamera(45,1,.1,100)
 const renderer={domElement:element,getContext:()=>gl,getPixelRatio:()=>1.25,getDrawingBufferSize:(v:THREE.Vector2)=>v.set(125,125)}as unknown as THREE.WebGLRenderer
 const probe=createLineRasterProbe(renderer,()=>1,()=>camera)
 try{expect(probe.read()?.css).toEqual([141.42,141.42])}finally{probe.dispose()}
})
it('a newly unsupported media ancestor withdraws the last valid observation and later recovery gets a new revision',()=>{
 const {host,element}=carrier('video');const video=element as HTMLVideoElement
 Object.defineProperties(video,{videoWidth:{get:()=>960},videoHeight:{get:()=>540}});const events:any[]=[]
 const observer=observeDecodedRaster(video,'CURRENT',e=>events.push(e));expect(events.at(-1).type).toBe('raster')
 host.style.rotate='45deg';observer.sample();expect(events.at(-1)).toEqual({type:'unobserved',owner:'CURRENT',revision:2})
 observer.sample();expect(events).toHaveLength(3);host.style.rotate='none';observer.sample();expect(events.at(-1).raster.revision).toBe(3)
 observer.dispose();expect(events.at(-1)).toEqual({type:'retired',owner:'CURRENT'})
})
