import Ajv from 'ajv'
import schema from '../../../shared/schema/render-display-diagnostics.schema.json'
import type { RenderViewState } from './sceneSnapshot'
import {copyGridRaster,type BackgroundGridRaster} from './displayRaster'

declare const __DCDTUNI_OV_RETURN_DIAGNOSTICS__: boolean
export const diagnosticReturnEnabled = typeof __DCDTUNI_OV_RETURN_DIAGNOSTICS__ !== 'undefined' && __DCDTUNI_OV_RETURN_DIAGNOSTICS__ === true
export const DIAGNOSTIC_REQUEST_BYTES = 1024
export const DIAGNOSTIC_REPLY_BYTES = 16384
const utf8 = new TextEncoder()
const ajv = new Ajv({ strict: false, strictNumbers: true })
const checkReply = ajv.compile(schema)
const checkRequest = ajv.compile(schema.definitions.request)
export type DiagnosticContext = Readonly<{ expectedSceneRevision:number; expectedProjectHash:string; roofModelUid?:string }>
export type DiagnosticReply = { requestId:string; ok:true; sceneRevision:number; projectHash:string;
  view:RenderViewState; diagnostics:Record<string,unknown> }
/** Owned primitives only: callers cannot change the pending request's identity. */
export function captureDiagnosticContext(value:DiagnosticContext):DiagnosticContext {
  const owned={expectedSceneRevision:value.expectedSceneRevision,expectedProjectHash:value.expectedProjectHash,
    ...(value.roofModelUid===undefined?{}:{roofModelUid:value.roofModelUid})}
  if(!checkRequest({requestId:'captured',diagnostics:{profile:'bounded-display-v1',...owned}}))throw new Error('Invalid bounded display context')
  return Object.freeze(owned)
}
export function diagnosticRequest(requestId:string,context:DiagnosticContext):string {
  const args={requestId,diagnostics:{profile:'bounded-display-v1',...captureDiagnosticContext(context)}}
  if(!checkRequest(args))throw new Error('Invalid bounded display request')
  const text=JSON.stringify(args)
  if(utf8.encode(text).byteLength>DIAGNOSTIC_REQUEST_BYTES)throw new Error('Bounded display request exceeds 1024 bytes')
  return text
}
/** Keep the raw Carb shape until this pending request is validated. Only its
 * known empty-list/None transport representation may be normalized. */
export function validateDiagnosticReply(value:unknown,context:DiagnosticContext):DiagnosticReply {
  const text=JSON.stringify(value,(_key,v)=>{
    if(typeof v==='number'&&!Number.isFinite(v))throw new Error('Nonfinite bounded display reply')
    return v
  })
  if(typeof text!=='string'||text.length>DIAGNOSTIC_REPLY_BYTES||utf8.encode(text).byteLength>DIAGNOSTIC_REPLY_BYTES)throw new Error('Bounded display reply exceeds 16384 bytes')
  const owned=JSON.parse(text) as Record<string,unknown>
  const rawView=owned.view
  if(!rawView||typeof rawView!=='object'||Array.isArray(rawView))throw new Error('Invalid raw display view')
  const view=rawView as Record<string,unknown>
  if(view.selectedUids&&typeof view.selectedUids==='object'&&!Array.isArray(view.selectedUids)&&Object.keys(view.selectedUids).length===0)view.selectedUids=[]
  if(!Object.prototype.hasOwnProperty.call(view,'timeSeconds'))view.timeSeconds=null
  if(!checkReply(owned))throw new Error('Invalid bounded display reply schema')
  if(JSON.stringify(view).length>4096||utf8.encode(JSON.stringify(view)).byteLength>4096)throw new Error('Bounded base view exceeds 4096 bytes')
  if(view.backgroundGridRaster!==undefined){
    const raster=copyGridRaster(view.backgroundGridRaster as BackgroundGridRaster)
    if(raster.binding.sceneRevision!==context.expectedSceneRevision||raster.binding.projectContentSha256!==context.expectedProjectHash)throw new Error('Stale bounded display raster')
    view.backgroundGridRaster=raster
  }
  const reply=owned as unknown as DiagnosticReply
  if(reply.sceneRevision!==context.expectedSceneRevision||reply.projectHash!==context.expectedProjectHash)throw new Error('Stale bounded display scene')
  if(context.roofModelUid!==undefined&&reply.diagnostics.roof&&
    typeof reply.diagnostics.roof==='object'&&'modelUid' in reply.diagnostics.roof&&
    (reply.diagnostics.roof as {modelUid:unknown}).modelUid!==context.roofModelUid)throw new Error('Wrong bounded display target')
  return reply
}
/** This tag only means one accepted UID is an actual floor construction. It
 * is not automatic roof recognition, and names/opacity are never evidence. */
export function selectedAcceptedFloor(project:unknown,selected:readonly string[],accepted:ReadonlySet<string>):string|undefined {
  if(selected.length!==1||!accepted.has(selected[0]!))return undefined
  const uid=selected[0]!
  const floors=(project as {constructions?:{floors?:Record<string,{uid?:string;kind?:string}>}}|null)?.constructions?.floors
  const floor=floors?.[uid]
  return floor?.uid===uid&&floor.kind==='floor'?uid:undefined
}
export function diagnosticReceipt(reply:DiagnosticReply):string {
  // The reply has already passed the independent exact whitelist and byte cap.
  const text=JSON.stringify({sceneRevision:reply.sceneRevision,projectHash:reply.projectHash,diagnostics:reply.diagnostics})
  if(utf8.encode(text).byteLength>DIAGNOSTIC_REPLY_BYTES)throw new Error('Bounded display receipt exceeds limit')
  return text
}
