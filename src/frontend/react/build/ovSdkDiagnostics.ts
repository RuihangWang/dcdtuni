import { createHash } from 'node:crypto'
import { readFileSync, realpathSync } from 'node:fs'
import { resolve } from 'node:path'
import type { Plugin } from 'vite'

// SDK5.18.2 discards the native numeric error before its first retry warning.
// This audited adapter adds an optional diagnostic receipt before conversion.
// The optional cancellation guard only retires retries of a confirmed canceled
// owner. Without that receipt all original retry/timeout/transport behavior remains.
export const OV_SDK_SHA256 = '46b9ee6862c2eda0d02f07d34dc1272801e3040e97cadadd60578d286b02e395'
export const OV_SDK_ANCHOR = 'onStreamStart(e){const t=new dl({...e,sessionId:this.sessionId()}),i=je.convert(t,Ji.startSessionResultEvent);'
export const OV_SDK_DIAGNOSTIC = 'try{const d=e&&typeof e==="object"?Object.getOwnPropertyDescriptor(e,"error"):void 0,v=d&&Object.prototype.hasOwnProperty.call(d,"value")?d.value:void 0,c=typeof v==="number"&&Number.isInteger(v)&&v>=0&&v<=4294967295?v:null;if(typeof this._props.onConnectionDiagnostic==="function")this._props.onConnectionDiagnostic({phase:"media-start",errorCode:c})}catch{}'
// Both anchors are unique in the exact audited SDK. Check again when a retry
// queued before cancellation executes; gl.destroy() does not cancel that timer.
export const OV_SDK_RETRY_ANCHOR = 'i.status===$.error&&this._props.maxReconnects+1-this._numAttemptedConnections>0&&('
export const OV_SDK_TIMER_ANCHOR = 'setTimeout((()=>{this.startButtonClick()}),this._props.reconnectDelay)'
export const OV_SDK_CANCEL_CHECK = '(()=>{try{return typeof this._props.shouldCancelReconnect==="function"&&this._props.shouldCancelReconnect()===true}catch{return false}})()'
export const OV_SDK_RETRY_GUARD = '&&!'+OV_SDK_CANCEL_CHECK
export const OV_SDK_TIMER_GUARD = 'if(!'+OV_SDK_CANCEL_CHECK+')'
// Raster fitting remains opt-in to the public SDK capability. Cap each axis
// with one scale; a zero-sized hidden canvas must not send a native zero frame.
export const OV_SDK_FIT_ANCHOR = 'resize(e,t){const i=this._maxWidth<e||this._maxHeight<t'
export const OV_SDK_FIT_HELPER = 'function dcdtuniFitRaster(w,h,mw,mh){if(![w,h,mw,mh].every(v=>typeof v==="number"&&Number.isFinite(v)&&Number.isInteger(v))||w<0||h<0||mw<1||mh<1||mw>4096||mh>4096)throw new Error("Omniverse raster dimensions must be finite bounded integers");if(w===0||h===0)return null;const s=Math.min(1,mw/w,mh/h),x=Math.floor(w*s),y=Math.floor(h*s);if(x<1||y<1)throw new Error("Omniverse viewport aspect cannot fit within raster bounds");return[x,y]}'
export const OV_SDK_FIT_INSERT = 'if(this._fitStreamResolution){const r=dcdtuniFitRaster(e,t,this._maxWidth,this._maxHeight);if(!r)return;[e,t]=r}'
const ENTRY = 'node_modules/@nvidia/omniverse-webrtc-streaming-library/dist/omniverse-webrtc-streaming-library.js'
export function augmentOvSdk(source: string): string {
  if (source.split(OV_SDK_ANCHOR).length !== 2) throw new Error('OV SDK diagnostic anchor is not unique; review the installed SDK before building')
  if (createHash('sha256').update(source).digest('hex') !== OV_SDK_SHA256) throw new Error('OV SDK version differs from audited5.18.2; review before building')
  if (source.split(OV_SDK_RETRY_ANCHOR).length !== 2 || source.split(OV_SDK_TIMER_ANCHOR).length !== 2)
    throw new Error('OV SDK cancellation anchors are not unique; review before building')
  if (source.split(OV_SDK_FIT_ANCHOR).length !== 2)
    throw new Error('OV SDK raster anchor is not unique; review before building')
  return source.replace(OV_SDK_ANCHOR, 'onStreamStart(e){' + OV_SDK_DIAGNOSTIC + OV_SDK_ANCHOR.slice('onStreamStart(e){'.length))
    .replace(OV_SDK_RETRY_ANCHOR, OV_SDK_RETRY_ANCHOR.slice(0,-3)+OV_SDK_RETRY_GUARD+'&&(')
    .replace(OV_SDK_TIMER_ANCHOR, 'setTimeout((()=>{'+OV_SDK_TIMER_GUARD+'this.startButtonClick()}),this._props.reconnectDelay)')
    .replace(OV_SDK_FIT_ANCHOR, 'resize(e,t){'+OV_SDK_FIT_INSERT+OV_SDK_FIT_ANCHOR.slice('resize(e,t){'.length))+'\n'+OV_SDK_FIT_HELPER
}
export function ovSdkDiagnostics(): Plugin {
  let entry = '', transformed = false
  return {
    name: 'dcdtuni-ov-sdk-diagnostics', enforce: 'pre',
    configResolved(config) { entry = realpathSync(resolve(config.root, ENTRY)).replace(/\\/g, '/') },
    // Run even if no graph imported the SDK; unsupported bytes fail closed.
    buildStart() { transformed = false; augmentOvSdk(readFileSync(entry, 'utf8')) },
    transform(source, id) {
      if (id.split('?')[0]!.replace(/\\/g, '/') !== entry) return null
      const code = augmentOvSdk(source); transformed = true
      return { code, map: null }
    },
    generateBundle() {
      if (!transformed) throw new Error('OV SDK diagnostic adapter was bypassed; refusing the production bundle')
    },
  }
}
