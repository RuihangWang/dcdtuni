import {expect,it} from 'vitest'
import {keepActiveOvCamera} from '../src/renderer/RendererControl'
import type {SceneSnapshot,CameraState} from '../src/renderer/sceneSnapshot'
const hash='a'.repeat(64),oldHash='b'.repeat(64),revision=31
const local:CameraState={position:[12.5514,7.2,13.2277],target:[4.4514,1.8,3.3277],up:[0,1,0],fov:45,aspect:1.5,near:.1,far:400}
const native:CameraState={...local,position:[4.8534,1.1215,17.1898]}
function packet():SceneSnapshot{return {schema:'dcdtuni.render-scene/v1',project:{uid:'proj_Shinaide',name:'controlled scene'},coordinateSystem:{upAxis:'Y',metersPerUnit:1,handedness:'right'},primitives:[],textures:{},warnings:[],view:{camera:local,selectedUids:['new-selection'],timeSeconds:20}}}
// The current caller has accepted hash/revision in its refs. This helper
// actually ignores both values and only tests a nonunique project UID.
it('preserves a current scene orbit and local selection/time',()=>{
 const p=packet();const ack={requestId:'read-current',ok:true,projectHash:hash,sceneRevision:revision,view:{camera:native,selectedUids:['old-selection'],timeSeconds:0}}
 expect(keepActiveOvCamera(p,ack,'proj_Shinaide',{projectUid:'proj_Shinaide',projectHash:hash,sceneRevision:revision}).view).toEqual({...p.view,camera:native})
})
for(const row of [{label:'other accepted revision',projectHash:hash,sceneRevision:32},{label:'other same-UID content',projectHash:oldHash,sceneRevision:revision},{label:'missing scene identity'}]){
 it('does not import '+row.label+' camera into a locally captured scene',()=>{
  const p=packet();const ack={requestId:'read-foreign',ok:true,...row,view:{camera:native,selectedUids:[],timeSeconds:null}}
  expect(keepActiveOvCamera(p,ack,'proj_Shinaide',{projectUid:'proj_Shinaide',projectHash:hash,sceneRevision:revision}).view.camera).toEqual(local)
 })
}
