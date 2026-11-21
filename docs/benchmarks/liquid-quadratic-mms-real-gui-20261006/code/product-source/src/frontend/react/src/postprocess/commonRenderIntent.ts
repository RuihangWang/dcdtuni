/** Presentation intent of the SAME accepted common numeric responses.
 * This names backend-owned tokens; it never supplies numeric authority. */
import type {WindResultFrame} from '../renderer/windResultProducer'
import type {VolumeIntent} from './displaySession'
import {sameResultSource,type ResultSource,type ScalarLut,type RunConvergence} from '../renderer/resultLayers'
import type {OvDisplayGrant} from '../renderer/ovProtocol'
import {requireNumericProduct} from './numericProducts'
import {assertRevisionHeader,assertSessionIdentity,revisionHeaders,type PostprocessSession} from './ppRevision'
export interface CommonVolumeNumericIntent {field:string;unit:string;token:string;resolution:number;lut:ScalarLut;visible:boolean}
export interface CommonRenderIntent {
 schema:'dcdtuni.common-render-intent/v1';revision:number;postprocess:PostprocessSession;source:ResultSource;meshToken:string
 surface:{fieldToken:string;field:string;lut:ScalarLut;hidden:string[];visible:boolean}
 slices:Array<{setId:number;token:string;sourceUid:string;lut:ScalarLut;visible:boolean}>
 streamlines:Array<{setId:number;token:string;sourceUid:string;lut:ScalarLut;visible:boolean;tubeRadiusMeters:number}>
 volume:CommonVolumeNumericIntent|null
}
export function commonRenderIntent(frame:WindResultFrame,revision:number,volume?:VolumeIntent):CommonRenderIntent {
 const descriptor=frame.descriptor
 if(descriptor.schema!=='dcdtuni.verified-air-result-source/v2')throw new Error('The selected result has no verified native numeric source')
 const token=(object:object,kind:'mesh'|'field'|'slice'|'streamline'|'volume')=>requireNumericProduct(object,kind,frame.session,descriptor.nativeInputSha256)
 const lut=(value:{colorMap:ScalarLut['palette'];range:[number,number];opacity:number}):ScalarLut=>({palette:value.colorMap,range:[...value.range],opacity:value.opacity})
 let volumeIntent:CommonVolumeNumericIntent|null=null
 if(volume?.payload){
  if(descriptor.fieldUnits[volume.field]!==volume.unit)throw new Error('The common volume unit differs from the actual selected field')
  if(volume.payload.dimensions.some(n=>n!==volume.resolution))throw new Error('The common volume resolution differs from actual grid dimensions')
  volumeIntent={field:volume.field,unit:volume.unit,token:token(volume.payload,'volume'),resolution:volume.resolution,lut:{palette:volume.palette,range:[...volume.range],opacity:volume.opacity},visible:volume.visible}
 }else if(volume?.visible)throw new Error('The common visible volume has no actual numeric grid')
 const uid=(kind:string,id:number)=>`${frame.session.runId}/time-${frame.session.timeValue}/${kind}-${id}`
 return {schema:'dcdtuni.common-render-intent/v1',revision,postprocess:{...frame.session},source:structuredClone(descriptor.source),meshToken:token(frame.mesh,'mesh'),
  surface:{fieldToken:token(frame.surface.values,'field'),field:frame.surface.field,lut:lut(frame.surface),hidden:[...frame.surface.hidden].sort(),visible:frame.surface.visible!==false},
  slices:frame.slices.map(value=>({setId:value.id,token:token(value.payload,'slice'),sourceUid:uid('slice',value.id),lut:lut(value),visible:value.visible})),
  streamlines:frame.streamlines.map(value=>({setId:value.id,token:token(value.set,'streamline'),sourceUid:uid('seed',value.id),lut:lut(value),visible:value.visible,tubeRadiusMeters:value.tubeRadius})),volume:volumeIntent}
}
export interface CanonicalRenderReceipt {sha256:string;revision:number;source:ResultSource;timeSeconds:number;postprocess:PostprocessSession;sceneRevision:number;displayGeneration:number;runConvergence:RunConvergence}
export interface CanonicalRenderReply {grant:OvDisplayGrant;packetReceipt:CanonicalRenderReceipt}
export async function requestCanonicalRenderGrant(intent:CommonRenderIntent,scene:{projectUid:string;projectHash:string;sceneRevision:number},generation:number,signal?:AbortSignal):Promise<CanonicalRenderReply> {
 const response=await fetch('/api/pp/render-grant',{method:'POST',headers:revisionHeaders(intent.postprocess,true),signal,
  body:JSON.stringify({schema:'dcdtuni.canonical-render-request/v1',scene:{projectUid:scene.projectUid,projectContentSha256:scene.projectHash,revision:scene.sceneRevision},displayGeneration:generation,intent})})
 if(!response.ok){
  let message=String(response.status)
  try {
   const body=await response.json()as {error?:unknown;reason?:unknown}
   if(typeof body.error==='string'&&body.error.trim())message=body.error
   const reason=typeof body.reason==='string'?body.reason.replace(/\s+/g,' ').trim().slice(0,384):''
   if(reason)message+=` (${reason})`
  }catch{}
  throw new Error(`Result display source verification failed: ${message}`)
 }
 assertRevisionHeader(response,intent.postprocess)
 const reply=await response.json()as CanonicalRenderReply,receipt=reply.packetReceipt
 if(!receipt||!reply.grant||typeof reply.grant.signature!=='string'||!reply.grant.payload||! /^[0-9a-f]{64}$/.test(receipt.sha256)||receipt.revision!==intent.revision||receipt.timeSeconds!==intent.postprocess.timeValue||receipt.sceneRevision!==scene.sceneRevision||receipt.displayGeneration!==generation||!sameResultSource(receipt.source,intent.source))throw new Error('Result display receipt belongs to another source, frame or scene')
 assertSessionIdentity(receipt.postprocess,intent.postprocess)
 return reply
}
