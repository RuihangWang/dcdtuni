import {afterEach,expect,it,vi} from 'vitest'
import {requestCanonicalRenderGrant} from '../src/postprocess/commonRenderIntent'
import {bindNumericProduct,requireNumericProduct} from '../src/postprocess/numericProducts'
const hash=(v:string)=>v.repeat(64),session={runId:hash('9'),timeValue:0.02,revision:'actual-response'}
const source={projectUid:'p',projectContentSha256:hash('a'),cfdrunSha256:hash('b'),meshReceiptSha256:hash('c'),meshQuality:'accepted' as const,run:{uid:'execution',receiptSha256:hash('d')}}
afterEach(()=>vi.unstubAllGlobals())
it('keeps response receipts bound to the decoded object, exact PP session and native bytes',()=>{
 const value={},receipt={schema:'dcdtuni.numeric-product-receipt/v1',token:hash('1'),kind:'slice',postprocess:session,payloadSha256:hash('2'),nativeInputSha256:hash('3')}
 bindNumericProduct(value,new Response(null,{headers:{'X-PP-Product':JSON.stringify(receipt)}}),'slice',session)
 expect(requireNumericProduct(value,'slice',session,hash('3'))).toBe(hash('1'))
 for(const [object,kind,selected,native]of [[{},'slice',session,hash('3')],[value,'field',session,hash('3')],[value,'slice',{...session,timeValue:0.01},hash('3')],[value,'slice',session,hash('4')]]as const)expect(()=>requireNumericProduct(object,kind,selected,native)).toThrow()
})
it('relays only common intent and accepts structural source identity despite canonical JSON key ordering',async()=>{
 const intent={schema:'dcdtuni.common-render-intent/v1' as const,revision:4,postprocess:session,source,meshToken:hash('1'),surface:{fieldToken:hash('2'),field:'T',lut:{palette:'viridis' as const,range:[290,310]as [number,number],opacity:0.4},hidden:[],visible:true},slices:[],streamlines:[],volume:null}
 const reply={grant:{payload:{kind:'rendererResult'},signature:hash('5')},packetReceipt:{sha256:hash('6'),revision:4,source:{run:source.run,meshQuality:source.meshQuality,meshReceiptSha256:source.meshReceiptSha256,cfdrunSha256:source.cfdrunSha256,projectContentSha256:source.projectContentSha256,projectUid:source.projectUid},timeSeconds:0.02,postprocess:session,sceneRevision:8,displayGeneration:12,runConvergence:{verdict:'NOT_CONVERGED',receiptSha256:hash('7')}}}
 const fetcher=vi.fn(async(_url:unknown,_init:RequestInit)=>new Response(JSON.stringify(reply),{headers:{'X-PP-Revision':session.revision}}));vi.stubGlobal('fetch',fetcher)
 expect(await requestCanonicalRenderGrant(intent,{projectUid:'p',projectHash:hash('a'),sceneRevision:8},12)).toEqual(reply)
 expect(JSON.parse(fetcher.mock.calls[0]![1]!.body as string)).toMatchObject({displayGeneration:12,intent})
 reply.packetReceipt.timeSeconds=0.01
 await expect(requestCanonicalRenderGrant(intent,{projectUid:'p',projectHash:hash('a'),sceneRevision:8},12)).rejects.toThrow(/another source, frame/)
})

it('keeps rejected grants rejected and exposes a bounded plain-text source reason',async()=>{
 const intent={schema:'dcdtuni.common-render-intent/v1' as const,revision:4,postprocess:session,source,meshToken:hash('1'),surface:{fieldToken:hash('2'),field:'T',lut:{palette:'viridis' as const,range:[290,310]as [number,number],opacity:0.4},hidden:[],visible:true},slices:[],streamlines:[],volume:null}
 const scene={projectUid:'p',projectHash:hash('a'),sceneRevision:8}
 let body:Record<string,unknown>={error:'Verified result display is unavailable for this frame',reason:'acknowledged scene differs from the accepted source project'}
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify(body),{status:422})))
 await expect(requestCanonicalRenderGrant(intent,scene,12)).rejects.toThrow('unavailable for this frame (acknowledged scene differs from the accepted source project)')
 body={error:'Rejected',reason:{secret:'not a text reason'}}
 await expect(requestCanonicalRenderGrant(intent,scene,12)).rejects.toThrow('verification failed: Rejected')
 body={error:'Rejected',reason:'x'.repeat(500)}
 await expect(requestCanonicalRenderGrant(intent,scene,12)).rejects.toThrow('Rejected ('+'x'.repeat(384)+')')
})

it.each(['source run','source project','scene generation','display generation','missing signature','missing receipt','PP session time'] as const)(
 'refuses a canonical grant with mismatched or absent %s',async(mismatch)=>{
  const intent={schema:'dcdtuni.common-render-intent/v1' as const,revision:4,postprocess:session,source,meshToken:hash('1'),surface:{fieldToken:hash('2'),field:'T',lut:{palette:'viridis' as const,range:[290,310] as [number,number],opacity:0.4},hidden:[],visible:true},slices:[],streamlines:[],volume:null}
  const scene={projectUid:'p',projectHash:hash('a'),sceneRevision:8}
  const reply={grant:{payload:{kind:'rendererResult'},signature:hash('5')},packetReceipt:{sha256:hash('6'),revision:4,source:structuredClone(source),timeSeconds:0.02,postprocess:{...session},sceneRevision:8,displayGeneration:12,runConvergence:{verdict:'NOT_CONVERGED',receiptSha256:hash('7')}}}
  if(mismatch==='source run')reply.packetReceipt.source.run.uid='different-execution'
  if(mismatch==='source project')reply.packetReceipt.source.projectUid='different-project'
  if(mismatch==='scene generation')reply.packetReceipt.sceneRevision=9
  if(mismatch==='display generation')reply.packetReceipt.displayGeneration=13
  if(mismatch==='PP session time')reply.packetReceipt.postprocess.timeValue=0.01
  const body:Record<string,unknown>=reply
  if(mismatch==='missing signature')body.grant={payload:reply.grant.payload}
  if(mismatch==='missing receipt')delete body.packetReceipt
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify(body),{headers:{'X-PP-Revision':session.revision}})))
  await expect(requestCanonicalRenderGrant(intent,scene,12)).rejects.toThrow()
 })
it('sends the exact shared frame, source and scene with the caller abort signal; source-binding refusal stays visible',async()=>{
 const intent={schema:'dcdtuni.common-render-intent/v1' as const,revision:4,postprocess:session,source,meshToken:hash('1'),surface:{fieldToken:hash('2'),field:'T',lut:{palette:'viridis' as const,range:[290,310] as [number,number],opacity:0.4},hidden:[],visible:true},slices:[],streamlines:[],volume:null}
 const scene={projectUid:'p',projectHash:hash('a'),sceneRevision:8},controller=new AbortController()
 const fetcher=vi.fn(async(_url:string,_options:RequestInit)=>new Response(JSON.stringify({error:'Source is not displayable',reason:'Missing object_bindings for the selected source'}),{status:422}))
 vi.stubGlobal('fetch',fetcher)
 await expect(requestCanonicalRenderGrant(intent,scene,12,controller.signal)).rejects.toThrow('object_bindings')
 const [url,options]=fetcher.mock.calls[0]!
 expect(url).toBe('/api/pp/render-grant');expect(options.method).toBe('POST');expect(options.signal).toBe(controller.signal)
 expect(JSON.parse(String(options.body))).toEqual({schema:'dcdtuni.canonical-render-request/v1',scene:{projectUid:'p',projectContentSha256:hash('a'),revision:8},displayGeneration:12,intent})
})
