/** Thin mesh carrier transport, using the same public preview selection/FIFO. */
import type { ResultLayerSet,ResultSource } from './resultLayers'
import {enqueueOvResultMutation}from './ovResultMutationQueue'
import {sameResultSource}from './resultLayers'
import type {OvCommandClient,RenderAck}from './ovProtocol'
import type { OvDisplayGrant } from './ovProtocol'
import type { VerifiedMeshPreviewDescriptor } from './windResultProducer'
export interface MeshPreviewIntent {schema:'dcdtuni.common-mesh-preview-intent/v1';revision:number;source:ResultSource;meshSnapshotKey:string;meshPayloadSha256:string;hidden:string[];opacity:number}
export interface MeshPreviewGrantReply {grant:OvDisplayGrant;packetReceipt:{sha256:string;revision:number;source:ResultSource;timeSeconds:null;meshPreview:{meshSnapshotKey:string;meshPayloadSha256:string;meshMetadataSha256:string};sceneRevision:number;displayGeneration:number}}
export async function assertMeshPreviewBytes(buffer:ArrayBuffer,descriptor:VerifiedMeshPreviewDescriptor):Promise<void>{
 if(descriptor.schema!=='dcdtuni.verified-mesh-preview-source/v2')return
 const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',buffer));const sha=Array.from(bytes,v=>v.toString(16).padStart(2,'0')).join('')
 if(sha!==descriptor.meshPayloadSha256)throw new Error('Loaded mesh preview bytes differ from current verified native mesh')
}
export async function requestMeshPreviewGrant(intent:MeshPreviewIntent,scene:{projectUid:string;projectHash:string;sceneRevision:number},generation:number):Promise<MeshPreviewGrantReply>{
 const response=await fetch('/api/mesh-preview/render-grant',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(60000),body:JSON.stringify({schema:'dcdtuni.canonical-mesh-preview-request/v1',scene:{projectUid:scene.projectUid,projectContentSha256:scene.projectHash,revision:scene.sceneRevision},displayGeneration:generation,intent})})
 const body=await response.json()
 if(!response.ok)throw new Error(String(body.error??'Current accepted mesh source is unavailable'))
 return body as MeshPreviewGrantReply
}
export function assertMeshPreviewGrantPacket(packet:ResultLayerSet,intent:MeshPreviewIntent){
 if(packet.schema!=='dcdtuni.render-result-layers/v4'||packet.source.run!==null||packet.timeSeconds!==null||packet.revision!==intent.revision||packet.meshPreview.meshSnapshotKey!==intent.meshSnapshotKey||packet.meshPreview.meshPayloadSha256!==intent.meshPayloadSha256)throw new Error('OV mesh preview requires a current canonical mesh-only intent')
}

/** Shared USD FIFO remains the sole transport owner for mesh and air. */
export async function transferMeshPreview(options:{packet:ResultLayerSet;intent:MeshPreviewIntent;scene:{projectUid:string;projectHash:string;sceneRevision:number};generation:number;client:Pick<OvCommandClient,'loadVerifiedResultLayers'>;isCurrent():boolean;requestGrant?:typeof requestMeshPreviewGrant}):Promise<RenderAck|undefined>{
 const {packet,intent,scene,generation,client,isCurrent}=options
 assertMeshPreviewGrantPacket(packet,intent)
 if(!isCurrent())return
 try {
  const reply=await(options.requestGrant??requestMeshPreviewGrant)(intent,scene,generation)
  const ack=await enqueueOvResultMutation(client,isCurrent,()=>client.loadVerifiedResultLayers(reply.grant),'mesh-preview')
  // An ACK/refusal for a retired hide/project generation cannot fail the new
  // session. The FIFO still records completed physical ownership for its clear.
  if(!isCurrent())return
  if(ack&&(ack.revision!==packet.revision||ack.packetSha256!==reply.packetReceipt.sha256||ack.timeSeconds!==null||ack.displayGeneration!==generation||ack.sceneRevision!==scene.sceneRevision||!sameResultSource(ack.source,packet.source)))throw new Error('Omniverse mesh source/revision acknowledgement mismatch')
  return ack
 }catch(error){if(!isCurrent())return;throw error}
}
