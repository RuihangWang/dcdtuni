import type {Construction} from '../schema/cfdproj'
import * as store from '../projectStore'
import {createAuthoredTBranch,hasAuthoredTBranchLayout,positivePhysicalDiameter} from './tBranch'
type Bag = Record<string, unknown>
export function fittingDiameter(model: Construction): number | null {
  const cooling=model.cooling as Bag | undefined
  return positivePhysicalDiameter(cooling?.fittingDiameterM) ? cooling.fittingDiameterM : null
}
/** Editing a saved physical fitting keeps every authored port/quaternion field. */
export function setFittingDiameter(uid: string, metres: number): boolean {
  const project=store.getProject(),hit=project && store.findConstruction(project,uid)
  if (!hit || hit.model.kind!=='pipe_junction' || !positivePhysicalDiameter(metres) || !hasAuthoredTBranchLayout(hit.model.cooling)) return false
  store.edit('setFittingDiameter', `${uid} -> ${metres} m`, draft=>{
    const model=store.findConstruction(draft,uid)!.model
    ;(model.cooling as Bag).fittingDiameterM=metres
  })
  return true
}
/** Explicit user authoring only; neither a loader nor a migration fallback. */
export function declareStandardFitting(uid: string, metres: number): boolean {
  const project=store.getProject(),hit=project && store.findConstruction(project,uid)
  if (!hit || hit.model.kind!=='pipe_junction' || !positivePhysicalDiameter(metres) || hasAuthoredTBranchLayout(hit.model.cooling)) return false
  const previous=(hit.model.cooling || {}) as Bag
  const ports=previous.ports
  // Preserve connection identity. Unknown old port IDs require explicit rebuild,
  // rather than dropping relations while replacing the physical declaration.
  if (ports!==undefined && (!Array.isArray(ports) || ports.length!==3 || !ports.every((p: Bag)=>p && typeof p.id==='string' && ['port_1','port_2','port_3'].includes(p.id)) || new Set(ports.map((p: Bag)=>p.id)).size!==3)) return false
  const authored=createAuthoredTBranch(metres)
  if (Array.isArray(ports)) authored.ports=authored.ports.map(port=>({...(ports.find((p: Bag)=>p.id===port.id) as object),...port,
    type: ((ports.find((p: Bag)=>p.id===port.id) as Bag).type as string | undefined) ?? port.type,
    connectedPipeId: ((ports.find((p: Bag)=>p.id===port.id) as Bag).connectedPipeId as string | null | undefined) ?? null}))
  // A valid saved orientation remains the owner's frame, even for old layouts.
  const q=previous.quaternion as Bag | undefined
  if (q && ['x','y','z','w'].every(k=>typeof q[k]==='number' && Number.isFinite(q[k])) && Math.hypot(q.x as number,q.y as number,q.z as number,q.w as number)>0)
    authored.quaternion={x:q.x as number,y:q.y as number,z:q.z as number,w:q.w as number}
  store.edit('declareStandardFitting', `${uid} -> explicit ${metres} m T fitting`, draft=>{
    const model=store.findConstruction(draft,uid)!.model
    // connectedPipes lives on the model, not cooling. Retain that relation list.
    const {connectedPipes: _,...physical}=authored
    model.cooling={...(model.cooling as Bag | undefined),...physical}
  })
  return true
}
