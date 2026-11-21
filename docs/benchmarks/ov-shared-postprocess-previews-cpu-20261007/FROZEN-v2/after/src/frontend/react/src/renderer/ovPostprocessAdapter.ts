import {buildPreviewPacket,parsePreviewAck,parsePreviewEvent,previewOwnerKey,samePreviewValue,type PreviewBinding,type PreviewPacket,type PreviewAck} from './ovPostprocessPreviews'
/** OV realizes the same accepted numeric product, never chooses a run/time or
 * computes a second singleton slice/trace. Capability gaps remain explicit. */
import type { OvCommandClient } from './ovProtocol'
import {requestCanonicalRenderGrant,type CommonRenderIntent,type CanonicalRenderReply} from '../postprocess/commonRenderIntent'
import type { DisplayApplyContext, DisplaySnapshot, PostprocessRendererAdapter,DisplayInteractionReadback } from '../postprocess/displaySession'
import { currentResultLayerContext, currentLiveAirResultSelection, currentResultLayerOwner } from './resultLayerRevision'
import { assertResultLayerCompatibility,sameResultSource } from './resultLayers'
import { enqueueOvResultMutation, clearOwnedOvResultLayers, setOvResultStage } from './ovResultMutationQueue'
export interface AcknowledgedDisplayScene {projectUid:string;projectHash:string;sceneRevision:number}
export interface OvPostprocessAdapterOptions {
  client: Pick<OvCommandClient,'loadVerifiedResultLayers'|'clearResultLayers'> & Partial<Pick<OvCommandClient,'setCommonPostprocessPreviews'|'retireCommonPostprocessPreviews'|'subscribePostprocessPreviewEvents'>>
  requestGrant?(intent:CommonRenderIntent,scene:AcknowledgedDisplayScene,generation:number,signal:AbortSignal):Promise<CanonicalRenderReply>
  onInteraction?(event:DisplayInteractionReadback):boolean
  sceneIdle(): Promise<void>
  scene(): AcknowledgedDisplayScene | null
}
export function createOvPostprocessAdapter(options: OvPostprocessAdapterOptions): PostprocessRendererAdapter {
  let closed = false
  const controller=new AbortController(),ownerKey=previewOwnerKey()
  let pendingPreview:AbortController|null=null,lastBinding:PreviewBinding|null=null,retireError=''
  let retirement:Promise<void>|null=null
  let preview:{packet:PreviewPacket;ack:PreviewAck;frame:DisplaySnapshot;context:DisplayApplyContext;scene:AcknowledgedDisplayScene;accepted:NonNullable<ReturnType<typeof currentResultLayerContext>>;lastSequence:number}|null=null
  function retireInteraction() {
    pendingPreview?.abort();pendingPreview=null;preview=null
    const binding=lastBinding;lastBinding=null
    if(binding&&options.client.retireCommonPostprocessPreviews) {
      const task=options.client.retireCommonPostprocessPreviews(binding).then(ack=>{if(ack.retiredPostprocessPreviews!==true)throw new Error('OV preview retirement was not acknowledged')}).catch(error=>{retireError=error instanceof Error?error.message:String(error)})
      retirement=Promise.all([retirement??Promise.resolve(),task]).then(()=>{})
    }
  }
  const unsubscribe=options.client.subscribePostprocessPreviewEvents?.(raw=>{
    const event=parsePreviewEvent(raw),active=preview
    if(!event||!active||event.sequence<=active.lastSequence||event.leaseKey!==active.ack.leaseKey||
       !samePreviewValue(event.binding,active.packet.binding)||!current(active.frame,active.context,active.scene)||
       currentResultLayerOwner()!=='air-postprocess'||currentResultLayerContext()!==active.accepted||
       !sameResultSource(active.accepted.source,event.binding.source)||active.accepted.timeSeconds!==event.binding.timeSeconds||!active.packet.handles.some(h=>h.kind===event.kind&&h.setId===event.setId))return
    if(options.onInteraction?.({kind:event.kind,setId:event.setId,position:[...event.position],generation:event.binding.displayGeneration,scopeKey:event.binding.scopeKey}))active.lastSequence=event.sequence
  })
  function current(frame: DisplaySnapshot, context: DisplayApplyContext, scene: AcknowledgedDisplayScene) {
    const now = options.scene(), packet = frame.packet
    return !closed && context.isCurrent() && frame.isCurrent() && !!packet &&
      now?.projectUid === packet.source.projectUid && now.projectHash === packet.source.projectContentSha256 &&
      now.sceneRevision === scene.sceneRevision && now.projectHash === scene.projectHash
  }
  return {kind:'ov',async render(frame,context) {
    preview=null
    if(retirement)await retirement
    if(retireError)return {status:'rejected',message:`OV preview retirement failed: ${retireError}`}
    if (!frame.packet) return {status:'rejected',message:'OV requires the same accepted mesh/run/frame receipt as the shared result producer'}
    await options.sceneIdle()
    const acknowledgedScene=options.scene(),scene=acknowledgedScene?{...acknowledgedScene}:null,packet=frame.packet
    if (!scene || !current(frame,context,scene)) return {status:'rejected',message:'OV has not acknowledged this project display generation'}
    setOvResultStage(options.client,scene)
    const accepted = currentResultLayerContext(), selected = currentLiveAirResultSelection()
    if (!accepted || !selected || accepted.selection?.owner !== 'air-postprocess' ||
        accepted.selection.session.runId !== selected.session.runId ||
        accepted.selection.session.revision !== selected.session.revision ||
        accepted.selection.session.timeValue !== selected.session.timeValue ||
        selected.session.timeValue !== packet.timeSeconds ||
        accepted.selection.surfaceField !== selected.surfaceField ||
        selected.meshReceiptSha256 !== packet.source.meshReceiptSha256) {
      return {status:'rejected',message:'Shared postprocess selection changed before OV conversion'}
    }
    assertResultLayerCompatibility(packet,accepted.source,accepted.timeSeconds)
    if(!frame.numericIntent)return {status:'rejected',message:frame.numericIntentError||'The selected result has no owned numeric response receipts'}
    const reply=await(options.requestGrant??requestCanonicalRenderGrant)(frame.numericIntent,scene,frame.generation,controller.signal)
    if(!current(frame,context,scene)||currentResultLayerContext()!==accepted)return {status:'rejected',message:'Result display selection changed during source verification'}
    const ack = await enqueueOvResultMutation(options.client,()=>current(frame,context,scene)&&currentResultLayerOwner()==='air-postprocess'&&currentResultLayerContext()===accepted,
      ()=>options.client.loadVerifiedResultLayers(reply.grant),'air-postprocess')
    if(!ack)return {status:'rejected',message:'OV result mutation was superseded before stage authoring'}
    if (!current(frame,context,scene)) return {status:'rejected',message:'OV result acknowledgement belongs to an older display generation'}
    if(ack.revision!==packet.revision||ack.packetSha256!==reply.packetReceipt.sha256||ack.timeSeconds!==packet.timeSeconds||ack.sceneRevision!==scene.sceneRevision||ack.displayGeneration!==frame.generation||!sameResultSource(ack.source,packet.source))throw new Error('OV result acknowledgement differs from the verified source/frame')
    let interactionReady=false
    const supportsPreviews=!!(options.client.setCommonPostprocessPreviews&&options.client.retireCommonPostprocessPreviews&&options.client.subscribePostprocessPreviewEvents&&options.onInteraction)
    if(supportsPreviews) {
      const packet=buildPreviewPacket(frame,reply.packetReceipt,ownerKey),pending=new AbortController()
      pendingPreview=pending;lastBinding=packet.binding
      const abort=()=>pending.abort();controller.signal.addEventListener('abort',abort,{once:true})
      try {
        const previewReply=await options.client.setCommonPostprocessPreviews!(packet,pending.signal)
        if(!current(frame,context,scene)||currentResultLayerContext()!==accepted)return {status:'rejected',message:'Preview source changed before acknowledgement'}
        const ack=parsePreviewAck(previewReply.postprocessPreviews)
        if(!ack||!samePreviewValue(ack.packet,packet))throw new Error('OV preview acknowledgement differs from owned source/frame/handles')
        preview={packet,ack,frame,context,scene,accepted,lastSequence:0};interactionReady=packet.handles.length>0
      }finally{controller.signal.removeEventListener('abort',abort);if(pendingPreview===pending)pendingPreview=null}
    }
    // ResultLayerSet transports every numeric surface/cut/trace list entry.
    // The signed canonical product also carries the common nodal volume grid
    // and transfer function. Geometry appearance and interactive handles still
    // need separate carrier conversion.
    const unsupported: string[] = []
    if(frame.appearance?.ao) unsupported.push('geometry depth enhancement')
    if(frame.appearance?.lightIntensity!==undefined&&frame.appearance.lightIntensity!==1) unsupported.push(`lighting ${frame.appearance.lightIntensity}`)
    if(frame.appearance?.modelOpacity!==undefined&&frame.appearance.modelOpacity!==1) unsupported.push(`model opacity ${frame.appearance.modelOpacity}`)
    if (!supportsPreviews&&frame.layers.ring?.show) unsupported.push(`slice preview handle ${frame.handles.ringSetId}`)
    if (!supportsPreviews&&frame.layers.seed?.show) unsupported.push(`streamline seed handle ${frame.handles.seedSetId}`)
    return unsupported.length ? {status:'unsupported',message:`OV adapter conversion is pending for: ${unsupported.join('; ')}`,interactionReady} : {status:'displayed',interactionReady}
  },retireInteraction,async clear(context) {
    retireInteraction();if(retirement)await retirement
    await options.sceneIdle()
    const scene=options.scene()
    if(!scene||closed||!context.isCurrent())return
    setOvResultStage(options.client,scene)
    await clearOwnedOvResultLayers(options.client,'air-postprocess',()=>!closed&&context.isCurrent()&&
      options.scene()?.sceneRevision===scene.sceneRevision&&currentResultLayerOwner()!=='mesh-preview',
      ()=>options.client.clearResultLayers(context.generation))
  },dispose() {retireInteraction();closed = true;controller.abort();unsubscribe?.()}}
}
