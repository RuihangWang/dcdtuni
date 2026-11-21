import * as THREE from 'three'
import * as ts from 'typescript'
import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {afterEach,expect,it,vi} from 'vitest'
import fixture from './fixtures/closedCabinetShinaide.json'
import type {CfdProject} from '../src/schema/cfdproj'
import {stateV4} from '../src/legacy/cfdprojModules'
import * as store from '../src/projectStore'
import {createSceneBuilder,emptyRegistries,type SceneHost} from '../src/interaction/sceneBuilder'
import {setLiquidModeActive} from '../src/liquid/liquidModeState'
import {paintSelectionHighlights} from '../src/interaction/selectionHighlight'
import {NO_VIEW_FADES} from '../src/geometry/viewFades'
import {pbrMaterials} from '../src/legacy/geometryBuilder'
import {applyClick} from '../src/interaction/selection'
import * as gesture from '../src/interaction/rackFaceGesture'
import {isSelectableHit,pickNearestHit,viewportSelectionFor} from '../src/interaction/picking'
import type {TreeSelection} from '../src/interaction/treeNodes'
const builders:Array<ReturnType<typeof createSceneBuilder>>=[]
afterEach(()=>{for(const b of builders.splice(0))b.clear();setLiquidModeActive(false);pbrMaterials()._resetForTests();vi.unstubAllGlobals();vi.restoreAllMocks()})
// Execute the actual Viewport selectAt arrow with real projectStore, scene
// builder, raycaster, picker and painter. Only DOM extent and App callbacks
// are controlled; no WebGL/browser/OV or physical solve acceptance is claimed.
function viewportSelect(context:Record<string,unknown>){
 const path=resolve('src/components/Viewport.tsx'),source=ts.createSourceFile(path,readFileSync(path,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX)
 let body:string|undefined
 const visit=(n:ts.Node)=>{if(ts.isVariableDeclaration(n)&&n.name.getText(source)==='selectAt')body=n.initializer?.getText(source);ts.forEachChild(n,visit)}
 visit(source);if(!body)throw new Error('Actual Viewport selectAt not found')
 const code=ts.transpileModule('const callback='+body+';return callback;',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
 const keys=Object.keys(context);return new Function(...keys,code)(...keys.map(k=>context[k])) as (e:MouseEvent)=>void
}
function scene(kind:'box'|'cdu'){
 // jsdom has no Canvas2D. This carrier only lets real procedural texture
 // construction create normal PBR materials. It makes no pixels/GPU claim.
 vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockImplementation(((type:string)=>{
  if(type!=='2d')throw new Error('Controlled test permits Canvas2D texture carrier only')
  return {fillStyle:'',fillRect(){},getImageData(_x:number,_y:number,w:number,h:number){return {data:new Uint8ClampedArray(w*h*4)}},createImageData(w:number,h:number){return {data:new Uint8ClampedArray(w*h*4)}},putImageData(){}}
 }) as unknown as typeof HTMLCanvasElement.prototype.getContext)
 vi.stubGlobal('fetch' ,vi.fn(async()=>new Response('{}',{status:200})))
 let raw:CfdProject
 if(kind==='box')raw=JSON.parse(readFileSync(resolve('test/fixtures/boxShiftActualGui.cfdproj'),'utf8')) as CfdProject
 else{raw=stateV4().getInitialStateV4() as unknown as CfdProject;raw.constructions!.cdus={m_30:structuredClone(fixture.models.find(m=>m.uid==='m_30')!)}}
 store.loadProject(raw,null);const project=store.getProject()!,globalGroup=new THREE.Group(),wrapperGroup=new THREE.Group();wrapperGroup.add(globalGroup)
 const h:SceneHost={globalGroup,wrapperGroup,registries:emptyRegistries(),project:()=>project,selectedUid:()=>null,webglAvailable:true}
 setLiquidModeActive(kind==='cdu');const builder=createSceneBuilder(h);builders.push(builder);builder.rebuild();globalGroup.updateMatrixWorld(true)
 const uid=kind==='box'?'m_1':'m_30',model=h.registries.modelMap.get(uid)!;expect(model.kind).toBe(kind)
 const root=h.registries.renderRootMap.get(uid)!,bounds=new THREE.Box3().setFromObject(root)
 return {h,uid,root,bounds,project}
}
it.each(['box','cdu'] as const)('Shift on each visible %s body face preserves the selected whole object and paint',(kind)=>{
 const {h,uid,root,bounds,project}=scene(kind),original=JSON.stringify(project),center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3())
 let selected={uids:[uid],active:uid as string|null},node:TreeSelection|null={type:'face',uid,faceId:'front'}
 const camera=new THREE.PerspectiveCamera(45,1,.001,1000),raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2()
 const select=viewportSelect({renderer:{domElement:{getBoundingClientRect:()=>({left:0,top:0,width:1000,height:1000})}},camera,raycaster,pointer,
  liquidMode:()=>null,registriesRef:{current:h.registries},content:h.globalGroup,isSelectableHit,pickNearestHit,viewportSelectionFor,
  isAdditiveSelectGesture:gesture.isAdditiveSelectGesture,isWholeObjectSelectGesture:(gesture as unknown as {isWholeObjectSelectGesture?:unknown}).isWholeObjectSelectGesture,
  onSelectRef:{current:(hit:string|null,toggle:boolean)=>{selected=applyClick(selected.uids,selected.active,hit,toggle,[uid]);node=null}},
  emitNode:(next:TreeSelection|null)=>{node=next},clearTransformTools:vi.fn()})
 for(const axis of [new THREE.Vector3(1,0,0),new THREE.Vector3(-1,0,0),new THREE.Vector3(0,1,0),new THREE.Vector3(0,-1,0),new THREE.Vector3(0,0,1),new THREE.Vector3(0,0,-1)]){
  selected={uids:[uid],active:uid};node={type:'face',uid,faceId:'front'};camera.position.copy(center).addScaledVector(axis,Math.max(size.x,size.y,size.z)*3+1);camera.up.set(0,1,0)
  if(axis.y!==0)camera.up.set(0,0,1);camera.lookAt(center);camera.updateMatrixWorld(true)
  raycaster.setFromCamera(new THREE.Vector2(),camera);const pick=pickNearestHit(raycaster.intersectObjects(h.globalGroup.children,true).filter(isSelectableHit))
  expect(pick?.object.userData.modelId,axis.toArray().join(',')).toBe(uid)
  select(new MouseEvent('click',{clientX:500,clientY:500,shiftKey:true}))
  expect(selected,axis.toArray().join(',')).toEqual({uids:[uid],active:uid});expect(node).toBeNull()
  paintSelectionHighlights(h.registries.renderRootMap,{uid,uids:new Set([uid]),node:null},()=>()=>null,NO_VIEW_FADES,u=>String(h.registries.modelMap.get(u)?.kind??''))
  let n=0;root.traverse(o=>{if((o as THREE.Mesh).isMesh){n++;expect(o.userData._origMaterial).toBeDefined()}});expect(n).toBeGreaterThan(0)
 }
 expect(JSON.stringify(project)).toBe(original)
})
it('Ctrl/Cmd retain additive toggle while Shift BOX/CDU keeps whole-object selection',()=>{
 const shift={shiftKey:true,ctrlKey:false,metaKey:false}
 for(const kind of ['box','cdu','rack']){expect(gesture.isAdditiveSelectGesture(shift,kind)).toBe(false)
  for(const modifier of ['ctrlKey','metaKey'] as const)expect(gesture.isAdditiveSelectGesture({...shift,[modifier]:true},kind)).toBe(true)}
 for(const kind of ['pipe','wall','baffle','floor','acu'])expect(gesture.isAdditiveSelectGesture(shift,kind)).toBe(true)
})
