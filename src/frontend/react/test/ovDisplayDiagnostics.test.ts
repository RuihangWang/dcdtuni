import {afterEach,expect,it,vi} from 'vitest'
import {OvCommandClient} from '../src/renderer/ovProtocol'
import {captureDiagnosticContext,diagnosticRequest,validateDiagnosticReply,selectedAcceptedFloor,diagnosticReceipt} from '../src/renderer/ovDisplayDiagnostics'
import sample from '../../shared/schema/render-display-diagnostics.schema.json'
const context={expectedSceneRevision:1,expectedProjectHash:'a'.repeat(64)}
function body(id='reply'):any {return {requestId:id,ok:true,sceneRevision:1,projectHash:'a'.repeat(64),view:{selectedUids:[],timeSeconds:null},diagnostics:{schema:'dcdtuni.render-display-diagnostics/v1',profile:'bounded-display-v1',sampleSequence:1,sampleElapsedMs:.1,pixelVerified:false,renderer:{settingsScope:'effective-carbs-settings; not compiled-shader proof',settings:Object.fromEntries(['translucencyAsOpacity','translucencyEnabled','raytracingFractionalCutoutOpacity','pathtracingFractionalCutoutOpacity'].map(x=>[x,{status:'unavailable',reason:'missing-setting'}]))},viewport:{status:'pending',reason:'layout-unresolved',layoutProof:'unresolved',textureToEncodedMapping:'unresolved'},roof:{status:'unavailable',reason:'target-missing',targetSource:'missing'}}}}
const clients:OvCommandClient[]=[]
afterEach(()=>{clients.splice(0).forEach(c=>c.dispose());vi.useRealTimers()})
function pair(){const sent:{command_name:string;message:string}[]=[];const c=new OvCommandClient(async p=>{sent.push(p)},50);clients.push(c);return {c,sent}}
it('sends one fixed context and preserves owned pending identity after caller mutation',async()=>{
 const {c,sent}=pair();const mutable={...context};const p=c.readViewDiagnostics(mutable);mutable.expectedProjectHash='b'.repeat(64)
 await Promise.resolve();expect(sent).toHaveLength(1);const args=JSON.parse(sent[0]!.message)
 expect(args.diagnostics).toEqual({profile:'bounded-display-v1',...context});expect(new TextEncoder().encode(sent[0]!.message).length).toBeLessThanOrEqual(1024)
 c.receive({event_type:'dcdtuni:renderAck',payload:body(args.requestId)});expect((await p).projectHash).toBe(context.expectedProjectHash)
})
it('diagnostic is single-flight, no retry or extra command',async()=>{const {c,sent}=pair();const p=c.readViewDiagnostics(context);await expect(c.readViewDiagnostics(context)).rejects.toThrow(/pending/);c.receive({event_type:'dcdtuni:renderAck',payload:body(JSON.parse(sent[0]!.message).requestId)});await p;expect(sent).toHaveLength(1)})
for(const [label,mutate] of Object.entries({
 nonemptySelectionObject:(v:any)=>{v.view.selectedUids={foreign:'path'}},
 selectionString:(v:any)=>{v.view.selectedUids='rack'},
 badTime:(v:any)=>{v.view.timeSeconds='1'},
 nonfiniteTime:(v:any)=>{v.view.timeSeconds=NaN},
 wrongHash:(v:any)=>{v.projectHash='b'.repeat(64)},
 wrongRevision:(v:any)=>{v.sceneRevision=2},
 unknownAsset:(v:any)=>{v.diagnostics.asset='https://private/asset'},
 resolvedLayout:(v:any)=>{v.diagnostics.viewport.layoutProof='owned-native-frame'},
 pixelClaim:(v:any)=>{v.diagnostics.pixelVerified=true},
 badMatrix:(v:any)=>{v.diagnostics.viewport.view=Array(15).fill(0)},
 nonfiniteMatrix:(v:any)=>{v.diagnostics.viewport.view=Array(16).fill(Infinity)},
 settingsDefault:(v:any)=>{v.diagnostics.renderer.settings.translucencyEnabled={status:'observed',reason:'none'}},
 viewBytes:(v:any)=>{v.view.selectedUids=Array(8).fill('😀'.repeat(128))},
 payloadLimit:(v:any)=>{v.diagnostics.extra='a'.repeat(16385)},
 cameraPath:(v:any)=>{v.view.camera={position:[0,0,1],target:[0,0,0],up:[0,1,0],fov:45,aspect:1,near:.1,far:10,sourcePath:'/foreign'}},
})){it(`rejects raw ${label} before loose normalization`,async()=>{const {c,sent}=pair();const p=c.readViewDiagnostics(context);const v=body(JSON.parse(sent[0]!.message).requestId);mutate(v);c.receive({event_type:'dcdtuni:renderAck',payload:v});await expect(p).rejects.toThrow(/bounded|Bounded|display|Nonfinite/);expect(sent).toHaveLength(1)})}
it('only the known Carb empty/omitted transport shapes normalize',()=>{const v=body();v.view.selectedUids={};delete v.view.timeSeconds;expect(validateDiagnosticReply(v,context).view).toEqual({selectedUids:[],timeSeconds:null});expect(v.view).toEqual({selectedUids:{}})})
it('takes an owned ACK copy, so post-receive mutation cannot alter view or receipt',async()=>{const {c,sent}=pair();const p=c.readViewDiagnostics(context);const v=body(JSON.parse(sent[0]!.message).requestId);c.receive({event_type:'dcdtuni:renderAck',payload:v});v.view.selectedUids.push('late');v.diagnostics.pixelVerified=true;const reply=await p;expect(reply.view.selectedUids).toEqual([]);expect(diagnosticReceipt(reply)).toContain('"pixelVerified":false')})
it('plain readView retains old transport normalization without diagnostic work',async()=>{const {c,sent}=pair();const p=c.readView();const args=JSON.parse(sent[0]!.message);expect(Object.keys(args)).toEqual(['requestId']);c.receive({event_type:'dcdtuni:renderAck',payload:{requestId:args.requestId,ok:true,view:{selectedUids:{nonempty:'legacy'},timeSeconds:'legacy'}}});expect((await p).view).toEqual({selectedUids:[],timeSeconds:null})})
it('readiness uses the existing camera-handoff without diagnostic arguments',async()=>{const {c,sent}=pair();const p=c.awaitOutboundReady(1,50);const args=JSON.parse(sent[0]!.message);expect(args).toEqual({requestId:expect.any(String),purpose:'camera-handoff'});expect(args).not.toHaveProperty('diagnostics');c.receive({event_type:'dcdtuni:renderAck',payload:{requestId:args.requestId,ok:false,error:'not-ready'}});expect((await p).ok).toBe(false)})
it('a timed-out late diagnostic reply has no consumer effect',async()=>{vi.useFakeTimers();const {c,sent}=pair();const apply=vi.fn();const p=c.readViewDiagnostics(context).then(apply,e=>e.message);await vi.advanceTimersByTimeAsync(3100);expect(await p).toMatch(/timed out/);c.receive({event_type:'dcdtuni:renderAck',payload:body(JSON.parse(sent[0]!.message).requestId)});await Promise.resolve();expect(apply).not.toHaveBeenCalled();expect(sent).toHaveLength(1)})
it('unknown/malformed context refuses before transport; owned context is frozen',()=>{const {c,sent}=pair();expect(()=>c.readViewDiagnostics({...context,expectedSceneRevision:-1})).toThrow();expect(()=>diagnosticRequest('id',{...context,roofModelUid:'/foreign'})).toThrow();expect(Object.isFrozen(captureDiagnosticContext(context))).toBe(true);expect(sent).toHaveLength(0);expect(sample.additionalProperties).toBe(false)})
it('floor identity comes from accepted UID+actual construction, never a name or opacity',()=>{const p={constructions:{floors:{f:{uid:'f',kind:'floor'},x:{uid:'x',kind:'box',name:'roof',opacity:.15}}}};const accepted=new Set(['f','x']);expect(selectedAcceptedFloor(p,['f'],accepted)).toBe('f');expect(selectedAcceptedFloor(p,['x'],accepted)).toBeUndefined();expect(selectedAcceptedFloor(p,['f','x'],accepted)).toBeUndefined();expect(selectedAcceptedFloor(p,['f'],new Set())).toBeUndefined();expect(selectedAcceptedFloor({constructions:{floors:{f:{uid:'foreign',kind:'floor'}}}},['f'],accepted)).toBeUndefined()})

function outputBody():any {
 const v=body(),unavailable=()=>({status:'unavailable',reason:'missing-public-value'})
 v.diagnostics.renderOutputObservations={schema:'dcdtuni.render-output-observations/v1',status:'observed',reason:'none',layoutProof:'unresolved',frameSourceBindingProof:'unresolved',
  observationContext:{sceneRevision:1,projectHash:'a'.repeat(64),displayGeneration:1},viewportOwner:unavailable(),deliveredViewportFrame:unavailable(),viewportCanvas:unavailable(),applicationWindow:unavailable(),framebuffer:unavailable(),
  viewportWindow:{status:'observed',reason:'none',rectKind:'outerViewportWindowFrame',positionUnit:'screen-pixel',dimensionUnit:'native-raw-unresolved',position:[0,0],size:[1600,900],contentSize:[1600,900],innerContentRect:null,innerContentProof:'unresolved'},
  widgetAssociation:{status:'observed',reason:'none',viewportId:'1',name:'Viewport',visible:true,innerContentRect:null,innerContentProof:'unresolved'}}
 return v
}
it('restores only omitted unresolved inner rectangles from the Carb transport',()=>{
 const v=outputBody(),o=v.diagnostics.renderOutputObservations
 delete o.viewportWindow.innerContentRect;delete o.widgetAssociation.innerContentRect
 const reply=validateDiagnosticReply(v,context),out:any=reply.diagnostics.renderOutputObservations
 expect(out.viewportWindow.innerContentRect).toBeNull();expect(out.widgetAssociation.innerContentRect).toBeNull()
 expect(out.layoutProof).toBe('unresolved');expect(out.frameSourceBindingProof).toBe('unresolved');expect(reply.diagnostics.pixelVerified).toBe(false)
 expect(o.viewportWindow).not.toHaveProperty('innerContentRect');expect(o.widgetAssociation).not.toHaveProperty('innerContentRect')
})
for(const name of ['viewportWindow','widgetAssociation'])for(const bad of [[],{},[0,0,1,1]])it(`rejects non-null ${name} rectangle instead of normalizing it`,()=>{
 const v=outputBody();v.diagnostics.renderOutputObservations[name].innerContentRect=bad;expect(()=>validateDiagnosticReply(v,context)).toThrow(/schema/)
})
it('an omitted rectangle cannot grant a claimed layout or observed bounds',()=>{
 const v=outputBody(),o=v.diagnostics.renderOutputObservations;delete o.viewportWindow.innerContentRect;o.viewportWindow.innerContentProof='verified';expect(()=>validateDiagnosticReply(v,context)).toThrow(/schema/)
})
it('unavailable observations retain their exact schema without added null members',()=>{
 const v=outputBody();v.diagnostics.renderOutputObservations.widgetAssociation={status:'unavailable',reason:'missing-public-value'}
 expect((validateDiagnosticReply(v,context).diagnostics.renderOutputObservations as any).widgetAssociation).toEqual(v.diagnostics.renderOutputObservations.widgetAssociation)
})


function materialBody():any {
 const v=body(),uid='roof-fixture',g='/World/RenderScene/Geometry/p_'+'1'.repeat(24),m='/World/RenderScene/Looks/p_'+'1'.repeat(24),s=m+'/Surface'
 const scalar={status:'observed',reason:'none',connected:false,authoredValue:.6}
 v.primitives=818
 v.diagnostics.roof={status:'observed',reason:'none',targetSource:'single-selected-accepted-source-floor',modelUid:uid,primitiveCount:1,materials:[{geometryPath:g,materialPath:m,surfacePath:s,shaderId:'UsdPreviewSurface',bindingPurpose:'allPurpose',opacity:{...scalar},metallic:{...scalar,authoredValue:0},opacityThreshold:{...scalar,authoredValue:0},compiledShaderState:'not-read'}]}
 const observation='active-material-backend-parameters-not-final-GPU-program-or-pixels'
 v.diagnostics.activeMaterialParameters={status:'observed',reason:'none',observation,context:{sceneRevision:1,projectHash:'a'.repeat(64),primitives:818,roofModelUid:uid,displayGeneration:1},records:[{modelUid:uid,geometryPath:g,materialPath:m,surfacePath:s,materialBackendFunctionName:'::rtx::UsdPreviewSurface::UsdPreviewSurface',flags:{enable_opacity:{status:'observed',value:false},enable_specular_transmission:{status:'unavailable',reason:'not-constant-or-absent'}},observation}],sourceContextStable:true,compiledGPUProgramRead:false,encodedPixelsRead:false,settingsAndUSDMutated:false}
 return v
}
it('accepts optional actual backend false and typed absent flags without creating a default',()=>{
 const reply=validateDiagnosticReply(materialBody(),{...context,roofModelUid:'roof-fixture'}),m:any=reply.diagnostics.activeMaterialParameters
 expect(m.records[0].flags.enable_opacity).toEqual({status:'observed',value:false})
 expect(m.records[0].flags.enable_specular_transmission).not.toHaveProperty('value')
 expect(reply.diagnostics.pixelVerified).toBe(false)
})
for(const [name,mutate] of Object.entries({
 staleContext:(v:any)=>{v.diagnostics.activeMaterialParameters.context.projectHash='b'.repeat(64)},
 wrongUID:(v:any)=>{v.diagnostics.activeMaterialParameters.records[0].modelUid='foreign'},
 wrongBinding:(v:any)=>{v.diagnostics.activeMaterialParameters.records[0].surfacePath='/World/RenderScene/Looks/p_'+'2'.repeat(24)+'/Surface'},
 duplicateBinding:(v:any)=>{v.diagnostics.activeMaterialParameters.records.push({...v.diagnostics.activeMaterialParameters.records[0]})},
 nativeResource:(v:any)=>{v.diagnostics.activeMaterialParameters.records[0].resource='native-pointer'},
 fakeDefault:(v:any)=>{v.diagnostics.activeMaterialParameters.records[0].flags.enable_specular_transmission={status:'unavailable',reason:'not-constant-or-absent',value:false}},
 compiledClaim:(v:any)=>{v.diagnostics.activeMaterialParameters.compiledGPUProgramRead=true},
 wrongCount:(v:any)=>{v.diagnostics.activeMaterialParameters.context.primitives=817},
 utf8Function:(v:any)=>{v.diagnostics.activeMaterialParameters.records[0].materialBackendFunctionName='😀'.repeat(300)},
})){it(`refuses optional active material ${name}`,()=>{const v=materialBody();mutate(v);expect(()=>validateDiagnosticReply(v,{...context,roofModelUid:'roof-fixture'})).toThrow()})}
it('typed inactive backend is optional and does not weaken the old canonical scene gate',()=>{
 const v=body();v.diagnostics.activeMaterialParameters={status:'unavailable',reason:'backend-unavailable',observation:'active-material-backend-parameters-not-final-GPU-program-or-pixels'}
 expect(validateDiagnosticReply(v,context).diagnostics.activeMaterialParameters).toEqual(v.diagnostics.activeMaterialParameters)
 v.projectHash='b'.repeat(64);expect(()=>validateDiagnosticReply(v,context)).toThrow(/Stale/)
})


function mdlMaterialBody(status:'observed'|'pending-module'|'unavailable'='observed'):any {
 const v=materialBody(),a=v.diagnostics.activeMaterialParameters,r=a.records[0]
 r.surfacePath=r.materialPath+'/FractionalSurface';r.sourceType='mdl';delete r.flags
 r.moduleLoaded=status!=='pending-module';r.backendStatus=status;r.backendReason=status==='observed'?'none':status==='pending-module'?'module-not-loaded':'backend-unavailable'
 r.parameters=status==='observed'?{opacity:{status:'observed',value:.6},opacityThreshold:{status:'observed',value:0}}:{opacity:{status:'unavailable',reason:'not-read'},opacityThreshold:{status:'unavailable',reason:'not-read'}}
 r.finalCutout={status:'unavailable',reason:'compiled-material-not-exposed'}
 if(status==='observed')r.materialBackendFunctionName='::Controlled::DcdtuniFractionalPreview';else delete r.materialBackendFunctionName
 a.renderer={mode:{status:'observed',value:'RealTimePathTracing'},raytracingFractionalCutoutOpacity:{status:'observed',value:true},pathtracingFractionalCutoutOpacity:{status:'observed',value:true}}
 return v
}
for(const status of ['observed','pending-module','unavailable'] as const){it(`accepts bound native MDL ${status} without treating parameters as pixels`,()=>{
 const reply=validateDiagnosticReply(mdlMaterialBody(status),{...context,roofModelUid:'roof-fixture'}),r:any=(reply.diagnostics.activeMaterialParameters as any).records[0]
 expect(r.backendStatus).toBe(status);expect(r.finalCutout).toEqual({status:'unavailable',reason:'compiled-material-not-exposed'});expect(reply.diagnostics.pixelVerified).toBe(false)
 if(status!=='observed')expect(r.parameters.opacity).not.toHaveProperty('value')
})}
it('retains typed unavailable connected native opacity without fabricating a scalar',()=>{
 const v=mdlMaterialBody();v.diagnostics.activeMaterialParameters.records[0].parameters.opacity={status:'unavailable',reason:'not-constant-or-absent'}
 const r:any=(validateDiagnosticReply(v,{...context,roofModelUid:'roof-fixture'}).diagnostics.activeMaterialParameters as any).records[0]
 expect(r.parameters.opacity).not.toHaveProperty('value')
})
for(const [name,mutate] of Object.entries({
 wrongNativePath:(v:any)=>{v.diagnostics.activeMaterialParameters.records[0].surfacePath='/World/RenderScene/Looks/p_'+'2'.repeat(24)+'/FractionalSurface'},
 missingNativeType:(v:any)=>{delete v.diagnostics.activeMaterialParameters.records[0].sourceType},
 pendingFakeFunction:(v:any)=>{const r=v.diagnostics.activeMaterialParameters.records[0];r.moduleLoaded=false;r.backendStatus='pending-module';r.backendReason='module-not-loaded'},
 boolAsOpacity:(v:any)=>{v.diagnostics.activeMaterialParameters.records[0].parameters.opacity.value=false},
 claimedCutout:(v:any)=>{v.diagnostics.activeMaterialParameters.records[0].finalCutout={status:'observed',value:.6}},
 unknownRenderer:(v:any)=>{v.diagnostics.activeMaterialParameters.renderer.mode.value='unverified-render-mode'},
 fakeFractionalSetting:(v:any)=>{v.diagnostics.activeMaterialParameters.renderer.pathtracingFractionalCutoutOpacity.value='true'},
})){it(`refuses native MDL diagnostic ${name}`,()=>{const v=mdlMaterialBody();mutate(v);expect(()=>validateDiagnosticReply(v,{...context,roofModelUid:'roof-fixture'})).toThrow()})}
