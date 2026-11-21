import {expect,it,vi} from 'vitest'
import {createOvStartReceipt,OvStreamLeases,terminateOvViewer,OV_CANCELED_START_BUDGET_MS} from '../src/renderer/ovStreamLease'
function deferred(){let resolve!:(value?:unknown)=>void,reject!:(error:unknown)=>void;const promise=new Promise<unknown>((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}}
async function clock(action:()=>Promise<void>){vi.useFakeTimers();try{await action()}finally{vi.clearAllTimers();vi.useRealTimers()}}
const phases={pendingStartMs:OV_CANCELED_START_BUDGET_MS}
it('late initialization is held; only its actual connecting cancellation receipt can retire retries',()=>clock(async()=>{
 const init=deferred(),receipt=createOvStartReceipt(),marked=vi.fn();let status=0
 const terminate=vi.fn(async()=>{if(status===1){status=3;return{status:'inProgress'}}status=0;return{status:'success'}})
 const closed=terminateOvViewer(terminate,()=>status===0,init.promise,15000,50,receipt,{...phases,onCancellationMarked:marked}).then(()=>true,()=>false)
 await vi.advanceTimersByTimeAsync(14000);expect(terminate).not.toHaveBeenCalled();expect(marked).not.toHaveBeenCalled()
 status=1;init.resolve();await vi.advanceTimersByTimeAsync(50);expect(marked).toHaveBeenCalledOnce();expect(status).toBe(3)
 await vi.advanceTimersByTimeAsync(30000);receipt.record({status:'canceled'});status=0;await vi.advanceTimersByTimeAsync(50);expect(await closed).toBe(true)
}))
it('a terminal callback before late inProgress init requires two serial public stops and confirmed idle',()=>clock(async()=>{
 const init=deferred(),receipt=createOvStartReceipt(),events:string[]=[];let status=0
 const closed=terminateOvViewer(async()=>{events.push('stop');if(status===1){status=3;return{status:'inProgress'}}status=0;return{status:'success'}},()=>status===0,init.promise,15000,50,receipt,phases).then(()=>true,()=>false)
 receipt.record({status:'success'});await vi.advanceTimersByTimeAsync(14000);expect(events).toEqual([])
 status=1;init.resolve();await vi.advanceTimersByTimeAsync(50);expect(await closed).toBe(true);expect(events).toEqual(['stop','stop'])
}))
it('an initialization that never settles remains blocked at the original15s bound even when its start callback or idle arrives later',()=>clock(async()=>{
 const init=deferred(),receipt=createOvStartReceipt(),stop=vi.fn(async()=>({status:'success'})),start=vi.fn(async()=>{}),leases=new OvStreamLeases()
 const first=leases.open(()=>init.promise,ready=>terminateOvViewer(stop,()=>true,ready,15000,50,receipt,phases));await Promise.resolve()
 const closing=first.close().then(()=>true,error=>error),next=leases.open(start,async()=>{}),result=next.ready.then(()=>true,error=>error)
 await vi.advanceTimersByTimeAsync(15000);expect(await closing).toBeInstanceOf(Error);expect(await result).toBeInstanceOf(Error)
 receipt.record({status:'canceled'});init.resolve();await first.ready;await vi.advanceTimersByTimeAsync(100);expect(stop).not.toHaveBeenCalled();expect(start).not.toHaveBeenCalled();await next.close()
}))
it('missing terminal receipt exhausts the finite native cleanup budget and cannot be unblocked by later idle',()=>clock(async()=>{
 const receipt=createOvStartReceipt(),leases=new OvStreamLeases();let idle=false
 const first=leases.open(async()=>{},ready=>terminateOvViewer(async()=>({status:'inProgress'}),()=>idle,ready,15000,50,receipt,phases));await first.ready
 const closing=first.close().then(()=>true,error=>error);await vi.advanceTimersByTimeAsync(OV_CANCELED_START_BUDGET_MS-1);let settled=false;void closing.then(()=>{settled=true});await Promise.resolve();expect(settled).toBe(false)
 await vi.advanceTimersByTimeAsync(1);expect((await closing).message).toContain('start event pending')
 idle=true;receipt.record({status:'canceled'});await vi.advanceTimersByTimeAsync(100);const start=vi.fn(async()=>{}),next=leases.open(start,async()=>{});await expect(next.ready).rejects.toThrow('reload the page');expect(start).not.toHaveBeenCalled();await next.close()
}))
it('terminal cleanup has its own original idle bound; a never-idle SDK keeps new viewers blocked',()=>clock(async()=>{
 const receipt=createOvStartReceipt();const closed=terminateOvViewer(async()=>({status:'inProgress'}),()=>false,Promise.resolve(),15000,50,receipt,phases).then(()=>true,error=>error)
 await vi.advanceTimersByTimeAsync(44000);receipt.record({status:'canceled'});await vi.advanceTimersByTimeAsync(14999);let settled=false;void closed.then(()=>{settled=true});await Promise.resolve();expect(settled).toBe(false)
 await vi.advanceTimersByTimeAsync(1);expect((await closed).message).toContain('start event settled')
}))
it('initialization rejection cleans real idle without pretending a terminal receipt; callback failure cannot change teardown',()=>clock(async()=>{
 const receipt=createOvStartReceipt();let status=1
 const closed=terminateOvViewer(async()=>{if(status===1){status=3;return{status:'inProgress'}}status=0;return{status:'success'}},()=>status===0,Promise.reject(Error('init')),15000,50,receipt,{...phases,onCancellationMarked:()=>{throw Error('controlled hook')}}).then(()=>true,()=>false)
 await vi.advanceTimersByTimeAsync(50);expect(status).toBe(3);receipt.record({status:'canceled'});await vi.advanceTimersByTimeAsync(50);expect(await closed).toBe(true)
}))
it.each([0,-1,NaN,Infinity,60001,true as any])('invalid or untracked phase budgets fail closed',async pendingStartMs=>{
 await expect(terminateOvViewer(async()=>{},()=>true,Promise.resolve(),15000,50,createOvStartReceipt(),{pendingStartMs})).rejects.toThrow('Invalid')
 await expect(terminateOvViewer(async()=>{},()=>true,Promise.resolve(),15000,50,undefined,phases)).rejects.toThrow('Invalid')
})
