import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {createModel, createPipeJunction, createPipeSegment} from '../src/legacy/factories'
import {stateV4} from '../src/legacy/cfdprojModules'
import {installLiquidGlobals, _resetLiquidUidCounter} from '../src/legacy/liquidGlobals'
import * as store from '../src/projectStore'
import type {CfdProject} from '../src/schema/cfdproj'
import * as THREE from 'three'
import {createSceneBuilder,emptyRegistries,type SceneHost} from '../src/interaction/sceneBuilder'
import {serializeProjectState,readProjectFileText} from '../src/io/projectFiles'
import {bindStlFileHashes} from '../src/legacy/cfdrunBuild'
import type {CfdrunV2} from '../src/schema/cfdrun'
import '../src/legacy/geometryBuilder'


type Bag = Record<string, any>
const w = window as unknown as Bag
function project() {const p=stateV4().getInitialStateV4() as unknown as CfdProject;p.uid='authored-tee';return p}
function live() {return store.getProject()!}
beforeEach(async()=>{_resetLiquidUidCounter();store.loadProject(project(), 'private-control.cfdproj');installLiquidGlobals();
 for(const key of ['createPipeSegmentModel','createPipeJunctionModel','addModel'])vi.stubGlobal(key,w[key]);
 vi.stubGlobal('t',(key:string)=>key);vi.stubGlobal('alert',vi.fn());vi.stubGlobal('rebuildGeometry',vi.fn());vi.stubGlobal('setSelection',vi.fn());vi.stubGlobal('pushUndoCheckpoint',store.pushUndoCheckpoint);
 await import('../../shared/js/liquid_mode.js')})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
function sourcePipe(diameter:unknown=.073) {const {bucket,model}=createPipeSegment(live());const pipe=model as Bag;pipe.geometry.diameterM=diameter;pipe.geometry.waypoints=[{x:-1,y:.25,z:0},{x:0,y:.25,z:0},{x:1,y:.25,z:0}];pipe.cooling.spec='pipe_999mm';pipe.cooling.loopRole='return';pipe.cooling.connection.startPortId='source.supply';pipe.cooling.connection.endPortId='destination.return';store.addModel(bucket,model);const actual=store.findConstruction(live(),model.uid)!.model as Bag;vi.stubGlobal('_modelMap',new Map([[model.uid,actual]]));return actual}
it('real shared upgrade inherits the authored 73mm diameter rather than bridge25mm or spec text',()=>{const pipe=sourcePipe(),id=w._upgradeToTBranch(pipe.uid,1);const junction=store.findConstruction(live(),id)!.model as Bag;const parts=Object.values(live().constructions!.pipeSegments!) as Bag[];console.info('ACTUAL_SPLIT',JSON.stringify({inputDiameter:.073,inputSpec:pipe.cooling.spec,junctionDiameter:junction.cooling.fittingDiameterM,partDiameters:parts.map(p=>p.geometry.diameterM)}));expect(parts.map(p=>p.geometry.diameterM)).toEqual([.073,.073]);expect(junction.cooling.fittingDiameterM).toBe(.073);expect(new Set(parts.map(p=>p.uid)).size).toBe(2);expect(parts.map(p=>p.cooling.loopRole)).toEqual(['return','return']);expect(parts[0]!.cooling.connection).toMatchObject({startPortId:'source.supply',endPortId:id+'.port_1'});expect(parts[1]!.cooling.connection).toMatchObject({startPortId:id+'.port_2',endPortId:'destination.return'});expect(junction.cooling.ports.map((p:Bag)=>p.connectedPipeId)).toEqual([parts[0]!.uid,parts[1]!.uid,null]);expect(store.findConstruction(live(),pipe.uid)).toBeNull()})
it('both real creation factories author an explicit25mm three-port physical fitting',()=>{const free=createPipeJunction(live()).model as Bag;const bridge=w.createPipeJunctionModel('Bridge',null,{x:0,y:.25,z:0}) as Bag;console.info('ACTUAL_FACTORY',JSON.stringify({free:free.cooling,bridge:bridge.cooling}));for(const m of [free,bridge]){expect(m.cooling.type).toBe('T_branch');expect(m.cooling.fittingDiameterM).toBe(.025);expect(m.cooling.ports).toHaveLength(3);expect(m.cooling.ports.map((p:Bag)=>p.direction)).toEqual([{x:-1,y:0,z:0},{x:1,y:0,z:0},{x:0,y:0,z:1}])}})

it.each([undefined, null, 0, -.1, NaN, Infinity, '.073'])('rejects invalid authored source diameter %s before split/undo or topology mutation',diameter=>{
 const pipe=sourcePipe(diameter);if(diameter===undefined)delete pipe.geometry.diameterM
 const before=JSON.stringify(live()),checkpoint=vi.fn(store.pushUndoCheckpoint);vi.stubGlobal('pushUndoCheckpoint',checkpoint)
 expect(w._upgradeToTBranch(pipe.uid,1)).toBeNull();expect(checkpoint).not.toHaveBeenCalled();expect(JSON.stringify(live())).toBe(before);expect(store.findConstruction(live(),pipe.uid)!.model).toBe(pipe);expect(w.alert).toHaveBeenCalled()
})
function exportFitting(p:CfdProject,id:string|string[]) {
 const group=new THREE.Group(),wrapper=new THREE.Group();wrapper.add(group);wrapper.updateMatrixWorld(true)
 const host:SceneHost={globalGroup:group,wrapperGroup:wrapper,registries:emptyRegistries(),project:()=>p,selectedUid:()=>null,webglAvailable:false},builder=createSceneBuilder(host)
 const old=w.allModels;w.allModels=()=>stateV4().allModels(p)
 try{return builder.api.buildGroupedStlMap(Array.isArray(id)?id:[id],false)}finally{builder.clear();w.allModels=old}
}
it('exports the factory physical fitting after yaw and uniform scale, with real source UID/carrier provenance and outward closed triangles',()=>{
 const p=live(),made=createPipeJunction(p,{x:.2,y:.25,z:.3}),m=made.model as Bag
 m.cooling.quaternion={x:0,y:Math.SQRT1_2,z:0,w:Math.SQRT1_2};m.geometry.scale={x:2,y:2,z:2};store.addModel(made.bucket,made.model)
 const actual=store.findConstruction(live(),m.uid)!.model as Bag,before=JSON.stringify(actual),files=exportFitting(live(),m.uid),text=files['merged_liquid_transport.stl']!
 expect(text).toBeTruthy();const vertices=[...text.matchAll(/vertex\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)/g)].map(row=>new THREE.Vector3(Number(row[1]),Number(row[2]),Number(row[3])))
 expect(Math.min(...vertices.map(v=>v.x))).toBeCloseTo(.175,7);expect(Math.max(...vertices.map(v=>v.x))).toBeCloseTo(.28,7)
 expect(Math.min(...vertices.map(v=>v.y))).toBeCloseTo(.225,7);expect(Math.max(...vertices.map(v=>v.y))).toBeCloseTo(.275,7)
 expect(Math.min(...vertices.map(v=>v.z))).toBeCloseTo(.22,7);expect(Math.max(...vertices.map(v=>v.z))).toBeCloseTo(.38,7)
 const edges=new Map<string,[number,number]>();let volume=0
 for(let i=0;i<vertices.length;i+=3){const a=vertices[i]!,b=vertices[i+1]!,c=vertices[i+2]!;expect(b.clone().sub(a).cross(c.clone().sub(a)).length()).toBeGreaterThan(0);volume+=a.dot(b.clone().cross(c))/6;for(const [one,two]of [[a,b],[b,c],[c,a]]){const u=one!.toArray().join(','),v=two!.toArray().join(','),key=[u,v].sort().join('|'),row=edges.get(key)??[0,0];row[u<v?0:1]++;edges.set(key,row)}}
 expect([...edges.values()].every(([a,b])=>a===1&&b===1)).toBe(true);expect(volume).toBeGreaterThan(0)
 const manifest={geometryExport:{coordinateSystem:'Y-up',unit:'m',stlFiles:{},solids:{}}} as CfdrunV2;bindStlFileHashes(manifest,files,live());expect(Object.values(manifest.geometryExport.sourceRegions??{}).map(row=>row.sourceEntityUid)).toEqual([m.uid]);expect(JSON.stringify(actual)).toBe(before)
})
it('keeps old absent fitting physics absent through actual load/save/reload and honestly rejects export with an actionable message',()=>{
 const made=createPipeJunction(live()),m=made.model as Bag;delete m.cooling.fittingDiameterM;store.addModel(made.bucket,made.model)
 const json=serializeProjectState(live()),loaded=readProjectFileText(json);expect(loaded.ok).toBe(true);if(!loaded.ok)throw new Error(loaded.error)
 // The dialog's parsed document is installed by the actual store load path,
 // which revives non-enumerable runtime kind from the canonical bucket.
 store.loadProject(loaded.project,'private-reloaded-fitting.cfdproj')
 const saved=store.findConstruction(live(),m.uid)!.model as Bag;expect(saved.kind).toBe('pipe_junction');expect(saved.cooling.fittingDiameterM).toBeUndefined();expect(saved.cooling.ports).toEqual(m.cooling.ports)
 expect(()=>exportFitting(live(),m.uid)).toThrow(/invalid fitting diameter.*Properties before exporting/)
 expect(saved.cooling.fittingDiameterM).toBeUndefined()
})

it('does not infer a missing physical port direction from its display or offset',()=>{
 const made=createPipeJunction(live()),m=made.model as Bag;delete m.cooling.ports[0].direction;store.addModel(made.bucket,made.model);const before=JSON.stringify(live())
 expect(()=>exportFitting(live(),m.uid)).toThrow(/explicit physical port offsets and directions.*Properties/);expect(JSON.stringify(live())).toBe(before)
})

it('runs the real73mm split through save/reload and grouped physical export without changing hydraulic input or contributor identities',()=>{
 const pipe=sourcePipe();pipe.cooling.connection.startPortId=null;pipe.cooling.connection.endPortId=null
 const id=w._upgradeToTBranch(pipe.uid,1),ids=stateV4().allModels(live()).filter(m=>m.kind==='pipe_segment'||m.kind==='pipe_junction').map(m=>m.uid)
 const loaded=readProjectFileText(serializeProjectState(live()));expect(loaded.ok).toBe(true);if(!loaded.ok)throw new Error(loaded.error);store.loadProject(loaded.project,'private73mm-reload.cfdproj')
 const before=JSON.stringify(live()),files=exportFitting(live(),ids),text=files['merged_liquid_transport.stl']!,ys=[...text.matchAll(/vertex\s+[^\s]+\s+([^\s]+)\s+[^\s]+/g)].map(row=>Number(row[1]))
 expect(Math.min(...ys)).toBeCloseTo(.25-.073/2,6);expect(Math.max(...ys)).toBeCloseTo(.25+.073/2,6)
 const manifest={geometryExport:{coordinateSystem:'Y-up',unit:'m',stlFiles:{},solids:{}}} as CfdrunV2;bindStlFileHashes(manifest,files,live());const rows=Object.values(manifest.geometryExport.sourceRegions??{});expect(new Set(rows.map(row=>row.sourceEntityUid))).toEqual(new Set(ids));expect(rows.every(row=>row.carrierContributorUids!.join(',')===[...ids].sort().join(','))).toBe(true)
 expect((store.findConstruction(live(),id)!.model.cooling as Bag).fittingDiameterM).toBe(.073);expect(JSON.stringify(live())).toBe(before)
})


// These controls call the production legacy bridge API. They do not invoke
// transport export or the whole project graph/compiler.
it.each([{start:true,end:false},{start:false,end:true},{start:true,end:true}])('split semantics preserves authored blind outer endpoints %j',blindEnds=>{
 const pipe=sourcePipe();pipe.cooling.connection.blindEnds=blindEnds
 if(blindEnds.start)pipe.cooling.connection.startPortId=null
 if(blindEnds.end)pipe.cooling.connection.endPortId=null
 const id=w._upgradeToTBranch(pipe.uid,1),parts=Object.values(live().constructions!.pipeSegments!) as Bag[]
 console.info('ACTUAL_SPLIT_BLIND',JSON.stringify({input:blindEnds,result:parts.map(part=>({waypoints:part.geometry.waypoints,connection:part.cooling.connection}))}))
 expect(id).toBeTruthy();expect(parts).toHaveLength(2)
 expect(parts[0]!.cooling.connection.blindEnds).toEqual({start:blindEnds.start,end:false})
 expect(parts[1]!.cooling.connection.blindEnds).toEqual({start:false,end:blindEnds.end})
 expect(parts[0]!.geometry.waypoints[0]).toEqual(pipe.geometry.waypoints[0])
 expect(parts[1]!.geometry.waypoints.at(-1)).toEqual(pipe.geometry.waypoints.at(-1))
 expect(parts[0]!.cooling.connection.endPortId).toBe(id+'.port_1')
 expect(parts[1]!.cooling.connection.startPortId).toBe(id+'.port_2')
})
const unallocatedDeclarations:[string,(pipe:Bag)=>void][]=[
 ['inline flow-control valve',pipe=>{pipe.cooling.valves=[{id:'authored_valve',type:'flow_control',side:'below',position:{x:-.5,y:.25,z:0},connectedManifoldPortId:'',connectedRackId:''}]}],
 ['tee branch with physical diameter and waypoints',pipe=>{pipe.cooling.valves=[{id:'authored_tee',type:'tee_junction',side:'below',position:{x:.5,y:.25,z:0},connectedManifoldPortId:'',connectedRackId:'',branchDiameterM:.031,branchWaypoints:[{x:.5,y:.25,z:0},{x:.5,y:.25,z:.6}]}]}],
 // Migration preserves this container but defines no element/owner contract.
 ['opaque connection junction declaration',pipe=>{pipe.cooling.connection.junctions=[{declared:'unallocated'}]}],
 ['malformed valve container',pipe=>{pipe.cooling.valves={id:'not-an-array'}}],
 ['malformed junction container',pipe=>{pipe.cooling.connection.junctions='not-an-array'}],
 ['malformed blind endpoint flag',pipe=>{pipe.cooling.connection.blindEnds={start:'yes',end:false}}],
 ['unknown blind endpoint declaration',pipe=>{pipe.cooling.connection.blindEnds={start:false,end:false,authoredUnknown:true}}],
]
it.each(unallocatedDeclarations)('split semantics rejects %s before factories, undo, store or map mutation',(_label,declare)=>{
 const pipe=sourcePipe();declare(pipe);const before=JSON.stringify(live()),map=w._modelMap,entries=[...map.entries()]
 const checkpoint=vi.fn(store.pushUndoCheckpoint),junctionFactory=vi.fn(w.createPipeJunctionModel),pipeFactory=vi.fn(w.createPipeSegmentModel)
 vi.stubGlobal('pushUndoCheckpoint',checkpoint);vi.stubGlobal('createPipeJunctionModel',junctionFactory);vi.stubGlobal('createPipeSegmentModel',pipeFactory)
 const remove=vi.spyOn(w,'removeModelByUid'),add=vi.spyOn(w,'addModel'),notify=vi.fn(),unsubscribe=store.subscribe(notify)
 try{
  const id=w._upgradeToTBranch(pipe.uid,1)
  console.info('ACTUAL_SPLIT_METADATA',JSON.stringify({label:_label,input:{valves:pipe.cooling.valves,connection:pipe.cooling.connection},returned:id,originalStillPresent:!!store.findConstruction(live(),pipe.uid),parts:Object.values(live().constructions!.pipeSegments!).map(part=>({cooling:part.cooling}))}))
  expect(id).toBeNull();expect(checkpoint).not.toHaveBeenCalled();expect(junctionFactory).not.toHaveBeenCalled();expect(pipeFactory).not.toHaveBeenCalled()
  expect(remove).not.toHaveBeenCalled();expect(add).not.toHaveBeenCalled();expect(notify).not.toHaveBeenCalled();expect(w.rebuildGeometry).not.toHaveBeenCalled();expect(w.setSelection).not.toHaveBeenCalled()
  expect(JSON.stringify(live())).toBe(before);expect(store.findConstruction(live(),pipe.uid)!.model).toBe(pipe);expect(w._modelMap).toBe(map);expect([...map.entries()]).toEqual(entries);expect(w.alert).toHaveBeenCalled()
 }finally{unsubscribe()}
})
it('split semantics permits empty declarations and preserves unrelated real CDU and network pump owners',()=>{
 const cduMade=createModel(live(),'cdu');store.addModel(cduMade.bucket,cduMade.model)
 const cdu=store.findConstruction(live(),cduMade.model.uid)!.model as Bag;cdu.cooling.advancedMode=true;cdu.constructions.pump.rpm=2450
 const networks=live().constructions!.liquidFlowNetworks as Bag
 // This is the independent legacy chain producer's actual pump record shape.
 networks.net_main.chains=[{id:'owner_chain',path:[{pump:'pump_default',rpm:2350}]}]
 const pumpBefore=JSON.stringify(cdu.constructions.pump),networkBefore=JSON.stringify(networks),pipe=sourcePipe();pipe.cooling.valves=[];pipe.cooling.connection.junctions=[]
 const id=w._upgradeToTBranch(pipe.uid,1)
 expect(id).toBeTruthy();expect((store.findConstruction(live(),cdu.uid)!.model as Bag).constructions.pump).toEqual(JSON.parse(pumpBefore))
 expect(JSON.stringify(live().constructions!.liquidFlowNetworks)).toBe(networkBefore)
 expect((store.findConstruction(live(),id)!.model as Bag).cooling.fittingDiameterM).toBe(.073)
})
