/** Literal actual CPP init/mesh/field response fixture, produced by native511 reader.
 * Its acceptance/fields are CONTROLLED contracts, not physical solve evidence. */
import {readFileSync,writeFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {expect,it} from 'vitest'
import {commonRenderIntent} from '../src/postprocess/commonRenderIntent'
import {bindNumericProduct} from '../src/postprocess/numericProducts'
import {decodeMesh,decodeField,type InitResult} from '../src/postprocess'
import type {WindResultFrame} from '../src/renderer/windResultProducer'
const snapshot=JSON.parse(readFileSync(resolve(__dirname,'fixtures/pp_full_init/grant-input.json'),'utf8'))
const buffer=(value:string)=>Uint8Array.from(Buffer.from(value,'base64')).buffer
it('projects the actual full CPP InitResult to only its exact session before canonical transport',()=>{
 const init=snapshot.initResponse as InitResult
 expect(Object.keys(init)).toContain('message');expect(Object.keys(init)).toContain('success')
 const mesh=decodeMesh(buffer(snapshot.mesh.base64),snapshot.mesh.meta)
 const field=decodeField(buffer(snapshot.field.base64),snapshot.field.meta)
 bindNumericProduct(mesh,new Response(null,{headers:{'X-PP-Product':JSON.stringify(snapshot.mesh.receipt)}}),'mesh',init)
 bindNumericProduct(field.values,new Response(null,{headers:{'X-PP-Product':JSON.stringify(snapshot.field.receipt)}}),'field',init)
 const frame:WindResultFrame={descriptor:snapshot.descriptor,session:init,currentProject:{uid:snapshot.descriptor.source.projectUid,contentSha256:snapshot.descriptor.source.projectContentSha256},mesh,activeSurfaceField:'T',surface:{field:'T',values:field.values,colorMap:'jet',range:[299,301],opacity:.7,hidden:new Set(),visible:true},slices:[],streamlines:[]}
 const intent=commonRenderIntent(frame,1)
 // Only portable private REPRO sets this path. Normal/CI runs do not write artifacts.
 if(process.env.DCDTUNI_CONTROLLED_INTENT_OUTPUT)writeFileSync(process.env.DCDTUNI_CONTROLLED_INTENT_OUTPUT,JSON.stringify(intent))
 expect(intent.postprocess).toEqual(snapshot.descriptor.postprocess)
 expect(Object.keys(intent.postprocess).sort()).toEqual(['revision','runId','timeValue'])
 expect(init).toEqual(snapshot.initResponse) // retain status/message/cached metadata for the public Panel.
 for(const change of [{runId:'other'},{timeValue:.01},{revision:'other'}])expect(()=>commonRenderIntent({...frame,session:{...init,...change}},1)).toThrow()
})
