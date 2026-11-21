import {expect,it} from 'vitest'
import controlled from './fixtures/controlled-render-output-a-reply.json'
import oldLiteralReply from './fixtures/actual-bounded-native-reply.json'
import {validateDiagnosticReply,diagnosticReceipt} from '../src/renderer/ovDisplayDiagnostics'
const context={expectedSceneRevision:controlled.sceneRevision,expectedProjectHash:controlled.projectHash,roofModelUid:controlled.diagnostics.roof.modelUid}
const copy=()=>structuredClone(controlled) as any
it('accepts actual production IExt/CPUUSD route with controlled native getter values; delivered/UI projection distinct and no layout/scene-frame claim',()=>{
 const v=copy(),r=validateDiagnosticReply(v,context),a=r.diagnostics.renderOutputObservations as any
 expect(a.deliveredViewportFrame.projection).not.toEqual(a.viewportCanvas.projection)
 expect(a.framebuffer.size).toEqual([1600,638]);expect(a.deliveredViewportFrame.resolution).toEqual([1600,900]);expect(a.applicationWindow.dpiScale).toBe(1.25)
 expect(a.layoutProof).toBe('unresolved');expect(a.frameSourceBindingProof).toBe('unresolved');expect(a.viewportWindow.innerContentRect).toBeNull()
 v.diagnostics.renderOutputObservations.framebuffer.size[0]=1;expect(a.framebuffer.size[0]).toBe(1600)
 expect(diagnosticReceipt(r)).toContain('renderOutputObservations')
})
it('legacy literal reply without optional observations remains byte-shaped compatible',()=>expect(validateDiagnosticReply(oldLiteralReply,context)).toEqual(oldLiteralReply))
const mutations:Record<string,(a:any)=>void>={
 sourceRevision:a=>a.observationContext.sceneRevision++,sourceHash:a=>a.observationContext.projectHash='b'.repeat(64),
 sceneFramePromotion:a=>a.frameSourceBindingProof='accepted',layoutPromotion:a=>a.layoutProof='whole-frame',
 unknownPayload:a=>a.SDP='not permitted',unknownFrameResource:a=>a.deliveredViewportFrame.resource={},
 negativeFrame:a=>a.deliveredViewportFrame.frameNumber=-1,boolFrame:a=>a.deliveredViewportFrame.frameNumber=true,oversizedFrame:a=>a.deliveredViewportFrame.frameNumber=9007199254740992,
 boolGeneration:a=>a.observationContext.displayGeneration=true,oversizedGeneration:a=>a.observationContext.displayGeneration=9007199254740992,
 shortMatrix:a=>a.deliveredViewportFrame.projection.pop(),boolMatrix:a=>a.deliveredViewportFrame.view[0]=true,NaNMatrix:a=>a.deliveredViewportFrame.view[0]=NaN,
 hugeMatrix:a=>a.viewportCanvas.projection[0]=1e19,
 zeroDim:a=>a.framebuffer.size[0]=0,pixelBudget:a=>a.deliveredViewportFrame.resolution=[16384,16384],decimalDim:a=>a.viewportCanvas.resolution[0]=1600.5,
 boolDim:a=>a.applicationWindow.size[0]=true,unattachedObserved:a=>a.framebuffer.attached=false,
 boolDpi:a=>a.applicationWindow.dpiScale=true,negativeDpi:a=>a.applicationWindow.dpiScale=-1,
 stringViewport:a=>a.viewportOwner.viewportId=17,wrongViewport:a=>a.widgetAssociation.viewportId='foreign',
 badCamera:a=>a.viewportOwner.cameraPath='/World/Other',longProduct:a=>a.viewportOwner.renderProductPath='p'.repeat(513),
 utf8Product:a=>a.viewportOwner.renderProductPath='😀'.repeat(256),controlProduct:a=>a.viewportOwner.renderProductPath='a\u0000b',
 guessedInnerRect:a=>a.viewportWindow.innerContentRect=[0,0,1600,638],guessedDpiUnit:a=>a.viewportWindow.dimensionUnit='screen-pixel',
 unavailableExtra:a=>a.deliveredViewportFrame={status:'unavailable',reason:'missing-public-value',frameNumber:1},
 unknownReason:a=>a.viewportCanvas={status:'unavailable',reason:'native-exception-payload'},
}
for(const[label,mutate]of Object.entries(mutations))it(`refuses ${label} observation without source/layout relaxation`,()=>{const v=copy();mutate(v.diagnostics.renderOutputObservations);expect(()=>validateDiagnosticReply(v,context)).toThrow()})
it('native unsupported/unavailable observation is optional, bounded and honest',()=>{
 const v=copy();v.diagnostics.renderOutputObservations={schema:'dcdtuni.render-output-observations/v1',status:'unavailable',reason:'unsupported-runtime',layoutProof:'unresolved',frameSourceBindingProof:'unresolved'}
 expect((validateDiagnosticReply(v,context).diagnostics.renderOutputObservations as any).status).toBe('unavailable')
})
