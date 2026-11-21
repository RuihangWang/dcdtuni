import {expect,it} from 'vitest'
import * as THREE from 'three'
import literal from './fixtures/actual-bounded-native-reply.json'
import actualUnion from './fixtures/joint-grid-native-reply.json'
import extensionUnion from './fixtures/joint-grid-extension-reply.json'
import diagnosticSchema from '../../shared/schema/render-display-diagnostics.schema.json'
import commonSchema from '../../shared/schema/render-scene.schema.json'
import {OvCommandClient} from '../src/renderer/ovProtocol'
import {validateDiagnosticReply} from '../src/renderer/ovDisplayDiagnostics'
function meta(){return{metricVersion:2,line:{owner:'CONTROLLED_THREE',drawingBuffer:[2490,995],css:[1992,796],pixelRatio:1.25,aliasedRange:[1,1],effectiveWidthPx:1,coordinateFrame:'scene-world',worldToClip:new THREE.Matrix4().elements},output:{owner:'CONTROLLED_MEDIA',revision:2,decoded:[1600,638],css:[1992,796],layoutCss:[1992,796],objectFit:'contain'},binding:{projectUid:'CONTROLLED_PROJECT',projectContentSha256:literal.projectHash,sceneRevision:literal.sceneRevision,viewRevision:3}}}
const context={expectedSceneRevision:literal.sceneRevision,expectedProjectHash:literal.projectHash}
function reply(){const v=structuredClone(literal) as any;v.view.backgroundGridRaster=meta();return v}
it('bounded reply uses exactly the common optional raster schema and retains unresolved native layout',()=>{
 expect(diagnosticSchema.properties.view.properties.backgroundGridRaster).toEqual(commonSchema.properties.view.properties.backgroundGridRaster)
 const raw=reply(),result=validateDiagnosticReply(raw,context);expect(result.view.backgroundGridRaster).toEqual(meta());expect(result.diagnostics.pixelVerified).toBe(false);expect((result.diagnostics.viewport as any).layoutProof).toBe('unresolved')
 raw.view.backgroundGridRaster.output.css[0]=0;expect(result.view.backgroundGridRaster?.output.css[0]).toBe(1992)
})
it.each(['zero-css','wrong-hash','wrong-scene','wrong-version','fake-layout','bool-revision','line-range'])('strict bounded raster rejects%s',kind=>{
 const v=reply(),r=v.view.backgroundGridRaster
 if(kind==='zero-css')r.output.css[0]=0
 if(kind==='wrong-hash')r.binding.projectContentSha256='b'.repeat(64)
 if(kind==='wrong-scene')r.binding.sceneRevision++
 if(kind==='wrong-version')r.metricVersion=1
 if(kind==='fake-layout')r.output.textureToEncodedMapping='owned-native-frame'
 if(kind==='bool-revision')r.binding.viewRevision=true
 if(kind==='line-range')r.line.aliasedRange=[2,1]
 expect(()=>validateDiagnosticReply(v,context)).toThrow()
})
it('ordinary and bounded getViewState preserve the same stored optional raster, no diagnostic retry or invented layout',async()=>{
 const sent:any[]=[],c=new OvCommandClient(async p=>{sent.push(p)},100)
 try{
  const raw=reply(),plain=c.readView();c.receive({event_type:'dcdtuni:renderAck',payload:{requestId:JSON.parse(sent[0].message).requestId,ok:true,view:raw.view,gridDisplay:{phase:'unsupported',reason:'stream-layout-unresolved',viewRevision:3}}});const ack=await plain;expect(ack.view?.backgroundGridRaster).toEqual(meta());expect(ack.gridDisplay?.phase).toBe('unsupported')
  const pending=c.readViewDiagnostics(context);raw.requestId=JSON.parse(sent[1].message).requestId;c.receive({event_type:'dcdtuni:renderAck',payload:raw});const bounded=await pending;expect(bounded.view).toEqual(ack.view);expect(bounded.diagnostics.pixelVerified).toBe(false);expect(sent).toHaveLength(2)
 }finally{c.dispose()}
})

it('accepts the actual CPU USD stored-view→bounded native reply on the strict client path',async()=>{
 const captured={expectedSceneRevision:actualUnion.sceneRevision,expectedProjectHash:actualUnion.projectHash}
 const validated=validateDiagnosticReply(actualUnion,captured);expect(validated).toEqual(actualUnion);expect(validated.view.backgroundGridRaster?.metricVersion).toBe(2)
 const sent:any[]=[],c=new OvCommandClient(async p=>{sent.push(p)},100)
 try{const pending=c.readViewDiagnostics(captured),raw=structuredClone(actualUnion);raw.requestId=JSON.parse(sent[0].message).requestId;c.receive({event_type:'dcdtuni:renderAck',payload:raw});expect((await pending).view.backgroundGridRaster).toEqual(actualUnion.view.backgroundGridRaster)}finally{c.dispose()}
})

it('validates the actual IExt update→plain read→bounded ACK literal on the strict client path',async()=>{
 const captured={expectedSceneRevision:extensionUnion.sceneRevision,expectedProjectHash:extensionUnion.projectHash},validated=validateDiagnosticReply(extensionUnion,captured)
 expect(validated).toEqual(extensionUnion);expect(validated.view.backgroundGridRaster?.metricVersion).toBe(2);expect((validated.diagnostics.viewport as any).layoutProof).toBe('unresolved')
 const sent:any[]=[],c=new OvCommandClient(async p=>{sent.push(p)},100)
 try{const pending=c.readViewDiagnostics(captured),raw=structuredClone(extensionUnion);raw.requestId=JSON.parse(sent[0].message).requestId;c.receive({event_type:'dcdtuni:renderAck',payload:raw});expect((await pending).view).toEqual(extensionUnion.view)}finally{c.dispose()}
})
