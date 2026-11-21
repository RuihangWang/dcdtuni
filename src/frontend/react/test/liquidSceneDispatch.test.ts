import {readFileSync} from 'node:fs'
import {resolve} from 'node:path'
import * as THREE from 'three'
import {expect,it} from 'vitest'
import {stateV4} from '../src/legacy/cfdprojModules'
import type {CfdProject} from '../src/schema/cfdproj'
import {createSceneBuilder,emptyRegistries,type SceneHost} from '../src/interaction/sceneBuilder'
import {setLiquidModeActive} from '../src/liquid/liquidModeState'

it('renders the saved dual-ring case in both modes without treating faceless liquid junctions as box surfaces',()=>{
 const raw=JSON.parse(readFileSync(resolve(process.cwd(),'../../../case/verification/shinaide-legacy-projects/Shinaide_liquid_ring_rear.json'),'utf8'))
 const project=stateV4().normalizeStateV4(raw) as unknown as CfdProject
 const before=JSON.stringify(project)
 const globalGroup=new THREE.Group(),wrapperGroup=new THREE.Group();wrapperGroup.add(globalGroup)
 const host:SceneHost={globalGroup,wrapperGroup,registries:emptyRegistries(),project:()=>project,selectedUid:()=>null,webglAvailable:false}
 const builder=createSceneBuilder(host)
 try {
  for(const on of [false,true,false]){
   setLiquidModeActive(on)
   expect(()=>builder.rebuild()).not.toThrow()
   expect(()=>builder.rebuildModels(['ring_supply_feed_tee','ring_return_feed_tee'])).not.toThrow()
   for(const uid of ['ring_supply_feed_tee','ring_return_feed_tee']){
    expect(host.registries.renderRootMap.has(uid)).toBe(true)
    expect(host.registries.renderMeshes.filter(m=>m.userData.modelId===uid)).toEqual([])
   }
   expect(host.registries.renderMeshes.some(m=>m.userData.modelId==='m_30'&&m.userData.exportName)).toBe(true)
   expect(JSON.stringify(project)).toBe(before)
  }
 }finally{builder.clear();setLiquidModeActive(false)}
})
