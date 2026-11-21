import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {createModel, createPipeSegment} from '../src/legacy/factories'
import {stateV4} from '../src/legacy/cfdprojModules'
import {installLiquidGlobals, _resetLiquidUidCounter} from '../src/legacy/liquidGlobals'
import {pipeSummary} from '../src/liquid/pipeInspector'
import * as store from '../src/projectStore'
import type {CfdProject} from '../src/schema/cfdproj'

// A pipe drawn in connect mode (the liquid toolbar's 连管模式) and a pipe made
// by 新建管道 must start at the same bore: the connect path used to inherit the
// 25 mm split seed while the toolbar pipe and the CDU ports are 100 mm.

type Bag = Record<string, any>
const w = window as unknown as Bag
function live() {return store.getProject()!}
beforeEach(async()=>{
  _resetLiquidUidCounter()
  const p=stateV4().getInitialStateV4() as unknown as CfdProject;p.uid='connect-mode'
  store.loadProject(p,'private-connect.cfdproj');installLiquidGlobals()
  for(const key of ['createPipeSegmentModel','createPipeJunctionModel','addModel'])vi.stubGlobal(key,w[key])
  vi.stubGlobal('t',(key:string)=>key);vi.stubGlobal('alert',vi.fn());vi.stubGlobal('rebuildGeometry',vi.fn())
  vi.stubGlobal('pushUndoCheckpoint',store.pushUndoCheckpoint);vi.stubGlobal('allModels',()=>stateV4().allModels(live()))
  await import('../../shared/js/liquid_mode.js')
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})

function addCdu(ports?:{id:string;diameterM:number}[]) {
  const made=createModel(live(),'cdu'),cdu=made.model as Bag
  if(ports)cdu.cooling.ports=ports.map((p,i)=>({id:p.id,position:{x:.15+.3*i,y:.15,z:1.2},direction:{x:0,y:0,z:1},diameterM:p.diameterM}))
  store.addModel(made.bucket,made.model);return cdu.uid as string
}
function connect(from:{modelId:string;portId:string},to:{modelId:string;portId:string}) {
  const before=new Set(Object.keys(live().constructions?.pipeSegments??{}))
  w._createPipeBetweenPorts({...from,position:{x:0,y:.2,z:0},type:'out'},{...to,position:{x:1,y:.2,z:0},type:'in'})
  const made=Object.values(live().constructions!.pipeSegments!).filter(p=>!before.has(p.uid))
  expect(made).toHaveLength(1)
  return made[0]!
}

it('takes the bore of the CDU port it starts at',()=>{
  const cdu=addCdu([{id:'supply',diameterM:.08},{id:'return',diameterM:.08}])
  const pipe=connect({modelId:cdu,portId:'supply'},{modelId:cdu,portId:'return'})
  expect(pipeSummary(pipe).diameterM).toBe(.08)
  expect((pipe as Bag).cooling.connection).toMatchObject({startPortId:`${cdu}.supply`,endPortId:`${cdu}.return`})
})

it('falls back to the toolbar pipe default when no port states a bore',()=>{
  const cdu=addCdu()
  const pipe=connect({modelId:cdu,portId:'supply'},{modelId:cdu,portId:'return'})
  const toolbarPipe=createPipeSegment(live()).model
  expect(pipeSummary(pipe).diameterM).toBe(pipeSummary(toolbarPipe).diameterM)
  expect(pipeSummary(pipe).diameterM).toBe(.1)
})
