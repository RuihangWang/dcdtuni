/** Public SDK sizing only. It does not grant decoded/layout/source authority. */
export const OV_STREAM_MAX_WIDTH=1600
export const OV_STREAM_MAX_HEIGHT=900
export function alignedOvStreamSize(width:number,height:number):[number,number]|null {
  if(!Number.isFinite(width)||!Number.isFinite(height)||width<8||height<2)return null
  const ratio=Math.min(1,OV_STREAM_MAX_WIDTH/width,OV_STREAM_MAX_HEIGHT/height)
  const w=Math.ceil(width*ratio/8)*8,h=Math.ceil(height*ratio/2)*2
  return Number.isSafeInteger(w)&&Number.isSafeInteger(h)&&w>=8&&h>=2
    &&w<=OV_STREAM_MAX_WIDTH&&h<=OV_STREAM_MAX_HEIGHT?[w,h]:null
}

/** Coalesce real layout changes; never send a later request for a retired owner.
 * An already dispatched SDK request cannot be canceled by this observer; its
 * completion is never treated as proof of the application's actual size. */
export function observeAlignedOvResize(video:HTMLVideoElement,id:string,
  isCurrent:()=>boolean,resize:(width:number,height:number)=>Promise<unknown>,
  onFailure:(reason:'public-resize-unavailable'|'resize-observer-unavailable')=>void) {
  let closed=false,timer:ReturnType<typeof setTimeout>|null=null,inFlight=false
  let wanted:[number,number]|null=null,last:[number,number]|null=null
  const owned=()=>!closed&&isCurrent()&&video.isConnected&&document.getElementById(id)===video
    &&video.parentElement?.id===`${id}-input-parent`
  const size=()=>owned()?alignedOvStreamSize(video.clientWidth,video.clientHeight):null
  const same=(a:[number,number]|null,b:[number,number]|null)=>!!a&&!!b&&a[0]===b[0]&&a[1]===b[1]
  const clear=()=>{if(timer!==null){clearTimeout(timer);timer=null}}
  let observer:ResizeObserver|null=null
  const dispose=()=>{if(closed)return;closed=true;clear();wanted=null;observer?.disconnect()}
  const fail=(reason:'public-resize-unavailable'|'resize-observer-unavailable')=>{
    if(!owned()){dispose();return};dispose();onFailure(reason)}
  const flush=()=>{clear();if(!owned()){dispose();return}
    const next=size();wanted=next
    if(!next||inFlight||same(next,last))return
    last=next;inFlight=true
    let promise:Promise<unknown>
    try {promise=resize(next[0],next[1])}catch{inFlight=false;fail('public-resize-unavailable');return}
    void Promise.resolve(promise).then(()=>{
      inFlight=false;if(!owned()){dispose();return}
      const current=size();wanted=current
      if(current&&!same(current,last))schedule()
    },()=>{inFlight=false;fail('public-resize-unavailable')})
  }
  const schedule=()=>{if(!owned()){dispose();return}
    wanted=size();if(!wanted||same(wanted,last))return
    clear();timer=setTimeout(flush,120)}
  if(typeof ResizeObserver!=='function'){fail('resize-observer-unavailable');return{dispose}}
  observer=new ResizeObserver(schedule);observer.observe(video);flush()
  return{dispose}
}
