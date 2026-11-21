import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot, type Root } from 'react-dom/client'
import { flushSync } from 'react-dom'
import type { CfdProject } from '../src/schema/cfdproj'

const actual = JSON.parse(readFileSync(resolve(process.cwd(), 'test/fixtures/air_solve_assessment/actual-not-converged.json'), 'utf8'))
const sha = 'a'.repeat(64)
let session: typeof import('../src/run/session')
let store: typeof import('../src/projectStore')
let paths: typeof import('../src/run/caseDir')
let streams: JobStream[]
let root: Root | null = null
let host: HTMLDivElement | null = null
let backendPath: string
let nextJob: number
let jobStatus: () => Promise<Response>
const settle = async () => { for (let i=0;i<20;i++) await Promise.resolve() }
const response = (body: unknown, status=200) => ({ ok: status===200, status, json: async()=>body, text: async()=>JSON.stringify(body) } as Response)
class JobStream {
  onmessage: ((event:{data:string})=>void)|null=null
  onerror: (()=>void)|null=null
  close=vi.fn()
  constructor(){streams.push(this)}
  finish(value:unknown){this.onmessage?.({data:JSON.stringify(value)})}
}
function assessment(verdict=actual.verdict, mode: string|null='steady') {
  return {format:'air-solve-assessment/v1',caseDir:'/case/A',cfdrunSha256:sha,
    runDir:'/case/A/log/this-job', reportSha256:'b'.repeat(64),mode,verdict,
    reasons:[...actual.residuals.reasons,...actual.sensors.reasons]}
}
async function render(){
  const {SolverPanel}=await import('../src/components/SolverPanel')
  flushSync(()=>root!.render(<SolverPanel project={store.getProject()}/>))
}
beforeEach(async()=>{
  vi.resetModules();streams=[];backendPath='/case/A';nextJob=0
  jobStatus=async()=>response({done:false})
  vi.stubGlobal('EventSource',JobStream)
  vi.stubGlobal('fetch',vi.fn(async(url:string,options?:RequestInit)=>{
    if(url==='/api/set-case-dir'){backendPath=JSON.parse(String(options?.body)).caseDir;return response({ok:true,caseDir:backendPath})}
    if(url==='/api/server-info')return response({dctwinAvailable:true,caseDir:backendPath,caseDirOk:true,allOk:true})
    if(url.startsWith('/api/scan-case-status'))return response({meshReady:true,solveReady:true,postprocessReady:true,logRuns:[],liquidRuns:[]})
    if(url==='/api/run-mesh')return response({jobId:'job-'+ ++nextJob,caseDir:backendPath,cfdrunSha256:sha})
    if(url.startsWith('/api/check-mesh'))return response({meshReady:true})
    if(url.startsWith('/api/job-status'))return jobStatus()
    if(url==='/api/abort-job')return response({status:'aborting'})
    throw new Error('Unexpected '+url)
  }))
  session=await import('../src/run/session');store=await import('../src/projectStore');paths=await import('../src/run/caseDir')
  const submit=await import('../src/run/runSubmit');vi.spyOn(submit,'buildRunRequest').mockResolvedValue({} as never)
  const locale=await import('../src/i18n');locale.setLocale('zh-CN')
  store.loadProject({schemaVersion:4,uid:'verdict-contract',name:'verdict',meta:{casePath:'/case/A'},constructions:{racks:{}}} as CfdProject,null)
  await vi.waitFor(()=>expect(session.isValidated()).toBe(true));await settle()
  host=document.createElement('div');document.body.append(host);root=createRoot(host)
  document.body.insertAdjacentHTML('beforeend','<div id="toolbar"></div><div id="right-panel"></div>')
  await render()
})
afterEach(async()=>{
  if(root)flushSync(()=>root!.unmount());root=null;host?.remove();host=null
  streams.forEach(stream=>stream.finish({done:true,returncode:-15,cancelled:true}));await settle()
  document.getElementById('toolbar')?.remove();document.getElementById('right-panel')?.remove()
  vi.clearAllTimers();vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks()
})
const finish=async(final:unknown)=>{streams.at(-1)!.finish(final);await settle();await render()}
const text=()=>host!.textContent??''

describe('physical solve outcome through real session and SolverPanel',()=>{
  it('shows the actual 2000-iteration NOT_CONVERGED report, thaws, and keeps diagnostic results accessible',async()=>{
    expect(actual.residuals.iterations).toBe(2000);expect(actual.sensors.status).toBe('NO_SENSORS')
    await session.runJob('solve');expect(session.isUiFrozen()).toBe(true)
    await finish({done:true,returncode:0,solveAssessment:assessment()})
    expect(text()).toContain('未收敛');expect(text()).not.toContain('✅ 求解成功')
    expect(host!.querySelector('#solve-job-status')?.getAttribute('data-outcome')).toBe('not-converged')
    expect(text()).toContain(actual.residuals.reasons[0]);expect(session.getJob()?.running).toBe(false)
    expect(session.isUiFrozen()).toBe(false);expect(session.isPostprocessReady()).toBe(true)
    await session.runJob('solve');expect(session.getJob()?.running).toBe(true)
  })
  it.each([
    ['CONVERGED','steady','converged','已收敛'],
    ['TRANSIENT_COMPLETED','transient','transient-completed','瞬态求解已到达'],
    ['TRANSIENT_INCOMPLETE','transient','transient-incomplete','瞬态求解未完成'],
    ['INCONCLUSIVE','steady','unconfirmed','未确认'],
  ])('relays %s using its own physical mode',async(verdict,mode,outcome,label)=>{
    await session.runJob('solve');await finish({done:true,returncode:0,solveAssessment:assessment(verdict,mode)})
    expect(host!.querySelector('#solve-job-status')?.getAttribute('data-outcome')).toBe(outcome)
    expect(text()).toContain(label)
    if(mode==='transient')expect(text()).not.toContain('稳态求解已收敛')
  })
  it.each(['missing','unknown','wrong-mode','wrong-source','wrong-case','invalid-sha'])('cannot label %s report as converged',async(kind)=>{
    let value:any=assessment('CONVERGED')
    if(kind==='missing')value=undefined
    if(kind==='unknown')value.verdict='MAGIC'
    if(kind==='wrong-mode')value.mode='transient'
    if(kind==='wrong-source')value.cfdrunSha256='c'.repeat(64)
    if(kind==='wrong-case')value.caseDir='/case/B'
    if(kind==='invalid-sha')value.reportSha256='invalid'
    await session.runJob('solve');await finish({done:true,returncode:0,solveAssessment:value})
    expect(text()).toContain('未确认');expect(host!.querySelector('#solve-job-status')?.getAttribute('data-outcome')).toBe('unconfirmed')
  })
  it.each([
    [{returncode:2},'failed','求解失败'],
    [{returncode:0,cancelled:true},'aborted','已中断'],
    [{returncode:0,cancelRequested:true},'aborted','已中断'],
    [{returncode:null},'unconfirmed','未确认'],
  ])('prioritizes process/cancellation state %j',async(final,outcome,label)=>{
    await session.runJob('solve');await finish({done:true,...final,solveAssessment:assessment('CONVERGED')})
    expect(host!.querySelector('#solve-job-status')?.getAttribute('data-outcome')).toBe(outcome);expect(text()).toContain(label)
    expect(session.isUiFrozen()).toBe(false)
  })
  it('does not show a different case job as converged after a project switch',async()=>{
    await session.runJob('solve');paths.beginCasePathChange();paths.writeCasePath('/case/B');await settle()
    await finish({done:true,returncode:0,solveAssessment:assessment('CONVERGED')})
    expect((session.getJob() as any)?.solveAssessment??null).toBeNull()
    expect(text()).not.toContain('稳态求解已收敛')
    expect(session.getCurrentCaseJob()).toBeNull()
    expect(host!.querySelector('#solve-job-status')).toBeNull()
  })
  it('uses the same verdict on SSE disconnect and polling completion',async()=>{
    await session.runJob('solve')
    jobStatus=async()=>response({done:true,returncode:0,solveAssessment:assessment()})
    vi.useFakeTimers();streams.at(-1)!.onerror?.();await vi.advanceTimersByTimeAsync(5000);await settle();vi.useRealTimers();await render()
    expect(text()).toContain('未收敛');expect(session.getJob()?.running).toBe(false)
  })
})
