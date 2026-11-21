import {act} from 'react'
import {createRoot,type Root} from 'react-dom/client'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {observeOvVideoFrameStats,ovVideoFrameStats,ViewportFps} from '../src/renderer/viewportFrameStats'
import {setOvSession,ovSession} from '../src/renderer/ovSession'
let clock=0
let dispose:(()=>void)[]=[]
let root:Root|null=null
let host:HTMLDivElement
function videoControl(supported=true) {
  const video=document.createElement('video')
  let paused=false,ended=false,id=0
  const callbacks=new Map<number,VideoFrameRequestCallback>()
  const all=new Map<number,VideoFrameRequestCallback>()
  Object.defineProperties(video,{paused:{get:()=>paused},ended:{get:()=>ended},readyState:{get:()=>4}})
  const cancel=vi.fn((key:number)=>{callbacks.delete(key)})
  if(supported)Object.defineProperties(video,{
    requestVideoFrameCallback:{value:(cb:VideoFrameRequestCallback)=>{callbacks.set(++id,cb);all.set(id,cb);return id}},
    cancelVideoFrameCallback:{value:cancel},
  })
  const frame=(time:number,count:number)=>{
    clock=time
    const [key,cb]=Array.from(callbacks)[0]!
    callbacks.delete(key)
    cb(time,{presentedFrames:count} as VideoFrameCallbackMetadata)
  }
  return {video,frame,callbacks,all,cancel,setPaused:(value:boolean)=>{paused=value},setEnded:(value:boolean)=>{ended=value}}
}
function watch(v:HTMLVideoElement) { const observer=observeOvVideoFrameStats(v);dispose.push(observer.dispose);return observer }
function render() {root=createRoot(host);act(()=>root!.render(<ViewportFps threeFps={144}/>))}
function text(){return host.querySelector('#fps-display')?.textContent}
beforeEach(()=>{
  vi.useFakeTimers();clock=0;vi.spyOn(performance,'now').mockImplementation(()=>clock)
  ;(globalThis as {IS_REACT_ACT_ENVIRONMENT?:boolean}).IS_REACT_ACT_ENVIRONMENT=true
  setOvSession({mode:'three',ready:false,client:null})
  host=document.createElement('div');document.body.append(host)
})
afterEach(()=>{act(()=>{root?.unmount();dispose.forEach(fn=>fn())});root=null;dispose=[];host.remove();vi.restoreAllMocks();vi.useRealTimers()})
it('selects the displayed renderer cadence',()=>{
  const v=videoControl();watch(v.video);render()
  expect(text()).toBe('FPS: 144')
  act(()=>{setOvSession({mode:'ov'});v.frame(0,100);v.frame(1000,130)})
  expect(text()).toBe('FPS: 30')
  act(()=>setOvSession({mode:'three'}));expect(text()).toBe('FPS: 144')
  expect(ovSession().ready).toBe(false);expect(ovSession().client).toBeNull()
})
it('does not use callback frequency as presented frame count',()=>{
  const v=videoControl();watch(v.video)
  v.frame(0,100);v.frame(250,115);v.frame(500,130);v.frame(750,145);v.frame(1000,160)
  expect(ovVideoFrameStats().fps).toBe(60)
})
it('initial, unsupported and pause/end states are unknown rather than zero or retained Three FPS',()=>{
  const v=videoControl();watch(v.video);render();act(()=>setOvSession({mode:'ov'}));expect(text()).toBe('FPS: —')
  act(()=>{v.frame(0,0);v.frame(1000,30)});expect(text()).toBe('FPS: 30')
  act(()=>{v.setPaused(true);v.video.dispatchEvent(new Event('pause'))});expect(text()).toBe('FPS: —')
  act(()=>{v.setPaused(false);v.video.dispatchEvent(new Event('playing'));v.frame(1200,31);v.frame(2200,61)});expect(text()).toBe('FPS: 30')
  act(()=>{v.setEnded(true);v.video.dispatchEvent(new Event('ended'))});expect(text()).toBe('FPS: —')
  act(()=>watch(videoControl(false).video));expect(text()).toBe('FPS: —')
})
it('expires a frozen playing readyState=4 video and duplicate counters cannot renew it',()=>{
  const v=videoControl();watch(v.video);v.frame(0,100);v.frame(1000,130);expect(ovVideoFrameStats().fps).toBe(30)
  v.frame(1999,130);clock=3000;vi.advanceTimersByTime(2000)
  expect(v.video.readyState).toBe(4);expect(v.video.paused).toBe(false);expect(ovVideoFrameStats().fps).toBeNull()
  v.frame(3001,131);expect(ovVideoFrameStats().fps).toBeNull();v.frame(4001,161);expect(ovVideoFrameStats().fps).toBe(30)
})
it('resets counter rollover, malformed metadata, backwards timestamps and gaps before recovering',()=>{
  const v=videoControl();watch(v.video);v.frame(0,100);v.frame(1000,130)
  v.frame(1100,1);expect(ovVideoFrameStats().fps).toBeNull();v.frame(2100,31);expect(ovVideoFrameStats().fps).toBe(30)
  v.frame(2200,NaN);expect(ovVideoFrameStats().fps).toBeNull();v.frame(2300,32);v.frame(3300,62);expect(ovVideoFrameStats().fps).toBe(30)
  v.frame(3200,63);expect(ovVideoFrameStats().fps).toBeNull();v.frame(6200,64);expect(ovVideoFrameStats().fps).toBeNull()
})
it('owner replacement cancels the previous sampler and queued callbacks cannot overwrite the new owner',()=>{
  const a=videoControl(),b=videoControl();watch(a.video);a.frame(0,0);a.frame(1000,30)
  const late=Array.from(a.callbacks.values())[0]!;watch(b.video)
  expect(a.cancel).toHaveBeenCalled();expect(a.callbacks.size).toBe(0);expect(ovVideoFrameStats().fps).toBeNull()
  b.frame(1100,500);b.frame(2100,560);expect(ovVideoFrameStats().fps).toBe(60)
  late(2200,{presentedFrames:500} as VideoFrameCallbackMetadata);a.video.dispatchEvent(new Event('pause'))
  expect(ovVideoFrameStats().fps).toBe(60);expect(a.callbacks.size).toBe(0)
})
it('disposal cancels callbacks/timers and late delivery cannot resurrect stats',()=>{
  const v=videoControl();const observer=watch(v.video);v.frame(0,0);v.frame(1000,30)
  const late=Array.from(v.callbacks.values())[0]!;observer.dispose();observer.dispose()
  expect(ovVideoFrameStats().fps).toBeNull();expect(v.cancel).toHaveBeenCalledTimes(1);expect(vi.getTimerCount()).toBe(0)
  late(2000,{presentedFrames:60} as VideoFrameCallbackMetadata);expect(ovVideoFrameStats().fps).toBeNull();expect(v.callbacks.size).toBe(0)
})

it('hides presentation telemetry while the document is hidden and resets buffering samples',()=>{
  const v=videoControl();watch(v.video);v.frame(0,0);v.frame(1000,30);expect(ovVideoFrameStats().fps).toBe(30)
  const visibility=vi.spyOn(document,'visibilityState','get').mockReturnValue('hidden')
  document.dispatchEvent(new Event('visibilitychange'));expect(ovVideoFrameStats().fps).toBeNull()
  v.frame(1500,45);expect(ovVideoFrameStats().fps).toBeNull()
  visibility.mockReturnValue('visible');document.dispatchEvent(new Event('visibilitychange'))
  v.frame(1600,46);v.frame(2600,76);expect(ovVideoFrameStats().fps).toBe(30)
  v.video.dispatchEvent(new Event('waiting'));expect(ovVideoFrameStats().fps).toBeNull()
})
