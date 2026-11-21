import {afterEach,expect,it,vi} from 'vitest'
import {alignedOvStreamSize,observeAlignedOvResize} from '../src/renderer/ovAlignedResize'
afterEach(()=>{document.body.replaceChildren();vi.useRealTimers();vi.unstubAllGlobals();vi.restoreAllMocks()})
let ownerCounter=0
function fixture(resize:(w:number,h:number)=>Promise<unknown>=vi.fn(async()=>({status:'success'}))){
 vi.useFakeTimers();let callback:ResizeObserverCallback=()=>{},width=1191,height=796,current=true
 const disconnect=vi.fn();vi.stubGlobal('ResizeObserver',class{constructor(cb:ResizeObserverCallback){callback=cb}observe=vi.fn();disconnect=disconnect})
 const id=`resize-video-${++ownerCounter}`
 const parent=document.createElement('div');parent.id=`${id}-input-parent`;document.body.append(parent)
 const video=document.createElement('video');video.id=id;parent.append(video)
 Object.defineProperties(video,{clientWidth:{get:()=>width},clientHeight:{get:()=>height}})
 const failure=vi.fn(),controller=observeAlignedOvResize(video,video.id,()=>current,resize,failure)
 return{video,parent,resize,failure,controller,disconnect,setSize:(w:number,h:number)=>{width=w;height=h;callback([],{}as ResizeObserver)},stale:()=>{current=false},callback:()=>callback([],{}as ResizeObserver)}
}
it('uses bounded align8/even requests with the unchanged 1600x900 maximum',()=>{
 expect(alignedOvStreamSize(1191,796)).toEqual([1192,796]);expect(alignedOvStreamSize(1191.2,795)).toEqual([1192,796])
 expect(alignedOvStreamSize(1600,900)).toEqual([1600,900]);expect(alignedOvStreamSize(2000,1000)).toEqual([1600,800])
 expect(alignedOvStreamSize(1000,1000)).toEqual([904,900]);expect(alignedOvStreamSize(8,2)).toEqual([8,2])
 for(const pair of [[0,796],[7,796],[1191,1],[Infinity,796],[NaN,796],[-1,100]])expect(alignedOvStreamSize(pair[0]!,pair[1]!)).toBeNull()
})
it('requests the initial actual element size and suppresses duplicate observer notifications',async()=>{
 const resize=vi.fn(async()=>({status:'success'})),f=fixture(resize)
 expect(resize).toHaveBeenCalledTimes(1);expect(resize).toHaveBeenCalledWith(1192,796);f.callback();f.callback();await vi.runAllTimersAsync();expect(resize).toHaveBeenCalledTimes(1)
 f.setSize(1599,899);await vi.advanceTimersByTimeAsync(120);expect(resize).toHaveBeenLastCalledWith(1600,900)
 f.controller.dispose();expect(f.disconnect).toHaveBeenCalledTimes(1)
})
it('coalesces changes during an in-flight resize, then reads only the latest measured dimensions',async()=>{
 let finish:()=>void=()=>{};const resize=vi.fn(()=>new Promise<void>(resolve=>{finish=resolve})),f=fixture(resize)
 f.setSize(900,600);f.setSize(999,699);await vi.advanceTimersByTimeAsync(120);expect(resize).toHaveBeenCalledTimes(1)
 finish();await Promise.resolve();await vi.advanceTimersByTimeAsync(120);expect(resize).toHaveBeenLastCalledWith(1000,700);expect(resize).toHaveBeenCalledTimes(2);f.controller.dispose()
})
it('retired, detached, replaced or unmounted owners cannot issue a later resize',async()=>{
 for(const retire of [(f:ReturnType<typeof fixture>)=>f.stale(),(f:ReturnType<typeof fixture>)=>f.video.remove(),(f:ReturnType<typeof fixture>)=>{f.video.id='old'},(f:ReturnType<typeof fixture>)=>f.controller.dispose()]){
  let finish:()=>void=()=>{};const resize=vi.fn(()=>new Promise<void>(resolve=>{finish=resolve})),f=fixture(resize)
  f.setSize(900,600);retire(f);finish();await Promise.resolve();await vi.runAllTimersAsync();f.callback();expect(resize).toHaveBeenCalledTimes(1);expect(f.failure).not.toHaveBeenCalled();expect(f.disconnect).toHaveBeenCalledTimes(1)
 }
})
it('public resize errors stop this observer once; stale errors do not log for a newer session',async()=>{
 const resize=vi.fn(()=>Promise.reject(Error('controlled no wire detail'))),f=fixture(resize);await Promise.resolve()
 expect(f.failure).toHaveBeenCalledTimes(1);expect(f.failure).toHaveBeenCalledWith('public-resize-unavailable');f.callback();await vi.runAllTimersAsync();expect(resize).toHaveBeenCalledTimes(1)
 let reject:(e:Error)=>void=()=>{};const g=fixture(vi.fn(()=>new Promise((_r,j)=>{reject=j})));g.stale();reject(Error('old'));await Promise.resolve();expect(g.failure).not.toHaveBeenCalled();expect(g.disconnect).toHaveBeenCalledTimes(1)
})
it('missing observation capability or invalid dimensions cannot silently guess a size',()=>{
 vi.stubGlobal('ResizeObserver',undefined);const p=document.createElement('div');p.id='x-input-parent';document.body.append(p);const v=document.createElement('video');v.id='x';p.append(v)
 const resize=vi.fn(),failure=vi.fn();observeAlignedOvResize(v,'x',()=>true,resize,failure);expect(resize).not.toHaveBeenCalled();expect(failure).toHaveBeenCalledTimes(1);expect(failure).toHaveBeenCalledWith('resize-observer-unavailable')
})
