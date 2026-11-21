import {afterEach,expect,it} from 'vitest'
import {readFileSync} from 'node:fs'
import {join} from 'node:path'
import {decodeSlice,decodeStreamlines} from '../src/postprocess'
import {bindNumericProduct} from '../src/postprocess/numericProducts'
import {commonRenderIntent} from '../src/postprocess/commonRenderIntent'
import {publishWindResultFrame,type WindResultFrame} from '../src/renderer/windResultProducer'
import {clearResultLayerSet} from '../src/renderer/resultLayerRevision'
const hash=(v:string)=>v.repeat(64)
function frame():WindResultFrame {
 const session={runId:hash('9'),timeValue:0.02,revision:'controlled-response'}
 const owned=<T extends object>(value:T,kind:'mesh'|'field'|'slice'|'streamline')=>bindNumericProduct(value,new Response(null,{headers:{'X-PP-Product':JSON.stringify({schema:'dcdtuni.numeric-product-receipt/v1',token:hash(({mesh:'1',field:'2',slice:'3',streamline:'4'})[kind]),kind,postprocess:session,payloadSha256:hash('5'),nativeInputSha256:hash('6')})}}),kind,session)
 const binary=(name:string)=>{const raw=readFileSync(join(__dirname,'__fixtures__',name));return raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength)}
 const cut=owned(decodeSlice(binary('pp_slice_small.bin')),'slice'),stream=owned(decodeStreamlines(binary('pp_streamlines_small.bin')),'streamline')
 return {session,descriptor:{schema:'dcdtuni.verified-air-result-source/v2',postprocess:session,nativeInputSha256:hash('6'),runConvergence:{verdict:'NOT_CONVERGED',receiptSha256:hash('7')},source:{projectUid:'p',projectContentSha256:hash('a'),cfdrunSha256:hash('b'),meshReceiptSha256:hash('c'),meshQuality:'accepted',run:{uid:'execution',receiptSha256:hash('d')}},fieldUnits:{T:'K',speed:'m/s',[stream.field]:'m/s',k:'m^2/s^2'},boundaryProvenanceByPatch:{wall:{schema:'dcdtuni.native-boundary-provenance/v1',patchRanges:[{bindingKind:'meshBoundaryOnly',rank:0,patchIndex:0,nativePatchName:'wall',startFace:3,nFaces:1,boundarySha256:hash('e'),sourceRegions:[]}]}}},
  currentProject:{uid:'p',contentSha256:hash('a')},mesh:owned({positions:new Float32Array([0,0,0,1,0,0,0,1,0]),indices:new Uint32Array([0,1,2]),edges:new Uint32Array([0,1,1,2,2,0]),patches:[{name:'wall',nPoints:3,nTriangles:1,nEdges:3,defaultVisible:true}],totals:{points:3,triangles:1,edges:3}},'mesh'),activeSurfaceField:'T',surface:{field:'T',values:owned(new Float32Array([300,301,302]),'field'),colorMap:'viridis',range:[290,310],opacity:0.4,hidden:new Set()},
  slices:[3,8].map(id=>({id,payload:cut,origin:[0,0.5,0],normal:[0,1,0],colorMap:'coolwarm',range:[288,295],opacity:0.2,visible:id===8})),
  streamlines:[2,9].map(id=>({id,set:stream,colorMap:'jet',range:[0,1],opacity:0.8,tubeRadius:0.01,visible:id===2}))}
}
afterEach(()=>clearResultLayerSet())
it('shared native packet and intent preserve ordered numeric multi-set identities and visibility',()=>{
 const value=frame(),packet=publishWindResultFrame(value),intent=commonRenderIntent(value,packet.revision)
 expect(packet.schema).toBe('dcdtuni.render-result-layers/v2');expect(packet.runConvergence?.verdict).toBe('NOT_CONVERGED')
 expect(packet.layers.map(layer=>layer.kind)).toEqual(['meshEdges','surfaceField','slice','streamline'])
 expect(intent.slices.map(set=>[set.setId,set.visible])).toEqual([[3,false],[8,true]])
 expect(intent.streamlines.map(set=>[set.setId,set.visible])).toEqual([[2,true],[9,false]])
 expect(intent.slices[1]!.sourceUid).toContain('/time-0.02/slice-8');expect(intent.streamlines[0]!.sourceUid).toContain('/time-0.02/seed-2')
 expect(packet.layers[0]).not.toHaveProperty('sourceFaceUids')
 value.surface.hidden.add('wall');expect(commonRenderIntent(value,packet.revision).surface.hidden).toEqual(['wall'])
})
it('optional native scalar uses the actual dimensional receipt and invalid provenance never publishes',()=>{
 const value=frame();value.activeSurfaceField='k';value.surface.field='k';value.slices=[];value.streamlines=[]
 expect(publishWindResultFrame(value).layers[1]).toMatchObject({field:{name:'k',unit:'m^2/s^2'}})
 if(value.descriptor.schema==='dcdtuni.verified-air-result-source/v2')value.descriptor.boundaryProvenanceByPatch.wall!.patchRanges[0]!.bindingKind='regionFacetInventory'
 expect(()=>publishWindResultFrame(value)).toThrow(/inventory/)
})

it('common volume shares the same pinned numerical response and keeps every public parameter',()=>{
 const value=frame(),payload={dimensions:[16,16,16]as [number,number,number],origin:[1,2,3]as [number,number,number],spacing:[.1,.2,.3]as [number,number,number],scalarRange:[290,310]as [number,number],sentinel:-9999,scalars:new Float32Array(16**3).fill(300)}
 bindNumericProduct(payload,new Response(null,{headers:{'X-PP-Product':JSON.stringify({schema:'dcdtuni.numeric-product-receipt/v1',token:hash('8'),kind:'volume',postprocess:value.session,payloadSha256:hash('5'),nativeInputSha256:hash('6')})}}),'volume',value.session)
 const volume={field:'T',unit:'K',visible:true,payload,range:[291,309]as [number,number],palette:'jet'as const,opacity:0.13,resolution:16}
 const intent=commonRenderIntent(value,4,volume)
 expect(intent.volume).toEqual({field:'T',unit:'K',token:hash('8'),resolution:16,lut:{palette:'jet',range:[291,309],opacity:0.13},visible:true})
 expect(()=>commonRenderIntent(value,4,{...volume,unit:'degC'})).toThrow(/unit/)
 expect(()=>commonRenderIntent(value,4,{...volume,resolution:128})).toThrow(/resolution/)
 expect(()=>commonRenderIntent(value,4,{...volume,payload:null})).toThrow(/no actual/)
 expect(commonRenderIntent(value,4,{...volume,visible:false}).volume?.visible).toBe(false)
})
