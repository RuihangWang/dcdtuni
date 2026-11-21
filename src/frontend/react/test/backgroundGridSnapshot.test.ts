import * as THREE from 'three'
import {expect,it} from 'vitest'
import {buildSceneSnapshot,captureViewState} from '../src/renderer/sceneSnapshot'
import {buildBackgroundGrid,disposeBackgroundGrid,GRID_OPACITY,GRID_Y_OFFSET} from '../src/geometry/backgroundGrid'
import {validateSceneSnapshot} from '../src/renderer/validateScene'
const project={uid:'controlled-grid',name:'view only'}
it('captures the public default and toolbar grid intent outside OO geometry without changing UID/frame/buffers',()=>{
 const root=new THREE.Group();root.position.set(-5,0,-4)
 const model=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial({color:'red'}));model.userData.modelId='box';root.add(model)
 const before=buildSceneSnapshot({root,project,timeSeconds:12.5,selectedUids:['box']})
 const after=buildSceneSnapshot({root,project,timeSeconds:12.5,selectedUids:['box'],backgroundGrid:{showGrid:false,gridSpacingMm:600}})
 expect(before.view.backgroundGrid).toMatchObject({visible:true,spacingM:.1,opacity:GRID_OPACITY})
 expect(after.view.backgroundGrid).toMatchObject({visible:false,spacingM:.6})
 expect(after.project).toEqual(before.project);expect(after.primitives).toEqual(before.primitives)
 expect(after.view.selectedUids).toEqual(['box']);expect(after.view.timeSeconds).toBe(12.5)
 const grid=after.view.backgroundGrid!;expect(grid.matrix.slice(12,15)).toEqual([5,GRID_Y_OFFSET,4])
 validateSceneSnapshot(after)
})
it('keeps the schema strict for finite display metadata and no model/source fields',()=>{
 const packet=buildSceneSnapshot({root:new THREE.Group(),project})
 for(const mutate of [(p:any)=>p.view.backgroundGrid.spacingM=NaN,(p:any)=>p.view.backgroundGrid.visible=1,(p:any)=>p.view.backgroundGrid.modelUid='fake',(p:any)=>p.view.backgroundGrid.matrix[0]=Infinity]){
  const candidate=structuredClone(packet);mutate(candidate);expect(()=>validateSceneSnapshot(candidate)).toThrow()
 }
})
it('produces literal real Three GridHelper golden buffers for actual CPU USD comparison',async()=>{
 const records=[]
 for(const spacingMm of [1,10,50,100,300,600,700,1000,5000]){
  const root=new THREE.Group();root.position.set(-5,0,-4);root.rotation.y=.3;root.updateMatrixWorld(true)
  const view=captureViewState({root,project,backgroundGrid:{showGrid:true,gridSpacingMm:spacingMm}})
  const helper=buildBackgroundGrid(spacingMm/1000)
  const positions=Array.from(helper.geometry.getAttribute('position').array)
  const samples=[0,12,Math.floor(positions.length/24)*12,positions.length-12].map(index=>({index,projectPosition:new THREE.Vector3().fromArray(positions,index).applyMatrix4(new THREE.Matrix4().fromArray(view.backgroundGrid!.matrix)).toArray()}))
  records.push({inputSpacingMm:spacingMm,view,positions,colors:Array.from(helper.geometry.getAttribute('color').array),samples})
  disposeBackgroundGrid(helper)
 }
 expect(records.every(r=>r.view.backgroundGrid!==undefined)).toBe(true)
 const output=process.env.DCDTUNI_GRID_GOLDEN_OUTPUT
 if(output){const {writeFileSync}=await import('node:fs');writeFileSync(output,JSON.stringify({producer:'actual-installed-THREE.GridHelper',cases:records}))}
})
