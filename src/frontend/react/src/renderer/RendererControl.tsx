import {nativeGridGeometrySynchronized,isTransientGridPending} from './nativeGridGeometryReceipt'
import { Icon } from '../components/Icon'
import {validateSourcePickReply,type SourcePickContext,type SourcePickRequest} from './sourceViewportPick'
import {isWholeObjectSelectGesture} from '../interaction/rackFaceGesture'
import {canSelectGeometryFromViewport} from '../interaction/picking'
import {isUiFrozen} from '../run/session'
import {captureDiagnosticContext,diagnosticReturnEnabled,diagnosticReceipt,selectedAcceptedFloor,type DiagnosticReply} from './ovDisplayDiagnostics'
import {DecodedRasterState,LatestViewDelivery,type DecodedRasterEvent,type BackgroundGridRaster} from './displayRaster'
import {sharedPostprocessDisplay} from '../postprocess/sharedDisplay'
import {transferMeshPreview,assertMeshPreviewGrantPacket} from './meshPreviewGrant'
import {useCallback,useEffect,useRef,useState,useSyncExternalStore,type ReactNode} from 'react'
import {createPortal} from 'react-dom'
import type * as THREE from 'three'
import type {CfdProject} from '../schema/cfdproj'
import * as store from '../projectStore'
import type {SceneBuilder} from '../interaction/sceneBuilder'
import {getViewportHandle,subscribeCameraSettled} from '../viewportBus'
import {resultOriginalMaterial,resultRenderRevision,selectedResultSample,selectedResultTime,subscribeResultSample} from '../liquid/liquidTempColor'
import {buildSceneSnapshot,captureViewState,type SceneSnapshot,type RenderViewState} from './sceneSnapshot'
import {restoreRenderCamera} from './viewState'
import type {SceneTransmission} from './ovSceneTransfer'
import {OvViewport} from './OvViewport'
import {sceneKeyOf,type OvCommandClient,type RenderAck} from './ovProtocol'
import {clearStaleResultLayerSet,currentLiveAirResultSelection,currentResultLayerContext,
  currentResultLayerOwner,currentResultLayerSet,resultLayerRevision,subscribeResultLayerRevision} from './resultLayerRevision'
import type {ResultLayerSet} from './resultLayers'
import {saveRenderScene} from './renderSceneExport'
import {cfdrunExport} from '../legacy/cfdrunBuild'
import {createOvEditBridge} from './ovEditBridge'
import {ovSession,setOvSession,subscribeOvSession} from './ovSession'
import {useSharedPostprocessRenderer} from './useSharedPostprocessRenderer'
import {clearOwnedOvResultLayers,setOvResultStage} from './ovResultMutationQueue'
import {getServerInfo,subscribeSession,type ServerInfo} from '../run/session'
import {getMeshPreviewSession,subscribeMeshPreview} from '../run/meshPreviewSession'
import {buildMeshPreviewOverlay,meshOverlayRevision,type RenderOverlayPacket} from './meshPreviewOverlay'
import {useTranslation} from '../useTranslation'
import type {TranslationKey} from '../i18n'
import type {ViewOptions} from '../components/Toolbar'
import {subscribeClosedCabinetDisplay,leafDisplayRevision,displaySceneKey} from '../liquid/closedCabinetDisplay'
import {readCasePath} from '../run/caseDir'
import {getLighting,subscribeLighting} from '../ui/lightingState'
import {OvDisplayDistributor,setOvDisplay,type DisplayChannelStatus} from './displayDistributor'
import {appOvLiquidColouring,startOvLiquidChannel} from '../liquid/ovLiquidDisplay'

/** At most one transfer plus one replaceable, not-yet-built snapshot. */
export class LatestSceneSync<Packet=SceneSnapshot> {
  private pending: {key:string; snapshot:()=>Packet}|null=null
  private timer:ReturnType<typeof setTimeout>|null=null
  private due=false
  private busy=false
  private closed=false
  private idleWaiters:(()=>void)[]=[]
  constructor(private load:(packet:Packet)=>Promise<unknown>,private failed:(error:Error)=>void,private acknowledgedKey='') {}
  /** Resolves once nothing is queued or in flight (or the sync closed): a
   * command sent during a scene transfer would wait behind it. */
  whenIdle():Promise<void>{return this.idle()?Promise.resolve():new Promise(resolve=>this.idleWaiters.push(resolve))}
  private idle(){return this.closed||(!this.busy&&!this.pending&&!this.timer)}
  private settleIdle(){if(this.idle()){const waiters=this.idleWaiters;this.idleWaiters=[];waiters.forEach(f=>f())}}
  offer(key:string,snapshot:()=>Packet) {
    if(this.closed)return
    if(this.timer)clearTimeout(this.timer)
    this.due=false
    this.pending={key,snapshot}
    this.timer=setTimeout(()=>{this.timer=null;this.due=true;void this.drain()},350)
  }
  isCurrent(key:string){return !this.closed&&!this.busy&&(!this.pending||this.pending.key===key)&&this.acknowledgedKey===key}
  close(){this.closed=true;this.pending=null;if(this.timer)clearTimeout(this.timer);this.timer=null;this.settleIdle()}
  private async drain() {
    if(this.closed||this.busy||!this.due||!this.pending){this.settleIdle();return}
    const next=this.pending
    this.pending=null
    if(next.key===this.acknowledgedKey){this.settleIdle();return}
    this.busy=true
    try {
      // Geometry, selected UIDs and time are captured together only when this job
      // actually starts. Superseded jobs never serialize their large textures.
      await this.load(next.snapshot())
      if(!this.closed)this.acknowledgedKey=next.key
    }catch(error){if(!this.closed)this.failed(error instanceof Error?error:new Error(String(error)))}
    finally{this.busy=false;if(!this.closed)void this.drain()}
  }
}

function currentMeshOverlayRevision(){return meshOverlayRevision(getMeshPreviewSession())}

/** A missing Kit reply must not keep the local viewport hidden for 45 seconds. */
export function waitForRenderView(promise:Promise<RenderAck>,timeoutMs=1000):Promise<RenderAck> {
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Omniverse view reply timed out')),timeoutMs)
    promise.then(value=>{clearTimeout(timer);resolve(value)},error=>{clearTimeout(timer);reject(error)})
  })
}

export function preferredOvEndpoint(info:Pick<ServerInfo,'ovSignalingHost'|'ovSignalingPort'>|null,
  browserHost:string):{host:string;port:number} {
  const configured=info?.ovSignalingHost?.trim()
  const port=info?.ovSignalingPort
  return {host:configured||browserHost,
    port:typeof port==='number'&&Number.isInteger(port)&&port>=1&&port<=65535?port:8301}
}

/** The endpoint the server advertises once its info has loaded; null before. */
export function advertisedOvEndpoint(info:Pick<ServerInfo,'ovSignalingHost'|'ovSignalingPort'>|null,
  browserHost:string):{host:string;port:number}|null {
  return info?preferredOvEndpoint(info,browserHost):null
}

/** A view reply belongs to one precisely acknowledged editor scene. */
export interface AcceptedRenderViewIdentity {
  projectUid:string
  projectHash:string|null
  sceneRevision:number
  /** Present for a camera transaction pinned to an accepted source packet. */
  primitives?:number
}
export function isAcceptedRenderView(ack:RenderAck,identity:AcceptedRenderViewIdentity):boolean {
  return identity.projectUid.trim().length>0&&typeof identity.projectHash==='string'
    &&/^[0-9a-f]{64}$/.test(identity.projectHash)
    &&Number.isSafeInteger(identity.sceneRevision)&&identity.sceneRevision>=0
    &&ack.ok===true&&ack.projectHash===identity.projectHash
    &&ack.sceneRevision===identity.sceneRevision
    &&(identity.primitives===undefined||(Number.isSafeInteger(identity.primitives)&&identity.primitives>=0
      &&Number.isSafeInteger(ack.primitives)&&ack.primitives===identity.primitives))
}
/** An acknowledged OV orbit survives a project edit; new time/selection still win. */
export function keepActiveOvCamera(packet:SceneSnapshot,ack:RenderAck,activeProjectUid:string,
  identity:AcceptedRenderViewIdentity):SceneSnapshot {
  if(packet.project.uid!==activeProjectUid||identity.projectUid!==activeProjectUid
    ||!isAcceptedRenderView(ack,identity)||!ack.view?.camera)return packet
  return {...packet,view:{...packet.view,camera:ack.view.camera}}
}

type Props={project:CfdProject|null;selectedUids:readonly string[];onSelection:(uids:string[])=>void;onPick?:(uid:string,toggle:boolean)=>void;
  activeTab:string;airMode:boolean;view?:Pick<ViewOptions,'showGrid'|'gridSpacingMm'>&Partial<Pick<ViewOptions,'lights'>>}
type LocalizedMessage={key?:TranslationKey;vars?:Record<string,string|number>;detail?:string}
type ResultTransmission={packet:ResultLayerSet|null;context:ReturnType<typeof currentResultLayerContext>}
export function currentResultTransmission(projectUid:string|null,ackedSceneHash:string|null):ResultTransmission {
  const packet=currentResultLayerSet(),context=currentResultLayerContext(),owner=currentResultLayerOwner()
  const empty={packet:null,context:null}
  const live=store.getProject()
  if(!packet||!context||!live?.uid||!projectUid||live.uid!==projectUid||
     packet.source.projectUid!==projectUid||context.source.projectUid!==projectUid)return empty
  let projectContentHash:string
  try {projectContentHash=cfdrunExport().projectContentHashSync(live)}catch{return empty}
  if(!ackedSceneHash||projectContentHash!==ackedSceneHash||
     packet.source.projectContentSha256!==projectContentHash||
     context.source.projectContentSha256!==projectContentHash||
     packet.timeSeconds!==context.timeSeconds)return empty
  if(owner==='air-postprocess') {
    const current=currentLiveAirResultSelection(),selected=context.selection
    if(!current||selected?.owner!=='air-postprocess'||
       current.session.runId!==selected.session.runId||
       current.session.revision!==selected.session.revision||
       current.session.timeValue!==selected.session.timeValue||
       current.session.timeValue!==packet.timeSeconds||
       current.surfaceField!==selected.surfaceField||
       current.meshReceiptSha256!==selected.meshReceiptSha256||
       current.meshReceiptSha256!==packet.source.meshReceiptSha256||
       !packet.source.run||
       !packet.layers.some(layer=>layer.kind==='surfaceField'&&layer.field.name===current.surfaceField))return empty
  }else if(owner==='mesh-preview') {
    const preview=getMeshPreviewSession(),selected=context.selection
    if(selected?.owner!=='mesh-preview'||!preview.visible||!preview.built||
       !preview.meshSnapshotKey||selected.meshSnapshotKey!==preview.meshSnapshotKey||
       packet.source.run!==null||packet.timeSeconds!==null)return empty
  }else return empty
  return {packet,context}
}
/** The view's lighting for Kit: the toolbar's on/off with the shared scale, preset and shadows. */
function currentLighting(view:Partial<Pick<ViewOptions,'lights'>>|null|undefined){
  const lit=getLighting()
  return {enabled:view?.lights!==false,scale:lit.scale,preset:lit.preset,shadows:lit.shadows}
}

export function RendererControl({project,selectedUids,onSelection,onPick,view,activeTab}:Props) {
  // Display controls remain owned by the common toolbar across both renderers.
  const viewRef=useRef(view);viewRef.current=view
  useSharedPostprocessRenderer()
  const {t}=useTranslation()
  const [toolbarBottom,setToolbarBottom]=useState(180)
  const [downloadFile,setDownloadFile]=useState<{url:string;name:string;warnings:string[];savedPath?:string}|null>(null)
  const [saving,setSaving]=useState(false)
  useEffect(()=>()=>{if(downloadFile)URL.revokeObjectURL(downloadFile.url)},[downloadFile])
  useEffect(()=>{
    const toolbar=document.getElementById('toolbar')
    if(!toolbar)return
    const update=()=>setToolbarBottom(toolbar.getBoundingClientRect().bottom)
    update()
    if(typeof ResizeObserver==='undefined')return
    const observer=new ResizeObserver(update);observer.observe(toolbar)
    return ()=>observer.disconnect()
  },[])
  // Keep the renderer row in Toolbar's mounted slot even when the toolbar
  // is collapsed. Only a view without a toolbar uses the viewport fallback.
  const [toolbarSlot,setToolbarSlot]=useState<HTMLElement|null>(null)
  useEffect(()=>{
    const toolbar=document.getElementById('toolbar')
    const slot=document.getElementById('renderer-switch-slot')
    if(toolbar&&slot)setToolbarSlot(slot)
  },[])
  const [mode,setMode]=useState<'three'|'ov'>('three')
  // Kit takes ~2 minutes to open its signalling port after the launcher
  // starts it. Poll the server's read-only probe so the toolbar can say
  // "starting" and connect once Kit listens, instead of failing early.
  const [ovStatus,setOvStatus]=useState<OvStatus|null>(null)
  const ovStatusRef=useRef(ovStatus);ovStatusRef.current=ovStatus
  const [waitingKit,setWaitingKit]=useState(false)
  const [ovFailed,setOvFailed]=useState(false)
  const [settingsOpen,setSettingsOpen]=useState(false)
  const settingsButton=useRef<HTMLButtonElement|null>(null)
  const settingsPanel=useRef<HTMLDivElement|null>(null)
  const bridgeConfigured=useSyncExternalStore(subscribeSession,()=>getServerInfo()?.ovBridge)
  useEffect(()=>{
    // Only a server started with --ov has a Kit to wait for; without one (or
    // before server-info loads) nothing is polled.
    if(bridgeConfigured===false)setOvStatus({bridge:false,kitListening:false})
    if(bridgeConfigured!==true)return
    let alive=true
    let timer:ReturnType<typeof setTimeout>|undefined
    const poll=async()=>{
      const next=await fetchOvStatus()
      if(!alive)return
      if(next)setOvStatus(next)
      timer=setTimeout(()=>void poll(),(next??ovStatusRef.current)?.kitListening?15000:3000)
    }
    void poll()
    return ()=>{alive=false;if(timer)clearTimeout(timer)}
  },[bridgeConfigured])
  const endpointInfo=useSyncExternalStore(subscribeSession,getServerInfo)
  const [server,setServer]=useState(()=>preferredOvEndpoint(getServerInfo(),location.hostname).host)
  const [port,setPort]=useState(()=>preferredOvEndpoint(getServerInfo(),location.hostname).port)
  const [customEndpoint,setCustomEndpoint]=useState(false)
  useEffect(()=>{
    const endpoint=customEndpoint?null:advertisedOvEndpoint(endpointInfo,location.hostname)
    if(!endpoint)return
    setServer(endpoint.host);setPort(endpoint.port)
  },[endpointInfo?.ovSignalingHost,endpointInfo?.ovSignalingPort,customEndpoint])
  const [message,setMessage]=useState<LocalizedMessage|null>(null)
  const [ready,setReady]=useState(false)
  const [returning,setReturning]=useState(false)
  const client=useRef<OvCommandClient|null>(null)
  const decodedRaster=useRef(new DecodedRasterState())
  const gridViewRevision=useRef(0)
  const lastGridReceipt=useRef<string|null>(null)
  const latestViewProducer=useRef<()=>void>(()=>{})
  const viewDelivery=useRef<LatestViewDelivery<RenderViewState,RenderAck>|null>(null)
  const sync=useRef<LatestSceneSync<SceneTransmission>|null>(null)
  const overlaySync=useRef<LatestSceneSync<RenderOverlayPacket>|null>(null)
  const resultSync=useRef<LatestSceneSync<ResultTransmission>|null>(null)
  /** The session's display distributor: owns the four queues above, the
   * post-processing and liquid channels, and their status. */
  const distributor=useRef<OvDisplayDistributor|null>(null)
  const closeDistributor=()=>{distributor.current?.close();distributor.current=null;setOvDisplay(null)}
  const sceneEpoch=useRef(0)
  const sceneRevision=useRef(-1)
  const acceptedPrimitiveCount=useRef(-1)
  const ackedSceneHash=useRef<string|null>(null)
  const acceptedModelUids=useRef<ReadonlySet<string>>(new Set())
  const sourceQueryBridge=useRef<ReturnType<typeof createOvEditBridge>|null>(null)
  const acceptedPickView=useRef<BackgroundGridRaster|null>(null)
  const sourcePickSequence=useRef(0)
  const pickHandler=useRef(onPick);pickHandler.current=onPick
  const activeTabRef=useRef(activeTab);activeTabRef.current=activeTab
  const selectionHandler=useRef(onSelection)
  selectionHandler.current=onSelection
  const degraded=useRef(false)
  const interactionAllowed=useRef(false)
  interactionAllowed.current=mode==='ov'&&ready&&!returning&&!degraded.current
  const projectUid=useRef<string|null>(project?.uid??null)
  projectUid.current=project?.uid??null
  const hasOverlay=useRef(false)
  const session=useRef(0)
  useSyncExternalStore(store.subscribe,store.revision)
  const sample=useSyncExternalStore(subscribeResultSample,selectedResultSample)
  const visualRevision=useSyncExternalStore(subscribeResultSample,resultRenderRevision)
  const displayRevision=useSyncExternalStore(subscribeClosedCabinetDisplay,leafDisplayRevision)
  const previewRevision=useSyncExternalStore(subscribeMeshPreview,currentMeshOverlayRevision)
  const layerRevision=useSyncExternalStore(subscribeResultLayerRevision,resultLayerRevision)
  const initialSent=useRef('')
  const sentProjectUid=useRef('')
  const timeSeconds=selectedResultTime()
  // While Kit colours liquid results itself, result recolouring (sample, time,
  // visual revision) must not resend the whole scene (~86 s on Shinaide):
  // the slider drives setLiquidFrame instead.
  // The result components are FROZEN at the value they had when Kit took over
  // (not dropped): changing the key's shape at the flip would itself resend
  // the scene, and a ~45 MB transfer queued ahead of setLiquidFrame starves it.
  const liquidActive=useSyncExternalStore(subscribeOvSession,()=>ovSession().liquidActive)
  const frozenResult=useRef<readonly [number,number|null,number]|null>(null)
  if(!liquidActive)frozenResult.current=null
  else if(!frozenResult.current)frozenResult.current=[sample,timeSeconds,visualRevision]
  const [keySample,keyTime,keyVisual]=frozenResult.current??[sample,timeSeconds,visualRevision]
  // Selection is view state, not scene content: it reaches Kit through
  // updateViewState below. Keyed here, every click resent the whole scene
  // (~40 MB, over a minute on Shinaide) and starved every other command.
  // Repaint notifications (logs/save) do not replace acknowledged scene content.
  // Hash the current source rather than its object identity: live edits may mutate it.
  // Only while OV shows the scene: the key keys nothing in Three, and this
  // pure-JS SHA-256 of the whole project ran on every live edit -- a sixth of
  // the main thread while dragging a select-all's opacity slider.
  const sourceHash=mode==='ov'&&project?.uid?cfdrunExport().projectContentHashSync(project):''
  const key=displaySceneKey(project?.uid??'',sourceHash,[keySample,keyTime,keyVisual],displayRevision)
  const canonicalMesh=currentResultLayerOwner()==='mesh-preview'&&currentResultLayerSet()?.schema==='dcdtuni.render-result-layers/v4'
  const overlayKey=JSON.stringify([project?.uid??'',previewRevision,canonicalMesh])
  const resultKey=JSON.stringify([project?.uid??'',sceneEpoch.current,layerRevision])
  const currentKey=useRef(key)
  currentKey.current=key
  const snapshot=useCallback(()=>{
    const live=window as unknown as {globalGroup?:THREE.Object3D;__sceneBuilder?:SceneBuilder}
    const handle=getViewportHandle()
    if(!project||!live.globalGroup||!handle)throw new Error(t('renderer.scene_not_ready'))
    return buildSceneSnapshot({root:live.globalGroup,project,camera:handle.camera,target:handle.controls.target,
      selectedUids,timeSeconds,backgroundGrid:viewRef.current,lighting:currentLighting(viewRef.current),helpers:live.__sceneBuilder?.registries.renderHelpers,
      materialOf:ovSession().liquidActive?resultOriginalMaterial:undefined})
  },[project,selectedUids,timeSeconds,t])
  const projectHash=useCallback(()=>{
    if(!project?.uid)throw new Error('No active project for Omniverse edit identity')
    return cfdrunExport().projectContentHashSync(project)
  },[project])
  function installEditBridge(value:OvCommandClient,ack:RenderAck,packet:SceneSnapshot,expectedHash=projectHash()) {
    const live=store.getProject()
    if(ack.projectHash!==expectedHash||
       !Number.isSafeInteger(ack.sceneRevision)||ack.sceneRevision===undefined||ack.sceneRevision<0) {
      throw new Error('Omniverse scene acknowledgement lacks the captured project identity')
    }
    // A correctly acknowledged older transfer cannot authorize the newer project.
    if(!live?.uid||live.uid!==packet.project.uid||
       cfdrunExport().projectContentHashSync(live)!==expectedHash)return false
    acceptedModelUids.current=new Set(packet.primitives.map(p=>p.modelUid))
    acceptedPrimitiveCount.current=packet.primitives.length
    sceneRevision.current=ack.sceneRevision
    ackedSceneHash.current=ack.projectHash
    setOvResultStage(value,{projectUid:packet.project.uid,projectHash:ack.projectHash,sceneRevision:ack.sceneRevision})
    setOvSession({projectUid:packet.project.uid,projectContentSha256:ack.projectHash,sceneRevision:ack.sceneRevision})
    const bridge=createOvEditBridge({snapshot:packet,sceneRevision:ack.sceneRevision,
      currentSceneRevision:()=>sceneRevision.current,
      // SceneSnapshot contains source geometry only. Result and mesh layers
      // live in separate USD subtrees and cannot supply these primitive IDs.
      pickablePrimitiveIds:new Set(packet.primitives.map(p=>p.id)),
      // Kit currently emits source picks only. Motion remains disabled until
      // model-anchor provenance and a real gizmo event source are verified.
      editablePrimitiveIds:new Set(),canEdit:()=>interactionAllowed.current,
      onSelection:uids=>selectionHandler.current(uids)})
    sourcePickSequence.current++;acceptedPickView.current=null
    sourceQueryBridge.current=createOvEditBridge({snapshot:packet,sceneRevision:ack.sceneRevision,
      currentSceneRevision:()=>sceneRevision.current,pickablePrimitiveIds:new Set(packet.primitives.map(p=>p.id)),
      editablePrimitiveIds:new Set(),canEdit:()=>sourcePickContext()!==null,
      onSelection:uids=>{const live=store.getProject(),uid=uids[0];if(!live||!uid)return
        const hit=store.findConstruction(live,uid),kind=hit?.bucket==='racks'?'rack':hit?.bucket==='cdus'?'cdu':hit?.bucket==='boxes'?'box':null
        const whole=isWholeObjectSelectGesture({shiftKey:true,ctrlKey:false,metaKey:false},kind)
        pickHandler.current?.(uid,!whole)}})
    value.onEditEvent(event=>{bridge.apply(event)})
    return true
  }
  const initialSnapshot=useCallback(()=>{
    const packet=snapshot()
    initialSent.current=key
    sentProjectUid.current=packet.project.uid
    return packet
  },[snapshot,key])
  function sourcePickContext():SourcePickContext|null {
    if(!pickHandler.current||!interactionAllowed.current||!canSelectGeometryFromViewport(activeTabRef.current,isUiFrozen())||hasOverlay.current
      ||!sync.current?.isCurrent(currentKey.current)||currentResultLayerSet())return null
    const raster=acceptedPickView.current,output=decodedRaster.current.read(),live=store.getProject()
    if(!raster||!output||!live?.uid||raster.binding.projectUid!==live.uid
      ||raster.binding.sceneRevision!==sceneRevision.current||raster.binding.projectContentSha256!==ackedSceneHash.current
      ||cfdrunExport().projectContentHashSync(live)!==ackedSceneHash.current
      ||raster.binding.viewRevision!==gridViewRevision.current||JSON.stringify(raster.output)!==JSON.stringify(output))return null
    return{binding:raster.binding,output,signalPort:port}
  }
  async function sourcePick(request:SourcePickRequest) {
    const active=client.current,context=sourcePickContext(),token=session.current,sequence=++sourcePickSequence.current
    if(!active||!context||!sourceQueryBridge.current)return
    const contextKey=JSON.stringify(context)
    try {const ack=await active.querySourcePick(request)
      if(session.current!==token||sourcePickSequence.current!==sequence||client.current!==active
        ||JSON.stringify(sourcePickContext())!==contextKey)return
      const reply=validateSourcePickReply(ack.sourcePickJSON,request)
      if(reply.status==='hit')sourceQueryBridge.current?.apply(reply.event)
      else if(reply.status==='refused')console.info('[dcdtuni:ov-source-pick]',JSON.stringify({phase:'refused',reason:reply.reason,measurements:reply.measurements}))
    }catch{/* A stale/closed/timed-out hit never changes common selection. */}
  }
  function failed(error:Error) {
    sourcePickSequence.current++;acceptedPickView.current=null;sourceQueryBridge.current=null
    sync.current?.close();sync.current=null;session.current+=1
    viewDelivery.current?.close();viewDelivery.current=null
    overlaySync.current?.close();overlaySync.current=null;hasOverlay.current=false
    resultSync.current?.close();resultSync.current=null
    closeDistributor()
    client.current?.onEditEvent(null);sceneRevision.current=-1;acceptedPrimitiveCount.current=-1;ackedSceneHash.current=null
    degraded.current=false
    setOvSession({client:null,ready:false,degraded:false,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})
    setMessage({key:getServerInfo()?.ovBridge===false?'renderer.ov_not_started':'renderer.ov_unavailable',vars:{error:error.message}})
    client.current=null;setReady(false);setReturning(false);setMode('three');setOvFailed(true)
  }
  /** Connect now, or wait for Kit when the launcher is still starting it. */
  function connectOv() {
    if(!project||returning||mode==='ov')return
    setOvFailed(false);setMessage(null)
    if(ovStatus?.bridge===false){setMessage({key:'renderer.ov_not_started',vars:{error:'--ov'}});setOvFailed(true);return}
    if(ovStatus?.bridge&&ovStatus.kitListening===false){setWaitingKit(true);return}
    setWaitingKit(false);session.current+=1;setMode('ov')
  }
  useEffect(()=>{
    if(!waitingKit||!ovStatus?.kitListening||!project)return
    setWaitingKit(false);setMessage(null);session.current+=1;setMode('ov')
  },[waitingKit,ovStatus?.kitListening,project])
  // Fetch and evaluate the streaming SDK (724 KB) once Kit is up, so choosing
  // OV starts connecting at once instead of loading it first.
  useEffect(()=>{if(ovStatus?.kitListening)void import('@nvidia/omniverse-webrtc-streaming-library').catch(()=>{})},[ovStatus?.kitListening])
  async function returnToThree() {
    if(returning)return
    setReturning(true)
    // This decoration status belongs to the retiring OV viewport. Keep actual
    // fallback/export errors, but never show its pending grid over Three.
    setMessage(previous=>previous?.key==='renderer.grid_waiting'||previous?.key==='renderer.grid_unavailable'?null:previous)
    sourcePickSequence.current++;acceptedPickView.current=null
    const token=++session.current
    const sourceKey=key
    const capturedClient=client.current
    const acceptedView={projectUid:sentProjectUid.current,projectHash:ackedSceneHash.current,sceneRevision:sceneRevision.current,primitives:acceptedPrimitiveCount.current}
    let diagnosticContext:ReturnType<typeof captureDiagnosticContext>|null=null
    let diagnosticCaptureError:unknown=null
    try {
      if(diagnosticReturnEnabled&&sceneRevision.current>=0&&ackedSceneHash.current)
        diagnosticContext=captureDiagnosticContext({expectedSceneRevision:sceneRevision.current,expectedProjectHash:ackedSceneHash.current,
          roofModelUid:selectedAcceptedFloor(project,selectedUids,acceptedModelUids.current)})
    }catch(error){diagnosticCaptureError=error}
    // If a new project/frame has not reached Kit yet, its camera/selection reply
    // belongs to the previous scene. Preserve the newer local state instead.
    const canRead=!!sync.current?.isCurrent(key)&&acceptedView.projectUid===projectUid.current
    sync.current?.close();sync.current=null
    overlaySync.current?.close();overlaySync.current=null;hasOverlay.current=false
    resultSync.current?.close();resultSync.current=null
    client.current?.onEditEvent(null);sceneRevision.current=-1;acceptedPrimitiveCount.current=-1;ackedSceneHash.current=null
    degraded.current=false
    try {
      if(diagnosticCaptureError)throw diagnosticCaptureError
      if(client.current&&ready&&canRead) {
        // Healthy Kit replies can take several seconds; keep one bounded handoff.
        const ack=await waitForRenderView(diagnosticContext
          ?capturedClient!.readViewDiagnostics(diagnosticContext):capturedClient!.readView("camera-handoff"),diagnosticContext?3000:8000)
        if(session.current!==token||currentKey.current!==sourceKey||client.current!==capturedClient
          ||ack.view?.timeSeconds!==timeSeconds||selectedResultTime()!==timeSeconds)return
        const liveProject=store.getProject()
        if(!liveProject||liveProject.uid!==acceptedView.projectUid
          ||cfdrunExport().projectContentHashSync(liveProject)!==acceptedView.projectHash)return
        if(!isAcceptedRenderView(ack,acceptedView))throw new Error('Omniverse view belongs to another acknowledged scene')
        if(diagnosticContext) {
          const live=store.getProject()
          if(!live||cfdrunExport().projectContentHashSync(live)!==diagnosticContext.expectedProjectHash||
             ack.sceneRevision!==diagnosticContext.expectedSceneRevision||ack.projectHash!==diagnosticContext.expectedProjectHash)return
          console.info('[OV bounded readonly receipt]',diagnosticReceipt(ack as DiagnosticReply))
          setMessage({detail:'OV readback captured; pixel/layout evidence remains unverified.'})
        }
        const handle=getViewportHandle()
        const root=(window as unknown as {globalGroup?:THREE.Object3D}).globalGroup
        if(ack.view?.camera&&handle&&root) {
          restoreRenderCamera(ack.view.camera,root,handle.camera,handle.controls.target)
          handle.controls.update()
        }
        if(ack.view)onSelection(ack.view.selectedUids)
      }
    }catch(error){if(session.current===token)setMessage({key:'renderer.local_view_fallback',vars:{error:String(error)}})}
    finally {if(session.current===token){viewDelivery.current?.close();viewDelivery.current=null;closeDistributor();client.current=null;setReady(false);setMode('three');setReturning(false);setOvSession({client:null,ready:false,degraded:false,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})}}
  }
  useEffect(()=>{
    if(mode==='ov'&&ready&&!returning)sync.current?.offer(key,()=>({packet:snapshot(),hash:projectHash()}))
    // The queue replaces its pending job immediately on every edit; its own
    // debounce controls when to start. React cleanup must not cancel an active job.
  },[key,mode,ready,snapshot,projectHash,returning])
  useEffect(()=>{
    if(mode!=='ov'||!ready||returning||!project?.uid)return
    const preview=getMeshPreviewSession()
    if(!preview.built&&!hasOverlay.current)return
    hasOverlay.current=!!preview.built
    const projectUid=project.uid
    overlaySync.current?.offer(overlayKey,()=>{const preview=getMeshPreviewSession();return buildMeshPreviewOverlay(projectUid,canonicalMesh?{...preview,built:null}:preview)})
  },[overlayKey,mode,ready,returning,project?.uid])
  useEffect(()=>{
    if(mode!=='ov'||!ready||returning)return
    resultSync.current?.offer(resultKey,()=>currentResultTransmission(projectUid.current,ackedSceneHash.current))
  },[resultKey,mode,ready,returning])
  // Camera linkage: the view cube, toolbar presets and finished orbit gestures
  // move the retained Three camera; in OV mode the same view state must reach
  // Kit or the cube appears disconnected from the re-render (user-reported).
  // updateViewState is a small command outside the scene-sync queue: it must
  // not re-send geometry, and a dead ack channel must not break navigation.
  const pushView=useCallback(()=>{
    if(mode!=='ov'||!ready||returning||degraded.current)return
    const active=client.current
    const viewport=getViewportHandle()
    const root=(window as unknown as {globalGroup?:THREE.Object3D}).globalGroup
    if(!active||!viewport||!root)return
    try{
      const line=viewport.readLineRaster?.(), output=decodedRaster.current.read()
      const hash=ackedSceneHash.current
      // A project edit may be waiting for a scene ACK. Its new observation
      // cannot be bound to the still-accepted old scene/source.
      if(!hash||projectHash()!==hash||ovSession().projectUid!==project?.uid)return
      const raster:BackgroundGridRaster|undefined=line&&output&&project?.uid&&hash&&sceneRevision.current>=0
        ?{metricVersion:2,line,output,binding:{projectUid:project.uid,projectContentSha256:hash,sceneRevision:sceneRevision.current,viewRevision:++gridViewRevision.current}}:undefined
      const view=captureViewState({root,project:project??{uid:''},camera:viewport.camera,
        target:viewport.controls.target,selectedUids,timeSeconds:selectedResultTime(),backgroundGrid:viewRef.current,
        lighting:currentLighting(viewRef.current),backgroundGridRaster:raster})
      viewDelivery.current?.offer(view)
    }catch{/* navigation must never throw into the orbit controls */}
  },[mode,ready,returning,project,selectedUids,projectHash])
  latestViewProducer.current=pushView
  const onDecodedRaster=useCallback((event:DecodedRasterEvent)=>{
    if(decodedRaster.current.accept(event))pushView()
  },[pushView])
  useEffect(()=>subscribeCameraSettled(pushView),[pushView])
  useEffect(()=>{pushView()},[selectedUids]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{if(viewRef.current)pushView()},[view?.showGrid,view?.gridSpacingMm,view?.lights,mode,ready,returning]) // eslint-disable-line react-hooks/exhaustive-deps
  // The lighting is one state for both renderers: a change in the top bar or
  // the post-processing slider reaches Kit's rig through the same view push.
  useEffect(()=>subscribeLighting(()=>latestViewProducer.current?.()),[])
  useEffect(()=>()=>{viewDelivery.current?.close();sync.current?.close();overlaySync.current?.close();resultSync.current?.close();closeDistributor();session.current+=1},[])
  useEffect(()=>{setOvSession({mode})},[mode])
  async function download() {
    const capturedKey=currentKey.current
    const caseDir=readCasePath()
    let generated:LocalizedMessage|null=null
    try {
      const packet=snapshot()
      const blob=new Blob([JSON.stringify(packet)],{type:'application/json'})
      const url=URL.createObjectURL(blob),a=document.createElement('a')
      const frame=packet.view.timeSeconds===null?'':`-t${packet.view.timeSeconds}s`
      const name=`${packet.project.name||'project'}${frame}.renderscene.json`
      setDownloadFile({url,name,warnings:packet.warnings})
      a.href=url;a.download=name;a.click()
      generated={key:'renderer.generated',vars:{primitives:packet.primitives.length,textures:Object.keys(packet.textures).length}}
      setMessage(generated)
      if(typeof caseDir==='string'&&caseDir.trim()) {
        setSaving(true)
        const receipt=await saveRenderScene(packet,caseDir)
        if(currentKey.current===capturedKey) {
          setDownloadFile(file=>file?.url===url?{...file,savedPath:receipt.path}:file)
          setMessage({key:'renderer.saved',vars:{time:packet.view.timeSeconds===null?'':` · ${packet.view.timeSeconds} s`}})
        }
      }
    }catch(error){if(currentKey.current===capturedKey)setMessage({...generated,detail:String(error)})}
    finally{setSaving(false)}
  }
  const inBar=Boolean(toolbarSlot)
  const pill=ovPillState({mode,ready,waitingKit,failed:ovFailed,status:ovStatus})
  const switchRow=<div className="renderer-switch-row" style={{pointerEvents:'auto'}}>
    <label className="renderer-selector-label"><span className="renderer-selector-caption">{t('renderer.label')}</span> <select aria-label={t('renderer.label')} value={mode==='ov'||waitingKit?'ov':'three'} disabled={!project||returning}
      onChange={e=>{if(e.target.value==='three'){if(waitingKit)setWaitingKit(false);else void returnToThree()}else connectOv()}}>
      <option value="three">Three.js</option><option value="ov">Omniverse</option>
    </select></label>
    <button type="button" className={`ov-status ov-status-${pill}`} data-state={pill}
      title={t(`renderer.ov_state_${pill}_hint` as TranslationKey,{port})} aria-label={t(`renderer.ov_state_${pill}` as TranslationKey)}
      disabled={!project||pill==='off'||pill==='connected'||pill==='connecting'||returning}
      onClick={()=>{if(pill==='waiting')setWaitingKit(false);else connectOv()}}>
      <span className="ov-status-dot" aria-hidden="true"/>{t(`renderer.ov_state_${pill}` as TranslationKey)}
    </button>
    <button type="button" ref={settingsButton} className={inBar?'tool-btn renderer-settings-btn':'btn renderer-settings-btn'} aria-expanded={settingsOpen}
      aria-label={t('renderer.connection')} title={t('renderer.connection')} onClick={()=>setSettingsOpen(v=>!v)}><Icon name="settings"/>{t('renderer.settings')}</button>
    <button className={inBar?'tool-btn':'btn'} title={t('renderer.export')} aria-label={t('renderer.export')} style={inBar?undefined:{width:'auto',margin:0,padding:'4px 10px'}} disabled={!project||saving} onClick={()=>void download()}><Icon name="export"/>{inBar?t('export.confirm'):t('renderer.export')}</button>
  </div>
  const settingsPopover=settingsOpen&&createPortal(<OvSettingsPopover anchor={settingsButton.current} panelRef={settingsPanel}
    onClose={()=>setSettingsOpen(false)}>
    <div className="renderer-connection-title">{t('renderer.connection')}</div>
    <label>{t('renderer.host')} <input aria-label={t('renderer.host_aria')} value={server} disabled={mode==='ov'} onChange={e=>{setCustomEndpoint(true);setServer(e.target.value)}}/></label>
    <label>{t('renderer.port')} <input aria-label={t('renderer.port_aria')} type="number" min={1} max={65535} value={port} disabled={mode==='ov'} onChange={e=>{setCustomEndpoint(true);setPort(Number(e.target.value))}}/></label>
    <p className={`renderer-connection-state ov-status-${pill}`}><span className="ov-status-dot" aria-hidden="true"/>{t(`renderer.ov_state_${pill}_hint` as TranslationKey,{port})}</p>
    <small>{t('renderer.connection_hint')}</small>
  </OvSettingsPopover>,document.body)
  return <>
    {toolbarSlot&&createPortal(switchRow,toolbarSlot)}
    {settingsPopover}
    <div className="renderer-controls" style={{position:'absolute',top:toolbarBottom+8,left:'calc(var(--left-panel-w) + 12px)',right:'calc(var(--right-panel-w) + 12px)',zIndex:8,pointerEvents:'none'}}>
      {!toolbarSlot&&switchRow}
      <div style={{display:'flex',gap:6,alignItems:'center',flexWrap:'wrap',pointerEvents:'auto'}}>
        {downloadFile&&<a href={downloadFile.url} download={downloadFile.name}>{t('renderer.download')}</a>}
        {downloadFile&&<details><summary>{t('renderer.export_details')}</summary><div style={{background:'#20252d',padding:8,fontSize:11,maxWidth:280,overflowWrap:'anywhere'}}>
          <div>{downloadFile.savedPath??downloadFile.name}</div>
          {downloadFile.warnings.map((warning,index)=><p key={index}>{warning}</p>)}
        </div></details>}
      </div>
      {message&&<div role="status" style={{background:'#20252d',padding:8,marginTop:6,pointerEvents:'auto',fontSize:12}}>{message.key?t(message.key,message.vars):''}{message.detail??''}</div>}

    </div>
    {mode==='ov'&&<OvViewport server={server} signalingPort={port} snapshot={initialSnapshot} handoffSnapshot={snapshot} onDecodedRaster={onDecodedRaster} sourcePickContext={sourcePickContext} onSourcePick={request=>void sourcePick(request)}
      projectHash={projectHash} onReady={(value,initialAck,initialPacket)=>{
        if(!initialAck){
          // Degraded display: video live, acknowledgement channel dead. Show the
          // unconfirmed re-render behind a banner; suspend everything that needs
          // the ack (edit bridge, live re-sync, camera read-back, result layers).
          degraded.current=true
          closeDistributor()
          client.current=value
          setOvSession({client:value,ready:true,degraded:true})
          setMessage({key:'renderer.ov_unconfirmed'})
          setReady(true)
          return
        }
        degraded.current=false
        setOvSession({client:value,ready:true,degraded:false,sceneIdle:()=>sync.current?.whenIdle()??Promise.resolve()})
        installEditBridge(value,initialAck,initialPacket)
        clearStaleResultLayerSet(initialPacket.project.uid,initialAck.projectHash!)
        client.current=value
        const token=session.current
        closeDistributor()
        const display=new OvDisplayDistributor(value)
        distributor.current=display;setOvDisplay(display)
        display.delivered('scene')  // the first scene came with the connection
        viewDelivery.current?.close()
        viewDelivery.current=display.own('view',new LatestViewDelivery(display.track('view',async(view:RenderViewState)=>{
          let ack=await value.updateView(view)
          const revision=view.backgroundGridRaster?.binding.viewRevision
          // Cold RTX startup can outlast two seconds of live video.
          // Observe this current receipt; do not resend/reset the view.
          const frameDeadline=performance.now()+240_000
          for(let attempt=0;attempt<240&&performance.now()<frameDeadline&&isTransientGridPending(ack.gridDisplay)
            &&revision!==undefined
            &&session.current===token&&gridViewRevision.current===revision;attempt++){
            await new Promise(resolve=>setTimeout(resolve,1000))
            if(session.current!==token||gridViewRevision.current!==revision||performance.now()>=frameDeadline)break
            ack=await value.readView()
          }
          return ack
        }),(view,ack)=>{
          if(session.current!==token||client.current!==value)return
          const expected=view.backgroundGridRaster?.binding
          if(expected&&(ack.sceneRevision!==expected.sceneRevision||ack.projectHash!==expected.projectContentSha256
            ||JSON.stringify(ack.view?.backgroundGridRaster?.binding)!==JSON.stringify(expected))){
            setMessage({key:'renderer.grid_unavailable'});return
          }
          // One bounded metadata receipt per phase/reason change; no scene,
          // texture, project payload or session token is logged.
          const grid=ack.gridDisplay
          const gridKey=JSON.stringify([token,grid?.phase,grid?.reason])
          if(grid&&lastGridReceipt.current!==gridKey){
            lastGridReceipt.current=gridKey
            console.info('[dcdtuni:ov]',JSON.stringify({phase:'grid-display',
              gridPhase:grid.phase,reason:grid.reason,viewRevision:grid.viewRevision}))
          }
          if(ack.ok&&expected)acceptedPickView.current=view.backgroundGridRaster??null
          const liveProject=store.getProject()
          const nativeGeometryCurrent=!!expected&&!!liveProject
            &&liveProject.uid===expected.projectUid&&projectUid.current===expected.projectUid
            &&ackedSceneHash.current===expected.projectContentSha256
            &&sceneRevision.current===expected.sceneRevision
            &&cfdrunExport().projectContentHashSync(liveProject)===expected.projectContentSha256
            &&nativeGridGeometrySynchronized(ack,view)
          if(nativeGeometryCurrent)
            setMessage(previous=>previous?.key==='renderer.grid_waiting'||previous?.key==='renderer.grid_unavailable'?null:previous)
          else if(ack.gridDisplay?.phase==='pending')setMessage({key:'renderer.grid_waiting'})
          else if(ack.gridDisplay?.phase==='unsupported'||ack.gridDisplay?.phase==='rejected')setMessage({key:'renderer.grid_unavailable'})
          else if(ack.gridDisplay?.phase==='rendered'||ack.gridDisplay?.phase==='hidden')
            setMessage(previous=>previous?.key==='renderer.grid_waiting'||previous?.key==='renderer.grid_unavailable'?null:previous)
        },()=>{if(session.current===token)setMessage({key:'renderer.grid_unavailable'})}))

        sync.current?.close()
        sync.current=display.own('scene',new LatestSceneSync<SceneTransmission>(display.track('scene',async ({packet,hash}:SceneTransmission)=>{
          let next=packet
          if(packet.project.uid===sentProjectUid.current){
            const acceptedView={projectUid:sentProjectUid.current,projectHash:ackedSceneHash.current,sceneRevision:sceneRevision.current,primitives:acceptedPrimitiveCount.current}
            const ack=await waitForRenderView(value.readView("camera-handoff"),3000)
            if(session.current!==token||client.current!==value)return
            if(sentProjectUid.current===acceptedView.projectUid
              &&ackedSceneHash.current===acceptedView.projectHash&&sceneRevision.current===acceptedView.sceneRevision)
              next=keepActiveOvCamera(packet,ack,acceptedView.projectUid,acceptedView)
          }
          if(session.current!==token||client.current!==value)return
          const ack=await value.loadScene(next,hash,await sceneKeyOf(next))
          if(installEditBridge(value,ack,next,hash)){
            clearStaleResultLayerSet(next.project.uid,ack.projectHash!)
            // Scene replacement resets the derived carrier. Rebind the latest
            // live raster to this ACK even if no camera/decoder size changed.
            latestViewProducer.current()
          }
          sentProjectUid.current=next.project.uid
          sceneEpoch.current+=1
          resultSync.current?.offer(JSON.stringify([next.project.uid,sceneEpoch.current,resultLayerRevision()]),
            ()=>currentResultTransmission(projectUid.current,ackedSceneHash.current))
        }),error=>{if(session.current===token)failed(error)},initialSent.current))
        overlaySync.current?.close()
        overlaySync.current=display.own('overlay',new LatestSceneSync<RenderOverlayPacket>(
          display.track('overlay',(packet:RenderOverlayPacket)=>value.loadOverlay(packet)),error=>{if(session.current===token)failed(error)},
        ))
        resultSync.current?.close()
        resultSync.current=display.own('result',new LatestSceneSync<ResultTransmission>(display.track('result',async (transmission:ResultTransmission)=>{
          // Air results are delivered by the common postprocess adapter. This
          // retained queue owns diagnostic mesh-preview packets only.
          const {packet,context}=transmission
          if(currentResultLayerOwner()==='air-postprocess')return
          if(!packet||!context){
            const epoch=sceneEpoch.current,hash=ackedSceneHash.current
            await clearOwnedOvResultLayers(value,'mesh-preview',()=>session.current===token&&
              sceneEpoch.current===epoch&&ackedSceneHash.current===hash&&
              currentResultLayerOwner()!=='air-postprocess'&&
              !currentResultTransmission(projectUid.current,ackedSceneHash.current).packet,
              ()=>value.clearResultLayers(sharedPostprocessDisplay.takeMeshDisplayOwnership()))
            return
          }
          if(currentResultLayerOwner()!=='mesh-preview')return
          if(context.selection?.owner!=='mesh-preview'||!context.selection.intent||ackedSceneHash.current===null)throw new Error('Current mesh preview lacks a verified canonical native source')
          const intent=context.selection.intent
          assertMeshPreviewGrantPacket(packet,intent)
          const generation=sharedPostprocessDisplay.takeMeshDisplayOwnership()
          const scene={projectUid:packet.source.projectUid,projectHash:ackedSceneHash.current,sceneRevision:sceneRevision.current}
          const isCurrent=()=>session.current===token&&currentResultLayerOwner()==='mesh-preview'&&currentResultLayerSet()===packet&&currentResultLayerContext()===context&&ackedSceneHash.current===scene.projectHash&&sceneRevision.current===scene.sceneRevision
          await transferMeshPreview({packet,intent,scene,generation,client:value,isCurrent})

        }),error=>{if(session.current===token)failed(error)}))
        resultSync.current.offer(JSON.stringify([projectUid.current,sceneEpoch.current,resultLayerRevision()]),
          ()=>currentResultTransmission(projectUid.current,ackedSceneHash.current))
        // Post-processing keeps its own presentation state (the shared driver
        // switches its carrier to Kit with this session); it is reported here.
        display.report('postprocess',()=>postprocessChannel(sharedPostprocessDisplay.status()))
        // The liquid colouring follows the shown result for the whole session.
        display.onClose(startOvLiquidChannel())
        display.report('liquid',()=>liquidChannel(appOvLiquidColouring().state))
        hasOverlay.current=false
        setReady(true)
      }} onError={failed}/>}
  </>
}


function postprocessChannel(s:ReturnType<typeof sharedPostprocessDisplay.status>):Pick<DisplayChannelStatus,'state'|'detail'|'error'> {
  const state=s.renderer!=='ov'||s.status==='idle'?'idle':s.status==='pending'?'sending':s.status==='rejected'?'failed':'delivered'
  return {state,detail:`${s.status}${s.timeSeconds!==null?` t=${s.timeSeconds}`:''}`,error:s.status==='rejected'?s.message:null}
}
function liquidChannel(s:ReturnType<typeof appOvLiquidColouring>['state']):Pick<DisplayChannelStatus,'state'|'detail'|'error'> {
  const state=s.status==='on'?'delivered':s.status==='loading'?'sending':s.status==='error'?'failed':'idle'
  return {state,detail:s.status+(s.frame?` sample ${s.frame.sample} painted ${s.frame.painted}`:''),error:s.status==='error'?s.note:null}
}

/** The server's read-only Kit probe (`GET /api/ov-status`). */
export interface OvStatus {bridge:boolean;kitListening:boolean|null}
export async function fetchOvStatus():Promise<OvStatus|null> {
  try {
    if(typeof fetch!=='function')return null
    const resp=await fetch('/api/ov-status')
    if(!resp.ok)return null
    const data=await resp.json() as {bridge?:unknown;kitListening?:unknown}
    return {bridge:data.bridge===true,kitListening:typeof data.kitListening==='boolean'?data.kitListening:null}
  }catch{return null}
}

export type OvPillState='unknown'|'off'|'starting'|'ready'|'waiting'|'connecting'|'connected'|'retry'
/** One word for the toolbar: is OV usable, and what does a click do? */
export function ovPillState(s:{mode:'three'|'ov';ready:boolean;waitingKit:boolean;failed:boolean;status:OvStatus|null}):OvPillState {
  if(s.mode==='ov')return s.ready?'connected':'connecting'
  if(s.waitingKit)return 'waiting'
  if(s.status?.bridge===false)return 'off'
  if(s.failed)return 'retry'
  if(s.status?.kitListening===false)return 'starting'
  if(s.status?.kitListening)return 'ready'
  return 'unknown'
}

/** Fixed below its button and portalled to <body>: the toolbar header is a
 *  scroll container, so an absolutely positioned panel inside it is clipped
 *  (that is why it once sat inline and pushed the whole bar open). */
function OvSettingsPopover({anchor,panelRef,onClose,children}:{anchor:HTMLElement|null;panelRef:{current:HTMLDivElement|null};onClose:()=>void;children:ReactNode}) {
  const [pos,setPos]=useState<{top:number;right:number}|null>(null)
  useEffect(()=>{
    const place=()=>{const r=anchor?.getBoundingClientRect();if(r)setPos({top:r.bottom+6,right:Math.max(8,window.innerWidth-r.right)})}
    place()
    const outside=(e:MouseEvent)=>{const target=e.target as Node;if(!panelRef.current?.contains(target)&&!anchor?.contains(target))onClose()}
    const key=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose()}
    window.addEventListener('resize',place);window.addEventListener('scroll',place,true)
    document.addEventListener('mousedown',outside);document.addEventListener('keydown',key)
    return ()=>{window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true)
      document.removeEventListener('mousedown',outside);document.removeEventListener('keydown',key)}
  },[anchor,onClose,panelRef])
  return <div ref={panelRef} className="renderer-connection-popover" role="dialog"
    style={{position:'fixed',top:pos?.top??0,right:pos?.right??8,visibility:pos?'visible':'hidden'}}>{children}</div>
}
