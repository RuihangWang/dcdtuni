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
  primitives?:number; sceneRequestId?:string|null;
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
  // Carb omits None members. These two schema fields only ever mean that
  // inner content bounds are unresolved; restore null solely for that tag.
  const outputRecord=(owned.diagnostics as Record<string,unknown>|undefined)?.renderOutputObservations
  if(outputRecord&&typeof outputRecord==='object'&&!Array.isArray(outputRecord)) {
    const output=outputRecord as Record<string,unknown>
    for(const name of ['viewportWindow','widgetAssociation']) {
      const value=output[name]
      if(value&&typeof value==='object'&&!Array.isArray(value)) {
        const group=value as Record<string,unknown>
        if(group.status==='observed'&&group.innerContentProof==='unresolved'&&
           !Object.prototype.hasOwnProperty.call(group,'innerContentRect'))group.innerContentRect=null
      }
    }
  }
  if(utf8.encode(JSON.stringify(owned)).byteLength>DIAGNOSTIC_REPLY_BYTES)throw new Error('Bounded display reply exceeds 16384 bytes')
  if(!checkReply(owned)) {
    // Fixed schema paths only; never include wire values or native objects.
    const detail=(checkReply.errors??[]).slice(0,3).map(e=>`${e.keyword}:${e.instancePath.slice(0,256)}`).join(';')
    throw new Error(`Invalid bounded display reply schema (${detail})`)
  }
  const active=(owned.diagnostics as Record<string,unknown>)?.activeMaterialParameters
  if(active!==undefined)validateActiveMaterialParameters(active,owned,context)
  const output=(owned.diagnostics as Record<string,unknown>)?.renderOutputObservations
  if(output!==undefined)validateRenderOutputObservations(output,context)
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

/** Metadata only: actual delivered scene epoch/stream layout stay unresolved.
 * JSON schema is checked above; these extra checks bound UTF-8 bytes and the
 * pixel-count product, neither of which JSON Schema draft-07 can express. */
export const RENDER_OUTPUT_OBSERVATION_BYTES=6144
function validateRenderOutputObservations(value:unknown,context:DiagnosticContext):void {
  if(utf8.encode(JSON.stringify(value)).byteLength>RENDER_OUTPUT_OBSERVATION_BYTES)throw new Error('Bounded render output observation exceeds 6144 bytes')
  const v=value as Record<string,any>
  if(v.status!=='observed')return
  if(v.observationContext.sceneRevision!==context.expectedSceneRevision||v.observationContext.projectHash!==context.expectedProjectHash)throw new Error('Stale bounded render output context')
  for(const [name,field,cap] of [['viewportOwner','viewportId',128],['viewportOwner','usdContextName',128],['viewportOwner','cameraPath',512],['viewportOwner','renderProductPath',512],['widgetAssociation','viewportId',128],['widgetAssociation','name',128]] as const){
    const group=v[name]
    if(group.status==='observed'&&utf8.encode(group[field]).byteLength>cap)throw new Error('Bounded render output string exceeds byte limit')
  }
  for(const [name,field] of [['deliveredViewportFrame','resolution'],['viewportCanvas','resolution'],['viewportCanvas','fullResolution'],['applicationWindow','size'],['framebuffer','size']] as const){
    const group=v[name]
    if(group.status==='observed'&&group[field][0]*group[field][1]>134217728)throw new Error('Bounded render output dimensions exceed pixel budget')
  }
  if(v.widgetAssociation.status==='observed'&&v.viewportOwner.status==='observed'&&v.widgetAssociation.viewportId!==v.viewportOwner.viewportId)throw new Error('Wrong bounded render output viewport owner')
}


/** Optional material-backend arguments only; canonical ACK identity stays above.
 * Source paths must be exactly those of the independently validated roof graph.
 */
function validateActiveMaterialParameters(value:unknown,reply:Record<string,unknown>,context:DiagnosticContext):void {
  if(utf8.encode(JSON.stringify(value)).byteLength>6144)throw new Error('Bounded active material parameters exceed 6144 bytes')
  const v=value as Record<string,any>
  if(v.status!=='observed')return
  if(v.context.sceneRevision!==context.expectedSceneRevision||v.context.projectHash!==context.expectedProjectHash||
     context.roofModelUid===undefined||v.context.roofModelUid!==context.roofModelUid||v.context.primitives!==reply.primitives)throw new Error('Stale bounded active material context')
  const roof=(reply.diagnostics as Record<string,any>).roof
  if(roof.status!=='observed'||roof.modelUid!==context.roofModelUid||roof.materials.length!==v.records.length)throw new Error('Wrong bounded active material target')
  const key=(r:Record<string,any>)=>JSON.stringify([r.geometryPath,r.materialPath,r.surfacePath])
  const expected=new Set<string>(roof.materials.map(key)),seen=new Set<string>()
  for(const record of v.records){
    const identity=key(record)
    const native=record.sourceType==='mdl'
    const canonicalIdentity=native?key({...record,surfacePath:record.materialPath+'/Surface'}):identity
    if(record.modelUid!==context.roofModelUid||!expected.has(canonicalIdentity)||seen.has(canonicalIdentity)||
       (native&&record.surfacePath!==record.materialPath+'/FractionalSurface'))throw new Error('Wrong bounded active material binding')
    if(record.materialBackendFunctionName!==undefined&&utf8.encode(record.materialBackendFunctionName).byteLength>1024)throw new Error('Bounded active material function exceeds byte limit')
    seen.add(canonicalIdentity)
  }
}
