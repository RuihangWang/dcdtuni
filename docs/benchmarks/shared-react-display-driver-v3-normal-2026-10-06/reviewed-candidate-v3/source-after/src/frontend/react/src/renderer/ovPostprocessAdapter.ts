/** OV realizes the same accepted numeric product, never chooses a run/time or
 * computes a second singleton slice/trace. Capability gaps remain explicit. */
import type { OvCommandClient } from './ovProtocol'
import type { DisplayApplyContext, DisplaySnapshot, PostprocessRendererAdapter } from '../postprocess/displaySession'
import { currentResultLayerContext, currentLiveAirResultSelection, currentResultLayerOwner } from './resultLayerRevision'
import { assertResultLayerCompatibility } from './resultLayers'
import { enqueueOvResultMutation, clearOwnedOvResultLayers, setOvResultStage } from './ovResultMutationQueue'
export interface AcknowledgedDisplayScene {projectUid:string;projectHash:string;sceneRevision:number}
export interface OvPostprocessAdapterOptions {
  client: Pick<OvCommandClient,'loadResultLayers'|'clearResultLayers'>
  sceneIdle(): Promise<void>
  scene(): AcknowledgedDisplayScene | null
}
export function createOvPostprocessAdapter(options: OvPostprocessAdapterOptions): PostprocessRendererAdapter {
  let closed = false
  function current(frame: DisplaySnapshot, context: DisplayApplyContext, scene: AcknowledgedDisplayScene) {
    const now = options.scene(), packet = frame.packet
    return !closed && context.isCurrent() && frame.isCurrent() && !!packet &&
      now?.projectUid === packet.source.projectUid && now.projectHash === packet.source.projectContentSha256 &&
      now.sceneRevision === scene.sceneRevision && now.projectHash === scene.projectHash
  }
  return {kind:'ov',async render(frame,context) {
    if (!frame.packet) return {status:'rejected',message:'OV requires the same accepted mesh/run/frame receipt as the shared result producer'}
    await options.sceneIdle()
    const scene = options.scene(), packet = frame.packet
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
    const ack = await enqueueOvResultMutation(options.client,()=>current(frame,context,scene)&&currentResultLayerOwner()==='air-postprocess'&&currentResultLayerContext()===accepted,
      ()=>options.client.loadResultLayers(packet,accepted.source,accepted.timeSeconds),'air-postprocess')
    if(!ack)return {status:'rejected',message:'OV result mutation was superseded before stage authoring'}
    if (!current(frame,context,scene)) return {status:'rejected',message:'OV result acknowledgement belongs to an older display generation'}
    if (ack.revision !== packet.revision) throw new Error('OV result revision mismatch')
    // ResultLayerSet transports every numeric surface/cut/trace list entry.
    // The current IndeX path cannot consume the common resampled grid or its
    // complete transfer function. Never silently replace these with demo defaults.
    const unsupported: string[] = []
    if(frame.appearance?.ao) unsupported.push('geometry depth enhancement')
    if(frame.appearance?.lightIntensity!==undefined&&frame.appearance.lightIntensity!==1) unsupported.push(`lighting ${frame.appearance.lightIntensity}`)
    if(frame.appearance?.modelOpacity!==undefined&&frame.appearance.modelOpacity!==1) unsupported.push(`model opacity ${frame.appearance.modelOpacity}`)
    if (frame.volume.visible && frame.volume.payload) unsupported.push(`volume grid ${frame.volume.field} (${frame.volume.unit}), ${frame.volume.resolution}³, ${frame.volume.palette}, opacity ${frame.volume.opacity}`)
    if (frame.layers.ring?.show) unsupported.push(`slice preview handle ${frame.handles.ringSetId}`)
    if (frame.layers.seed?.show) unsupported.push(`streamline seed handle ${frame.handles.seedSetId}`)
    return unsupported.length ? {status:'unsupported',message:`OV adapter conversion is pending for: ${unsupported.join('; ')}`} : {status:'displayed'}
  },async clear(context) {
    await options.sceneIdle()
    const scene=options.scene()
    if(!scene||closed||!context.isCurrent())return
    setOvResultStage(options.client,scene)
    await clearOwnedOvResultLayers(options.client,'air-postprocess',()=>!closed&&context.isCurrent()&&
      options.scene()?.sceneRevision===scene.sceneRevision&&currentResultLayerOwner()!=='mesh-preview',
      ()=>options.client.clearResultLayers())
  },dispose() {closed = true}}
}
