/**
 * Running the legacy liquid-cooling scene modules inside React.
 *
 * `js/liquid_pipe_renderer.js` (1065 lines) draws supply and return pipes,
 * their control points and waypoint spheres; `js/liquid_mode.js` (2548) holds
 * the pipe drag-connect state machine, port indicators, T-branch junctions and
 * valve placement. Both predate the shared-module refactor and read their
 * dependencies as BARE GLOBALS — `globalGroup`, `state`, `allModels`,
 * `requestRender`, `logOperation`, `pushUndoCheckpoint`, `updateUI`.
 *
 * That is not a reason to rewrite them. A bare read of an undeclared name
 * resolves against the global object at run time even inside a module, so the
 * modules work unchanged as long as those names exist on `window` with the
 * meanings they expect. This file is that shim, and it is deliberately small:
 * every entry is one line with a React counterpart, and nothing else about the
 * two apps is coupled.
 *
 * ## `state` has to be a GETTER
 *
 * The legacy page keeps ONE project object and mutates it. React replaces it on
 * every edit, so a plain `window.state = project` would leave the modules
 * holding the project as it was when liquid mode was entered — pipes would be
 * drawn from a snapshot and edits would land on an object nothing renders.
 * Defined as an accessor so every read is current.
 *
 * ## Undo
 *
 * These modules mutate the project in place after calling
 * `pushUndoCheckpoint()`. The store grew that call for exactly this: it
 * snapshots and then swaps in a clone, so the snapshot is not the object about
 * to be mutated.
 */

import * as THREE from 'three'
import type { CfdProject, Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import { isRackDetailMode } from '../geometry/rackDetail'
import { isLiquidModeActive } from '../liquid/liquidModeState'
import { mountLiquidPostprocessHost } from '../liquid/liquidPostprocessHost'
import { stateV4 } from './cfdprojModules'
import { installLiquidGlobals } from './liquidGlobals'
import { installColorbarGlobal } from './colorbarGlobal'
import {cabinetFrame,clipClosedCabinetPipe,terminalStraightDomain,
  type TerminalDisplayPlane,type ClosedCabinet} from '../geometry/closedCabinetPipeClip'
import {renderRootOf,subscribeRenderedOpacity} from '../geometry/modelOpacity'
import {showCabinetPipes,registerCabinetDisplayRefresh,setCabinetDisplayDiagnostics,requestCabinetDisplayRefresh} from '../liquid/closedCabinetDisplay'

export interface LiquidSceneHandles {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: { enabled: boolean }
  globalGroup: THREE.Group
}

export interface LiquidPipeRendererApi {
  updatePipeGeometry(): void
  refreshPipeDisplayGeometry(): void
  buildPipeControlPoints(modelId: string): void
  clearPipeControlPoints(): void
  buildPipeSegmentWaypointSpheres(modelId: string): void
  getCduPort(model: Construction, which: string): unknown
  getRackPort(model: Construction, which: string): unknown
  resolveChainEquipmentPort(token: string): unknown
}

export interface LiquidModeApi {
  renderValves(): void
  deselectAllValves(): void
  clearWaypointSpheres(): void
  buildWaypointSpheres(modelId: string): void
  /** Takes the legacy SELECTION object, not a uid. */
  onPipeSelectionChanged(selection: { type: string; modelId: string } | null): void
  /**
   * The T-branch rotate gizmo: a Z-only TransformControls on the junction cube.
   *
   * Z-only because a T-branch has three coplanar ports — turning it out of that
   * plane is not an orientation the fitting has. Its `objectChange` writes
   * `cooling.quaternion` (NOT `geometry.quaternion`, which is what the generic
   * model gizmo writes and what the pipe renderer ignores) and then calls
   * `_reroutePipesForJunction`, which walks the ports and moves the endpoint of
   * every pipe attached to one.
   */
  attachJunctionGizmo(junctionId: string): void
  detachJunctionGizmo(): void
  /** Re-attach to the freshly built cube after the pipe geometry is rebuilt. */
  refreshJunctionGizmo(): void
  /** Returns true when the click was consumed by a pipe control point. */
  handleControlPointClick?(event: PointerEvent, maxDistance: number): boolean
  /** Returns true when the click was consumed by a valve. */
  handleValveClick?(event: PointerEvent, maxDistance: number): boolean
  /** Right-click: the pipe/control-point context menus. */
  handleContextMenu?(event: MouseEvent): void
  /**
   * The valve → rack-manifold rubber band (js/liquid_mode.js:2009-2117).
   *
   * `startValveDrag` returns whether the press landed on the SELECTED valve and
   * therefore belongs to this gesture; the other two are unconditional. The
   * drop snaps to a manifold port within 0.3 m and writes the fitting's
   * `connectedRackId` / `connectedManifoldPortId` — which is what the graph
   * compiler needs before it emits that rack's spoke edges
   * (js/liquid_graphgen.js:617), so a valve without them is placed and unwired.
   */
  startValveDrag?(event: PointerEvent): boolean
  updateValveDrag?(event: PointerEvent): void
  endValveDrag?(event: PointerEvent): void
}

let loaded = false
let contextMenuTarget: HTMLElement | null = null
let contextMenuHandler: ((event: Event) => void) | null = null
/**
 * Whether the app is showing liquid mode; read by the legacy predicate below.
 *
 * Re-exported rather than owned: the chart host reads the same flag and is
 * imported by THIS file, so keeping the state here would make the two a cycle.
 * `App` imports `setLiquidModeActive` from here, so the re-export is what keeps
 * that call site unchanged.
 */
export { isLiquidModeActive, setLiquidModeActive, subscribeLiquidMode } from '../liquid/liquidModeState'

function defineGetter(name: string, get: () => unknown) {
  const w = window as unknown as Record<string, unknown>
  const existing = Object.getOwnPropertyDescriptor(w, name)
  // Never clobber a real global: on the legacy page these names are the page's
  // own, and this file must be a no-op there.
  if (existing && !existing.get) return
  Object.defineProperty(w, name, { configurable: true, get })
}

interface PipeDisplayContext {
  pipeModelId: string | null
  points: THREE.Vector3[]
  path: THREE.CurvePath<THREE.Vector3>
  segments: number
  equipmentEndpoints: Array<{modelId:string;end:'start'|'end';frame:{
    position:THREE.Vector3;direction:THREE.Vector3
  } | null}>
}
/** Only the actual visible, opaque structural shell can conceal its route. */
export function cabinetIsClosed(model:Construction,root:THREE.Object3D|null):boolean {
  if(model.meta?.enabled===false || (model.meta?.display?.opacity??1)<.999 || !root)return false
  for(let p:THREE.Object3D|null=root;p;p=p.parent)if(!p.visible)return false
  let faces=0,closed=true
  root.traverse(child=>{
    const mesh=child as THREE.Mesh
    if(!mesh.isMesh || !child.userData.exportName || child.userData.liquidVisualDetail)return
    faces++
    for(let p:THREE.Object3D|null=child;p&&p!==root.parent;p=p.parent)if(!p.visible)closed=false
    const materials=Array.isArray(mesh.material)?mesh.material:[mesh.material]
    if(!materials.length || materials.some(m=>!m.visible || !Number.isFinite(m.opacity)||m.opacity<.999))closed=false
  })
  return faces>0&&closed
}

let unsubscribeDisplayOpacity:(()=>void)|null=null
/** Register a display-only adapter before loading the shared pipe renderer. */
export function installCabinetPipeDisplay():void {
  const w=window as unknown as Record<string,unknown>
  let diagnostics:string[]=[]
  w._beginLiquidPipeDisplay=()=>{diagnostics=[]}
  w._endLiquidPipeDisplay=()=>setCabinetDisplayDiagnostics(diagnostics)
  w._displayLiquidPipeGeometry=(original:THREE.BufferGeometry,context:PipeDisplayContext)=>{
    const failed=(reason:string)=>{
      const message=(context.pipeModelId??'pipe')+': '+reason
      diagnostics.push(message)
      return {geometry:original,diagnostic:reason}
    }
    if(showCabinetPipes())return {geometry:original,diagnostic:null}
    try {
      const all=(w.allModels as (()=>Construction[])|undefined)?.()??[]
      const cabinets:ClosedCabinet[]=[],terminals:TerminalDisplayPlane[]=[]
      for(const endpoint of context.equipmentEndpoints){
        const model=all.find(m=>m.uid===endpoint.modelId)
        if(!model || model.kind!=='cdu' || !cabinetIsClosed(model,renderRootOf(model.uid)))continue
        const box=cabinetFrame(model.uid,model.geometry??{})
        if(!box)return failed('invalid-cabinet')
        if(!cabinets.some(c=>c.uid===box.uid))cabinets.push(box)
        const frame=endpoint.frame
        if(!frame || ![...frame.position.toArray(),...frame.direction.toArray()].every(Number.isFinite) ||
          frame.direction.lengthSq()<1e-24)return failed('invalid-terminal')
        const domain=terminalStraightDomain(context.path,context.segments,endpoint.end)
        if(!domain)return failed('unsupported-terminal-strip')
        const curve=endpoint.end==='start'?context.path.curves[0]:context.path.curves[context.path.curves.length-1]
        if(!(curve instanceof THREE.LineCurve3))return failed('unsupported-terminal-strip')
        const terminal=endpoint.end==='start'?curve.v1:curve.v2
        const adjacent=endpoint.end==='start'?curve.v2:curve.v1
        if(terminal.distanceTo(frame.position)>1e-9)return failed('unbound-terminal')
        const normal=frame.direction.clone().normalize(),dot=adjacent.clone().sub(terminal).normalize().dot(normal)
        if(!Number.isFinite(dot)||Math.abs(dot)<1e-8)return failed('tangent-terminal')
        terminals.push({point:frame.position.clone(),normal,keepSign:dot>0?1:-1,...domain})
      }
      const result=clipClosedCabinetPipe(original,cabinets,terminals)
      if(result.diagnostic)return failed(result.diagnostic)
      return result
    }catch{return failed('display-clip-failed')}
  }
  unsubscribeDisplayOpacity?.()
  unsubscribeDisplayOpacity=subscribeRenderedOpacity(changedRoot=>{
    const models=(w.allModels as (()=>Construction[])|undefined)?.()??[]
    const contains=(ancestor:THREE.Object3D,node:THREE.Object3D)=>{
      for(let p:THREE.Object3D|null=node;p;p=p.parent)if(p===ancestor)return true
      return false
    }
    if(models.some(model=>{
      if(model.kind!=='cdu')return false
      const root=renderRootOf(model.uid)
      return !!root&&(contains(changedRoot,root)||contains(root,changedRoot))
    }))requestCabinetDisplayRefresh()
  })
  registerCabinetDisplayRefresh(()=>{
    liquidPipes()?.refreshPipeDisplayGeometry()
    ;(w.requestRender as (()=>void)|undefined)?.()
  })
}

export async function installLiquidScene(handles: LiquidSceneHandles): Promise<{
  pipes: LiquidPipeRendererApi
  mode: LiquidModeApi
}> {
  const w = window as unknown as Record<string, unknown>

  defineGetter('state', () => store.getProject())
  w.globalGroup = handles.globalGroup
  w.scene = handles.scene
  w.camera = handles.camera
  w.renderer = handles.renderer
  w.controls = handles.controls
  w.allModels = () => {
    const p = store.getProject()
    return p ? (stateV4().allModels(p as CfdProject) as Construction[]) : []
  }
  installCabinetPipeDisplay()
  w.getModelById = (uid: string) => {
    const p = store.getProject()
    return p ? (store.findConstruction(p, uid)?.model ?? null) : null
  }
  /**
   * The legacy page's model map, as a live adapter.
   *
   * `liquid_mode.js` calls `_modelMap.get(uid)` in nineteen places and
   * `set`/`delete` in six. Handing over a real Map built at install time would
   * go stale on the first project edit — React replaces the project object —
   * so this is a Map-SHAPED view that resolves against the live project.
   *
   * `set` and `delete` are accepted and ignored: they mirror the page's cache
   * bookkeeping, and here the project IS the cache.
   */
  w._modelMap = {
    get: (uid: string) => {
      const p = store.getProject()
      return p ? (store.findConstruction(p, uid)?.model ?? null) : null
    },
    set: () => undefined,
    delete: () => undefined,
    has: (uid: string) => {
      const p = store.getProject()
      return !!(p && store.findConstruction(p, uid))
    },
  }
  if (!w.requestRender) w.requestRender = () => {}
  /**
   * The legacy display-mode predicate.
   *
   * `handleContextMenu` and a dozen other places in the liquid modules bail out
   * unless `isLiquidMode()` says so, and it reads `uiState.displayMode` — a
   * legacy UI object this app does not have. Without it every right-click
   * returns at the first line and the pipe menus never appear.
   */
  defineGetter('uiState', () => ({ displayMode: isLiquidModeActive() ? 'liquid' : 'geometry' }))
  w.isLiquidMode = () => isLiquidModeActive()
  // The shared pipe and valve renderers no-op (after clearing) while this is
  // true: the detail rack sits re-centred at the origin and world-placed
  // pipes can never line up with it. Without this global they kept drawing.
  w.isRackDetailMode = () => isRackDetailMode()
  // The legacy signature is (action, detail, meta, kind); the extra two are
  // classification the React log does not keep, so they are dropped rather
  // than stringified into the message.
  w.logOperation = (action: string, detail: string) => store.logOperation(action, detail ?? '')
  w.pushUndoCheckpoint = (action: string) => store.pushUndoCheckpoint(action ?? 'liquidEdit')
  w.updateUI = () => store.notifyLiveEdit()

  // The four liquid accessors the legacy page owns (STL_generate.html:1481).
  // `js/liquid_cooling.js` calls them through `window`, and the .cfdrun builder
  // calls that — so without them the CFD-package export fails with
  // "window._liqCfg is not a function" and nothing else explains why.
  //
  // They create the sub-objects they read, exactly as the originals do: a
  // project that has never been in liquid mode has no `runDefaults.liquid`, and
  // returning undefined there would push the check into every caller.
  type Bag = Record<string, unknown>
  w._liqCfg = () => {
    const p = store.getProject() as unknown as Bag
    const r = (p.runDefaults ?? (p.runDefaults = {})) as Bag
    return (r.liquid ?? (r.liquid = {})) as Bag
  }
  w._liqFluid = () => {
    const p = store.getProject() as unknown as Bag
    const models = p.models as Bag | undefined
    const material = models?.materialModels as Bag | undefined
    const fluids = material?.fluids as Bag | undefined
    const values = fluids ? Object.values(fluids) : []
    return values.length ? values[0] : undefined
  }
  w._liqSetFluid = (fluid: { type?: string } | null) => {
    const p = store.getProject() as unknown as Bag
    const models = (p.models ?? (p.models = {})) as Bag
    const material = (models.materialModels ?? (models.materialModels = {})) as Bag
    const fluids = (material.fluids ?? (material.fluids = {})) as Bag
    // Cleared before re-keying: the fluid is keyed by TYPE, so switching from
    // water to a glycol mix would otherwise leave both in the file and the
    // solver would read whichever came first.
    Object.keys(fluids).forEach((k) => delete fluids[k])
    if (fluid) fluids[fluid.type ?? 'fluid'] = fluid
  }
  w._liqNetMain = (create: boolean) => {
    const p = store.getProject() as unknown as Bag
    const c = (p.constructions ?? (p.constructions = {})) as Bag
    const nets = (c.liquidFlowNetworks ?? (c.liquidFlowNetworks = {})) as Bag
    if (!nets.net_main && create) {
      nets.net_main = { uid: 'net_main', geometry: {}, meta: { name: 'main', enabled: true } }
    }
    return nets.net_main
  }

  // The edit-side globals — addModel, removeModelByUid, the pipe factories,
  // _getManifoldEndpoint. Installed BEFORE the imports below, so nothing the
  // modules do at load time sees a half-built set.
  installLiquidGlobals()
  // The colorbar factory the shared renderer looks for by name. Installed here,
  // before the modules are imported, for the same reason as the rest: a guarded
  // `typeof window._ppCreateColorbarWidget === 'function'` that runs before it
  // exists is a silent no-op, and the symptom is a coloured scene with no scale.
  installColorbarGlobal()

  // The liquid post-processing chart window. Its own root on `document.body`,
  // like the air solver monitor's: `window.lsmShowPostProcessWindow` is called
  // from a tree click and from the post-processing panel, and a window that
  // lived inside either would be torn down by the tab switch that opened it.
  mountLiquidPostprocessHost()

  if (!loaded) {
    await import('../../../shared/js/liquid_cooling.js')
    await import('../../../shared/js/liquid_pipe_renderer.js')
    await import('../../../shared/js/liquid_mode.js')
    loaded = true
  }
  const pipes = w._liquidPipeRenderer as LiquidPipeRendererApi | undefined
  const mode = w._liquidMode as LiquidModeApi | undefined
  if (!pipes || !mode) {
    throw new Error('liquid scene modules did not install their APIs on window')
  }
  // `liquid_mode.js` attaches its own contextmenu listener from a `window.load`
  // handler. That event fired long before this dynamic import resolved, so in
  // this app it never runs and the right-click menus — the pipe control-point
  // menu above all — are unreachable. Attach it here instead.
  //
  // Per CANVAS, not once per session: StrictMode mounts the viewport twice in
  // development, and an install-once flag would leave the listener on the FIRST
  // renderer's canvas — the one that was thrown away — so every right-click on
  // the live canvas would do nothing.
  if (contextMenuTarget !== handles.renderer.domElement && mode.handleContextMenu) {
    if (contextMenuTarget && contextMenuHandler) {
      contextMenuTarget.removeEventListener('contextmenu', contextMenuHandler)
    }
    contextMenuHandler = (event: Event) => {
      liquidModeApi()?.handleContextMenu?.(event as MouseEvent)
    }
    contextMenuTarget = handles.renderer.domElement
    contextMenuTarget.addEventListener('contextmenu', contextMenuHandler)
  }
  return { pipes, mode }
}

export function liquidPipes(): LiquidPipeRendererApi | null {
  return (window as unknown as { _liquidPipeRenderer?: LiquidPipeRendererApi })
    ._liquidPipeRenderer ?? null
}

/** Internal alias, so the listener always calls the CURRENT module API. */
function liquidModeApi(): LiquidModeApi | null {
  return (window as unknown as { _liquidMode?: LiquidModeApi })._liquidMode ?? null
}

export function liquidMode(): LiquidModeApi | null {
  return (window as unknown as { _liquidMode?: LiquidModeApi })._liquidMode ?? null
}
