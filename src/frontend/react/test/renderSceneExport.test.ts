import {afterEach,expect,it,vi} from 'vitest'
import type {SceneSnapshot} from '../src/renderer/sceneSnapshot'
import {saveRenderScene} from '../src/renderer/renderSceneExport'

const scene:SceneSnapshot={schema:'dcdtuni.render-scene/v1',project:{uid:'hybrid',name:'Hybrid'},
 coordinateSystem:{upAxis:'Y',metersPerUnit:1,handedness:'right'},primitives:[],textures:{},
 view:{selectedUids:['rack1'],timeSeconds:600},warnings:[]}
const receipt={path:'/case/Render/scenes/scene.json',sha256:'a'.repeat(64),project_uid:'hybrid',
 time_seconds:600,primitives:0,textures:0}
afterEach(()=>vi.restoreAllMocks())

it('persists the frozen rendered frame against the explicitly selected project case',async()=>{
 const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(receipt),{status:201}))
 const result=await saveRenderScene(scene,'/case')
 expect(fetch.mock.calls[0]![0]).toBe('/api/render-scene/export')
 expect(JSON.parse(fetch.mock.calls[0]![1]!.body as string)).toEqual({case_dir:'/case',scene})
 expect(result).toEqual(receipt)
})

it('does not accept an acknowledgment for another project or physical time',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(new Response(JSON.stringify({...receipt,project_uid:'other'}),{status:201}))
  .mockResolvedValueOnce(new Response(JSON.stringify({...receipt,time_seconds:0}),{status:201}))
 await expect(saveRenderScene(scene,'/case')).rejects.toThrow(/different|identity|match/)
 await expect(saveRenderScene(scene,'/case')).rejects.toThrow(/different|identity|match/)
})

it('reports server refusal and never converts a failed write into a saved result',async()=>{
 vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify({error:'case directory changed'}),{status:409}))
 await expect(saveRenderScene(scene,'/case')).rejects.toThrow('case directory changed')
})
