/** Actual native511 CPP response buffers -> public producer/intent/material/colorbar.
 * Uniform fields/receipts are CONTROLLED contracts, never a physical solve. */
import {readFileSync,writeFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {act} from 'react'
import {createRoot} from 'react-dom/client'
import {it,expect,vi} from 'vitest'
import {ColorbarOverlay} from '../src/components/ColorbarOverlay'
import {colormapData,createColormapTexture,type ColorMap} from '../src/postprocess/ppColormaps'
import {setTempUnit} from '../src/postprocess/ppUnits'
import {decodeVolume,buildVolume} from '../src/postprocess/ppVolume'
import {createFieldMaterial,createScalarFlatMaterial} from '../src/postprocess/ppMaterials'
import {fieldRange,buildSurface,buildSlice,buildStreamlines} from '../src/postprocess/ppRenderer'
import {commonRenderIntent} from '../src/postprocess/commonRenderIntent'
import {bindNumericProduct} from '../src/postprocess/numericProducts'
import {decodeMesh,decodeField,decodeSlice,decodeStreamlines,sliceDisplayRange,type InitResult} from '../src/postprocess'
import {publishWindResultFrame,type WindResultFrame} from '../src/renderer/windResultProducer'
import {assertResultLayerCompatibility} from '../src/renderer/resultLayers'
const snapshot=JSON.parse(readFileSync(resolve(__dirname,'fixtures/constant_common_lut/input.json'),'utf8'))
const buffer=(value:string)=>Uint8Array.from(Buffer.from(value,'base64')).buffer
function frame():WindResultFrame{
 const init=snapshot.initResponse as InitResult
 const mesh=decodeMesh(buffer(snapshot.mesh.base64),snapshot.mesh.meta),field=decodeField(buffer(snapshot.field.base64),snapshot.field.meta),slice=decodeSlice(buffer(snapshot.slice.base64)),stream=decodeStreamlines(buffer(snapshot.streamline.base64))
 for(const [object,kind]of [[mesh,'mesh'],[field.values,'field'],[slice,'slice'],[stream,'streamline']]as const)bindNumericProduct(object,new Response(null,{headers:{'X-PP-Product':JSON.stringify(snapshot[kind].receipt)}}),kind,init)
 return {descriptor:snapshot.descriptor,session:init,currentProject:{uid:snapshot.descriptor.source.projectUid,contentSha256:snapshot.descriptor.source.projectContentSha256},mesh,activeSurfaceField:'T',surface:{field:'T',values:field.values,colorMap:'jet',range:fieldRange(field.values),opacity:.7,hidden:new Set(),visible:true},slices:[{id:3,payload:slice,normal:[0,0,1],origin:[1,1,1],visible:true,colorMap:'jet',range:sliceDisplayRange(slice),opacity:.7}],streamlines:[{id:7,set:stream,tubeRadius:.01,visible:true,colorMap:'jet',range:stream.scalarRange,opacity:.7}]}
}
it('publishes the real constant field auto-range across surface, cut and nonempty trace without widening source or LUT',()=>{
 const input=frame();expect(input.surface.range).toEqual([300,300]);expect(input.slices[0]!.payload.nTriangles).toBeGreaterThan(0);expect(input.streamlines[0]!.set.lines.length).toBeGreaterThan(0)
 // Emit before publisher validation for portable actual backend RED as well.
 const intent=commonRenderIntent(input,1);if(process.env.DCDTUNI_CONSTANT_INTENT_OUTPUT)writeFileSync(process.env.DCDTUNI_CONSTANT_INTENT_OUTPUT,JSON.stringify(intent))
 const packet=publishWindResultFrame(input);expect(input.slices[0]!.range).toEqual([300,300])
 expect(new Set(packet.layers.map(l=>l.kind))).toEqual(new Set(['surfaceField','slice','streamline']))
 for(const layer of packet.layers)if('lut'in layer)expect(layer.lut.range).toEqual([300,300])
 expect(packet.source).toEqual(snapshot.descriptor.source);expect(packet.runConvergence!.verdict).toBe('NOT_CONVERGED')
 expect(()=>assertResultLayerCompatibility(packet,{...packet.source,cfdrunSha256:'0'.repeat(64)},packet.timeSeconds)).toThrow()
 expect(()=>assertResultLayerCompatibility(packet,packet.source,.01)).toThrow()
 for(const update of [{runId:'other'},{timeValue:.01},{revision:'old'}])expect(()=>publishWindResultFrame({...input,session:{...input.session,...update}})).toThrow()
})
it('keeps the actual public slice auto-scale flat without altering native samples',()=>{const input=frame();expect(input.slices[0]!.range).toEqual([300,300]);expect(input.slices[0]!.payload.scalarRange).toEqual([300,300])})
it('rejects nonfinite, boolean and inverted domains and keeps actual Three carrier scalar/uniform values',()=>{
 const input=frame();for(const range of [[301,300],[NaN,NaN],[true,true]]as any)expect(()=>publishWindResultFrame({...input,surface:{...input.surface,range}})).toThrow()
 const surface=buildSurface(input.mesh,{values:input.surface.values,colorMap:'jet',climMin:300,climMax:300,hidden:input.surface.hidden}),slice=buildSlice(input.slices[0]!.payload,'jet',[300,300]),stream=buildStreamlines(input.streamlines[0]!.set,'jet',[300,300],.01)
 for(const built of [surface,slice,stream]){built.object.traverse(child=>{if('material'in child){const mat=(child as any).material;expect(mat.uniforms.uMinValue.value).toBe(300);expect(mat.uniforms.uMaxValue.value).toBe(300);const values=(child as any).geometry.getAttribute('fieldValue');expect([...values.array].every(v=>v===300)).toBe(true)}});built.dispose()}

})
it.each(['jet','coolwarm','viridis']as ColorMap[])('renders a constant %s colorbar from the same endpoint table; auto-scale applies the exact raw flat range',palette=>{
 ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;setTempUnit('K');const styles:string[]=[];const context={fillStyle:'',fillRect(){styles.push(this.fillStyle)}};const spy=vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host),onApply=vi.fn();const props={id:'constant-bar',label:'T',field:'T',colorMap:palette,climMin:300,climMax:300,onApply,onAutoScale:()=>({min:300,max:300})}
 try{
  act(()=>root.render(<ColorbarOverlay {...props}/>));const rgb=colormapData(palette);expect(new Set(styles)).toEqual(new Set([`rgb(${rgb[0]},${rgb[1]},${rgb[2]})`]));expect(document.querySelector('#constant-bar')!.textContent).toContain('300')
  act(()=>document.querySelector('#constant-bar')!.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})));act(()=>document.querySelector<HTMLButtonElement>('#constant-bar-dialog .pp-cbw-auto')!.click());act(()=>document.querySelector<HTMLButtonElement>('#constant-bar-dialog .pp-cbw-apply')!.click());expect(onApply).toHaveBeenLastCalledWith(300,300,palette)
  styles.length=0;act(()=>root.render(<ColorbarOverlay {...props} climMax={301}/>));expect(new Set(styles).size).toBeGreaterThan(100)
  styles.length=0;act(()=>root.render(<ColorbarOverlay {...props}/>));expect(new Set(styles)).toEqual(new Set([`rgb(${rgb[0]},${rgb[1]},${rgb[2]})`]))
 }finally{act(()=>root.unmount());host.remove();spy.mockRestore()}
})

it('retains a collapsed percentile LUT without claiming that every original sample is constant',()=>{
 const values=new Float32Array([...Array.from({length:99},()=>300),350]);const input=frame();const cut={...input.slices[0]!.payload,values};expect(sliceDisplayRange(cut)).toEqual([300,300]);expect(values[99]).toBe(350)
})
it('provides the actual material fragment sources and common table for optional CPU-only GLSL execution',()=>{
 if(!process.env.DCDTUNI_CONSTANT_SHADER_OUTPUT)return
 const palettes=['jet','coolwarm','viridis']as ColorMap[];const texture=createColormapTexture('jet'),lit=createFieldMaterial(texture,300,300),flat=createScalarFlatMaterial(texture,300,300,1)
 writeFileSync(process.env.DCDTUNI_CONSTANT_SHADER_OUTPUT,JSON.stringify({lit:lit.fragmentShader,flat:flat.fragmentShader,palettes:Object.fromEntries(palettes.map(p=>[p,[...colormapData(p)]])),rangeCases:[[300,300],[299,301],[300,300+1e-8]].map(domain=>{const m=createFieldMaterial(texture,domain[0]!,domain[1]!);const flat=m.uniforms.uFlatRange?.value??false;m.dispose();return {domain,flat}})}));lit.dispose();flat.dispose();texture.dispose()
})

it('preserves original volume occupancy and distinguishes allowed outside NaN from invalid physical samples',()=>{
 const original=readFileSync(resolve(__dirname,'fixtures/common-volume-original-mask/native-controlled-p-collision.volume'));const buffer=original.buffer.slice(original.byteOffset,original.byteOffset+original.byteLength)as ArrayBuffer;const header=new DataView(buffer).getUint32(0,true);const scalars=new Float32Array(buffer,4+header,16**3);scalars[7]=NaN
 const decoded=decodeVolume(buffer);expect(decoded.validity![7]).toBe(0);expect(Number.isNaN(decoded.scalars[7])).toBe(true);expect(decoded.scalarRange).toEqual([-9999,-9999]);const built=buildVolume(decoded,'jet',[-9999,-9999],.31,16);const material=built.object.material as any;expect(material.uniforms.uValidity.value.image.data).toBe(decoded.validity);expect(material.uniforms.uMinValue.value).toBe(-9999);expect(material.uniforms.uMaxValue.value).toBe(-9999);built.dispose()
 scalars[0]=NaN;expect(()=>decodeVolume(buffer)).toThrow()
})

it('derives collapsed display flags from literal raw equality and follows existing min/max updates before float rounding',()=>{
 const tex=createColormapTexture('jet'),mat=createFieldMaterial(tex,300,300+1e-8);expect(Math.fround(300)).toBe(Math.fround(300+1e-8));expect(mat.uniforms.uFlatRange!.value).toBe(false);mat.uniforms.uMaxValue!.value=300;expect(mat.uniforms.uFlatRange!.value).toBe(true);mat.uniforms.uMinValue!.value=299;expect(mat.uniforms.uFlatRange!.value).toBe(false);mat.dispose();tex.dispose()
})
