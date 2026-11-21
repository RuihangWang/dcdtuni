import {useSyncExternalStore} from 'react'
import {ovSession,subscribeOvSession} from './ovSession'

/** Presentation telemetry only: this never grants a scene, camera or input owner. */
export interface OvVideoFrameStats { fps: number | null }
let stats: OvVideoFrameStats = {fps:null}
let currentOwner: symbol | null = null
let retireCurrent: (()=>void) | null = null
const listeners = new Set<()=>void>()
export function ovVideoFrameStats(): OvVideoFrameStats { return stats }
export function subscribeOvVideoFrameStats(listener:()=>void): ()=>void {
  listeners.add(listener)
  return ()=>{listeners.delete(listener)}
}
function publish(owner:symbol, fps:number|null): void {
  if(currentOwner!==owner||stats.fps===fps)return
  stats={fps}
  listeners.forEach(listener=>listener())
}

/** Counts compositor presentations, not the retained Three RAF or stream ACKs.
 * Unsupported, paused and stale videos remain unknown. No readiness flag or
 * playback-quality total is used as evidence of fresh frame advancement. */
export function observeOvVideoFrameStats(video:HTMLVideoElement): {dispose:()=>void} {
  retireCurrent?.()
  const owner=Symbol('OV video presentation stats')
  currentOwner=owner
  publish(owner,null)
  let disposed=false
  let callback:number|null=null
  let timer:ReturnType<typeof setInterval>|null=null
  let lastFrame:{time:number;count:number}|null=null
  let sample:{time:number;count:number}|null=null
  const owns=()=>!disposed&&currentOwner===owner
  function reset() {
    if(!owns())return
    lastFrame=null;sample=null;publish(owner,null)
  }
  function next() {
    if(owns())callback=video.requestVideoFrameCallback(frame)
  }
  function frame(now:number, metadata:VideoFrameCallbackMetadata) {
    callback=null
    if(!owns())return
    const count=metadata.presentedFrames
    if(video.paused||video.ended||video.ownerDocument.visibilityState==='hidden'
      ||!Number.isFinite(now)||now<0||!Number.isSafeInteger(count)||count<0) {
      reset();next();return
    }
    if(lastFrame&&(now<=lastFrame.time||count<lastFrame.count||now-lastFrame.time>=2000))reset()
    // Duplicate counters do not refresh freshness or inflate the FPS.
    if(lastFrame&&count===lastFrame.count){next();return}
    lastFrame={time:now,count}
    if(!sample)sample=lastFrame
    else if(now-sample.time>=1000) {
      publish(owner,Math.round((count-sample.count)*1000/(now-sample.time)))
      sample=lastFrame
    }
    next()
  }
  const events=['pause','ended','emptied','abort','waiting','stalled','error','playing','seeked']
  function dispose() {
    if(disposed)return
    disposed=true
    if(callback!==null&&typeof video.cancelVideoFrameCallback==='function')video.cancelVideoFrameCallback(callback)
    if(timer!==null)clearInterval(timer)
    events.forEach(event=>video.removeEventListener(event,reset))
    video.ownerDocument.removeEventListener('visibilitychange',reset)
    if(currentOwner===owner) {
      publish(owner,null);currentOwner=null;retireCurrent=null
    }
  }
  retireCurrent=dispose
  if(typeof video.requestVideoFrameCallback==='function'&&typeof video.cancelVideoFrameCallback==='function') {
    events.forEach(event=>video.addEventListener(event,reset))
    video.ownerDocument.addEventListener('visibilitychange',reset)
    timer=setInterval(()=>{
      if(owns()&&(video.paused||video.ended||video.ownerDocument.visibilityState==='hidden'
        ||lastFrame&&performance.now()-lastFrame.time>=2000))reset()
    },500)
    next()
  }
  return {dispose}
}

/** RendererControl remains the only renderer authority; this merely reads it. */
export function ViewportFps({threeFps}:{threeFps:number}) {
  const {mode}=useSyncExternalStore(subscribeOvSession,ovSession,ovSession)
  const video=useSyncExternalStore(subscribeOvVideoFrameStats,ovVideoFrameStats,ovVideoFrameStats)
  const fps=mode==='ov'?video.fps:threeFps
  return <span id="fps-display" title={mode==='ov'?'OV video presentation FPS':'Three.js render FPS'}>FPS: {fps??'—'}</span>
}
