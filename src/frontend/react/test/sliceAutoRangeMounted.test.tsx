/** Mounted actual public panel and actual HTTP decoders, with controlled
 * in-memory responses. No live backend, browser, GPU or numeric acceptance. */
import {act} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {PostprocessPanel} from '../src/components/PostprocessPanel'
import * as store from '../src/projectStore'
import type {CfdProject} from '../src/schema/cfdproj'
import {sharedPostprocessDisplay} from '../src/postprocess/sharedDisplay'
import {createThreePostprocessAdapter} from '../src/renderer/threePostprocessAdapter'
import {setPpInternalVisible} from '../src/postprocess/ppViewState'
import {setTempUnit} from '../src/postprocess/ppUnits'
import {setLocale} from '../src/i18n'
import {sliceDisplayRange,decodeSlice} from '../src/postprocess'
let host:HTMLDivElement,root:Root
const caseDir='/controlled/slice-range'
const revision='slice-range-revision'
const response=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json','X-PP-Revision':revision}})
function meshBytes() {
 const bytes=new ArrayBuffer(72)
 new Float32Array(bytes,0,9).set([0,0,0,1,0,0,0,1,0])
 new Uint32Array(bytes,36,3).set([0,1,2]);new Uint32Array(bytes,48,6).set([0,1,1,2,2,0]);return bytes
}
function fieldBytes() {
 const bytes=new ArrayBuffer(28),v=new DataView(bytes)
 v.setFloat32(0,300,true);v.setFloat32(4,302,true);v.setUint32(8,1,true);v.setUint32(12,3,true)
 new Float32Array(bytes,16,3).set([300,301,302]);return bytes
}
function sliceBytes() {
 const header=new TextEncoder().encode(JSON.stringify({nPoints:3,nTriangles:1,scalarRange:[300,302],field:'T'}))
 const n=Math.ceil(header.length/4)*4,bytes=new ArrayBuffer(4+n+36+12+12)
 new DataView(bytes).setUint32(0,n,true);new Uint8Array(bytes,4,header.length).set(header)
 new Float32Array(bytes,4+n,9).set([0,0,0,1,0,0,0,1,0])
 new Uint32Array(bytes,4+n+36,3).set([0,1,2]);new Float32Array(bytes,4+n+48,3).set([300,301,302]);return bytes
}
function controlled(input:unknown,_options?:RequestInit):Promise<Response> {
 const url=String(input)
 if(url.startsWith('/api/scan-case-status?'))return Promise.resolve(response({caseDir,logRuns:[{name:'controlled',path:caseDir+'/log/run',timesteps:[1],runId:'controlled-run'}]}))
 if(url==='/api/pp/init')return Promise.resolve(response({success:true,message:'controlled',timeValue:1,runId:'controlled-run',revision,caseDir}))
 if(url==='/api/pp/render-source')return Promise.resolve(response({error:'Diagnostic controlled test, no physical certificate'},422))
 if(url==='/api/pp/mesh')return Promise.resolve(response({patches:[{name:'wall',type:'wall',nPoints:3,nTriangles:1,nEdges:3,faceStart:0,faceCount:1}],fields:[{name:'T',type:'scalar'}],totalPoints:3,totalTriangles:1,runId:'controlled-run',timeValue:1,revision}))
 const data=url==='/api/pp/mesh/binary'?meshBytes():url.startsWith('/api/pp/field/')?fieldBytes():url==='/api/pp/slice'?sliceBytes():null
 if(data)return Promise.resolve(new Response(data,{headers:{'X-PP-Revision':revision}}))
 throw new Error('Unexpected controlled request '+url)
}
beforeEach(async()=>{
 ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true
 vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue({fillRect:vi.fn()}as unknown as CanvasRenderingContext2D)
 vi.spyOn(globalThis,'fetch').mockImplementation(controlled as any)
 setTempUnit('K');setLocale('en-US');setPpInternalVisible(true)
 sharedPostprocessDisplay.activate({kind:'three',render:()=>({status:'displayed'}),clear(){},dispose(){}})
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
 await act(async()=>store.loadProject({schemaVersion:5,name:'range',uid:'range-project',meta:{casePath:caseDir},constructions:{}} as CfdProject,caseDir+'/project.cfdproj'))
})
afterEach(()=>{
 act(()=>root.unmount());host.remove();sharedPostprocessDisplay.clear();sharedPostprocessDisplay.activate(createThreePostprocessAdapter())
 setTempUnit('C');setLocale('zh-CN');vi.restoreAllMocks()
})
async function mount(){await act(async()=>root.render(<PostprocessPanel/>));await act(async()=>host.querySelector<HTMLButtonElement>('#btn-init-postprocess')!.click());expect(host.querySelector<HTMLElement>('#postprocess-controls')!.style.display).toBe('block')}
function edit(id:string,value:string){
 const input=document.querySelector<HTMLInputElement>('#'+id)!
 act(()=>input.focus())
 act(()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}))})
 act(()=>input.blur())
}
function mode(id:string,value:string){
 const select=document.querySelector<HTMLSelectElement>('#'+id)
 // Before-image has no mode control; preserve the scientific RED at apply.
 if(!select)return
 act(()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,value);select.dispatchEvent(new Event('change',{bubbles:true}))})
}
async function apply(){await act(async()=>host.querySelector<HTMLButtonElement>('#pp-apply-slice')!.click())}
function lastSlice(){return sharedPostprocessDisplay.current()!.layers.slices!.at(-1)!}
function openEdit(){act(()=>host.querySelector('#pp-slice-list .pp-set-label')!.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})))}
it.each(['en-US','zh-CN'] as const)('default %s displays explicit automatic intent, never a fictitious 0 K',async(locale)=>{
 await mount();act(()=>{setLocale(locale);setTempUnit('C')})
 const input=host.querySelector<HTMLInputElement>('#pp-slice-clim-min')!
 expect(input.value).toBe('');expect(input.placeholder).toMatch(locale==='en-US'?/Auto.*data/:/自动.*数据/)
 expect(host.querySelector<HTMLSelectElement>('#pp-slice-range-mode')!.value).toBe('auto')
 await apply();expect(lastSlice().clim).toEqual(sliceDisplayRange(decodeSlice(sliceBytes())))
})
it.each(['three','ov'] as const)('shared %s assembly preserves manual equal 0 K and all native values',async(kind)=>{
 await mount()
 sharedPostprocessDisplay.activate({kind,render:()=>({status:'displayed'}),clear(){},dispose(){}})
 mode('pp-slice-range-mode','manual');edit('pp-slice-clim-min','5');edit('pp-slice-clim-max','5')
 edit('pp-slice-clim-min','0');edit('pp-slice-clim-max','0');await apply()
 expect(lastSlice().clim).toEqual([0,0]);expect([...lastSlice().payload.values!]).toEqual([300,301,302])
 await act(async()=>sharedPostprocessDisplay.whenIdle())
 expect(sharedPostprocessDisplay.status().renderer).toBe(kind)
})
it('keeps a literal very narrow nonflat range without epsilon classification',async()=>{
 await mount();mode('pp-slice-range-mode','manual')
 edit('pp-slice-clim-min','295.149993896');edit('pp-slice-clim-max','295.15');await apply()
 expect(lastSlice().clim).toEqual([295.149993896,295.15])
})
it('reopens a manual equal slice as manual and edit apply preserves equal literals',async()=>{
 await mount();mode('pp-slice-range-mode','manual');edit('pp-slice-clim-min','290');edit('pp-slice-clim-max','310');await apply()
 openEdit();mode('dlg-slice-range-mode','manual');edit('dlg-slice-clim-min','295.15');edit('dlg-slice-clim-max','295.15')
 await act(async()=>document.querySelector<HTMLButtonElement>('#dlg-slice-apply')!.click())
 expect(lastSlice().clim).toEqual([295.15,295.15]);openEdit()
 expect(document.querySelector<HTMLSelectElement>('#dlg-slice-range-mode')!.value).toBe('manual')
})
it('only an explicit editor auto selection returns to the unchanged robust data range',async()=>{
 await mount();mode('pp-slice-range-mode','manual');edit('pp-slice-clim-min','280');edit('pp-slice-clim-max','330');await apply()
 openEdit();mode('dlg-slice-range-mode','auto')
 await act(async()=>document.querySelector<HTMLButtonElement>('#dlg-slice-apply')!.click())
 expect(lastSlice().clim).toEqual(sliceDisplayRange(decodeSlice(sliceBytes())));openEdit()
 expect(document.querySelector<HTMLSelectElement>('#dlg-slice-range-mode')!.value).toBe('auto')
})

it('manual colorbar equal apply survives reopening and applying the slice editor',async()=>{
 await mount();await apply()
 act(()=>document.querySelector('#pp-colorbar-slice-1')!.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})))
 for(const selector of ['.pp-cbw-min','.pp-cbw-max']){
  const input=document.querySelector<HTMLInputElement>('#pp-colorbar-slice-1-dialog '+selector)!
  act(()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,'295.15');input.dispatchEvent(new Event('input',{bubbles:true}))})
 }
 act(()=>document.querySelector<HTMLButtonElement>('#pp-colorbar-slice-1-dialog .pp-cbw-apply')!.click())
 expect(lastSlice().clim).toEqual([295.15,295.15]);openEdit()
 await act(async()=>document.querySelector<HTMLButtonElement>('#dlg-slice-apply')!.click())
 expect(lastSlice().clim).toEqual([295.15,295.15])
 expect([...lastSlice().payload.values!]).toEqual([300,301,302])
})
