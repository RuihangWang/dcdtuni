/** Actual mounted public panel and actual HTTP decoders. Controlled responses
 * intentionally ignore AbortSignal to exercise late completion; no browser/CFD acceptance. */
import {act} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {beforeEach,afterEach,it,expect,vi} from 'vitest'
import {PostprocessPanel} from '../src/components/PostprocessPanel'
import * as store from '../src/projectStore'
import type {CfdProject} from '../src/schema/cfdproj'
import {initRun} from '../src/postprocess'
import {sharedPostprocessDisplay} from '../src/postprocess/sharedDisplay'
import {setPpInternalVisible} from '../src/postprocess/ppViewState'
let host:HTMLDivElement,root:Root
const A='/controlled/A',B='/controlled/B'
const json=(value:unknown,status=200,revision?:string)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json',...(revision?{'X-PP-Revision':revision}:{})}})
const flush=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,0))})
const deferred=<T,>()=>{let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>resolve=done);return {promise,resolve}}
const project=(uid:string,path:string):CfdProject=>({schemaVersion:5,name:uid,uid,meta:{casePath:path},constructions:{}})
const load=async(uid:string,path:string)=>act(async()=>store.loadProject(project(uid,path),path+'/project.cfdproj'))
const list=(dir:string)=>({caseDir:dir,logRuns:[{name:dir===A?'run-A':'run-B',path:dir+'/log/run',timesteps:[1],runId:dir===A?'run-A':'run-B',transient:false}]})
const result=(dir:string)=>({success:true,message:dir===A?'Opened A':'Opened B',timeValue:1,runId:dir===A?'run-A':'run-B',revision:dir===A?'revision-A':'revision-B',caseDir:dir})
function mesh(revision:string){return {patches:[{name:'box',type:'wall',nPoints:3,nTriangles:1,nEdges:3,faceStart:0,faceCount:1}],fields:[{name:'T',type:'scalar'}],totalPoints:3,totalTriangles:1,runId:revision==='revision-A'?'run-A':'run-B',timeValue:1,revision}}
function binary(revision:string,kind:string){
 if(kind==='field'){const out=new ArrayBuffer(28),v=new DataView(out);v.setFloat32(0,301,true);v.setFloat32(4,303,true);v.setUint32(8,1,true);v.setUint32(12,3,true);new Float32Array(out,16).set([301,302,303]);return new Response(out,{headers:{'X-PP-Revision':revision}})}
 const out=new ArrayBuffer(72),x=revision==='revision-A'?1:9;new Float32Array(out,0,9).set([x,0,0,x,1,0,x,0,1]);new Uint32Array(out,36,3).set([0,1,2]);new Uint32Array(out,48,6).set([0,1,1,2,2,0]);return new Response(out,{headers:{'X-PP-Revision':revision}})
}
function standard(input:unknown,options?:RequestInit):Promise<Response>{
 const url=String(input),revision=new Headers(options?.headers).get('X-PP-Revision')??''
 if(url==='/api/scan-case-status')return Promise.resolve(json(list(B))) // real old process-default route, another tab selected B.
 if(url.startsWith('/api/scan-case-status?'))return Promise.resolve(json(list(new URL(url,'http://controlled').searchParams.get('caseDir')!)))
 if(url==='/api/pp/init')return Promise.resolve(json(result(JSON.parse(String(options?.body)).caseDir)))
 if(url==='/api/pp/render-source')return Promise.resolve(json({error:'Controlled diagnostic presentation has no physical certificate'},422))
 if(url==='/api/pp/mesh')return Promise.resolve(json(mesh(revision),200,revision))
 if(url==='/api/pp/mesh/binary')return Promise.resolve(binary(revision,'mesh'))
 if(url.startsWith('/api/pp/field/'))return Promise.resolve(binary(revision,'field'))
 throw new Error('Unexpected controlled request '+url)
}
const runs=()=>[...host.querySelectorAll<HTMLOptionElement>('#pp-log-run option')].map(o=>o.value).filter(Boolean)
const enable=async()=>act(async()=>host.querySelector<HTMLButtonElement>('#btn-init-postprocess')!.click())
beforeEach(async()=>{
 ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true
 vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue({fillRect:vi.fn()}as unknown as CanvasRenderingContext2D)
 host=document.createElement('div');document.body.append(host);root=createRoot(host)
 vi.spyOn(globalThis,'fetch').mockImplementation(standard as any);setPpInternalVisible(true);await load('project-A',A)
})
afterEach(()=>{act(()=>root.unmount());host.remove();sharedPostprocessDisplay.clear();vi.restoreAllMocks()})
const mount=async()=>{await act(async()=>root.render(<PostprocessPanel/>));await flush()}
it('scans this saved case on mount; UID or directory changes clear the previous run immediately',async()=>{
 await mount();expect(runs()).toEqual([A+'/log/run'])
 const pending=deferred<Response>();vi.mocked(fetch).mockImplementation((input,options)=>String(input).includes('caseDir='+encodeURIComponent(B))?pending.promise:standard(input,options))
 await load('project-A',B);expect(runs()).toEqual([]);expect(host.querySelector<HTMLButtonElement>('#btn-init-postprocess')!.disabled).toBe(true)
 await act(async()=>pending.resolve(json(list(B))));expect(runs()).toEqual([B+'/log/run'])
 const uidPending=deferred<Response>();vi.mocked(fetch).mockImplementation(()=>uidPending.promise)
 await load('different-project',B);expect(runs()).toEqual([])
 await act(async()=>uidPending.resolve(json(list(B))));expect(runs()).toEqual([B+'/log/run'])
 expect(vi.mocked(fetch).mock.calls.every(([url])=>String(url).startsWith('/api/scan-case-status?caseDir='))).toBe(true)
})
it('an older A scan arriving after the B scan cannot replace the run selection',async()=>{
 const pending=deferred<Response>();vi.mocked(fetch).mockImplementation((input,options)=>String(input).includes('caseDir='+encodeURIComponent(A))?pending.promise:standard(input,options))
 await mount();await load('project-B',B);await flush();expect(runs()).toEqual([B+'/log/run'])
 await act(async()=>pending.resolve(json(list(A))));expect(runs()).toEqual([B+'/log/run'])
})
it('refuses another-case scan response and does not fall back to a global directory when path is absent',async()=>{
 vi.mocked(fetch).mockResolvedValue(json(list(B)));await mount();expect(runs()).toEqual([])
 expect(host.textContent).toContain('another case directory')
 vi.mocked(fetch).mockClear();await load('unsaved','');await flush();expect(runs()).toEqual([]);expect(fetch).not.toHaveBeenCalled()
})
it('explicit init carries its case and refuses a response signed for another directory',async()=>{
 vi.mocked(fetch).mockResolvedValue(json(result(A)));await expect(initRun(A+'/log/run',1,undefined,A)).resolves.toMatchObject({caseDir:A})
 expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0]![1]!.body))).toMatchObject({caseDir:A})
 vi.mocked(fetch).mockResolvedValue(json(result(B)));await expect(initRun(A+'/log/run',1,undefined,A)).rejects.toThrow(/another case directory/)
})
it('late old init cannot restore A or begin mesh requests after B has completed',async()=>{
 const pending=deferred<Response>();vi.mocked(fetch).mockImplementation((input,options)=>String(input)==='/api/pp/init'&&JSON.parse(String(options?.body)).caseDir===A?pending.promise:standard(input,options))
 await mount();await enable();await load('project-B',B);await flush();await enable();await flush()
 expect(host.querySelector('#postprocess-init-status')!.textContent).toContain('Opened B')
 const count=vi.mocked(fetch).mock.calls.filter(([url])=>String(url)==='/api/pp/mesh').length
 await act(async()=>pending.resolve(json(result(A))));await flush()
 expect(host.querySelector('#postprocess-init-status')!.textContent).toContain('Opened B')
 expect(vi.mocked(fetch).mock.calls.filter(([url])=>String(url)==='/api/pp/mesh')).toHaveLength(count)
})
it('late A mesh decoding cannot mutate the already loaded B carrier even when fetch ignores abort',async()=>{
 const pending=deferred<Response>();vi.mocked(fetch).mockImplementation((input,options)=>String(input)==='/api/pp/mesh/binary'&&new Headers(options?.headers).get('X-PP-Revision')==='revision-A'?pending.promise:standard(input,options))
 const present=vi.spyOn(sharedPostprocessDisplay,'present')
 await mount();await enable();await vi.waitFor(()=>expect(vi.mocked(fetch).mock.calls.some(([url,options])=>String(url)==='/api/pp/mesh/binary'&&new Headers(options?.headers).get('X-PP-Revision')==='revision-A')).toBe(true));await load('project-B',B);await flush();await enable();await flush()
 expect(host.querySelector('#postprocess-init-status')!.textContent).toContain('Opened B')
 await act(async()=>pending.resolve(binary('revision-A','mesh')));await flush()
 const palette=host.querySelector<HTMLSelectElement>('#pp-cmap-preset')!;act(()=>{palette.value='viridis';palette.dispatchEvent(new Event('change',{bubbles:true}))});await flush()
 const frames=present.mock.calls.map(([frame])=>frame).filter(frame=>frame.layers.mesh)
 const last=frames.at(-1)!;expect(last.layers.mesh!.positions[0]).toBe(9)
 expect(last.windFrame).toBeNull() // ordinary diagnostics remain Three-readable, no invented certificate.
})
it('a tab switch hides the opened result and shows it again: nothing is torn down or reloaded',async()=>{
 const setShown=vi.fn();(window as any).__ppViewport={...(window as any).__ppViewport,setShown}
 const clear=vi.spyOn(sharedPostprocessDisplay,'clear')
 await mount();await enable();await flush()
 const initCalls=()=>vi.mocked(fetch).mock.calls.filter(([url])=>String(url)==='/api/pp/init').length
 const opened=initCalls();expect(opened).toBe(1)
 const status=host.textContent
 clear.mockClear()
 await act(async()=>root.render(<PostprocessPanel active={false}/>));await flush()
 expect(setShown).toHaveBeenLastCalledWith(false)
 expect(clear).not.toHaveBeenCalled()
 expect(document.getElementById('pp-colorbar-surface')).toBeNull()
 await act(async()=>root.render(<PostprocessPanel active/>));await flush()
 expect(setShown).toHaveBeenLastCalledWith(true)
 expect(clear).not.toHaveBeenCalled()
 expect(initCalls()).toBe(opened)
 expect(host.textContent).toBe(status)
 delete (window as any).__ppViewport.setShown
})
it('刷新 takes the selected run\'s time directories as they are now, and says so',async()=>{
 await mount();expect(runs()).toEqual([A+'/log/run'])
 const steps=()=>[...host.querySelectorAll<HTMLOptionElement>('#pp-timestep option')].map(o=>o.value).filter(Boolean)
 const before=steps()
 vi.mocked(fetch).mockImplementation((input,options)=>String(input).startsWith('/api/scan-case-status?')
  ?Promise.resolve(json({caseDir:A,logRuns:[{name:'run-A',path:A+'/log/run',timesteps:[1,2],runId:'run-A',transient:false}]}))
  :standard(input,options))
 const logged=vi.spyOn(store,'logAction')
 await act(async()=>host.querySelector<HTMLButtonElement>('#pp-refresh-runs')!.click());await flush()
 expect(before).not.toContain('2')
 expect(steps()).toContain('2')
 expect(logged.mock.calls.some(([action])=>action==='ppRefreshRuns')).toBe(true)
})
