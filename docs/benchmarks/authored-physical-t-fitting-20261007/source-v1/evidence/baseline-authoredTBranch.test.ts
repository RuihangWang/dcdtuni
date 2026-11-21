import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {createPipeJunction, createPipeSegment} from '../src/legacy/factories'
import {stateV4} from '../src/legacy/cfdprojModules'
import {installLiquidGlobals, _resetLiquidUidCounter} from '../src/legacy/liquidGlobals'
import * as store from '../src/projectStore'
import type {CfdProject} from '../src/schema/cfdproj'

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
it('real shared upgrade inherits the authored 73mm diameter rather than bridge25mm or spec text',()=>{const pipe=sourcePipe(),id=w._upgradeToTBranch(pipe.uid,1);const junction=store.findConstruction(live(),id)!.model as Bag;const parts=Object.values(live().constructions!.pipeSegments!) as Bag[];console.info('ACTUAL_SPLIT',JSON.stringify({inputDiameter:.073,inputSpec:pipe.cooling.spec,junctionDiameter:junction.cooling.fittingDiameterM,partDiameters:parts.map(p=>p.geometry.diameterM)}));expect(parts.map(p=>p.geometry.diameterM)).toEqual([.073,.073]);expect(junction.cooling.fittingDiameterM).toBe(.073)})
it('both real creation factories author an explicit25mm three-port physical fitting',()=>{const free=createPipeJunction(live()).model as Bag;const bridge=w.createPipeJunctionModel('Bridge',null,{x:0,y:.25,z:0}) as Bag;console.info('ACTUAL_FACTORY',JSON.stringify({free:free.cooling,bridge:bridge.cooling}));for(const m of [free,bridge]){expect(m.cooling.type).toBe('T_branch');expect(m.cooling.fittingDiameterM).toBe(.025);expect(m.cooling.ports).toHaveLength(3);expect(m.cooling.ports.map((p:Bag)=>p.direction)).toEqual([{x:-1,y:0,z:0},{x:1,y:0,z:0},{x:0,y:0,z:1}])}})
