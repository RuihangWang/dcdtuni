/** One FIFO stage-mutation lane for every result owner on one OV transport.
 * A sent mutation completes before a newer owner authors. Ownership is recorded
 * only from completed writes, separately from current frontend intent. */
export type OvResultOwner='air-postprocess'|'mesh-preview'
export interface OvResultStage {projectUid:string;projectHash:string;sceneRevision:number}
type Lane={tail:Promise<void>;stageKey:string;epoch:number;owner:OvResultOwner|null}
const lanes=new WeakMap<object,Lane>()
function laneFor(client:object):Lane {
 let lane=lanes.get(client)
 if(!lane){lane={tail:Promise.resolve(),stageKey:'',epoch:0,owner:null};lanes.set(client,lane)}
 return lane
}
/** Called only after the scene ACK: author_scene clears RESULT_ROOT on actual
 * geometry replacement. Old result ACKs cannot acquire the new scene owner. */
export function setOvResultStage(client:object,stage:OvResultStage):void {
 const lane=laneFor(client),key=JSON.stringify([stage.projectUid,stage.projectHash,stage.sceneRevision])
 if(key!==lane.stageKey){lane.stageKey=key;lane.epoch++;lane.owner=null}
}
export function enqueueOvResultMutation<T>(client:object,isCurrent:()=>boolean,apply:()=>Promise<T>,nextOwner?:OvResultOwner|null):Promise<T|undefined> {
 const lane=laneFor(client),epoch=lane.epoch
 const result=lane.tail.then(async()=>{
  if(epoch!==lane.epoch||!isCurrent())return undefined
  const value=await apply()
  if(epoch===lane.epoch&&nextOwner!==undefined)lane.owner=nextOwner
  return value
 })
 lane.tail=result.then(()=>{},()=>{})
 return result
}
/** Mesh hide/air clear remove only that carrier's actually ACKed subtree.
 * The guard is evaluated after an in-flight load; a later air intent prevents a
 * pending mesh clear, and a sent mesh clear finishes before the later air load. */
export function clearOwnedOvResultLayers<T>(client:object,owner:OvResultOwner,isCurrent:()=>boolean,clear:()=>Promise<T>):Promise<T|undefined> {
 const lane=laneFor(client)
 return enqueueOvResultMutation(client,()=>lane.owner===owner&&isCurrent(),clear,null)
}
