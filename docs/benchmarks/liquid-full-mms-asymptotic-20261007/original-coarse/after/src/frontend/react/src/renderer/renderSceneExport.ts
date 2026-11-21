import type {SceneSnapshot} from './sceneSnapshot'

export interface RenderSceneReceipt {
 path:string;sha256:string;project_uid:string;time_seconds:number|null;primitives:number;textures:number
}

/** Save only a derived snapshot; geometry/solver project sources are untouched. */
export async function saveRenderScene(scene:SceneSnapshot,caseDir:string):Promise<RenderSceneReceipt> {
 if(!caseDir.trim())throw new Error('请选择当前工程的 case 目录')
 const response=await fetch('/api/render-scene/export',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({scene,case_dir:caseDir})})
 const result=await response.json()
 if(!response.ok)throw new Error(result.error||`场景保存失败 (${response.status})`)
 if(result.project_uid!==scene.project.uid||result.time_seconds!==scene.view.timeSeconds||
  result.primitives!==scene.primitives.length||result.textures!==Object.keys(scene.textures).length||
  typeof result.path!=='string'||!result.path||typeof result.sha256!=='string'||!/^[0-9a-f]{64}$/.test(result.sha256)) {
  throw new Error('Saved scene acknowledgment does not match the exported project/time identity')
 }
 return result
}
