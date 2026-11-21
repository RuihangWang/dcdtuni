import {useCallback,useEffect,useRef,useState,useSyncExternalStore} from 'react'
import {createPortal} from 'react-dom'
import type * as THREE from 'three'
import type {CfdProject} from '../schema/cfdproj'
import * as store from '../projectStore'
import type {SceneBuilder} from '../interaction/sceneBuilder'
import {getViewportHandle,subscribeCameraSettled} from '../viewportBus'
import {resultOriginalMaterial,resultRenderRevision,selectedResultSample,selectedResultTime,subscribeResultSample} from '../liquid/liquidTempColor'
import {buildSceneSnapshot,captureViewState,type SceneSnapshot} from './sceneSnapshot'
import {restoreRenderCamera} from './viewState'
import type {SceneTransmission} from './ovSceneTransfer'
import {OvViewport} from './OvViewport'
import type {OvCommandClient,RenderAck} from './ovProtocol'
import {clearStaleResultLayerSet,currentLiveAirResultSelection,currentResultLayerContext,
  currentResultLayerOwner,currentResultLayerSet,resultLayerRevision,subscribeResultLayerRevision} from './resultLayerRevision'
import type {ResultLayerSet,ResultSource} from './resultLayers'
import {saveRenderScene} from './renderSceneExport'
import {cfdrunExport} from '../legacy/cfdrunBuild'
import {createOvEditBridge} from './ovEditBridge'
import {ovSession,setOvSession,subscribeOvSession} from './ovSession'
import {useSharedPostprocessRenderer} from './useSharedPostprocessRenderer'
import {enqueueOvResultMutation} from './ovResultMutationQueue'
import {getServerInfo,subscribeSession,type ServerInfo} from '../run/session'
import {getMeshPreviewSession,subscribeMeshPreview} from '../run/meshPreviewSession'
import {buildMeshPreviewOverlay,meshOverlayRevision,type RenderOverlayPacket} from './meshPreviewOverlay'
import {useTranslation} from '../useTranslation'
import type {TranslationKey} from '../i18n'
import {subscribeClosedCabinetDisplay,leafDisplayRevision,displaySceneKey} from '../liquid/closedCabinetDisplay'

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

/** An OV-side orbit survives a project edit; new React time and selection still win. */
export function keepActiveOvCamera(packet:SceneSnapshot,ack:RenderAck,activeProjectUid:string):SceneSnapshot {
  if(packet.project.uid!==activeProjectUid||!ack.view?.camera)return packet
  return {...packet,view:{...packet.view,camera:ack.view.camera}}
}

type Props={project:CfdProject|null;selectedUids:readonly string[];onSelection:(uids:string[])=>void;
  activeTab:string;airMode:boolean}
type LocalizedMessage={key?:TranslationKey;vars?:Record<string,string|number>;detail?:string}
type ResultTransmission={packet:ResultLayerSet|null;context:{source:ResultSource;timeSeconds:number|null}|null}
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
export function RendererControl({project,selectedUids,onSelection}:Props) {
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
  const sync=useRef<LatestSceneSync<SceneTransmission>|null>(null)
  const overlaySync=useRef<LatestSceneSync<RenderOverlayPacket>|null>(null)
  const resultSync=useRef<LatestSceneSync<ResultTransmission>|null>(null)
  const sceneEpoch=useRef(0)
  const sceneRevision=useRef(-1)
  const ackedSceneHash=useRef<string|null>(null)
  const selectionHandler=useRef(onSelection)
  selectionHandler.current=onSelection
  const degraded=useRef(false)
  const interactionAllowed=useRef(false)
  interactionAllowed.current=mode==='ov'&&ready&&!returning&&!degraded.current
  const projectUid=useRef<string|null>(project?.uid??null)
  projectUid.current=project?.uid??null
  const hasOverlay=useRef(false)
  const session=useRef(0)
  const revision=useSyncExternalStore(store.subscribe,store.revision)
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
  const key=displaySceneKey(project?.uid??'',revision,[keySample,keyTime,keyVisual],displayRevision)
  const overlayKey=JSON.stringify([project?.uid??'',previewRevision])
  const resultKey=JSON.stringify([project?.uid??'',sceneEpoch.current,layerRevision])
  const currentKey=useRef(key)
  currentKey.current=key
  const snapshot=useCallback(()=>{
    const live=window as unknown as {globalGroup?:THREE.Object3D;__sceneBuilder?:SceneBuilder}
    const handle=getViewportHandle()
    if(!project||!live.globalGroup||!handle)throw new Error(t('renderer.scene_not_ready'))
    return buildSceneSnapshot({root:live.globalGroup,project,camera:handle.camera,target:handle.controls.target,
      selectedUids,timeSeconds,helpers:live.__sceneBuilder?.registries.renderHelpers,
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
    sceneRevision.current=ack.sceneRevision
    ackedSceneHash.current=ack.projectHash
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
    value.onEditEvent(event=>{bridge.apply(event)})
    return true
  }
  const initialSnapshot=useCallback(()=>{
    const packet=snapshot()
    initialSent.current=key
    sentProjectUid.current=packet.project.uid
    return packet
  },[snapshot,key])
  function failed(error:Error) {
    sync.current?.close();sync.current=null;session.current+=1
    overlaySync.current?.close();overlaySync.current=null;hasOverlay.current=false
    resultSync.current?.close();resultSync.current=null
    client.current?.onEditEvent(null);sceneRevision.current=-1;ackedSceneHash.current=null
    degraded.current=false
    setOvSession({client:null,ready:false,degraded:false,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})
    setMessage({key:getServerInfo()?.ovBridge===false?'renderer.ov_not_started':'renderer.ov_unavailable',vars:{error:error.message}})
    client.current=null;setReady(false);setReturning(false);setMode('three')
  }
  async function returnToThree() {
    if(returning)return
    setReturning(true)
    const token=++session.current
    const sourceKey=key
    // If a new project/frame has not reached Kit yet, its camera/selection reply
    // belongs to the previous scene. Preserve the newer local state instead.
    const canRead=!!sync.current?.isCurrent(key)
    sync.current?.close();sync.current=null
    overlaySync.current?.close();overlaySync.current=null;hasOverlay.current=false
    resultSync.current?.close();resultSync.current=null
    client.current?.onEditEvent(null);sceneRevision.current=-1;ackedSceneHash.current=null
    degraded.current=false
    try {
      if(client.current&&ready&&canRead) {
        const ack=await waitForRenderView(client.current.readView())
        if(session.current!==token||currentKey.current!==sourceKey||ack.view?.timeSeconds!==timeSeconds)return
        const handle=getViewportHandle()
        const root=(window as unknown as {globalGroup?:THREE.Object3D}).globalGroup
        if(ack.view?.camera&&handle&&root) {
          restoreRenderCamera(ack.view.camera,root,handle.camera,handle.controls.target)
          handle.controls.update()
        }
        if(ack.view)onSelection(ack.view.selectedUids)
      }
    }catch(error){if(session.current===token)setMessage({key:'renderer.local_view_fallback',vars:{error:String(error)}})}
    finally {if(session.current===token){client.current=null;setReady(false);setMode('three');setReturning(false);setOvSession({client:null,ready:false,degraded:false,liquidActive:false,sceneIdle:null,projectUid:null,projectContentSha256:null,sceneRevision:null})}}
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
    overlaySync.current?.offer(overlayKey,()=>buildMeshPreviewOverlay(projectUid,getMeshPreviewSession()))
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
      const view=captureViewState({root,project:project??{uid:''},camera:viewport.camera,
        target:viewport.controls.target,selectedUids,timeSeconds:selectedResultTime()})
      void active.updateView(view).catch(()=>{})
    }catch{/* navigation must never throw into the orbit controls */}
  },[mode,ready,returning,project,selectedUids])
  useEffect(()=>subscribeCameraSettled(pushView),[pushView])
  useEffect(()=>{pushView()},[selectedUids]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>()=>{sync.current?.close();overlaySync.current?.close();resultSync.current?.close();session.current+=1},[])
  useEffect(()=>{setOvSession({mode})},[mode])
  async function download() {
    const capturedKey=currentKey.current
    const caseDir=project?.meta?.casePath
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
  const switchRow=<div style={{display:'flex',gap:6,alignItems:'center',flexWrap:inBar?'nowrap':'wrap',pointerEvents:'auto'}}>
    <label>{t('renderer.label')} <select aria-label={t('renderer.label')} value={mode} disabled={!project||returning}
      onChange={e=>{if(e.target.value==='three')void returnToThree();else{session.current+=1;setMessage(null);setMode('ov')}}}>
      <option value="three">Three.js</option><option value="ov">Omniverse</option>
    </select></label>
    <button className={inBar?'tool-btn':'btn'} style={inBar?undefined:{width:'auto',margin:0,padding:'4px 10px'}} disabled={!project||saving} onClick={()=>void download()}>{t('renderer.export')}</button>
    <details className="renderer-connection"><summary>{t('renderer.connection')}</summary><div style={{background:'var(--panel)',padding:8}}>
      <label>{t('renderer.host')} <input aria-label={t('renderer.host_aria')} value={server} disabled={mode==='ov'} onChange={e=>{setCustomEndpoint(true);setServer(e.target.value)}}/></label>
      <label>{t('renderer.port')} <input aria-label={t('renderer.port_aria')} type="number" min={1} max={65535} value={port} disabled={mode==='ov'} onChange={e=>{setCustomEndpoint(true);setPort(Number(e.target.value))}}/></label>
      <small>{t('renderer.connection_hint')}</small>
    </div></details>
  </div>
  return <>
    {toolbarSlot&&createPortal(switchRow,toolbarSlot)}
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
    {mode==='ov'&&<OvViewport server={server} signalingPort={port} snapshot={initialSnapshot}
      projectHash={projectHash} onReady={(value,initialAck,initialPacket)=>{
        if(!initialAck){
          // Degraded display: video live, acknowledgement channel dead. Show the
          // unconfirmed re-render behind a banner; suspend everything that needs
          // the ack (edit bridge, live re-sync, camera read-back, result layers).
          degraded.current=true
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
        sync.current?.close()
        sync.current=new LatestSceneSync<SceneTransmission>(async ({packet,hash})=>{
          let next=packet
          if(packet.project.uid===sentProjectUid.current){
            const ack=await waitForRenderView(value.readView(),3000)
            next=keepActiveOvCamera(packet,ack,sentProjectUid.current)
          }
          const ack=await value.loadScene(next,hash)
          if(installEditBridge(value,ack,next,hash))clearStaleResultLayerSet(next.project.uid,ack.projectHash!)
          sentProjectUid.current=next.project.uid
          sceneEpoch.current+=1
          resultSync.current?.offer(JSON.stringify([next.project.uid,sceneEpoch.current,resultLayerRevision()]),
            ()=>currentResultTransmission(projectUid.current,ackedSceneHash.current))
        },error=>{if(session.current===token)failed(error)},initialSent.current)
        overlaySync.current?.close()
        overlaySync.current=new LatestSceneSync<RenderOverlayPacket>(
          packet=>value.loadOverlay(packet),error=>{if(session.current===token)failed(error)},
        )
        resultSync.current?.close()
        resultSync.current=new LatestSceneSync<ResultTransmission>(async transmission=>{
          // Air results are delivered by the common postprocess adapter. This
          // retained queue owns diagnostic mesh-preview packets only.
          const {packet,context}=transmission
          if(currentResultLayerOwner()!=='mesh-preview'||!packet||!context)return
          const ack=await enqueueOvResultMutation(value,()=>session.current===token&&
            currentResultLayerOwner()==='mesh-preview'&&currentResultLayerSet()===packet&&
            currentResultLayerContext()===context&&ackedSceneHash.current===packet.source.projectContentSha256,
            ()=>value.loadResultLayers(packet,context.source,context.timeSeconds))
          if(ack&&ack.revision!==packet.revision)throw new Error('Omniverse result revision mismatch')
        },error=>{if(session.current===token)failed(error)})
        resultSync.current.offer(JSON.stringify([projectUid.current,sceneEpoch.current,resultLayerRevision()]),
          ()=>currentResultTransmission(projectUid.current,ackedSceneHash.current))
        hasOverlay.current=false
        setReady(true)
      }} onError={failed}/>}
  </>
}
