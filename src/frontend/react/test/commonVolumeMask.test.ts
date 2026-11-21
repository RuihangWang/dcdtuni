import {describe,it,expect} from 'vitest'
import {readFileSync} from 'node:fs'
import * as THREE from 'three'
import {decodeVolume,percentileRange,buildVolume} from '../src/postprocess/ppVolume'
const fixture=readFileSync('test/fixtures/common-volume-original-mask/native-controlled-p-collision.volume')
const original=()=>fixture.buffer.slice(fixture.byteOffset,fixture.byteOffset+fixture.byteLength) as ArrayBuffer
const mutateHeader=(fn:(header:any)=>void)=>{const raw=original(),view=new DataView(raw),n=view.getUint32(0,true);const head=JSON.parse(new TextDecoder().decode(new Uint8Array(raw,4,n)).replace(/\0+$/,''));fn(head);const h=new TextEncoder().encode(JSON.stringify(head)),size=Math.ceil(h.length/4)*4;const b=new ArrayBuffer(4+size+raw.byteLength-4-n);new DataView(b).setUint32(0,size,true);new Uint8Array(b,4,h.length).set(h);new Uint8Array(b,4+size).set(new Uint8Array(raw,4+n));return b}
describe('original VTK volume mask in the public Three driver',()=>{
 it('preserves legal sentinel-equal values and the actual disconnected hole independently',()=>{
  const p=decodeVolume(original());expect(p.scalarRange).toEqual([-9999,-9999]);expect(new Set(p.validity)).toEqual(new Set([0,1]));expect(p.scalars.every(v=>v===-9999)).toBe(true)
  expect(p.validity![0]).toBe(1);expect(p.validity![7]).toBe(0);expect(p.validity![15]).toBe(1)
  expect(percentileRange(p.scalars,0.02,0.98,p.sentinel,p.validity)).toEqual([-9999,-9999])
 })
 it('uses the original 0/1 texture, keeps the literal flat domain and opacity, and disposes both carrier textures',()=>{
  const p=decodeVolume(original());const v=buildVolume(p,'jet',[-9999,-9999],.31,16);const m=v.object.material as THREE.ShaderMaterial
  expect(m.uniforms.uValidity!.value.image.data).toBe(p.validity);expect(m.uniforms.uValidity!.value.type).toBe(THREE.UnsignedByteType)
  expect(m.uniforms.uMinValue!.value).toBe(-9999);expect(m.uniforms.uMaxValue!.value).toBe(-9999);expect(m.uniforms.uOpacity!.value).toBe(.31)
  expect(m.fragmentShader).toContain('max(uMaxValue - uMinValue, 1e-6)');expect(m.fragmentShader).toContain('sampleValue.y > 0.5');expect(m.fragmentShader).not.toContain('scalar != uSentinel')
  let scalarDisposed=false,maskDisposed=false;m.uniforms.uVolume!.value.addEventListener('dispose',()=>scalarDisposed=true);m.uniforms.uValidity!.value.addEventListener('dispose',()=>maskDisposed=true);v.dispose();expect([scalarDisposed,maskDisposed]).toEqual([true,true])
 })
 it.each(['mask-value','short','long','count','boolean-count','no-mask','wrong-range','boolean-domain','boolean-origin','boolean-spacing'])('rejects %s without accepting a forged grid',kind=>{
  let b=original()
  if(kind==='mask-value'){const view=new DataView(b);new Uint8Array(b)[4+view.getUint32(0,true)+16**3*4]=2}
  else if(kind==='short')b=b.slice(0,-1)
  else if(kind==='long'){const x=new Uint8Array(b.byteLength+1);x.set(new Uint8Array(b));b=x.buffer}
  else b=mutateHeader(h=>{if(kind==='count')h.validityMask.byteCount--;else if(kind==='boolean-count')h.validityMask.byteCount=true;else if(kind==='no-mask')delete h.validityMask;else if(kind==='wrong-range')h.scalarRange=[0,0];else if(kind==='boolean-domain')h.scalarRange=[false,1];else if(kind==='boolean-origin')h.origin[0]=true;else h.spacing[0]=true})
  expect(()=>decodeVolume(b)).toThrow()
 })
 it.each([[true,1],[0,false],[NaN,1],[0,Infinity],[0,1e300],[2,1]].map(range=>({range})))('refuses invalid shader domain $range',({range})=>{expect(()=>buildVolume(decodeVolume(original()),'jet',range as unknown as [number,number],.2,16)).toThrow()})
})
