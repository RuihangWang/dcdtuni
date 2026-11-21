import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LiquidResultsPanel } from '../src/components/LiquidResultsPanel'
import { fetchLiquidResults, liquidRunState, resetLiquidRun } from '../src/liquid/liquidSession'
import { selectedLiquidRun, setSelectedLiquidRun } from '../src/liquid/liquidPostprocess'
import { workspaceResetSteps } from '../src/workspaceReset'

let host:HTMLDivElement,root:Root
const response=(value:unknown)=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}})
function result(runId:string,duration:number) {
  return {run_id:runId,converged:true,time_s:[0,duration],
    rack_summaries:[{rack_id:'rack',total_flow_kg_s:[1,1],supply_temp_K:[300,300],return_temp_K:[310,310],
      servers:[{server_id:'server',flow_kg_s:[1,1],max_chip_temp_K:[320,320],heat_removed_W:[100,100],cp_edge_ids:[]}]}],cp_details:[]}
}
beforeEach(()=>{
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true
  resetLiquidRun();setSelectedLiquidRun('')
  host=document.createElement('div');document.body.append(host);root=createRoot(host)
})
afterEach(()=>{act(()=>root.unmount());host.remove();resetLiquidRun();setSelectedLiquidRun('');vi.restoreAllMocks()})

it('loads and refreshes the explicitly selected historical run without returning to latest',async()=>{
  const historical=result('old',600),latest=result('latest',20)
  const request=vi.spyOn(globalThis,'fetch').mockImplementation(async input=>response(String(input).includes('run=run-old')?historical:latest))
  setSelectedLiquidRun('run-old')
  await act(async()=>root.render(<LiquidResultsPanel/>))
  expect(request.mock.calls[0]?.[0]).toBe('/api/liquid/results?run=run-old')
  expect(liquidRunState().results).toEqual(historical)
  expect(host.querySelector('#lr-time-samples')?.textContent).toContain('600.0 s')
  await act(async()=>{(host.querySelector('#btn-refresh-liquid-results') as HTMLButtonElement).click()})
  expect(request.mock.calls.at(-1)?.[0]).toBe('/api/liquid/results?run=run-old')
  expect(liquidRunState().results).toEqual(historical)
  expect(selectedLiquidRun()).toBe('run-old')
  expect(host.querySelector('#lr-time-samples')?.textContent).toContain('600.0 s')
})

it('ignores a late latest-result fetch after another run has been selected and loaded',async()=>{
  let release!:(value:Response)=>void
  vi.spyOn(globalThis,'fetch').mockReturnValueOnce(new Promise(resolve=>{release=resolve}))
    .mockResolvedValueOnce(response(result('historical',600)))
  await act(async()=>root.render(<LiquidResultsPanel/>)) // latest remains in flight
  setSelectedLiquidRun('run-historical')
  await act(async()=>{await fetchLiquidResults('run-historical')})
  await act(async()=>{release(response(result('latest',20)))})
  expect(liquidRunState().results).toEqual(result('historical',600))
  expect(host.querySelector('#lr-time-samples')?.textContent).toContain('600.0 s')
})

it('does not install a result from the preceding project after the liquid state is reset',async()=>{
  let release!:(value:Response)=>void
  vi.spyOn(globalThis,'fetch').mockReturnValueOnce(new Promise(resolve=>{release=resolve}))
  const pending=fetchLiquidResults('previous-project-run')
  resetLiquidRun()
  release(response(result('previous-project',600)))
  expect(await pending).toBeNull()
  expect(liquidRunState().results).toBeNull()
})

it('uses the existing workspace reset to clear the previous case selection before the next refresh',async()=>{
  let release!:(value:Response)=>void
  const request=vi.spyOn(globalThis,'fetch').mockReturnValueOnce(new Promise(resolve=>{release=resolve}))
    .mockResolvedValueOnce(response(result('new-case-latest',10)))
  setSelectedLiquidRun('previous-case-run')
  const previous=fetchLiquidResults(selectedLiquidRun())
  workspaceResetSteps({}).find(step=>step.name==='liquid')!.run()
  expect(selectedLiquidRun()).toBe('')
  await act(async()=>root.render(<LiquidResultsPanel/>))
  release(response(result('previous-case',600)))
  await act(async()=>{await previous})
  expect(request.mock.calls.at(-1)?.[0]).toBe('/api/liquid/results')
  expect(liquidRunState().results).toEqual(result('new-case-latest',10))
})

it('keeps an empty selected run as the explicit latest-result choice',async()=>{
  const request=vi.spyOn(globalThis,'fetch').mockResolvedValue(response(result('latest',20)))
  setSelectedLiquidRun('')
  await act(async()=>root.render(<LiquidResultsPanel/>))
  await act(async()=>{(host.querySelector('#btn-refresh-liquid-results') as HTMLButtonElement).click()})
  expect(request.mock.calls.map(([url])=>url)).toEqual(['/api/liquid/results','/api/liquid/results'])
  expect(liquidRunState().results).toEqual(result('latest',20))
})
