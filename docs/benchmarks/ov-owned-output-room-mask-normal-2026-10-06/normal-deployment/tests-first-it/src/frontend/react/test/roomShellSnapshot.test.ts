import * as THREE from 'three'
import {describe,it,expect} from 'vitest'
import {readFileSync} from 'node:fs'
import {buildSceneSnapshot} from '../src/renderer/sceneSnapshot'

function model(root:THREE.Group,uid:string,hidden=false) {
  const group=new THREE.Group();group.userData.modelId=uid;group.visible=!hidden
  const mesh=new THREE.Mesh(new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute([0,0,0,1,0,0,0,1,0],3)),new THREE.MeshBasicMaterial())
  mesh.name='Room_Wall_misleading';group.add(mesh);root.add(group)
}
function stripRole(packet:ReturnType<typeof buildSceneSnapshot>) {
  return packet.primitives.map(({displayRole,...rest})=>rest)
}
describe('authoritative display-only room enclosure role',()=>{
  it('uses actual walls/floors boundary flags and leaves actual equipment visible geometry intact',()=>{
    const project=JSON.parse(readFileSync(new URL('./fixtures/ov-room-shell-project.cfdproj',import.meta.url),'utf8'))
    const root=new THREE.Group()
    const uids=['walls','floors','boxes','acus'].flatMap(bucket=>Object.values(project.constructions[bucket]).map((m:any)=>m.uid))
    for(const uid of uids)model(root,String(uid))
    const before=JSON.stringify(project)
    const packet=buildSceneSnapshot({root,project})
    expect(packet.primitives.filter((p:any)=>p.displayRole==='room-shell').map((p:any)=>p.modelUid)).toEqual(['m_15','m_17','m_18','m_19'])
    expect(packet.primitives.filter((p:any)=>['m_heat','m_20','m_return'].includes(p.modelUid)).every((p:any)=>p.displayRole===undefined)).toBe(true)
    expect(JSON.stringify(project)).toBe(before)
    const unmarked=JSON.parse(before)
    for(const bucket of ['walls','floors'])for(const record of Object.values(unmarked.constructions[bucket]) as any[])delete record.isRoomBoundary
    expect(stripRole(packet)).toEqual(stripRole(buildSceneSnapshot({root,project:unmarked})))
  })
  it('does not guess from names/kind-like userdata/interior wall/box flags',()=>{
    const root=new THREE.Group()
    for(const uid of ['interior','equipment','shell'])model(root,uid,uid==='shell')
    const project={uid:'p',constructions:{
      walls:{interior:{uid:'interior',isRoomBoundary:false},shell:{uid:'shell',isRoomBoundary:true}},
      boxes:{equipment:{uid:'equipment',isRoomBoundary:true,meta:{name:'Room_Wall'}}}}}
    const p=buildSceneSnapshot({root,project})
    expect(p.primitives.map((x:any)=>[x.modelUid,x.displayRole,x.visible])).toEqual([
      ['interior',undefined,true],['equipment',undefined,true],['shell','room-shell',false]])
    expect(root.children[2]!.visible).toBe(false)
  })
  it('legacy projects without constructions remain valid and untagged',()=>{
    const root=new THREE.Group();model(root,'legacy')
    const p=buildSceneSnapshot({root,project:{uid:'p'}})
    expect(p.primitives).toHaveLength(1)
    expect(p.primitives[0]!.displayRole).toBeUndefined()
  })
})
