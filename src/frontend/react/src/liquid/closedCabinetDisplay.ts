/** Session-local display policy. Never serialized into a project or run. */
let showInterior=false
let displayRevision=0
let diagnostics: readonly string[]=[]
const listeners=new Set<()=>void>()
let refresh:(()=>void)|null=null
export const showCabinetPipes=()=>showInterior
export const leafDisplayRevision=()=>displayRevision
export const closedCabinetDiagnostics=()=>diagnostics
export function subscribeClosedCabinetDisplay(fn:()=>void){listeners.add(fn);return()=>{listeners.delete(fn)}}
export function registerCabinetDisplayRefresh(fn:(()=>void)|null){refresh=fn}
export function publishLeafDisplayChange(){displayRevision++;listeners.forEach(fn=>fn())}
export function setShowCabinetPipes(on:boolean){
  if(on===showInterior)return
  showInterior=on
  refresh?.()
  publishLeafDisplayChange()
}
export function setCabinetDisplayDiagnostics(next:readonly string[]){
  const stable=[...new Set(next)].sort()
  if(JSON.stringify(stable)===JSON.stringify(diagnostics))return
  diagnostics=stable
  publishLeafDisplayChange()
}
/** Opacity previews arrive in batches. Read final material visibility once. */
let pending=false
export function requestCabinetDisplayRefresh(){
  if(pending)return
  pending=true
  queueMicrotask(()=>{
    pending=false
    refresh?.()
    publishLeafDisplayChange()
  })
}
/** The display revision stays live even when OV owns the liquid result frame. */
export function displaySceneKey(projectUid:string,sourceIdentity:string|number,
  frozenResult:readonly [number,number|null,number],revision:number){
  return JSON.stringify([projectUid,sourceIdentity,...frozenResult,revision])
}
