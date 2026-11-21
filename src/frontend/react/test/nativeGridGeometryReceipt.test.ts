import {expect,it} from 'vitest'
import receiptSchema from '../../shared/schema/render-grid-native-geometry.schema.json'
import commonSchema from '../../shared/schema/render-scene.schema.json'
import {nativeGridGeometrySynchronized,isTransientGridPending} from '../src/renderer/nativeGridGeometryReceipt'
import type {RenderAck} from '../src/renderer/ovProtocol'
import type {RenderViewState} from '../src/renderer/sceneSnapshot'
function fixture(){
 const binding={projectUid:'controlled-project',projectContentSha256:'a'.repeat(64),sceneRevision:2,viewRevision:3}
 const grid={visible:true,spacingM:.6,divisions:4,extentM:2.4,centerColor:[.2,.3,.4],lineColor:[.1,.2,.3],opacity:.6,matrix:[1,0,0,0,0,1,0,0,0,0,1,0,0,-.005,0,1]}
 const camera={position:[0,3,8],target:[0,0,0],up:[0,1,0],fov:50,aspect:1.5,near:.1,far:100}
 const view={backgroundGrid:grid,backgroundGridRaster:{binding},camera,timeSeconds:null} as unknown as RenderViewState
 const native={schema:'dcdtuni.native-grid-geometry/v1',phase:'synchronized',domain:'native-world-plane',binding,grid,requestedCamera:camera,acceptedCamera:camera,timeSeconds:null,nativeSource:{ownerSha256:'b'.repeat(64),projectionSha256:'c'.repeat(64),frameNumber:4,resolution:[1920,1080],fullResolution:[1920,1080]},carrierSha256:'d'.repeat(64),gridPixelVerified:false,encodedFrameProof:'unresolved'}
 const ack={ok:true,requestId:'controlled',view,sceneRevision:2,projectHash:binding.projectContentSha256,gridDisplay:{phase:'unsupported',reason:'stream-layout-unresolved',viewRevision:3,gridPixelVerified:false,nativeGeometry:native}} as unknown as RenderAck
 return{view:structuredClone(view),ack:structuredClone(ack)}
}
it('uses the common world grid and binding schema without changing the encoded contract',()=>{
 expect(receiptSchema.properties.grid).toEqual(commonSchema.properties.view.properties.backgroundGrid)
 expect(receiptSchema.properties.binding).toEqual(commonSchema.properties.view.properties.backgroundGridRaster.properties.binding)
 const {view,ack}=fixture();expect(nativeGridGeometrySynchronized(ack,view)).toBe(true)
 expect(ack.gridDisplay?.phase).toBe('unsupported');expect(ack.gridDisplay?.reason).toBe('stream-layout-unresolved')
 expect(ack.gridDisplay?.nativeGeometry?.gridPixelVerified).toBe(false);expect(ack.gridDisplay?.nativeGeometry?.encodedFrameProof).toBe('unresolved')
})
it.each(['missing','fake-pixel','fake-encoded','extra-authority','wrong-domain','bool-frame','bad-owner','wrong-project','wrong-scene','wrong-view','wrong-grid','hidden','wrong-camera','wrong-time','wrong-echo','no-binding','failed-ack','wrong-top-phase','no-top-false'])('refuses %s',kind=>{
 const {view,ack}=fixture(),status=ack.gridDisplay as any,n=status.nativeGeometry as any
 if(kind==='missing')delete status.nativeGeometry
 if(kind==='fake-pixel')n.gridPixelVerified=true
 if(kind==='fake-encoded')n.encodedFrameProof='verified'
 if(kind==='extra-authority')n.sourcePickAllowed=true
 if(kind==='wrong-domain')n.domain='encoded-pixel'
 if(kind==='bool-frame')n.nativeSource.frameNumber=true
 if(kind==='bad-owner')n.nativeSource.ownerSha256='unbound'
 if(kind==='wrong-project')n.binding.projectUid='foreign'
 if(kind==='wrong-scene')n.binding.sceneRevision++
 if(kind==='wrong-view')n.binding.viewRevision++
 if(kind==='wrong-grid')n.grid.spacingM=.7
 if(kind==='hidden')view.backgroundGrid!.visible=false
 if(kind==='wrong-camera')ack.view!.camera={position:[1,2,3],target:[0,0,0],up:[0,1,0],fov:50,aspect:1,near:.1,far:100}
 if(kind==='wrong-time')ack.view!.timeSeconds=1
 if(kind==='wrong-echo')ack.view!.backgroundGrid!.matrix[13]=0
 if(kind==='no-binding')delete view.backgroundGridRaster
 if(kind==='failed-ack')ack.ok=false
 if(kind==='wrong-top-phase')status.phase='rendered'
 if(kind==='no-top-false')delete status.gridPixelVerified
 expect(nativeGridGeometrySynchronized(ack,view)).toBe(false)
})

it.each(['viewport-frame-not-current','native-grid-view-not-current','native-camera-frame-not-current','native-projection-frame-not-current','native-camera-changed-during-sample','native-observation-changed-during-sample','native-observation-changed-during-lowering'])('bounded transition selector includes %s',reason=>{
 expect(isTransientGridPending({phase:'pending',reason})).toBe(true)
 expect(isTransientGridPending({phase:'unsupported',reason})).toBe(false)
})
it.each(['stream-layout-unresolved','stream-layout-degenerate','native-public-viewport-observation-unavailable','unknown'])('stable/unknown %s is not a retry signal',reason=>{
 expect(isTransientGridPending({phase:'unsupported',reason})).toBe(false)
 expect(isTransientGridPending({phase:'pending',reason})).toBe(false)
})
