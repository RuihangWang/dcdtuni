/** Actual installed SDK5.18.2 class/converter/enum bytes, isolated from network.
 * Storage, clock and UI/log carriers are controlled; no browser acceptance. */
import { readFileSync, realpathSync } from 'node:fs'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import { expect, it } from 'vitest'
import { augmentOvSdk, OV_SDK_ANCHOR, OV_SDK_DIAGNOSTIC, OV_SDK_SHA256, OV_SDK_RETRY_GUARD, OV_SDK_TIMER_GUARD, OV_SDK_FIT_INSERT, OV_SDK_FIT_HELPER, ovSdkDiagnostics } from '../build/ovSdkDiagnostics'
const literal = readFileSync(resolve('node_modules/@nvidia/omniverse-webrtc-streaming-library/dist/omniverse-webrtc-streaming-library.js'),'utf8')
function classBytes(source:string,name:string) {
 const start=source.indexOf('class '+name+'{');expect(start).toBeGreaterThan(-1)
 let depth=0,quote='',escaped=false
 for(let i=source.indexOf('{',start);i<source.length;i++) {
  const c=source[i]!
  if(quote){if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c===quote)quote='';continue}
  if(c==='"'||c==="'"||c==='`'){quote=c;continue}
  if(c==='{')depth++;else if(c==='}'&&--depth===0)return source.slice(start,i+1)
 }
 throw new Error('actual SDK class not delimited')
}
const enumStart=literal.indexOf('e.RErrorCode=void 0,')+'e.RErrorCode=void 0,'.length
const enumEnd=literal.indexOf('(e.RErrorCode||(e.RErrorCode={}))',enumStart)+'(e.RErrorCode||(e.RErrorCode={}))'.length
const R:Record<string,any>={};new Function('e','return ('+literal.slice(enumStart,enumEnd)+');')(R)
const noOffer=R.RErrorCode.StreamerNoOffer as number
function pipeline(source:string,hook?: (event:unknown)=>void,cancel?:()=>unknown) {
 const effects:unknown[]=[],pending:Array<{callback:()=>void,ms:number}>=[]
 const storage=(o:object,k:string,value:unknown)=>Object.defineProperty(o,k,{value,writable:true,configurable:true,enumerable:true})
 const logger={event:(event:unknown)=>effects.push(['sdk-log',structuredClone(event)])}
 const status={success:'success',error:'error',warning:'warning'}
 const dom=class {constructor(){effects.push(['dom-created'])}}
 // The full actual SDK gl/dl/je/Rs classes and actual error enum are executed.
 // No substituted start/retry/converter implementations or hidden SDK state.
 const Client=new Function('R','ne','Ji','fe','$','ul','setTimeout','al',
   classBytes(source,'Rs')+';'+classBytes(source,'je')+';'+classBytes(source,'dl')+';'+classBytes(source,'gl')+';return gl')(
    R,storage,{startSessionResultEvent:'StartSessionResultEvent'},logger,status,dom,
    (callback:()=>void,ms:number)=>{effects.push(['timer',ms]);pending.push({callback,ms});return pending.length},
    (code:number)=>'0x'+(code>>>0).toString(16).toUpperCase().padStart(8,'0'))
 const client=new Client({maxReconnects:0,reconnectDelay:2000,mic:false,sessionId:'CONTROLLED_SESSION',onConnectionDiagnostic:hook,shouldCancelReconnect:cancel,
  onStart:(event:unknown)=>effects.push(['public-onStart',structuredClone(event)])})
 client.startButtonClick=()=>{effects.push(['retry-start'])}
 return {effects,pending,client}
}
it('binds exact installed bytes and preserves every original byte, including retry/timeout/cancellation branches',()=>{
 expect(createHash('sha256').update(literal).digest('hex')).toBe(OV_SDK_SHA256)
 const adapted=augmentOvSdk(literal);expect(adapted.replace(OV_SDK_DIAGNOSTIC,'').replace(OV_SDK_RETRY_GUARD,'').replace(OV_SDK_TIMER_GUARD,'').replace(OV_SDK_FIT_INSERT,'').replace('\n'+OV_SDK_FIT_HELPER,'')).toBe(literal)
 expect(()=>augmentOvSdk(literal.replace(OV_SDK_ANCHOR,'missingAnchor'))).toThrow(/anchor/)
 expect(()=>augmentOvSdk(literal+OV_SDK_ANCHOR)).toThrow(/anchor/)
 expect(()=>augmentOvSdk(literal+' ')).toThrow(/version/)
})
it('reproduces actual maxReconnects0 masking and captures the original native code before it is discarded',()=>{
 const receipts:unknown[]=[],before=pipeline(literal),after=pipeline(process.env.DCDTUNI_DIAGNOSTIC_BASELINE==='1'?literal:augmentOvSdk(literal),event=>receipts.push(event))
 expect(noOffer).toBeGreaterThan(0);const input={error:noOffer,sdp:'CONTROLLED_EXCLUDED_SDP',sessionId:'CONTROLLED_EXCLUDED_SESSION'}
 const original=JSON.stringify(input)
 expect(before.client.onStreamStart(input)).toBeUndefined();expect(after.client.onStreamStart(input)).toBeUndefined()
 expect(after.effects).toEqual(before.effects);expect(JSON.stringify(input)).toBe(original)
 expect((before.effects.at(-1)as any)[1]).toMatchObject({status:'warning',info:'1 of 1 connection attempts have failed. Retrying...'})
 expect(receipts).toEqual([{phase:'media-start',errorCode:noOffer}]);expect(JSON.stringify(receipts)).not.toContain('CONTROLLED_EXCLUDED')
 before.pending[0]!.callback();after.pending[0]!.callback();expect(after.effects).toEqual(before.effects)
})
it.each([null,true,-1,0x100000000,NaN,Infinity,'CONTROLLED_EXCLUDED_SECRET',{code:7,description:'CONTROLLED_EXCLUDED_SDP'}])('emits only uint32 or null for an unsupported error value',error=>{
 const receipts:unknown[]=[],before=pipeline(literal),after=pipeline(augmentOvSdk(literal),event=>receipts.push(event))
 expect(()=>before.client.onStreamStart({error})).not.toThrow();expect(()=>after.client.onStreamStart({error})).not.toThrow()
 expect(receipts).toEqual([{phase:'media-start',errorCode:null}]);expect(after.effects).toEqual(before.effects)
})
it('diagnostic exceptions cannot change the SDK callback, retry scheduling or original return',()=>{
 const before=pipeline(literal),after=pipeline(augmentOvSdk(literal),()=>{throw new Error('CONTROLLED diagnostic failure')})
 expect(before.client.onStreamStart({error:noOffer})).toBeUndefined();expect(after.client.onStreamStart({error:noOffer})).toBeUndefined()
 expect(after.effects).toEqual(before.effects);expect(after.client._numAttemptedConnections).toBe(before.client._numAttemptedConnections)
})
it('does not evaluate input getters for diagnostics; the original converter evaluates them exactly as before',()=>{
 let beforeReads=0,afterReads=0;const receipts:unknown[]=[],before=pipeline(literal),after=pipeline(augmentOvSdk(literal),event=>receipts.push(event))
 const a={get error(){beforeReads++;return noOffer}},b={get error(){afterReads++;return noOffer}}
 before.client.onStreamStart(a);after.client.onStreamStart(b);expect(afterReads).toBe(beforeReads);expect(beforeReads).toBe(1)
 expect(receipts).toEqual([{phase:'media-start',errorCode:null}]);expect(after.effects).toEqual(before.effects)
})
it('success and absent diagnostic callback preserve actual original SDK effects and retry counter reset',()=>{
 const before=pipeline(literal),after=pipeline(augmentOvSdk(literal));before.client._numAttemptedConnections=3;after.client._numAttemptedConnections=3
 before.client.onStreamStart({});after.client.onStreamStart({});expect(after.effects).toEqual(before.effects)
 expect(after.client._numAttemptedConnections).toBe(0);expect(before.pending).toHaveLength(0)
})
it('the Vite adapter validates SDK bytes at build start and only transforms its exact entry',()=>{
 const plugin=ovSdkDiagnostics() as any;plugin.configResolved({root:process.cwd()});expect(()=>plugin.buildStart()).not.toThrow()
 expect(()=>plugin.generateBundle()).toThrow(/bypassed/)
 expect(plugin.transform(literal,resolve('unrelated.js'))).toBeNull()
 const result=plugin.transform(literal,realpathSync(resolve('node_modules/@nvidia/omniverse-webrtc-streaming-library/dist/omniverse-webrtc-streaming-library.js')))
 expect(result.code).toBe(augmentOvSdk(literal));expect(result.map).toBeNull();expect(()=>plugin.generateBundle()).not.toThrow()
})

it.each([()=>false,()=>undefined,()=>1,()=> 'true',()=>{throw Error('controlled cancellation hook')}])('a missing true cancellation receipt preserves literal SDK effects, first retry and return',cancel=>{
 const before=pipeline(literal),after=pipeline(augmentOvSdk(literal),undefined,cancel)
 expect(before.client.onStreamStart({error:noOffer})).toBeUndefined();expect(after.client.onStreamStart({error:noOffer})).toBeUndefined();expect(after.effects).toEqual(before.effects)
 before.pending[0]!.callback();after.pending[0]!.callback();expect(after.effects).toEqual(before.effects)
})
it('a true receipt suppresses retry only, retains native diagnostic and original error callback return',()=>{
 const receipts:unknown[]=[],after=pipeline(augmentOvSdk(literal),event=>receipts.push(event),()=>true)
 expect(after.client.onStreamStart({error:noOffer})).toBeUndefined();expect(after.pending).toHaveLength(0)
 expect((after.effects.at(-1)as any)[1]).toMatchObject({status:'error'});expect(receipts).toEqual([{phase:'media-start',errorCode:noOffer}])
})
it('a queued first-error retry checks cancellation at execution; no receipt or a thrown hook retains its original callback',()=>{
 for(const outcome of[true,false,'true','throw']){
  let closed=false;const after=pipeline(augmentOvSdk(literal),undefined,()=>{if(closed&&outcome==='throw')throw Error('controlled');return closed?outcome:false})
  after.client.onStreamStart({error:noOffer});expect(after.pending).toHaveLength(1);closed=true;expect(()=>after.pending[0]!.callback()).not.toThrow()
  expect(after.effects.filter((event:any)=>event[0]==='retry-start')).toHaveLength(outcome===true?0:1)
 }
})
