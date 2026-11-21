/** One display owner for both renderers. Numerical cuts/traces are made by the
 * existing postprocess producer; adapters only realize these products. */
import type { ColorMap } from './ppColormaps'
import type { PostprocessLayers } from './ppLayers'
import type { VolumePayload } from './ppVolume'
import { clearResultLayerSet, currentResultLayerSet } from '../renderer/resultLayerRevision'
import { publishWindResultFrame, type WindResultFrame } from '../renderer/windResultProducer'
import type { ResultLayerSet } from '../renderer/resultLayers'
import {commonRenderIntent,type CommonRenderIntent} from './commonRenderIntent'

export type DisplaySetId = number | 'draft'
export interface DisplayAppearance {modelOpacity:number;fieldOpacity:number;lightIntensity:number;ao:boolean;aoStrength:number;renderOrder:string[]}
const defaultAppearance = (): DisplayAppearance => ({modelOpacity:1,fieldOpacity:1,lightIntensity:1,ao:false,aoStrength:2,renderOrder:['geometry','surface','slice','streamline','volume']})
export interface VolumeIntent {
  field: string
  /** Raw numerical unit; display temperature conversion stays in the UI. */
  unit: string
  visible: boolean
  payload: VolumePayload | null
  range: [number, number]
  palette: ColorMap
  opacity: number
  resolution: number
}
export interface DisplayInput {
  layers: PostprocessLayers
  appearance?: DisplayAppearance
  windFrame: WindResultFrame | null
  volume: VolumeIntent
  internalVisible: boolean
  handles: { ringSetId: DisplaySetId; seedSetId: DisplaySetId }
  /** Check the CURRENT accepted selection and project, separately from payload claims. */
  isCurrent(): boolean
}
export interface DisplaySnapshot extends Omit<DisplayInput, 'windFrame'> {
  generation: number
  scopeKey: string
  packet: ResultLayerSet | null
  numericIntent?: CommonRenderIntent | null
  numericIntentError?: string
}
export type DisplayStatus = {
  status: 'idle' | 'pending' | 'displayed' | 'unsupported' | 'rejected'
  renderer: 'three' | 'ov'
  generation: number
  timeSeconds: number | null
  message: string
}
export type DisplayOutcome = Pick<DisplayStatus, 'status'> & { message?: string; interactionReady?:boolean }
export interface DisplayApplyContext { isCurrent(): boolean; generation?:number }
export interface PostprocessRendererAdapter {
  kind: 'three' | 'ov'
  render(snapshot: DisplaySnapshot, context: DisplayApplyContext): Promise<DisplayOutcome> | DisplayOutcome
  clear(context: DisplayApplyContext): Promise<void> | void
  /** Release adapter-owned tasks, never the shared transport or solver data. */
  dispose(): void
  retireInteraction?():void
  configureAppearance?(appearance: DisplayAppearance, context: DisplayApplyContext): void
}
export type DisplayInteraction = {kind: 'ring' | 'seed'; setId: DisplaySetId; position: [number, number, number]}
export type DisplayInteractionReadback = DisplayInteraction & {generation: number; scopeKey: string}

function validateVolume(volume: VolumeIntent) {
  if (typeof volume.visible !== 'boolean') throw new Error('Invalid shared volume visibility')
  if (typeof volume.opacity !== 'number' || !Number.isFinite(volume.opacity) || volume.opacity < 0 || volume.opacity > 1) throw new Error('Invalid shared volume opacity')
  if (!Number.isSafeInteger(volume.resolution) || volume.resolution < 2 || volume.resolution > 512) throw new Error('Invalid shared volume resolution')
  if (!Array.isArray(volume.range) || volume.range.length !== 2 || !volume.range.every(v => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= 3.4028234663852886e38) || volume.range[0] > volume.range[1]) throw new Error('Invalid shared volume range')
  if (!['coolwarm','jet','viridis'].includes(volume.palette)) throw new Error('Invalid shared volume palette')
  if (!volume.field || !volume.unit) throw new Error('Missing raw shared volume field/unit')
}

/** A latest replacement queue owns a display generation independently of run
 * revision. A late ACK cannot certify a later frame or a different adapter. */
export class PostprocessDisplayDriver {
  private frame: DisplaySnapshot | null = null
  private input: DisplayInput | null = null
  private appearance: DisplayAppearance = defaultAppearance()
  private fieldOpacityOverride: number | null = null
  private generation = 0
  private interactionGeneration = -1
  private adapter: PostprocessRendererAdapter
  private pending = false
  private busy = false
  private scheduled = false
  private lane = 0
  private localCompletion: Promise<void> | null = null
  private idleWaiters: (() => void)[] = []
  private listeners = new Set<() => void>()
  private interactionListeners = new Set<(event: DisplayInteraction) => void>()
  private state: DisplayStatus
  constructor(adapter: PostprocessRendererAdapter) {
    this.adapter = adapter
    this.state = {status:'idle',renderer:adapter.kind,generation:0,timeSeconds:null,message:''}
  }
  current() { return this.frame }
  status = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener);return () => {this.listeners.delete(listener)} }
  onInteraction(listener: (event: DisplayInteraction) => void) {
    this.interactionListeners.add(listener);return () => {this.interactionListeners.delete(listener)}
  }
  private statusChanged(status: DisplayStatus['status'], message = '') {
    if(status==='idle'||status==='pending'||status==='rejected')this.interactionGeneration=-1
    this.state = {status,message,renderer:this.adapter.kind,generation:this.generation,timeSeconds:this.frame?.packet?.timeSeconds ?? null}
    for (const listener of [...this.listeners]) listener()
  }
  private applied(outcome:DisplayOutcome) {
    this.interactionGeneration=outcome.interactionReady&&(outcome.status==='displayed'||outcome.status==='unsupported')?this.generation:-1
    this.statusChanged(outcome.status,outcome.message)
  }
  present(input: DisplayInput) {
    validateVolume(input.volume)
    if (!input.isCurrent()) throw new Error('Shared display input is no longer the current accepted frame')
    this.adapter.retireInteraction?.()
    // All receipt, session, raw-unit, physical-time and live-field checks stay
    // in the existing producer. A diagnostic frame cannot forge a packet.
    const opacity=this.fieldOpacityOverride??input.windFrame?.surface.opacity??input.layers.surface.opacity??this.appearance.fieldOpacity
    const windFrame=input.windFrame?{...input.windFrame,surface:{...input.windFrame.surface,opacity}}:null
    let packet: ResultLayerSet | null = null
    if (windFrame) { publishWindResultFrame(windFrame);packet = currentResultLayerSet() }
    else clearResultLayerSet('air-postprocess')
    let numericIntent:CommonRenderIntent|null=null,numericIntentError=''
    if(windFrame&&packet){try{numericIntent=commonRenderIntent(windFrame,packet.revision,input.volume)}catch(error){numericIntentError=error instanceof Error?error.message:String(error)}}
    this.input = input
    this.generation++
    this.frame = {layers:{...input.layers,surface:{...input.layers.surface,opacity}},appearance:{...(input.appearance??this.appearance),fieldOpacity:opacity,renderOrder:[...(input.appearance??this.appearance).renderOrder]},volume:{...input.volume,range:[...input.volume.range]},
      internalVisible:input.internalVisible,handles:{...input.handles},isCurrent:input.isCurrent,
      generation:this.generation,packet,numericIntent,numericIntentError,
      scopeKey:packet ? JSON.stringify([packet.source,input.windFrame!.session]) : `diagnostic/${this.generation}`}
    this.statusChanged('pending');this.queue()
    return this.frame
  }
  /** Mesh preview shares this presentation counter while retaining its public
   * MeshPanel session. Retire an older air input before its mesh carrier loads. */
  takeMeshDisplayOwnership():number {
    this.adapter.retireInteraction?.()
    this.generation++;this.frame=null;this.input=null;this.pending=false
    clearResultLayerSet('air-postprocess');this.statusChanged('idle')
    return this.generation
  }
  clear() {
    this.adapter.retireInteraction?.()
    this.generation++;this.frame = null;this.input = null
    clearResultLayerSet('air-postprocess')
    this.statusChanged('idle')
    this.queue()
  }
  activate(adapter: PostprocessRendererAdapter) {
    if (this.adapter === adapter) return
    this.adapter.dispose();this.adapter = adapter
    // Retire the old remote lane. Local replay never waits for a disconnected
    // renderer's ACK; its late completion has no access to the new lane.
    this.lane++;this.busy=false;this.pending=false;this.scheduled=false;this.localCompletion=null
    this.settleIdle()
    this.statusChanged(this.frame ? 'pending' : 'idle');this.queue()
  }
  configureAppearance(patch: Partial<DisplayAppearance>) {
    const next = {...this.appearance,...patch,renderOrder:[...(patch.renderOrder??this.appearance.renderOrder)]}
    if (![next.modelOpacity,next.fieldOpacity].every(v=>Number.isFinite(v)&&v>=0&&v<=1) ||
        !Number.isFinite(next.lightIntensity)||next.lightIntensity<0||next.lightIntensity>2 ||
        !Number.isFinite(next.aoStrength)||next.aoStrength<0||next.aoStrength>6) throw new Error('Invalid shared display appearance')
    this.appearance=next
    if(patch.fieldOpacity!==undefined)this.fieldOpacityOverride=patch.fieldOpacity
    if (this.input?.isCurrent()) {
      const input=this.input
      this.present({...input,appearance:next,layers:{...input.layers,surface:{...input.layers.surface,opacity:next.fieldOpacity}},
        windFrame:input.windFrame?{...input.windFrame,surface:{...input.windFrame.surface,opacity:next.fieldOpacity}}:null})
    } else {
      const adapter=this.adapter,generation=this.generation
      adapter.configureAppearance?.(next,{isCurrent:()=>adapter===this.adapter&&generation===this.generation})
    }
  }
  setInternalVisible(visible: boolean) {
    if(!this.input?.isCurrent())return
    const input=this.input
    this.present({...input,internalVisible:visible,windFrame:input.windFrame?{...input.windFrame,
      slices:input.windFrame.slices.map(layer=>({...layer,visible:visible&&input.layers.slices?.find(v=>v.id===layer.id)?.visible===true})),
      streamlines:input.windFrame.streamlines.map(layer=>({...layer,visible:visible&&input.layers.streamlines?.find(v=>v.id===layer.id)?.visible===true}))}:null})
  }
  interact(event: DisplayInteractionReadback): boolean {
    const frame = this.frame
    const id = event.kind === 'ring' ? frame?.handles.ringSetId : frame?.handles.seedSetId
    if (!frame || (this.state.status !== 'displayed' && !(this.state.status==='unsupported'&&this.interactionGeneration===frame.generation)) || event.generation !== frame.generation ||
        event.scopeKey !== frame.scopeKey || event.setId !== id || !frame.isCurrent() ||
        !event.position.every(Number.isFinite)) return false
    for (const listener of [...this.interactionListeners]) listener({kind:event.kind,setId:event.setId,position:[...event.position]})
    return true
  }
  whenIdle(): Promise<void> {
    if(this.adapter.kind==='three') return this.localCompletion ?? Promise.resolve()
    return this.busy || this.pending || this.scheduled ? new Promise(resolve => this.idleWaiters.push(resolve)) : Promise.resolve()
  }
  private settleIdle() {
    const waiters=this.idleWaiters;this.idleWaiters=[];waiters.forEach(resolve=>resolve())
  }
  private applyLocal() {
    this.pending=false
    const frame=this.frame,adapter=this.adapter,generation=this.generation,lane=this.lane
    const isCurrent=()=>lane===this.lane&&adapter===this.adapter&&generation===this.generation&&frame===this.frame&&(!frame||frame.isCurrent())
    if(!isCurrent()){this.statusChanged('rejected','The accepted display source changed');return}
    const finished=(outcome:DisplayOutcome|void)=>{if(isCurrent()&&outcome)this.applied(outcome)}
    const failed=(error:unknown)=>{if(isCurrent())this.statusChanged('rejected',error instanceof Error?error.message:String(error))}
    try {
      const result=frame?adapter.render(frame,{isCurrent,generation}):adapter.clear({isCurrent,generation})
      // Invoke the retained Three viewport immediately, even if an earlier local
      // test adapter returns a promise. Only current completion may update status.
      if(result instanceof Promise){
        const completion=result.then(finished,failed).finally(()=>{if(this.localCompletion===completion)this.localCompletion=null})
        this.localCompletion=completion
      }else{this.localCompletion=null;finished(result)}
    }catch(error){failed(error)}
  }
  private queue() {
    if(this.adapter.kind==='three'){this.applyLocal();return}
    this.pending = true
    if (this.busy || this.scheduled) return
    this.scheduled = true
    const lane=this.lane
    queueMicrotask(() => {if(lane!==this.lane)return;this.scheduled = false;void this.drain(lane)})
  }
  private async drain(lane:number) {
    if (this.busy || lane!==this.lane) return
    this.busy = true
    try {
      while (this.pending && lane===this.lane) {
        this.pending = false
        const frame = this.frame, adapter = this.adapter, generation = this.generation
        const isCurrent = () => lane===this.lane && adapter === this.adapter && generation === this.generation &&
          frame === this.frame && (!frame || frame.isCurrent())
        if (!isCurrent()) {if (lane===this.lane&&generation === this.generation) this.statusChanged('rejected','The accepted display source changed');continue}
        try {
          if (!frame) {await adapter.clear({isCurrent,generation});continue}
          const outcome = await adapter.render(frame,{isCurrent,generation})
          if (isCurrent()) this.applied(outcome)
        } catch (error) {
          if (isCurrent()) this.statusChanged('rejected',error instanceof Error ? error.message : String(error))
        }
      }
    } finally {
      if(lane===this.lane){
        this.busy = false
        if (this.pending) this.queue()
        else this.settleIdle()
      }
    }
  }
}
