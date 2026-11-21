import {expect,it,vi} from 'vitest'
import {enqueueOvResultMutation,clearOwnedOvResultLayers,setOvResultStage} from '../src/renderer/ovResultMutationQueue'
it('finishes a sent old mutation before a newer owner authors the same stage',async()=>{
 const client={},writes:string[]=[];let release!:()=>void
 const old=enqueueOvResultMutation(client,()=>true,async()=>{await new Promise<void>(done=>release=done);writes.push('old-clear')})
 await Promise.resolve()
 const author=enqueueOvResultMutation(client,()=>true,async()=>{writes.push('air-author');return 42})
 await Promise.resolve();expect(writes).toEqual([])
 release();await old;expect(await author).toBe(42);expect(writes).toEqual(['old-clear','air-author'])
})
it('drops a queued mesh mutation after the common owner or stage changes',async()=>{
 const client={};let release!:()=>void,meshCurrent=true;const apply=vi.fn(async()=>{})
 const first=enqueueOvResultMutation(client,()=>true,()=>new Promise<void>(done=>release=done));await Promise.resolve()
 const mesh=enqueueOvResultMutation(client,()=>meshCurrent,apply);meshCurrent=false;release();await first;await mesh
 expect(apply).not.toHaveBeenCalled()
})
it('keeps different transport sessions independent and continues after a rejected ACK',async()=>{
 const one={},two={};let release!:()=>void
 const busy=enqueueOvResultMutation(one,()=>true,()=>new Promise<void>(done=>release=done));await Promise.resolve()
 expect(await enqueueOvResultMutation(two,()=>true,async()=>8)).toBe(8)
 release();await busy
 await expect(enqueueOvResultMutation(one,()=>true,async()=>{throw new Error('ACK rejected')})).rejects.toThrow('ACK rejected')
 expect(await enqueueOvResultMutation(one,()=>true,async()=>9)).toBe(9)
})

const scene=(revision:number)=>({projectUid:'p',projectHash:'a'.repeat(64),sceneRevision:revision})
it('clears only the completed mesh owner when intent becomes null',async()=>{
 const client={},clear=vi.fn(async()=>{});setOvResultStage(client,scene(1))
 await enqueueOvResultMutation(client,()=>true,async()=>{},'mesh-preview')
 await clearOwnedOvResultLayers(client,'mesh-preview',()=>true,clear);expect(clear).toHaveBeenCalledOnce()
 await clearOwnedOvResultLayers(client,'mesh-preview',()=>true,clear);expect(clear).toHaveBeenCalledOnce()
})
it('waits for an in-flight mesh before clearing its ACKed carrier on hide',async()=>{
 const client={},clear=vi.fn(async()=>{});let release!:()=>void;setOvResultStage(client,scene(1))
 const mesh=enqueueOvResultMutation(client,()=>true,()=>new Promise<void>(done=>release=done),'mesh-preview');await Promise.resolve()
 const hide=clearOwnedOvResultLayers(client,'mesh-preview',()=>true,clear)
 await Promise.resolve();expect(clear).not.toHaveBeenCalled();release();await mesh;await hide;expect(clear).toHaveBeenCalledOnce()
})
it('skips a pending mesh hide after air takes ownership and cannot clear the air carrier',async()=>{
 const client={},clear=vi.fn(async()=>{});let release!:()=>void,hideCurrent=true;setOvResultStage(client,scene(1))
 const mesh=enqueueOvResultMutation(client,()=>true,()=>new Promise<void>(done=>release=done),'mesh-preview');await Promise.resolve()
 const hide=clearOwnedOvResultLayers(client,'mesh-preview',()=>hideCurrent,clear);hideCurrent=false
 const air=enqueueOvResultMutation(client,()=>true,async()=>{},'air-postprocess');release();await mesh;await hide;await air
 await clearOwnedOvResultLayers(client,'mesh-preview',()=>true,clear);expect(clear).not.toHaveBeenCalled()
 await clearOwnedOvResultLayers(client,'air-postprocess',()=>true,clear);expect(clear).toHaveBeenCalledOnce()
})
it('drops old scene queued clear and does not let its late mesh ACK claim the new stage',async()=>{
 const client={},clear=vi.fn(async()=>{});let release!:()=>void;setOvResultStage(client,scene(1))
 const mesh=enqueueOvResultMutation(client,()=>true,()=>new Promise<void>(done=>release=done),'mesh-preview');await Promise.resolve()
 const oldClear=clearOwnedOvResultLayers(client,'mesh-preview',()=>true,clear)
 setOvResultStage(client,scene(2));release();await mesh;await oldClear
 await clearOwnedOvResultLayers(client,'mesh-preview',()=>true,clear);expect(clear).not.toHaveBeenCalled()
 await enqueueOvResultMutation(client,()=>true,async()=>{},'air-postprocess')
 await clearOwnedOvResultLayers(client,'air-postprocess',()=>true,clear);expect(clear).toHaveBeenCalledOnce()
})
