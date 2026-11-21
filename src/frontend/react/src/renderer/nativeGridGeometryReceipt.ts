import Ajv from 'ajv'
import schema from '../../../shared/schema/render-grid-native-geometry.schema.json'
import type {BackgroundGridView} from '../geometry/backgroundGrid'
import type {GridRasterBinding} from './displayRaster'
import type {RenderAck} from './ovProtocol'
import type {RenderViewState,CameraState} from './sceneSnapshot'

/** World-plane decoration only; never an encoded/source/edit/pick grant. */
export interface NativeGridGeometryReceipt {
  schema:'dcdtuni.native-grid-geometry/v1';phase:'synchronized';domain:'native-world-plane'
  binding:GridRasterBinding;grid:BackgroundGridView
  requestedCamera:CameraState;acceptedCamera:CameraState;timeSeconds:number|null
  nativeSource:{ownerSha256:string;projectionSha256:string;frameNumber:number;
    resolution:[number,number];fullResolution:[number,number]}
  carrierSha256:string;gridPixelVerified:false;encodedFrameProof:'unresolved'
}

type NativeReceiptDiagnosticStage='decode-input'|'validation'
type NativeReceiptRefusal='not-evaluated'|'ack-not-success'|'top-grid-status'|'schema-rejected'
  |'schema-time-key-missing'|'binding-or-hidden'|'receipt-binding'|'ack-binding'
  |'scene-source'|'grid-intent'|'requested-camera'|'accepted-camera'
  |'receipt-time'|'ack-time'|'synchronized'
const nativeReceiptDiagnosticsEnabled=
  import.meta.env.VITE_DC_CFD_NATIVE_GRID_RECEIPT_DIAGNOSTICS==='grid-receipt-v1'
const nativeReceiptDiagnosticSeen=new Set<string>()
function diagnosticRecord(v:unknown):v is Record<string,unknown>{
  return typeof v==='object'&&v!==null&&!Array.isArray(v)
}
const own=(v:Record<string,unknown>,key:string)=>Object.prototype.hasOwnProperty.call(v,key)
/** Opt-in sanitized key flags only. One module budget per browser window:
 * no source values, request commands, native reads, proof repair or polling. */
export function traceNativeGridReceipt(stage:NativeReceiptDiagnosticStage,status:unknown,
  acceptedView:unknown,refusal:NativeReceiptRefusal='not-evaluated'):void{
  if(!nativeReceiptDiagnosticsEnabled||nativeReceiptDiagnosticSeen.size>=32)return
  try{
    const g=diagnosticRecord(status)?status:{},n=diagnosticRecord(g.nativeGeometry)?g.nativeGeometry:{}
    const v=diagnosticRecord(acceptedView)?acceptedView:{}
    const row={schema:'dcdtuni.native-grid-receipt-diagnostic/v1',stage,refusal,
      topGridStatusPresent:diagnosticRecord(status),
      nativeGeometryPresent:own(g,'nativeGeometry'),nativeGeometryObject:diagnosticRecord(g.nativeGeometry),
      nativeGeometryTimeSecondsPresent:own(n,'timeSeconds'),
      nativeGeometryTimeSecondsIsNull:own(n,'timeSeconds')&&n.timeSeconds===null,
      acceptedViewTimeSecondsPresent:own(v,'timeSeconds'),
      acceptedViewTimeSecondsIsNull:own(v,'timeSeconds')&&v.timeSeconds===null,
      topGridPixelVerifiedIsFalse:g.gridPixelVerified===false,
      nestedGridPixelVerifiedIsFalse:n.gridPixelVerified===false,
      nestedEncodedFrameProofUnresolved:n.encodedFrameProof==='unresolved',
      diagnosticAuthority:false,additionalNativeReads:false}
    const key=JSON.stringify(row)
    if(nativeReceiptDiagnosticSeen.has(key))return
    nativeReceiptDiagnosticSeen.add(key)
    console.info('[dcdtuni:ov-native-receipt-diagnostic]',JSON.stringify(
      {...row,sequence:nativeReceiptDiagnosticSeen.size}))
  }catch{/* A failed diagnostic cannot alter protocol/validation. */}
}

const check=new Ajv({strict:false,strictNumbers:true}).compile(schema)
const bindingKeys=['projectUid','projectContentSha256','sceneRevision','viewRevision']as const
const gridKeys=['visible','spacingM','divisions','extentM','centerColor','lineColor','opacity','matrix']as const
function sameBinding(a:GridRasterBinding|undefined,b:GridRasterBinding|undefined){
  return !!a&&!!b&&bindingKeys.every(k=>a[k]===b[k])
}
function sameGrid(a:BackgroundGridView|undefined,b:BackgroundGridView|undefined){
  return !!a&&!!b&&gridKeys.every(k=>JSON.stringify(a[k])===JSON.stringify(b[k]))
}
function sameCamera(a:RenderViewState['camera'],b:RenderViewState['camera']){
  const keys=['position','target','up','fov','aspect','near','far']as const
  return a===undefined&&b===undefined||!!a&&!!b&&keys.every(k=>JSON.stringify(a[k])===JSON.stringify(b[k]))
}
/** Validate against this exact sent view and its ordinary accepted scene ACK.
 * Callers must additionally fence the live local project/session before use. */
export function nativeGridGeometrySynchronized(ack:RenderAck,view:RenderViewState):boolean {
  const status=ack.gridDisplay,receipt=status?.nativeGeometry,binding=view.backgroundGridRaster?.binding
  const finish=(refusal:NativeReceiptRefusal,result=false)=>{
    traceNativeGridReceipt('validation',status,ack.view,refusal);return result
  }
  // The existing predicate and strict evaluation order remain unchanged.
  if(ack.ok!==true)return finish('ack-not-success')
  if(status?.phase!=='unsupported'||status.gridPixelVerified!==false)return finish('top-grid-status')
  if(!check(receipt))return finish(diagnosticRecord(receipt)&&!own(receipt,'timeSeconds')
    ?'schema-time-key-missing':'schema-rejected')
  if(!binding||!view.backgroundGrid?.visible)return finish('binding-or-hidden')
  if(!sameBinding(receipt!.binding,binding))return finish('receipt-binding')
  if(!sameBinding(ack.view?.backgroundGridRaster?.binding,binding))return finish('ack-binding')
  if(ack.projectHash!==binding.projectContentSha256||ack.sceneRevision!==binding.sceneRevision)return finish('scene-source')
  if(!sameGrid(receipt!.grid,view.backgroundGrid)||!sameGrid(ack.view?.backgroundGrid,view.backgroundGrid))return finish('grid-intent')
  if(!sameCamera(receipt!.requestedCamera,view.camera))return finish('requested-camera')
  if(!sameCamera(receipt!.acceptedCamera,ack.view?.camera))return finish('accepted-camera')
  if(receipt!.timeSeconds!==view.timeSeconds)return finish('receipt-time')
  if(ack.view?.timeSeconds!==view.timeSeconds)return finish('ack-time')
  return finish('synchronized',true)
}

/** Bounded follow-up only for these explicit current native-frame transitions.
 * Stable encoded-layout refusal and unavailable runtime are not retry signals. */
export function isTransientGridPending(status:RenderAck['gridDisplay']):boolean {
  return status?.phase==='pending'&&[
    'viewport-frame-not-current','native-grid-view-not-current',
    'viewport-camera-frame-not-current','viewport-projection-frame-not-current',
    'native-camera-frame-not-current','native-projection-frame-not-current',
    'native-camera-changed-during-sample','native-observation-changed-during-sample',
    'native-observation-changed-during-lowering'
  ].includes(status.reason??'')
}
