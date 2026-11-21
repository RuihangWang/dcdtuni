import * as THREE from 'three'
import {afterEach,expect,it,vi} from 'vitest'
import * as color from '../src/liquid/liquidTempColor'
import {fetchLiquidResults,resetLiquidRun} from '../src/liquid/liquidSession'
import {buildSceneSnapshot} from '../src/renderer/sceneSnapshot'

afterEach(()=>{
  color.setPipeTempColoring(false)
  color.setChipTempColoring(false)
  color.setResultSample(-1)
  resetLiquidRun()
  vi.restoreAllMocks()
  delete (window as any).globalGroup
  delete (window as any).allModels
})

it('publishes fresh material snapshots when a pipe overlay is toggled at the same physical time',async()=>{
  const root=new THREE.Group()
  const mesh=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshStandardMaterial({color:0xffffff}))
  mesh.userData={modelId:'pipeUid',pipeModelId:'pipeUid'}
  root.add(mesh)
  ;(window as any).globalGroup=root
  const raw={time_s:[0,20],cell_edge_ids:['pipeEdge'],cell_temp_K:[[300,320]],
    object_bindings:{pipeUid:{kind:'pipe_segment',edge_ids:['pipeEdge']}}}
  vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(raw)))
  await fetchLiquidResults()
  color.setResultSample(1)
  const snapshot=()=>buildSceneSnapshot({root,project:{uid:'project',name:'test'},timeSeconds:color.selectedResultTime()})
  const before=snapshot()
  const delivered:ReturnType<typeof snapshot>[]=[]
  let lastRevision=0
  const unsubscribe=color.subscribeResultSample(()=>{
    const revision=(color as unknown as {resultRenderRevision?:()=>number}).resultRenderRevision?.()
    if(typeof revision==='number'&&revision!==lastRevision){lastRevision=revision;delivered.push(snapshot())}
  })
  try {
    expect(color.setPipeTempColoring(true)).toBe(true)
    expect(delivered).toHaveLength(1)
    expect(delivered[0]!.primitives[0]!.material.color).not.toEqual(before.primitives[0]!.material.color)
    expect(color.setPipeTempColoring(false)).toBe(true)
    expect(delivered).toHaveLength(2)
    expect(delivered[1]!.primitives[0]!.material.color).toEqual(before.primitives[0]!.material.color)
    expect(delivered.map(packet=>packet.view.timeSeconds)).toEqual([20,20])
    expect(color.selectedResultSample()).toBe(1)
  }finally{unsubscribe()}
})

it('publishes chip overlay changes without requiring a time-slider or project edit',async()=>{
  const root=new THREE.Group()
  for(const equipmentId of ['srvA','srvB']){
    const mesh=new THREE.Mesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshStandardMaterial({color:0xffffff}))
    mesh.userData={modelId:'rackUid',equipmentId};root.add(mesh)
  }
  ;(window as any).globalGroup=root
  ;(window as any).allModels=()=>[{uid:'rackUid',kind:'rack',meta:{name:'Rack'},
    constructions:{servers:{srvA:{meta:{name:'srvA'}},srvB:{meta:{name:'srvB'}}}}}]
  const raw={time_s:[0,20],chip_temp_K:[[300,302],[310,312]],
    rack_summaries:[{rack_id:'Rack',servers:[{server_id:'srvA'},{server_id:'srvB'}]}]}
  vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(raw)))
  await fetchLiquidResults();color.setResultSample(1)
  const changed=vi.fn()
  const unsubscribe=color.subscribeResultSample(changed)
  try {
    expect(color.setChipTempColoring(true)).toBe(true)
    expect(changed).toHaveBeenCalledTimes(1)
    expect(color.setChipTempColoring(false)).toBe(true)
    expect(changed).toHaveBeenCalledTimes(2)
    expect(color.selectedResultSample()).toBe(1)
    expect(color.selectedResultTime()).toBe(20)
  }finally{unsubscribe()}
})
